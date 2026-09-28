from __future__ import annotations

import base64
import json
import re
import shlex
import ipaddress
import socket
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


SENSITIVE_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "credentials",
    "credentials.json",
    "service-account.json",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}

SENSITIVE_FILE_SUFFIXES = {
    ".key",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
}

SECRET_VALUE_PATTERNS = [
    re.compile(r"(?i)\b(authorization[ \t]*[:=][ \t]*bearer[ \t]+)([A-Za-z0-9._~+/=-]+)"),
    # Retain canonical Bearer scheme shorthand for existing long credentials.
    # Lowercase prose needs an Authorization header before it is treated as secret.
    re.compile(r"\b(Bearer[ \t]+)([A-Za-z0-9._~+/=-]{16,})"),
    re.compile(
        r"(?i)\b([A-Z0-9_]*(?:api[_-]?key|token|secret|password|passwd|credential))\b"
        r"(\s*[:=]\s*)"
        r"([^\s'\"]+)"
    ),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{16,})\b"),
]


# Compact JWT candidates have three separate base64url fields. Bounds and token
# boundaries prevent partial matches inside oversized fields; the disjoint field
# alphabet/dot separators avoid nested or ambiguous regex repetition.
_JWT_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_-]{8,1024})\."
    r"[A-Za-z0-9_-]{2,8192}\.[A-Za-z0-9_-]{1,2048}(?![A-Za-z0-9_.-])"
)


def _redact_jwt(match: re.Match[str]) -> str:
    # Inspect only the bounded JOSE header, not the payload or signature. Requiring
    # a JSON object with an algorithm distinguishes JWTs from versions/domains.
    # This recognizes secret material; it does not authenticate the token.
    encoded = match[1]
    try:
        header = json.loads(base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True,
        ).decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        return match[0]
    return "[REDACTED]" if isinstance(header, dict) and isinstance(header.get("alg"), str) else match[0]


DESTRUCTIVE_PATTERNS = [
    re.compile(r"(?i)\brm\s+.*-(?:r|f|rf|fr)\b"),
    re.compile(r"(?i)\bremove-item\b.*-(?:recurse|force)\b"),
    re.compile(r"(?i)\brmdir\b.*(/s|-r|--recursive)"),
    re.compile(r"(?i)\bdel\b.*(/s|/q)"),
    re.compile(r"(?i)\bgit\s+reset\s+--hard\b"),
    re.compile(r"(?i)\bgit\s+clean\b.*-(?:f|x|d)"),
    re.compile(r"(?i)\bgit\s+checkout\s+(?:--|\.)"),
    re.compile(r"(?i)\bmkfs\b"),
    re.compile(r"(?i)\bformat\b.*[A-Z]:"),
]

SHELL_CONTROL_PATTERNS = [
    re.compile(r"[;&|]"),
    re.compile(r"`"),
    re.compile(r"\$\("),
    re.compile(r">\s*"),
    re.compile(r"<\s*"),
    re.compile(r"(?i)\b(?:2>|1>|out-file|set-content|add-content)\b"),
]

ARBITRARY_CODE_PATTERNS = [
    re.compile(r"(?i)\bpython(?:3|3\.\d+)?\s+-c\b"),
    re.compile(r"(?i)\bpy\s+-\d+(?:\.\d+)?\s+-c\b"),
    re.compile(r"(?i)\bnode\s+-e\b"),
    re.compile(r"(?i)\bpowershell\b.*\b(?:-encodedcommand|-enc|-command)\b"),
    re.compile(r"(?i)\bpwsh\b.*\b(?:-encodedcommand|-enc|-command)\b"),
    re.compile(r"(?i)\bperl\s+-e\b"),
    re.compile(r"(?i)\bruby\s+-e\b"),
]

WORKSPACE_ESCAPE_PATTERNS = [
    re.compile(r"(?i)(?:^|\s)(?:cd|pushd|set-location)\s+\.\."),
    re.compile(r"(?i)(?:^|\s)(?:cd|pushd|set-location)\s+[A-Za-z]:\\"),
    re.compile(r"(?i)(?:^|\s)(?:cd|pushd|set-location)\s+/"),
]


