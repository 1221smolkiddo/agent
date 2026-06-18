from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from .config import Settings
from .factory import create_agent
from .permissions import confirm_permission
from .resume import build_resume_task, format_run_detail
from .sandbox import create_sandbox_workspace
from .storage import AgentStorage
from .status import StatusReporter
from .terminal_ui import print_plan_panel

app = typer.Typer(help="A CLI-first coding agent.")
history_app = typer.Typer(
    help="Inspect saved agent runs.",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(history_app, name="history")


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
        result = agent.run_detailed(task)
    except KeyboardInterrupt:
        typer.echo("\nSTOPPED by user")
        raise typer.Exit(code=130)
    print_plan_panel(result.plan_updates)
    typer.echo(result.message)


@app.command()
def resume(
    run_id: int = typer.Argument(..., help="Run ID to resume."),
    instruction: str = typer.Argument("", help="Optional extra instruction for the resumed run."),
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Override workspace directory."),
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
    """Resume a previous run with its saved step context."""
    settings = Settings()
    storage = AgentStorage(settings.agent_db_path)
    run_row = storage.get_run(run_id)
    if run_row is None:
        typer.echo(f"No run found with id {run_id}.")
        raise typer.Exit(code=1)

    workspace = (cwd or Path(run_row["cwd"])).resolve()
    if sandbox:
        sandbox_workspace = create_sandbox_workspace(workspace)
        workspace = sandbox_workspace.path
        typer.echo(f"Sandbox: {workspace}")

    task = build_resume_task(run_row, storage.run_steps_payloads(run_id), instruction or None)
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
        result = agent.run_detailed(task)
    except KeyboardInterrupt:
        typer.echo("\nSTOPPED by user")
        raise typer.Exit(code=130)
    print_plan_panel(result.plan_updates)
    typer.echo(result.message)


@history_app.callback(invoke_without_command=True)
def history(
    ctx: typer.Context,
    limit: int = typer.Option(10, "--limit", min=1, help="Number of runs to show."),
) -> None:
    """Show recent agent runs."""
    if ctx.invoked_subcommand is not None:
        return
    storage = AgentStorage(Settings().agent_db_path)
    rows = storage.recent_runs(limit)
    if not rows:
        typer.echo("No runs recorded yet.")
        return

    for row in rows:
        typer.echo(f"{row['id']} | {row['created_at']} | {row['model']} | {row['task']}")


@history_app.command("show")
def history_show(run_id: int = typer.Argument(..., help="Run ID to inspect.")) -> None:
    """Show saved steps for one agent run."""
    storage = AgentStorage(Settings().agent_db_path)
    run_row = storage.get_run(run_id)
    if run_row is None:
        typer.echo(f"No run found with id {run_id}.")
        raise typer.Exit(code=1)
    typer.echo(format_run_detail(run_row, storage.run_steps_payloads(run_id)))
