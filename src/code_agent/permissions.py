from __future__ import annotations

from collections.abc import Callable
from enum import Enum

import typer

MAX_PERMISSION_DETAIL_CHARS = 6000
MAX_PERMISSION_DETAIL_LINES = 120

READ_ONLY_ACTIONS = frozenset({
    "list_files",
    "read_file",
    "summarize_code",
    "repo_map",
    "rank_context",
    "symbol_index",
    "detect_verification",
    "suggest_verification",
})


HIGH_RISK_ACTIONS = frozenset({
    "apply_patch",
    "run_shell",
    "delete_file",
    "inspect_git_diff",
    "search",
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
        callback: Callable[[str, str], bool],
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
            return self._callback(action, detail)
        if self._mode == ApprovalMode.auto_read and action in READ_ONLY_ACTIONS:
            return True
        if self._mode == ApprovalMode.approve_task and self._task_approved:
            return True
        approved = self._callback(action, detail)
        if approved and self._mode == ApprovalMode.approve_task:
            self._task_approved = True
        return approved

    def reset_task(self) -> None:
        """Call between tasks to reset per-task approval state."""
        self._task_approved = False


def confirm_permission(action: str, detail: str) -> bool:
    typer.echo("")
    typer.echo(f"Permission requested: {action}")
    typer.echo(format_permission_detail(detail))
    return typer.confirm("Allow this action?", default=False)


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
