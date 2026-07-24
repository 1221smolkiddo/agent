from __future__ import annotations

import pytest
from rich.console import Console

from code_agent.diff_viewer import parse_unified_diff
from code_agent.diff_viewer_rows import (
    DiffViewModel,
    FileHeaderRow,
    HunkHeaderRow,
    LineRow,
    build_diff_view_model,
)
from code_agent.interactive_diff import (
    DiffViewerState,
    apply_viewer_key,
    ensure_cursor_visible,
    file_header_at_row,
    go_end,
    go_home,
    hunk_header_rows,
    hunk_position_at_row,
    initial_viewer_state,
    iter_visible_rows,
    move_cursor,
    next_hunk,
    page_down,
    page_up,
    previous_hunk,
    render_diff_row,
    render_file_header_row,
    render_hunk_header_row,
    render_line_row,
    render_screen,
    render_sticky_header,
    show_diff,
    visible_row_range,
)


def _sample_model() -> DiffViewModel:
    diff = "\n".join(
        [
            "--- a/a.txt",
            "+++ b/a.txt",
            "@@ -1 +1 @@",
            "-one",
            "+ONE",
            "@@ -3 +3 @@",
            "-three",
            "+THREE",
            "--- a/b.txt",
            "+++ b/b.txt",
            "@@ -1 +1 @@",
            "-two",
            "+TWO",
            "",
        ]
    )
    return build_diff_view_model(parse_unified_diff(diff))


def _state(
    *,
    cursor_row: int = 0,
    scroll_offset: int = 0,
    terminal_height: int = 12,
) -> DiffViewerState:
    return DiffViewerState(cursor_row, scroll_offset, terminal_height)


def test_initial_state() -> None:
    state = initial_viewer_state(terminal_height=20)

    assert state.cursor_row == 0
    assert state.scroll_offset == 0
    assert state.terminal_height == 20
    assert state.content_height == 16


def test_move_cursor_down_and_up() -> None:
    model = _sample_model()
    state = _state()

    down = move_cursor(state, model.row_count, 1)
    assert down.cursor_row == 1

    up = move_cursor(down, model.row_count, -1)
    assert up.cursor_row == 0


def test_move_cursor_clamps_at_boundaries() -> None:
    model = _sample_model()
    state = _state(cursor_row=model.row_count - 1)

    moved = move_cursor(state, model.row_count, 1)
    assert moved.cursor_row == model.row_count - 1

    top = move_cursor(_state(), model.row_count, -1)
    assert top.cursor_row == 0


def test_ensure_cursor_visible_scrolls_down() -> None:
    model = _sample_model()
    state = _state(cursor_row=10, scroll_offset=0, terminal_height=8)

    visible = ensure_cursor_visible(state, model.row_count)

    assert visible.cursor_row == 10
    assert visible.scroll_offset == 7


def test_ensure_cursor_visible_scrolls_up() -> None:
    model = _sample_model()
    state = _state(cursor_row=2, scroll_offset=8, terminal_height=8)

    visible = ensure_cursor_visible(state, model.row_count)

    assert visible.cursor_row == 2
    assert visible.scroll_offset == 2


def test_page_navigation() -> None:
    model = _sample_model()
    state = _state(terminal_height=8)

    down = page_down(state, model.row_count)
    assert down.cursor_row == 3

    up = page_up(down, model.row_count)
    assert up.cursor_row == 0


def test_home_and_end() -> None:
    model = _sample_model()
    state = _state(cursor_row=5, scroll_offset=3, terminal_height=8)

    home = go_home(state, model.row_count)
    assert home.cursor_row == 0
    assert home.scroll_offset == 0

    end = go_end(home, model.row_count)
    assert end.cursor_row == model.row_count - 1
    assert end.scroll_offset == max(0, model.row_count - end.content_height)


def test_next_and_previous_hunk() -> None:
    model = _sample_model()
    hunk_rows = hunk_header_rows(model)
    state = _state(cursor_row=hunk_rows[0])

    jumped = next_hunk(state, model)
    assert jumped.cursor_row == hunk_rows[1]

    previous = previous_hunk(jumped, model)
    assert previous.cursor_row == hunk_rows[0]


def test_previous_hunk_from_later_line_targets_current_file_hunk() -> None:
    model = _sample_model()
    second_file_hunk = hunk_header_rows(model)[-1]
    state = _state(cursor_row=second_file_hunk + 1)

    previous = previous_hunk(state, model)
    assert previous.cursor_row == second_file_hunk


def test_apply_viewer_key_exit_keys() -> None:
    model = _sample_model()
    state = _state()

    assert apply_viewer_key(state, model, "quit") is None
    assert apply_viewer_key(state, model, "exit") is None


def test_apply_viewer_key_interrupt_raises() -> None:
    model = _sample_model()
    state = _state()

    with pytest.raises(KeyboardInterrupt):
        apply_viewer_key(state, model, "interrupt")


def test_apply_viewer_key_arrow_aliases() -> None:
    model = _sample_model()
    state = _state()

    assert apply_viewer_key(state, model, "down").cursor_row == 1
    assert apply_viewer_key(state, model, "right").cursor_row == 1
    assert apply_viewer_key(_state(cursor_row=1), model, "up").cursor_row == 0
    assert apply_viewer_key(_state(cursor_row=1), model, "left").cursor_row == 0


