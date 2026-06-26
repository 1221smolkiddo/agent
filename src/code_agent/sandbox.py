from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import ApplyPatchAction
from .tools import ToolRegistry
from .patches import git_style_unified_diff


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


@dataclass(frozen=True)
class SandboxDiff:
    base: Path
    sandbox: Path
    changed_paths: list[str]
    patch: str
    skipped_paths: list[str]

    @property
    def has_changes(self) -> bool:
        return bool(self.changed_paths)


@dataclass(frozen=True)
class SandboxApplyResult:
    ok: bool
    changed_paths: list[str]
    output: str
    metadata: dict[str, Any]


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


def diff_sandbox_workspace(
    base: Path,
    sandbox: Path,
    *,
    paths: list[str] | None = None,
) -> SandboxDiff:
    resolved_base = base.resolve()
    resolved_sandbox = sandbox.resolve()
    selected = _normalize_selected_paths(paths)
    base_files = _indexed_files(resolved_base, selected)
    sandbox_files = _indexed_files(resolved_sandbox, selected)
    all_paths = sorted(set(base_files) | set(sandbox_files))
    patch_parts: list[str] = []
    changed_paths: list[str] = []
    skipped_paths: list[str] = []

    for relative in all_paths:
        base_path = resolved_base / relative
        sandbox_path = resolved_sandbox / relative
        try:
            before = base_path.read_text(encoding="utf-8").splitlines()
            before_exists = base_path.is_file()
        except (OSError, UnicodeDecodeError):
            before = []
            before_exists = base_path.is_file()
            if before_exists:
                skipped_paths.append(relative)
                continue
        try:
            after = sandbox_path.read_text(encoding="utf-8").splitlines()
            after_exists = sandbox_path.is_file()
        except (OSError, UnicodeDecodeError):
            after = []
            after_exists = sandbox_path.is_file()
            if after_exists:
                skipped_paths.append(relative)
                continue

        if before_exists and after_exists and before == after:
            continue
        if not before_exists and not after_exists:
            continue

        changed_paths.append(relative)
        diff = git_style_unified_diff(
            relative,
            "\n".join(before),
            "\n".join(after),
            before_exists=before_exists,
            after_exists=after_exists,
        )
        if diff:
            patch_parts.append(diff.rstrip() + "\n")

    return SandboxDiff(
        base=resolved_base,
        sandbox=resolved_sandbox,
        changed_paths=changed_paths,
        patch="\n".join(patch_parts).rstrip() + ("\n" if patch_parts else ""),
        skipped_paths=skipped_paths,
    )


def format_sandbox_diff(diff: SandboxDiff) -> str:
    lines = [
        "Sandbox diff:",
        f"- base: {diff.base}",
        f"- sandbox: {diff.sandbox}",
        f"- changed files: {len(diff.changed_paths)}",
    ]
    if diff.changed_paths:
        lines.extend(f"- {path}" for path in diff.changed_paths)
    if diff.skipped_paths:
        lines.append(f"- skipped non-text/unreadable files: {', '.join(diff.skipped_paths)}")
    if diff.patch:
        lines.extend(["", diff.patch])
    elif not diff.changed_paths:
        lines.extend(["", "<no sandbox changes>"])
    return "\n".join(lines)


def format_sandbox_limits() -> str:
    return "\n".join(
        [
            "Sandbox limits:",
            "- uses a copied workspace, not OS-level process isolation",
            "- excludes local state such as .env, .git, .venv, caches, node_modules, and .code-agent",
            "- shell commands still run as local processes inside the sandbox path",
            "- network access follows normal shell policy unless --deny-network-shell or AGENT_SHELL_NETWORK=deny is used",
            "- base workspace files change only after explicit sandbox apply promotion",
        ]
    )


def promote_sandbox_changes(
    base: Path,
    sandbox: Path,
    *,
    paths: list[str] | None = None,
    approval_callback=None,
) -> SandboxApplyResult:
    diff = diff_sandbox_workspace(base, sandbox, paths=paths)
    if diff.skipped_paths:
        return SandboxApplyResult(
            ok=False,
            changed_paths=diff.changed_paths,
            output="Refusing to apply sandbox changes with unreadable files: "
            + ", ".join(diff.skipped_paths),
            metadata={"skipped_paths": diff.skipped_paths},
        )
    if not diff.patch:
        return SandboxApplyResult(
            ok=True,
            changed_paths=[],
            output="No sandbox changes to apply.",
            metadata={"changed_paths": []},
        )

    tools = ToolRegistry(
        workspace=diff.base,
        dry_run=False,
        approval_callback=approval_callback,
    )
    result = tools.run(ApplyPatchAction(type="apply_patch", patch=diff.patch))
    if not result.ok:
        return SandboxApplyResult(
            ok=False,
            changed_paths=diff.changed_paths,
            output=result.output,
            metadata=result.metadata,
        )

    verification_error = _verify_promoted_files(diff)
    if verification_error:
        return SandboxApplyResult(
            ok=False,
            changed_paths=diff.changed_paths,
            output=verification_error,
            metadata=result.metadata,
        )
    return SandboxApplyResult(
        ok=True,
        changed_paths=diff.changed_paths,
        output=result.output,
        metadata=result.metadata,
    )


def _ignore_sandbox_entries(_directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in SANDBOX_EXCLUDES or name in SANDBOX_FILE_EXCLUDES:
            ignored.add(name)
    return ignored


def _indexed_files(root: Path, selected: set[str] | None) -> set[str]:
    files: set[str] = set()
    for path in root.rglob("*"):
        if _is_ignored_path(path, root) or not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if selected is not None and relative not in selected:
            continue
        files.add(relative)
    return files


def _normalize_selected_paths(paths: list[str] | None) -> set[str] | None:
    if not paths:
        return None
    selected: set[str] = set()
    for path in paths:
        normalized = path.replace("\\", "/").strip()
        if not normalized or normalized.startswith("/") or ":" in normalized:
            raise ValueError(f"Sandbox paths must be workspace-relative: {path}")
        if ".." in normalized.split("/"):
            raise ValueError(f"Sandbox paths must not contain '..': {path}")
        selected.add(normalized)
    return selected


def _is_ignored_path(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    return any(part in SANDBOX_EXCLUDES or part in SANDBOX_FILE_EXCLUDES for part in relative.parts)


def _verify_promoted_files(diff: SandboxDiff) -> str | None:
    for relative in diff.changed_paths:
        base_path = diff.base / relative
        sandbox_path = diff.sandbox / relative
        if sandbox_path.exists() != base_path.exists():
            return f"Sandbox promotion verification failed for {relative}: existence mismatch."
        if not sandbox_path.exists():
            continue
        try:
            base_content = base_path.read_text(encoding="utf-8")
            sandbox_content = sandbox_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return f"Sandbox promotion verification failed for {relative}: {exc}"
        if base_content != sandbox_content:
            return f"Sandbox promotion verification failed for {relative}: content mismatch."
    return None
