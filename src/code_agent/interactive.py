from __future__ import annotations

from pathlib import Path

import typer

from .config import Settings
from .factory import create_agent
from .permissions import confirm_permission
from .sandbox import create_sandbox_workspace
from .storage import AgentStorage
from .status import StatusReporter
from .terminal_ui import print_key_values, print_panel


def main() -> None:
    settings = Settings()
    base_cwd = Path.cwd().resolve()
    cwd = base_cwd
    model: str | None = None
    dry_run = True
    sandbox_enabled = False
    max_steps = 12
    max_failures: int | None = None
    transcript: list[tuple[str, str]] = []

    print_panel(
        "Agent47",
        (
            "Interactive coding agent\n"
            "Type a task or question. Use /help for commands. Use /stop or Ctrl+C to quit.\n\n"
            f"Workspace: {cwd}\n"
            f"Mode: {'dry-run' if dry_run else 'write-enabled'}"
        ),
    )

    while True:
        try:
            typer.echo("")
            user_input = typer.prompt("agent47 >").strip()
        except (EOFError, KeyboardInterrupt):
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
            print_panel("You", user_input)
            task = task_with_transcript(user_input, transcript)
            result = agent.run(task)
            print_panel("Agent47", result)
            transcript.append((user_input, result))
            transcript = transcript[-8:]
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


def is_casual_greeting(user_input: str) -> bool:
    normalized = user_input.strip().lower()
    return normalized in {"hey", "hi", "hello", "yo", "sup", "hiya"}


def task_with_transcript(user_input: str, transcript: list[tuple[str, str]]) -> str:
    if not transcript or not should_include_transcript(user_input):
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


def should_include_transcript(user_input: str) -> bool:
    normalized = user_input.lower()
    return any(
        phrase in normalized
        for phrase in [
            "summarise your response",
            "summarize your response",
            "summarise your responses",
            "summarize your responses",
            "what did you say",
            "previous response",
            "previous responses",
            "our conversation",
            "this conversation",
            "recap",
        ]
    )
