from __future__ import annotations

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

    def thinking(self, step: int) -> None:
        print_status_line("THINKING", f"step {step}")

    def action(self, action: AgentAction) -> None:
        status = format_action_status(action)
        label, _, detail = status.partition(" ")
        print_status_line(label, detail)

    def recovery(self, detail: str) -> None:
        print_status_line("RECOVERING", detail)

    def done(self) -> None:
        print_status_line("DONE", "")

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


def format_action_status(action: AgentAction) -> str:
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
        return "PLANNING updating task plan"
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
