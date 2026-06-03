from __future__ import annotations

import difflib
import html.parser
import subprocess
import urllib.parse
import urllib.request
from collections.abc import Callable
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
    WebSearchAction,
    WriteFileAction,
)
from .parsing import summarize_code_file


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
        if isinstance(action, RunShellAction):
            return self._run_shell(action.command)
        if isinstance(action, SearchAction):
            return self._search(action.query, action.path)
        if isinstance(action, WebSearchAction):
            return self._web_search(action.query)
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
            if entry.name in {
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
            }:
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
        diff = self._diff(requested_path, before, content)
        if not self._approve("write_file", diff or f"Create or overwrite {requested_path}"):
            return ToolResult(ok=False, output="Permission denied for write_file.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(ok=True, output=diff)

    def _edit_file(self, requested_path: str, find: str, replace: str) -> ToolResult:
        if self.dry_run:
            return ToolResult(ok=False, output="Dry-run mode skipped edit_file.")
        target = self.resolve_inside_workspace(requested_path)
        before = target.read_text(encoding="utf-8")
        if find not in before:
            return ToolResult(ok=False, output=f"Could not find exact text in {requested_path}")
        after = before.replace(find, replace, 1)
        diff = self._diff(requested_path, before, after)
        if not self._approve("edit_file", diff):
            return ToolResult(ok=False, output="Permission denied for edit_file.")
        target.write_text(after, encoding="utf-8")
        return ToolResult(ok=True, output=diff)

    def _run_shell(self, command: str) -> ToolResult:
        if self.dry_run:
            return ToolResult(ok=False, output="Dry-run mode skipped run_shell.")
        if not self._approve("run_shell", command):
            return ToolResult(ok=False, output="Permission denied for run_shell.")
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

    def _web_search(self, query: str) -> ToolResult:
        if not self._approve("web_search", query):
            return ToolResult(ok=False, output="Permission denied for web_search.")

        url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "agent47/0.1"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            html = response.read().decode("utf-8", errors="replace")

        parser = DuckDuckGoParser()
        parser.feed(html)
        results = parser.results[:5]
        if not results:
            return ToolResult(ok=True, output="<no web results>")
        return ToolResult(
            ok=True,
            output="\n".join(f"{index + 1}. {title}\n{href}" for index, (title, href) in enumerate(results)),
        )

    def _approve(self, action: str, detail: str) -> bool:
        if self.approval_callback is None:
            return False
        return self.approval_callback(action, detail)

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
