from __future__ import annotations

import _thread
import contextlib
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time
import traceback
from typing import Protocol

import typer  # noqa: F401
from rich.prompt import Prompt
from rich.table import Table
from typer._click.exceptions import Abort

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.formatted_text import HTML

from .config import Settings
from .auth.session import LocalSession
from .credentials.keyring import CredentialStore, KeyringUnavailableError
from .credentials.providers import ProviderMetadata, provider_specs
from .factory import create_agent, create_chat_client
from .diff_launcher import launch_diff_viewer
from .diff_types import DiffViewMode
from .model_profiles import validate_profile_name
from .model_presets import MODEL_PRESETS, resolve_model_preset
from .model_registry import REGISTERED_MODELS, find_registered_model, validate_model_selection
from .permissions import ApprovalMode, PermissionPolicy, confirm_permission
from .resume import build_resume_task, format_run_detail, latest_execution_state
from .revert import apply_revert_plan, build_revert_plan, format_revert_preview
from .sandbox import (
    create_sandbox_workspace,
    diff_sandbox_workspace,
    format_sandbox_diff,
    promote_sandbox_changes,
)
from .sandbox_security import SandboxIsolationError, resolve_sandbox_policy
from .session import SessionState
from .storage import AgentStorage
from .status import StatusReporter, analyze_workspace
from .terminal_ui import (
    console,
    print_error_card,
    print_key_values,
    print_panel,
    print_renderable_panel,
    print_startup_header,
    print_work_report_panel,
    print_response,
    print_help_panel,
    print_advanced_panel,
    print_keys_panel,
    print_status_panel,
    print_command_preview,
    print_suggestions_panel,
)
from rich.text import Text
from .work_report import should_show_work_report
from .command_registry import CommandCompleter, build_default_registry
from .fuzzy import suggest_commands

SESSION_REGISTRY = build_default_registry()


DEFAULT_DRY_RUN = False
CTRL_C = "\x03"
CTRL_E = "\x05"


class InteractiveExitRequested(KeyboardInterrupt):
    """Raised when the user asks the interactive agent to exit immediately."""