@dataclass(frozen=True)
class ShellPolicy:
    category: str
    risk: str
    allowed: bool
    reason: str
    may_write: bool = False
    may_network: bool = False
    arbitrary_code: bool = False
    timeout_seconds: int = 60


@dataclass(frozen=True)
class NetworkPolicy:
    host: str
    category: str
    risk: str
    allowed: bool
    reason: str
    resolved_addresses: tuple[str, ...] = ()


def is_sensitive_path(path: Path, workspace: Path) -> bool:
    try:
        relative = path.resolve().relative_to(workspace.resolve())
    except ValueError:
        return True
    for part in relative.parts:
        lowered = part.lower()
        if lowered in SENSITIVE_FILE_NAMES:
            return True
        if lowered == ".env" or lowered.startswith(".env."):
            return True
        if Path(lowered).suffix in SENSITIVE_FILE_SUFFIXES:
            return True
    return False


def redact_secrets(value: str) -> str:
    redacted = value
    for pattern in SECRET_VALUE_PATTERNS:
        if pattern.pattern.lower().startswith("(?i)\\b(authorization"):
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        elif pattern.pattern.startswith("\\b(Bearer"):
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        elif pattern.pattern.startswith("\\b(sk-"):
            redacted = pattern.sub("[REDACTED]", redacted)
        else:
            redacted = pattern.sub(r"\1\2[REDACTED]", redacted)
    return _JWT_TOKEN.sub(_redact_jwt, redacted)



# One recursive boundary for event payloads, snapshots, UI metadata and checkpoints.
# These are provider-private fields, not public plans or verification explanations.
_PRIVATE_FIELDS = {"reasoning_content", "reasoning_details", "chain_of_thought", "private_analysis", "provider_debug"}
_CREDENTIAL_FIELDS = {"api_key", "apikey", "password", "authorization", "access_token", "refresh_token", "client_secret"}


def sanitize_payload(value):
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _PRIVATE_FIELDS:
                continue
            safe_key = redact_secrets(str(key))
            result[safe_key] = (
                "[REDACTED]" if normalized in _CREDENTIAL_FIELDS and item is not None
                else sanitize_payload(item)
            )
        return result
    if isinstance(value, (list, tuple)):
        return [sanitize_payload(item) for item in value]
    return value


def safe_exception(exc: BaseException, *, component: str = "Operation") -> str:
    # Never echo exception bodies: they can contain arbitrary prompts and responses.
    if type(exc).__name__ == "SandboxIsolationError":
        return "Sandbox isolation unavailable; Agent47 will not fall back to local execution."
    if isinstance(exc, (TimeoutError,)):
        category = "timed out"
    elif isinstance(exc, PermissionError):
        category = "permission denied"
    elif isinstance(exc, (KeyboardInterrupt, SystemExit)):
        category = "cancelled"
    else:
        category = "failed"
    return f"{component} {category} ({type(exc).__name__})."

