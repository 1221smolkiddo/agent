from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SANDBOX_EXCLUDES = {
    ".code-agent",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}

SANDBOX_FILE_EXCLUDES = {
    ".env",
}


@dataclass(frozen=True)
class SandboxWorkspace:
    source: Path
    path: Path


def create_sandbox_workspace(source: Path) -> SandboxWorkspace:
    resolved_source = source.resolve()
    sandbox_root = resolved_source / ".code-agent" / "sandboxes"
    sandbox_root.mkdir(parents=True, exist_ok=True)
    sandbox_name = datetime.now(timezone.utc).strftime("sandbox-%Y%m%d-%H%M%S")
    sandbox_path = sandbox_root / sandbox_name

    shutil.copytree(
        resolved_source,
        sandbox_path,
        ignore=_ignore_sandbox_entries,
    )
    return SandboxWorkspace(source=resolved_source, path=sandbox_path)


def _ignore_sandbox_entries(_directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in SANDBOX_EXCLUDES or name in SANDBOX_FILE_EXCLUDES:
            ignored.add(name)
    return ignored
