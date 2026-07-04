import pytest
from typer._click.exceptions import Abort

import code_agent.interactive as interactive
from code_agent.agent import AgentRunResult
from code_agent.interactive import DEFAULT_DRY_RUN, read_prompt
from code_agent.interactive import format_model_selection_preview, is_persona_instruction
from code_agent.config import Settings
from code_agent.work_report import should_show_work_report
from code_agent.terminal_ui import SessionHeader, format_status_line, print_session_header, print_work_report_panel, console
from code_agent.status import StatusReporter


def test_format_work_report_body_produces_rich_panel() -> None:
    result = AgentRunResult(
        message="Updated docs and verified tests.",
        run_id=7,
        task="update the docs",
        changed_paths=["docs/PROGRESS.md"],
        mutation_records=[
            {
                "action": "edit_file",
                "path": "docs/PROGRESS.md",
                "ok": True,
            }
        ],
        verification_results=[
            {"purpose": "test", "command": "uv run pytest", "ok": True, "status": "passed"}
        ],
    )

    with console.capture() as capture:
        print_work_report_panel(result)

    text = capture.get()
    
    assert "Finished Work" in text
    assert "update the docs" not in text
    assert "Modified:" not in text
    assert "docs/PROGRESS.md" in text
    assert "uv run pytest" in text
    assert "Passed" in text


def test_should_show_work_report_stays_quiet_for_simple_chat() -> None:
    assert not should_show_work_report(AgentRunResult(message="hello", run_id=1))


def test_interactive_mode_starts_write_enabled() -> None:
    assert DEFAULT_DRY_RUN is False


def test_read_prompt_propagates_click_abort(monkeypatch) -> None:
    monkeypatch.setattr(interactive.typer, "echo", lambda *_args, **_kwargs: None)

    def abort_input(_prompt):
        raise Abort()

    monkeypatch.setattr("builtins.input", abort_input)

    with pytest.raises(Abort):
        read_prompt()


def test_model_selection_preview_lists_presets_and_shortcut_note() -> None:
    preview = format_model_selection_preview("current-model")

    assert "Current model: current-model" in preview
    assert "qwen-coder" in preview
    assert "gemini-flash" in preview
    assert "Ctrl+M" in preview


def test_prompt_model_selection_accepts_number(monkeypatch) -> None:
    monkeypatch.setattr(interactive.Prompt, "ask", lambda *_args, **_kwargs: "2")

    selected = interactive.prompt_model_selection("current-model")

    assert selected == "gemini-3.5-flash"


def test_prompt_model_selection_accepts_preset_name(monkeypatch) -> None:
    monkeypatch.setattr(interactive.Prompt, "ask", lambda *_args, **_kwargs: "deepseek-pro")

    selected = interactive.prompt_model_selection("current-model")

    assert selected == "deepseek-v4-pro"


def test_current_model_name_uses_preset_or_override() -> None:
    settings = Settings(_env_file=None, agent_model_preset="glm-5.2", agent_model="fallback")

    assert interactive.current_model_name(settings, None) == "z-ai/glm-5.2"
    assert interactive.current_model_name(settings, "custom-model") == "custom-model"
    assert interactive.current_provider_name(settings, None) == "nvidia"
    assert interactive.model_display_name("z-ai/glm-5.2") == "glm-5.2"


def test_interactive_prompt_contains_repo_model_and_profile(tmp_path) -> None:
    settings = Settings(_env_file=None, agent_model_preset="glm-5.2", agent_profile="coder")

    prompt = interactive.format_interactive_prompt(settings, tmp_path, None, None)

    assert prompt.startswith(f"agent47({tmp_path.name})")
    assert "[glm-5.2|coder]" in prompt


def test_model_command_without_value_reports_current_model(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        agent_model_preset="glm-5.2",
        agent_model="fallback",
        agent_db_path=tmp_path / "agent.db",
    )

    state = interactive.handle_command(
        "/model",
        settings,
        tmp_path,
        tmp_path,
        model=None,
        profile=None,
        dry_run=False,
        stream_model=True,
        sandbox_enabled=False,
        max_steps=12,
        max_failures=None,
    )

    assert state.model is None


