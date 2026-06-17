import pytest
from typer._click.exceptions import Abort

import code_agent.interactive as interactive
from code_agent.agent import AgentRunResult
from code_agent.interactive import DEFAULT_DRY_RUN, read_prompt, run_interactive_turn
from code_agent.session import SessionState
from code_agent.terminal_ui import (
    format_agent_banner,
    format_key_values,
    format_panel,
    format_prompt_footer,
    format_prompt_header,
)


def test_format_panel_makes_labeled_box() -> None:
    panel = format_panel("Agent47", "hello\nworld", width=32)

    assert panel.splitlines()[0] == "+ AGENT47 ---------------------+"
    assert "| hello                        |" in panel
    assert "| world                        |" in panel
    assert panel.splitlines()[-1] == "+------------------------------+"


def test_format_agent_banner_centers_title() -> None:
    banner = format_agent_banner(width=32)

    assert banner.splitlines() == [
        "+------------------------------+",
        "|        A G E N T 4 7         |",
        "+------------------------------+",
    ]


def test_format_panel_wraps_long_lines() -> None:
    panel = format_panel("You", "one two three four five six", width=24)

    assert "| one two three four   |" in panel
    assert "| five six             |" in panel


def test_format_key_values_uses_panel_body() -> None:
    panel = format_key_values("Status", [("Mode", "dry-run"), ("Sandbox", "off")], width=34)

    assert "| Mode: dry-run                  |" in panel
    assert "| Sandbox: off                   |" in panel


def test_format_prompt_border_parts() -> None:
    assert format_prompt_header("You", width=24) == "+ YOU -----------------+"
    assert format_prompt_footer(width=24) == "+----------------------+"


def test_interactive_mode_starts_write_enabled() -> None:
    assert DEFAULT_DRY_RUN is False


def test_read_prompt_propagates_click_abort(monkeypatch) -> None:
    monkeypatch.setattr(interactive.typer, "echo", lambda *_args, **_kwargs: None)

    def abort_input(_prompt):
        raise Abort()

    monkeypatch.setattr("builtins.input", abort_input)

    with pytest.raises(Abort):
        read_prompt()


def test_read_prompt_uses_bordered_input(monkeypatch) -> None:
    echoed: list[str] = []
    monkeypatch.setattr(interactive.typer, "echo", lambda value="", **_kwargs: echoed.append(value))
    monkeypatch.setattr("builtins.input", lambda prompt: "hello there")

    assert read_prompt() == "hello there"
    assert echoed[1] == format_prompt_header("You")
    assert echoed[2] == format_prompt_footer()


def test_interactive_turn_does_not_print_user_panel(monkeypatch) -> None:
    printed: list[tuple[str, str]] = []
    monkeypatch.setattr(interactive, "print_panel", lambda title, body: printed.append((title, body)))

    class Agent:
        def run_detailed(self, _task: str) -> AgentRunResult:
            return AgentRunResult(message="done", run_id=1)

    transcript = run_interactive_turn("make a file", Agent(), [], SessionState())

    assert printed == [("Agent47", "done")]
    assert transcript == [("make a file", "done")]
