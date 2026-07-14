from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TextIO

from .permissions import MANUAL_APPROVAL_ACTIONS
from .schema import AgentAction
from .status import _semantic_stage
from .work_report import build_work_report_payload, should_show_work_report

if TYPE_CHECKING:
    from .agent import AgentRunResult


PROTOCOL_VERSION = "agent47-json-v1"


def protocol_event(event: str, **payload: Any) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL_VERSION,
        "event": event,
        "timestamp": datetime.now(UTC).isoformat(),
        **payload,
    }


def event_to_json_line(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class JsonEventEmitter:
    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream or sys.stdout

    def emit(self, event: str, **payload: Any) -> None:
        self.stream.write(event_to_json_line(protocol_event(event, **payload)) + "\n")
        self.stream.flush()


class JsonProtocolReporter:
    def __init__(self, emitter: JsonEventEmitter) -> None:
        self.emitter = emitter
        self._stream_chars = 0
        self._has_plan = False
        self._consecutive_retries = 0

    def thinking(self, step: int) -> None:
        label = "PLANNING" if self._has_plan else "THINKING"
        self.emitter.emit("status", label=label, detail=f"step {step}", step=step)

    def action(self, action: AgentAction) -> None:
        self._consecutive_retries = 0
        stage, detail = _semantic_stage(action)
        if stage is None:
            from .schema import UpdatePlanAction as UP
            if isinstance(action, UP):
                self._has_plan = True
            return
        label = stage
        self.emitter.emit(
            "action_started",
            action_type=action.type,
            label=label,
            detail=detail,
            action=action.model_dump(exclude_none=True),
        )
        from .schema import UpdatePlanAction as UP
        if isinstance(action, UP):
            self._has_plan = True

    def recovery(self, detail: str) -> None:
        self._consecutive_retries += 1
        # Only emit recovery events on 2nd+ consecutive retry
        if self._consecutive_retries >= 2:
            self.emitter.emit("recovery", label="RETRYING", detail=_friendly_retry_detail(detail))

    def done(self) -> None:
        # Suppress DONE — response itself implies completion
        pass

    def model_stream_start(self, step: int) -> None:
        self._stream_chars = 0
        self.emitter.emit("model_stream_started", label="GENERATING", step=step)

    def model_stream_chunk(self, chunk: str) -> None:
        self._stream_chars += len(chunk)
        self.emitter.emit("model_stream_delta", chars=len(chunk))

    def model_stream_end(self) -> None:
        self.emitter.emit("model_stream_finished")

    def workspace_analysis(self, summary: str) -> None:
        self.emitter.emit("workspace_analysis", summary=summary)

    def mutation_preview(
        self,
        creates: list[str],
        modifies: list[str],
        deletes: list[str],
    ) -> None:
        self.emitter.emit(
            "mutation_preview",
            creates=creates,
            modifies=modifies,
            deletes=deletes,
        )


def json_approval_callback(
    emitter: JsonEventEmitter,
    *,
    approve_all: bool = False,
    input_stream: TextIO | None = None,
) -> Callable[..., bool]:
    def approve(action: str, detail: str, metadata: dict[str, Any] | None = None) -> bool:
        request_id = str(uuid.uuid4())
        approval_payload = {
            "request_id": request_id,
            "action_type": action,
            "detail": detail,
            "metadata": metadata or {},
        }
        if approve_all and action in MANUAL_APPROVAL_ACTIONS:
            emitter.emit(
                "approval_requested",
                **approval_payload,
                approved=False,
                mode="manual_required",
            )
            emitter.emit(
                "approval_resolved",
                request_id=request_id,
                action_type=action,
                approved=False,
                mode="manual_required",
                reason=f"{action} requires explicit manual approval and cannot use --approve-all",
            )
            return False
        if approve_all:
            emitter.emit(
                "approval_requested",
                **approval_payload,
                approved=True,
                mode="approve_all",
            )
            emitter.emit(
                "approval_resolved",
                request_id=request_id,
                action_type=action,
                approved=True,
                mode="approve_all",
                reason="approved by --approve-all",
            )
            return True
        if input_stream is None:
            emitter.emit(
                "approval_requested",
                **approval_payload,
                approved=False,
                mode="default_deny",
            )
            emitter.emit(
                "approval_resolved",
                request_id=request_id,
                action_type=action,
                approved=False,
                mode="default_deny",
                reason="JSON mode denies approvals unless --approval-stdin or --approve-all is used",
            )
            return False

        emitter.emit(
            "approval_requested",
            **approval_payload,
            approved=None,
            mode="stdin",
            response_schema={
                "type": "approval_response",
                "request_id": request_id,
                "approved": True,
                "reason": "optional short reason",
            },
        )
        approved, reason = _read_json_approval_response(input_stream, request_id)
        emitter.emit(
            "approval_resolved",
            request_id=request_id,
            action_type=action,
            approved=approved,
            mode="stdin",
            reason=reason,
        )
        return approved

    return approve


def _read_json_approval_response(input_stream: TextIO, request_id: str) -> tuple[bool, str]:
    raw = input_stream.readline()
    if not raw:
        return False, "approval input ended before a response was received"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return False, f"invalid approval JSON: {exc.msg}"
    if not isinstance(payload, dict):
        return False, "approval response must be a JSON object"
    if payload.get("type") != "approval_response":
        return False, "approval response type must be approval_response"
    response_request_id = payload.get("request_id")
    if response_request_id != request_id:
        return False, "approval response request_id did not match"
    approved = payload.get("approved")
    if not isinstance(approved, bool):
        return False, "approval response must include approved boolean"
    reason = str(payload.get("reason") or ("approved" if approved else "denied"))
    return approved, reason


def emit_run_started(
    emitter: JsonEventEmitter,
    *,
    task: str,
    cwd: str,
    dry_run: bool,
    sandbox: bool,
    model: str | None,
    preset: str | None = None,
    profile: str | None,
    max_steps: int,
) -> None:
    emitter.emit(
        "run_started",
        task=task,
        cwd=cwd,
        dry_run=dry_run,
        sandbox=sandbox,
        model=model,
        preset=preset,
        profile=profile,
        max_steps=max_steps,
    )


def emit_run_finished(emitter: JsonEventEmitter, result: AgentRunResult) -> None:
    payload = result_payload(result)
    if should_show_work_report(result):
        payload["work_report"] = build_work_report_payload(result)
    emitter.emit("run_finished", **payload)


def emit_run_failed(emitter: JsonEventEmitter, message: str, *, code: str = "run_failed") -> None:
    emitter.emit("run_failed", code=code, message=message)


def result_payload(result: AgentRunResult) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "task": getattr(result, "clean_task", "") or result.task,
        "message": result.message,
        "blocked": result.blocked,
        "changed_paths": result.changed_paths,
        "mutation_records": result.mutation_records,
        "command_records": result.command_records,
        "verification_results": result.verification_results,
        "context_records": result.context_records,
        "model_usage_records": result.model_usage_records,
        "plan_updates": result.plan_updates,
        "review_records": result.review_records,
        "failed_actions": result.failed_actions,
        "denied_actions": result.denied_actions,
        "execution_state": result.execution_state,
    }


def _friendly_retry_detail(detail: str) -> str:
    lowered = detail.lower()
    if "invalid model action" in lowered or "parse" in lowered:
        return "retrying with a cleaner response"
    if "non-workspace" in lowered or "blocked workspace tool" in lowered:
        return "switching back to chat mode"
    if "tool failed" in lowered:
        return "trying another approach"
    if "model failed" in lowered:
        return "model call failed"
    return "trying again"