class InteractiveAgent(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str: ...
    def run_detailed(self, task: str): ...
    def cancel(self, reason: str = "user stop") -> int: ...


def main() -> None:
    # `agent47` remains the interactive default, while named commands share
    # the richer Typer CLI used by `code-agent` (for example `agent47 login`).
    if len(sys.argv) > 1:
        from .cli import app

        app(prog_name="agent47")
        return
    settings = Settings()
    onboarding_model = require_interactive_onboarding(settings)
    if onboarding_model is _ONBOARDING_BLOCKED:
        return
    base_cwd = Path.cwd().resolve()
    cwd = base_cwd
    model: str | None = onboarding_model
    profile: str | None = None
    dry_run = DEFAULT_DRY_RUN
    stream_model = settings.agent_stream
    sandbox_enabled = False
    max_steps = 12
    max_failures: int | None = None
    transcript: list[tuple[str, str]] = []
    session_state = SessionState()
    permission_policy = PermissionPolicy(confirm_permission, ApprovalMode.auto_read)

    print_startup_header(
        cwd,
        "write-enabled" if not dry_run else "dry-run",
        current_model_name(settings, model),
        version=agent_version(),
        profile=profile or settings.agent_profile,
        provider=current_provider_name(settings, model),
        sandbox=sandbox_enabled,
        approval=permission_policy.mode.value,
        git_branch=git_branch(cwd),
    )
    workspace_summary = analyze_workspace(cwd)
    StatusReporter.mark_workspace_seen(workspace_summary)

    session = PromptSession(completer=CommandCompleter(SESSION_REGISTRY), complete_while_typing=True)
    while True:
        try:
            with patch_stdout():
                user_input = read_prompt(session, settings, cwd, model, profile)
        except InteractiveExitRequested:
            print_panel("System", "bye")
            return
        except (EOFError, KeyboardInterrupt, Abort):
            print_panel("System", "bye")
            return

        if not user_input:
            continue

        if is_persona_instruction(user_input):
            session_state.set_steering(user_input)
            print_response("Agent47", "Got it. I will use that as guidance for future turns.")
            continue

        if user_input.startswith("/"):
            command_result = handle_command(
                user_input,
                settings,
                base_cwd,
                cwd,
                model,
                profile,
                dry_run,
                stream_model,
                sandbox_enabled,
                max_steps,
                max_failures,
                session_state,
                permission_policy,
            )
            if command_result.exit_requested:
                print_panel("System", "bye")
                return
            base_cwd = command_result.base_cwd
            cwd = command_result.cwd
            model = command_result.model
            profile = command_result.profile
            dry_run = command_result.dry_run
            stream_model = command_result.stream_model
            sandbox_enabled = command_result.sandbox_enabled
            max_steps = command_result.max_steps
            max_failures = command_result.max_failures
            continue

        # Chat vs Execute separation: lightweight chat path for non-workspace questions
        if should_use_lightweight_chat(user_input, transcript, session_state):
            client: InteractiveAgent | None = None
            reporter: StatusReporter | None = None
            try:
                client = create_chat_client(
                    settings=settings,
                    model=model,
                    profile=profile,
                    stream_model=stream_model,
                )
                reporter = StatusReporter()
                reporter.thinking(1)
                with active_shortcuts(client):
                    response = run_lightweight_chat(user_input, client)
                reporter.done()
            except InteractiveExitRequested:
                if reporter:
                    reporter.done()
                if client:
                    client.cancel("interactive exit shortcut")
                print_panel("System", "bye")
                return
            except KeyboardInterrupt:
                if reporter:
                    reporter.done()
                if client:
                    client.cancel("interactive keyboard interrupt")
                print_panel("Stopped", "Current action stopped. Interactive session is still open.")
                continue
            except Exception as exc:
                if reporter:
                    reporter.done()
                session_state._last_exc = traceback.format_exc()
                print_error_card(
                    "Model Request Failed",
                    [
                        ("What failed:", "The lightweight chat request could not be completed."),
                        ("Reason:", friendly_model_error(exc)),
                    ],
                    ["Retry in a moment", "Switch models with /model", "Use /debug for the full traceback"]
                )
                continue
            print_response("Agent47", response)
            transcript.append((user_input, response))
            transcript = transcript[-8:]
            continue

        permission_policy.reset_task()
        agent: InteractiveAgent | None = None
        reporter = StatusReporter()
        try:
            agent = create_agent(
                settings=settings,
                cwd=cwd,
                model=model,
                profile=profile,
                dry_run=dry_run,
                max_steps=max_steps,
                max_failures=max_failures,
                approval_callback=reporter.guard_prompt(permission_policy.approve),
                reporter=reporter,
                stream_model=stream_model,
                require_process_isolation=sandbox_enabled,
            )
            with active_shortcuts(agent):
                transcript = run_interactive_turn(user_input, agent, transcript, session_state)
        except InteractiveExitRequested:
            if agent is not None:
                agent.cancel("interactive exit shortcut")
            print_panel("System", "bye")
            return
        except KeyboardInterrupt:
            if agent is not None:
                agent.cancel("interactive keyboard interrupt")
            print_panel("Stopped", "Current action stopped. Interactive session is still open.")
            continue
        except Exception as exc:
            session_state._last_exc = traceback.format_exc()
            print_error_card(
                "Error",
                [
                    ("Task execution could not be completed.", ""),
                    ("Reason:", str(exc)),
                ],
                ["Use /debug to see the full stack trace", "Check model configurations"]
            )


_ONBOARDING_BLOCKED = object()


def require_interactive_onboarding(settings: Settings) -> str | None | object:
    """Require a local OAuth session and secure BYOK key before interactive use.

    The automation-oriented ``code-agent`` command remains available for scripted
    and CI workflows.  The bare ``agent47`` terminal, however, never starts a
    model session until both local account authentication and a provider key in
    the OS credential store are present.
    """
    if not sys.stdin.isatty():
        print_panel(
            "Interactive Setup Required",
            "Run 'agent47 auth login', then 'agent47 keys add <provider>', from an interactive terminal.",
            style="yellow",
        )
        return _ONBOARDING_BLOCKED

    try:
        session = LocalSession()
        signed_in = session.signed_in()
    except KeyringUnavailableError as exc:
        print_panel("Secure Storage Unavailable", str(exc), style="red")
        return _ONBOARDING_BLOCKED

    if not signed_in:
        print_panel(
            "Welcome to Agent47",
            "Sign in with Google to use the interactive terminal. Your OAuth session is stored in your operating system credential manager.",
            style="cyan",
        )
        if Prompt.ask("Sign in now?", choices=["yes", "no"], default="yes") != "yes":
            print_panel("Sign-in Required", "Run 'agent47 auth login' when you are ready.", style="yellow")
            return _ONBOARDING_BLOCKED
        try:
            _start_auth_login()
        except typer.Exit:
            return _ONBOARDING_BLOCKED
        try:
            if not LocalSession().signed_in():
                print_panel("Sign-in Incomplete", "Login did not create a usable local session.", style="red")
                return _ONBOARDING_BLOCKED
        except KeyringUnavailableError as exc:
            print_panel("Secure Storage Unavailable", str(exc), style="red")
            return _ONBOARDING_BLOCKED

    try:
        store = CredentialStore()
        default_provider = current_provider_name(settings, None)
        if store.get_provider_key(default_provider):
            return None
        return _select_or_add_interactive_provider_key(store, default_provider)
    except KeyringUnavailableError as exc:
        print_panel("Secure Storage Unavailable", str(exc), style="red")
        return _ONBOARDING_BLOCKED


def _select_or_add_interactive_provider_key(
    store: CredentialStore, default_provider: str
) -> str | object:
    """Select a secure provider key and return a compatible default model."""
    providers = [spec for spec in provider_specs() if spec.environment_variable]
    if not providers:
        print_panel("API Key Setup", "No interactive key providers are available.", style="red")
        return _ONBOARDING_BLOCKED

    while True:
        print_panel(
            "API Key Required",
            "Choose a model provider. Agent47 validates and stores your key only in the operating system credential manager.",
            style="yellow",
        )
        for index, spec in enumerate(providers, start=1):
            try:
                state = "saved" if store.get_provider_key(spec.name) else "needs key"
            except KeyringUnavailableError:
                raise
            preferred = " (current model)" if spec.name == default_provider else ""
            console.print(f"  [{index}] {spec.display_name} — {state}{preferred}")
        console.print("  [q] Exit")
        choice = Prompt.ask(
            "Select a provider", choices=[*(str(i) for i in range(1, len(providers) + 1)), "q"]
        )
        if choice == "q":
            print_panel("API Key Required", "No model session was started.", style="yellow")
            return _ONBOARDING_BLOCKED
        selected = providers[int(choice) - 1]
        if not store.get_provider_key(selected.name):
            try:
                _start_key_setup(selected)
            except typer.Exit:
                pass
        if store.get_provider_key(selected.name):
            model = _default_model_for_provider(selected)
            print_panel(
                "Provider Ready",
                f"{selected.display_name} is configured securely. Use /model to choose another model with a saved provider key.",
                style="green",
            )
            return model
        print_panel(
            "API Key Not Saved",
            "Agent47 cannot start a model session until a provider key is saved. Try again or exit.",
            style="yellow",
        )


def _default_model_for_provider(provider: ProviderMetadata) -> str | None:
    for preset in MODEL_PRESETS.values():
        if preset.provider == provider.name:
            return preset.model
    if provider.name == "openai":
        return "gpt-4.1"
    return None


def _start_auth_login() -> None:
    from .cli import auth_login_command

    auth_login_command()


def _start_key_setup(provider: ProviderMetadata) -> None:
    from .cli import keys_add_command

    keys_add_command(provider.name)


class CommandState:
    def __init__(
        self,
        base_cwd: Path,
        cwd: Path,
        model: str | None,
        profile: str | None,
        dry_run: bool,
        stream_model: bool,
        sandbox_enabled: bool,
        max_steps: int,
        max_failures: int | None,
        exit_requested: bool = False,
    ) -> None:
        self.base_cwd = base_cwd
        self.cwd = cwd
        self.model = model
        self.profile = profile
        self.dry_run = dry_run
        self.stream_model = stream_model
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
    profile: str | None,
    dry_run: bool,
    stream_model: bool,
    sandbox_enabled: bool,
    max_steps: int,
    max_failures: int | None,
    session_state: SessionState | None = None,
    permission_policy: PermissionPolicy | None = None,
) -> CommandState:
    parts = raw.split(maxsplit=1)
    command = parts[0].lower()
    value = parts[1].strip() if len(parts) > 1 else ""

    # Record usage if command is known
    matched_meta = SESSION_REGISTRY.lookup(command)
    if matched_meta:
        SESSION_REGISTRY.record_usage(matched_meta.name)

    if command in {"/exit", "/quit", "/q", "/stop"}:
        return CommandState(
            base_cwd,
            cwd,
            model,
            profile,
            dry_run,
            stream_model,
            sandbox_enabled,
            max_steps,
            max_failures,
            exit_requested=True,
        )
    if command == "/help":
        if value:
            meta = SESSION_REGISTRY.lookup(value)
            if meta:
                print_command_preview(meta)
            else:
                print_help()
        else:
            print_help()
    elif command == "/advanced":
        print_advanced_panel(SESSION_REGISTRY.by_category(include_hidden=True))
    elif command == "/keys":
        _handle_keys_command(settings, model)
    elif command in {"/diff-view", "/diff"}:
        diff_text = session_state.last_diff if session_state else None
        pref_mode = session_state.preferred_diff_mode if session_state else None
        res = launch_diff_viewer(diff_text, mode=pref_mode, console=console)
        if not res.success and res.message:
            console.print(res.message)
    elif command == "/diff-mode":
        if not value:
            current_mode = (
                session_state.preferred_diff_mode if session_state else DiffViewMode.UNIFIED
            )
            current_str = (
                "Side-by-Side" if current_mode == DiffViewMode.SIDE_BY_SIDE else "Unified"
            )
            info = (
                f"Current Mode\n  • {current_str}\n\n"
                "Available Modes\n  • unified\n  • side-by-side\n\n"
                "Usage\n  /diff-mode unified\n  /diff-mode side-by-side\n\n"
                "Tip\n  Press TAB while viewing a diff to switch modes temporarily."
            )
            print_panel("Diff Viewer", info)
        else:
            try:
                mode = DiffViewMode.normalize(value)
                if session_state:
                    session_state.set_preferred_diff_mode(mode)
                print_panel("Diff Mode", f"Preferred diff mode updated to: {mode.value}")
            except ValueError:
                print_error_card(
                    "Invalid Diff Mode",
                    [
                        ("Invalid mode:", value),
                        ("Allowed modes:", "unified, side-by-side"),
                    ],
                    ["Use '/diff-mode unified' or '/diff-mode side-by-side'"],
                )
    elif command == "/dry-run":
        dry_run = True
        print_panel("Mode", "dry-run")
    elif command == "/write":
        dry_run = False
        print_panel("Mode", "write-enabled")
    elif command == "/stream":
        stream_model = value.lower() != "off"
        print_panel("Streaming", "on" if stream_model else "off")
    elif command == "/cwd":
        if value:
            base_cwd = Path(value).expanduser().resolve()
            cwd = base_cwd
            sandbox_enabled = False
        print_panel("Workspace", str(cwd))
    elif command == "/sandbox":
        if value.lower() == "off":
            cwd = base_cwd
            sandbox_enabled = False
            print_key_values("Sandbox", [("Sandbox", "off"), ("Workspace", cwd)])
        elif value.lower().startswith("diff"):
            if not sandbox_enabled:
                print_panel("Sandbox", "Sandbox is not active. Use /sandbox first.")
            else:
                paths = value.split()[1:]
                try:
                    diff = diff_sandbox_workspace(base_cwd, cwd, paths=paths)
                except ValueError as exc:
                    print_panel("Sandbox", str(exc))
                else:
                    print_panel("Sandbox Diff", format_sandbox_diff(diff))
        elif value.lower().startswith("apply"):
            if not sandbox_enabled:
                print_panel("Sandbox", "Sandbox is not active. Use /sandbox first.")
            else:
                paths = value.split()[1:]
                try:
                    result = promote_sandbox_changes(
                        base_cwd,
                        cwd,
                        paths=paths,
                        approval_callback=confirm_permission,
                    )
                except ValueError as exc:
                    print_panel("Sandbox Apply", str(exc))
                else:
                    body = result.output
                    if result.changed_paths:
                        body += "\nPromoted files: " + ", ".join(result.changed_paths)
                    print_panel("Sandbox Apply", body)
        else:
            try:
                policy = resolve_sandbox_policy(
                    base_cwd,
                    backend=settings.sandbox_backend,
                    container_image=settings.agent_sandbox_image,
                    require_process_isolation=True,
                )
                sandbox_workspace = create_sandbox_workspace(base_cwd, policy=policy)
            except SandboxIsolationError as exc:
                print_panel("Sandbox Unavailable", str(exc))
            else:
                cwd = sandbox_workspace.path
                sandbox_enabled = True
                print_key_values(
                    "Sandbox",
                    [
                        ("Sandbox", "on"),
                        ("Workspace", cwd),
                        ("Backend", policy.backend),
                        ("Process isolated", "yes"),
                    ],
                )
    elif command == "/model":
        if value.lower() in {"select", "picker", "list"}:
            selected_model = prompt_model_selection(current_model=current_model_name(settings, model))
            if selected_model:
                model = switch_model_or_report(settings, selected_model, stream_model=stream_model) or model
            print_panel("Model", current_model_name(settings, model))
        elif value:
            switched_model = switch_model_or_report(settings, value, stream_model=stream_model)
            if switched_model:
                model = switched_model
                print_panel("Model", model)
        else:
            print_model_status(settings, model, profile)
    elif command == "/profile":
        if value:
            try:
                profile = validate_profile_name(value)
            except ValueError as exc:
                print_panel("Profile", str(exc))
                return CommandState(
                    base_cwd,
                    cwd,
                    model,
                    profile,
                    dry_run,
                    stream_model,
                    sandbox_enabled,
                    max_steps,
                    max_failures,
                )
        print_panel("Profile", profile or settings.agent_profile)
    elif command == "/max-steps":
        if value:
            max_steps = int(value)
        print_panel("Max Steps", str(max_steps))
    elif command == "/max-failures":
        if value:
            max_failures = int(value)
        print_panel("Max Failures", str(max_failures or settings.agent_max_failures))
    elif command == "/history":
        print_history(settings)
    elif command == "/resume":
        run_resume_command(
            value=value,
            settings=settings,
            cwd=cwd,
            model=model,
            profile=profile,
            dry_run=dry_run,
            stream_model=stream_model,
            max_steps=max_steps,
            max_failures=max_failures,
            sandbox_enabled=sandbox_enabled,
            session_state=session_state,
        )
    elif command in {"/revert", "/restore"}:
        run_revert_command(settings, value, session_state=session_state)
    elif command == "/history-show":
        print_history_detail(settings, value)
    elif command == "/status":
        _handle_status_command(settings, cwd, model, profile, dry_run, stream_model, sandbox_enabled, permission_policy)
    elif command == "/approve-all":
        if permission_policy:
            permission_policy.set_mode(ApprovalMode.approve_task)
        print_panel("Mode", "Approve all actions for each task after first approval.")
    elif command == "/auto-read":
        if permission_policy:
            permission_policy.set_mode(ApprovalMode.auto_read)
        print_panel("Mode", "Auto-approve read-only operations. Mutations still require approval.")
    elif command == "/per-action":
        if permission_policy:
            permission_policy.set_mode(ApprovalMode.per_action)
        print_panel("Mode", "Every action requires individual approval.")
    elif command == "/clear":
        console.clear()
    elif command == "/debug":
        exc_trace = getattr(session_state, "_last_exc", None)
        if exc_trace:
            print_panel("Debug Trace", exc_trace, style="red")
        else:
            print_panel("Debug", "No recent errors to show.")
    elif command == "/diff":
        print_panel("Diff", "Detailed diffs are available via /history-show <id> or sandbox diff.")
    elif command == "/report":
        print_panel("Report", "Detailed reports are saved to history. Use /history-show <id> to view.")
    elif command == "/files":
        print_panel("Files", f"Current workspace: {cwd}")
    elif command == "/models":
        selected_model = prompt_model_selection(current_model=current_model_name(settings, model))
        if selected_model:
            model = switch_model_or_report(settings, selected_model, stream_model=stream_model) or model
        print_panel("Model", current_model_name(settings, model))
    elif command == "/settings":
        print_key_values(
            "Settings",
            [
                ("Mode", "dry-run" if dry_run else "write-enabled"),
                ("Approvals", permission_policy.mode.value if permission_policy else "per_action"),
                ("Streaming", "on" if stream_model else "off"),
                ("Sandbox", "on" if sandbox_enabled else "off"),
                ("Model", current_model_name(settings, model)),
                ("Provider", current_provider_name(settings, model)),
                ("Profile", profile or settings.agent_profile),
            ],
        )
    elif command == "/mcp":
        _handle_mcp_command(value, cwd)
    elif command == "/steer":
        if session_state is None:
            print_panel("Steering", "Session steering is unavailable.")
        elif value.lower() in {"clear", "off", "reset"}:
            session_state.clear_steering()
            print_panel("Steering", "cleared")
        elif value:
            session_state.set_steering(value)
            print_panel("Steering", session_state.conversation_steering or "cleared")
        else:
            print_panel("Steering", session_state.conversation_steering or "No steering set.")
    else:
        suggestions = suggest_commands(command, SESSION_REGISTRY)
        print_suggestions_panel(command, suggestions)

    return CommandState(
        base_cwd,
        cwd,
        model,
        profile,
        dry_run,
        stream_model,
        sandbox_enabled,
        max_steps,
        max_failures,
    )


