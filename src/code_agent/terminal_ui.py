from __future__ import annotations

from .safety import redact_secrets

from dataclasses import dataclass
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

from .work_report import _change_items_filtered, _process_items, should_show_work_report

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


@dataclass(frozen=True)
class SessionHeader:
    version: str
    profile: str
    model: str
    provider: str
    workspace: str
    sandbox: str
    approval: str
    git_branch: str | None = None

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


def print_startup_header(
    cwd: str | Path,
    mode: str,
    model: str,
    *,
    version: str = "0.x",
    profile: str = "default",
    provider: str = "openrouter",
    sandbox: bool = False,
    approval: str = "auto_read",
    git_branch: str | None = None,
) -> None:
    header = SessionHeader(
        version=version,
        profile=profile,
        model=model,
        provider=provider,
        workspace=str(Path(cwd)),
        sandbox="enabled" if sandbox else "disabled",
        approval=approval,
        git_branch=git_branch,
    )
    print_session_header(header, mode=mode)


def blend_colors(color1: tuple[int, int, int], color2: tuple[int, int, int], ratio: float) -> str:
    r = int(color1[0] + (color2[0] - color1[0]) * ratio)
    g = int(color1[1] + (color2[1] - color1[1]) * ratio)
    b = int(color1[2] + (color2[2] - color1[2]) * ratio)
    return f"#{r:02x}{g:02x}{b:02x}"

def print_session_header(header: SessionHeader, *, mode: str) -> None:
    ascii_art = """
    _    ____ _____ _   _ _____ _  _  _____ 
   / \\  / ___| ____| \\ | |_   _| || ||___  |
  / _ \\| |  _|  _| |  \\| | | | | || |_  / / 
 / ___ \\ |_| | |___| |\\  | | | |__   _|/ /  
/_/   \\_\\____|_____|_| \\_| |_|    |_| /_/   
"""
    start_color = (0, 215, 130) # Cyber Green
    end_color = (0, 180, 255)   # Bright Cyan

    rich_text = Text()
    lines = ascii_art.strip("\n").splitlines()
    for line in lines:
        for i, char in enumerate(line):
            ratio = i / max(1, len(line) - 1)
            hex_color = blend_colors(start_color, end_color, ratio)
            rich_text.append(char, style=f"bold {hex_color}")
        rich_text.append("\n")

    console.print(rich_text)
    
    tips = Text()
    tips.append("Tips for getting started:\n", style="muted")
    tips.append("1. Ask questions, edit files, or run commands.\n", style="default")
    tips.append("2. Be specific for the best results.\n", style="default")
    tips.append("3. ", style="default")
    tips.append("/help", style="bold cyan")
    tips.append(" for more information.\n", style="default")
    
    console.print(tips)



def format_status_line(
    *,
    model: str,
    provider: str,
    approval: str,
    sandbox: bool,
    git_status: str = "unknown",
    tokens: str = "-",
    cost: str = "-",
    elapsed: str = "-",
) -> str:
    sandbox_text = "Sandbox ON" if sandbox else "Sandbox OFF"
    return (
        f"{model} | {provider} | {tokens} tokens | {cost} | {elapsed} | "
        f"{git_status} | {approval} | {sandbox_text}"
    )

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
    
    if result.message:
        sections.append(_render_markdown(normal_result_message(result.message, blocked=result.blocked), max_chars=4000))

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
        for v in result.verification_results:
            label = v.get("purpose") or v.get("name") or "Verification"
            label = normal_result_message(str(label), blocked=not v.get("ok"))
            status_word = "Passed" if v.get("ok") else "Failed"
            status_text = Text(status_word, style="green" if v.get("ok") else "red")
            verification.add_row(str(label), status_text)
        sections.extend([Text("Verification", style="bold underline"), verification])

    process_items = _process_items(result)
    if process_items:
        processes = Table(
            show_header=True,
            header_style="bold cyan",
            box=box.SIMPLE,
            padding=(0, 2),
            expand=True,
        )
        processes.add_column("Activity", no_wrap=True)
        processes.add_column("Status", no_wrap=True)
        processes.add_column("Details", overflow="fold")
        for item in process_items:
            action = str(item["action"])
            activity = {
                "start_process": "Started", "stop_process": "Stopped",
                "restart_process": "Restarted", "send_process_input": "Updated",
            }.get(action, "Managed")
            detail = ", ".join(
                part.strip() for part in str(item["detail"]).strip(" ()").split(",")
                if part.strip().startswith(("port=", "ready="))
            )
            processes.add_row(activity, item["status"], detail)
        sections.extend([Text("Managed Processes", style="bold underline"), processes])

    disposition = getattr(result, "disposition", "success")
    title = "Stopped" if disposition == "terminal" else "Paused" if result.blocked else "Finished Work"
    duration = getattr(result, "duration", None)
    if duration is not None:
        title += f" [{duration}s]"

    print_renderable_panel(title, Group(*_with_spacers(sections)), style="yellow" if result.blocked else "green")

