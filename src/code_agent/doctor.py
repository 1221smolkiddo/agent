"""Comprehensive environment diagnostics for Agent47.

Covers: Python version, platform, workspace, storage, console scripts,
external tools (git, rg, docker/podman), API keys, model configuration,
authentication, session validity, keyring health, internet connectivity,
stored provider keys, and provider reachability.
"""

from __future__ import annotations

import json
import platform
import shutil
import socket
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import Settings
from .memory import MAX_MEMORY_FILE_CHARS, memory_file_path
from .model_presets import resolve_model_preset
from .model_registry import find_registered_model, provider_name_list
from .storage import AgentStorage


DoctorStatus = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: DoctorStatus
    detail: str
    hint: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "hint": self.hint,
        }


@dataclass(frozen=True)
class DoctorReport:
    platform: str
    python: str
    cwd: str
    checks: list[DoctorCheck]

    @property
    def ok(self) -> bool:
        return not any(check.status == "fail" for check in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(check.status == "warn" for check in self.checks)

    def as_dict(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "python": self.python,
            "cwd": self.cwd,
            "ok": self.ok,
            "checks": [check.as_dict() for check in self.checks],
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True)

    def format_text(self) -> str:
        lines = [
            "Agent47 doctor",
            f"- platform: {self.platform}",
            f"- python: {self.python}",
            f"- cwd: {self.cwd}",
            "",
            "Checks:",
        ]
        use_unicode = True
        try:
            encoding = getattr(sys.stdout, "encoding", None) or "ascii"
            "✓".encode(encoding)
        except (UnicodeEncodeError, LookupError):
            use_unicode = False

        status_icons = (
            {"pass": "✓", "warn": "⚠", "fail": "✗"}
            if use_unicode
            else {"pass": "[OK]", "warn": "[!]", "fail": "[X]"}
        )
        for check in self.checks:
            icon = status_icons.get(check.status, "?")
            suffix = f"  Hint: {check.hint}" if check.hint else ""
            lines.append(f"  {icon} {check.status.upper()} {check.name}: {check.detail}{suffix}")
        return "\n".join(lines)


def run_doctor(
    cwd: Path | None = None,
    settings: Settings | None = None,
    include_performance: bool = False,
) -> DoctorReport:
    workspace = (cwd or Path.cwd()).resolve()
    config = settings or Settings.for_workspace(workspace)
    checks = [
        # ── Runtime ──
        _check_python_version(),
        _check_supported_platform(),
        _check_workspace_writable(workspace),
        _check_storage(config.agent_db_path),
        _check_console_scripts(),
        # ── External Tools ──
        _check_command(
            "git", required=False, hint="Install git for patch application and git diff awareness."
        ),
        _check_command(
            "rg",
            required=False,
            hint="Install ripgrep for faster project search; Agent47 has a slower Python fallback.",
        ),
        _check_docker_or_podman(),
        # ── Configuration ──
        _check_api_key(config),
        _check_model_deadlines(config),
        _check_fallback_models(config),
        _check_env_file(workspace),
        _check_project_memory(workspace),
        # ── Authentication & Credentials ──
        _check_authentication(),
        _check_session(),
        _check_keyring(),
        _check_stored_provider_keys(),
        # ── Network ──
        _check_internet(),
        _check_provider_reachability(config),
    ]
    if include_performance:
        checks.extend(_check_performance_metrics(workspace, config))
    return DoctorReport(
        platform=f"{platform.system()} {platform.release()}",
        python=sys.version.split()[0],
        cwd=str(workspace),
        checks=checks,
    )


# ======================================================================
# Runtime checks
# ======================================================================


def _check_python_version() -> DoctorCheck:
    version = sys.version_info
    if version >= (3, 11):
        return DoctorCheck(
            "python-version", "pass", f"{version.major}.{version.minor}.{version.micro}"
        )
    return DoctorCheck(
        "python-version",
        "fail",
        f"{version.major}.{version.minor}.{version.micro}",
        "Install Python 3.11 or newer.",
    )


def _check_supported_platform() -> DoctorCheck:
    system = platform.system().lower()
    if system in {"windows", "darwin", "linux"}:
        return DoctorCheck("platform", "pass", platform.platform())
    return DoctorCheck(
        "platform",
        "warn",
        platform.platform(),
        "Agent47 is tested primarily on Windows, macOS, and Linux.",
    )


def _check_workspace_writable(workspace: Path) -> DoctorCheck:
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=".agent47-doctor-", dir=workspace, delete=True
        ):
            pass
    except OSError as exc:
        return DoctorCheck(
            "workspace-writable",
            "fail",
            str(exc),
            "Choose a writable workspace or fix directory permissions.",
        )
    return DoctorCheck("workspace-writable", "pass", str(workspace))


