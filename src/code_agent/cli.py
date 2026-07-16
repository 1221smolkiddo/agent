from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Optional

import typer

from .collaboration import (
    build_changelog_entry,
    build_collaboration_context,
    build_commit_message,
    build_pr_summary,
    build_review_report,
    commit_changes,
    create_branch,
    format_collaboration_status,
    format_review_report,
    load_pr_template,
)
from .config import Settings
from .debug_bundle import export_debug_bundle
from .doctor import run_doctor
from .eval_reports import (
    DEFAULT_REPORT_DIR,
    build_capability_dashboard,
    build_failure_analytics,
    format_capability_dashboard,
    format_failure_analytics,
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
from .sandbox_security import (
    SandboxIsolationError,
    resolve_sandbox_policy,
    sandbox_health,
)
from .storage import AgentStorage
from .status import StatusReporter
from .terminal_ui import print_work_report_panel
from .transactions import TransactionError, WorkspaceTransactionManager
from .work_report import should_show_work_report

app = typer.Typer(help="A CLI-first coding agent.")
history_app = typer.Typer(
    help="Inspect saved agent runs.",
    invoke_without_command=True,
    no_args_is_help=False,
)
sandbox_app = typer.Typer(help="Inspect and promote sandbox workspace changes.")
collab_app = typer.Typer(help="Branch, commit, PR, changelog, and collaboration helpers.")
transactions_app = typer.Typer(help="Inspect, recover, undo, redo, and restore transactions.")
app.add_typer(history_app, name="history")
app.add_typer(sandbox_app, name="sandbox")
app.add_typer(collab_app, name="collab")
app.add_typer(transactions_app, name="transactions")


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
    sandbox: bool = typer.Option(
        False,
        "--sandbox",
        help="Run in a copied workspace with required Docker/Podman process isolation.",
    ),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="Show compact model streaming progress."),
    deny_network_shell: bool = typer.Option(
        False,
        "--deny-network-shell",
        help="Block shell commands classified as install/network for this run.",
    ),
    sandbox_backend: Optional[str] = typer.Option(
        None,
        "--sandbox-backend",
        help="Sandbox backend override: auto, docker, or podman. Local is refused with --sandbox.",
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
    try:
        sandbox_policy = resolve_sandbox_policy(
            workspace,
            backend=sandbox_backend or settings.sandbox_backend,
            container_image=settings.agent_sandbox_image,
            require_process_isolation=sandbox,
        )
    except SandboxIsolationError as exc:
        typer.echo(f"Sandbox unavailable: {exc}")
        raise typer.Exit(code=1)
    if sandbox:
        sandbox_workspace = create_sandbox_workspace(
            workspace,
            policy=sandbox_policy,
        )
        workspace = sandbox_workspace.path
        typer.echo(f"Sandbox: {workspace}")
        typer.echo(format_sandbox_limits(sandbox_workspace.policy))

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
        sandbox_backend=sandbox_policy.backend,
        require_process_isolation=sandbox,
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
    dashboard: bool = typer.Option(False, "--dashboard", help="Show release-readiness capability dashboard."),
    analytics: bool = typer.Option(False, "--analytics", help="Show per-case failure analytics and regressions."),
    min_pass_rate: float = typer.Option(
        0.8,
        "--min-pass-rate",
        min=0.0,
        max=1.0,
        help="Minimum latest-live pass rate required by the dashboard gate.",
    ),
    min_live_reports: int = typer.Option(
        1,
        "--min-live-reports",
        min=0,
        help="Minimum saved live reports required by the dashboard gate.",
    ),
) -> None:
    """List saved eval reports, summaries, dashboard, or failure analytics."""
    if dashboard:
        payload = build_capability_dashboard(
            report_dir,
            min_pass_rate=min_pass_rate,
            min_live_reports=min_live_reports,
        )
        typer.echo(
            json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if json_output
            else format_capability_dashboard(payload)
        )
        return
    if analytics:
        payload = build_failure_analytics(report_dir)
        typer.echo(
            json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if json_output
            else format_failure_analytics(payload)
        )
        return

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
    require_dashboard: bool = typer.Option(
        False,
        "--require-dashboard",
        help="Also require the saved eval capability dashboard gate to pass.",
    ),
    report_dir: Path = typer.Option(DEFAULT_REPORT_DIR, "--report-dir", help="Eval report directory."),
) -> None:
    """Run the local release-readiness gate."""
    dashboard_payload = None
    report = run_release_smoke(cwd.resolve(), include_build=not skip_build)
    if require_dashboard:
        dashboard_payload = build_capability_dashboard(report_dir)
    dashboard_ok = dashboard_payload is None or dashboard_payload.get("gate", {}).get("status") == "pass"
    if json_output:
        payload = report.as_dict()
        if dashboard_payload is not None:
            payload["capability_dashboard"] = dashboard_payload
            payload["ok"] = bool(payload["ok"] and dashboard_ok)
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        typer.echo(report.format_text())
        if dashboard_payload is not None:
            typer.echo("")
            typer.echo(format_capability_dashboard(dashboard_payload))
    if not report.ok or not dashboard_ok:
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


