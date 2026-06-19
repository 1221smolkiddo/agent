from __future__ import annotations

import re
import shlex
import ipaddress
import urllib.parse
from dataclasses import dataclass
from pathlib import Path


SENSITIVE_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".npmrc",
    ".pypirc",
    ".netrc",
}

SECRET_VALUE_PATTERNS = [
    re.compile(r"(?i)\b(authorization\s*[:=]\s*bearer\s+)([A-Za-z0-9._~+/=-]{16,})"),
    re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/=-]{16,})"),
    re.compile(
        r"(?i)\b(api[_-]?key|token|secret|password|passwd|credential)\b"
        r"(\s*[:=]\s*)"
        r"([^\s'\"]+)"
    ),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{16,})\b"),
]

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


def is_sensitive_path(path: Path, workspace: Path) -> bool:
    try:
        relative = path.resolve().relative_to(workspace.resolve())
    except ValueError:
        return True
    return any(part.lower() in SENSITIVE_FILE_NAMES for part in relative.parts)


def redact_secrets(value: str) -> str:
    redacted = value
    for pattern in SECRET_VALUE_PATTERNS:
        if pattern.pattern.lower().startswith("(?i)\\b(authorization"):
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        elif pattern.pattern.lower().startswith("(?i)\\b(bearer"):
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        elif pattern.pattern.startswith("\\b(sk-"):
            redacted = pattern.sub("[REDACTED]", redacted)
        else:
            redacted = pattern.sub(r"\1\2[REDACTED]", redacted)
    return redacted


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
            timeout_seconds=180,
        )
    if _looks_like_test_or_build(lowered):
        return ShellPolicy(
            "verification",
            "medium",
            True,
            "Runs project verification.",
            may_write=True,
            timeout_seconds=120,
        )
    if _looks_like_git(lowered):
        return ShellPolicy(
            "git",
            "medium",
            True,
            "Touches git metadata or repository state.",
            may_write=True,
            timeout_seconds=60,
        )
    if _looks_like_read_only(lowered):
        return ShellPolicy(
            "read-only",
            "low",
            True,
            "Inspects local state without obvious mutation.",
            timeout_seconds=30,
        )
    return ShellPolicy(
        "unknown",
        "high",
        False,
        "Unclassified shell commands are blocked until an explicit policy class is added.",
        arbitrary_code=True,
        timeout_seconds=0,
    )


def classify_network_url(url: str) -> NetworkPolicy:
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
    if not host:
        return NetworkPolicy(
            host="<none>",
            category="invalid-url",
            risk="high",
            allowed=False,
            reason="URL does not contain a host.",
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
        return NetworkPolicy(
            host=host,
            category="public-web",
            risk="medium",
            allowed=True,
            reason="Public web target.",
        )
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    ):
        return NetworkPolicy(
            host=host,
            category="local-network",
            risk="critical",
            allowed=False,
            reason="Private, loopback, reserved, link-local, and multicast web targets are blocked.",
        )
    return NetworkPolicy(
        host=host,
        category="public-web",
        risk="medium",
        allowed=True,
        reason="Public web target.",
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