def _check_storage(db_path: Path) -> DoctorCheck:
    try:
        target = db_path
        if not target.is_absolute():
            target = Path.cwd() / target
        target.parent.mkdir(parents=True, exist_ok=True)
        storage = AgentStorage(target)
        with sqlite3.connect(target) as conn:
            conn.execute("select 1")
    except sqlite3.Error as exc:
        return DoctorCheck(
            "sqlite-storage",
            "fail",
            str(exc),
            "Set AGENT_DB_PATH to a writable SQLite location.",
        )
    except OSError as exc:
        return DoctorCheck(
            "sqlite-storage",
            "fail",
            str(exc),
            "Set AGENT_DB_PATH to a writable SQLite location.",
        )
    return DoctorCheck(
        "sqlite-storage",
        "pass",
        f"{target} (schema v{storage.schema_version})",
    )


def _check_console_scripts() -> DoctorCheck:
    missing = [name for name in ["code-agent", "agent47"] if shutil.which(name) is None]
    if not missing:
        return DoctorCheck("console-scripts", "pass", "code-agent and agent47 are on PATH")
    return DoctorCheck(
        "console-scripts",
        "warn",
        "missing from PATH: " + ", ".join(missing),
        "Use `uv run ...`, activate the venv, or install with pipx/pip.",
    )


# ======================================================================
# External tools
# ======================================================================


def _check_command(command: str, *, required: bool, hint: str) -> DoctorCheck:
    path = shutil.which(command)
    if path:
        return DoctorCheck(f"command-{command}", "pass", path)
    return DoctorCheck(
        f"command-{command}",
        "fail" if required else "warn",
        "not found on PATH",
        hint,
    )


def _check_docker_or_podman() -> DoctorCheck:
    docker = shutil.which("docker")
    podman = shutil.which("podman")
    if docker and podman:
        return DoctorCheck("container-runtime", "pass", f"docker={docker}, podman={podman}")
    if docker:
        return DoctorCheck("container-runtime", "pass", f"docker={docker}")
    if podman:
        return DoctorCheck("container-runtime", "pass", f"podman={podman}")
    return DoctorCheck(
        "container-runtime",
        "warn",
        "neither docker nor podman found on PATH",
        "Install Docker or Podman for container sandbox isolation.",
    )


# ======================================================================
# Configuration checks
# ======================================================================


def _check_model_deadlines(settings: Settings) -> DoctorCheck:
    return DoctorCheck(
        "model-deadlines", "pass",
        f"turn={settings.agent_model_timeout_seconds:g}s; no overall run timeout; Agent47-owned retries",
    )


def _check_fallback_models(settings: Settings) -> DoctorCheck:
    models = settings.fallback_model_list
    if not models:
        return DoctorCheck(
            "model-fallback",
            "pass",
            "no fallback models configured (optional; startup warning enabled)",
            "Set AGENT_FALLBACK_MODELS to one or more models with configured provider credentials.",
        )
    missing: list[str] = []
    for model in models:
        registered = find_registered_model(model)
        provider = registered.provider if registered else settings.provider_name
        try:
            settings.model_api_key_for(provider)
        except RuntimeError:
            missing.append(f"{model} ({provider})")
    if missing:
        return DoctorCheck(
            "model-fallback",
            "warn",
            f"{len(models)} configured; missing credentials for: {', '.join(missing)}",
            "Configure the required provider keys or remove unusable fallback models.",
        )
    return DoctorCheck(
        "model-fallback",
        "pass",
        f"{len(models)} configured with available provider credentials",
    )


def _check_api_key(settings: Settings) -> DoctorCheck:
    try:
        preset = resolve_model_preset(settings.agent_model_preset)
        provider = settings.provider_name_for(preset.provider if preset else None)
    except RuntimeError as exc:
        return DoctorCheck(
            "api-key",
            "fail",
            str(exc),
            f"Set AGENT_PROVIDER to one of: {provider_name_list()}.",
        )
    except ValueError as exc:
        return DoctorCheck(
            "api-key",
            "fail",
            str(exc),
            "Set AGENT_MODEL_PRESET to a known preset or leave it empty.",
        )
    try:
        settings.model_api_key_for(provider)
        return DoctorCheck("api-key", "pass", f"configured for provider {provider}")
    except RuntimeError as exc:
        return DoctorCheck(
            "api-key",
            "warn",
            f"not configured for provider {provider}",
            str(exc),
        )


