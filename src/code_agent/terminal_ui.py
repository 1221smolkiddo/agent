from __future__ import annotations

import os
import sys
import textwrap
from collections.abc import Iterable
from typing import TYPE_CHECKING

import typer

from .work_report import format_work_report_body, should_show_work_report

if TYPE_CHECKING:
    from .agent import AgentRunResult


PANEL_WIDTH = 78
ACCENT_COLOR = typer.colors.CYAN
MUTED_COLOR = typer.colors.BRIGHT_BLACK
TITLE_COLOR = typer.colors.BRIGHT_CYAN


PANEL_COLORS = {
    "Agent47": typer.colors.CYAN,
    "You": typer.colors.GREEN,
    "Help": typer.colors.BLUE,
    "Status": typer.colors.CYAN,
    "History": typer.colors.MAGENTA,
    "Plan": typer.colors.BLUE,
    "Work Report": typer.colors.BLUE,
    "Resume": typer.colors.MAGENTA,
    "Mode": typer.colors.YELLOW,
    "Workspace": typer.colors.CYAN,
    "Model": typer.colors.CYAN,
    "Max Steps": typer.colors.YELLOW,
    "Max Failures": typer.colors.YELLOW,
    "Sandbox": typer.colors.YELLOW,
    "System": typer.colors.BRIGHT_BLACK,
    "Unknown Command": typer.colors.RED,
}


STATUS_COLORS = {
    "THINKING": typer.colors.BLUE,
    "READING": typer.colors.CYAN,
    "SEARCHING": typer.colors.CYAN,
    "SEARCHING WEB": typer.colors.CYAN,
    "ANALYZING": typer.colors.MAGENTA,
    "CHECKING": typer.colors.MAGENTA,
    "EDITING": typer.colors.YELLOW,
    "INSTALLING": typer.colors.YELLOW,
    "TESTING": typer.colors.BLUE,
    "BUILDING": typer.colors.BLUE,
    "RUNNING": typer.colors.WHITE,
    "RECOVERING": typer.colors.YELLOW,
    "STREAMING": typer.colors.BLUE,
    "DONE": typer.colors.GREEN,
}


def print_panel(title: str, body: str, *, width: int = PANEL_WIDTH) -> None:
    typer.echo(colorize_panel(format_panel(title, body, width=width), title))


def print_agent_banner(*, width: int = PANEL_WIDTH) -> None:
    typer.echo(colorize_banner(format_agent_banner(width=width)))


def format_agent_banner(*, width: int = PANEL_WIDTH) -> str:
    safe_width = max(width, 24)
    inner_width = safe_width - 2
    title = "A G E N T 4 7"
    top = "+" + "-" * inner_width + "+"
    middle = "|" + title.center(inner_width) + "|"
    bottom = "+" + "-" * inner_width + "+"
    return "\n".join([top, middle, bottom])


def format_panel(title: str, body: str, *, width: int = PANEL_WIDTH) -> str:
    safe_width = max(width, 24)
    inner_width = safe_width - 4
    header = _header(title, safe_width)
    footer = "+" + "-" * (safe_width - 2) + "+"
    lines = [header]
    for line in _wrap_body(body, inner_width):
        lines.append(f"| {line.ljust(inner_width)} |")
    lines.append(footer)
    return "\n".join(lines)


def format_key_values(title: str, rows: Iterable[tuple[str, object]], *, width: int = PANEL_WIDTH) -> str:
    body = "\n".join(f"{key}: {value}" for key, value in rows)
    return format_panel(title, body, width=width)


def print_key_values(title: str, rows: Iterable[tuple[str, object]], *, width: int = PANEL_WIDTH) -> None:
    typer.echo(colorize_panel(format_key_values(title, rows, width=width), title))


def format_plan_panel(plan_updates: list[dict[str, object]], *, width: int = PANEL_WIDTH) -> str:
    body = format_plan_body(plan_updates)
    return format_panel("Plan", body, width=width)


def print_plan_panel(plan_updates: list[dict[str, object]], *, width: int = PANEL_WIDTH) -> None:
    if not plan_updates:
        return
    typer.echo(colorize_panel(format_plan_panel(plan_updates, width=width), "Plan"))


def print_work_report_panel(result: AgentRunResult, *, width: int = PANEL_WIDTH) -> None:
    if not should_show_work_report(result):
        return
    typer.echo(colorize_panel(format_work_report_panel(result, width=width), "Work Report"))


