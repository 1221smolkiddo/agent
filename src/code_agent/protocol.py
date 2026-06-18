from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TextIO

from .schema import AgentAction
from .status import format_action_status
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

    def thinking(self, step: int) -> None:
        self.emitter.emit("status", label="THINKING", detail=f"step {step}", step=step)

    def action(self, action: AgentAction) -> None:
        status = format_action_status(action)
        label, _, detail = status.partition(" ")
        self.emitter.emit(
            "action_started",
            action_type=action.type,
            label=label,
            detail=detail,
            action=action.model_dump(exclude_none=True),
        )

    def recovery(self, detail: str) -> None:
        self.emitter.emit("recovery", label="RECOVERING", detail=detail)

    def done(self) -> None:
        self.emitter.emit("status", label="DONE", detail="")

    def model_stream_start(self, step: int) -> None:
        self._stream_chars = 0
        self.emitter.emit("model_stream_started", step=step)

    def model_stream_chunk(self, chunk: str) -> None:
        self._stream_chars += len(chunk)
        self.emitter.emit("model_stream_delta", chars=len(chunk))

    def model_stream_end(self) -> None:
        self.emitter.emit("model_stream_finished")


def json_approval_callback(
    emitter: JsonEventEmitter,
    *,
    approve_all: bool = False,
) -> Callable[[str, str], bool]:
    def approve(action: str, detail: str) -> bool:
        approved = approve_all
        emitter.emit(
            "approval_requested",
            action_type=action,
            detail=detail,
            approved=approved,
            mode="approve_all" if approve_all else "default_deny",
        )
        return approved

    return approve


def emit_run_started(
    emitter: JsonEventEmitter,
    *,
    task: str,
    cwd: str,
    dry_run: bool,
    sandbox: bool,
    model: str | None,
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
        "task": result.task,
        "message": result.message,
        "blocked": result.blocked,
        "changed_paths": result.changed_paths,
        "mutation_records": result.mutation_records,
        "command_records": result.command_records,
        "verification_results": result.verification_results,
        "context_records": result.context_records,
        "plan_updates": result.plan_updates,
        "failed_actions": result.failed_actions,
        "denied_actions": result.denied_actions,
    }