def print_help() -> None:
    """Render categorized help view using the single source of truth registry."""
    print_help_panel(SESSION_REGISTRY.by_category(include_hidden=False))


def _handle_keys_command(settings: Settings, model: str | None) -> None:
    """Gather provider API key configuration state and render /keys panel."""
    store = CredentialStore() if CredentialStore.is_available() else None
    provider_states: list[tuple[str, bool, str]] = []

    for spec in provider_specs():
        if not spec.environment_variable and spec.name != "compatible":
            continue
        has_key = False
        if store:
            try:
                has_key = bool(store.get_provider_key(spec.name))
            except Exception:
                has_key = False
        if not has_key:
            env_key = getattr(settings, f"{spec.name}_api_key", None)
            has_key = bool(env_key)

        provider_states.append((spec.display_name, has_key, spec.environment_variable or ""))

    current_prov = current_provider_name(settings, model)
    current_mod = current_model_name(settings, model)
    fallback_chain = settings.fallback_model_list or [current_mod]

    print_keys_panel(provider_states, current_prov, current_mod, fallback_chain)


def _handle_status_command(
    settings: Settings,
    cwd: Path,
    model: str | None,
    profile: str | None,
    dry_run: bool,
    stream_model: bool,
    sandbox_enabled: bool,
    permission_policy: PermissionPolicy | None,
) -> None:
    """Gather environment, auth, model, and workspace status and render /status panel."""
    auth_status = "✓ Signed in" if LocalSession().signed_in() else "✗ Not signed in"
    branch = git_branch(cwd) or ""
    
    # Calculate API key summary
    store = CredentialStore() if CredentialStore.is_available() else None
    key_summary_parts: list[str] = []
    for p_name in ["gemini", "openrouter", "openai", "anthropic", "deepseek"]:
        has_k = False
        if store:
            try:
                has_k = bool(store.get_provider_key(p_name))
            except Exception:
                has_k = False
        if not has_k:
            has_k = bool(getattr(settings, f"{p_name}_api_key", None))
        symbol = "✓" if has_k else "✗"
        key_summary_parts.append(f"{p_name.capitalize()} {symbol}")
    key_summary = "  ".join(key_summary_parts)

    rows: list[tuple[str, str, str]] = [
        ("Workspace", "Path", str(cwd)),
        ("Workspace", "Git Branch", branch),
        ("Authentication", "Account", auth_status),
        ("Model", "Current Model", current_model_name(settings, model)),
        ("Model", "Provider", current_provider_name(settings, model)),
        ("Model", "Profile", profile or settings.agent_profile),
        ("Session", "Mode", "dry-run" if dry_run else "write-enabled"),
        ("Session", "Approvals", permission_policy.mode.value if permission_policy else "auto_read"),
        ("Session", "Sandbox", "enabled" if sandbox_enabled else "disabled"),
        ("Session", "Streaming", "on" if stream_model else "off"),
        ("API Keys", "Summary", key_summary),
    ]

    print_status_panel(rows)


