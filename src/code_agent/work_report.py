from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent import AgentRunResult


def should_show_work_report(result: AgentRunResult) -> bool:
    did_real_work = bool(
        result.plan_updates
        or result.changed_paths
        or result.mutation_records
        or result.command_records
        or result.verification_results
        or result.context_records
    )
    return bool(
        did_real_work
        or ((result.failed_actions or result.denied_actions or result.blocked) and did_real_work)
    )


def build_work_report_payload(result: AgentRunResult) -> dict[str, Any]:
    body = format_work_report_body(result)
    task_text = _clean_task_text(result)
    sections = {
        "current_task": _single_line(task_text) or "<not recorded>",
        "current_step": _current_step(result.plan_updates),
        "files_being_modified": result.changed_paths,
        "progress": _progress_summary(result),
        "planned_target_files": _latest_plan_list(result.plan_updates, "target_files"),
        "owned_files": _latest_plan_list(result.plan_updates, "owned_files"),
        "planned_checks": _latest_plan_list(result.plan_updates, "checks"),
        "blockers": _latest_plan_list(result.plan_updates, "blockers"),
        "risk_notes": _latest_plan_list(result.plan_updates, "risk_notes"),
        "context_analysis": _context_items(result),
        "model_usage": _model_usage_items(result),
        "commands_executed": _command_items(result),
        "managed_processes": _process_items(result),
        "command_diagnostics": _diagnostic_items(result),
        "validation_status": _validation_items(result),
        "reviewer_pass": _reviewer_items(result),
        "modified_files": result.changed_paths,
        "change_summary": _change_items(result),
        "transactions": _transaction_items(result),
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
    sections = _meaningful_sections(result)
    lines: list[str] = []
    for title, body in sections:
        if not body.strip():
            continue
        lines.append(f"{title}:")
        lines.extend(f"  {line}" for line in body.splitlines())
        lines.append("")
    return "\n".join(lines).rstrip()


def _meaningful_sections(result: AgentRunResult) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    progress = _progress_summary(result)
    if progress:
        sections.append(("Plan", progress))
    targets = _latest_plan_list(result.plan_updates, "target_files")
    if targets:
        sections.append(("Intended Files", _list_or_empty(targets)))
    # Reconcile intended vs actual
    if targets and result.changed_paths:
        intended_set = set(targets)
        actual_set = set(result.changed_paths)
        missed = sorted(intended_set - actual_set)
        extra = sorted(actual_set - intended_set)
        if missed:
            sections.append(("Intended But Not Modified", _list_or_empty(missed)))
        if extra:
            sections.append(("Additionally Modified", _list_or_empty(extra)))
    changes = _compact_change_summary(result)
    if changes:
        sections.append(("Changes", changes))
    transactions = _transactions_summary(result)
    if transactions:
        sections.append(("Transactions", transactions))
    context = _context_summary(result)
    if context:
        sections.append(("Analyzed", context))
    commands = _commands_summary(result)
    if commands:
        sections.append(("Commands Executed", commands))
    processes = _processes_summary(result)
    if processes:
        sections.append(("Managed Processes", processes))
    diagnostics = _diagnostics_summary(result)
    if diagnostics:
        sections.append(("Diagnostics", diagnostics))
    validation = _validation_summary(result)
    if validation:
        sections.append(("Validation", validation))
    reviewer = _reviewer_summary(result)
    if reviewer:
        sections.append(("Reviewer", reviewer))
    blockers = _latest_plan_list(result.plan_updates, "blockers")
    if blockers:
        sections.append(("Blockers", _list_or_empty(blockers)))
    risks = _latest_plan_list(result.plan_updates, "risk_notes")
    if risks:
        sections.append(("Risks", _list_or_empty(risks)))
    outcome = _single_line(result.message, max_chars=900)
    if outcome:
        sections.append(("Result", outcome))
    return sections


def _current_step(plan_updates: list[dict[str, Any]]) -> str:
    if not plan_updates:
        return ""
    raw_steps = plan_updates[-1].get("steps", [])
    if not isinstance(raw_steps, list):
        return ""
    for item in raw_steps:
        if isinstance(item, dict) and item.get("status") == "in_progress":
            return str(item.get("step", "<unnamed step>"))
    for item in reversed(raw_steps):
        if isinstance(item, dict) and item.get("status") in {"blocked", "completed"}:
            return str(item.get("step", "<unnamed step>"))
    return ""


def _progress_summary(result: AgentRunResult) -> str:
    if not result.plan_updates:
        return ""
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


def _latest_plan_list(plan_updates: list[dict[str, Any]], key: str) -> list[str]:
    if not plan_updates:
        return []
    values = plan_updates[-1].get(key, [])
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if str(value).strip()]


def _commands_summary(result: AgentRunResult) -> str:
    commands = _command_items(result)
    return "\n".join(
        f"- `{item['command']}`: {item['status']}{item['detail']}" for item in commands
    )


