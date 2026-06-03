from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from .config import Settings
from .factory import create_agent
from .permissions import confirm_permission
from .sandbox import create_sandbox_workspace
from .storage import AgentStorage
from .status import StatusReporter

app = typer.Typer(help="A CLI-first coding agent.")


@app.command()
def run(
    task: str = typer.Argument(..., help="The coding task for the agent."),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    model: Optional[str] = typer.Option(None, "--model", help="Model override."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Inspect only; skip writes and shell."),
    sandbox: bool = typer.Option(False, "--sandbox", help="Run inside an isolated workspace copy."),
    max_steps: int = typer.Option(12, "--max-steps", min=1, help="Maximum agent loop steps."),
    max_failures: Optional[int] = typer.Option(
        None,
        "--max-failures",
        min=1,
        help="Consecutive failures before the agent stops retrying.",
    ),
) -> None:
    """Run the coding agent on a task."""
    settings = Settings()
    workspace = cwd.resolve()
    if sandbox:
        sandbox_workspace = create_sandbox_workspace(workspace)
        workspace = sandbox_workspace.path
        typer.echo(f"Sandbox: {workspace}")

    agent = create_agent(
        settings=settings,
        cwd=workspace,
        model=model,
        dry_run=dry_run,
        max_steps=max_steps,
        max_failures=max_failures,
        approval_callback=confirm_permission,
        reporter=StatusReporter(),
    )
    try:
        result = agent.run(task)
    except KeyboardInterrupt:
        typer.echo("\nSTOPPED by user")
        raise typer.Exit(code=130)
    typer.echo(result)


@app.command()
def history(limit: int = typer.Option(10, "--limit", min=1, help="Number of runs to show.")) -> None:
    """Show recent agent runs."""
    storage = AgentStorage(Settings().agent_db_path)
    rows = storage.recent_runs(limit)
    if not rows:
        typer.echo("No runs recorded yet.")
        return

    for row in rows:
        typer.echo(f"{row['id']} | {row['created_at']} | {row['model']} | {row['task']}")
