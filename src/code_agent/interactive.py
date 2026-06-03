from __future__ import annotations

from pathlib import Path

import typer

from .config import Settings
from .factory import create_agent
from .permissions import confirm_permission
from .sandbox import create_sandbox_workspace
from .storage import AgentStorage


def main() -> None:
    settings = Settings()
    base_cwd = Path.cwd().resolve()
    cwd = base_cwd
    model: str | None = None
    dry_run = True
    sandbox_enabled = False
    max_steps = 12
    max_failures: int | None = None

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
            command_result = handle_command(
                user_input,
                settings,
                base_cwd,
                cwd,
                model,
                dry_run,
                sandbox_enabled,
                max_steps,
                max_failures,
            )
            if command_result.exit_requested:
                typer.echo("bye")
                return
            base_cwd = command_result.base_cwd
            cwd = command_result.cwd
            model = command_result.model
            dry_run = command_result.dry_run
            sandbox_enabled = command_result.sandbox_enabled
            max_steps = command_result.max_steps
            max_failures = command_result.max_failures
            continue

        agent = create_agent(
            settings=settings,
            cwd=cwd,
            model=model,
            dry_run=dry_run,
            max_steps=max_steps,
            max_failures=max_failures,
            approval_callback=confirm_permission,
        )
        typer.echo(agent.run(user_input))


class CommandState:
    def __init__(
        self,
        base_cwd: Path,
        cwd: Path,
        model: str | None,
        dry_run: bool,
        sandbox_enabled: bool,
        max_steps: int,
        max_failures: int | None,
        exit_requested: bool = False,
    ) -> None:
        self.base_cwd = base_cwd
        self.cwd = cwd
        self.model = model
        self.dry_run = dry_run
        self.sandbox_enabled = sandbox_enabled
        self.max_steps = max_steps
        self.max_failures = max_failures
        self.exit_requested = exit_requested


def handle_command(
    raw: str,
    settings: Settings,
    base_cwd: Path,
    cwd: Path,
    model: str | None,
    dry_run: bool,
    sandbox_enabled: bool,
    max_steps: int,
    max_failures: int | None,
) -> CommandState:
    parts = raw.split(maxsplit=1)
    command = parts[0].lower()
    value = parts[1].strip() if len(parts) > 1 else ""

    if command in {"/exit", "/quit", "/q"}:
        return CommandState(
            base_cwd,
            cwd,
            model,
            dry_run,
            sandbox_enabled,
            max_steps,
            max_failures,
            exit_requested=True,
        )
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
            base_cwd = Path(value).expanduser().resolve()
            cwd = base_cwd
            sandbox_enabled = False
        typer.echo(f"Workspace: {cwd}")
    elif command == "/sandbox":
        if value.lower() == "off":
            cwd = base_cwd
            sandbox_enabled = False
            typer.echo("Sandbox: off")
            typer.echo(f"Workspace: {cwd}")
        else:
            sandbox_workspace = create_sandbox_workspace(base_cwd)
            cwd = sandbox_workspace.path
            sandbox_enabled = True
            typer.echo("Sandbox: on")
            typer.echo(f"Sandbox workspace: {cwd}")
    elif command == "/model":
        if value:
            model = value
        typer.echo(f"Model: {model or settings.agent_model}")
    elif command == "/max-steps":
        if value:
            max_steps = int(value)
        typer.echo(f"Max steps: {max_steps}")
    elif command == "/max-failures":
        if value:
            max_failures = int(value)
        typer.echo(f"Max failures: {max_failures or settings.agent_max_failures}")
    elif command == "/history":
        print_history(settings)
    elif command == "/status":
        typer.echo(f"Base workspace: {base_cwd}")
        typer.echo(f"Workspace: {cwd}")
        typer.echo(f"Model: {model or settings.agent_model}")
        typer.echo(f"Mode: {'dry-run' if dry_run else 'write-enabled'}")
        typer.echo(f"Sandbox: {'on' if sandbox_enabled else 'off'}")
        typer.echo(f"Max steps: {max_steps}")
        typer.echo(f"Max failures: {max_failures or settings.agent_max_failures}")
    else:
        typer.echo(f"Unknown command: {command}")
        typer.echo("Use /help to see available commands.")

    return CommandState(base_cwd, cwd, model, dry_run, sandbox_enabled, max_steps, max_failures)


def print_help() -> None:
    typer.echo(
        """
Commands:
  /help              Show this help.
  /status            Show current workspace, model, mode, and max steps.
  /dry-run           Inspect only; skip writes and shell commands.
  /write             Allow writes and shell commands.
  /cwd <path>        Change workspace.
  /sandbox [off]     Create and use a sandbox copy, or turn it off.
  /model <name>      Change model for this session.
  /max-steps <n>     Change max agent loop steps.
  /max-failures <n>  Change consecutive failure recovery budget.
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
