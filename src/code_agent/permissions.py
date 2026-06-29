from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt

from .terminal_ui import console

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
})


HIGH_RISK_ACTIONS = frozenset({
    "apply_patch",
    "run_shell",
    "delete_file",
    "inspect_git_diff",
    "search",
    "update_memory",
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
    ) -> None:
        self._callback = callback
        self._mode = mode
        self._task_approved = False

    @property
    def mode(self) -> ApprovalMode:
        return self._mode

    def set_mode(self, mode: ApprovalMode) -> None:
        self._mode = mode
        self._task_approved = False

    def approve(self, action: str, detail: str) -> bool:
        if action in MANUAL_APPROVAL_ACTIONS:
            response = self._callback(action, detail)
            return response in {"y", "a"}  # Even if 'a', it only approves this action because it's high risk
            
        if self._mode == ApprovalMode.auto_read and action in READ_ONLY_ACTIONS:
            return True
        if self._mode == ApprovalMode.approve_task and self._task_approved:
            return True
            
        response = self._callback(action, detail)
        if response == "a":
            self._mode = ApprovalMode.approve_task
            self._task_approved = True
            return True
            
        return response == "y"

    def reset_task(self) -> None:
        """Call between tasks to reset per-task approval state."""
        self._task_approved = False


def confirm_permission(action: str, detail: str) -> str:
    action_labels = {
        "run_shell": "Run shell command",
        "delete_file": "Delete file",
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
    }
    label = action_labels.get(action, action.replace("_", " ").title())

    text = Text()
    text.append(f"{label}\n\n", style="bold")

    preview = _format_permission_preview(detail)
    if preview:
        text.append("Preview:\n", style="muted")
        text.append(f"{preview}\n", style="default")

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
            r"\[y] Approve  \[n] Deny  \[a] Approve All For Task  \[v] View Full Detail",
            choices=["y", "n", "a", "v"],
            default="n",
            show_choices=False,
        )
        if response == "v":
            console.print(
                Panel(
                    Text(format_permission_detail(detail)),
                    title=Text("Full Detail", style="bold"),
                    border_style="muted",
                )
            )
            continue
        return response


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
