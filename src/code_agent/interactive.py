from __future__ import annotations

from pathlib import Path
from typing import Protocol

import typer  # noqa: F401
from typer._click.exceptions import Abort

from .config import Settings
from .factory import create_agent
from .model_profiles import validate_profile_name
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
    print_startup_header,
    print_work_report_panel,
    print_response,
)
from rich.text import Text
from .work_report import should_show_work_report

DEFAULT_DRY_RUN = False


class InteractiveAgent(Protocol):
    def run_detailed(self, task: str): ...


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

    print_startup_header(cwd, "write-enabled" if not dry_run else "dry-run", model or settings.agent_model)
    workspace_summary = analyze_workspace(cwd)
    console.print(f"[bold cyan]WORKSPACE[/bold cyan]   {workspace_summary.splitlines()[0]}")
    for line in workspace_summary.splitlines()[1:]:
        console.print(line)
    console.print()

    while True:
        try:
            user_input = read_prompt()
        except (EOFError, KeyboardInterrupt, Abort):
            print_panel("System", "bye")
            return

        if not user_input:
            continue

        if is_casual_greeting(user_input):
            print_response("Agent47", "Hey! I am ready. Ask me a question, or use /help to see commands.")
            continue
        if is_persona_instruction(user_input):
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
            agent = create_agent(
                settings=settings,
                cwd=cwd,
                model=model,
                profile=profile,
                dry_run=dry_run,
                max_steps=1,
                max_failures=1,
                approval_callback=permission_policy.approve,
                reporter=None,
                stream_model=stream_model,
            )
            try:
                response = agent.run(user_input)
            except KeyboardInterrupt:
                print_panel("System", "STOPPED by user")
                return
            except Exception as exc:
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
            transcript = run_interactive_turn(user_input, agent, transcript, session_state)
        except KeyboardInterrupt:
            print_panel("System", "STOPPED by user")
            return
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
        if value:
            model = value
        print_panel("Model", model or settings.agent_model)
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
    elif command == "/revert":
        run_revert_command(settings, value)
    elif command == "/history-show":
        print_history_detail(settings, value)
    elif command == "/status":
        print_key_values(
            "Status",
            [
                ("Workspace", cwd),
                ("Mode", "dry-run" if dry_run else "write-enabled"),
                ("Current model", model or settings.agent_model),
                ("Approvals", permission_policy.mode.value if permission_policy else "per_action"),
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
        print_panel("Models", f"Current model: {model or settings.agent_model}")
    elif command == "/settings":
        print_key_values(
            "Settings",
            [
                ("Mode", "dry-run" if dry_run else "write-enabled"),
                ("Approvals", permission_policy.mode.value if permission_policy else "per_action"),
                ("Streaming", "on" if stream_model else "off"),
                ("Sandbox", "on" if sandbox_enabled else "off"),
            ],
        )
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
    print_panel(
        "Help",
        """
Commands:
  /help              Show this help.
  /status            Show current workspace, model, mode, and approvals.
  /dry-run           Inspect only; skip writes and shell commands.
  /write             Allow writes and shell commands.
  /stream [off]      Turn compact model streaming progress on or off.
  /cwd <path>        Change workspace.
  /sandbox [off]     Create and use a sandbox copy, or turn it off.
  /sandbox diff      Show changes between the sandbox and base workspace.
  /sandbox apply     Promote sandbox changes back to the base workspace.
  /model <name>      Change model for this session.
  /profile <name>    Change model profile: default, planner, coder, reviewer, or fast.
  /max-steps <n>     Change max agent loop steps.
  /max-failures <n>  Change consecutive failure recovery budget.
  /approve-all       Approve all actions for each task after first approval.
  /auto-read         Auto-approve read-only operations.
  /per-action        Require individual approval for every action.
  /history           Show recent saved agent runs.
  /history-show <id> Show saved steps for one run.
  /diff             Show where to find detailed diff output.
  /resume <id> [msg] Resume a saved run with optional extra instruction.
  /revert <id>       Revert verified file changes from a prior run.
  /clear             Clear the terminal screen.
  /report            Information about detailed reports.
  /files             Show current workspace path.
  /models            Show current model.
  /settings          Show current session settings.
  /debug             Show stack trace of the last error.
  /stop              Quit.
  /exit              Quit.
""".strip()
    )


def print_history(settings: Settings) -> None:
    storage = AgentStorage(settings.agent_db_path)
    rows = storage.recent_runs(10)
    if not rows:
        print_panel("History", "No runs recorded yet.")
        return
    print_panel(
        "History",
        "\n".join(f"{row['id']} | {row['created_at']} | {row['model']} | {row['task']}" for row in rows),
    )


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


def run_revert_command(settings: Settings, value: str) -> None:
    if not value:
        print_panel("Revert", "Usage: /revert <run-id>")
        return
    try:
        run_id = int(value)
    except ValueError:
        print_panel("Revert", f"Invalid run id: {value}")
        return
    storage = AgentStorage(settings.agent_db_path)
    try:
        plan = build_revert_plan(storage, run_id)
    except ValueError as exc:
        print_panel("Revert", str(exc))
        return
    print_panel("Revert Preview", format_revert_preview(plan))
    result = apply_revert_plan(storage, plan, approval_callback=confirm_permission)
    body = result.output + f"\nRevert run id: {result.run_id}"
    print_panel("Revert", body)


def is_casual_greeting(user_input: str) -> bool:
    normalized = user_input.strip().lower()
    return normalized in {"hey", "hi", "hello", "yo", "sup", "hiya"}


def is_chat_request(user_input: str) -> bool:
    """Detect non-workspace questions that should use a lightweight chat path."""
    normalized = user_input.strip().lower()

    # Short meta-questions
    chat_patterns = [
        "what can you do",
        "who are you",
        "what are you",
        "how do you work",
        "help me",
        "what is ",
        "what are ",
        "explain ",
        "define ",
        "how to ",
        "how do i ",
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
        "file", "project", "workspace", "codebase", "repo", "directory",
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


def read_prompt() -> str:
    console.print(Text("You", style="bold green"), end=" ")
    value = input("| ").strip()
    return value


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
