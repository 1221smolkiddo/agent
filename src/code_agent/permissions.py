from __future__ import annotations

from typing import Any, Callable
from enum import Enum

from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt

from .diff_launcher import extract_unified_diff, launch_diff_viewer
from .terminal_ui import console
from .safety import redact_command_for_display

MAX_PERMISSION_DETAIL_CHARS = 6000
MAX_PERMISSION_DETAIL_LINES = 120

READ_ONLY_ACTIONS = frozenset({
    "list_files",
    "read_file",
    "summarize_code",
    "repo_map",
    "rank_context",
    "symbol_index",
    "dependency_graph",
    "read_memory",
    "detect_verification",
    "suggest_verification",
    "list_transactions",
    "list_processes",
    "inspect_process",
    "read_process_logs",
    "process_events",
})


HIGH_RISK_ACTIONS = frozenset({
    "apply_patch",
    "run_shell",
    "start_process",
    "send_process_input",
    "stop_process",
    "restart_process",
    "delete_file",
    "move_file",
    "undo_transaction",
    "redo_transaction",
    "restore_snapshot",
    "recover_transactions",
    "inspect_git_diff",
    "search",
    "update_memory",
    "invoke_tool",
})

MANUAL_APPROVAL_ACTIONS = HIGH_RISK_ACTIONS


class ApprovalMode(str, Enum):
    per_action = "per_action"
    approve_task = "approve_task"
    auto_read = "auto_read"


class PermissionPolicy:
    """Wraps an interactive approval callback with policy-based auto-approval."""

    def __init__(
        self,
        callback: Callable[[str, str], str],
        mode: ApprovalMode = ApprovalMode.per_action,
        session_state: Any | None = None,
    ) -> None:
        self._callback = callback
        self._mode = mode
        self._task_approved = False
        self.session_state = session_state

    @property
    def mode(self) -> ApprovalMode:
        return self._mode

    def set_mode(self, mode: ApprovalMode) -> None:
        self._mode = mode
        self._task_approved = False

    def approve(self, action: str, detail: str, session_state: Any | None = None) -> bool:
        state = session_state or self.session_state
        extracted = extract_unified_diff(detail)
        if extracted and state is not None and hasattr(state, "set_last_diff"):
            state.set_last_diff(extracted)

        if action in MANUAL_APPROVAL_ACTIONS:
            response = self._invoke_callback(action, detail, state)
            return response in {"y", "a"}  # Even if 'a', it only approves this action because it's high risk
            
        if self._mode == ApprovalMode.auto_read and action in READ_ONLY_ACTIONS:
            return True
        if self._mode == ApprovalMode.approve_task and self._task_approved:
            return True
            
        response = self._invoke_callback(action, detail, state)
        if response == "a":
            self._mode = ApprovalMode.approve_task
            self._task_approved = True
            return True
            
        return response == "y"

    def _invoke_callback(self, action: str, detail: str, state: Any | None) -> str:
        try:
            return self._callback(action, detail, session_state=state)
        except TypeError:
            return self._callback(action, detail)

    def reset_task(self) -> None:
        """Call between tasks to reset per-task approval state."""
        self._task_approved = False


def confirm_permission(
    action: str,
    detail: str,
    session_state: Any | None = None,
    read_key: Callable[[], Any] | None = None,
) -> str:
    action_labels = {
        "run_shell": "Run shell command",
        "start_process": "Start managed process",
        "list_processes": "List managed processes",
        "inspect_process": "Inspect managed process",
        "read_process_logs": "Read managed process logs",
        "process_events": "Read managed process events",
        "send_process_input": "Send process input",
        "stop_process": "Stop managed process",
        "restart_process": "Restart managed process",
        "delete_file": "Delete file",
        "move_file": "Move file",
        "list_transactions": "List transactions",
        "undo_transaction": "Undo transaction",
        "redo_transaction": "Redo transaction",
        "restore_snapshot": "Restore workspace snapshot",
        "recover_transactions": "Recover interrupted transactions",
        "apply_patch": "Apply patch",
        "inspect_git_diff": "Inspect git diff",
        "search": "Search workspace",
        "list_files": "List files",
        "read_file": "Read file",
        "summarize_code": "Summarize code",
        "repo_map": "Inspect repository map",
        "rank_context": "Rank context",
        "symbol_index": "Index symbols",
        "dependency_graph": "Analyze dependency graph",
        "read_memory": "Read project memory",
        "update_memory": "Update project memory",
        "detect_verification": "Detect verification commands",
        "suggest_verification": "Suggest verification",
        "git_branch": "Create git branch",
        "git_commit": "Create git commit",
        "invoke_tool": "Invoke dynamic tool",
    }
    label = action_labels.get(action, action.replace("_", " ").title())

    extracted_diff = extract_unified_diff(detail)
    if extracted_diff and session_state is not None and hasattr(session_state, "set_last_diff"):
        session_state.set_last_diff(extracted_diff)

    text = Text()
    text.append(f"{label}\n\n", style="bold")

    preview_detail = detail
    if action in {"run_shell", "start_process"}:
        command_line = next((line for line in detail.splitlines() if line.startswith("Command: ")), None)
        if command_line is not None:
            text.append("Command:\n", style="bold")
            text.append(redact_command_for_display(command_line.removeprefix("Command: ")) + "\n\n")
            preview_detail = "\n".join(line for line in detail.splitlines() if line != command_line)
    preview = _format_permission_preview(preview_detail)
    if preview:
        text.append("Preview:\n", style="muted")
        text.append(f"{preview}\n", style="default")
    text.append("\nChoose:\n", style="muted")
    text.append("  y", style="bold green")
    text.append(" approve once\n", style="default")
    text.append("  n", style="bold red")
    text.append(" deny\n", style="default")
    if action not in MANUAL_APPROVAL_ACTIONS:
        text.append("  a", style="bold cyan")
        text.append(" approve all low-risk actions for this task\n", style="default")
    
    has_diff = bool(extracted_diff or (session_state and getattr(session_state, "last_diff", None)))
    text.append("  v", style="bold yellow")
    if has_diff:
        text.append(" view interactive diff\n", style="default")
    else:
        text.append(" view full detail\n", style="default")

    console.print(
        Panel(
            text,
            title=Text("Permission Required", style="bold yellow"),
            border_style="yellow",
        )
    )

    # Keep the default prompt compact; full raw detail is explicit via "v".
    while True:
        response = Prompt.ask(
            _permission_prompt(action),
            choices=["y", "n", "a", "v"],
            default="n",
            show_choices=False,
            show_default=True,
            console=console,
        )
        if response == "v":
            diff_text = extracted_diff or (getattr(session_state, "last_diff", None) if session_state else None)
            if diff_text:
                mode = getattr(session_state, "preferred_diff_mode", None)
                res = launch_diff_viewer(
                    diff_text, mode=mode, console=console, read_key=read_key
                )
                if not res.success and res.message:
                    console.print(
                        Panel(
                            Text(format_permission_detail(detail)),
                            title=Text(f"Full Detail ({res.message})", style="bold"),
                            border_style="muted",
                        )
                    )
            else:
                console.print(
                    Panel(
                        Text(format_permission_detail(detail)),
                        title=Text("Full Detail", style="bold"),
                        border_style="muted",
                    )
                )
            continue
        if response == "a" and action in MANUAL_APPROVAL_ACTIONS:
            console.print("Approve-all is disabled for high-risk actions. Choose y or n.", style="warning")
            continue
        return response