def classify_shell_command(command: str) -> ShellPolicy:
    normalized = " ".join(command.strip().split())
    lowered = normalized.lower()
    if not normalized:
        return ShellPolicy("unknown", "low", False, "Empty shell commands are not useful.")
    if any(pattern.search(normalized) for pattern in DESTRUCTIVE_PATTERNS):
        return ShellPolicy(
            "destructive",
            "critical",
            False,
            "Destructive shell commands are blocked by Agent47 policy.",
            may_write=True,
            timeout_seconds=0,
        )
    if any(pattern.search(normalized) for pattern in SHELL_CONTROL_PATTERNS):
        return ShellPolicy(
            "compound-shell",
            "high",
            False,
            "Compound shell syntax, redirection, and pipelines are blocked; run one explicit command at a time.",
            may_write=True,
            arbitrary_code=True,
            timeout_seconds=0,
        )
    if any(pattern.search(normalized) for pattern in WORKSPACE_ESCAPE_PATTERNS):
        return ShellPolicy(
            "workspace-escape",
            "critical",
            False,
            "Changing the shell working directory outside the workspace is blocked.",
            may_write=True,
            arbitrary_code=True,
            timeout_seconds=0,
        )
    if any(pattern.search(normalized) for pattern in ARBITRARY_CODE_PATTERNS):
        return ShellPolicy(
            "arbitrary-code",
            "critical",
            False,
            "Inline interpreter execution is blocked; use checked-in scripts or project verification commands.",
            may_write=True,
            arbitrary_code=True,
            timeout_seconds=0,
        )
    if _looks_like_install_or_network(lowered):
        return ShellPolicy(
            "install/network",
            "high",
            True,
            "May install packages or contact external network resources.",
            may_write=True,
            may_network=True,
            timeout_seconds=600,
        )
    if _looks_like_test_or_build(lowered):
        return ShellPolicy(
            "verification",
            "medium",
            True,
            "Runs project verification.",
            may_write=True,
            timeout_seconds=600,
        )
    if _looks_like_development_process(lowered):
        return ShellPolicy(
            "development",
            "high",
            True,
            "Runs project code as a development server, watcher, or checked-in script.",
            may_write=True,
            may_network=True,
            arbitrary_code=True,
            timeout_seconds=0,
        )
    if _looks_like_git(lowered):
        return ShellPolicy(
            "git",
            "medium",
            True,
            "Touches git metadata or repository state.",
            may_write=True,
            timeout_seconds=120,
        )
    if _looks_like_read_only(lowered):
        return ShellPolicy(
            "read-only",
            "low",
            True,
            "Inspects local state without obvious mutation.",
            timeout_seconds=60,
        )
    return ShellPolicy(
        "unknown",
        "high",
        False,
        "Unclassified shell commands are blocked until an explicit policy class is added.",
        arbitrary_code=True,
        timeout_seconds=0,
    )


DnsResolver = Callable[[str], tuple[str, ...]]


def classify_network_url(
    url: str,
    *,
    domain_allowlist: tuple[str, ...] = (),
    resolve_dns: bool = False,
    dns_resolver: DnsResolver | None = None,
) -> NetworkPolicy:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        return NetworkPolicy(
            host=host or "<none>",
            category="unsupported-scheme",
            risk="high",
            allowed=False,
            reason="Only http and https URLs are allowed for web access.",
        )
    if parsed.scheme != "https":
        return NetworkPolicy(
            host=host or "<none>",
            category="insecure-transport",
            risk="high",
            allowed=False,
            reason="Plain HTTP is blocked; external web access must use HTTPS.",
        )
    if not host:
        return NetworkPolicy(
            host="<none>",
            category="invalid-url",
            risk="high",
            allowed=False,
            reason="URL does not contain a host.",
        )
    normalized_allowlist = _normalize_domain_allowlist(domain_allowlist)
    if normalized_allowlist and not _host_matches_domain_allowlist(host, normalized_allowlist):
        return NetworkPolicy(
            host=host,
            category="domain-not-allowlisted",
            risk="high",
            allowed=False,
            reason=(
                "Host is not included in the configured domain allowlist: "
                + ", ".join(normalized_allowlist)
            ),
        )
    if host in {"localhost"} or host.endswith(".localhost"):
        return NetworkPolicy(
            host=host,
            category="local-network",
            risk="critical",
            allowed=False,
            reason="Localhost web targets are blocked by Agent47 network policy.",
        )
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        if not resolve_dns:
            return NetworkPolicy(
                host=host,
                category="public-web",
                risk="medium",
                allowed=True,
                reason="Public web target.",
            )
        resolver = dns_resolver or resolve_public_dns_addresses
        try:
            resolved_addresses = resolver(host)
        except OSError as exc:
            return NetworkPolicy(
                host=host,
                category="dns-resolution-failed",
                risk="high",
                allowed=False,
                reason=f"DNS resolution failed for {host}: {exc}.",
            )
        blocked = _blocked_network_addresses(resolved_addresses)
        if blocked:
            return NetworkPolicy(
                host=host,
                category="dns-private-network",
                risk="critical",
                allowed=False,
                reason=(
                    "DNS resolved to private, loopback, reserved, link-local, or "
                    f"multicast address(es): {', '.join(blocked)}."
                ),
                resolved_addresses=tuple(resolved_addresses),
            )
        return NetworkPolicy(
            host=host,
            category="public-web",
            risk="medium",
            allowed=True,
            reason="Public web target.",
            resolved_addresses=tuple(resolved_addresses),
        )
    if _is_blocked_network_address(address):
        return NetworkPolicy(
            host=host,
            category="local-network",
            risk="critical",
            allowed=False,
            reason="Private, loopback, reserved, link-local, and multicast web targets are blocked.",
            resolved_addresses=(str(address),),
        )
    return NetworkPolicy(
        host=host,
        category="public-web",
        risk="medium",
        allowed=True,
        reason="Public web target.",
        resolved_addresses=(str(address),),
    )


