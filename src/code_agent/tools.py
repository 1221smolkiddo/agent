from __future__ import annotations

import base64
import difflib
import html.parser
import inspect
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .schema import (
    AgentAction,
    ApplyPatchAction,
    DeleteFileAction,
    DependencyGraphAction,
    DetectVerificationAction,
    EditFileAction,
    InspectGitDiffAction,
    ListFilesAction,
    RankContextAction,
    ReadFileAction,
    RepoMapAction,
    RunShellAction,
    SearchAction,
    SuggestVerificationAction,
    SymbolIndexAction,
    SummarizeCodeAction,
    ToolResult,
    WebSearchAction,
    WriteFileAction,
)
from .parsing import summarize_code_file
from .repo_index import build_dependency_graph, build_repo_map, build_symbol_index, rank_context
from .safety import classify_network_url, classify_shell_command, is_sensitive_path, redact_secrets
from .verification import detect_verification_commands, suggest_verification_commands

IGNORED_NAMES = {
    ".code-agent",
    ".env",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
}

MAX_MUTATION_OUTPUT_CHARS = 12000
MAX_SHELL_OUTPUT_CHARS = 20000
SAFE_ENV_KEYS = {
    "ALLUSERSPROFILE",
    "APPDATA",
    "COMSPEC",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PATH",
    "PATHEXT",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "PSMODULEPATH",
    "PUBLIC",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERDOMAIN",
    "USERNAME",
    "USERPROFILE",
    "WINDIR",
}


