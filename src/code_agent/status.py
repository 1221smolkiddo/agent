from __future__ import annotations

import typer

from .schema import (
    AgentAction,
    EditFileAction,
    ListFilesAction,
    ReadFileAction,
    RunShellAction,
    SearchAction,
    SummarizeCodeAction,
    WebSearchAction,
    WriteFileAction,
)


class StatusReporter:
    def thinking(self, step: int) -> None:
        typer.echo(f"THINKING step {step}")

    def action(self, action: AgentAction) -> None:
        typer.echo(format_action_status(action))

    def recovery(self, detail: str) -> None:
        typer.echo(f"RECOVERING {detail}")

    def done(self) -> None:
        typer.echo("DONE")


def format_action_status(action: AgentAction) -> str:
    if isinstance(action, ListFilesAction):
        return f"READING listing {action.path or '.'}"
    if isinstance(action, ReadFileAction):
        return f"READING {action.path}"
    if isinstance(action, WriteFileAction):
        return f"EDITING writing {action.path}"
    if isinstance(action, EditFileAction):
        return f"EDITING {action.path}"
    if isinstance(action, SearchAction):
        return f"SEARCHING project for {action.query}"
    if isinstance(action, WebSearchAction):
        return f"SEARCHING WEB for {action.query}"
    if isinstance(action, SummarizeCodeAction):
        return f"ANALYZING code structure in {action.path}"
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