def resolve_public_dns_addresses(host: str) -> tuple[str, ...]:
    addresses = {
        item[4][0]
        for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        if item and len(item) >= 5 and item[4]
    }
    return tuple(sorted(addresses))


def _normalize_domain_allowlist(domain_allowlist: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for item in domain_allowlist:
        host = item.strip().lower().rstrip(".")
        if "://" in host:
            host = urllib.parse.urlparse(host).hostname or ""
        if host:
            normalized.append(host)
    return tuple(normalized)


def _host_matches_domain_allowlist(host: str, domain_allowlist: tuple[str, ...]) -> bool:
    clean_host = host.strip().lower().rstrip(".")
    for pattern in domain_allowlist:
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if clean_host.endswith(suffix) and clean_host != pattern[2:]:
                return True
            continue
        if clean_host == pattern or clean_host.endswith("." + pattern):
            return True
    return False


def _blocked_network_addresses(addresses: tuple[str, ...]) -> tuple[str, ...]:
    blocked: list[str] = []
    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            blocked.append(raw)
            continue
        if _is_blocked_network_address(address):
            blocked.append(str(address))
    return tuple(blocked)


def _is_blocked_network_address(address: ipaddress._BaseAddress) -> bool:
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    )


def _looks_like_install_or_network(lowered: str) -> bool:
    network_tokens = [
        "npm install",
        "npm i ",
        "pip install",
        "uv add",
        "uv pip install",
        "cargo install",
        "go get",
        "curl ",
        "wget ",
        "Invoke-WebRequest".lower(),
        "irm ",
        "iwr ",
    ]
    return any(token in lowered for token in network_tokens)


def _looks_like_test_or_build(lowered: str) -> bool:
    tokens = [
        "pytest",
        "ruff check",
        "mypy",
        "eslint",
        "npm run test",
        "npm test",
        "npm run lint",
        "npm run build",
        "npm run typecheck",
        "cargo test",
        "cargo build",
        "cargo clippy",
        "go test",
        "go build",
        "uv build",
    ]
    return any(token in lowered for token in tokens)


def _looks_like_development_process(lowered: str) -> bool:
    tokens = [
        "npm run dev",
        "npm run start",
        "pnpm dev",
        "pnpm start",
        "yarn dev",
        "yarn start",
        "bun run dev",
        "vite",
        "next dev",
        "uvicorn ",
        "flask run",
        "manage.py runserver",
        "cargo run",
        "go run ",
        "dotnet run",
        "dotnet watch",
    ]
    if any(token in lowered for token in tokens):
        return True
    return bool(
        re.fullmatch(
            r"(?:python(?:3|3\.\d+)?|py(?:\s+-\d+(?:\.\d+)?)?|node|ruby|perl)\s+"
            r"[^\s]+\.(?:py|js|mjs|cjs|rb|pl)(?:\s+[^;&|<>]*)?",
            lowered,
        )
    )


def _looks_like_git(lowered: str) -> bool:
    return lowered.startswith("git ") or " git " in lowered


def _looks_like_read_only(lowered: str) -> bool:
    first = _first_token(lowered)
    return first in {
        "dir",
        "ls",
        "get-childitem",
        "select-string",
        "get-content",
        "type",
        "cat",
        "rg",
        "findstr",
        "git",
    }


def _first_token(command: str) -> str:
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        parts = command.split()
    return parts[0].lower() if parts else ""