def print_history(settings: Settings) -> None:
    storage = AgentStorage(settings.agent_db_path)
    rows = storage.recent_runs(10)
    if not rows:
        print_panel("History", "No runs recorded yet.")
        return
    table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    table.add_column("Run", style="cyan", no_wrap=True)
    table.add_column("Created", style="muted", no_wrap=True)
    table.add_column("Model", overflow="fold")
    table.add_column("Task", overflow="fold")
    for row in rows:
        table.add_row(str(row["id"]), str(row["created_at"]), str(row["model"]), str(row["task"]))
    print_renderable_panel("History", table, style="cyan")


def print_history_detail(settings: Settings, value: str) -> None:
    if not value:
        print_panel("History", "Usage: /history-show <run-id>")
        return
    try:
        run_id = int(value)
    except ValueError:
        print_panel("History", f"Invalid run id: {value}")
        return
    storage = AgentStorage(settings.agent_db_path)
    run_row = storage.get_run(run_id)
    if run_row is None:
        print_panel("History", f"No run found with id {run_id}.")
        return
    print_panel(
        "History",
        format_run_detail(
            run_row,
            storage.run_steps_payloads(run_id),
            storage.get_work_report(run_id),
        ),
    )


def run_resume_command(
    value: str,
    settings: Settings,
    cwd: Path,
    model: str | None,
    profile: str | None,
    dry_run: bool,
    stream_model: bool,
    max_steps: int,
    max_failures: int | None,
    sandbox_enabled: bool,
    session_state: SessionState | None,
) -> None:
    if not value:
        print_panel("Resume", "Usage: /resume <run-id> [extra instruction]")
        return
    raw_run_id, _, instruction = value.partition(" ")
    try:
        run_id = int(raw_run_id)
    except ValueError:
        print_panel("Resume", f"Invalid run id: {raw_run_id}")
        return

    storage = AgentStorage(settings.agent_db_path)
    run_row = storage.get_run(run_id)
    if run_row is None:
        print_panel("Resume", f"No run found with id {run_id}.")
        return

    prior_steps = storage.run_steps_payloads(run_id)
    task = build_resume_task(
        run_row,
        prior_steps,
        instruction.strip() or None,
        storage.get_work_report(run_id),
    )
    try:
        reporter = StatusReporter()
        agent = create_agent(
            settings=settings,
            cwd=cwd,
            model=model,
            profile=profile,
            dry_run=dry_run,
            max_steps=max_steps,
            max_failures=max_failures,
            approval_callback=reporter.guard_prompt(confirm_permission),
            reporter=reporter,
            stream_model=stream_model,
            require_process_isolation=sandbox_enabled,
            execution_state_snapshot=latest_execution_state(prior_steps),
            resumed_from_run_id=run_id,
        )
    except SandboxIsolationError as exc:
        print_panel("Sandbox Unavailable", str(exc))
        return
    result = agent.run_detailed(task)
    print_work_report_panel(result)
    if not should_show_work_report(result):
        print_response("Agent47", result.message)
    if session_state is not None:
        session_state.update(f"resume run {run_id}", result)


