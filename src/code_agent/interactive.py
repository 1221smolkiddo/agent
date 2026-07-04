from __future__ import annotations

import _thread
import contextlib
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
import signal
import sys
import threading
import time
from typing import Protocol

import typer  # noqa: F401
from rich.prompt import Prompt
from rich.table import Table
from typer._click.exceptions import Abort

from .config import Settings
from .factory import create_agent, create_chat_client
from .model_profiles import validate_profile_name
from .model_presets import MODEL_PRESETS, resolve_model_preset
from .model_registry import REGISTERED_MODELS, find_registered_model, validate_model_selection
from .permissions import ApprovalMode, PermissionPolicy, confirm_permission
from .resume import build_resume_task, format_run_detail
from .revert import apply_revert_plan, build_revert_plan, format_revert_preview
from .sandbox import (
    create_sandbox_workspace,
    diff_sandbox_workspace,
    format_sandbox_diff,
    promote_sandbox_changes,
)
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
    format_status_line,
)
from rich.text import Text
from .work_report import should_show_work_report

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
    settings = Settings()
    base_cwd = Path.cwd().resolve()
    cwd = base_cwd
    model: str | None = None
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
    console.print(f"[bold cyan]WORKSPACE[/bold cyan]   {workspace_summary.splitlines()[0]}")
    for line in workspace_summary.splitlines()[1:]:
        console.print(line)
    console.print()

    while True:
        try:
            user_input = read_prompt(settings, cwd, model, profile)
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
        if is_chat_request(user_input):
            client = create_chat_client(
                settings=settings,
                model=model,
                profile=profile,
                stream_model=stream_model,
            )
            reporter = StatusReporter()
            reporter.thinking(1)
            try:
                with active_shortcuts(client):
                    response = run_lightweight_chat(user_input, client)
                reporter.done()
            except InteractiveExitRequested:
                reporter.done()
                client.cancel("interactive exit shortcut")
                print_panel("System", "bye")
                return
            except KeyboardInterrupt:
                reporter.done()
                client.cancel("interactive keyboard interrupt")
                print_panel("Stopped", "Current action stopped. Interactive session is still open.")
                continue
            except Exception as exc:
                reporter.done()
                print_error_card(
                    "Error",
                    [
                        ("Model request could not be completed.", ""),
                        ("Reason:", str(exc)),
                    ],
                    ["Check your credentials", "Use /debug for more info"]
                )
                continue
            print_response("Agent47", response)
            transcript.append((user_input, response))
            transcript = transcript[-8:]
            continue

        permission_policy.reset_task()
        agent = create_agent(
            settings=settings,
            cwd=cwd,
            model=model,
            profile=profile,
            dry_run=dry_run,
            max_steps=max_steps,
            max_failures=max_failures,
            approval_callback=permission_policy.approve,
            reporter=StatusReporter(),
            stream_model=stream_model,
        )
        try:
            with active_shortcuts(agent):
                transcript = run_interactive_turn(user_input, agent, transcript, session_state)
        except InteractiveExitRequested:
            agent.cancel("interactive exit shortcut")
            print_panel("System", "bye")
            return
        except KeyboardInterrupt:
            agent.cancel("interactive keyboard interrupt")
            print_panel("Stopped", "Current action stopped. Interactive session is still open.")
            continue
        except Exception as exc:
            import traceback
            session_state._last_exc = traceback.format_exc()
            print_error_card(
                "Error",
                [
                    ("Task execution could not be completed.", ""),
                    ("Reason:", str(exc)),
                ],
                ["Use /debug to see the full stack trace", "Check model configurations"]
            )


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
        print_help()
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
            sandbox_workspace = create_sandbox_workspace(base_cwd)
            cwd = sandbox_workspace.path
            sandbox_enabled = True
            print_key_values("Sandbox", [("Sandbox", "on"), ("Workspace", cwd)])
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
            session_state=session_state,
        )
    elif command in {"/revert", "/restore"}:
        run_revert_command(settings, value, session_state=session_state)
    elif command == "/history-show":
        print_history_detail(settings, value)
    elif command == "/status":
        print_key_values(
            "Status",
            [
                ("Workspace", cwd),
                ("Mode", "dry-run" if dry_run else "write-enabled"),
                ("Current model", current_model_name(settings, model)),
                ("Provider", current_provider_name(settings, model)),
                ("Profile", profile or settings.agent_profile),
                ("Git", git_branch(cwd) or "none"),
                ("Approvals", permission_policy.mode.value if permission_policy else "per_action"),
                (
                    "Status",
                    format_status_line(
                        model=current_model_name(settings, model),
                        provider=current_provider_name(settings, model),
                        approval=permission_policy.mode.value if permission_policy else "per_action",
                        sandbox=sandbox_enabled,
                        git_status="branch " + git_branch(cwd) if git_branch(cwd) else "no git",
                    ),
                ),
            ],
        )
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
        print_panel("Unknown Command", f"{command}\nUse /help to see available commands.", style="red")

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
    table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    table.add_column("Command", style="cyan", no_wrap=True)
    table.add_column("Use", overflow="fold")
    for command, description in [
        ("/help", "Show this help."),
        ("/status", "Show workspace, model, mode, and approvals."),
        ("/model", "Show the current model and capabilities."),
        ("/models", "Preview model presets and select one."),
        ("/model select", "Open the model picker."),
        ("/model <name>", "Change model for this session."),
        ("/profile <name>", "Change model profile: default, planner, coder, reviewer, or fast."),
        ("/dry-run", "Inspect only; skip writes and shell commands."),
        ("/write", "Allow writes and shell commands."),
        ("/stream [off]", "Turn compact model streaming progress on or off."),
        ("/cwd <path>", "Change workspace."),
        ("/sandbox [off]", "Create and use a sandbox copy, or turn it off."),
        ("/sandbox diff", "Show changes between the sandbox and base workspace."),
        ("/sandbox apply", "Promote sandbox changes back to the base workspace."),
        ("/approve-all", "Approve all actions for each task after first approval."),
        ("/auto-read", "Auto-approve read-only operations."),
        ("/per-action", "Require individual approval for every action."),
        ("/history", "Show recent saved agent runs."),
        ("/history-show <id>", "Show saved steps for one run."),
        ("/resume <id> [msg]", "Resume a saved run with optional extra instruction."),
        ("/restore <id|last>", "Restore verified file changes from a prior run."),
        ("/revert <id|last>", "Alias for restore."),
        ("/diff", "Show where to find detailed diff output."),
        ("/report", "Show where detailed reports are saved."),
        ("/files", "Show current workspace path."),
        ("/settings", "Show current session settings."),
        ("/steer <guidance>", "Steer future turns with style, focus, or constraints."),
        ("/steer clear", "Clear conversation steering."),
        ("/debug", "Show stack trace of the last error."),
        ("Ctrl+C", "Stop the active model/tool turn and return to the prompt."),
        ("Ctrl+E", "Exit the interactive agent."),
        ("/stop", "Quit interactive mode between turns."),
    ]:
        table.add_row(command, description)
    print_renderable_panel("Help", table, style="cyan")


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

    task = build_resume_task(
        run_row,
        storage.run_steps_payloads(run_id),
        instruction.strip() or None,
        storage.get_work_report(run_id),
    )
    agent = create_agent(
        settings=settings,
        cwd=cwd,
        model=model,
        profile=profile,
        dry_run=dry_run,
        max_steps=max_steps,
        max_failures=max_failures,
        approval_callback=confirm_permission,
        reporter=StatusReporter(),
        stream_model=stream_model,
    )
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
        padding=(0, 2),
        title=Text(f"Current model: {current_model}", style="muted"),
        title_justify="left",
    )
    table.add_column("#", justify="right", style="cyan", no_wrap=True)
    table.add_column("Preset", style="bold")
    table.add_column("Provider", style="muted", no_wrap=True)
    table.add_column("Model", style="cyan", overflow="fold")
    table.add_column("Context", no_wrap=True)
    table.add_column("Tools", no_wrap=True)
    table.add_column("Stream", no_wrap=True)
    table.add_column("Quality", no_wrap=True)
    table.add_column("Speed", no_wrap=True)
    table.add_column("Cost", no_wrap=True)
    table.add_column("Best for", overflow="fold")
    for index, preset in enumerate(MODEL_PRESETS.values(), start=1):
        registered = REGISTERED_MODELS.get(preset.name)
        caps = registered.capabilities if registered else None
        table.add_row(
            str(index),
            preset.name,
            preset.provider,
            preset.model,
            registered.context_window if registered else "unknown",
            yes_no(caps.json_actions if caps else True),
            yes_no(caps.streaming if caps else True),
            stars(registered.quality if registered else 3),
            stars(registered.speed if registered else 3),
            stars(registered.cost if registered else 3),
            preset.description,
        )
    table.caption = (
        "Select by number/name, or enter any provider model id. "
        "Most terminals encode Ctrl+M as Enter; use /models for the picker."
    )
    return table


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
    settings: Settings | None = None,
    cwd: Path | None = None,
    model: str | None = None,
    profile: str | None = None,
) -> str:
    if settings is None:
        prompt = "agent47 > "
    else:
        prompt = format_interactive_prompt(settings, cwd or Path.cwd(), model, profile)
    return read_interactive_line(prompt).strip()


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
    return model


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
    print_work_report_panel(result)
    if not should_show_work_report(result):
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