@sandbox_app.command("health")
def sandbox_health_command(
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    backend: Optional[str] = typer.Option(
        None,
        "--backend",
        help="Process-isolation backend to check: auto, docker, or podman.",
    ),
) -> None:
    """Show sandbox backend availability and isolation guarantees."""
    settings = Settings()
    try:
        policy = resolve_sandbox_policy(
            cwd.resolve(),
            backend=backend or settings.sandbox_backend,
            container_image=settings.agent_sandbox_image,
            require_process_isolation=True,
        )
    except SandboxIsolationError as exc:
        typer.echo(f"Sandbox unavailable: {exc}")
        raise typer.Exit(code=1)
    typer.echo(sandbox_health(policy).format_text())


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
    sandbox: bool = typer.Option(
        False,
        "--sandbox",
        help="Run in a copied workspace with required Docker/Podman process isolation.",
    ),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="Emit model stream lifecycle events."),
    deny_network_shell: bool = typer.Option(
        False,
        "--deny-network-shell",
        help="Block shell commands classified as install/network for this run.",
    ),
    sandbox_backend: Optional[str] = typer.Option(
        None,
        "--sandbox-backend",
        help="Sandbox backend override: auto, docker, or podman. Local is refused with --sandbox.",
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
        sandbox_policy = resolve_sandbox_policy(
            workspace,
            backend=sandbox_backend or settings.sandbox_backend,
            container_image=settings.agent_sandbox_image,
            require_process_isolation=sandbox,
        )
        if sandbox:
            sandbox_workspace = create_sandbox_workspace(
                workspace,
                policy=sandbox_policy,
            )
            workspace = sandbox_workspace.path
            emitter.emit(
                "sandbox_created",
                path=str(workspace),
                source=str(cwd.resolve()),
                backend=sandbox_policy.backend,
                process_isolated=sandbox_policy.process_isolated,
                isolation_required=sandbox_policy.process_isolation_required,
            )
            emitter.emit("sandbox_limits", detail=format_sandbox_limits(sandbox_workspace.policy))

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
            sandbox_backend=sandbox_policy.backend,
            process_isolated=sandbox_policy.process_isolated,
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
            sandbox_backend=sandbox_policy.backend,
            require_process_isolation=sandbox,
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
    sandbox: bool = typer.Option(
        False,
        "--sandbox",
        help="Run in a copied workspace with required Docker/Podman process isolation.",
    ),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="Show compact model streaming progress."),
    deny_network_shell: bool = typer.Option(
        False,
        "--deny-network-shell",
        help="Block shell commands classified as install/network for this run.",
    ),
    sandbox_backend: Optional[str] = typer.Option(
        None,
        "--sandbox-backend",
        help="Sandbox backend override: auto, docker, or podman. Local is refused with --sandbox.",
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
    try:
        sandbox_policy = resolve_sandbox_policy(
            workspace,
            backend=sandbox_backend or settings.sandbox_backend,
            container_image=settings.agent_sandbox_image,
            require_process_isolation=sandbox,
        )
    except SandboxIsolationError as exc:
        typer.echo(f"Sandbox unavailable: {exc}")
        raise typer.Exit(code=1)
    if sandbox:
        sandbox_workspace = create_sandbox_workspace(
            workspace,
            policy=sandbox_policy,
        )
        workspace = sandbox_workspace.path
        typer.echo(f"Sandbox: {workspace}")
        typer.echo(format_sandbox_limits(sandbox_workspace.policy))

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
        sandbox_backend=sandbox_policy.backend,
        require_process_isolation=sandbox,
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


@transactions_app.command("list")
def transactions_list_command(
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    run_id: Optional[int] = typer.Option(None, "--run-id", help="Filter by Agent47 run ID."),
) -> None:
    """List workspace transaction journals."""
    manager = WorkspaceTransactionManager(cwd.resolve())
    transactions = manager.list_transactions()
    if run_id is not None:
        transactions = [item for item in transactions if item.get("run_id") == run_id]
    if not transactions:
        typer.echo("No transactions.")
        return
    for item in transactions:
        typer.echo(
            f"{item['id']}  {item['state']}  {item['action']}  "
            f"run={item.get('run_id') or '-'}  paths={', '.join(item['paths'])}"
        )


@transactions_app.command("undo")
def transactions_undo_command(
    transaction_id: str = typer.Argument(..., help="Committed transaction ID."),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    path: Optional[list[str]] = typer.Option(None, "--path", help="Restore only this path."),
) -> None:
    """Undo a committed transaction without overwriting newer edits."""
    _run_transaction_command(
        WorkspaceTransactionManager(cwd.resolve()),
        "undo",
        transaction_id,
        path or [],
    )


@transactions_app.command("redo")
def transactions_redo_command(
    transaction_id: str = typer.Argument(..., help="Committed transaction ID."),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    path: Optional[list[str]] = typer.Option(None, "--path", help="Reapply only this path."),
) -> None:
    """Redo a committed transaction after it has been undone."""
    _run_transaction_command(
        WorkspaceTransactionManager(cwd.resolve()),
        "redo",
        transaction_id,
        path or [],
    )


@transactions_app.command("restore")
def transactions_restore_command(
    transaction_id: str = typer.Argument(..., help="Transaction checkpoint ID."),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    path: Optional[list[str]] = typer.Option(
        None,
        "--path",
        help="Restore only this path; omit to restore the complete workspace snapshot.",
    ),
) -> None:
    """Restore selected files or the complete workspace checkpoint."""
    _run_transaction_command(
        WorkspaceTransactionManager(cwd.resolve()),
        "restore",
        transaction_id,
        path or [],
    )


@transactions_app.command("recover")
def transactions_recover_command(
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
) -> None:
    """Recover interrupted transaction journals."""
    manager = WorkspaceTransactionManager(cwd.resolve())
    results = [*manager.consume_recovery_results(), *manager.recover_incomplete()]
    if not results:
        typer.echo("No interrupted transactions.")
        return
    for result in results:
        typer.echo(result.output)
        if result.conflicts:
            typer.echo("Conflicts: " + ", ".join(result.conflicts))
    if any(not result.ok for result in results):
        raise typer.Exit(code=1)


def _run_transaction_command(
    manager: WorkspaceTransactionManager,
    operation: str,
    transaction_id: str,
    paths: list[str],
) -> None:
    try:
        if operation == "undo":
            plan = manager.plan_undo(transaction_id, paths=paths or None)
        elif operation == "redo":
            plan = manager.plan_redo(transaction_id, paths=paths or None)
        else:
            plan = manager.plan_restore_snapshot(transaction_id, paths=paths or None)
    except TransactionError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1)
    preview = manager.format_preview(plan)
    typer.echo(preview)
    approval_action = {
        "undo": "undo_transaction",
        "redo": "redo_transaction",
        "restore": "restore_snapshot",
    }[operation]
    if confirm_permission(approval_action, preview) not in {"y", "a"}:
        plan.abort("permission denied")
        typer.echo("Permission denied.")
        raise typer.Exit(code=1)
    result = plan.commit()
    typer.echo(result.output)
    typer.echo(f"Transaction id: {result.transaction_id}")
    if not result.ok:
        raise typer.Exit(code=1)


