from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VerificationCommand:
    purpose: str
    command: str
    source: str


def detect_verification_commands(workspace: Path) -> str:
    commands: list[VerificationCommand] = []
    commands.extend(_python_commands(workspace))
    commands.extend(_node_commands(workspace))
    commands.extend(_rust_commands(workspace))
    commands.extend(_go_commands(workspace))

    if not commands:
        return "No verification commands detected from known project files."

    lines = ["Detected verification commands:"]
    seen: set[tuple[str, str]] = set()
    for item in commands:
        key = (item.purpose, item.command)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {item.purpose}: {item.command} ({item.source})")
    return "\n".join(lines)


def _python_commands(workspace: Path) -> list[VerificationCommand]:
    pyproject = workspace / "pyproject.toml"
    if not pyproject.exists():
        return []

    commands: list[VerificationCommand] = []
    data = _read_toml(pyproject)
    uses_uv = (workspace / "uv.lock").exists()
    runner = "uv run" if uses_uv else "python -m"
    tool_data = data.get("tool", {}) if isinstance(data, dict) else {}
    project_data = data.get("project", {}) if isinstance(data, dict) else {}

    if "pytest" in tool_data or _dependency_mentions(project_data, "pytest"):
        command = "uv run pytest" if uses_uv else "python -m pytest"
        commands.append(VerificationCommand("test", command, "pyproject.toml"))

    if "ruff" in tool_data or _dependency_mentions(project_data, "ruff"):
        target = "src tests" if (workspace / "src").exists() and (workspace / "tests").exists() else "."
        commands.append(VerificationCommand("lint", f"{runner} ruff check {target}", "pyproject.toml"))

    if "mypy" in tool_data or _dependency_mentions(project_data, "mypy"):
        commands.append(VerificationCommand("typecheck", f"{runner} mypy .", "pyproject.toml"))

    if "hatch" in data.get("build-system", {}).get("build-backend", ""):
        command = "uv build" if uses_uv else "python -m build"
        commands.append(VerificationCommand("build", command, "pyproject.toml"))

    return commands


def _node_commands(workspace: Path) -> list[VerificationCommand]:
    package_json = workspace / "package.json"
    if not package_json.exists():
        return []

    data = json.loads(package_json.read_text(encoding="utf-8"))
    scripts = data.get("scripts", {})
    if not isinstance(scripts, dict):
        return []

    commands: list[VerificationCommand] = []
    for purpose, script_names in {
        "test": ["test"],
        "lint": ["lint"],
        "typecheck": ["typecheck", "check"],
        "build": ["build"],
    }.items():
        for script in script_names:
            if script in scripts:
                commands.append(VerificationCommand(purpose, f"npm run {script}", "package.json"))
                break
    return commands


def _rust_commands(workspace: Path) -> list[VerificationCommand]:
    if not (workspace / "Cargo.toml").exists():
        return []
    return [
        VerificationCommand("test", "cargo test", "Cargo.toml"),
        VerificationCommand("build", "cargo build", "Cargo.toml"),
        VerificationCommand("lint", "cargo clippy", "Cargo.toml"),
    ]


def _go_commands(workspace: Path) -> list[VerificationCommand]:
    if not (workspace / "go.mod").exists():
        return []
    return [
        VerificationCommand("test", "go test ./...", "go.mod"),
        VerificationCommand("build", "go build ./...", "go.mod"),
    ]


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return {}


def _dependency_mentions(project_data: object, package: str) -> bool:
    if not isinstance(project_data, dict):
        return False
    candidates: list[str] = []
    dependencies = project_data.get("dependencies", [])
    if isinstance(dependencies, list):
        candidates.extend(str(item) for item in dependencies)
    optional = project_data.get("optional-dependencies", {})
    if isinstance(optional, dict):
        for values in optional.values():
            if isinstance(values, list):
                candidates.extend(str(item) for item in values)
    return any(item.lower().startswith(package.lower()) for item in candidates)
