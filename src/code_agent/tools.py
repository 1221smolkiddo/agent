from __future__ import annotations

import difflib
import subprocess
from pathlib import Path

from .schema import (
    AgentAction,
    EditFileAction,
    ListFilesAction,
    ReadFileAction,
    RunShellAction,
    SearchAction,
    SummarizeCodeAction,
    ToolResult,
    WriteFileAction,
)
from .parsing import summarize_code_file


class ToolRegistry:
    def __init__(self, workspace: Path, dry_run: bool) -> None:
        self.workspace = workspace.resolve()
        self.dry_run = dry_run

    def run(self, action: AgentAction) -> ToolResult:
        if isinstance(action, ListFilesAction):
            return self._list_files(action.path)
        if isinstance(action, ReadFileAction):
            return self._read_file(action.path)
        if isinstance(action, WriteFileAction):
            return self._write_file(action.path, action.content)
        if isinstance(action, EditFileAction):
            return self._edit_file(action.path, action.find, action.replace)
        if isinstance(action, RunShellAction):
            return self._run_shell(action.command)
        if isinstance(action, SearchAction):
            return self._search(action.query, action.path)
        if isinstance(action, SummarizeCodeAction):
            return self._summarize_code(action.path)
        return ToolResult(ok=False, output=f"Unsupported action: {action.type}")

    def resolve_inside_workspace(self, requested_path: str | None = None) -> Path:
        target = (self.workspace / (requested_path or ".")).resolve()
        if target != self.workspace and self.workspace not in target.parents:
            raise ValueError(f"Path escapes workspace: {requested_path}")
        return target

    def _list_files(self, requested_path: str | None) -> ToolResult:
        target = self.resolve_inside_workspace(requested_path)
        entries = []
        for entry in sorted(target.iterdir(), key=lambda item: item.name.lower()):
            if entry.name in {".git", ".venv", "node_modules", "__pycache__", "dist"}:
                continue
            prefix = "dir " if entry.is_dir() else "file"
            entries.append(f"{prefix} {entry.name}")
        return ToolResult(ok=True, output="\n".join(entries) or "<empty>")

    def _read_file(self, requested_path: str) -> ToolResult:
        target = self.resolve_inside_workspace(requested_path)
        return ToolResult(ok=True, output=target.read_text(encoding="utf-8"))

    def _write_file(self, requested_path: str, content: str) -> ToolResult:
        if self.dry_run:
            return ToolResult(ok=False, output="Dry-run mode skipped write_file.")
        target = self.resolve_inside_workspace(requested_path)
        before = target.read_text(encoding="utf-8") if target.exists() else ""
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(ok=True, output=self._diff(requested_path, before, content))

    def _edit_file(self, requested_path: str, find: str, replace: str) -> ToolResult:
        if self.dry_run:
            return ToolResult(ok=False, output="Dry-run mode skipped edit_file.")
        target = self.resolve_inside_workspace(requested_path)
        before = target.read_text(encoding="utf-8")
        if find not in before:
            return ToolResult(ok=False, output=f"Could not find exact text in {requested_path}")
        after = before.replace(find, replace, 1)
        target.write_text(after, encoding="utf-8")
        return ToolResult(ok=True, output=self._diff(requested_path, before, after))

    def _run_shell(self, command: str) -> ToolResult:
        if self.dry_run:
            return ToolResult(ok=False, output="Dry-run mode skipped run_shell.")
        completed = subprocess.run(
            command,
            cwd=self.workspace,
            shell=True,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
        return ToolResult(ok=completed.returncode == 0, output=output or "<no output>")

    def _search(self, query: str, requested_path: str | None) -> ToolResult:
        target = self.resolve_inside_workspace(requested_path)
        completed = subprocess.run(
            ["rg", "--line-number", "--hidden", "--glob", "!.git", query, str(target)],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if completed.returncode not in {0, 1}:
            return ToolResult(ok=False, output=completed.stderr.strip())
        return ToolResult(ok=True, output=completed.stdout.strip() or "<no matches>")

    def _summarize_code(self, requested_path: str) -> ToolResult:
        target = self.resolve_inside_workspace(requested_path)
        return ToolResult(ok=True, output=summarize_code_file(target))

    @staticmethod
    def _diff(path: str, before: str, after: str) -> str:
        return "\n".join(
            difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )
        )