def print_error_card(title: str, lines: list[tuple[str, str]], suggestions: list[str]) -> None:
    text = Text()
    for label, detail in lines:
        if label:
            text.append(f"{label}\n", style="muted")
        text.append(f"{redact_secrets(detail)}\n\n", style="danger" if label == "Reason:" else "default")
    
    if suggestions:
        text.append("Suggested actions:\n", style="muted")
        for sug in suggestions:
            text.append(f"- {sug}\n", style="default")

    text.rstrip()
    print_renderable_panel(title, text, style="red")

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
    console.print(_render_markdown(normal_result_message(str(body))))
    console.print()


def _render_markdown(body: str, *, max_chars: int | None = None) -> Markdown:
    normalized = _normalize_panel_body(redact_secrets(body))
    if max_chars is not None and len(normalized) > max_chars:
        normalized = normalized[:max_chars].rstrip()
        if normalized.count("```") % 2:
            normalized += "\n```"
        normalized += "\n\n… output truncated"
    return Markdown(normalized, justify="left", hyperlinks=False)


def _normalize_panel_body(body: str) -> str:
    lines = [line.rstrip() for line in redact_secrets(body).strip().splitlines()]
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


def print_help_panel(by_category: dict[str, list[object]]) -> None:
    """Render single-page categorized help panel with icons."""
    from .command_registry import CATEGORY_ICONS

    sections: list[object] = []
    for category, commands in by_category.items():
        if not commands:
            continue
        icon = CATEGORY_ICONS.get(category, "•")
        header = Text(f"{icon} {category}", style="bold cyan")

        table = Table(show_header=False, box=None, padding=(0, 2, 0, 2), expand=True)
        table.add_column("Command", style="cyan", no_wrap=True, width=18)
        table.add_column("Description", style="default")

        for cmd in commands:
            name = getattr(cmd, "name", str(cmd))
            desc = getattr(cmd, "description", "")
            table.add_row(name, desc)

        sections.extend([header, table])

    footer = Text()
    footer.append("Tip: Use ", style="muted")
    footer.append("/advanced", style="bold cyan")
    footer.append(" to see all power-user commands.\n", style="muted")
    footer.append("Shortcuts: ", style="muted")
    footer.append("Ctrl+C", style="bold")
    footer.append(" Stop turn | ", style="muted")
    footer.append("Ctrl+E", style="bold")
    footer.append(" Exit session", style="muted")
    sections.append(footer)

    print_renderable_panel("Help", Group(*_with_spacers(sections)), style="cyan")


def print_advanced_panel(by_category: dict[str, list[object]]) -> None:
    """Render full power-user command reference by category with icons."""
    from .command_registry import CATEGORY_ICONS

    sections: list[object] = []
    for category, commands in by_category.items():
        if not commands:
            continue
        icon = CATEGORY_ICONS.get(category, "•")
        header = Text(f"{icon} {category}", style="bold cyan")

        table = Table(show_header=False, box=None, padding=(0, 2, 0, 2), expand=True)
        table.add_column("Command", style="cyan", no_wrap=True, width=22)
        table.add_column("Description", style="default")

        for cmd in commands:
            name = getattr(cmd, "name", str(cmd))
            desc = getattr(cmd, "description", "")
            badge = " [hidden]" if getattr(cmd, "hidden", False) else ""
            table.add_row(name + badge, desc)

        sections.extend([header, table])

    print_renderable_panel("Advanced Commands Reference", Group(*_with_spacers(sections)), style="magenta")


