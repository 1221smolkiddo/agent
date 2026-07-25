"""Interactive terminal viewer for flattened diff view models."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from .diff_types import DiffViewMode
from .diff_viewer_rows import (
    DiffRow,
    DiffViewModel,
    FileHeaderRow,
    HunkHeaderRow,
    LineRow,
)

DiffViewerKey = Literal[
    "up",
    "down",
    "left",
    "right",
    "page_up",
    "page_down",
    "home",
    "end",
    "next_hunk",
    "prev_hunk",
    "toggle_mode",
    "quit",
    "exit",
    "interrupt",
]

STICKY_HEADER_LINES = 4
DEFAULT_TERMINAL_HEIGHT = 24
MIN_SIDE_BY_SIDE_WIDTH = 70


@dataclass(frozen=True)
class DiffViewerState:
    """Separate viewer state for cursor, scrolling, and view mode."""

    cursor_row: int
    scroll_offset: int
    terminal_height: int
    view_mode: DiffViewMode = DiffViewMode.UNIFIED

    @property
    def content_height(self) -> int:
        return max(1, self.terminal_height - STICKY_HEADER_LINES)


def initial_viewer_state(
    *,
    terminal_height: int = DEFAULT_TERMINAL_HEIGHT,
    view_mode: DiffViewMode | str = DiffViewMode.UNIFIED,
) -> DiffViewerState:
    """Return the default viewer state for a new session."""
    mode = DiffViewMode.normalize(view_mode)
    return DiffViewerState(
        cursor_row=0,
        scroll_offset=0,
        terminal_height=terminal_height,
        view_mode=mode,
    )


def clamp_row(row: int, row_count: int) -> int:
    """Clamp a row index to valid model bounds."""
    if row_count <= 0:
        return 0
    return max(0, min(row, row_count - 1))


def visible_row_range(state: DiffViewerState, row_count: int) -> tuple[int, int]:
    """Return the half-open `[start, end)` range of model rows in the viewport."""
    if row_count <= 0:
        return 0, 0
    start = clamp_row(state.scroll_offset, row_count)
    end = min(row_count, start + state.content_height)
    return start, end


def ensure_cursor_visible(state: DiffViewerState, row_count: int) -> DiffViewerState:
    """Adjust scroll offset so the cursor stays inside the viewport."""
    if row_count <= 0:
        return DiffViewerState(0, 0, state.terminal_height, view_mode=state.view_mode)

    content_height = state.content_height
    scroll_offset = state.scroll_offset
    cursor_row = clamp_row(state.cursor_row, row_count)

    if cursor_row < scroll_offset:
        scroll_offset = cursor_row
    elif cursor_row >= scroll_offset + content_height:
        scroll_offset = cursor_row - content_height + 1

    max_scroll = max(0, row_count - content_height)
    scroll_offset = max(0, min(scroll_offset, max_scroll))
    return DiffViewerState(
        cursor_row=cursor_row,
        scroll_offset=scroll_offset,
        terminal_height=state.terminal_height,
        view_mode=state.view_mode,
    )


def move_cursor(state: DiffViewerState, row_count: int, delta: int) -> DiffViewerState:
    """Move the cursor by `delta` rows and keep it visible."""
    cursor_row = clamp_row(state.cursor_row + delta, row_count)
    return ensure_cursor_visible(
        DiffViewerState(
            cursor_row=cursor_row,
            scroll_offset=state.scroll_offset,
            terminal_height=state.terminal_height,
            view_mode=state.view_mode,
        ),
        row_count,
    )


def page_down(state: DiffViewerState, row_count: int) -> DiffViewerState:
    """Move one page down."""
    step = max(1, state.content_height - 1)
    return move_cursor(state, row_count, step)


def page_up(state: DiffViewerState, row_count: int) -> DiffViewerState:
    """Move one page up."""
    step = max(1, state.content_height - 1)
    return move_cursor(state, row_count, -step)


def go_home(state: DiffViewerState, row_count: int) -> DiffViewerState:
    """Jump to the first row."""
    _ = row_count
    return DiffViewerState(0, 0, state.terminal_height, view_mode=state.view_mode)


def go_end(state: DiffViewerState, row_count: int) -> DiffViewerState:
    """Jump to the last row."""
    if row_count <= 0:
        return DiffViewerState(0, 0, state.terminal_height, view_mode=state.view_mode)
    cursor_row = row_count - 1
    scroll_offset = max(0, row_count - state.content_height)
    return DiffViewerState(
        cursor_row=cursor_row,
        scroll_offset=scroll_offset,
        terminal_height=state.terminal_height,
        view_mode=state.view_mode,
    )


def hunk_header_rows(model: DiffViewModel) -> tuple[int, ...]:
    """Return model row indices for every hunk header."""
    return tuple(
        index for index, row in enumerate(model.rows) if isinstance(row, HunkHeaderRow)
    )


def next_hunk(state: DiffViewerState, model: DiffViewModel) -> DiffViewerState:
    """Jump to the next hunk header row."""
    for index in hunk_header_rows(model):
        if index > state.cursor_row:
            return ensure_cursor_visible(
                DiffViewerState(
                    index,
                    state.scroll_offset,
                    state.terminal_height,
                    view_mode=state.view_mode,
                ),
                model.row_count,
            )
    return state


def previous_hunk(state: DiffViewerState, model: DiffViewModel) -> DiffViewerState:
    """Jump to the previous hunk header row."""
    previous: int | None = None
    for index in hunk_header_rows(model):
        if index >= state.cursor_row:
            break
        previous = index
    if previous is None:
        return state
    return ensure_cursor_visible(
        DiffViewerState(
            previous,
            state.scroll_offset,
            state.terminal_height,
            view_mode=state.view_mode,
        ),
        model.row_count,
    )


def apply_viewer_key(
    state: DiffViewerState,
    model: DiffViewModel,
    key: DiffViewerKey,
) -> DiffViewerState | None:
    """Apply a navigation key and return the next state, or `None` to exit."""
    row_count = model.row_count

    if key in {"quit", "exit"}:
        return None
    if key == "interrupt":
        raise KeyboardInterrupt
    if key in {"up", "left"}:
        return move_cursor(state, row_count, -1)
    if key in {"down", "right"}:
        return move_cursor(state, row_count, 1)
    if key == "page_up":
        return page_up(state, row_count)
    if key == "page_down":
        return page_down(state, row_count)
    if key == "home":
        return go_home(state, row_count)
    if key == "end":
        return go_end(state, row_count)
    if key == "next_hunk":
        return next_hunk(state, model)
    if key == "prev_hunk":
        return previous_hunk(state, model)
    if key == "toggle_mode":
        next_mode = (
            DiffViewMode.SIDE_BY_SIDE
            if state.view_mode == DiffViewMode.UNIFIED
            else DiffViewMode.UNIFIED
        )
        return DiffViewerState(
            cursor_row=state.cursor_row,
            scroll_offset=state.scroll_offset,
            terminal_height=state.terminal_height,
            view_mode=next_mode,
        )
    return state


def file_header_at_row(model: DiffViewModel, row_index: int) -> FileHeaderRow | None:
    """Return the nearest file header at or above `row_index`."""
    if not model.rows:
        return None
    bounded = clamp_row(row_index, model.row_count)
    for index in range(bounded, -1, -1):
        row = model.rows[index]
        if isinstance(row, FileHeaderRow):
            return row
    return None


def hunk_position_at_row(model: DiffViewModel, row_index: int) -> tuple[int, int]:
    """Return `(current_hunk, total_hunks)` for the file containing `row_index`."""
    file_row = file_header_at_row(model, row_index)
    if file_row is None:
        return 0, 0

    hunk_rows = [
        index
        for index, row in enumerate(model.rows)
        if isinstance(row, HunkHeaderRow) and row.file_index == file_row.file_index
    ]
    if not hunk_rows:
        return 0, 0

    current = 0
    bounded = clamp_row(row_index, model.row_count)
    for number, hunk_index in enumerate(hunk_rows, start=1):
        if hunk_index <= bounded:
            current = number
    return current, len(hunk_rows)


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3] + "..."


def render_file_header_row(row: FileHeaderRow) -> Text:
    """Render a file header row."""
    filename = row.filename or "<unknown>"
    rendered = Text()
    rendered.append("📄 ", style="bold")
    rendered.append(filename, style="bold cyan")
    rendered.append("    ", style="default")
    rendered.append(f"+{row.total_additions}", style="bold green")
    rendered.append("  ", style="default")
    rendered.append(f"-{row.total_deletions}", style="bold red")
    return rendered


def render_hunk_header_row(row: HunkHeaderRow) -> Text:
    """Render a hunk header row."""
    return Text(row.header, style="bold yellow")


def render_line_row(row: LineRow) -> Text:
    """Render one diff line in unified format."""
    old_number = "" if row.old_line_number is None else f"{row.old_line_number:>4}"
    new_number = "" if row.new_line_number is None else f"{row.new_line_number:>4}"
    rendered = Text()
    rendered.append(f"{old_number} {new_number}  ", style="bright_black")

    if row.line_kind == "added":
        rendered.append("+ ", style="bold green")
        rendered.append(row.text, style="green")
    elif row.line_kind == "removed":
        rendered.append("- ", style="bold red")
        rendered.append(row.text, style="red")
    else:
        rendered.append("  ", style="dim")
        rendered.append(row.text, style="dim")
    return rendered


def render_side_by_side_line_row(
    row: LineRow,
    column_width: int,
) -> Text:
    """Render one LineRow in side-by-side format (old file left, new file right)."""
    rendered = Text()
    code_width = max(1, column_width - 7)  # 4 line num + 1 space + 2 sign = 7

    if row.line_kind == "context":
        old_num = f"{row.old_line_number:>4}" if row.old_line_number is not None else "    "
        new_num = f"{row.new_line_number:>4}" if row.new_line_number is not None else "    "
        old_txt = _truncate_text(row.text, code_width)
        new_txt = _truncate_text(row.text, code_width)

        left = Text()
        left.append(f"{old_num}   ", style="bright_black")
        left.append(old_txt.ljust(code_width), style="dim")

        right = Text()
        right.append(f"{new_num}   ", style="bright_black")
        right.append(new_txt.ljust(code_width), style="dim")

    elif row.line_kind == "removed":
        old_num = f"{row.old_line_number:>4}" if row.old_line_number is not None else "    "
        old_txt = _truncate_text(row.text, code_width)

        left = Text()
        left.append(f"{old_num} ", style="bright_black")
        left.append("- ", style="bold red")
        left.append(old_txt.ljust(code_width), style="red")

        right = Text(" " * column_width)

    elif row.line_kind == "added":
        new_num = f"{row.new_line_number:>4}" if row.new_line_number is not None else "    "
        new_txt = _truncate_text(row.text, code_width)

        left = Text(" " * column_width)

        right = Text()
        right.append(f"{new_num} ", style="bright_black")
        right.append("+ ", style="bold green")
        right.append(new_txt.ljust(code_width), style="green")

    rendered.append_text(left)
    rendered.append(" │ ", style="bright_black")
    rendered.append_text(right)
    return rendered


def render_diff_row(row: DiffRow, *, highlight: bool = False) -> Text:
    """Render any supported diff row in unified format."""
    if isinstance(row, FileHeaderRow):
        rendered = render_file_header_row(row)
    elif isinstance(row, HunkHeaderRow):
        rendered = render_hunk_header_row(row)
    else:
        rendered = render_line_row(row)

    if highlight:
        rendered.stylize("reverse")
    return rendered


def render_sticky_header(
    model: DiffViewModel,
    state: DiffViewerState,
    console: Console | None = None,
) -> Panel:
    """Render the sticky viewer header."""
    file_row = file_header_at_row(model, state.cursor_row)
    filename = file_row.filename if file_row and file_row.filename else "<none>"
    current_hunk, total_hunks = hunk_position_at_row(model, state.cursor_row)

    width = console.width if console else 80
    is_narrow = (
        state.view_mode == DiffViewMode.SIDE_BY_SIDE and width < MIN_SIDE_BY_SIDE_WIDTH
    )
    mode_label = (
        "Side-by-Side" if state.view_mode == DiffViewMode.SIDE_BY_SIDE else "Unified"
    )
    if is_narrow:
        mode_label += " (Narrow terminal fallback)"

    body = Text()
    body.append("Agent47 Diff Viewer", style="bold")
    body.append(
        f"  [{mode_label}]\n",
        style="bold magenta" if state.view_mode == DiffViewMode.SIDE_BY_SIDE else "bold cyan",
    )
    body.append(f"File: {filename}\n", style="cyan")
    body.append(f"Hunk: {current_hunk} / {total_hunks}\n", style="yellow")
    body.append(f"Rows: {model.row_count}\n", style="bright_black")
    body.append("Press TAB to toggle mode  |  Press q to exit", style="dim")
    return Panel(body, border_style="bright_black", padding=(0, 1))


def render_unified_viewport(
    model: DiffViewModel, state: DiffViewerState
) -> RenderableType:
    """Render the visible viewport rows in single-column unified format."""
    start, end = visible_row_range(state, model.row_count)
    lines: list[Text] = []
    for index in range(start, end):
        lines.append(
            render_diff_row(model.rows[index], highlight=index == state.cursor_row)
        )

    if not lines:
        lines.append(Text("No diff rows to display.", style="dim"))

    while len(lines) < state.content_height:
        lines.append(Text(""))

    return Text("\n").join(lines)


def render_side_by_side_viewport(
    model: DiffViewModel, state: DiffViewerState, console_width: int
) -> RenderableType:
    """Render the visible viewport rows in two-column side-by-side format."""
    start, end = visible_row_range(state, model.row_count)
    lines: list[Text] = []
    col_width = max(10, (console_width - 3) // 2)

    for index in range(start, end):
        row = model.rows[index]
        is_highlight = index == state.cursor_row
        if isinstance(row, LineRow):
            rendered = render_side_by_side_line_row(row, col_width)
            if is_highlight:
                rendered.stylize("reverse")
            lines.append(rendered)
        else:
            lines.append(render_diff_row(row, highlight=is_highlight))

    if not lines:
        lines.append(Text("No diff rows to display.", style="dim"))

    while len(lines) < state.content_height:
        lines.append(Text(""))

    return Text("\n").join(lines)


def render_viewport(
    model: DiffViewModel,
    state: DiffViewerState,
    console: Console | None = None,
) -> RenderableType:
    """Render visible viewport using the active rendering mode."""
    width = console.width if console else 80
    if state.view_mode == DiffViewMode.SIDE_BY_SIDE:
        if width < MIN_SIDE_BY_SIDE_WIDTH:
            return render_unified_viewport(model, state)
        return render_side_by_side_viewport(model, state, width)
    return render_unified_viewport(model, state)


def render_screen(
    model: DiffViewModel,
    state: DiffViewerState,
    console: Console | None = None,
) -> RenderableType:
    """Render sticky header plus active viewport."""
    return Group(
        render_sticky_header(model, state, console=console),
        Text(""),
        render_viewport(model, state, console=console),
    )


def show_diff(
    model: DiffViewModel,
    *,
    view_mode: DiffViewMode | str | None = None,
    console: Console | None = None,
    read_key: Callable[[], DiffViewerKey | None] | None = None,
) -> None:
    """Display an interactive diff viewer until the user exits."""
    console = console or Console()
    terminal_height = (
        console.size.height
        or shutil.get_terminal_size(fallback=(80, DEFAULT_TERMINAL_HEIGHT)).lines
    )
    initial_mode = (
        DiffViewMode.normalize(view_mode)
        if view_mode is not None
        else DiffViewMode.UNIFIED
    )
    state = initial_viewer_state(
        terminal_height=terminal_height, view_mode=initial_mode
    )
    key_reader = read_key or _default_read_key
    use_screen = read_key is None and console.is_terminal

    if not use_screen and read_key is None:
        console.print(render_screen(model, state, console=console))
        return

    with Live(
        render_screen(model, state, console=console),
        console=console,
        screen=use_screen,
        transient=use_screen,
        refresh_per_second=12,
    ) as live:
        while True:
            key = key_reader()
            if key is None:
                continue
            try:
                next_state = apply_viewer_key(state, model, key)
            except KeyboardInterrupt:
                raise
            if next_state is None:
                break
            state = next_state
            live.update(render_screen(model, state, console=console))


def _default_read_key() -> DiffViewerKey | None:
    if os.name == "nt":
        return _read_key_windows()
    return _read_key_posix()


def _read_key_windows() -> DiffViewerKey | None:
    import msvcrt

    first = msvcrt.getwch()
    if first in {"\x00", "\xe0"}:
        second = msvcrt.getwch()
        return _windows_special_key(second)
    return _normalize_key(first)


def _windows_special_key(code: str) -> DiffViewerKey | None:
    mapping = {
        "H": "up",
        "P": "down",
        "K": "left",
        "M": "right",
        "I": "page_up",
        "Q": "page_down",
        "G": "home",
        "O": "end",
    }
    return mapping.get(code)


def _read_key_posix() -> DiffViewerKey | None:
    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        first = sys.stdin.read(1)
        if first != "\x1b":
            return _normalize_key(first)
        sequence = first
        while True:
            next_char = sys.stdin.read(1)
            if not next_char:
                break
            sequence += next_char
            if next_char.isalpha() or next_char in {"~"}:
                break
            if len(sequence) > 8:
                break
        return _posix_escape_key(sequence)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _posix_escape_key(sequence: str) -> DiffViewerKey | None:
    mapping = {
        "\x1b[A": "up",
        "\x1b[B": "down",
        "\x1b[C": "right",
        "\x1b[D": "left",
        "\x1b[5~": "page_up",
        "\x1b[6~": "page_down",
        "\x1b[H": "home",
        "\x1b[F": "end",
        "\x1b[1~": "home",
        "\x1b[4~": "end",
        "\x1bOA": "up",
        "\x1bOB": "down",
        "\x1bOC": "right",
        "\x1bOD": "left",
        "\x1b": "exit",
    }
    return mapping.get(sequence)


def _normalize_key(value: str) -> DiffViewerKey | None:
    if value in {"q", "Q"}:
        return "quit"
    if value == "\x03":
        return "interrupt"
    if value in {"n", "N"}:
        return "next_hunk"
    if value in {"p", "P"}:
        return "prev_hunk"
    if value in {"\t", "t", "T"}:
        return "toggle_mode"
    if value == "\x1b":
        return "exit"
    return None


def iter_visible_rows(
    model: DiffViewModel, state: DiffViewerState
) -> Iterable[tuple[int, DiffRow]]:
    """Yield `(row_index, row)` pairs for the current viewport."""
    start, end = visible_row_range(state, model.row_count)
    for index in range(start, end):
        yield index, model.rows[index]