class ToolRegistry:
    def __init__(
        self,
        workspace: Path,
        dry_run: bool,
        approval_callback: Callable[[str, str], bool] | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.dry_run = dry_run
        self.approval_callback = approval_callback

    def run(self, action: AgentAction) -> ToolResult:
        if isinstance(action, ListFilesAction):
            return self._list_files(action.path)
        if isinstance(action, ReadFileAction):
            return self._read_file(action.path)
        if isinstance(action, WriteFileAction):
            return self._write_file(action.path, action.content)
        if isinstance(action, EditFileAction):
            return self._edit_file(action.path, action.find, action.replace)
        if isinstance(action, ApplyPatchAction):
            return self._apply_patch(action.patch)
        if isinstance(action, DeleteFileAction):
            return self._delete_file(action.path)
        if isinstance(action, RunShellAction):
            return self._run_shell(action.command)
        if isinstance(action, SearchAction):
            return self._search(action.query, action.path)
        if isinstance(action, WebSearchAction):
            return self._web_search(action.query)
        if isinstance(action, SummarizeCodeAction):
            return self._summarize_code(action.path)
        if isinstance(action, DetectVerificationAction):
            return self._detect_verification()
        if isinstance(action, SuggestVerificationAction):
            return self._suggest_verification(action.changed_paths)
        if isinstance(action, InspectGitDiffAction):
            return self._inspect_git_diff(action.include_diff, action.max_chars)
        if isinstance(action, RepoMapAction):
            return self._repo_map(action.max_files)
        if isinstance(action, RankContextAction):
            return self._rank_context(action.task, action.max_results)
        if isinstance(action, SymbolIndexAction):
            return self._symbol_index(action.max_files, action.max_symbols)
        if isinstance(action, DependencyGraphAction):
            return self._dependency_graph(action.max_files, action.max_edges)
        return ToolResult(ok=False, output=f"Unsupported action: {action.type}")

    def resolve_inside_workspace(self, requested_path: str | None = None) -> Path:
        target = (self.workspace / (requested_path or ".")).resolve()
        if target != self.workspace and self.workspace not in target.parents:
            raise ValueError(f"Path escapes workspace: {requested_path}")
        return target

    def _list_files(self, requested_path: str | None) -> ToolResult:
        if not self._approve("list_files", f"List files in {requested_path or '.'}"):
            return ToolResult(ok=False, output="Permission denied for list_files.")
        target = self.resolve_inside_workspace(requested_path)
        entries = []
        for entry in sorted(target.iterdir(), key=lambda item: item.name.lower()):
            if entry.name in IGNORED_NAMES:
                continue
            prefix = "dir " if entry.is_dir() else "file"
            entries.append(f"{prefix} {entry.name}")
        return ToolResult(ok=True, output="\n".join(entries) or "<empty>")

    def _read_file(self, requested_path: str) -> ToolResult:
        target = self.resolve_inside_workspace(requested_path)
        if is_sensitive_path(target, self.workspace):
            return ToolResult(
                ok=False,
                output=(
                    f"Refusing to read sensitive file: {requested_path}. "
                    "Ask the user to provide only the specific non-secret value needed."
                ),
            )
        if not self._approve("read_file", requested_path):
            return ToolResult(ok=False, output="Permission denied for read_file.")
        return ToolResult(ok=True, output=redact_secrets(target.read_text(encoding="utf-8")))

    def _write_file(self, requested_path: str, content: str) -> ToolResult:
        if self.dry_run:
            return ToolResult(
                ok=False,
                output=(
                    "Dry-run mode skipped write_file. Tell the user to run /write, "
                    "/sandbox plus /write, or use --sandbox/without --dry-run before editing files."
                ),
            )
        target = self.resolve_inside_workspace(requested_path)
        if is_sensitive_path(target, self.workspace):
            return ToolResult(ok=False, output=f"Refusing to write sensitive file: {requested_path}.")
        before = target.read_text(encoding="utf-8") if target.exists() else ""
        diff = self._diff(requested_path, before, content)
        if not self._approve("write_file", diff or f"Create or overwrite {requested_path}"):
            return ToolResult(ok=False, output="Permission denied for write_file.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(ok=True, output=self._truncate(diff, MAX_MUTATION_OUTPUT_CHARS))

    def _edit_file(self, requested_path: str, find: str, replace: str) -> ToolResult:
        if self.dry_run:
            return ToolResult(
                ok=False,
                output=(
                    "Dry-run mode skipped edit_file. Tell the user to run /write, "
                    "/sandbox plus /write, or use --sandbox/without --dry-run before editing files."
                ),
            )
        target = self.resolve_inside_workspace(requested_path)
        if is_sensitive_path(target, self.workspace):
            return ToolResult(ok=False, output=f"Refusing to edit sensitive file: {requested_path}.")
        before = target.read_text(encoding="utf-8")
        if find not in before:
            return ToolResult(ok=False, output=f"Could not find exact text in {requested_path}")
        after = before.replace(find, replace, 1)
        diff = self._diff(requested_path, before, after)
        if not self._approve("edit_file", diff):
            return ToolResult(ok=False, output="Permission denied for edit_file.")
        target.write_text(after, encoding="utf-8")
        return ToolResult(ok=True, output=self._truncate(diff, MAX_MUTATION_OUTPUT_CHARS))

    def _apply_patch(self, patch: str) -> ToolResult:
        if self.dry_run:
            return ToolResult(
                ok=False,
                output=(
                    "Dry-run mode skipped apply_patch. Tell the user to run /write, "
                    "/sandbox plus /write, or use --sandbox/without --dry-run before editing files."
                ),
            )
        try:
            metadata = self._patch_metadata(patch)
            paths = set(metadata["paths"])
            if not paths:
                return ToolResult(ok=False, output="Patch does not contain any target file paths.")
            for path in paths:
                target = self.resolve_inside_workspace(path)
                if is_sensitive_path(target, self.workspace):
                    return ToolResult(ok=False, output=f"Refusing to patch sensitive file: {path}.")
        except ValueError as exc:
            return ToolResult(ok=False, output=str(exc))
        approval_detail = self._patch_approval_detail(patch, metadata)
        if not self._approve("apply_patch", approval_detail, metadata):
            return ToolResult(ok=False, output="Permission denied for apply_patch.")

        check = self._git_apply(patch, check=True)
        if not check.ok:
            check.metadata = metadata | {"stage": "check"}
            return check
        applied = self._git_apply(patch, check=False)
        applied.metadata = metadata | {"stage": "apply"}
        if applied.ok:
            applied.output = self._patch_applied_output(metadata)
        return applied

    def _delete_file(self, requested_path: str) -> ToolResult:
        if self.dry_run:
            return ToolResult(
                ok=False,
                output=(
                    "Dry-run mode skipped delete_file. Tell the user to run /write, "
                    "/sandbox plus /write, or use --sandbox/without --dry-run before deleting files."
                ),
            )
        try:
            target = self.resolve_inside_workspace(requested_path)
        except ValueError as exc:
            return ToolResult(ok=False, output=str(exc))
        if is_sensitive_path(target, self.workspace):
            return ToolResult(ok=False, output=f"Refusing to delete sensitive file: {requested_path}.")
        if not target.exists():
            return ToolResult(ok=False, output=f"File does not exist: {requested_path}")
        if not target.is_file():
            return ToolResult(ok=False, output=f"Refusing to delete non-file path: {requested_path}")
        before = target.read_text(encoding="utf-8")
        diff = self._diff(requested_path, before, "")
        if not self._approve("delete_file", diff or f"Delete {requested_path}"):
            return ToolResult(ok=False, output="Permission denied for delete_file.")
        target.unlink()
        return ToolResult(
            ok=True,
            output=self._truncate(f"Deleted {requested_path}.\n{diff}", MAX_MUTATION_OUTPUT_CHARS),
        )

    def _run_shell(self, command: str) -> ToolResult:
        if self.dry_run:
            return ToolResult(
                ok=False,
                output=(
                    "Dry-run mode skipped run_shell. Tell the user to run /write, "
                    "/sandbox plus /write, or use --sandbox/without --dry-run before shell commands."
                ),
            )
        policy = classify_shell_command(command)
        if not policy.allowed:
            return ToolResult(
                ok=False,
                output=(
                    f"Blocked {policy.category} shell command ({policy.risk} risk): "
                    f"{policy.reason}"
                ),
            )
        approval_detail = (
            f"Risk: {policy.risk}\n"
            f"Category: {policy.category}\n"
            f"Reason: {policy.reason}\n"
            f"May write files: {'yes' if policy.may_write else 'no'}\n"
            f"May access network: {'yes' if policy.may_network else 'no'}\n"
            f"Arbitrary code: {'yes' if policy.arbitrary_code else 'no'}\n"
            f"Timeout: {policy.timeout_seconds}s\n"
            f"Command: {command}"
        )
        if not self._approve("run_shell", approval_detail):
            return ToolResult(ok=False, output="Permission denied for run_shell.")
        try:
            if self._is_python_test_command(command):
                self._clear_python_bytecode_cache()
            completed = subprocess.run(
                command,
                cwd=self.workspace,
                shell=True,
                text=True,
                capture_output=True,
                timeout=policy.timeout_seconds,
                check=False,
                env=self._safe_shell_env(python_no_bytecode=self._is_python_test_command(command)),
            )
        except subprocess.TimeoutExpired as exc:
            output = "\n".join(
                self._coerce_process_output(part) for part in [exc.stdout, exc.stderr] if part
            ).strip()
            detail = f"Shell command timed out after {policy.timeout_seconds}s."
            if output:
                detail += "\n" + output
            return ToolResult(ok=False, output=redact_secrets(self._truncate(detail, MAX_SHELL_OUTPUT_CHARS)))
        output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
        return ToolResult(
            ok=completed.returncode == 0,
            output=redact_secrets(self._truncate(output, MAX_SHELL_OUTPUT_CHARS)) or "<no output>",
        )

    def _search(self, query: str, requested_path: str | None) -> ToolResult:
        if not self._approve("search", f"Search {requested_path or '.'} for {query}"):
            return ToolResult(ok=False, output="Permission denied for search.")
        target = self.resolve_inside_workspace(requested_path)
        try:
            completed = subprocess.run(
                [
                    "rg",
                    "--line-number",
                    "--hidden",
                    *[item for name in IGNORED_NAMES for item in ["--glob", f"!{name}"]],
                    query,
                    str(target),
                ],
                cwd=self.workspace,
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
        except FileNotFoundError:
            return ToolResult(ok=True, output=redact_secrets(self._python_search(query, target)))
        if completed.returncode not in {0, 1}:
            return ToolResult(ok=False, output=redact_secrets(completed.stderr.strip()))
        return ToolResult(ok=True, output=redact_secrets(completed.stdout.strip()) or "<no matches>")

    def _summarize_code(self, requested_path: str) -> ToolResult:
        if not self._approve("summarize_code", requested_path):
            return ToolResult(ok=False, output="Permission denied for summarize_code.")
        target = self.resolve_inside_workspace(requested_path)
        return ToolResult(ok=True, output=redact_secrets(summarize_code_file(target)))

    def _detect_verification(self) -> ToolResult:
        if not self._approve(
            "detect_verification",
            "Detect test, lint, typecheck, and build commands from project configuration.",
        ):
            return ToolResult(ok=False, output="Permission denied for detect_verification.")
        return ToolResult(ok=True, output=detect_verification_commands(self.workspace))

    def _suggest_verification(self, changed_paths: list[str]) -> ToolResult:
        detail = "Suggest focused verification commands"
        if changed_paths:
            detail += " for: " + ", ".join(changed_paths)
        if not self._approve("suggest_verification", detail):
            return ToolResult(ok=False, output="Permission denied for suggest_verification.")
        return ToolResult(ok=True, output=suggest_verification_commands(self.workspace, changed_paths))

    def _inspect_git_diff(self, include_diff: bool, max_chars: int) -> ToolResult:
        detail = "Inspect git status and changed paths"
        if include_diff:
            detail += " with diff hunks"
        if not self._approve("inspect_git_diff", detail):
            return ToolResult(ok=False, output="Permission denied for inspect_git_diff.")

        inside_work_tree = self._git(["rev-parse", "--is-inside-work-tree"], timeout=15)
        if not inside_work_tree.ok or inside_work_tree.output.strip() != "true":
            return ToolResult(ok=False, output="Workspace is not inside a git work tree.")

        sections = [
            ("Status", self._git(["status", "--short"], timeout=30)),
            ("Unstaged changes", self._git(["diff", "--name-status"], timeout=30)),
            ("Staged changes", self._git(["diff", "--cached", "--name-status"], timeout=30)),
        ]
        output_parts: list[str] = []
        for title, result in sections:
            if not result.ok:
                return result
            output_parts.append(f"{title}:\n{result.output.strip() or '<clean>'}")

        if include_diff:
            diff_sections = [
                ("Unstaged diff", self._git(["diff", "--no-ext-diff"], timeout=60)),
                ("Staged diff", self._git(["diff", "--cached", "--no-ext-diff"], timeout=60)),
            ]
            for title, result in diff_sections:
                if not result.ok:
                    return result
                output_parts.append(f"{title}:\n{result.output.strip() or '<empty>'}")

        output = "\n\n".join(output_parts)
        return ToolResult(ok=True, output=self._truncate(redact_secrets(output), max_chars))

    def _repo_map(self, max_files: int) -> ToolResult:
        if not self._approve("repo_map", "Build a lightweight repository map and important-file summary."):
            return ToolResult(ok=False, output="Permission denied for repo_map.")
        return ToolResult(ok=True, output=build_repo_map(self.workspace, max_files=max_files))

    def _rank_context(self, task: str, max_results: int) -> ToolResult:
        if not self._approve(
            "rank_context",
            f"Rank likely relevant files for task: {task}",
        ):
            return ToolResult(ok=False, output="Permission denied for rank_context.")
        return ToolResult(ok=True, output=rank_context(self.workspace, task, max_results=max_results))

    def _symbol_index(self, max_files: int, max_symbols: int) -> ToolResult:
        if not self._approve(
            "symbol_index",
            "Build a compact symbol index from source and test files.",
        ):
            return ToolResult(ok=False, output="Permission denied for symbol_index.")
        return ToolResult(
            ok=True,
            output=build_symbol_index(
                self.workspace,
                max_files=max_files,
                max_symbols=max_symbols,
            ),
        )

    def _dependency_graph(self, max_files: int, max_edges: int) -> ToolResult:
        if not self._approve(
            "dependency_graph",
            "Build a lightweight dependency graph from source and test imports.",
        ):
            return ToolResult(ok=False, output="Permission denied for dependency_graph.")
        return ToolResult(
            ok=True,
            output=build_dependency_graph(
                self.workspace,
                max_files=max_files,
                max_edges=max_edges,
            ),
        )

    def _web_search(self, query: str) -> ToolResult:
        approval_detail = (
            f"Risk: medium\n"
            f"Category: public-web-search\n"
            f"Providers: www.bing.com, duckduckgo.com\n"
            f"Query: {query}"
        )
        if not self._approve("web_search", approval_detail):
            return ToolResult(ok=False, output="Permission denied for web_search.")

        results = self._search_bing(query)
        if not results:
            results = self._search_duckduckgo(query)
        results = self._filter_safe_web_results(results)
        if not results:
            return ToolResult(
                ok=False,
                output=(
                    "No web results were found from the configured search providers. "
                    "Try a more specific query or another source."
                ),
            )
        return ToolResult(
            ok=True,
            output=redact_secrets(
                "\n".join(f"{index + 1}. {title}\n{href}" for index, (title, href) in enumerate(results))
            ),
        )

    def _approve(self, action: str, detail: str, metadata: dict[str, Any] | None = None) -> bool:
        if self.approval_callback is None:
            return False
        if metadata is not None and self._callback_accepts_metadata():
            return self.approval_callback(action, detail, metadata)
        return self.approval_callback(action, detail)

    def _callback_accepts_metadata(self) -> bool:
        if self.approval_callback is None:
            return False
        try:
            signature = inspect.signature(self.approval_callback)
        except (TypeError, ValueError):
            return False
        positional = 0
        for parameter in signature.parameters.values():
            if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
                return True
            if parameter.kind in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            }:
                positional += 1
        return positional >= 3

    def _search_bing(self, query: str) -> list[tuple[str, str]]:
        url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query})
        html = self._fetch_url(url)
        parser = BingParser()
        parser.feed(html)
        return parser.results[:5]

    def _search_duckduckgo(self, query: str) -> list[tuple[str, str]]:
        url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        html = self._fetch_url(url)
        parser = DuckDuckGoParser()
        parser.feed(html)
        return parser.results[:5]

    @staticmethod
    def _fetch_url(url: str) -> str:
        policy = classify_network_url(url)
        if not policy.allowed:
            raise ValueError(
                f"Blocked {policy.category} web target ({policy.risk} risk): {policy.reason}"
            )
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Agent47/0.1 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="replace")

    @staticmethod
    def _filter_safe_web_results(results: list[tuple[str, str]]) -> list[tuple[str, str]]:
        return [(title, href) for title, href in results if classify_network_url(href).allowed]

    def _python_search(self, query: str, target: Path) -> str:
        try:
            pattern = re.compile(query)
        except re.error:
            pattern = re.compile(re.escape(query))

        files = [target] if target.is_file() else sorted(target.rglob("*"))
        matches: list[str] = []
        for path in files:
            if self._is_ignored_path(path) or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            relative = path.relative_to(self.workspace)
            for line_number, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    matches.append(f"{relative}:{line_number}:{line}")
        return "\n".join(matches) or "<no matches>"

    def _is_ignored_path(self, path: Path) -> bool:
        try:
            relative = path.relative_to(self.workspace)
        except ValueError:
            return True
        return any(part in IGNORED_NAMES for part in relative.parts)

    @staticmethod
    def _safe_shell_env(*, python_no_bytecode: bool = False) -> dict[str, str]:
        env: dict[str, str] = {}
        for key, value in os.environ.items():
            upper = key.upper()
            if upper in SAFE_ENV_KEYS:
                env[key] = value
        env["AGENT47_SANDBOXED_SHELL"] = "1"
        if python_no_bytecode:
            env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    @staticmethod
    def _is_python_test_command(command: str) -> bool:
        lowered = command.lower()
        return "pytest" in lowered

    def _clear_python_bytecode_cache(self) -> None:
        for cache_dir in self.workspace.rglob("__pycache__"):
            shutil.rmtree(cache_dir, ignore_errors=True)

    @staticmethod
    def _coerce_process_output(value: str | bytes) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

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

    @staticmethod
    def _paths_from_patch(patch: str) -> set[str]:
        return set(ToolRegistry._patch_metadata(patch)["paths"])

    @staticmethod
    def _patch_metadata(patch: str) -> dict[str, Any]:
        files: dict[str, dict[str, Any]] = {}
        current_paths: set[str] = set()
        for line in patch.splitlines():
            if line.startswith("diff --git "):
                current_paths = set()
                parts = line.split()
                if len(parts) >= 4:
                    for raw_path in [parts[2], parts[3]]:
                        path = ToolRegistry._clean_patch_path(raw_path)
                        if path:
                            current_paths.add(path)
                            files.setdefault(path, ToolRegistry._empty_patch_file_metadata(path))
                continue
            if line.startswith("--- ") or line.startswith("+++ "):
                if line.startswith("--- "):
                    current_paths = set()
                raw_path = line[4:].split("\t", 1)[0].strip()
                path = ToolRegistry._clean_patch_path(raw_path)
                if path:
                    current_paths.add(path)
                    files.setdefault(path, ToolRegistry._empty_patch_file_metadata(path))
                continue
            if not current_paths:
                continue
            if line.startswith("+") and not line.startswith("+++"):
                for path in current_paths:
                    files[path]["additions"] += 1
            elif line.startswith("-") and not line.startswith("---"):
                for path in current_paths:
                    files[path]["deletions"] += 1

        for item in files.values():
            additions = int(item["additions"])
            deletions = int(item["deletions"])
            if additions and deletions:
                item["operation"] = "update"
            elif additions:
                item["operation"] = "create_or_update"
            elif deletions:
                item["operation"] = "delete_or_update"
            else:
                item["operation"] = "metadata_only"

        ordered_files = sorted(files.values(), key=lambda item: str(item["path"]))
        return {
            "kind": "unified_diff_change_set",
            "file_count": len(ordered_files),
            "paths": [str(item["path"]) for item in ordered_files],
            "files": ordered_files,
            "total_additions": sum(int(item["additions"]) for item in ordered_files),
            "total_deletions": sum(int(item["deletions"]) for item in ordered_files),
        }

    @staticmethod
    def _empty_patch_file_metadata(path: str) -> dict[str, Any]:
        return {"path": path, "operation": "update", "additions": 0, "deletions": 0}

    @staticmethod
    def _patch_approval_detail(patch: str, metadata: dict[str, Any]) -> str:
        file_lines = [
            (
                f"- {item['path']}: {item['operation']}, "
                f"+{item['additions']} -{item['deletions']}"
            )
            for item in metadata.get("files", [])
        ]
        summary = [
            "Patch preview:",
            f"Files: {metadata['file_count']}",
            f"Total changes: +{metadata['total_additions']} -{metadata['total_deletions']}",
            *file_lines,
            "",
            "Unified diff:",
            patch,
        ]
        return "\n".join(summary)

    @staticmethod
    def _patch_applied_output(metadata: dict[str, Any]) -> str:
        file_lines = [
            f"- {item['path']}: {item['operation']} (+{item['additions']} -{item['deletions']})"
            for item in metadata.get("files", [])
        ]
        return "\n".join(
            [
                "Patch applied.",
                f"Files changed: {metadata['file_count']}",
                f"Total changes: +{metadata['total_additions']} -{metadata['total_deletions']}",
                *file_lines,
            ]
        )

    @staticmethod
    def _clean_patch_path(path: str) -> str | None:
        if path == "/dev/null":
            return None
        for prefix in ("a/", "b/"):
            if path.startswith(prefix):
                return path[len(prefix) :]
        return path

    def _git_apply(self, patch: str, check: bool) -> ToolResult:
        command = ["git", "apply", "--whitespace=nowarn"]
        if check:
            command.append("--check")
        completed = subprocess.run(
            command,
            cwd=self.workspace,
            input=patch,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
        if completed.returncode == 0:
            return ToolResult(ok=True, output="Patch can be applied." if check else "Patch applied.")
        stage = "Patch check failed" if check else "Patch apply failed"
        return ToolResult(ok=False, output=redact_secrets(f"{stage}: {output or '<no output>'}"))

    def _git(self, args: list[str], timeout: int) -> ToolResult:
        completed = subprocess.run(
            ["git", *args],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
        return ToolResult(ok=completed.returncode == 0, output=redact_secrets(output))

    @staticmethod
    def _truncate(output: str, max_chars: int) -> str:
        if len(output) <= max_chars:
            return output
        remaining = len(output) - max_chars
        return output[:max_chars].rstrip() + f"\n<truncated {remaining} chars>"


class DuckDuckGoParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[tuple[str, str]] = []
        self._in_result_link = False
        self._current_href = ""
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        class_name = attrs_dict.get("class", "")
        if tag == "a" and "result__a" in class_name:
            self._in_result_link = True
            self._current_href = attrs_dict.get("href") or ""
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._in_result_link:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._in_result_link:
            return
        title = " ".join("".join(self._current_text).split())
        href = self._clean_href(self._current_href)
        if title and href:
            self.results.append((title, href))
        self._in_result_link = False
        self._current_href = ""
        self._current_text = []

    @staticmethod
    def _clean_href(href: str) -> str:
        if href.startswith("//duckduckgo.com/l/?"):
            parsed = urllib.parse.urlparse("https:" + href)
            query = urllib.parse.parse_qs(parsed.query)
            return query.get("uddg", [href])[0]
        return href


class BingParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[tuple[str, str]] = []
        self._in_result = False
        self._in_heading = False
        self._in_title_link = False
        self._current_href = ""
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        class_name = attrs_dict.get("class", "")
        if tag == "li" and "b_algo" in class_name:
            self._in_result = True
            return
        if tag == "h2" and self._in_result:
            self._in_heading = True
            return
        if tag == "a" and self._in_result and self._in_heading and not self._in_title_link:
            href = attrs_dict.get("href") or ""
            if href.startswith("http"):
                self._in_title_link = True
                self._current_href = self._clean_href(href)
                self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._in_title_link:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title_link:
            title = " ".join("".join(self._current_text).split())
            if title and self._current_href:
                self.results.append((title, self._current_href))
            self._in_title_link = False
            self._current_href = ""
            self._current_text = []
            return
        if tag == "h2" and self._in_heading:
            self._in_heading = False
            return
        if tag == "li" and self._in_result:
            self._in_result = False
            self._in_heading = False

    @staticmethod
    def _clean_href(href: str) -> str:
        parsed = urllib.parse.urlparse(href)
        if parsed.netloc.endswith("bing.com") and parsed.path.startswith("/ck/"):
            values = urllib.parse.parse_qs(parsed.query).get("u", [])
            if not values:
                match = re.search(r"[?&]u=([^&]+)", href)
                if match:
                    values = [urllib.parse.unquote(match.group(1))]
            if values:
                encoded = values[0]
                if encoded.startswith("a1"):
                    encoded = encoded[2:]
                padding = "=" * (-len(encoded) % 4)
                try:
                    return base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    return href
        return href
