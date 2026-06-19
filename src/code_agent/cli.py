from __future__ import annotations

from pathlib import Path
import sys
from typing import Optional

import typer

from .config import Settings
from .doctor import run_doctor
from .evals import run_builtin_evals
from .factory import create_agent
from .model_profiles import validate_profile_name
from .permissions import confirm_permission
from .protocol import (
    JsonEventEmitter,
    JsonProtocolReporter,
    emit_run_failed,
    emit_run_finished,
    emit_run_started,
    json_approval_callback,
)
from .resume import build_resume_task, format_run_detail
from .sandbox import (
    create_sandbox_workspace,
    diff_sandbox_workspace,
    format_sandbox_diff,
    promote_sandbox_changes,
)
from .storage import AgentStorage
from .status import StatusReporter
from .terminal_ui import print_work_report_panel

app = typer.Typer(help="A CLI-first coding agent.")
history_app = typer.Typer(
    help="Inspect saved agent runs.",
    invoke_without_command=True,
    no_args_is_help=False,
)
sandbox_app = typer.Typer(help="Inspect and promote sandbox workspace changes.")
app.add_typer(history_app, name="history")
app.add_typer(sandbox_app, name="sandbox")


def validate_profile_option(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        return validate_profile_name(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def run(
    task: str = typer.Argument(..., help="The coding task for the agent."),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    model: Optional[str] = typer.Option(None, "--model", help="Model override."),
    profile: Optional[str] = typer.Option(
        None,
        "--profile",
        callback=validate_profile_option,
        help="Model profile: default, planner, coder, reviewer, or fast.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Inspect only; skip writes and shell."),
    sandbox: bool = typer.Option(False, "--sandbox", help="Run inside an isolated workspace copy."),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="Show compact model streaming progress."),
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
        stream_model=stream,
        profile=profile,
    )
    try:
        result = agent.run_detailed(task)
    except KeyboardInterrupt:
        typer.echo("\nSTOPPED by user")
        raise typer.Exit(code=130)
    print_work_report_panel(result)
    typer.echo(result.message)


@app.command("evals")
def evals_command() -> None:
    """Run local deterministic safety and regression evals."""
    result = run_builtin_evals()
    typer.echo(result.format())
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("doctor")
def doctor_command(
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory to check."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    strict: bool = typer.Option(False, "--strict", help="Exit nonzero on warnings as well as failures."),
) -> None:
    """Check local install, platform, tools, and configuration."""
    report = run_doctor(cwd=cwd)
    typer.echo(report.to_json() if json_output else report.format_text())
    if not report.ok or (strict and report.has_warnings):
        raise typer.Exit(code=1)


@sandbox_app.command("diff")
def sandbox_diff_command(
    sandbox: Path = typer.Argument(..., help="Sandbox workspace path."),
    base: Path = typer.Option(Path.cwd(), "--base", help="Base workspace to compare against."),
    path: list[str] = typer.Option(None, "--path", help="Limit diff/apply to a workspace-relative file."),
) -> None:
    """Show changed files and unified diff between a sandbox and base workspace."""
    try:
        diff = diff_sandbox_workspace(base, sandbox, paths=path)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2)
    typer.echo(format_sandbox_diff(diff))
    if diff.skipped_paths:
        raise typer.Exit(code=1)