def run_revert_command(
    settings: Settings,
    value: str,
    *,
    session_state: SessionState | None = None,
) -> None:
    if not value:
        value = "last"
    if value.lower() == "last":
        if session_state is None or session_state.last_run_id is None:
            print_panel("Restore", "No last run is available. Use /history, then /restore <run-id>.")
            return
        run_id = session_state.last_run_id
    else:
        try:
            run_id = int(value)
        except ValueError:
            print_panel("Restore", f"Invalid run id: {value}")
            return
    if run_id is None:
        print_panel("Restore", "Usage: /restore <run-id|last>")
        return
    storage = AgentStorage(settings.agent_db_path)
    try:
        plan = build_revert_plan(storage, run_id)
    except ValueError as exc:
        print_panel("Restore", str(exc))
        return
    print_panel("Restore Preview", format_revert_preview(plan))
    result = apply_revert_plan(storage, plan, approval_callback=confirm_permission)
    body = result.output + f"\nRevert run id: {result.run_id}"
    print_panel("Restore", body)


def prompt_model_selection(current_model: str) -> str | None:
    print_renderable_panel("Models", build_model_selection_table(current_model), style="cyan")
    choice = Prompt.ask(
        "Select model preset by number/name, enter a custom model id, or press Enter to keep current",
        default="",
        show_default=False,
    ).strip()
    if not choice:
        return None
    if choice.isdigit():
        index = int(choice) - 1
        presets = list(MODEL_PRESETS.values())
        if 0 <= index < len(presets):
            return presets[index].model
        print_panel("Model", f"Invalid selection: {choice}", style="red")
        return None
    try:
        preset = resolve_model_preset(choice)
    except ValueError:
        return choice
    return preset.model if preset else None


def format_model_selection_preview(current_model: str) -> str:
    lines = [
        f"Current model: {current_model}",
        "",
        "Built-in presets:",
    ]
    for index, preset in enumerate(MODEL_PRESETS.values(), start=1):
        registered = REGISTERED_MODELS.get(preset.name)
        meta = format_model_metadata(registered) if registered else ""
        lines.append(f"{index}. {preset.name}  provider={preset.provider}  model={preset.model}")
        lines.append(f"   {preset.description}")
        if meta:
            lines.append(f"   {meta}")
    lines.extend(
        [
            "",
            "Tip: /model <id> accepts any provider model id.",
            "Shortcut note: most terminals encode Ctrl+M exactly like Enter; "
            "use /models when your terminal cannot distinguish it.",
        ]
    )
    return "\n".join(lines)


def build_model_selection_table(current_model: str) -> Table:
    table = Table(
        show_header=True,
        header_style="bold cyan",
        box=None,
        padding=(0, 1),
        title=Text(f"Current model: {current_model}", style="muted"),
        title_justify="left",
        expand=True,
    )
    table.add_column("#", justify="right", style="cyan", no_wrap=True)
    table.add_column("Preset", style="bold", overflow="ellipsis", max_width=22)
    table.add_column("Provider", style="muted", no_wrap=True, max_width=12)
    table.add_column("Model", style="cyan", overflow="fold", ratio=2, min_width=18)
    table.add_column("Ctx", no_wrap=True, max_width=8)
    table.add_column("Caps", no_wrap=True, max_width=14)
    table.add_column("Score", no_wrap=True, max_width=12)
    for index, preset in enumerate(MODEL_PRESETS.values(), start=1):
        registered = REGISTERED_MODELS.get(preset.name)
        caps = registered.capabilities if registered else None
        capabilities = compact_capabilities(caps)
        score = compact_model_score(registered)
        table.add_row(
            str(index),
            preset.name,
            preset.provider,
            preset.model,
            registered.context_window if registered else "unknown",
            capabilities,
            score,
        )
    table.caption = (
        "Caps: T=tool actions, S=stream, U=usage. Score: Q quality, F speed, $ cost. "
        "Select by number/name, or enter any provider model id."
    )
    return table


def compact_capabilities(caps) -> str:
    if caps is None:
        return "T S J"
    labels = []
    if caps.json_actions:
        labels.append("T")
    if caps.streaming:
        labels.append("S")
    if caps.token_usage:
        labels.append("U")
    return " ".join(labels) if labels else "-"


def compact_model_score(model) -> str:
    if model is None:
        return "Q3 F3 $3"
    return f"Q{model.quality} F{model.speed} ${model.cost}"


def is_casual_greeting(user_input: str) -> bool:
    normalized = user_input.strip().lower()
    return normalized in {
        "hey",
        "hi",
        "hii",
        "hiii",
        "hello",
        "yo",
        "sup",
        "hiya",
        "heyy",
        "hey there",
        "hello there",
    }


def is_chat_request(user_input: str) -> bool:
    """Detect non-workspace questions that should use a lightweight chat path."""
    normalized = user_input.strip().lower()
    if is_casual_greeting(normalized):
        return True

    # Short meta-questions
    chat_patterns = [
        "what can you do",
        "who are you",
        "what are you",
        "how do you work",
        "help me",
        "what is ",
        "what are ",
        "how far ",
        "how close ",
        "how much ",
        "how long ",
        "how many ",
        "explain ",
        "define ",
        "how to ",
        "how do i ",
        "so how ",
        "are we ",
        "where are we ",
        "tell me about ",
        "what's the difference ",
        "compare ",
        "why is ",
        "why does ",
        "when was ",
        "when did ",
        "who is ",
        "who was ",
        "thank",
        "thanks",
    ]

    # If it starts with a chat pattern and doesn't mention workspace terms, it's chat
    workspace_terms = [
        "file", "workspace", "codebase", "repo", "directory",
        "create", "edit", "update", "fix", "build", "test", "run",
        "commit", "diff", "patch", "lint", "debug", "refactor",
        "add", "delete", "remove", "implement", "install",
    ]

    has_workspace_signal = any(term in normalized for term in workspace_terms)
    has_chat_signal = any(normalized.startswith(pattern) for pattern in chat_patterns)

    if has_chat_signal and not has_workspace_signal:
        return True

    return False