def test_model_selection_table_shows_capabilities() -> None:
    table = interactive.build_model_selection_table("z-ai/glm-5.2")

    columns = [column.header for column in table.columns]
    assert "Context" in columns
    assert "Tools" in columns
    assert "Quality" in columns
    assert "Speed" in columns
    assert "Cost" in columns


def test_stop_command_requests_exit_between_turns(tmp_path) -> None:
    settings = Settings(_env_file=None, agent_db_path=tmp_path / "agent.db")

    state = interactive.handle_command(
        "/stop",
        settings,
        tmp_path,
        tmp_path,
        model=None,
        profile=None,
        dry_run=False,
        stream_model=True,
        sandbox_enabled=False,
        max_steps=12,
        max_failures=None,
    )

    assert state.exit_requested is True


def test_read_interactive_line_uses_plain_input_when_not_tty(monkeypatch) -> None:
    monkeypatch.setattr(interactive.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt: f"typed after {prompt}")

    assert interactive.read_interactive_line("prompt> ") == "typed after prompt> "


def test_steer_command_updates_session_state(tmp_path) -> None:
    settings = Settings(_env_file=None, agent_provider="openrouter", agent_db_path=tmp_path / "agent.db")
    session_state = interactive.SessionState()

    interactive.handle_command(
        "/steer be concise and ask before broad refactors",
        settings,
        tmp_path,
        tmp_path,
        model=None,
        profile=None,
        dry_run=False,
        stream_model=True,
        sandbox_enabled=False,
        max_steps=12,
        max_failures=None,
        session_state=session_state,
    )

    assert session_state.conversation_steering == "be concise and ask before broad refactors"

    interactive.handle_command(
        "/steer clear",
        settings,
        tmp_path,
        tmp_path,
        model=None,
        profile=None,
        dry_run=False,
        stream_model=True,
        sandbox_enabled=False,
        max_steps=12,
        max_failures=None,
        session_state=session_state,
    )

    assert session_state.conversation_steering is None


def test_lightweight_chat_prompt_avoids_workspace_work() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.messages = []

        def complete(self, messages):
            self.messages = messages
            return "hi"

    client = FakeClient()

    assert interactive.run_lightweight_chat("hii", client) == "hi"
    assert "Do not inspect files" in client.messages[0]["content"]
    assert client.messages[1] == {"role": "user", "content": "hii"}


def test_status_line_and_header_are_compact() -> None:
    line = format_status_line(
        model="glm-5.2",
        provider="nvidia",
        approval="auto_read",
        sandbox=True,
        git_status="Clean",
        tokens="8.4k",
        cost="$0.02",
        elapsed="14s",
    )

    assert "glm-5.2 | nvidia | 8.4k tokens | $0.02 | 14s | Clean" in line
    assert "Sandbox ON" in line

    with console.capture() as capture:
        print_session_header(
            SessionHeader(
                version="0.1.0",
                profile="coder",
                model="glm-5.2",
                provider="nvidia",
                workspace="repo",
                sandbox="enabled",
                approval="auto_read",
                git_branch="main",
            ),
            mode="write-enabled",
        )

    rendered = capture.get()
    assert "Agent47 v0.1.0" in rendered
    assert "glm-5.2" in rendered
    assert "Git main" in rendered


def test_status_reporter_uses_single_thinking_spinner(monkeypatch) -> None:
    class FakeLive:
        def __init__(self, *args, **kwargs) -> None:
            self.renderable = None

        def start(self) -> None:
            pass

        def update(self, renderable) -> None:
            self.renderable = renderable

        def stop(self) -> None:
            pass

    monkeypatch.setattr("code_agent.status.Live", FakeLive)
    reporter = StatusReporter()

    reporter.thinking(1)
    with console.capture() as capture:
        console.print(reporter._render())
    rendered = capture.get()

    assert "Thinking" in rendered
    assert "Generating output" not in rendered
    assert "Gathering context" not in rendered


def test_is_persona_instruction() -> None:
    assert is_persona_instruction("You are a senior frontend engineer")
    assert is_persona_instruction("Act as a concise reviewer")
    assert not is_persona_instruction("You are a senior frontend engineer; update the app")