def _permission_prompt(action: str) -> str:
    if action in MANUAL_APPROVAL_ACTIONS:
        return "[bold yellow]Approve?[/bold yellow] [green]y[/green]/[red]n[/red]/[yellow]v[/yellow]"
    return "[bold yellow]Approve?[/bold yellow] [green]y[/green]/[red]n[/red]/[cyan]a[/cyan]/[yellow]v[/yellow]"


def _format_permission_preview(detail: str, max_lines: int = 6, max_chars: int = 800) -> str:
    """Generate a short preview for permission details.

    If `detail` looks like a unified diff, extract added lines (or a small snippet).
    Otherwise, return the first non-empty lines up to `max_lines`.
    """
    if not detail:
        return ""
    # Heuristic: detect diff markers
    diff_markers = ("diff --git", "@@", "--- ", "+++ ")
    lines = detail.splitlines()
    if "Unified diff:" in lines:
        lines = lines[: lines.index("Unified diff:")]
    if any(any(marker in line for marker in diff_markers) for line in lines):
        summary = _format_diff_summary(lines)
        if summary:
            return summary

    # Fallback: show the first few meaningful lines
    meaningful = [ln for ln in lines if ln.strip()][:max_lines]
    if meaningful:
        preview = "\n".join(meaningful)
        if len(preview) > max_chars:
            preview = preview[:max_chars].rstrip() + "\n..."
        return preview
    # As last resort, truncate the raw detail
    truncated = detail[:max_chars]
    if len(detail) > max_chars:
        truncated = truncated.rstrip() + "\n..."
    return truncated


def _format_diff_summary(lines: list[str]) -> str:
    paths: list[str] = []
    additions = 0
    deletions = 0
    for line in lines:
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path != "/dev/null":
                paths.append(path.removeprefix("b/"))
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1

    parts: list[str] = []
    if paths:
        unique_paths = list(dict.fromkeys(paths))
        parts.append("Files: " + ", ".join(unique_paths[:6]))
        if len(unique_paths) > 6:
            parts.append(f"...and {len(unique_paths) - 6} more")
    parts.append(f"Changes: +{additions} -{deletions}")
    return "\n".join(parts)


def confirm_permission_with_policy(
    policy: PermissionPolicy,
) -> Callable[[str, str], bool]:
    """Return a callback that delegates to the policy."""
    return policy.approve


def format_permission_detail(
    detail: str,
    *,
    max_chars: int = MAX_PERMISSION_DETAIL_CHARS,
    max_lines: int = MAX_PERMISSION_DETAIL_LINES,
) -> str:
    lines = detail.splitlines()
    truncated_by_lines = len(lines) > max_lines
    rendered = "\n".join(lines[:max_lines])
    truncated_by_chars = len(rendered) > max_chars
    if truncated_by_chars:
        rendered = rendered[:max_chars].rstrip()

    omitted_lines = len(lines) - max_lines if truncated_by_lines else 0
    omitted_chars = len(detail) - len(rendered) if truncated_by_chars else 0
    if omitted_lines or omitted_chars:
        suffix_parts = []
        if omitted_lines:
            suffix_parts.append(f"{omitted_lines} lines")
        if omitted_chars:
            suffix_parts.append(f"{omitted_chars} chars")
        rendered += "\n<preview truncated: " + ", ".join(suffix_parts) + " omitted>"
    return rendered
