from __future__ import annotations

from pathlib import Path

import typer

from .config import Settings
from .factory import create_agent
from .permissions import confirm_permission
from .storage import AgentStorage


def main() -> None:
    settings = Settings()
    cwd = Path.cwd()
    model: str | None = None
    dry_run = True
    max_steps = 12

    typer.echo("agent47 interactive coding agent")
    typer.echo("Type a task or question. Use /help for commands. Use /exit to quit.")
    typer.echo(f"Workspace: {cwd}")
    typer.echo(f"Mode: {'dry-run' if dry_run else 'write-enabled'}")

    while True:
        try:
            user_input = typer.prompt("agent47").strip()
        except (EOFError, KeyboardInterrupt):
            typer.echo("\nbye")
            return

        if not user_input:
            continue

        if user_input.startswith("/"):
            command_result = handle_command(user_input, settings, cwd, model, dry_run, max_steps)
            if command_result.exit_requested:
                typer.echo("bye")
                return
            cwd = command_result.cwd
            model = command_result.model
            dry_run = command_result.dry_run
            max_steps = command_result.max_steps
            continue

        agent = create_agent(
            settings=settings,
            cwd=cwd,
            model=model,
            dry_run=dry_run,
            max_steps=max_steps,
            approval_callback=confirm_permission,
        )
        typer.echo(agent.run(user_input))


class CommandState:
    def __init__(
        self,
        cwd: Path,
        model: str | None,
        dry_run: bool,
        max_steps: int,
        exit_requested: bool = False,
    ) -> None:
        self.cwd = cwd
        self.model = model
        self.dry_run = dry_run
        self.max_steps = max_steps
        self.exit_requested = exit_requested


def handle_command(
    raw: str,
    settings: Settings,
    cwd: Path,
    model: str | None,
    dry_run: bool,
    max_steps: int,
) -> CommandState:
    parts = raw.split(maxsplit=1)
    command = parts[0].lower()
    value = parts[1].strip() if len(parts) > 1 else ""

    if command in {"/exit", "/quit", "/q"}:
        return CommandState(cwd, model, dry_run, max_steps, exit_requested=True)
    if command == "/help":
        print_help()
    elif command == "/dry-run":
        dry_run = True
        typer.echo("Mode: dry-run")
    elif command == "/write":
        dry_run = False
        typer.echo("Mode: write-enabled")
    elif command == "/cwd":
        if value:
            cwd = Path(value).expanduser().resolve()
        typer.echo(f"Workspace: {cwd}")
    elif command == "/model":
        if value:
            model = value
        typer.echo(f"Model: {model or settings.agent_model}")
    elif command == "/max-steps":
        if value:
            max_steps = int(value)
        typer.echo(f"Max steps: {max_steps}")
    elif command == "/history":
        print_history(settings)
    elif command == "/status":
        typer.echo(f"Workspace: {cwd}")
        typer.echo(f"Model: {model or settings.agent_model}")
        typer.echo(f"Mode: {'dry-run' if dry_run else 'write-enabled'}")
        typer.echo(f"Max steps: {max_steps}")
    else:
        typer.echo(f"Unknown command: {command}")
        typer.echo("Use /help to see available commands.")

    return CommandState(cwd, model, dry_run, max_steps)


def print_help() -> None:
    typer.echo(
        """
Commands:
  /help              Show this help.
  /status            Show current workspace, model, mode, and max steps.
  /dry-run           Inspect only; skip writes and shell commands.
  /write             Allow writes and shell commands.
  /cwd <path>        Change workspace.
  /model <name>      Change model for this session.
  /max-steps <n>     Change max agent loop steps.
  /history           Show recent saved agent runs.
  /exit              Quit.
""".strip()
    )


def print_history(settings: Settings) -> None:
    storage = AgentStorage(settings.agent_db_path)
    rows = storage.recent_runs(10)
    if not rows:
        typer.echo("No runs recorded yet.")
        return
    for row in rows:
        typer.echo(f"{row['id']} | {row['created_at']} | {row['model']} | {row['task']}")
