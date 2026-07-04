import pytest
from typer._click.exceptions import Abort

import code_agent.interactive as interactive
from code_agent.agent import AgentRunResult
from code_agent.interactive import DEFAULT_DRY_RUN, read_prompt
from code_agent.interactive import format_model_selection_preview, is_persona_instruction
from code_agent.config import Settings
from code_agent.work_report import should_show_work_report
from code_agent.terminal_ui import print_work_report_panel, console


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


def test_force_stop_commands_request_exit(tmp_path) -> None:
    settings = Settings(_env_file=None, agent_db_path=tmp_path / "agent.db")

    for raw in ["/x", "/force-stop", "/force-exit"]:
        state = interactive.handle_command(
            raw,
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


def test_plain_x_is_documented_stop_shortcut(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "x")

    assert read_prompt() == "x"


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


def test_is_persona_instruction() -> None:
    assert is_persona_instruction("You are a senior frontend engineer")
    assert is_persona_instruction("Act as a concise reviewer")
    assert not is_persona_instruction("You are a senior frontend engineer; update the app")
