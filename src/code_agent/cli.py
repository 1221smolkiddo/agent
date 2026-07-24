from __future__ import annotations

import json
import os
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
from .account.profile import AccountStore
from .auth.config import OAuthConfigurationError
from .auth.google import GoogleAuthenticator
from .auth.oauth import OAuthError
from .auth.session import LocalSession
from .credentials.keyring import CredentialStore, KeyringUnavailableError
from .credentials.providers import provider_spec, provider_specs, validate_provider_key
from .container_manager import ContainerError, ContainerManager
from .debug_bundle import export_debug_bundle
from .doctor import run_doctor
from .durable_execution import Command, DurableExecutionRuntime
from .execution_observability import ExecutionInspector
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
from .platform_runtime import PlatformRuntime
from .protocol import (
    JsonEventEmitter,
    JsonProtocolReporter,
    emit_run_failed,
    emit_run_finished,
    emit_run_started,
    json_approval_callback,
)
from .release_smoke import run_release_smoke
from .runtime_migration import (
    MigrationStateStore,
    PROMOTION_DECISION_TYPES,
    PromotionPolicy,
    PromotionStage,
    ShadowDivergenceStore,
)
from .resume import (
    build_resume_task,
    format_run_detail,
    latest_durable_execution_id,
    latest_execution_state,
)
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
from .tools import ToolRegistry
from .transactions import TransactionError, WorkspaceTransactionManager
from .work_report import should_show_work_report
from .schema import (
    InspectProcessAction,
    ListProcessesAction,
    ProcessEventsAction,
    ReadProcessLogsAction,
    RestartProcessAction,
    SendProcessInputAction,
    StartProcessAction,
    StopProcessAction,
)

app = typer.Typer(help="A CLI-first coding agent.")
history_app = typer.Typer(
    help="Inspect saved agent runs.",
    invoke_without_command=True,
    no_args_is_help=False,
)
sandbox_app = typer.Typer(help="Inspect and promote sandbox workspace changes.")
collab_app = typer.Typer(help="Branch, commit, PR, changelog, and collaboration helpers.")
transactions_app = typer.Typer(help="Inspect, recover, undo, redo, and restore transactions.")
processes_app = typer.Typer(help="Start, monitor, control, and recover managed processes.")
containers_app = typer.Typer(help="Create, inspect, stop, and clean reusable workspace containers.")
platform_app = typer.Typer(help="Inspect dynamic tools, skills, agents, plugins, and MCP servers.")
execution_app = typer.Typer(help="Create, inspect, control, checkpoint, and replay durable executions.")
keys_app = typer.Typer(help="Manage API keys in the operating system credential store.", invoke_without_command=True)
auth_app = typer.Typer(help="Sign in, sign out, and manage Google OAuth authentication.")
app.add_typer(history_app, name="history")
app.add_typer(sandbox_app, name="sandbox")
app.add_typer(collab_app, name="collab")
app.add_typer(transactions_app, name="transactions")
app.add_typer(processes_app, name="processes")
app.add_typer(containers_app, name="containers")
app.add_typer(platform_app, name="platform")
app.add_typer(execution_app, name="execution")
app.add_typer(keys_app, name="keys")
app.add_typer(auth_app, name="auth")


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


def _credential_error(exc: Exception) -> None:
    typer.echo(f"Credential storage error: {exc}", err=True)
    raise typer.Exit(code=1)


# ======================================================================
# Auth command group: agent47 auth {login,logout,status,refresh,repair}
# ======================================================================


