from __future__ import annotations

import json
import sqlite3
from typing import Any


def format_run_detail(
    run: sqlite3.Row,
    steps: list[dict[str, Any]],
    work_report: dict[str, Any] | None = None,
) -> str:
    lines = [
        f"Run {run['id']}",
        f"Created: {run['created_at']}",
        f"Model: {run['model']}",
        f"Workspace: {run['cwd']}",
        f"Task: {run['task']}",
        "",
    ]
    if work_report:
        lines.extend(["Work Report:", indent(str(work_report["body"])), ""])
    lines.append("Steps:")
    if not steps:
        lines.append("<none>")
        return "\n".join(lines)

    for index, step in enumerate(steps, start=1):
        payload = step["payload"]
        lines.append(f"{index}. {step['role']} {step['created_at']}")
        lines.append(indent(_summarize_payload(payload)))
    return "\n".join(lines)


def build_resume_task(
    run: sqlite3.Row,
    steps: list[dict[str, Any]],
    instruction: str | None = None,
    work_report: dict[str, Any] | None = None,
) -> str:
    sections = [
        f"Resume Agent47 run {run['id']}.",
        "",
        "Original task:",
        run["task"],
        "",
        "Prior run context:",
        compact_run_context(steps),
    ]
    if work_report:
        sections.extend(["", "Prior work report:", str(work_report["body"])])
    sections.extend(
        [
            "",
            "Continue from this state. Respect any prior denied permissions, failed tools, changed files, "
            "and verification outcomes. Re-inspect files or git state before editing if the workspace may have changed.",
        ]
    )
    if instruction:
        sections.extend(["", "New user instruction for this resume:", instruction])
    return "\n".join(sections)


def compact_run_context(steps: list[dict[str, Any]], max_chars: int = 12000) -> str:
    if not steps:
        return "<no prior steps were stored>"

    lines: list[str] = []
    for index, step in enumerate(steps, start=1):
        payload = step["payload"]
        summary = _summarize_payload(payload)
        lines.append(f"{index}. {step['role']}: {summary}")

    output = "\n".join(lines)
    if len(output) <= max_chars:
        return output
    remaining = len(output) - max_chars
    return output[:max_chars].rstrip() + f"\n<truncated {remaining} chars from prior run>"


def _summarize_payload(payload: dict[str, Any]) -> str:
    payload_type = payload.get("type")
    if payload_type == "tool_result":
        return _summarize_tool_result(payload)
    if payload_type == "plan_updated":
        return _summarize_plan_update(payload)
    if payload_type == "automatic_verification_result":
        return (
            "automatic verification "
            f"{payload.get('purpose', '<unknown>')} `{payload.get('command', '<unknown>')}` "
            f"{payload.get('status', 'unknown')}"
        )
    if payload_type == "execution_state":
        return _summarize_execution_state(payload)
    if payload_type == "final":
        return f"final: {payload.get('message', '')}"
    if payload_type:
        return _summarize_action(payload)
    if "raw" in payload:
        return _single_line(f"raw assistant response: {payload['raw']}")
    return _single_line(json.dumps(payload, sort_keys=True))


def _summarize_action(payload: dict[str, Any]) -> str:
    action_type = payload.get("type", "<unknown>")
    path = payload.get("path")
    command = payload.get("command")
    query = payload.get("query")
    if path:
        return f"action {action_type} path={path}"
    if command:
        return f"action {action_type} command={command}"
    if query:
        return f"action {action_type} query={query}"
    if action_type == "apply_patch":
        return "action apply_patch"
    if action_type == "update_plan":
        steps = payload.get("steps", [])
        statuses = [
            f"{step.get('status', 'unknown')}:{step.get('step', '<unnamed>')}" for step in steps
        ]
        return "action update_plan " + "; ".join(statuses) + _plan_metadata_suffix(payload)
    return f"action {action_type}"


def _summarize_plan_update(payload: dict[str, Any]) -> str:
    steps = payload.get("steps", [])
    if not steps:
        return "plan updated"
    rendered = [
        f"{step.get('status', 'unknown')}:{step.get('step', '<unnamed>')}" for step in steps
    ]
    return "plan updated " + "; ".join(rendered) + _plan_metadata_suffix(payload)


def _plan_metadata_suffix(payload: dict[str, Any]) -> str:
    labels = [
        ("target_files", "targets"),
        ("owned_files", "owned"),
        ("checks", "checks"),
        ("blockers", "blockers"),
        ("risk_notes", "risks"),
    ]
    rendered: list[str] = []
    for key, label in labels:
        values = payload.get(key, [])
        if not isinstance(values, list):
            continue
        value_text = ", ".join(str(value) for value in values if str(value).strip())
        if value_text:
            rendered.append(f"{label}={value_text}")
    return " | " + " | ".join(rendered) if rendered else ""


def _summarize_tool_result(payload: dict[str, Any]) -> str:
    status = "ok" if payload.get("ok") else "failed"
    parts = [f"tool_result {status}"]
    if changed_paths := payload.get("changed_paths"):
        parts.append("changed_paths=" + ", ".join(str(path) for path in changed_paths))
    if verification := payload.get("verification_result"):
        parts.append(
            "verification="
            f"{verification.get('purpose')} `{verification.get('command')}` {verification.get('status')}"
        )
    if auto := payload.get("automatic_verification_results"):
        rendered = [
            f"{item.get('purpose')} `{item.get('command')}` {item.get('status')}" for item in auto
        ]
        parts.append("automatic_verification=" + "; ".join(rendered))
    output = payload.get("output", "")
    if output:
        parts.append("output=" + _single_line(str(output), max_chars=600))
    return "; ".join(parts)


def _summarize_execution_state(payload: dict[str, Any]) -> str:
    plan_steps = payload.get("plan_steps", [])
    pending = [
        str(item.get("step"))
        for item in plan_steps
        if isinstance(item, dict) and item.get("status") != "completed"
    ]
    parts = [
        f"execution phase={payload.get('phase', 'unknown')}",
        f"step={payload.get('step', 0)}/{payload.get('max_steps', 0)}",
        f"generation={payload.get('workspace_generation', 0)}",
    ]
    if pending:
        parts.append("remaining=" + ", ".join(pending))
    if criteria := payload.get("acceptance_criteria"):
        parts.append("criteria=" + ", ".join(str(item) for item in criteria))
    if failures := payload.get("failed_hypotheses"):
        parts.append("failed_hypotheses=" + " | ".join(str(item) for item in failures[-3:]))
    parts.append(f"verification_confidence={payload.get('verification_confidence', 'none')}")
    return "; ".join(parts)


def _single_line(value: str, max_chars: int = 1000) -> str:
    rendered = " ".join(value.split())
    if len(rendered) <= max_chars:
        return rendered
    remaining = len(rendered) - max_chars
    return rendered[:max_chars].rstrip() + f" <truncated {remaining} chars>"


def indent(value: str) -> str:
    return "\n".join(f"  {line}" for line in value.splitlines())