def should_use_lightweight_chat(
    user_input: str,
    transcript: list[tuple[str, str]],
    session_state: SessionState,
) -> bool:
    if not is_chat_request(user_input):
        return False
    if is_casual_greeting(user_input):
        return True
    if _has_workspace_session_context(transcript, session_state) and _looks_like_contextual_followup(user_input):
        return False
    return True


def _has_workspace_session_context(
    transcript: list[tuple[str, str]],
    session_state: SessionState,
) -> bool:
    if (
        session_state.current_task
        or session_state.target_files
        or session_state.last_created_files
        or session_state.last_edited_files
        or session_state.last_deleted_files
        or session_state.last_run_id is not None
    ):
        return True
    transcript_text = " ".join(part for turn in transcript[-3:] for part in turn).lower()
    return any(term in transcript_text for term in ("project", "repo", "workspace", "readme", "codebase"))


def _looks_like_contextual_followup(user_input: str) -> bool:
    tokens = re.findall(r"[a-z0-9']+", user_input.lower())
    if not tokens:
        return False

    deictic_terms = {
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "we",
        "us",
        "our",
        "ours",
        "here",
        "there",
        "current",
        "previous",
        "above",
        "last",
        "same",
    }
    followup_terms = {
        "continue",
        "summarize",
        "summarise",
        "explain",
        "expand",
        "improve",
        "compare",
        "assess",
        "evaluate",
        "review",
        "next",
        "status",
        "progress",
        "ready",
        "remaining",
        "gap",
        "gaps",
        "closer",
        "away",
    }

    has_deictic_reference = any(token in deictic_terms for token in tokens)
    has_followup_intent = any(token in followup_terms for token in tokens)
    has_question_context = tokens[0] in {"what", "why", "how", "where", "when", "should", "can", "could"}
    return has_deictic_reference or (has_question_context and has_followup_intent)


def is_persona_instruction(user_input: str) -> bool:
    normalized = " ".join(user_input.strip().lower().split())
    if not normalized:
        return False
    starters = (
        "you are ",
        "you're ",
        "act as ",
        "pretend you are ",
        "from now on ",
        "respond as ",
    )
    task_terms = (
        "create",
        "write",
        "edit",
        "update",
        "fix",
        "implement",
        "run",
        "test",
        "inspect",
        "read",
        "search",
        "build",
    )
    return normalized.startswith(starters) and not any(
        f" {term} " in f" {normalized} " for term in task_terms
    )


def read_prompt(
    session: PromptSession,
    settings: Settings | None = None,
    cwd: Path | None = None,
    model: str | None = None,
    profile: str | None = None,
) -> str:
    if settings is None:
        status_line = "Agent47"
    else:
        branch = git_branch(cwd) or (cwd.name if cwd else "unknown")
        model_label = model_display_name(current_model_name(settings, model))
        active_profile = profile or settings.agent_profile
        status_line = f"Using: {branch} branch | {model_label} | {active_profile} profile"

    message = HTML(f'<style color="gray">{status_line}</style>\n<b>&gt;</b> ')
    placeholder = HTML('<style color="gray">Type your message or /help...</style>')

    # Catch Ctrl-C (KeyboardInterrupt) specifically to map it to our custom behavior
    try:
        line = session.prompt(message, placeholder=placeholder)
        if line == CTRL_E:
            raise InteractiveExitRequested
        return line.strip()
    except KeyboardInterrupt:
        # PromptSession raises KeyboardInterrupt when user hits Ctrl-C
        # which our loop handles to skip/reset
        raise


def format_interactive_prompt(
    settings: Settings,
    cwd: Path,
    model: str | None,
    profile: str | None,
) -> str:
    branch = git_branch(cwd) or cwd.name
    model_label = model_display_name(current_model_name(settings, model))
    active_profile = profile or settings.agent_profile
    return f"agent47({branch}) [{model_label}|{active_profile}] > "


def current_model_name(settings: Settings, model_override: str | None) -> str:
    if model_override:
        return model_override
    preset = resolve_model_preset(settings.agent_model_preset)
    return preset.model if preset else settings.agent_model


def current_provider_name(settings: Settings, model_override: str | None) -> str:
    active_model = current_model_name(settings, model_override)
    inferred = inferred_provider_for_model(settings, active_model)
    if inferred:
        return inferred
    preset = resolve_model_preset(settings.agent_model_preset)
    return settings.provider_name_for(preset.provider if preset else None)


def resolve_model_choice(choice: str) -> str:
    try:
        preset = resolve_model_preset(choice)
    except ValueError:
        return choice
    return preset.model if preset else choice


def inferred_provider_for_model(settings: Settings, model: str) -> str | None:
    registered = find_registered_model(model)
    if registered:
        return registered.provider
    return None


def model_switch_error(settings: Settings, model: str, *, stream_model: bool = True) -> str | None:
    provider = inferred_provider_for_model(settings, model) or settings.provider_name
    try:
        validate_model_selection(provider=provider, model=model, stream=stream_model)
        settings.model_api_key_for(provider)
    except RuntimeError as exc:
        return str(exc)
    return None


def switch_model_or_report(
    settings: Settings,
    choice: str,
    *,
    stream_model: bool,
    env_path: Path = Path(".env"),
) -> str | None:
    model = resolve_model_choice(choice)
    error = model_switch_error(settings, model, stream_model=stream_model)
    if error:
        print_error_card(
            "Model Switch Failed",
            [("Reason:", error)],
            [
                "Set the provider API key in .env",
                "Use /models to choose a configured preset",
                "Use /model to inspect the current model",
            ],
        )
        return None
    try:
        persist_model_selection(settings, model, env_path=env_path)
    except OSError as exc:
        print_error_card(
            "Model Saved For Session Only",
            [
                ("What failed:", "The model was switched for this session, but .env could not be updated."),
                ("Reason:", str(exc)),
            ],
            ["Check file permissions", "Update .env manually if you want this model after restart"],
        )
    return model