def format_work_report_panel(result: AgentRunResult, *, width: int = PANEL_WIDTH) -> str:
    return format_panel("Work Report", format_work_report_body(result), width=width)


def format_plan_body(plan_updates: list[dict[str, object]]) -> str:
    if not plan_updates:
        return ""

    latest = plan_updates[-1]
    raw_steps = latest.get("steps", [])
    if not isinstance(raw_steps, list) or not raw_steps:
        return str(latest.get("output", "Plan updated"))

    lines: list[str] = []
    for index, item in enumerate(raw_steps, start=1):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "pending"))
        marker = _plan_status_marker(status)
        step = str(item.get("step", "<unnamed step>"))
        note = item.get("note")
        line = f"{marker} {index}. {step}"
        if note:
            line += f" ({note})"
        lines.append(line)
    return "\n".join(lines) if lines else str(latest.get("output", "Plan updated"))


def _plan_status_marker(status: str) -> str:
    return {
        "pending": "[ ]",
        "in_progress": "[>]",
        "completed": "[x]",
        "blocked": "[!]",
    }.get(status, "[ ]")


def print_status_line(label: str, detail: str) -> None:
    color = STATUS_COLORS.get(label, typer.colors.WHITE)
    rendered_label = style(f"{label:<10}", fg=color, bold=True)
    rendered_detail = style(detail, fg=typer.colors.WHITE)
    typer.echo(f"  {rendered_label} {rendered_detail}")


def print_stream_start(detail: str) -> None:
    color = STATUS_COLORS.get("STREAMING", typer.colors.BLUE)
    rendered_label = style(f"{'STREAMING':<10}", fg=color, bold=True)
    rendered_detail = style(detail, fg=typer.colors.WHITE)
    typer.echo(f"  {rendered_label} {rendered_detail}", nl=False)


def print_stream_marker(marker: str = ".") -> None:
    typer.echo(style(marker, fg=typer.colors.BLUE), nl=False)


def print_stream_end() -> None:
    typer.echo("")


def format_prompt_header(title: str, *, width: int = PANEL_WIDTH) -> str:
    return _header(title, max(width, 24))


def format_prompt_footer(*, width: int = PANEL_WIDTH) -> str:
    safe_width = max(width, 24)
    return "+" + "-" * (safe_width - 2) + "+"


def _header(title: str, width: int) -> str:
    normalized = f" {title.strip().upper()} "
    available = width - 2
    if len(normalized) >= available:
        return "+" + normalized[:available].ljust(available, "-") + "+"
    return "+" + normalized + "-" * (available - len(normalized)) + "+"


def _wrap_body(body: str, width: int) -> list[str]:
    if not body:
        return [""]

    wrapped: list[str] = []
    for raw_line in body.splitlines():
        if not raw_line:
            wrapped.append("")
            continue
        wrapped.extend(
            textwrap.wrap(
                raw_line,
                width=width,
                replace_whitespace=False,
                drop_whitespace=False,
                break_long_words=True,
                break_on_hyphens=False,
            )
            or [""]
        )
    return wrapped


def colorize_panel(panel: str, title: str) -> str:
    if not should_color():
        return panel
    color = PANEL_COLORS.get(title, ACCENT_COLOR)
    lines = panel.splitlines()
    styled: list[str] = []
    for line in lines:
        if line.startswith("+"):
            styled.append(style(line, fg=color, bold=True))
        elif line.startswith("|"):
            styled.append(style(line, fg=typer.colors.WHITE))
        else:
            styled.append(line)
    return "\n".join(styled)


def colorize_banner(banner: str) -> str:
    if not should_color():
        return banner
    lines = banner.splitlines()
    return "\n".join(
        [
            style(lines[0], fg=MUTED_COLOR),
            style(lines[1], fg=TITLE_COLOR, bold=True),
            style(lines[2], fg=MUTED_COLOR),
        ]
    )


def style(value: str, *, fg: str, bold: bool = False) -> str:
    if not should_color():
        return value
    return typer.style(value, fg=fg, bold=bold)


def should_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("AGENT47_COLOR", "").lower() in {"1", "true", "yes", "always"}:
        return True
    return bool(getattr(sys.stdout, "isatty", lambda: False)())