def _processes_summary(result: AgentRunResult) -> str:
    return "\n".join(
        f"- `{item['id']}`: {item['action']} → {item['status']}{item['detail']}"
        for item in _process_items(result)
    )


def _process_items(result: AgentRunResult) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for record in result.command_records:
        if record.get("kind") != "managed_process":
            continue
        process = record.get("process")
        process = process if isinstance(process, dict) else {}
        process_id = str(process.get("process_id") or "<pending>")
        status = str(process.get("status") or record.get("status") or "unknown")
        details = []
        if process.get("pid"):
            details.append(f"pid={process['pid']}")
        if process.get("detected_port"):
            details.append(f"port={process['detected_port']}")
        if process.get("ready") is not None:
            details.append(f"ready={str(process['ready']).lower()}")
        items.append(
            {
                "id": process_id,
                "action": str(record.get("action") or "process"),
                "status": status,
                "detail": f" ({', '.join(details)})" if details else "",
            }
        )
    return items


def _context_summary(result: AgentRunResult) -> str:
    items = _context_items(result)
    return "\n".join(
        f"- {item['action']}: {item['status']}{item['detail']}" for item in items
    )


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
    verification_commands = {
        str(item.get("command", "")).strip()
        for item in result.verification_results
        if str(item.get("command", "")).strip()
    }
    for item in result.command_records:
        command = str(item.get("command", "")).strip()
        if not command or command in seen or command in verification_commands:
            continue
        seen.add(command)
        status = str(item.get("status", "passed" if item.get("ok") else "failed"))
        execution = item.get("execution")
        duration = ""
        if isinstance(execution, dict) and execution.get("duration_ms") is not None:
            duration = f" ({float(execution['duration_ms']):.0f}ms)"
        commands.append({"command": command, "status": status, "detail": duration})
    return commands


def _diagnostics_summary(result: AgentRunResult) -> str:
    return "\n".join(
        f"- {item['location']}{item['severity']} {item['category']}: {item['message']}"
        f"{item['history']}"
        for item in _diagnostic_items(result)
    )


def _diagnostic_items(result: AgentRunResult) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for record in result.command_records:
        report = record.get("diagnostics")
        if not isinstance(report, dict):
            continue
        history = report.get("history")
        history_suffix = ""
        if isinstance(history, dict) and history.get("recurring"):
            history_suffix = (
                f" (recurring; {int(history.get('prior_occurrences', 0))} prior occurrence(s))"
            )
        for item in report.get("diagnostics", []):
            if not isinstance(item, dict):
                continue
            location = str(item.get("location") or "")
            message = _single_line(str(item.get("message") or ""), max_chars=200)
            key = (location, str(item.get("rule") or ""), message)
            if key in seen:
                continue
            seen.add(key)
            output.append(
                {
                    "location": f"`{location}` " if location else "",
                    "severity": str(item.get("severity") or "error"),
                    "category": str(item.get("category") or "unknown"),
                    "message": message,
                    "history": history_suffix,
                }
            )
            if len(output) >= 20:
                return output
    return output


def _model_usage_summary(result: AgentRunResult) -> str:
    items = _model_usage_items(result)
    return "\n".join(
        f"- {item['model']}: {item['status']}, tokens={item['total_tokens']}, "
        f"cost={item['estimated_cost_usd']}{item['fallback']}"
        for item in items
    ) or "<none>"


def _model_usage_items(result: AgentRunResult) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for record in result.model_usage_records:
        total_tokens = record.get("total_tokens")
        estimated_cost = record.get("estimated_cost_usd")
        fallback_from = str(record.get("fallback_from") or "")
        fallback = f", fallback_from={fallback_from}" if fallback_from else ""
        error = str(record.get("error") or "")
        status = "ok" if record.get("ok") is True else f"failed: {_single_line(error, max_chars=120)}"
        items.append(
            {
                "model": str(record.get("model", "<unknown>")),
                "status": status,
                "total_tokens": str(total_tokens if total_tokens is not None else "<unknown>"),
                "estimated_cost_usd": str(
                    estimated_cost if estimated_cost is not None else "<not configured>"
                ),
                "fallback": fallback,
            }
        )
    return items


def _validation_summary(result: AgentRunResult) -> str:
    items = _validation_items(result)
    return "\n".join(
        f"- {item['purpose']} `{item['command']}`: {item['status']}{item['detail']}" for item in items
    )


def _validation_items(result: AgentRunResult) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for item in result.verification_results:
        items.append(
            {
                "purpose": str(item.get("purpose", "check")),
                "command": str(item.get("command", "<unknown>")),
                "status": str(item.get("status", "passed" if item.get("ok") else "failed")),
                "detail": _verification_detail(item),
            }
        )
    return items