def persist_model_selection(settings: Settings, model: str, *, env_path: Path = Path(".env")) -> None:
    registered = find_registered_model(model)
    provider = registered.provider if registered else settings.provider_name
    preset = registered.name if registered else ""
    updates = {
        "AGENT_PROVIDER": provider,
        "AGENT_MODEL_PRESET": preset,
        "AGENT_MODEL": model,
    }
    write_env_values(env_path, updates)
    settings.agent_provider = provider
    settings.agent_model_preset = preset or None
    settings.agent_model = model


def write_env_values(env_path: Path, updates: dict[str, str]) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining = dict(updates)
    output: list[str] = []

    for line in lines:
        key = _env_line_key(line)
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)

    if remaining and output and output[-1].strip():
        output.append("")
    for key, value in remaining.items():
        output.append(f"{key}={value}")

    env_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def _env_line_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        return None
    key = line.split("=", 1)[0].strip()
    return key if key else None


def model_display_name(model: str) -> str:
    registered = find_registered_model(model)
    return registered.name if registered else model.rsplit("/", 1)[-1]


def print_model_status(settings: Settings, model_override: str | None, profile: str | None) -> None:
    model_name = current_model_name(settings, model_override)
    registered = find_registered_model(model_name)
    rows: list[tuple[str, object]] = [
        ("Model", model_name),
        ("Display", model_display_name(model_name)),
        ("Provider", current_provider_name(settings, model_override)),
        ("Profile", profile or settings.agent_profile),
    ]
    if registered:
        caps = registered.capabilities
        rows.extend(
            [
                ("Context", registered.context_window),
                ("Tools", yes_no(caps.json_actions)),
                ("Reasoning", stars(registered.reasoning)),
                ("Streaming", yes_no(caps.streaming)),
                ("Quality", stars(registered.quality)),
                ("Speed", stars(registered.speed)),
                ("Cost", stars(registered.cost)),
            ]
        )
    print_key_values("Model", rows)


def stars(value: int, total: int = 5) -> str:
    clamped = max(0, min(total, value))
    return "*" * clamped + "-" * (total - clamped)


def yes_no(value: bool) -> str:
    return "yes" if value else "no"


def format_model_metadata(model) -> str:
    caps = model.capabilities
    return (
        f"context={model.context_window} tools={yes_no(caps.json_actions)} "
        f"reasoning={stars(model.reasoning)} speed={stars(model.speed)} cost={stars(model.cost)}"
    )


def agent_version() -> str:
    try:
        return version("code-agent")
    except PackageNotFoundError:
        return "0.1.0"


def git_branch(cwd: Path) -> str | None:
    git_dir = find_git_dir(cwd)
    if git_dir is None:
        return None
    head = git_dir / "HEAD"
    try:
        content = head.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    prefix = "ref: refs/heads/"
    if content.startswith(prefix):
        return content[len(prefix):]
    return content[:7] if content else None


def find_git_dir(cwd: Path) -> Path | None:
    for path in [cwd, *cwd.parents]:
        candidate = path / ".git"
        if candidate.is_dir():
            return candidate
        if candidate.is_file():
            try:
                text = candidate.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            prefix = "gitdir: "
            if text.startswith(prefix):
                return (path / text[len(prefix):]).resolve()
    return None


def run_lightweight_chat(user_input: str, client: InteractiveAgent) -> str:
    return client.complete(
        [
            {
                "role": "system",
                "content": (
                    "You are Agent47 in casual interactive chat mode. "
                    "Answer conversationally and briefly. Do not inspect files, gather workspace "
                    "context, plan code changes, or suggest tool work unless the user explicitly "
                    "asks for a workspace, code, file, terminal, git, or project task."
                ),
            },
            {"role": "user", "content": user_input},
        ]
    )


def friendly_model_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return friendly_model_error_text(f"{exc.__class__.__name__}: {message}")


def friendly_model_error_text(message: str) -> str:
    cleaned = message.strip() or "Unknown model error."
    lowered = cleaned.lower()
    if "resourceexhausted" in lowered or "request limit reached" in lowered or "rate limit" in lowered:
        return (
            "The model provider is currently capacity-limited or rate-limited. "
            f"{cleaned}"
        )
    if "insufficient credits" in lowered or "payment required" in lowered:
        return (
            "The model provider reported insufficient credits for this request. "
            f"{cleaned}"
        )
    if "api key" in lowered or "unauthorized" in lowered or "authentication" in lowered:
        return (
            "The provider rejected the configured credentials. "
            f"{cleaned}"
        )
    return cleaned


def is_model_failure_result(result) -> bool:
    return any(
        item.get("type") == "model_failure" or item.get("kind") == "model_failure"
        for item in result.failed_actions
        if isinstance(item, dict)
    )


def print_model_failure_card(result) -> None:
    reason = result.message
    for item in reversed(result.failed_actions):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "model_failure" or item.get("kind") == "model_failure":
            reason = str(item.get("output") or result.message)
            break
    print_error_card(
        "Model Request Failed",
        [
            ("What failed:", "The model request could not be completed."),
            ("Reason:", friendly_model_error_text(reason)),
        ],
        ["Retry in a moment", "Switch models with /model", "Check provider quota or credits"],
    )


def read_interactive_line(prompt: str) -> str:
    if os.name == "nt" and sys.stdin.isatty():
        return read_windows_line(prompt)
    return input(prompt)


def read_windows_line(prompt: str) -> str:
    import msvcrt

    print(prompt, end="", flush=True)
    chars: list[str] = []
    while True:
        char = msvcrt.getwch()
        if char in {"\r", "\n"}:
            print()
            return "".join(chars)
        if char == CTRL_C:
            raise KeyboardInterrupt
        if char == CTRL_E:
            raise InteractiveExitRequested
        if char in {"\b", "\x7f"}:
            if chars:
                chars.pop()
                print("\b \b", end="", flush=True)
            continue
        if char in {"\x00", "\xe0"}:
            msvcrt.getwch()
            continue
        if char < " ":
            continue
        chars.append(char)
        print(char, end="", flush=True)


@contextlib.contextmanager
def active_shortcuts(agent: InteractiveAgent):
    monitor = ActiveShortcutMonitor(agent)
    monitor.start()
    try:
        yield
    except KeyboardInterrupt as exc:
        if monitor.exit_requested:
            raise InteractiveExitRequested from exc
        raise
    finally:
        monitor.stop()


