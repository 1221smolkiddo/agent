from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Iterable
from typing import TYPE_CHECKING

from rich.console import Console
from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.table import box
from rich.text import Text
from rich.theme import Theme

from .work_report import _single_line, _change_items_filtered, should_show_work_report

if TYPE_CHECKING:
    from .agent import AgentRunResult

# Setup Rich Console with a professional theme
custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "danger": "bold red",
    "success": "bold green",
    "muted": "bright_black",
    "accent": "bright_cyan",
})
console = Console(theme=custom_theme)

def should_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("AGENT47_COLOR", "").lower() in {"1", "true", "yes", "always"}:
        return True
    return console.is_terminal


def _format_text(value: str, style: str = "default") -> Text:
    return Text(value, style=style)


def print_panel(title: str, body: str, *, style: str = "muted", markup: bool = False) -> None:
    if not body.strip():
        return
    renderable = Markdown(body) if markup else _format_text(_normalize_panel_body(body), style="default")
    console.print()
    console.print(
        Panel(
            renderable,
            title=Text(title, style="bold"),
            title_align="left",
            border_style=style,
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )
    console.print()


def print_renderable_panel(title: str, renderable: object, *, style: str = "muted") -> None:
    console.print()
    console.print(
        Panel(
            renderable,
            title=Text(title, style="bold"),
            title_align="left",
            border_style=style,
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )
    console.print()


def print_startup_header(cwd: str, mode: str, model: str) -> None:
    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    grid.add_column(justify="right")
    title = Text("Agent47", style="bold cyan")
    title.append("  coding agent", style="muted")
    mode_text = Text("Write Enabled" if mode == "write-enabled" else "Dry Run")
    mode_text.stylize("green" if mode == "write-enabled" else "yellow")
    grid.add_row(title, mode_text)
    grid.add_row(Text(str(Path(cwd)), style="muted"), Text(f"Model: {model}", style="cyan"))
    print_renderable_panel("Session", grid, style="cyan")

def print_key_values(title: str, rows: Iterable[tuple[str, object]]) -> None:
    """Compact key-values."""
    rendered_rows = list(rows)
    if not rendered_rows:
        return
    table = Table.grid(expand=True, padding=(0, 3))
    table.add_column(style="muted", no_wrap=True)
    table.add_column()
    for key, value in rendered_rows:
        table.add_row(Text(str(key), style="bold"), Text(str(value), style="default"))
    print_renderable_panel(title, table, style="cyan")

def print_work_report_panel(result: AgentRunResult) -> None:
    if not should_show_work_report(result):
        return

    modified_paths: list[str] = []
    created_paths: list[str] = []
    deleted_paths: list[str] = []
    
    for item in _change_items_filtered(result):
        if item["status"] != "ok":
            continue
        action = item["action"]
        path = item["path"]
        if action == "write_file":
            created_paths.append(path)
        elif action == "delete_file":
            deleted_paths.append(path)
        else:
            modified_paths.append(path)

    sections: list[object] = []
    summary = Table.grid(expand=True)
    summary.add_column(ratio=1)
    summary.add_column(justify="right", no_wrap=True)
    summary.add_row(Text("Finished Work", style="bold green"), Text(f"Run #{result.run_id}", style="muted"))
    if result.message:
        summary.add_row(Text(_single_line(result.message, max_chars=120), style="default"), Text(""))
    duration = getattr(result, "duration", None)
    if duration is not None:
        summary.add_row(Text(f"Duration: {duration}s", style="muted"), Text(""))
    sections.append(summary)

    if created_paths or modified_paths or deleted_paths:
        changes = Table(show_header=True, header_style="bold cyan", box=box.SIMPLE, padding=(0, 2), expand=True)
        changes.add_column("Action", style="muted", no_wrap=True)
        changes.add_column("Path", overflow="fold")
        for path in created_paths:
            changes.add_row("Created", str(path))
        for path in modified_paths:
            changes.add_row("Modified", str(path))
        for path in deleted_paths:
            changes.add_row("Deleted", str(path))
        sections.extend([Text("Changes", style="bold underline"), changes])

    if result.verification_results:
        verification = Table(show_header=True, header_style="bold cyan", box=box.SIMPLE, padding=(0, 2), expand=True)
        verification.add_column("Check", overflow="fold")
        verification.add_column("Status", no_wrap=True)
        verification.add_column("Command", overflow="fold")
        for v in result.verification_results:
            label = v.get("purpose") or v.get("command") or v.get("name") or "verification"
            status_word = "Passed" if v.get("ok") else "Failed"
            status_text = Text(status_word, style="green" if v.get("ok") else "red")
            verification.add_row(str(label), status_text, str(v.get("command") or ""))
        sections.extend([Text("Verification", style="bold underline"), verification])

    if not created_paths and not modified_paths and not deleted_paths and not result.verification_results:
        sections.append(Text(_single_line(result.message, max_chars=900), style="default"))

    print_renderable_panel("Finished Work", Group(*_with_spacers(sections)), style="green")

def print_error_card(title: str, lines: list[tuple[str, str]], suggestions: list[str]) -> None:
    text = Text()
    for label, detail in lines:
        if label:
            text.append(f"{label}\n", style="muted")
        text.append(f"{detail}\n\n", style="danger" if label == "Reason:" else "default")
    
    if suggestions:
        text.append("Suggested actions:\n", style="muted")
        for sug in suggestions:
            text.append(f"- {sug}\n", style="default")

    print_renderable_panel(title, text.rstrip(), style="red")

def format_prompt_header(title: str) -> str:
    return f"[bold green]{title}[/bold green]"

def format_prompt_footer() -> str:
    return ""

def colorize_panel(panel: str, title: str) -> str:
    # Used for backward compatibility with existing tests
    return panel

def print_agent_banner(*args, **kwargs) -> None:
    # Stubbed out for backward compatibility
    pass

def print_plan_panel(*args, **kwargs) -> None:
    # Plan is now shown via timeline
    pass


def print_response(author: str, body: str, *, author_style: str = "bold cyan") -> None:
    """Print a conversational response inline instead of a bordered panel.

    Use for normal assistant/user messages. Panels remain reserved for
    permission requests, errors, and detailed reports.
    """
    if not body:
        return
    console.print()
    console.print(Text(author, style=author_style))
    console.print(Text(_normalize_panel_body(str(body)), style="default"))
    console.print()


def _normalize_panel_body(body: str) -> str:
    lines = [line.rstrip() for line in body.strip().splitlines()]
    collapsed: list[str] = []
    previous_blank = False
    for line in lines:
        blank = not line.strip()
        if blank and previous_blank:
            continue
        collapsed.append(line)
        previous_blank = blank
    normalized = "\n".join(collapsed)
    return normalized


def _with_spacers(items: list[object]) -> list[object]:
    spaced: list[object] = []
    for item in items:
        if spaced:
            spaced.append(Text(""))
        spaced.append(item)
    return spaced