def print_keys_panel(
    provider_states: list[tuple[str, bool, str]],
    current_provider: str,
    current_model: str,
    fallback_chain: list[str],
) -> None:
    """Render API Key configuration panel with status, current model, and fallback chain."""
    table = Table(show_header=True, header_style="bold cyan", box=box.SIMPLE, padding=(0, 2), expand=True)
    table.add_column("Provider", style="bold", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Environment / Keyring", style="muted")

    for display_name, configured, env_var in provider_states:
        status_text = Text("✓ Configured", style="bold green") if configured else Text("✗ Missing", style="bold red")
        table.add_row(display_name, status_text, env_var or "keyring")

    sections: list[object] = [table]

    meta_table = Table.grid(expand=True, padding=(0, 2))
    meta_table.add_column(style="muted", no_wrap=True)
    meta_table.add_column()
    meta_table.add_row(Text("Current Provider", style="bold"), Text(current_provider, style="cyan"))
    meta_table.add_row(Text("Current Model", style="bold"), Text(current_model, style="cyan"))
    if fallback_chain:
        chain_str = " -> ".join(fallback_chain)
        meta_table.add_row(Text("Fallback Chain", style="bold"), Text(chain_str, style="default"))

    sections.append(meta_table)

    missing = [name for name, cfg, _ in provider_states if not cfg]
    if missing:
        guidance = Text()
        guidance.append("To add a missing key:\n", style="muted")
        guidance.append(f"  agent47 keys add {missing[0].lower().split()[0]}\n", style="bold yellow")
        guidance.append("  or set the environment variable in your .env file.", style="muted")
        sections.append(guidance)

    print_renderable_panel("API Keys", Group(*_with_spacers(sections)), style="cyan")


def print_status_panel(
    rows: list[tuple[str, str, str]],
) -> None:
    """Render enriched status panel hiding empty/unavailable rows."""
    table = Table.grid(expand=True, padding=(0, 3))
    table.add_column(style="muted", no_wrap=True)
    table.add_column()

    for category, key, value in rows:
        if not value or value == "none" or value == "unknown":
            continue
        label = f"{category} • {key}" if category else key
        table.add_row(Text(label, style="bold"), Text(value, style="default"))

    print_renderable_panel("Status", table, style="cyan")


def print_command_preview(meta: object) -> None:
    """Render detailed command preview card."""
    from .command_registry import CATEGORY_ICONS

    name = getattr(meta, "name", "/command")
    category = getattr(meta, "category", "General")
    icon = CATEGORY_ICONS.get(category, "•")
    desc = getattr(meta, "description", "")
    usage = getattr(meta, "usage", "")
    examples = getattr(meta, "examples", ())
    aliases = getattr(meta, "aliases", ())
    keywords = getattr(meta, "keywords", ())

    sections: list[object] = []
    
    header = Text()
    header.append(f"{icon} {name}\n", style="bold cyan")
    header.append(desc, style="default")
    sections.append(header)

    grid = Table.grid(expand=True, padding=(0, 2))
    grid.add_column(style="muted", no_wrap=True)
    grid.add_column()

    grid.add_row(Text("Category", style="bold"), Text(f"{icon} {category}"))

    if usage:
        grid.add_row(Text("Usage", style="bold"), Text(usage, style="yellow"))
    if examples:
        grid.add_row(Text("Examples", style="bold"), Text("\n".join(examples), style="green"))
    if aliases:
        grid.add_row(Text("Aliases", style="bold"), Text(", ".join(aliases), style="default"))
    if keywords:
        grid.add_row(Text("Keywords", style="bold"), Text(", ".join(keywords), style="muted"))

    sections.append(grid)

    print_renderable_panel(f"Command Info: {name}", Group(*_with_spacers(sections)), style="cyan")


def print_suggestions_panel(query: str, suggestions: list[object]) -> None:
    """Render fuzzy suggestion panel when an unknown command is typed."""
    from .command_registry import CATEGORY_ICONS

    text = Text()
    text.append(f"Unknown command: {query}\n\n", style="danger")

    if suggestions:
        text.append("Did you mean?\n", style="muted")
        for s in suggestions:
            name = getattr(s, "name", str(s))
            desc = getattr(s, "description", "")
            cat = getattr(s, "category", "")
            icon = CATEGORY_ICONS.get(cat, "•")
            text.append(f"  {icon} ", style="default")
            text.append(f"{name:<18}", style="bold cyan")
            text.append(f"{desc}\n", style="default")
    else:
        text.append("Type ", style="muted")
        text.append("/", style="bold cyan")
        text.append(" to browse available commands, or ", style="muted")
        text.append("/help", style="bold cyan")
        text.append(" for assistance.", style="muted")

    print_renderable_panel("Unknown Command", text, style="red")



def normal_result_message(message: str, *, blocked: bool = False) -> str:
    """Keep protocol failure details in history/debug, not normal terminal output."""
    import json

    safe = redact_secrets(message)
    try:
        parsed = json.loads(safe)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict) and "type" in parsed:
        return "The model returned an internal action instead of a user-facing response."
    if contains_technical_failure(safe):
        return "I could not complete this operation. Use /debug or /history for technical details."
    if contains_internal_protocol(safe):
        if "action_loop" in safe:
            return "I stopped after repeated actions without progress. Review the task or try a new approach."
        if "failed_strategy" in safe:
            return "I stopped because the attempted approach had already failed."
        return "I could not complete this step. Use /debug or /history for technical details."
    if blocked and "{" in safe and '"type"' in safe:
        return "The task stopped before completion. Use /debug or /history for technical details."
    return safe


def contains_technical_failure(message: str) -> bool:
    import re

    return bool(re.search(
        r"Traceback \(most recent call last\)|Exception in thread|Adapter failed|"
        r"\b(?:FileNotFoundError|OperationalError|PermissionError|RuntimeError|OSError)\b",
        message, re.I,
    ))


def contains_internal_protocol(message: str) -> bool:
    """Recognize protocol identifiers without hiding ordinary prose or source paths."""
    import re
    from typing import get_args
    from .schema import AgentAction

    identifiers = {
        "action_loop", "failed_strategy", "model_dump", "execution_state",
        "state_payload", "fingerprint", "reasoning_details",
    }
    for action in get_args(AgentAction):
        identifiers.update(
            value for value in get_args(action.model_fields["type"].annotation)
            if "_" in value
        )
    return bool(
        re.search(r"\b(?:" + "|".join(sorted(identifiers)) + r")\b", message)
        or re.search(r'["\']type["\']\s*:', message)
    )
