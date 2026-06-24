from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Iterable
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.theme import Theme

from .work_report import _clean_task_text, _single_line, _change_items_filtered, should_show_work_report

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


def print_panel(title: str, body: str, *, style: str = "accent") -> None:
    if not body.strip():
        return
    console.print(
        Panel(
            _format_text(body),
            title=Text(title, style="bold"),
            title_align="left",
            border_style=style,
            padding=(0, 1),
        )
    )


def print_startup_header(cwd: str, mode: str, model: str) -> None:
    header = Text()
    header.append("Agent47", style="bold cyan")
    header.append(" │ ", style="muted")
    header.append("Workspace: ", style="muted")
    header.append(Path(cwd).name, style="cyan")
    header.append(" │ ", style="muted")
    header.append("Model: ", style="muted")
    header.append(model, style="cyan")
    header.append(" │ ", style="muted")
    header.append("Write Enabled" if mode == "write-enabled" else "Dry Run", style="green" if mode == "write-enabled" else "yellow")

    console.print(header)
    console.print(Text(f"Path: {cwd}", style="muted"))
    console.print(Text("Type a task or /help", style="muted"))
    console.print()

def print_key_values(title: str, rows: Iterable[tuple[str, object]]) -> None:
    """Compact key-values."""
    rendered_rows = list(rows)
    if not rendered_rows:
        return
    text = Text()
    for key, value in rendered_rows:
        text.append(f"{key}: ", style="muted")
        text.append(str(value), style="default")
        text.append("\n")
    console.print(
        Panel(
            text.rstrip(),
            title=Text(title, style="bold"),
            title_align="left",
            border_style="cyan",
            padding=(0, 1),
        )
    )

def print_work_report_panel(result: AgentRunResult) -> None:
    if not should_show_work_report(result):
        return
        
    text = Text()
    
    task = _single_line(_clean_task_text(result))
    if task:
        text.append(f"✓ {task}\n\n", style="bold green")
        
    modified_paths = []
    created_paths = []
    deleted_paths = []
    
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
            
    if modified_paths:
        text.append("Modified:\n", style="bold")
        for p in modified_paths:
            text.append(f"• {p}\n")
        text.append("\n")
        
    if created_paths:
        text.append("Added:\n", style="bold")
        for p in created_paths:
            text.append(f"• {p}\n")
        text.append("\n")
        
    if deleted_paths:
        text.append("Deleted:\n", style="bold")
        for p in deleted_paths:
            text.append(f"• {p}\n")
        text.append("\n")
        
    if result.verification_results:
        text.append("Verification:\n", style="muted")
        all_passed = all(v.get("ok") for v in result.verification_results)
        text.append("✓ Passed\n" if all_passed else "✗ Failed\n", style="green" if all_passed else "red")
        text.append("\n")

    if not modified_paths and not created_paths and not deleted_paths:
        text.append(f"{_single_line(result.message, max_chars=900)}\n")

    text.rstrip()
    console.print(
        Panel(
            text,
            title=Text("Completion Summary", style="bold"),
            border_style="blue",
            padding=(0, 1),
        )
    )

def print_error_card(title: str, lines: list[tuple[str, str]], suggestions: list[str]) -> None:
    text = Text()
    for label, detail in lines:
        if label:
            text.append(f"{label}\n", style="muted")
        text.append(f"{detail}\n\n", style="danger" if label == "Reason:" else "default")
    
    if suggestions:
        text.append("Suggested Actions:\n", style="muted")
        for sug in suggestions:
            text.append(f"• {sug}\n", style="default")
            
    console.print(
        Panel(
            text.rstrip(),
            title=Text(title, style="bold red"),
            title_align="left",
            border_style="red",
        )
    )

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
