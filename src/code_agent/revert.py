from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schema import ApplyPatchAction
from .storage import AgentStorage
from .tools import ToolRegistry


@dataclass(frozen=True)
class RevertPlan:
    run_id: int
    cwd: Path
    records: list[dict[str, Any]]
    inverse_patch: str

    @property
    def changed_paths(self) -> list[str]:
        return sorted({str(record["path"]) for record in self.records if record.get("path")})


@dataclass(frozen=True)
class RevertResult:
    ok: bool
    run_id: int
    reverted_run_id: int
    changed_paths: list[str]
    output: str
    failed_paths: list[str] = field(default_factory=list)


def build_revert_plan(storage: AgentStorage, run_id: int) -> RevertPlan:
    run = storage.get_run(run_id)
    if run is None:
        raise ValueError(f"No run found with id {run_id}.")
    records = _reversible_records(storage.run_steps_payloads(run_id))
    if not records:
        raise ValueError(f"Run {run_id} has no verified reversible file changes.")
    inverse_patch = "\n".join(str(record["inverse_patch"]).rstrip() + "\n" for record in reversed(records))
    return RevertPlan(run_id=run_id, cwd=Path(run["cwd"]).resolve(), records=records, inverse_patch=inverse_patch)


def format_revert_preview(plan: RevertPlan) -> str:
    lines = [
        f"Revert preview for run {plan.run_id}:",
        f"- workspace: {plan.cwd}",
        f"- files: {len(plan.changed_paths)}",
    ]
    lines.extend(f"- {path}" for path in plan.changed_paths)
    lines.extend(["", "Inverse patch:", plan.inverse_patch.rstrip()])
    return "\n".join(lines)


def apply_revert_plan(
    storage: AgentStorage,
    plan: RevertPlan,
    *,
    approval_callback=None,
) -> RevertResult:
    revert_run_id = storage.create_run(
        task=f"revert run {plan.run_id}",
        model="agent47-revert",
        cwd=plan.cwd,
    )
    storage.add_step(
        revert_run_id,
        "assistant",
        {
            "type": "revert",
            "reverted_run_id": plan.run_id,
            "changed_paths": plan.changed_paths,
        },
    )
    tools = ToolRegistry(
        workspace=plan.cwd,
        dry_run=False,
        approval_callback=approval_callback,
    )
    result = tools.run(ApplyPatchAction(type="apply_patch", patch=plan.inverse_patch))
    payload: dict[str, Any] = {
        "type": "revert_result",
        "ok": result.ok,
        "reverted_run_id": plan.run_id,
        "changed_paths": plan.changed_paths,
        "output": result.output,
        "metadata": result.metadata,
    }
    if not result.ok:
        storage.add_step(revert_run_id, "tool", payload)
        return RevertResult(
            ok=False,
            run_id=revert_run_id,
            reverted_run_id=plan.run_id,
            changed_paths=plan.changed_paths,
            output=result.output,
            failed_paths=plan.changed_paths,
        )

    failed_paths = _verify_revert(plan)
    if failed_paths:
        payload["ok"] = False
        payload["verification_error"] = "Revert verification failed for: " + ", ".join(failed_paths)
        storage.add_step(revert_run_id, "tool", payload)
        return RevertResult(
            ok=False,
            run_id=revert_run_id,
            reverted_run_id=plan.run_id,
            changed_paths=plan.changed_paths,
            output=payload["verification_error"],
            failed_paths=failed_paths,
        )

    payload["verification"] = "Reverted files match their recorded pre-change state."
    storage.add_step(revert_run_id, "tool", payload)
    return RevertResult(
        ok=True,
        run_id=revert_run_id,
        reverted_run_id=plan.run_id,
        changed_paths=plan.changed_paths,
        output="Reverted run "
        f"{plan.run_id}. Files restored: {', '.join(plan.changed_paths) or '<none>'}.",
    )


def _reversible_records(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for step in steps:
        payload = step.get("payload", {})
        if not isinstance(payload, dict):
            continue
        mutation_records = payload.get("mutation_records", [])
        if not isinstance(mutation_records, list):
            continue
        for record in mutation_records:
            if (
                isinstance(record, dict)
                and record.get("ok") is True
                and record.get("inverse_patch")
                and record.get("path")
            ):
                records.append(record)
    return records


def _verify_revert(plan: RevertPlan) -> list[str]:
    failed: list[str] = []
    latest_by_path: dict[str, dict[str, Any]] = {}
    for record in plan.records:
        latest_by_path[str(record["path"])] = record
    for path, record in latest_by_path.items():
        target = (plan.cwd / path).resolve()
        expected_exists = bool(record.get("exists_before"))
        if target.exists() != expected_exists:
            failed.append(path)
            continue
        if not expected_exists:
            continue
        expected_hash = str(record.get("before_sha256") or "")
        if not expected_hash:
            failed.append(path)
            continue
        try:
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            failed.append(path)
            continue
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != expected_hash:
            failed.append(path)
    return failed