def test_visible_row_range_top_boundary() -> None:
    state = _state(scroll_offset=0, terminal_height=8)

    assert visible_row_range(state, 20) == (0, 4)


def test_visible_row_range_bottom_boundary() -> None:
    state = _state(scroll_offset=16, terminal_height=8)

    assert visible_row_range(state, 20) == (16, 20)


def test_visible_row_range_empty_model() -> None:
    state = _state()

    assert visible_row_range(state, 0) == (0, 0)


def test_iter_visible_rows_across_multiple_files() -> None:
    model = _sample_model()
    state = _state(scroll_offset=0, terminal_height=8)

    visible = list(iter_visible_rows(model, state))
    assert len(visible) == 4
    assert isinstance(visible[0][1], FileHeaderRow)
    assert isinstance(visible[1][1], HunkHeaderRow)


def test_file_header_and_hunk_position_helpers() -> None:
    model = _sample_model()

    file_row = file_header_at_row(model, 7)
    assert isinstance(file_row, FileHeaderRow)
    assert file_row.filename == "b.txt"

    on_file_header, total_on_file = hunk_position_at_row(model, 7)
    assert on_file_header == 0
    assert total_on_file == 1

    on_line, total_on_line = hunk_position_at_row(model, 9)
    assert on_line == 1
    assert total_on_line == 1


def test_render_file_header_row() -> None:
    row = FileHeaderRow(
        file_index=0,
        old_path="src/runtime.py",
        new_path="src/runtime.py",
        total_additions=14,
        total_deletions=5,
    )

    rendered = render_file_header_row(row)
    text = rendered.plain

    assert "src/runtime.py" in text
    assert "+14" in text
    assert "-5" in text


def test_render_hunk_header_row() -> None:
    row = HunkHeaderRow(
        file_index=0,
        hunk_index=0,
        header="@@ -24,8 +24,11 @@",
        additions=3,
        deletions=1,
        context=2,
    )

    assert render_hunk_header_row(row).plain == "@@ -24,8 +24,11 @@"


def test_render_line_rows_use_expected_styles() -> None:
    context = render_line_row(
        LineRow(0, 0, 0, "context", "unchanged", 10, 10)
    )
    added = render_line_row(
        LineRow(0, 0, 1, "added", "added line", None, 11)
    )
    removed = render_line_row(
        LineRow(0, 0, 2, "removed", "removed line", 10, None)
    )

    assert "unchanged" in context.plain
    assert "+ added line" in added.plain
    assert "- removed line" in removed.plain
    assert "10" in context.plain
    assert "11" in added.plain


def test_render_diff_row_highlights_cursor() -> None:
    row = HunkHeaderRow(
        file_index=0,
        hunk_index=0,
        header="@@ -1 +1 @@",
        additions=1,
        deletions=1,
        context=0,
    )

    rendered = render_diff_row(row, highlight=True)
    assert any("reverse" in (span.style or "") for span in rendered._spans)


def test_render_sticky_header_contains_status_fields() -> None:
    model = _sample_model()
    state = _state(cursor_row=6, terminal_height=12)
    console = Console(width=100, force_terminal=True)

    with console.capture() as capture:
        console.print(render_sticky_header(model, state))

    text = capture.get()
    assert "Agent47 Diff Viewer" in text
    assert "File:" in text
    assert "Hunk:" in text
    assert f"Rows: {model.row_count}" in text
    assert "Press q to exit" in text


def test_render_screen_only_includes_viewport_rows() -> None:
    model = _sample_model()
    state = _state(scroll_offset=0, terminal_height=8)
    console = Console(width=120, force_terminal=True)

    with console.capture() as capture:
        console.print(render_screen(model, state))

    text = capture.get()
    assert "Agent47 Diff Viewer" in text
    assert "ONE" in text
    assert "TWO" not in text


def test_show_diff_non_interactive_renders_once() -> None:
    model = _sample_model()
    console = Console(record=True, force_terminal=False)

    show_diff(model, console=console)

    output = console.export_text()
    assert "Agent47 Diff Viewer" in output
    assert "a.txt" in output


def test_show_diff_with_injected_keys_exits_cleanly() -> None:
    model = _sample_model()
    console = Console(record=True, force_terminal=True, width=120)
    keys = iter(["down", "next_hunk", "quit"])

    show_diff(model, console=console, read_key=lambda: next(keys, "quit"))

    output = console.export_text()
    assert "Agent47 Diff Viewer" in output


def test_large_diff_viewport_stays_bounded() -> None:
    lines = [
        "--- a/big.txt",
        "+++ b/big.txt",
        "@@ -1,100 +1,100 @@",
    ]
    for index in range(1, 101):
        lines.append(f"-old {index}")
        lines.append(f"+new {index}")
    model = build_diff_view_model(parse_unified_diff("\n".join(lines) + "\n"))
    state = _state(scroll_offset=150, terminal_height=10)

    start, end = visible_row_range(state, model.row_count)
    assert end - start == state.content_height
    assert end <= model.row_count