@collab_app.command("status")
def collab_status_command(
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    run_id: Optional[int] = typer.Option(None, "--run-id", help="Use a saved Agent47 run as context."),
) -> None:
    """Show git and run-history collaboration state."""
    storage = AgentStorage(Settings().agent_db_path) if run_id is not None else None
    try:
        context = build_collaboration_context(cwd.resolve(), storage, run_id=run_id)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1)
    typer.echo(format_collaboration_status(context))


@collab_app.command("commit-message")
def collab_commit_message_command(
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    run_id: Optional[int] = typer.Option(None, "--run-id", help="Use a saved Agent47 run as context."),
) -> None:
    """Generate a reviewable commit message from git state and optional run history."""
    storage = AgentStorage(Settings().agent_db_path) if run_id is not None else None
    try:
        context = build_collaboration_context(cwd.resolve(), storage, run_id=run_id)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1)
    typer.echo(build_commit_message(context))


@collab_app.command("pr-summary")
def collab_pr_summary_command(
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    run_id: Optional[int] = typer.Option(None, "--run-id", help="Use a saved Agent47 run as context."),
    use_template: bool = typer.Option(True, "--template/--no-template", help="Use .github PR template when available."),
) -> None:
    """Generate a PR summary from git state, run history, and the local PR template."""
    storage = AgentStorage(Settings().agent_db_path) if run_id is not None else None
    try:
        context = build_collaboration_context(cwd.resolve(), storage, run_id=run_id)
        template = load_pr_template(cwd.resolve()) if use_template else None
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1)
    typer.echo(build_pr_summary(context, template))


