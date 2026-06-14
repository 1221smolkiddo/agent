import pytest
from typer._click.exceptions import Abort

import code_agent.interactive as interactive
from code_agent.interactive import DEFAULT_DRY_RUN, read_prompt
from code_agent.terminal_ui import format_key_values, format_panel


def test_format_panel_makes_labeled_box() -> None:
    panel = format_panel("Agent47", "hello\nworld", width=32)

    assert panel.splitlines()[0] == "+ AGENT47 ---------------------+"
    assert "| hello                        |" in panel
    assert "| world                        |" in panel
    assert panel.splitlines()[-1] == "+------------------------------+"


def test_format_panel_wraps_long_lines() -> None:
    panel = format_panel("You", "one two three four five six", width=24)

    assert "| one two three four   |" in panel
    assert "| five six             |" in panel


def test_format_key_values_uses_panel_body() -> None:
    panel = format_key_values("Status", [("Mode", "dry-run"), ("Sandbox", "off")], width=34)

    assert "| Mode: dry-run                  |" in panel
    assert "| Sandbox: off                   |" in panel


def test_interactive_mode_starts_write_enabled() -> None:
    assert DEFAULT_DRY_RUN is False


def test_read_prompt_propagates_click_abort(monkeypatch) -> None:
    monkeypatch.setattr(interactive.typer, "echo", lambda *_args, **_kwargs: None)

    def abort_prompt(_prompt):
        raise Abort()

    monkeypatch.setattr(interactive.typer, "prompt", abort_prompt)

    with pytest.raises(Abort):
        read_prompt()
