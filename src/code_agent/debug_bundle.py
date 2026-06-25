from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .safety import redact_secrets
from .storage import AgentStorage


def export_debug_bundle(
    storage: AgentStorage,
    run_id: int,
    output_dir: Path | None = None,
) -> Path:
    run = storage.get_run(run_id)
    if run is None:
        raise ValueError(f"No run found with id {run_id}.")

    target_dir = output_dir or storage.db_path.parent / "debug-bundles"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"run-{run_id}-{stamp}.json"
    payload = {
        "type": "agent47_debug_bundle",
        "created_at": datetime.now(UTC).isoformat(),
        "run": {
            "id": run["id"],
            "created_at": run["created_at"],
            "task": run["task"],
            "model": run["model"],
            "cwd": run["cwd"],
        },
        "summary": _run_summary(storage, run_id),
        "steps": storage.run_steps_payloads(run_id),
        "work_report": storage.get_work_report(run_id),
        "model_usage": storage.model_usage(run_id),
    }
    target.write_text(
        json.dumps(_redact_payload(payload), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return target


def _run_summary(storage: AgentStorage, run_id: int) -> dict[str, Any]:
    steps = storage.run_steps_payloads(run_id)
    tool_steps = [step for step in steps if step.get("role") == "tool"]
    failed_steps = [
        step
        for step in tool_steps
        if isinstance(step.get("payload"), dict) and step["payload"].get("ok") is False
    ]
    actions = [
        step["payload"].get("type")
        for step in steps
        if isinstance(step.get("payload"), dict) and step["payload"].get("type")
    ]
    return {
        "step_count": len(steps),
        "tool_step_count": len(tool_steps),
        "failed_tool_step_count": len(failed_steps),
        "actions": actions,
    }


def _redact_payload(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_payload(item) for key, item in value.items()}
    return value