@sandbox_app.command("apply")
def sandbox_apply_command(
    sandbox: Path = typer.Argument(..., help="Sandbox workspace path."),
    base: Path = typer.Option(Path.cwd(), "--base", help="Base workspace to promote changes into."),
    path: list[str] = typer.Option(None, "--path", help="Limit apply to a workspace-relative file."),
) -> None:
    """Promote sandbox changes into the base workspace through patch approval and verification."""
    try:
        result = promote_sandbox_changes(
            base,
            sandbox,
            paths=path,
            approval_callback=confirm_permission,
        )
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2)
    typer.echo(result.output)
    if result.changed_paths:
        typer.echo("Promoted files: " + ", ".join(result.changed_paths))
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("run-json")
def run_json(
    task: str = typer.Argument(..., help="The coding task for the agent."),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    model: Optional[str] = typer.Option(None, "--model", help="Model override."),
    profile: Optional[str] = typer.Option(
        None,
        "--profile",
        callback=validate_profile_option,
        help="Model profile: default, planner, coder, reviewer, or fast.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Inspect only; skip writes and shell."),
    sandbox: bool = typer.Option(False, "--sandbox", help="Run inside an isolated workspace copy."),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="Emit model stream lifecycle events."),
    max_steps: int = typer.Option(12, "--max-steps", min=1, help="Maximum agent loop steps."),
    max_failures: Optional[int] = typer.Option(
        None,
        "--max-failures",
        min=1,
        help="Consecutive failures before the agent stops retrying.",
    ),
    approve_all: bool = typer.Option(
        False,
        "--approve-all",
        help="Approve every tool request. Intended only for trusted automation.",
    ),
    approval_stdin: bool = typer.Option(
        False,
        "--approval-stdin",
        help="Read one JSON approval response from stdin for each approval request.",
    ),
) -> None:
    """Run the agent and emit newline-delimited JSON protocol events."""
    emitter = JsonEventEmitter()
    if approve_all and approval_stdin:
        emit_run_failed(
            emitter,
            "--approve-all and --approval-stdin cannot be used together.",
            code="invalid_approval_mode",
        )
        raise typer.Exit(code=2)
    settings = Settings()
    workspace = cwd.resolve()
    try:
        if sandbox:
            sandbox_workspace = create_sandbox_workspace(workspace)
            workspace = sandbox_workspace.path
            emitter.emit("sandbox_created", path=str(workspace), source=str(cwd.resolve()))

        emit_run_started(
            emitter,
            task=task,
            cwd=str(workspace),
            dry_run=dry_run,
            sandbox=sandbox,
            model=model,
            profile=profile,
            max_steps=max_steps,
        )
        agent = create_agent(
            settings=settings,
            cwd=workspace,
            model=model,
            dry_run=dry_run,
            max_steps=max_steps,
            max_failures=max_failures,
            approval_callback=json_approval_callback(
                emitter,
                approve_all=approve_all,
                input_stream=sys.stdin if approval_stdin else None,
            ),
            reporter=JsonProtocolReporter(emitter),
            stream_model=stream,
            profile=profile,
        )
        result = agent.run_detailed(task)
    except KeyboardInterrupt:
        emit_run_failed(emitter, "Stopped by user.", code="keyboard_interrupt")
        raise typer.Exit(code=130)
    except Exception as exc:
        emit_run_failed(emitter, str(exc), code=type(exc).__name__)
        raise typer.Exit(code=1)
    emit_run_finished(emitter, result)
    if result.blocked or result.failed_actions:
        raise typer.Exit(code=1)


@app.command()
def resume(
    run_id: int = typer.Argument(..., help="Run ID to resume."),
    instruction: str = typer.Argument("", help="Optional extra instruction for the resumed run."),
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Override workspace directory."),
    model: Optional[str] = typer.Option(None, "--model", help="Model override."),
    profile: Optional[str] = typer.Option(
        None,
        "--profile",
        callback=validate_profile_option,
        help="Model profile: default, planner, coder, reviewer, or fast.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Inspect only; skip writes and shell."),
    sandbox: bool = typer.Option(False, "--sandbox", help="Run inside an isolated workspace copy."),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="Show compact model streaming progress."),
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

    task = build_resume_task(
        run_row,
        storage.run_steps_payloads(run_id),
        instruction or None,
        storage.get_work_report(run_id),
    )
    agent = create_agent(
        settings=settings,
        cwd=workspace,
        model=model,
        dry_run=dry_run,
        max_steps=max_steps,
        max_failures=max_failures,
        approval_callback=confirm_permission,
        reporter=StatusReporter(),
        stream_model=stream,
        profile=profile,
    )
    try:
        result = agent.run_detailed(task)
    except KeyboardInterrupt:
        typer.echo("\nSTOPPED by user")
        raise typer.Exit(code=130)
    print_work_report_panel(result)
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
    typer.echo(
        format_run_detail(
            run_row,
            storage.run_steps_payloads(run_id),
            storage.get_work_report(run_id),
        )
    )
