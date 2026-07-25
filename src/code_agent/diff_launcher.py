"""Launcher and integration helper for the interactive diff viewer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable

from rich.console import Console

from .diff_types import DiffViewMode
from .diff_viewer import DiffParseError, parse_unified_diff
from .diff_viewer_rows import build_diff_view_model
from .interactive_diff import show_diff
from .terminal_ui import console as default_console


class DiffLaunchStatus(Enum):
    SHOWN = auto()
    NO_DIFF = auto()
    PARSE_ERROR = auto()
    VIEWER_ERROR = auto()


@dataclass(frozen=True)
class DiffLaunchResult:
    status: DiffLaunchStatus
    success: bool
    message: str | None = None


def extract_unified_diff(detail: str) -> str | None:
    """Extract raw unified diff text from a permission detail string or raw diff."""
    if not detail or not detail.strip():
        return None
    if "Unified diff:" in detail:
        diff_part = detail.split("Unified diff:", 1)[1].lstrip()
        if diff_part.strip():
            return diff_part.strip() + "\n"
    lines = detail.splitlines()
    diff_start = None
    for i, line in enumerate(lines):
        if line.startswith("diff --git ") or line.startswith("--- "):
            diff_start = i
            break
    if diff_start is not None:
        return "\n".join(lines[diff_start:]).strip() + "\n"
    return None


def launch_diff_viewer(
    diff_text: str | None,
    *,
    mode: DiffViewMode | str | None = None,
    console: Console | None = None,
    read_key: Callable[[], Any] | None = None,
) -> DiffLaunchResult:
    """Parse unified diff, build view model, and launch the interactive diff viewer."""
    target_console = console or default_console
    if not diff_text or not diff_text.strip():
        msg = "No diff is currently available.\n\nRun a task that produces file changes first."
        return DiffLaunchResult(status=DiffLaunchStatus.NO_DIFF, success=False, message=msg)

    try:
        files = parse_unified_diff(diff_text)
        if not files:
            msg = "No file changes found in the diff."
            return DiffLaunchResult(status=DiffLaunchStatus.NO_DIFF, success=False, message=msg)
    except (DiffParseError, ValueError) as exc:
        msg = f"Could not parse diff: {exc}"
        return DiffLaunchResult(status=DiffLaunchStatus.PARSE_ERROR, success=False, message=msg)

    try:
        model = build_diff_view_model(files)
        view_mode = DiffViewMode.normalize(mode) if mode is not None else None
        show_diff(model, view_mode=view_mode, console=target_console, read_key=read_key)
        return DiffLaunchResult(status=DiffLaunchStatus.SHOWN, success=True)
    except Exception as exc:
        msg = f"Viewer error: {exc}"
        return DiffLaunchResult(status=DiffLaunchStatus.VIEWER_ERROR, success=False, message=msg)