def _check_env_file(workspace: Path) -> DoctorCheck:
    env_path = workspace / ".env"
    example_path = workspace / ".env.example"
    if env_path.exists():
        return DoctorCheck("env-file", "pass", ".env exists")
    if example_path.exists():
        return DoctorCheck(
            "env-file",
            "warn",
            ".env is missing but .env.example exists",
            "Copy .env.example to .env and fill in your API key.",
        )
    return DoctorCheck(
        "env-file",
        "warn",
        ".env and .env.example are missing",
        "Create .env or configure environment variables directly.",
    )


def _check_project_memory(workspace: Path) -> DoctorCheck:
    path = memory_file_path(workspace)
    if not path.exists():
        return DoctorCheck(
            "project-memory",
            "pass",
            ".code-agent/memory/project.md not initialized yet",
        )
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return DoctorCheck(
            "project-memory",
            "fail",
            f"{path} is not UTF-8 text",
            "Rewrite project memory as UTF-8 Markdown or delete it to regenerate.",
        )
    except OSError as exc:
        return DoctorCheck(
            "project-memory",
            "fail",
            str(exc),
            "Fix .code-agent/memory permissions or delete the damaged memory file.",
        )
    if len(content) > MAX_MEMORY_FILE_CHARS:
        return DoctorCheck(
            "project-memory",
            "warn",
            f"{path} is {len(content)} chars",
            f"Keep project memory below {MAX_MEMORY_FILE_CHARS} chars.",
        )
    return DoctorCheck("project-memory", "pass", f"{path} ({len(content)} chars)")


# ======================================================================
# Authentication & Credentials
# ======================================================================


def _check_authentication() -> DoctorCheck:
    """Check whether a Google account profile is stored locally."""
    try:
        from .account.profile import AccountStore

        account = AccountStore().load()
    except Exception:
        return DoctorCheck(
            "authentication",
            "warn",
            "could not read account profile",
            "Run 'agent47 auth login' to sign in.",
        )
    if account:
        return DoctorCheck(
            "authentication",
            "pass",
            f"signed in as {account.email} (provider: {account.provider})",
        )
    return DoctorCheck(
        "authentication",
        "warn",
        "not signed in",
        "Run 'agent47 auth login' to sign in with Google.",
    )


def _check_session() -> DoctorCheck:
    """Check whether OAuth tokens are available and include a refresh token."""
    try:
        from .credentials.keyring import CredentialStore

        store = CredentialStore()
        tokens = store.get_oauth_tokens()
    except Exception:
        return DoctorCheck(
            "session",
            "warn",
            "could not access credential store",
            "Check keyring availability with 'agent47 doctor'.",
        )
    if not tokens:
        return DoctorCheck(
            "session",
            "warn",
            "no OAuth tokens stored",
            "Run 'agent47 auth login' to sign in.",
        )
    has_refresh = isinstance(tokens.get("refresh_token"), str) and bool(
        tokens.get("refresh_token")
    )
    has_access = isinstance(tokens.get("access_token"), str) and bool(
        tokens.get("access_token")
    )
    parts = []
    if has_access:
        parts.append("access token present")
    if has_refresh:
        parts.append("refresh token present")
    if not parts:
        return DoctorCheck(
            "session",
            "warn",
            "tokens stored but incomplete",
            "Run 'agent47 auth repair' or 'agent47 auth login'.",
        )
    return DoctorCheck("session", "pass", "; ".join(parts))


def _check_keyring() -> DoctorCheck:
    """Check that the OS credential store is accessible and using a secure backend."""
    try:
        from .credentials.keyring import CredentialStore

        CredentialStore._backend()
        backend = CredentialStore.backend_name()
        return DoctorCheck("keyring", "pass", f"secure backend: {backend}")
    except Exception as exc:
        from .credentials.keyring import keyring_setup_hint

        return DoctorCheck(
            "keyring",
            "fail",
            str(exc).split("\n")[0],
            keyring_setup_hint(),
        )