def _reviewer_summary(result: AgentRunResult) -> str:
    items = _reviewer_items(result)
    return "\n".join(
        f"- {item['decision']}: {item['summary']}{item['detail']}" for item in items
    )


def _reviewer_items(result: AgentRunResult) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for record in result.review_records:
        decision = "approved" if record.get("ok") is True else "rejected"
        summary = _single_line(str(record.get("summary", "<no summary>")), max_chars=200)
        issues = record.get("issues", [])
        required_actions = record.get("required_actions", [])
        detail_parts: list[str] = []
        if issues:
            detail_parts.append("issues: " + "; ".join(str(i) for i in issues))
        if required_actions:
            detail_parts.append("actions: " + "; ".join(str(a) for a in required_actions))
        detail = " (" + ", ".join(detail_parts) + ")" if detail_parts else ""
        items.append({"decision": decision, "summary": summary, "detail": detail})
    return items


def _verification_detail(item: dict[str, Any]) -> str:
    diagnostics = item.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return ""
    summary = _single_line(str(diagnostics.get("summary", "")), max_chars=160)
    return f" - {summary}" if summary else ""


def _change_summary(result: AgentRunResult) -> str:
    changes = _change_items(result)
    return "\n".join(
        f"- {item['action']} {item['path']}: {item['status']}{item['detail']}" for item in changes
    )


def _transactions_summary(result: AgentRunResult) -> str:
    return "\n".join(
        f"- `{item['id']}`: {item['state']} ({item['paths']})"
        for item in _transaction_items(result)
    )


def _transaction_items(result: AgentRunResult) -> list[dict[str, str]]:
    grouped: dict[str, dict[str, str]] = {}
    for record in result.mutation_records:
        transaction_id = str(record.get("transaction_id") or "")
        if not transaction_id:
            continue
        item = grouped.setdefault(
            transaction_id,
            {
                "id": transaction_id,
                "state": str(record.get("transaction_state") or "unknown"),
                "paths": "",
            },
        )
        paths = [path for path in item["paths"].split(", ") if path]
        path = str(record.get("path") or "")
        if path and path not in paths:
            paths.append(path)
        item["paths"] = ", ".join(paths)
    return list(grouped.values())


def _compact_change_summary(result: AgentRunResult) -> str:
    """Compact checklist-style change summary for user-facing reports."""
    items = _change_items_filtered(result)
    lines: list[str] = []
    for item in items:
        marker = "✓" if item["status"] == "ok" else "✗"
        action_label = _friendly_action_label(item["action"])
        line = f"{marker} {action_label} {item['path']}"
        detail = item.get("detail", "")
        if detail:
            line += f" {detail}"
        lines.append(line)
    return "\n".join(lines)


def _friendly_action_label(action: str) -> str:
    return {
        "write_file": "Created",
        "edit_file": "Modified",
        "apply_patch": "Patched",
        "delete_file": "Deleted",
        "move_file": "Moved",
    }.get(action, action.replace("_", " ").capitalize())


def _change_items_filtered(result: AgentRunResult) -> list[dict[str, str]]:
    """Return change items, hiding failed attempts when a success exists for the same path."""
    all_items = _change_items(result)
    successful_paths = {
        item["path"] for item in all_items if item["status"] == "ok"
    }
    filtered: list[dict[str, str]] = []
    seen_ok: set[str] = set()
    retry_count: dict[str, int] = {}
    for item in all_items:
        path = item["path"]
        if item["status"] != "ok" and path in successful_paths:
            retry_count[path] = retry_count.get(path, 0) + 1
            continue
        if item["status"] == "ok" and path in seen_ok:
            continue
        seen_ok.add(path)
        if path in retry_count:
            item = {**item, "detail": f"({retry_count[path]} retries)"}
        filtered.append(item)
    return filtered


def _change_items(result: AgentRunResult) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    for record in result.mutation_records:
        changes.append(
            {
                "action": str(record.get("action", "change")),
                "path": str(record.get("path", "<unknown>")),
                "status": "ok" if record.get("ok") is True else "failed",
                "detail": _patch_detail(record),
            }
        )
    return changes


def _patch_detail(record: dict[str, Any]) -> str:
    patch = record.get("patch")
    if not isinstance(patch, dict):
        return ""
    operation = str(patch.get("operation", "")).strip()
    additions = patch.get("additions")
    deletions = patch.get("deletions")
    if not operation and additions is None and deletions is None:
        return ""
    return f"(+{additions} -{deletions})"


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


def _list_or_empty(items: list[str]) -> str:
    return ", ".join(items)


def _single_line(value: str, max_chars: int = 500) -> str:
    rendered = " ".join(value.split())
    if len(rendered) <= max_chars:
        return rendered
    return rendered[:max_chars].rstrip() + " <truncated>"


def _clean_task_text(result: AgentRunResult) -> str:
    """Return user-facing task text, preferring clean_task over raw task."""
    return getattr(result, "clean_task", "") or result.task
