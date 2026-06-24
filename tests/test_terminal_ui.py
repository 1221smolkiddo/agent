import pytest
from typer._click.exceptions import Abort

import code_agent.interactive as interactive
from code_agent.agent import AgentRunResult
from code_agent.interactive import DEFAULT_DRY_RUN, read_prompt, run_interactive_turn
from code_agent.interactive import is_persona_instruction
from code_agent.session import SessionState
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
    
    assert "update the docs" in text
    assert "Modified:" in text
    assert "docs/PROGRESS.md" in text
    assert "Verification:" in text
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


def test_is_persona_instruction() -> None:
    assert is_persona_instruction("You are a senior frontend engineer")
    assert is_persona_instruction("Act as a concise reviewer")
    assert not is_persona_instruction("You are a senior frontend engineer; update the app")
