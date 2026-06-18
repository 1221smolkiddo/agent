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
    format_plan_body,
    format_plan_panel,
    format_prompt_footer,
    format_prompt_header,
    print_stream_end,
    print_stream_marker,
    print_stream_start,
)
from code_agent.work_report import format_work_report_body, should_show_work_report


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


def test_format_plan_body_uses_latest_plan_update() -> None:
    body = format_plan_body(
        [
            {
                "steps": [
                    {"step": "Old step", "status": "in_progress"},
                ]
            },
            {
                "steps": [
                    {"step": "Inspect docs", "status": "completed"},
                    {"step": "Render plan panel", "status": "in_progress"},
                    {"step": "Handle blocker", "status": "blocked", "note": "needs approval"},
                    {"step": "Update docs", "status": "pending"},
                ]
            },
        ]
    )

    assert body == (
        "[x] 1. Inspect docs\n"
        "[>] 2. Render plan panel\n"
        "[!] 3. Handle blocker (needs approval)\n"
        "[ ] 4. Update docs"
    )


def test_format_plan_panel_labels_plan() -> None:
    panel = format_plan_panel(
        [{"steps": [{"step": "Render visible progress", "status": "in_progress"}]}],
        width=40,
    )

    assert panel.splitlines()[0] == "+ PLAN --------------------------------+"
    assert "| [>] 1. Render visible progress       |" in panel


def test_format_work_report_body_uses_requested_sections_and_changed_diff_lines() -> None:
    result = AgentRunResult(
        message="Updated docs and verified tests.",
        run_id=7,
        task="update the docs",
        changed_paths=["docs/PROGRESS.md"],
        plan_updates=[
            {
                "steps": [
                    {"step": "Inspect docs", "status": "completed"},
                    {"step": "Update report UI", "status": "completed"},
                ]
            }
        ],
        mutation_records=[
            {
                "action": "edit_file",
                "path": "docs/PROGRESS.md",
                "ok": True,
                "output": (
                    "--- a/docs/PROGRESS.md\n"
                    "+++ b/docs/PROGRESS.md\n"
                    "@@ -1,4 +1,4 @@\n"
                    " unchanged context\n"
                    "-old line\n"
                    "+new line"
                ),
            }
        ],
        command_records=[{"command": "uv run pytest", "ok": True, "status": "passed"}],
        verification_results=[
            {"purpose": "test", "command": "uv run pytest", "ok": True, "status": "passed"}
        ],
        context_records=[
            {"action": "repo_map", "ok": True, "status": "ok"},
            {"action": "rank_context", "task": "update report UI", "ok": True, "status": "ok"},
        ],
    )

    body = format_work_report_body(result)

    assert "Current Task:\n  update the docs" in body
    assert "Current Step:\n  Update report UI" in body
    assert "Files Being Modified:\n  docs/PROGRESS.md" in body
    assert "Context Analysis:\n  - repo_map: ok\n  - rank_context: ok for `update report UI`" in body
    assert "Commands Executed:\n  - `uv run pytest`: passed" in body
    assert "Validation Status:\n  - test `uv run pytest`: passed" in body
    assert "Change Summary:\n  - edit_file docs/PROGRESS.md: ok" in body
    assert "Diff Review:" in body
    assert "  -old line" in body
    assert "  +new line" in body
    assert "unchanged context" not in body
    assert "Final Outcome:\n  Updated docs and verified tests." in body


def test_should_show_work_report_stays_quiet_for_simple_chat() -> None:
    assert not should_show_work_report(AgentRunResult(message="hello", run_id=1))


def test_format_prompt_border_parts() -> None:
    assert format_prompt_header("You", width=24) == "+ YOU -----------------+"
    assert format_prompt_footer(width=24) == "+----------------------+"


def test_stream_helpers_render_compact_progress(monkeypatch) -> None:
    echoed: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        "code_agent.terminal_ui.typer.echo",
        lambda value="", nl=True, **_kwargs: echoed.append((value, nl)),
    )

    print_stream_start("model response for step 1")
    print_stream_marker()
    print_stream_end()

    assert "STREAMING" in echoed[0][0]
    assert "model response for step 1" in echoed[0][0]
    assert echoed[0][1] is False
    assert echoed[1] == (".", False)
    assert echoed[2] == ("", True)


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


def test_interactive_turn_prints_work_report_before_agent_message(monkeypatch) -> None:
    printed: list[tuple[str, str]] = []
    monkeypatch.setattr(interactive, "print_panel", lambda title, body: printed.append((title, body)))
    monkeypatch.setattr(
        interactive,
        "print_work_report_panel",
        lambda result: printed.append(("Work Report", format_work_report_body(result)))
        if should_show_work_report(result)
        else None,
    )

    class Agent:
        def run_detailed(self, _task: str) -> AgentRunResult:
            return AgentRunResult(
                message="done",
                run_id=1,
                task="make progress visible",
                changed_paths=["README.md"],
                mutation_records=[
                    {
                        "action": "edit_file",
                        "path": "README.md",
                        "ok": True,
                        "output": "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new",
                    }
                ],
                plan_updates=[
                    {
                        "steps": [
                            {"step": "Inspect docs", "status": "completed"},
                            {"step": "Render plan", "status": "in_progress"},
                        ]
                    }
                ],
            )

    transcript = run_interactive_turn("make progress visible", Agent(), [], SessionState())

    assert printed[0][0] == "Work Report"
    assert "Current Task:\n  make progress visible" in printed[0][1]
    assert "Diff Review:" in printed[0][1]
    assert "  +new" in printed[0][1]
    assert printed[1] == ("Agent47", "done")
    assert transcript == [("make progress visible", "done")]
