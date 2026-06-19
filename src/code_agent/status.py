from __future__ import annotations

from pathlib import Path

from .schema import (
    AgentAction,
    ApplyPatchAction,
    DeleteFileAction,
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
    UpdatePlanAction,
    WebSearchAction,
    WriteFileAction,
)
from .terminal_ui import print_status_line, print_stream_end, print_stream_marker, print_stream_start


class StatusReporter:
    def __init__(self) -> None:
        self._stream_chars = 0
        self._has_plan = False
        self._consecutive_retries = 0

    def thinking(self, step: int) -> None:
        if self._has_plan:
            print_status_line("PLANNING", f"step {step}")
        else:
            print_status_line("THINKING", f"step {step}")

    def action(self, action: AgentAction) -> None:
        self._consecutive_retries = 0
        status = format_action_status(action)
        if status is None:
            # Suppressed action (e.g. internal plan updates)
            if isinstance(action, UpdatePlanAction):
                self._has_plan = True
            return
        label, _, detail = status.partition(" ")
        print_status_line(label, detail)
        if isinstance(action, UpdatePlanAction):
            self._has_plan = True

    def recovery(self, detail: str) -> None:
        self._consecutive_retries += 1
        # Only surface retries on 2nd+ consecutive failure
        if self._consecutive_retries >= 2:
            print_status_line("RETRYING", friendly_retry_detail(detail))

    def done(self) -> None:
        # Issue #20: The response itself implies completion; suppress DONE line
        pass

    def model_stream_start(self, step: int) -> None:
        self._stream_chars = 0
        print_stream_start(f"model response for step {step}")

    def model_stream_chunk(self, chunk: str) -> None:
        self._stream_chars += len(chunk)
        if self._stream_chars >= 120:
            print_stream_marker()
            self._stream_chars = 0

    def model_stream_end(self) -> None:
        print_stream_end()

    def workspace_analysis(self, summary: str) -> None:
        """Report workspace discovery results."""
        print_status_line("WORKSPACE", summary)

    def mutation_preview(
        self,
        creates: list[str],
        modifies: list[str],
        deletes: list[str],
    ) -> None:
        """Show file operation preview before execution."""
        parts: list[str] = []
        if creates:
            parts.append("create: " + ", ".join(creates))
        if modifies:
            parts.append("modify: " + ", ".join(modifies))
        if deletes:
            parts.append("delete: " + ", ".join(deletes))
        if parts:
            print_status_line("PREVIEW", "; ".join(parts))


def format_action_status(action: AgentAction) -> str | None:
    if isinstance(action, ListFilesAction):
        return f"READING listing {action.path or '.'}"
    if isinstance(action, ReadFileAction):
        return f"READING {action.path}"
    if isinstance(action, WriteFileAction):
        return f"EDITING writing {action.path}"
    if isinstance(action, EditFileAction):
        return f"EDITING {action.path}"
    if isinstance(action, ApplyPatchAction):
        return "EDITING applying patch"
    if isinstance(action, DeleteFileAction):
        return f"EDITING deleting {action.path}"
    if isinstance(action, SearchAction):
        return f"SEARCHING project for {action.query}"
    if isinstance(action, WebSearchAction):
        return f"SEARCHING WEB for {action.query}"
    if isinstance(action, SummarizeCodeAction):
        return f"ANALYZING code structure in {action.path}"
    if isinstance(action, DetectVerificationAction):
        return "CHECKING project verification commands"
    if isinstance(action, SuggestVerificationAction):
        return "CHECKING suggested verification"
    if isinstance(action, InspectGitDiffAction):
        return "READING git changes"
    if isinstance(action, RepoMapAction):
        return "ANALYZING repository map"
    if isinstance(action, RankContextAction):
        return "ANALYZING relevant context"
    if isinstance(action, SymbolIndexAction):
        return "ANALYZING symbol index"
    if isinstance(action, UpdatePlanAction):
        # Issue #15: Suppress internal plan update status lines;
        # the plan panel already surfaces plan state.
        return None
    if isinstance(action, RunShellAction):
        return format_shell_status(action.command)
    return f"WORKING {action.type}"


def format_shell_status(command: str) -> str:
    normalized = command.lower()
    if any(token in normalized for token in [" install", "pip install", "uv add", "npm i", "npm install"]):
        return f"INSTALLING packages with {command}"
    if any(token in normalized for token in [" test", "pytest", "npm run test", "cargo test"]):
        return f"TESTING with {command}"
    if any(token in normalized for token in [" build", "npm run build", "cargo build", "uv build"]):
        return f"BUILDING with {command}"
    if any(token in normalized for token in [" lint", "ruff check", "eslint"]):
        return f"CHECKING with {command}"
    return f"RUNNING shell command {command}"


def friendly_retry_detail(detail: str) -> str:
    lowered = detail.lower()
    if "invalid model action" in lowered or "parse" in lowered:
        return "retrying with a cleaner response"
    if "non-workspace" in lowered or "blocked workspace tool" in lowered:
        return "switching back to chat mode"
    if "tool failed" in lowered:
        return "trying another approach"
    if "model failed" in lowered:
        return "model call failed"
    return "trying again"


def analyze_workspace(cwd: Path) -> str:
    """Detect workspace type and produce a human-readable summary."""
    indicators: list[str] = []

    if (cwd / "package.json").exists():
        indicators.append("Node.js project (package.json)")
    if (cwd / "pyproject.toml").exists():
        indicators.append("Python project (pyproject.toml)")
    if (cwd / "Cargo.toml").exists():
        indicators.append("Rust project (Cargo.toml)")
    if (cwd / "go.mod").exists():
        indicators.append("Go project (go.mod)")
    if (cwd / "Makefile").exists():
        indicators.append("Makefile found")
    if (cwd / "Dockerfile").exists():
        indicators.append("Docker project")

    # Detect key directories
    key_dirs = ["src", "tests", "test", "lib", "docs", "scripts"]
    found_dirs = [d for d in key_dirs if (cwd / d).is_dir()]
    if found_dirs:
        indicators.append("dirs: " + ", ".join(found_dirs))

    if not indicators:
        # Check if empty
        children = list(cwd.iterdir())
        non_hidden = [c for c in children if not c.name.startswith(".")]
        if not non_hidden:
            return "Empty directory"
        return f"Directory with {len(non_hidden)} items"

    return "; ".join(indicators)