@collab_app.command("changelog")
def collab_changelog_command(
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    run_id: Optional[int] = typer.Option(None, "--run-id", help="Use a saved Agent47 run as context."),
    version: str = typer.Option("Unreleased", "--version", help="Changelog heading."),
) -> None:
    """Generate a changelog entry from git state and optional run history."""
    storage = AgentStorage(Settings().agent_db_path) if run_id is not None else None
    try:
        context = build_collaboration_context(cwd.resolve(), storage, run_id=run_id)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1)
    typer.echo(build_changelog_entry(context, version=version))


@collab_app.command("review")
def collab_review_command(
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    run_id: Optional[int] = typer.Option(None, "--run-id", help="Use a saved Agent47 run as context."),
    strict: bool = typer.Option(False, "--strict", help="Exit nonzero for high or critical findings."),
) -> None:
    """Review changed work for bugs, regressions, missing tests, and security risks."""
    storage = AgentStorage(Settings().agent_db_path) if run_id is not None else None
    try:
        context = build_collaboration_context(cwd.resolve(), storage, run_id=run_id)
        report = build_review_report(context)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1)
    typer.echo(format_review_report(report))
    if strict and not report.ok:
        raise typer.Exit(code=1)


@collab_app.command("branch")
def collab_branch_command(
    branch: str = typer.Argument(..., help="Branch name to create."),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    apply: bool = typer.Option(False, "--apply", help="Actually create and switch to the branch."),
) -> None:
    """Preview or create a work branch with approval."""
    try:
        result = create_branch(
            cwd.resolve(),
            branch,
            approval_callback=confirm_permission,
            apply=apply,
        )
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1)
    typer.echo(result.output)
    if not result.ok:
        raise typer.Exit(code=1)


@collab_app.command("commit")
def collab_commit_command(
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    run_id: Optional[int] = typer.Option(None, "--run-id", help="Use a saved Agent47 run as context."),
    message: Optional[str] = typer.Option(None, "--message", "-m", help="Commit message override."),
    path: list[str] = typer.Option(None, "--path", help="Limit commit to workspace-relative path."),
    commit: bool = typer.Option(False, "--commit", help="Actually stage and commit after approval."),
) -> None:
    """Preview or create a git commit from changed files and run history."""
    storage = AgentStorage(Settings().agent_db_path) if run_id is not None else None
    try:
        context = build_collaboration_context(cwd.resolve(), storage, run_id=run_id)
        commit_message = message or build_commit_message(context).splitlines()[0]
        result = commit_changes(
            cwd.resolve(),
            commit_message,
            paths=path or None,
            approval_callback=confirm_permission,
            commit=commit,
        )
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1)
    typer.echo(result.output)
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


@history_app.command("delete")
def history_delete(
    run_id: int = typer.Argument(..., help="Run ID to permanently delete."),
    yes: bool = typer.Option(False, "--yes", help="Confirm permanent deletion."),
) -> None:
    """Permanently delete one run and its saved data."""
    if not yes:
        typer.echo("Refusing to delete history without --yes.")
        raise typer.Exit(code=1)
    storage = AgentStorage(Settings().agent_db_path)
    if not storage.delete_run(run_id):
        typer.echo(f"No run found with id {run_id}.")
        raise typer.Exit(code=1)
    typer.echo(f"Deleted run {run_id}.")


@history_app.command("prune")
def history_prune(
    keep_last: int = typer.Option(20, "--keep-last", min=0, help="Recent runs to retain."),
    yes: bool = typer.Option(False, "--yes", help="Confirm permanent deletion."),
) -> None:
    """Delete old runs while retaining the newest runs."""
    if not yes:
        typer.echo("Refusing to prune history without --yes.")
        raise typer.Exit(code=1)
    deleted = AgentStorage(Settings().agent_db_path).prune_runs(keep_last)
    typer.echo(f"Deleted {deleted} old run(s); retained the newest {keep_last}.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
