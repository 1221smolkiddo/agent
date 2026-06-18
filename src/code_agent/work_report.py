from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent import AgentRunResult


def should_show_work_report(result: AgentRunResult) -> bool:
    return bool(
        result.plan_updates
        or result.changed_paths
        or result.mutation_records
        or result.command_records
        or result.verification_results
        or result.context_records
        or result.failed_actions
        or result.denied_actions
        or result.blocked
    )


def build_work_report_payload(result: AgentRunResult) -> dict[str, Any]:
    body = format_work_report_body(result)
    sections = {
        "current_task": _single_line(result.task) or "<not recorded>",
        "current_step": _current_step(result.plan_updates),
        "files_being_modified": result.changed_paths,
        "progress": _progress_summary(result),
        "context_analysis": _context_items(result),
        "commands_executed": _command_items(result),
        "validation_status": _validation_items(result),
        "modified_files": result.changed_paths,
        "change_summary": _change_items(result),
        "diff_review": _diff_review_lines(result),
        "final_outcome": _single_line(result.message, max_chars=900),
    }
    return {
        "type": "work_report",
        "run_id": result.run_id,
        "body": body,
        "sections": sections,
    }


def format_work_report_body(result: AgentRunResult) -> str:
    sections = [
        ("Current Task", _single_line(result.task) or "<not recorded>"),
        ("Current Step", _current_step(result.plan_updates)),
        ("Files Being Modified", _list_or_none(result.changed_paths)),
        ("Progress", _progress_summary(result)),
        ("Context Analysis", _context_summary(result)),
        ("Commands Executed", _commands_summary(result)),
        ("Validation Status", _validation_summary(result)),
        ("Modified Files", _list_or_none(result.changed_paths)),
        ("Change Summary", _change_summary(result)),
        ("Diff Review", _diff_review(result)),
        ("Final Outcome", _single_line(result.message, max_chars=900)),
    ]
    lines: list[str] = []
    for title, body in sections:
        lines.append(f"{title}:")
        lines.extend(f"  {line}" for line in body.splitlines())
        lines.append("")
    return "\n".join(lines).rstrip()


def _current_step(plan_updates: list[dict[str, Any]]) -> str:
    if not plan_updates:
        return "<none>"
    raw_steps = plan_updates[-1].get("steps", [])
    if not isinstance(raw_steps, list):
        return "<none>"
    for item in raw_steps:
        if isinstance(item, dict) and item.get("status") == "in_progress":
            return str(item.get("step", "<unnamed step>"))
    for item in reversed(raw_steps):
        if isinstance(item, dict) and item.get("status") in {"blocked", "completed"}:
            return str(item.get("step", "<unnamed step>"))
    return "<none>"


def _progress_summary(result: AgentRunResult) -> str:
    if not result.plan_updates:
        return "No durable plan was recorded."
    raw_steps = result.plan_updates[-1].get("steps", [])
    if not isinstance(raw_steps, list) or not raw_steps:
        return "Plan updated."
    counts = {"completed": 0, "in_progress": 0, "blocked": 0, "pending": 0}
    for item in raw_steps:
        if isinstance(item, dict):
            status = str(item.get("status", "pending"))
            if status in counts:
                counts[status] += 1
    return (
        f"{counts['completed']} completed, {counts['in_progress']} current, "
        f"{counts['pending']} pending, {counts['blocked']} blocked."
    )


def _commands_summary(result: AgentRunResult) -> str:
    commands = _command_items(result)
    return "\n".join(f"- `{item['command']}`: {item['status']}" for item in commands) or "<none>"


def _context_summary(result: AgentRunResult) -> str:
    items = _context_items(result)
    return "\n".join(
        f"- {item['action']}: {item['status']}{item['detail']}" for item in items
    ) or "<none>"


def _context_items(result: AgentRunResult) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for record in result.context_records:
        action = str(record.get("action", "context"))
        status = str(record.get("status", "ok" if record.get("ok") else "failed"))
        task = _single_line(str(record.get("task", "")), max_chars=120)
        detail = f" for `{task}`" if task else ""
        items.append({"action": action, "status": status, "detail": detail})
    return items


def _command_items(result: AgentRunResult) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in [*result.command_records, *result.verification_results]:
        command = str(item.get("command", "")).strip()
        if not command or command in seen:
            continue
        seen.add(command)
        status = str(item.get("status", "passed" if item.get("ok") else "failed"))
        commands.append({"command": command, "status": status})
    return commands


def _validation_summary(result: AgentRunResult) -> str:
    items = _validation_items(result)
    return "\n".join(
        f"- {item['purpose']} `{item['command']}`: {item['status']}" for item in items
    ) or "Not run."


def _validation_items(result: AgentRunResult) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for item in result.verification_results:
        items.append(
            {
                "purpose": str(item.get("purpose", "check")),
                "command": str(item.get("command", "<unknown>")),
                "status": str(item.get("status", "passed" if item.get("ok") else "failed")),
            }
        )
    return items


def _change_summary(result: AgentRunResult) -> str:
    changes = _change_items(result)
    return "\n".join(
        f"- {item['action']} {item['path']}: {item['status']}" for item in changes
    ) or "<none>"


def _change_items(result: AgentRunResult) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    for record in result.mutation_records:
        changes.append(
            {
                "action": str(record.get("action", "change")),
                "path": str(record.get("path", "<unknown>")),
                "status": "ok" if record.get("ok") is True else "failed",
            }
        )
    return changes


def _diff_review(result: AgentRunResult, max_lines: int = 80) -> str:
    lines = _diff_review_lines(result, max_lines=max_lines)
    return "\n".join(lines) if lines else "<none>"


def _diff_review_lines(result: AgentRunResult, max_lines: int = 80) -> list[str]:
    lines: list[str] = []
    for record in result.mutation_records:
        output = str(record.get("output", ""))
        for line in output.splitlines():
            if _is_diff_review_line(line):
                lines.append(line)
            if len(lines) >= max_lines:
                lines.append(f"<diff review truncated after {max_lines} changed lines>")
                return lines
    return lines


def _is_diff_review_line(line: str) -> bool:
    return (
        line.startswith("@@")
        or line.startswith("--- ")
        or line.startswith("+++ ")
        or (line.startswith("+") and not line.startswith("+++"))
        or (line.startswith("-") and not line.startswith("---"))
    )


def _list_or_none(items: list[str]) -> str:
    return ", ".join(items) if items else "<none>"


def _single_line(value: str, max_chars: int = 500) -> str:
    rendered = " ".join(value.split())
    if len(rendered) <= max_chars:
        return rendered
    return rendered[:max_chars].rstrip() + " <truncated>"