@auth_app.command("login")
def auth_login_command() -> None:
    """Sign in locally with Google using OAuth PKCE."""
    try:
        typer.echo("Opening browser for Google sign-in…")
        typer.echo("Waiting for authentication…")
        account = GoogleAuthenticator().login()
    except (OAuthConfigurationError, OAuthError, KeyringUnavailableError) as exc:
        typer.echo(f"Login failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo("✓ Successfully authenticated")
    typer.echo(f"Welcome back {account.name}")
    typer.echo(account.email)


@auth_app.command("logout")
def auth_logout_command(
    remove_keys: bool = typer.Option(
        False, "--remove-keys", help="Also delete all saved provider API keys."
    ),
) -> None:
    """Remove the local Google session and optionally saved BYOK credentials."""
    try:
        session = LocalSession()
        profile_deleted, tokens_deleted = session.clear()
        removed: list[str] = []
        if remove_keys:
            store = CredentialStore()
            for spec in provider_specs():
                if store.delete_provider_key(spec.name):
                    removed.append(spec.display_name)
    except KeyringUnavailableError as exc:
        _credential_error(exc)
        return
    if profile_deleted or tokens_deleted:
        typer.echo("Signed out.  Local Google session credentials were removed.")
    else:
        typer.echo("You are not currently signed in.")
    if removed:
        typer.echo("Removed API keys: " + ", ".join(removed))


@auth_app.command("status")
def auth_status_command() -> None:
    """Show the current authentication and session status."""
    account = AccountStore().load()
    typer.echo("─── Authentication ───")
    if account:
        typer.echo("  ✓ Logged In")
    else:
        typer.echo("  ✗ Not Logged In")
        typer.echo("\n  Run 'agent47 auth login' to sign in.")
        raise typer.Exit(code=1)

    # Session validity.
    try:
        store = CredentialStore()
        tokens = store.get_oauth_tokens()
    except KeyringUnavailableError:
        tokens = None

    if tokens:
        has_access = isinstance(tokens.get("access_token"), str) and bool(
            tokens.get("access_token")
        )
        has_refresh = isinstance(tokens.get("refresh_token"), str) and bool(
            tokens.get("refresh_token")
        )
        typer.echo(f"  {'✓' if has_access else '✗'} Access Token")
        typer.echo(f"  {'✓' if has_refresh else '✗'} Refresh Token")
    else:
        typer.echo("  ✗ No Session Tokens")

    typer.echo("\n  Provider:   Google")
    typer.echo(f"  Email:      {account.email}")
    typer.echo(f"  Name:       {account.name}")
    typer.echo(f"  Created:    {account.created_at}")
    typer.echo(f"  Last Login: {account.last_login_at}")


@auth_app.command("refresh")
def auth_refresh_command() -> None:
    """Force-refresh the OAuth access token."""
    try:
        auth = GoogleAuthenticator()
        new_token = auth.refresh()
    except (OAuthConfigurationError, KeyringUnavailableError) as exc:
        typer.echo(f"Refresh failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if new_token:
        typer.echo("✓ Access token refreshed successfully.")
    else:
        typer.echo(
            "Could not refresh the access token.  "
            "Run 'agent47 auth login' to sign in again.",
            err=True,
        )
        raise typer.Exit(code=1)


@auth_app.command("repair")
def auth_repair_command() -> None:
    """Automatically recover from stale sessions, missing metadata, or invalid tokens."""
    try:
        auth = GoogleAuthenticator()
        actions = auth.repair()
    except (OAuthConfigurationError, KeyringUnavailableError) as exc:
        typer.echo(f"Repair failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if actions:
        for action in actions:
            typer.echo(f"  • {action}")
        typer.echo("\n✓ Repair complete.")
    else:
        typer.echo("✓ Authentication state is healthy.  No repair needed.")


# ======================================================================
# Backward-compatible root-level aliases
# ======================================================================


@app.command("login", hidden=True)
def login_command() -> None:
    """Sign in locally with Google using OAuth PKCE (alias for 'auth login')."""
    auth_login_command()


@app.command("logout", hidden=True)
def logout_command(
    remove_keys: bool = typer.Option(
        False, "--remove-keys", help="Also delete all saved provider API keys."
    ),
) -> None:
    """Sign out (alias for 'auth logout')."""
    auth_logout_command(remove_keys=remove_keys)


@app.command("account")
def account_command() -> None:
    """Show the locally stored Google account profile."""
    account = AccountStore().load()
    if account is None:
        typer.echo("Not signed in.  Run 'agent47 auth login' to authenticate.")
        raise typer.Exit(code=1)
    typer.echo("Google Account")
    typer.echo(f"  Name:       {account.name}")
    typer.echo(f"  Email:      {account.email}")
    typer.echo("  Provider:   Google")
    typer.echo("  Signed In:  Yes")
    typer.echo(f"  Created:    {account.created_at}")
    typer.echo(f"  Last Login: {account.last_login_at}")


@app.command("whoami")
def whoami_command() -> None:
    """Show the signed-in identity and configured BYOK providers."""
    account = AccountStore().load()
    if account is None:
        typer.echo("Not signed in")
    else:
        typer.echo(account.name)
        typer.echo(account.email)
    typer.echo("\nProviders")
    try:
        store = CredentialStore()
        for spec in provider_specs():
            marker = "✓" if store.get_provider_key(spec.name) else "✗"
            typer.echo(f"  {marker} {spec.display_name}")
    except KeyringUnavailableError as exc:
        _credential_error(exc)


# ======================================================================
# Keys command group
# ======================================================================


@keys_app.callback()
def keys_command(ctx: typer.Context) -> None:
    """List locally stored API keys without printing any secret material."""
    if ctx.invoked_subcommand is not None:
        return
    keys_list_command()


@keys_app.command("list")
def keys_list_command() -> None:
    """Show which providers have stored API keys."""
    try:
        store = CredentialStore()
        for spec in provider_specs():
            status = "configured" if store.get_provider_key(spec.name) else "not configured"
            typer.echo(f"  {spec.name:<12} {status}")
    except KeyringUnavailableError as exc:
        _credential_error(exc)


@keys_app.command("add")
def keys_add_command(
    provider: str = typer.Argument(...),
    base_url: str | None = typer.Option(
        None, "--base-url", help="Base URL for OpenAI-compatible providers."
    ),
    skip_validation: bool = typer.Option(
        False, "--skip-validation", help="Store the key without testing it first."
    ),
) -> None:
    """Prompt for, validate, and save an API key in the platform credential manager."""
    try:
        spec = provider_spec(provider)
    except ValueError as exc:
        _credential_error(exc)
        return
    key = typer.prompt(
        f"{spec.display_name} API key", hide_input=True, confirmation_prompt=True
    )
    if not key.strip():
        typer.echo("API key must not be blank.", err=True)
        raise typer.Exit(code=1)
    # Validate the key against the provider before storing.
    if not skip_validation:
        typer.echo(f"Validating {spec.display_name} key…")
        valid, message = validate_provider_key(spec.name, key.strip(), base_url=base_url)
        if not valid:
            typer.echo(f"Validation failed: {message}", err=True)
            typer.echo("Use --skip-validation to store the key anyway.", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"  ✓ {message}")
    try:
        CredentialStore().set_provider_key(spec.name, key)
    except KeyringUnavailableError as exc:
        _credential_error(exc)
        return
    typer.echo(f"Saved {spec.display_name} API key to secure local storage.")


@keys_app.command("remove")
def keys_remove_command(provider: str) -> None:
    """Remove a stored API key from the platform credential manager."""
    try:
        spec = provider_spec(provider)
        removed = CredentialStore().delete_provider_key(spec.name)
    except (ValueError, KeyringUnavailableError) as exc:
        _credential_error(exc)
        return
    typer.echo(
        f"Removed {spec.display_name} API key."
        if removed
        else f"No {spec.display_name} API key was saved."
    )


@keys_app.command("test")
def keys_test_command(
    provider: str = typer.Argument(...),
    base_url: str | None = typer.Option(
        None, "--base-url", help="Base URL for an OpenAI-compatible provider."
    ),
) -> None:
    """Perform a no-cost provider check with a securely stored API key."""
    try:
        selected = provider_spec(provider)
        store = CredentialStore()
        key = store.get_provider_key(selected.name)
    except (ValueError, KeyringUnavailableError) as exc:
        _credential_error(exc)
        return
    if not key:
        typer.echo(f"No secure {selected.display_name} API key is configured.")
        raise typer.Exit(code=1)
    valid, message = validate_provider_key(selected.name, key, base_url=base_url)
    typer.echo(f"{selected.display_name}: {message}")
    if not valid:
        raise typer.Exit(code=1)


@keys_app.command("rotate")
def keys_rotate_command(
    provider: str = typer.Argument(...),
    base_url: str | None = typer.Option(
        None, "--base-url", help="Base URL for OpenAI-compatible providers."
    ),
    skip_validation: bool = typer.Option(
        False, "--skip-validation", help="Store the key without testing it first."
    ),
) -> None:
    """Replace an existing provider API key with a new one."""
    try:
        spec = provider_spec(provider)
    except ValueError as exc:
        _credential_error(exc)
        return
    store = CredentialStore()
    existing = store.get_provider_key(spec.name)
    if not existing:
        typer.echo(f"No existing {spec.display_name} API key found.  Use 'keys add' instead.")
        raise typer.Exit(code=1)
    new_key = typer.prompt(
        f"New {spec.display_name} API key", hide_input=True, confirmation_prompt=True
    )
    if not new_key.strip():
        typer.echo("API key must not be blank.", err=True)
        raise typer.Exit(code=1)
    # Validate before replacing.
    if not skip_validation:
        typer.echo(f"Validating new {spec.display_name} key…")
        valid, message = validate_provider_key(spec.name, new_key.strip(), base_url=base_url)
        if not valid:
            typer.echo(f"Validation failed: {message}", err=True)
            typer.echo("The old key has NOT been replaced.", err=True)
            typer.echo("Use --skip-validation to replace anyway.", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"  ✓ {message}")
    try:
        store.set_provider_key(spec.name, new_key)
    except KeyringUnavailableError as exc:
        _credential_error(exc)
        return
    typer.echo(f"Rotated {spec.display_name} API key in secure local storage.")


@keys_app.command("migrate")
def keys_migrate_command(
    env_file: Path = typer.Option(
        Path(".env"), "--env-file", help="Path to the .env file to scan."
    ),
    remove: bool = typer.Option(
        False, "--remove", help="Remove migrated keys from the .env file."
    ),
) -> None:
    """Detect provider API keys in .env and import them into secure storage."""
    migrated, skipped = _migrate_env_keys(env_file, remove=remove)
    if not migrated and not skipped:
        typer.echo("No provider API keys found in the .env file.")
        return
    for name in migrated:
        typer.echo(f"  ✓ Migrated {name}")
    for name, reason in skipped:
        typer.echo(f"  ✗ Skipped {name}: {reason}")
    if migrated:
        typer.echo(f"\n{len(migrated)} key(s) imported into secure local storage.")


def _migrate_env_keys(
    env_path: Path, *, remove: bool
) -> tuple[list[str], list[tuple[str, str]]]:
    """Scan a .env file for provider keys and migrate them to the keyring.

    Returns (migrated_names, [(skipped_name, reason), ...]).
    Never displays any key values.
    """
    if not env_path.exists():
        return [], []

    migrated: list[str] = []
    skipped: list[tuple[str, str]] = []
    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    removed_vars: set[str] = set()

    store = CredentialStore()
    for spec in provider_specs():
        if not spec.environment_variable:
            continue
        env_var = spec.environment_variable
        # Find the value in the .env content.
        value = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            var_name, _, var_value = stripped.partition("=")
            if var_name.strip() == env_var and var_value.strip():
                value = var_value.strip().strip("'\"")
                break
        if not value:
            continue
        # Check if already in keyring.
        try:
            existing = store.get_provider_key(spec.name)
            if existing:
                skipped.append((spec.display_name, "already configured in keyring"))
                continue
        except KeyringUnavailableError:
            skipped.append((spec.display_name, "keyring unavailable"))
            continue
        # Confirm with user.
        if not typer.confirm(
            f"Import {spec.display_name} key from .env into secure storage?"
        ):
            skipped.append((spec.display_name, "user declined"))
            continue
        try:
            store.set_provider_key(spec.name, value)
            # Verify.
            stored = store.get_provider_key(spec.name)
            if stored != value:
                skipped.append((spec.display_name, "verification failed"))
                continue
        except KeyringUnavailableError:
            skipped.append((spec.display_name, "keyring unavailable"))
            continue
        migrated.append(spec.display_name)
        if remove:
            removed_vars.add(env_var)

    # Remove migrated keys from .env if requested.
    if remove and removed_vars:
        new_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if "=" in stripped and not stripped.startswith("#"):
                var_name = stripped.partition("=")[0].strip()
                if var_name in removed_vars:
                    continue
            new_lines.append(line)
        env_path.write_text("".join(new_lines), encoding="utf-8")

    return migrated, skipped


@app.command("config")
def config_command() -> None:
    """Show safe local configuration and credential-store status."""
    settings = Settings()
    oauth_ready = bool(os.environ.get("GOOGLE_CLIENT_ID"))
    keyring_ok = CredentialStore.is_available()
    typer.echo(f"Google OAuth:     {'configured' if oauth_ready else 'not configured'}")
    typer.echo(f"Keyring Backend:  {CredentialStore.backend_name()}")
    typer.echo(f"Keyring Status:   {'available' if keyring_ok else 'unavailable'}")
    typer.echo(f"Provider:         {settings.provider_name}")
    typer.echo(f"Model:            {settings.agent_model}")
    typer.echo("API keys: managed through 'agent47 keys' and never displayed here.")


@app.command("models")
def models_command() -> None:
    """List available model presets."""
    typer.echo(format_model_presets())


@platform_app.command("inspect")
def platform_inspect_command(
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    trust_workspace_extensions: bool = typer.Option(
        False,
        "--trust-workspace-extensions",
        help="Load workspace plugins and start enabled MCP server commands.",
    ),
) -> None:
    """Print the active platform registry as machine-readable JSON."""
    runtime = PlatformRuntime.create(
        cwd.resolve(), trust_workspace_extensions=trust_workspace_extensions
    )
    try:
        typer.echo(json.dumps(runtime.capabilities(), indent=2, sort_keys=True))
    finally:
        runtime.close()


def _execution_runtime(db: Path | None) -> DurableExecutionRuntime:
    settings = Settings()
    return DurableExecutionRuntime((db or settings.agent_execution_db_path).resolve())


@execution_app.command("create")
def execution_create_command(
    goal: str = typer.Argument(..., help="Durable execution goal."),
    db: Optional[Path] = typer.Option(None, "--db", help="Execution event database."),
    token_budget: int = typer.Option(100_000, "--token-budget", min=1),
    dollar_budget: float = typer.Option(25.0, "--dollar-budget", min=0),
    profile: str = typer.Option("auto", "--profile", help="Execution profile: auto, simple, medium, complex."),
) -> None:
    """Create an event-sourced execution with an initial root task."""
    from .execution_profiles import ExecutionComplexity, TaskIntent

    runtime = _execution_runtime(db)

    explicit_complexity: ExecutionComplexity | None = None
    if profile.lower() != "auto":
        explicit_complexity = ExecutionComplexity(profile.lower())

    intent = TaskIntent(goal=goal, explicit_complexity=explicit_complexity)

    # When explicit budgets are given, pass them through; otherwise let the profile decide
    budgets: dict[str, float] | None = None
    if token_budget != 100_000 or dollar_budget != 25.0:
        budgets = {"tokens": token_budget, "dollars": dollar_budget}

    execution_id = runtime.create_planned(goal, budgets=budgets, intent=intent)

    state = runtime.engine.state(execution_id)
    if state.active_profile:
        complexity = state.active_profile.get("complexity", "unknown").upper()
        typer.echo(f"Execution Profile: {complexity}")

    typer.echo(execution_id)


@execution_app.command("list")
def execution_list_command(
    db: Optional[Path] = typer.Option(None, "--db"),
    limit: int = typer.Option(20, "--limit", min=1, max=1000),
) -> None:
    runtime = _execution_runtime(db)
    typer.echo(json.dumps(runtime.store.executions(limit), indent=2))


@execution_app.command("show")
def execution_show_command(
    execution_id: str,
    db: Optional[Path] = typer.Option(None, "--db"),
) -> None:
    runtime = _execution_runtime(db)
    typer.echo(json.dumps(runtime.engine.state(execution_id).canonical(), indent=2, sort_keys=True))


@execution_app.command("trace")
def execution_trace_command(
    execution_id: str,
    db: Optional[Path] = typer.Option(None, "--db"),
) -> None:
    runtime = _execution_runtime(db)
    typer.echo(json.dumps(runtime.trace.export(execution_id), indent=2, sort_keys=True))


@execution_app.command("replay")
def execution_replay_command(
    execution_id: str,
    db: Optional[Path] = typer.Option(None, "--db"),
) -> None:
    runtime = _execution_runtime(db)
    state = runtime.engine.replay(execution_id)
    typer.echo(json.dumps(state.canonical(), indent=2, sort_keys=True))


@execution_app.command("recover")
def execution_recover_command(
    execution_id: str,
    db: Optional[Path] = typer.Option(None, "--db"),
) -> None:
    """Restore from snapshot and events, quarantining ambiguous running effects."""
    runtime = _execution_runtime(db)
    state = runtime.recover(execution_id)
    typer.echo(json.dumps(state.canonical(), indent=2, sort_keys=True))


@execution_app.command("explain")
def execution_explain_command(
    execution_id: str,
    db: Optional[Path] = typer.Option(None, "--db"),
) -> None:
    """Explain blockers, criteria, replans, models, budgets, and critical path."""
    runtime = _execution_runtime(db)
    inspector = ExecutionInspector(runtime.store)
    typer.echo(json.dumps(
        inspector.explain(runtime.engine.state(execution_id)), indent=2, sort_keys=True
    ))


@execution_app.command("shadow-report")
def execution_shadow_report_command(
    execution_id: Optional[str] = typer.Argument(None),
    db: Optional[Path] = typer.Option(None, "--db"),
) -> None:
    """Report structured legacy-versus-engine shadow divergences."""
    runtime = _execution_runtime(db)
    store = ShadowDivergenceStore(runtime.store.path)
    typer.echo(json.dumps({
        "metrics": store.metrics(execution_id),
        "divergences": store.list(execution_id),
    }, indent=2, sort_keys=True))


@execution_app.command("promotion-status")
def execution_promotion_status_command(
    db: Optional[Path] = typer.Option(None, "--db"),
) -> None:
    runtime = _execution_runtime(db)
    typer.echo(json.dumps(MigrationStateStore(runtime.store.path).get(), indent=2, sort_keys=True))


@execution_app.command("runtime-status")
def execution_runtime_status_command(
    db: Optional[Path] = typer.Option(None, "--db"),
    minimum_samples: int = typer.Option(100, "--minimum-samples", min=1),
    maximum_divergence_rate: float = typer.Option(
        0.01, "--maximum-divergence-rate", min=0, max=1
    ),
) -> None:
    """Show the current authority stage and the evidence required for its next promotion."""
    runtime = _execution_runtime(db)
    migration = MigrationStateStore(runtime.store.path).get()
    current = PromotionStage(migration["stage_value"])
    next_stage = (
        PromotionStage(current.value + 1)
        if current != PromotionStage.ENGINE_ONLY
        else None
    )
    decision_types = PROMOTION_DECISION_TYPES.get(next_stage, ()) if next_stage else ()
    metrics = ShadowDivergenceStore(runtime.store.path).metrics(
        decision_types=decision_types or None
    )
    policy = PromotionPolicy(
        minimum_samples=minimum_samples,
        maximum_divergence_rate=maximum_divergence_rate,
    )
    eligible, reason = (
        policy.evaluate(metrics)
        if next_stage is not None
        else (False, "The runtime is already engine-only.")
    )
    typer.echo(json.dumps({
        **migration,
        "next_stage": next_stage.name.lower() if next_stage else None,
        "required_decision_types": list(decision_types),
        "qualification": {"eligible": eligible, "reason": reason, **metrics},
    }, indent=2, sort_keys=True))


@execution_app.command("recover-active")
def execution_recover_active_command(
    db: Optional[Path] = typer.Option(None, "--db"),
) -> None:
    """Replay every active execution and quarantine ambiguous running effects."""
    runtime = _execution_runtime(db)
    recovered: list[dict[str, object]] = []
    for item in runtime.store.executions(limit=10_000):
        execution_id = str(item["execution_id"])
        state = runtime.engine.state(execution_id)
        if state.status.value != "active":
            continue
        state = runtime.recover(execution_id)
        recovered.append({
            "execution_id": execution_id,
            "status": state.status.value,
            "unknown_effects": sorted(
                effect.id
                for effect in state.effects.values()
                if effect.state.value == "unknown"
            ),
        })
    typer.echo(json.dumps({"recovered": recovered}, indent=2, sort_keys=True))


@execution_app.command("promote")
def execution_promote_command(
    target: str = typer.Argument(..., help="Next promotion stage name."),
    db: Optional[Path] = typer.Option(None, "--db"),
    minimum_samples: int = typer.Option(100, "--minimum-samples", min=1),
    maximum_divergence_rate: float = typer.Option(
        0.01, "--maximum-divergence-rate", min=0, max=1
    ),
) -> None:
    runtime = _execution_runtime(db)
    try:
        stage = PromotionStage[target.strip().upper()]
    except KeyError as exc:
        raise typer.BadParameter(
            "Unknown stage. Use trace_projection, planning, scheduling_budgets, "
            "verification_replanning, side_effects, recovery_completion, or engine_only."
        ) from exc
    migration = MigrationStateStore(runtime.store.path)
    state = migration.promote(
        stage,
        ShadowDivergenceStore(runtime.store.path),
        PromotionPolicy(
            minimum_samples=minimum_samples,
            maximum_divergence_rate=maximum_divergence_rate,
        ),
    )
    typer.echo(json.dumps(state, indent=2, sort_keys=True))


def _dispatch_execution_control(execution_id: str, db: Path | None, command: str) -> None:
    runtime = _execution_runtime(db)
    runtime.engine.dispatch(Command(command, execution_id))
    typer.echo(json.dumps({"execution_id": execution_id, "status": runtime.engine.state(execution_id).status.value}))


@execution_app.command("pause")
def execution_pause_command(execution_id: str, db: Optional[Path] = typer.Option(None, "--db")) -> None:
    _dispatch_execution_control(execution_id, db, "PauseExecution")


@execution_app.command("resume")
def execution_resume_command(execution_id: str, db: Optional[Path] = typer.Option(None, "--db")) -> None:
    _dispatch_execution_control(execution_id, db, "ResumeExecution")


@execution_app.command("cancel")
def execution_cancel_command(execution_id: str, db: Optional[Path] = typer.Option(None, "--db")) -> None:
    _dispatch_execution_control(execution_id, db, "CancelExecution")


@execution_app.command("checkpoint")
def execution_checkpoint_command(
    execution_id: str,
    reason: str = typer.Option("operator", "--reason"),
    db: Optional[Path] = typer.Option(None, "--db"),
) -> None:
    runtime = _execution_runtime(db)
    checksum = runtime.engine.checkpoint(execution_id, reason)
    typer.echo(json.dumps({"execution_id": execution_id, "checksum": checksum}))


@execution_app.command("approve")
def execution_approve_command(
    execution_id: str,
    approval_id: str,
    granted_by: str = typer.Option("operator", "--granted-by"),
    db: Optional[Path] = typer.Option(None, "--db"),
) -> None:
    runtime = _execution_runtime(db)
    runtime.engine.dispatch(Command("GrantApproval", execution_id, {
        "approval_id": approval_id, "granted_by": granted_by,
    }))
    typer.echo(json.dumps({"execution_id": execution_id, "approval_id": approval_id, "granted": True}))


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
        help="Model preset, such as qwen-coder, gemini-flash, deepseek-pro, or glm-5.2.",
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
    execution_id: Optional[str] = typer.Option(
        None,
        "--execution-id",
        help="Resume an active durable execution with the same task goal.",
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

    reporter = StatusReporter()
    agent = create_agent(
        settings=settings,
        cwd=workspace,
        model=model,
        dry_run=dry_run,
        max_steps=max_steps,
        max_failures=max_failures,
        approval_callback=reporter.guard_prompt(confirm_permission),
        reporter=reporter,
        stream_model=stream,
        profile=profile,
        provider=provider,
        preset=preset,
        shell_network_policy="deny" if deny_network_shell else None,
        sandbox_backend=sandbox_policy.backend,
        require_process_isolation=sandbox,
        durable_execution_id=execution_id,
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
    trials: int = typer.Option(
        1,
        "--trials",
        min=1,
        max=20,
        help="Repeat every live eval case to measure model variance.",
    ),
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
            trials=trials,
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


def _workspace_container(cwd: Path, backend: Optional[str]) -> ContainerManager:
    settings = Settings()
    policy = resolve_sandbox_policy(
        cwd.resolve(),
        backend=backend or settings.sandbox_backend,
        container_image=settings.agent_sandbox_image,
        require_process_isolation=True,
    )
    return ContainerManager(cwd.resolve(), policy)


@containers_app.command("start")
def container_start_command(
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    backend: Optional[str] = typer.Option(None, "--backend", help="Docker or Podman."),
) -> None:
    """Lazily create or reuse the hardened workspace execution container."""
    try:
        record = _workspace_container(cwd, backend).ensure_running()
    except (ContainerError, SandboxIsolationError) as exc:
        typer.echo(f"Container unavailable: {exc}")
        raise typer.Exit(code=1)
    typer.echo(json.dumps(record.__dict__, indent=2, ensure_ascii=False))


@containers_app.command("status")
def container_status_command(
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    backend: Optional[str] = typer.Option(None, "--backend", help="Docker or Podman."),
) -> None:
    """Inspect persisted and live workspace container state."""
    try:
        payload = _workspace_container(cwd, backend).reconcile()
    except (ContainerError, SandboxIsolationError) as exc:
        typer.echo(f"Container unavailable: {exc}")
        raise typer.Exit(code=1)
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))


@containers_app.command("stop")
def container_stop_command(
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    backend: Optional[str] = typer.Option(None, "--backend", help="Docker or Podman."),
    remove: bool = typer.Option(False, "--remove", help="Remove container state instead of stopping."),
) -> None:
    """Gracefully stop or fully remove the reusable workspace container."""
    try:
        changed = _workspace_container(cwd, backend).stop(remove=remove)
    except (ContainerError, SandboxIsolationError) as exc:
        typer.echo(f"Container unavailable: {exc}")
        raise typer.Exit(code=1)
    typer.echo("Container removed." if remove and changed else "Container stopped." if changed else "No container found.")


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
        help="Model preset, such as qwen-coder, gemini-flash, deepseek-pro, or glm-5.2.",
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
        help="Model preset, such as qwen-coder, gemini-flash, deepseek-pro, or glm-5.2.",
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

    prior_steps = storage.run_steps_payloads(run_id)
    task = build_resume_task(
        run_row,
        prior_steps,
        instruction or None,
        storage.get_work_report(run_id),
    )
    reporter = StatusReporter()
    agent = create_agent(
        settings=settings,
        cwd=workspace,
        model=model,
        dry_run=dry_run,
        max_steps=max_steps,
        max_failures=max_failures,
        approval_callback=reporter.guard_prompt(confirm_permission),
        reporter=reporter,
        stream_model=stream,
        profile=profile,
        provider=provider,
        preset=preset,
        shell_network_policy="deny" if deny_network_shell else None,
        sandbox_backend=sandbox_policy.backend,
        require_process_isolation=sandbox,
        execution_state_snapshot=latest_execution_state(prior_steps),
        resumed_from_run_id=run_id,
        durable_execution_id=latest_durable_execution_id(prior_steps),
        durable_goal=str(run_row["task"]),
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


@processes_app.command("start")
def processes_start_command(
    command: str = typer.Argument(..., help="Shell-free command to start."),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    name: Optional[str] = typer.Option(None, "--name", help="Stable display name."),
    working_directory: Optional[str] = typer.Option(
        None,
        "--working-directory",
        help="Workspace-relative process working directory.",
    ),
    interactive: bool = typer.Option(False, "--interactive", help="Keep stdin controllable."),
    pty: bool = typer.Option(False, "--pty", help="Allocate a native PTY where supported."),
    timeout_seconds: int = typer.Option(0, "--timeout", min=0, help="Zero disables timeout."),
    readiness_port: Optional[int] = typer.Option(None, "--port", min=1, max=65535),
    auto_restart: bool = typer.Option(False, "--auto-restart"),
    max_restarts: int = typer.Option(3, "--max-restarts", min=0, max=20),
    memory_limit_mb: Optional[int] = typer.Option(None, "--memory-mb", min=16),
    cpu_time_limit_seconds: Optional[int] = typer.Option(None, "--cpu-seconds", min=1),
) -> None:
    """Start a durable managed process."""
    _run_process_tool(
        cwd,
        StartProcessAction(
            type="start_process",
            command=command,
            name=name,
            working_directory=working_directory,
            interactive=interactive,
            pty=pty,
            timeout_seconds=timeout_seconds,
            readiness_port=readiness_port,
            auto_restart=auto_restart,
            max_restarts=max_restarts,
            memory_limit_mb=memory_limit_mb,
            cpu_time_limit_seconds=cpu_time_limit_seconds,
        ),
    )


@processes_app.command("list")
def processes_list_command(
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    active: bool = typer.Option(False, "--active", help="Hide finished jobs."),
) -> None:
    """List persisted process state."""
    _run_process_tool(
        cwd,
        ListProcessesAction(type="list_processes", include_finished=not active),
    )


@processes_app.command("inspect")
def processes_inspect_command(
    process_id: str,
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
) -> None:
    """Inspect lifecycle, health, ports, and resources."""
    _run_process_tool(cwd, InspectProcessAction(type="inspect_process", process_id=process_id))


@processes_app.command("logs")
def processes_logs_command(
    process_id: str,
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    stream: str = typer.Option("all", "--stream", help="all, stdout, stderr, or terminal."),
    tail_chars: int = typer.Option(20000, "--tail-chars", min=100, max=200000),
) -> None:
    """Read bounded persistent logs."""
    _run_process_tool(
        cwd,
        ReadProcessLogsAction(
            type="read_process_logs",
            process_id=process_id,
            stream=stream,
            tail_chars=tail_chars,
        ),
    )


@processes_app.command("events")
def processes_events_command(
    process_id: str,
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    after: int = typer.Option(0, "--after", min=0, help="Return events after this cursor."),
    limit: int = typer.Option(500, "--limit", min=1, max=2000),
) -> None:
    """Consume ordered NDJSON-derived process events."""
    _run_process_tool(
        cwd,
        ProcessEventsAction(type="process_events", process_id=process_id, after=after, limit=limit),
    )


@processes_app.command("input")
def processes_input_command(
    process_id: str,
    data: str = typer.Argument(..., help="Input text; include a newline when required."),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
) -> None:
    """Send input through a durable interactive control channel."""
    _run_process_tool(
        cwd,
        SendProcessInputAction(type="send_process_input", process_id=process_id, data=data),
    )


@processes_app.command("stop")
def processes_stop_command(
    process_id: str,
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    grace_seconds: float = typer.Option(5.0, "--grace", min=0.1, max=60.0),
) -> None:
    """Gracefully stop a process tree, then force it if needed."""
    _run_process_tool(
        cwd,
        StopProcessAction(
            type="stop_process",
            process_id=process_id,
            grace_seconds=grace_seconds,
        ),
    )


@processes_app.command("restart")
def processes_restart_command(
    process_id: str,
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
) -> None:
    """Request an immediate managed restart."""
    _run_process_tool(cwd, RestartProcessAction(type="restart_process", process_id=process_id))


def _run_process_tool(cwd: Path, action: object) -> None:
    read_actions = {"list_processes", "inspect_process", "read_process_logs", "process_events"}

    def approve(action_name: str, detail: str) -> str | bool:
        return True if action_name in read_actions else confirm_permission(action_name, detail)

    tools = ToolRegistry(workspace=cwd.resolve(), dry_run=False, approval_callback=approve)
    result = tools.run(action)  # type: ignore[arg-type]
    typer.echo(result.output)
    if not result.ok:
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