class ActiveShortcutMonitor:
    def __init__(self, agent: InteractiveAgent) -> None:
        self.agent = agent
        self.exit_requested = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._previous_sigint = None

    def start(self) -> None:
        self._previous_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._handle_sigint)
        if os.name == "nt" and sys.stdin.isatty():
            self._thread = threading.Thread(target=self._watch_windows_keys, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._previous_sigint is not None:
            signal.signal(signal.SIGINT, self._previous_sigint)

    def _handle_sigint(self, signum, frame) -> None:
        self.agent.cancel("interactive ctrl+c")
        raise KeyboardInterrupt

    def _watch_windows_keys(self) -> None:
        import msvcrt

        while not self._stop.is_set():
            if not msvcrt.kbhit():
                time.sleep(0.05)
                continue
            char = msvcrt.getwch()
            if char == CTRL_C:
                self.agent.cancel("interactive ctrl+c")
                _thread.interrupt_main()
                return
            if char == CTRL_E:
                self.exit_requested = True
                self.agent.cancel("interactive ctrl+e")
                _thread.interrupt_main()
                return


def run_interactive_turn(
    user_input: str,
    agent: InteractiveAgent,
    transcript: list[tuple[str, str]],
    session_state: SessionState,
) -> list[tuple[str, str]]:
    task = task_with_context(user_input, transcript, session_state)
    result = agent.run_detailed(task)
    if is_model_failure_result(result):
        print_model_failure_card(result)
    else:
        print_work_report_panel(result)
    if not should_show_work_report(result) and not is_model_failure_result(result):
        print_response("Agent47", result.message)
    session_state.update(user_input, result)
    transcript.append((user_input, result.message))
    return transcript[-8:]


def task_with_transcript(user_input: str, transcript: list[tuple[str, str]]) -> str:
    if not transcript:
        return user_input

    lines = [
        user_input,
        "",
        "Recent interactive transcript for reference:",
    ]
    for index, (user, assistant) in enumerate(transcript[-6:], start=1):
        lines.append(f"Turn {index} user: {user}")
        lines.append(f"Turn {index} Agent47: {assistant}")
    return "\n".join(lines)


def task_with_context(
    user_input: str,
    transcript: list[tuple[str, str]],
    session_state: SessionState,
) -> str:
    sections = [user_input]
    rendered_state = session_state.render()
    if rendered_state:
        sections.extend(["", rendered_state])
    if transcript:
        sections.extend(["", _format_transcript(transcript)])
    return "\n".join(sections)


def _format_transcript(transcript: list[tuple[str, str]]) -> str:
    lines = ["Recent interactive transcript for reference:"]
    for index, (user, assistant) in enumerate(transcript[-6:], start=1):
        lines.append(f"Turn {index} user: {user}")
        lines.append(f"Turn {index} Agent47: {assistant}")
    return "\n".join(lines)


def _handle_mcp_command(value: str, cwd: Path) -> None:
    from .platform_runtime import PlatformRuntime
    from .terminal_ui import print_error_card, print_panel, print_renderable_panel
    
    path = cwd / ".agents" / "mcp.json"
    if not path.exists():
        print_error_card("MCP Configuration Error", [("Error:", "No MCP configuration found at .agents/mcp.json")], ["Create a configuration in .agents/mcp.json"])
        return
        
    runtime = PlatformRuntime.create(cwd, trust_workspace_extensions=False)
    try:
        try:
            runtime.configure_mcp()
        except Exception as exc:
            print_error_card("MCP Configuration Error", [("Error:", str(exc))], ["Fix the configuration in .agents/mcp.json"])
            return

        command = value.lower().strip()
        if not command or command == "status":
            runtime.mcp.start_enabled()
            status_info = runtime.mcp.status()
            if not status_info:
                print_panel("MCP Status", "No servers configured.")
                return
            
            from rich.table import Table
            from rich import box
            table = Table(box=box.MINIMAL)
            table.add_column("Server", style="bold")
            table.add_column("Enabled")
            table.add_column("Connected")
            table.add_column("Status")
            
            for info in status_info:
                name = info.get("name", "Unknown")
                client = runtime.mcp.clients.get(name)
                enabled_str = "Yes" if (client and client.config.enabled) else "No"
                connected = info.get("connected")
                connected_str = "Yes" if connected else "No"
                
                if not connected:
                    health = "Not running"
                else:
                    health_data = info.get("health")
                    if isinstance(health_data, tuple) and len(health_data) == 2:
                        health = health_data[1]
                    else:
                        health = str(health_data) if health_data is not None else "Not running"
                table.add_row(name, enabled_str, connected_str, health)
            print_renderable_panel("MCP Status", table)
        elif command == "tools":
            runtime.mcp.start_enabled()
            tools = [t for t in runtime.tools.discover() if t.get("namespace", "").startswith("mcp-")]
            if not tools:
                print_panel("MCP Tools", "No MCP tools discovered.")
                return
            
            from rich.table import Table
            from rich import box
            table = Table(box=box.MINIMAL)
            table.add_column("Namespace", style="cyan")
            table.add_column("Tool", style="bold")
            table.add_column("Description")
            
            grouped = {}
            for t in tools:
                grouped.setdefault(t.get("namespace", ""), []).append(t)
                
            for ns, ns_tools in sorted(grouped.items()):
                for idx, t in enumerate(ns_tools):
                    table.add_row(
                        ns if idx == 0 else "",
                        t.get("name", ""),
                        t.get("description", "")
                    )
            print_renderable_panel("MCP Tools", table)
        elif command == "resources":
            runtime.mcp.start_enabled()
            resources = runtime.list_mcp_resources()
            if not resources or all(not items for items in resources.values()):
                print_panel("MCP Resources", "No resources available.")
                return
                
            from rich.table import Table
            table = Table(box=box.MINIMAL)
            table.add_column("Server", style="cyan")
            table.add_column("Resource", style="bold")
            table.add_column("URI")
            table.add_column("Description")
            
            for server, items in sorted(resources.items()):
                for idx, item in enumerate(items):
                    table.add_row(
                        server if idx == 0 else "",
                        item.get("name", ""),
                        item.get("uri", ""),
                        item.get("description", "")
                    )
            print_renderable_panel("MCP Resources", table)
        elif command == "prompts":
            runtime.mcp.start_enabled()
            prompts = runtime.list_mcp_prompts()
            if not prompts or all(not items for items in prompts.values()):
                print_panel("MCP Prompts", "No prompts available.")
                return
                
            from rich.table import Table
            table = Table(box=box.MINIMAL)
            table.add_column("Server", style="cyan")
            table.add_column("Prompt", style="bold")
            table.add_column("Description")
            
            for server, items in sorted(prompts.items()):
                for idx, item in enumerate(items):
                    table.add_row(
                        server if idx == 0 else "",
                        item.get("name", ""),
                        item.get("description", "")
                    )
            print_renderable_panel("MCP Prompts", table)
        else:
            print_error_card(
                "Invalid MCP Command",
                [("Unknown subcommand:", command)],
                ["Use '/mcp', '/mcp tools', '/mcp resources', or '/mcp prompts'"]
            )
    except Exception as exc:
        print_panel("MCP Error", f"Failed to execute MCP command: {exc}", style="red")
    finally:
        runtime.close()

