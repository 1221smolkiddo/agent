from __future__ import annotations

from pathlib import Path
from typing import Protocol

import typer
from typer._click.exceptions import Abort

from .config import Settings
from .factory import create_agent
from .permissions import confirm_permission
from .resume import build_resume_task, format_run_detail
from .sandbox import create_sandbox_workspace
from .session import SessionState
from .storage import AgentStorage
from .status import StatusReporter
from .terminal_ui import (
    colorize_panel,
    format_prompt_footer,
    format_prompt_header,
    print_agent_banner,
    print_key_values,
    print_panel,
    print_work_report_panel,
)

DEFAULT_DRY_RUN = False


class InteractiveAgent(Protocol):
    def run_detailed(self, task: str): ...


def main() -> None:
    settings = Settings()
    base_cwd = Path.cwd().resolve()
    cwd = base_cwd
    model: str | None = None
    dry_run = DEFAULT_DRY_RUN
    sandbox_enabled = False
    max_steps = 12
    max_failures: int | None = None
    transcript: list[tuple[str, str]] = []
    session_state = SessionState()

    print_agent_banner()
    print_panel(
        "Status",
        (
            "Interactive coding agent\n"
            "Type a task or question. Use /help for commands. Use /stop or Ctrl+C to quit.\n\n"
            f"Workspace: {cwd}\n"
            f"Mode: {'dry-run' if dry_run else 'write-enabled'}"
        ),
    )

    while True:
        try:
            user_input = read_prompt()
        except (EOFError, KeyboardInterrupt, Abort):
            print_panel("System", "bye")
            return

        if not user_input:
            continue

        if is_casual_greeting(user_input):
            print_panel("Agent47", "Hey! I am ready. Ask me a question, or use /help to see commands.")
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
                session_state,
            )
            if command_result.exit_requested:
                print_panel("System", "bye")
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
            reporter=StatusReporter(),
        )
        try:
            transcript = run_interactive_turn(user_input, agent, transcript, session_state)
        except KeyboardInterrupt:
            print_panel("System", "STOPPED by user")
            return


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
    session_state: SessionState | None = None,
) -> CommandState:
    parts = raw.split(maxsplit=1)
    command = parts[0].lower()
    value = parts[1].strip() if len(parts) > 1 else ""

    if command in {"/exit", "/quit", "/q", "/stop"}:
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
        print_panel("Mode", "dry-run")
    elif command == "/write":
        dry_run = False
        print_panel("Mode", "write-enabled")
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
        else:
            sandbox_workspace = create_sandbox_workspace(base_cwd)
            cwd = sandbox_workspace.path
            sandbox_enabled = True
            print_key_values("Sandbox", [("Sandbox", "on"), ("Workspace", cwd)])
    elif command == "/model":
        if value:
            model = value
        print_panel("Model", model or settings.agent_model)
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
            dry_run=dry_run,
            max_steps=max_steps,
            max_failures=max_failures,
            session_state=session_state,
        )
    elif command == "/history-show":
        print_history_detail(settings, value)
    elif command == "/status":
        print_key_values(
            "Status",
            [
                ("Base workspace", base_cwd),
                ("Workspace", cwd),
                ("Model", model or settings.agent_model),
                ("Mode", "dry-run" if dry_run else "write-enabled"),
                ("Sandbox", "on" if sandbox_enabled else "off"),
                ("Max steps", max_steps),
                ("Max failures", max_failures or settings.agent_max_failures),
                ("Session", session_state.render() if session_state else "<unavailable>"),
            ],
        )
    else:
        print_panel("Unknown Command", f"{command}\nUse /help to see available commands.")

    return CommandState(base_cwd, cwd, model, dry_run, sandbox_enabled, max_steps, max_failures)


def print_help() -> None:
    print_panel(
        "Help",
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
  /history-show <id> Show saved steps for one run.
  /resume <id> [msg] Resume a saved run with optional extra instruction.
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
    dry_run: bool,
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
        dry_run=dry_run,
        max_steps=max_steps,
        max_failures=max_failures,
        approval_callback=confirm_permission,
        reporter=StatusReporter(),
    )
    result = agent.run_detailed(task)
    print_work_report_panel(result)
    print_panel("Agent47", result.message)
    if session_state is not None:
        session_state.update(f"resume run {run_id}", result)


def is_casual_greeting(user_input: str) -> bool:
    normalized = user_input.strip().lower()
    return normalized in {"hey", "hi", "hello", "yo", "sup", "hiya"}


def read_prompt() -> str:
    typer.echo("")
    typer.echo(colorize_panel(format_prompt_header("You"), "You"))
    value = input("| ").strip()
    typer.echo(colorize_panel(format_prompt_footer(), "You"))
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
    print_panel("Agent47", result.message)
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