def _check_stored_provider_keys() -> DoctorCheck:
    """Count how many provider API keys are stored in the keyring."""
    try:
        from .credentials.keyring import CredentialStore
        from .credentials.providers import provider_specs

        store = CredentialStore()
        configured = []
        for spec in provider_specs():
            try:
                if store.get_provider_key(spec.name):
                    configured.append(spec.display_name)
            except Exception:
                pass
        if configured:
            return DoctorCheck(
                "stored-api-keys",
                "pass",
                f"{len(configured)} provider(s): {', '.join(configured)}",
            )
        return DoctorCheck(
            "stored-api-keys",
            "warn",
            "no provider API keys in secure storage",
            "Run 'agent47 keys add <provider>' to store keys securely.",
        )
    except Exception:
        return DoctorCheck(
            "stored-api-keys",
            "warn",
            "could not query keyring",
            "Check keyring availability.",
        )


# ======================================================================
# Network checks
# ======================================================================


def _check_internet() -> DoctorCheck:
    """Basic connectivity check via DNS resolution."""
    hosts = ["accounts.google.com", "api.openai.com", "dns.google"]
    resolved = []
    for host in hosts:
        try:
            socket.getaddrinfo(host, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
            resolved.append(host)
        except (socket.gaierror, OSError):
            pass
    if resolved:
        return DoctorCheck("internet", "pass", f"DNS resolved: {', '.join(resolved)}")
    return DoctorCheck(
        "internet",
        "warn",
        "could not resolve any test hosts",
        "Check your network connection and DNS configuration.",
    )


def _check_provider_reachability(settings: Settings) -> DoctorCheck:
    """Lightweight TCP connect test to the configured provider."""
    try:
        preset = resolve_model_preset(settings.agent_model_preset)
        provider = settings.provider_name_for(preset.provider if preset else None)
    except (RuntimeError, ValueError):
        return DoctorCheck(
            "provider-reachability",
            "warn",
            "could not determine provider",
            "Configure AGENT_PROVIDER.",
        )

    host_by_provider = {
        "openrouter": "openrouter.ai",
        "openai": "api.openai.com",
        "gemini": "generativelanguage.googleapis.com",
        "deepseek": "api.deepseek.com",
        "nvidia": "integrate.api.nvidia.com",
        "groq": "api.groq.com",
        "anthropic": "api.anthropic.com",
    }
    host = host_by_provider.get(provider)
    if not host:
        return DoctorCheck(
            "provider-reachability",
            "warn",
            f"no known endpoint for provider {provider}",
            "Use 'agent47 keys test <provider>' for a full check.",
        )
    try:
        sock = socket.create_connection((host, 443), timeout=5)
        sock.close()
        return DoctorCheck("provider-reachability", "pass", f"{host}:443 reachable")
    except (socket.timeout, OSError):
        return DoctorCheck(
            "provider-reachability",
            "warn",
            f"could not reach {host}:443",
            "Check your network or firewall settings.",
        )


def _check_performance_metrics(workspace: Path, config: Settings) -> list[DoctorCheck]:
    import subprocess
    import time
    checks = []

    # 1. Storage Connection & Init Latency
    t0 = time.perf_counter()
    try:
        _ = AgentStorage(config.agent_db_path)
        dur = (time.perf_counter() - t0) * 1000
        checks.append(DoctorCheck("perf-storage", "pass" if dur < 150 else "warn", f"Storage connection/init: {dur:.2f}ms"))
    except Exception as exc:
        checks.append(DoctorCheck("perf-storage", "warn", f"Storage performance check failed: {exc}"))

    # 2. Repo Indexing Speed
    t0 = time.perf_counter()
    try:
        from .repo_index import index_repo
        files = index_repo(workspace)
        dur = (time.perf_counter() - t0) * 1000
        checks.append(DoctorCheck("perf-repo-index", "pass" if dur < 1000 else "warn", f"Indexed {len(files)} files in {dur:.2f}ms"))
    except Exception as exc:
        checks.append(DoctorCheck("perf-repo-index", "warn", f"Repo index performance check failed: {exc}"))

    # 3. Process Spawn Overhead
    t0 = time.perf_counter()
    try:
        subprocess.run(["git", "--version"], capture_output=True, text=True, check=False)
        dur = (time.perf_counter() - t0) * 1000
        checks.append(DoctorCheck("perf-process-spawn", "pass" if dur < 250 else "warn", f"Subprocess spawn latency: {dur:.2f}ms"))
    except Exception as exc:
        checks.append(DoctorCheck("perf-process-spawn", "warn", f"Process spawn check failed: {exc}"))

    return checks
