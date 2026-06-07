from __future__ import annotations

import textwrap
from collections.abc import Iterable

import typer


PANEL_WIDTH = 78


def print_panel(title: str, body: str, *, width: int = PANEL_WIDTH) -> None:
    typer.echo(format_panel(title, body, width=width))


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
    typer.echo(format_key_values(title, rows, width=width))


def print_status_line(label: str, detail: str) -> None:
    typer.echo(f"  {label:<10} {detail}")


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
