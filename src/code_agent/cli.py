from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Optional

import typer

from .config import Settings
from .debug_bundle import export_debug_bundle
from .doctor import run_doctor
from .eval_reports import (
    DEFAULT_REPORT_DIR,
    format_eval_report_index,
    format_eval_report_summary,
    list_eval_reports,
    save_eval_report,
    summarize_eval_reports,
)
from .factory import create_agent
from .model_profiles import validate_profile_name
from .model_presets import format_model_presets, resolve_model_preset
from .model_registry import provider_name_list, validate_provider_name
from .permissions import confirm_permission
from .protocol import (
    JsonEventEmitter,
    JsonProtocolReporter,
    emit_run_failed,
    emit_run_finished,
    emit_run_started,
    json_approval_callback,
)
from .release_smoke import run_release_smoke
from .resume import build_resume_task, format_run_detail
from .revert import apply_revert_plan, build_revert_plan, format_revert_preview
from .sandbox import (
    create_sandbox_workspace,
    diff_sandbox_workspace,
    format_sandbox_diff,
    format_sandbox_limits,
    promote_sandbox_changes,
)
from .storage import AgentStorage
from .status import StatusReporter
from .terminal_ui import print_work_report_panel
from .work_report import should_show_work_report

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


def validate_preset_option(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        preset = resolve_model_preset(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    return preset.name if preset else None


def validate_provider_option(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        return validate_provider_name(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


PROVIDER_HELP = f"Provider override: {provider_name_list()}."


@app.command("models")
def models_command() -> None:
    """List available model presets."""
    typer.echo(format_model_presets())


@app.command()
def run(
    task: str = typer.Argument(..., help="The coding task for the agent."),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        callback=validate_provider_option,
        help=PROVIDER_HELP,
    ),
    preset: Optional[str] = typer.Option(
        None,
        "--preset",
        callback=validate_preset_option,
        help="Model preset, such as qwen-coder, gemini-flash, deepseek-pro, glm-5.2, or deepseek-v4-flash.",
    ),
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
    deny_network_shell: bool = typer.Option(
        False,
        "--deny-network-shell",
        help="Block shell commands classified as install/network for this run.",
    ),
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
        typer.echo(format_sandbox_limits())

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
        provider=provider,
        preset=preset,
        shell_network_policy="deny" if deny_network_shell else None,
    )
    try:
        result = agent.run_detailed(task)
    except KeyboardInterrupt:
        agent.cancel("keyboard interrupt")
        typer.echo("\nSTOPPED by user")
        raise typer.Exit(code=130)
    print_work_report_panel(result)
    if not should_show_work_report(result):
        typer.echo(result.message)


@app.command("evals")
def evals_command(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable eval results."),
    live: bool = typer.Option(False, "--live", help="Run opt-in live-model benchmark evals."),
    limit: Optional[int] = typer.Option(None, "--limit", min=1, help="Limit live eval cases."),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        callback=validate_provider_option,
        help=f"Provider override for live evals: {provider_name_list()}.",
    ),
    preset: Optional[str] = typer.Option(
        None,
        "--preset",
        callback=validate_preset_option,
        help="Model preset for live evals.",
    ),
    model: Optional[str] = typer.Option(None, "--model", help="Model override for live evals."),
    profile: Optional[str] = typer.Option(
        "coder",
        "--profile",
        callback=validate_profile_option,
        help="Model profile for live evals.",
    ),
    save_report: bool = typer.Option(False, "--save-report", help="Save a JSON eval report."),
    report_dir: Path = typer.Option(DEFAULT_REPORT_DIR, "--report-dir", help="Eval report directory."),
) -> None:
    """Run deterministic evals, or explicit live-model benchmark evals."""
    from . import evals as evals_module

    result = (
        evals_module.run_live_evals(
            model=model,
            provider=provider,
            preset=preset,
            profile=profile,
            limit=limit,
        )
        if live
        else evals_module.run_builtin_evals()
    )
    if save_report:
        path = save_eval_report(result, report_dir=report_dir, label="live-evals" if live else "evals")
        typer.echo(f"Eval report saved: {path}")
    typer.echo(result.to_json() if json_output else result.format())
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("eval-reports")
def eval_reports_command(
    report_dir: Path = typer.Option(DEFAULT_REPORT_DIR, "--report-dir", help="Eval report directory."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    summary: bool = typer.Option(False, "--summary", help="Group reports by mode, provider, and model."),
) -> None:
    """List saved eval reports."""
    reports = list_eval_reports(report_dir)
    payload = summarize_eval_reports(reports) if summary else reports
    typer.echo(
        json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if json_output
        else format_eval_report_summary(payload)
        if summary
        else format_eval_report_index(payload)
    )


@app.command("release-smoke")
def release_smoke_command(
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    skip_build: bool = typer.Option(False, "--skip-build", help="Skip package build gate."),
) -> None:
    """Run the local release-readiness gate."""
    report = run_release_smoke(cwd.resolve(), include_build=not skip_build)
    typer.echo(report.to_json() if json_output else report.format_text())
    if not report.ok:
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
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        callback=validate_provider_option,
        help=PROVIDER_HELP,
    ),
    preset: Optional[str] = typer.Option(
        None,
        "--preset",
        callback=validate_preset_option,
        help="Model preset, such as qwen-coder, gemini-flash, deepseek-pro, glm-5.2, or deepseek-v4-flash.",
    ),
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
    deny_network_shell: bool = typer.Option(
        False,
        "--deny-network-shell",
        help="Block shell commands classified as install/network for this run.",
    ),
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
    agent = None
    try:
        if sandbox:
            sandbox_workspace = create_sandbox_workspace(workspace)
            workspace = sandbox_workspace.path
            emitter.emit("sandbox_created", path=str(workspace), source=str(cwd.resolve()))
            emitter.emit("sandbox_limits", detail=format_sandbox_limits())

        emit_run_started(
            emitter,
            task=task,
            cwd=str(workspace),
            dry_run=dry_run,
            sandbox=sandbox,
            model=model,
            preset=preset,
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
            provider=provider,
            preset=preset,
            shell_network_policy="deny" if deny_network_shell else None,
        )
        result = agent.run_detailed(task)
    except KeyboardInterrupt:
        if agent is not None:
            agent.cancel("keyboard interrupt")
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
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        callback=validate_provider_option,
        help=PROVIDER_HELP,
    ),
    preset: Optional[str] = typer.Option(
        None,
        "--preset",
        callback=validate_preset_option,
        help="Model preset, such as qwen-coder, gemini-flash, deepseek-pro, glm-5.2, or deepseek-v4-flash.",
    ),
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
    deny_network_shell: bool = typer.Option(
        False,
        "--deny-network-shell",
        help="Block shell commands classified as install/network for this run.",
    ),
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
        typer.echo(format_sandbox_limits())

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
        provider=provider,
        preset=preset,
        shell_network_policy="deny" if deny_network_shell else None,
    )
    try:
        result = agent.run_detailed(task)
    except KeyboardInterrupt:
        agent.cancel("keyboard interrupt")
        typer.echo("\nSTOPPED by user")
        raise typer.Exit(code=130)
    print_work_report_panel(result)
    if not should_show_work_report(result):
        typer.echo(result.message)


@app.command("revert")
def revert_command(
    run_id: int = typer.Argument(..., help="Run ID whose verified changes should be reverted."),
) -> None:
    """Revert verified file changes from a previous run."""
    storage = AgentStorage(Settings().agent_db_path)
    try:
        plan = build_revert_plan(storage, run_id)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1)
    typer.echo(format_revert_preview(plan))
    result = apply_revert_plan(
        storage,
        plan,
        approval_callback=confirm_permission,
    )
    typer.echo(result.output)
    typer.echo(f"Revert run id: {result.run_id}")
    if not result.ok:
        raise typer.Exit(code=1)


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


@history_app.command("export")
def history_export(
    run_id: int = typer.Argument(..., help="Run ID to export."),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", help="Directory for the debug bundle."),
) -> None:
    """Export a redacted debug bundle for one run."""
    storage = AgentStorage(Settings().agent_db_path)
    try:
        path = export_debug_bundle(storage, run_id, output_dir)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1)
    typer.echo(f"Debug bundle exported: {path}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
