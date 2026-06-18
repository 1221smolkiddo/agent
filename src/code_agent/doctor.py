from __future__ import annotations

import json
import os
import platform
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import Settings


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
        for check in self.checks:
            suffix = f" Hint: {check.hint}" if check.hint else ""
            lines.append(f"- {check.status.upper()} {check.name}: {check.detail}{suffix}")
        return "\n".join(lines)


def run_doctor(cwd: Path | None = None, settings: Settings | None = None) -> DoctorReport:
    workspace = (cwd or Path.cwd()).resolve()
    config = settings or Settings()
    checks = [
        _check_python_version(),
        _check_supported_platform(),
        _check_workspace_writable(workspace),
        _check_storage(config.agent_db_path),
        _check_console_scripts(),
        _check_command("git", required=False, hint="Install git for patch application and git diff awareness."),
        _check_command(
            "rg",
            required=False,
            hint="Install ripgrep for faster project search; Agent47 has a slower Python fallback.",
        ),
        _check_api_key(),
        _check_env_file(workspace),
    ]
    return DoctorReport(
        platform=f"{platform.system()} {platform.release()}",
        python=sys.version.split()[0],
        cwd=str(workspace),
        checks=checks,
    )


def _check_python_version() -> DoctorCheck:
    version = sys.version_info
    if version >= (3, 11):
        return DoctorCheck("python-version", "pass", f"{version.major}.{version.minor}.{version.micro}")
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
        with tempfile.NamedTemporaryFile(prefix=".agent47-doctor-", dir=workspace, delete=True):
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
    return DoctorCheck("sqlite-storage", "pass", str(target))


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


def _check_api_key() -> DoctorCheck:
    if os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        return DoctorCheck("api-key", "pass", "configured")
    return DoctorCheck(
        "api-key",
        "warn",
        "not configured",
        "Set OPENROUTER_API_KEY before running live model tasks.",
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
