from __future__ import annotations

from .schema import (
    AgentAction,
    ApplyPatchAction,
    DetectVerificationAction,
    EditFileAction,
    ListFilesAction,
    ReadFileAction,
    RunShellAction,
    SearchAction,
    SuggestVerificationAction,
    SummarizeCodeAction,
    WebSearchAction,
    WriteFileAction,
)
from .terminal_ui import print_status_line


class StatusReporter:
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
