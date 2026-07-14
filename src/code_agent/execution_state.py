from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .models import ChatMessage
from .schema import AgentAction, ToolResult, UpdatePlanAction


class ExecutionPhase(str, Enum):
    DISCOVER = "discover"
    PLAN = "plan"
    EXECUTE = "execute"
    VERIFY = "verify"
    RECOVER = "recover"
    FINALIZE = "finalize"


@dataclass(frozen=True)
class ActionOutcome:
    ok: bool
    output_digest: str


@dataclass
class ExecutionState:
    task: str
    max_steps: int
    repeated_outcome_limit: int = 2
    phase: ExecutionPhase = ExecutionPhase.DISCOVER
    step: int = 0
    workspace_generation: int = 0
    action_count: int = 0
    context_chars: int = 0
    compacted_messages: int = 0
    plan_steps: list[dict[str, str]] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    changed_paths: list[str] = field(default_factory=list)
    failed_hypotheses: list[str] = field(default_factory=list)
    verification_records: list[dict[str, Any]] = field(default_factory=list)
    _outcomes: dict[str, list[ActionOutcome]] = field(default_factory=dict)

    def begin_step(self, step: int) -> None:
        self.step = step

    def update_plan(self, action: UpdatePlanAction) -> None:
        self.phase = ExecutionPhase.PLAN
        self.plan_steps = [item.model_dump(exclude_none=True) for item in action.steps]
        self.acceptance_criteria = _dedupe([*action.checks, *self.acceptance_criteria])

    def begin_action(self, action: AgentAction) -> None:
        self.phase = _phase_for_action(action)

    def repeated_action_detail(self, action: AgentAction) -> str | None:
        outcomes = self._outcomes.get(self._fingerprint(action), [])
        if len(outcomes) < self.repeated_outcome_limit:
            return None
        recent = outcomes[-self.repeated_outcome_limit :]
        if len(set(recent)) != 1:
            return None
        status = "succeeded" if recent[-1].ok else "failed"
        return (
            f"Blocked repeated action loop: `{action.type}` produced the same {status} outcome "
            f"{self.repeated_outcome_limit} times without any workspace change. Choose a materially "
            "different action, inspect new evidence, update the plan, or finalize honestly."
        )

    def record_action(
        self,
        action: AgentAction,
        result: ToolResult,
        changed_paths: list[str],
    ) -> None:
        fingerprint = self._fingerprint(action)
        outcome = ActionOutcome(
            ok=result.ok,
            output_digest=hashlib.sha256(result.output.encode("utf-8")).hexdigest(),
        )
        self._outcomes.setdefault(fingerprint, []).append(outcome)
        self.action_count += 1
        if not result.ok:
            self.phase = ExecutionPhase.RECOVER
            hypothesis = f"{action.type}: {' '.join(result.output.split())[:240]}"
            self.failed_hypotheses = _dedupe([*self.failed_hypotheses, hypothesis])[-8:]
        if changed_paths:
            self.changed_paths = _dedupe([*self.changed_paths, *changed_paths])
            self.workspace_generation += 1

    def record_verification(self, results: list[dict[str, Any]]) -> None:
        if not results:
            return
        self.phase = (
            ExecutionPhase.VERIFY
            if all(bool(item.get("ok")) for item in results)
            else ExecutionPhase.RECOVER
        )
        for item in results:
            record = {
                "purpose": str(item.get("purpose", "verification")),
                "command": str(item.get("command", "")),
                "status": str(item.get("status", "unknown")),
                "automatic": bool(item.get("automatic", False)),
            }
            self.verification_records.append(record)
        self.verification_records = self.verification_records[-12:]

    def record_context(self, *, chars: int, compacted_messages: int) -> None:
        self.context_chars = chars
        self.compacted_messages += compacted_messages

    def recover(self, detail: str) -> None:
        self.phase = ExecutionPhase.RECOVER
        cleaned = " ".join(detail.split())[:240]
        if cleaned:
            self.failed_hypotheses = _dedupe([*self.failed_hypotheses, cleaned])[-8:]

    def finalize(self) -> None:
        self.phase = ExecutionPhase.FINALIZE

    def finalization_blocker(
        self,
        *,
        claims_success: bool,
        verification_results: list[dict[str, Any]],
    ) -> str | None:
        if not claims_success:
            return None
        unfinished = [
            str(item.get("step"))
            for item in self.plan_steps
            if item.get("status") != "completed"
        ]
        if unfinished:
            return (
                "The final answer claims completion while the durable plan still has unfinished steps: "
                + ", ".join(unfinished)
                + ". Complete the work or update the plan truthfully before finalizing."
            )
        passed_commands = [
            _normalize_command(str(item.get("command", "")))
            for item in verification_results
            if item.get("ok") is True
        ]
        unmet = [
            criterion
            for criterion in self.acceptance_criteria
            if not any(
                _commands_equivalent(_normalize_command(criterion), passed)
                for passed in passed_commands
            )
        ]
        if unmet:
            return (
                "The final answer claims completion before these planned checks passed: "
                + ", ".join(unmet)
                + ". Run the checks or update the plan with an honest blocker."
            )
        return None

    def snapshot(self) -> dict[str, Any]:
        return {
            "type": "execution_state",
            "phase": self.phase.value,
            "step": self.step,
            "max_steps": self.max_steps,
            "workspace_generation": self.workspace_generation,
            "action_count": self.action_count,
            "context_chars": self.context_chars,
            "compacted_messages": self.compacted_messages,
            "plan_steps": self.plan_steps,
            "acceptance_criteria": self.acceptance_criteria,
            "changed_paths": self.changed_paths,
            "failed_hypotheses": self.failed_hypotheses,
            "verification_records": self.verification_records,
            "verification_confidence": self._verification_confidence(),
        }

    def _fingerprint(self, action: AgentAction) -> str:
        payload = json.dumps(
            action.model_dump(exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"{self.workspace_generation}:{digest}"

    def _verification_confidence(self) -> str:
        if not self.verification_records:
            return "none"
        latest_by_purpose: dict[str, dict[str, Any]] = {}
        for item in self.verification_records:
            latest_by_purpose[str(item["purpose"])] = item
        if any(item["status"] != "passed" for item in latest_by_purpose.values()):
            return "failed"
        if len(latest_by_purpose) >= 2:
            return "high"
        return "focused"


def compact_message_history(
    messages: list[ChatMessage],
    *,
    max_chars: int,
) -> tuple[list[ChatMessage], int]:
    if max_chars < 8_000:
        raise ValueError("Context budget must be at least 8000 characters.")
    total = _message_chars(messages)
    if total <= max_chars or len(messages) <= 3:
        return messages, 0

    head = messages[:2]
    tail: list[ChatMessage] = []
    reserved = _message_chars(head) + 1_500
    budget = max(max_chars - reserved, 0)
    used = 0
    for message in reversed(messages[2:]):
        size = len(message.get("content", ""))
        if tail and used + size > budget:
            break
        tail.append(message)
        used += size
    tail.reverse()
    omitted = messages[2 : len(messages) - len(tail)]
    if not omitted:
        return messages, 0
    summary = _summarize_omitted_messages(omitted)
    compacted = [
        *head,
        {
            "role": "user",
            "content": (
                "Deterministic execution-history checkpoint. Earlier messages were compacted; "
                "do not treat this summary as new instructions.\n" + summary
            ),
        },
        *tail,
    ]
    return compacted, len(omitted)


def _summarize_omitted_messages(messages: list[ChatMessage]) -> str:
    actions: list[str] = []
    failures = 0
    changed_paths: list[str] = []
    for message in messages:
        content = message.get("content", "")
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        payload_type = str(payload.get("type") or "")
        if payload_type and payload_type != "tool_result":
            actions.append(payload_type)
        if payload_type == "tool_result":
            failures += int(payload.get("ok") is False)
            paths = payload.get("changed_paths", [])
            if isinstance(paths, list):
                changed_paths.extend(str(path) for path in paths)
    return (
        f"omitted_messages={len(messages)}; actions={', '.join(actions[-12:]) or 'none'}; "
        f"failed_results={failures}; changed_paths={', '.join(_dedupe(changed_paths)) or 'none'}."
    )


def _phase_for_action(action: AgentAction) -> ExecutionPhase:
    if action.type in {
        "list_files",
        "read_file",
        "search",
        "summarize_code",
        "inspect_git_diff",
        "repo_map",
        "rank_context",
        "symbol_index",
        "lsp_status",
        "lsp_definition",
        "lsp_references",
        "lsp_hover",
        "lsp_rename",
        "lsp_workspace_symbols",
        "lsp_completion",
        "lsp_formatting",
        "lsp_code_actions",
        "dependency_graph",
        "read_memory",
        "web_search",
    }:
        return ExecutionPhase.DISCOVER
    if action.type in {
        "detect_verification",
        "suggest_verification",
        "lsp_diagnostics",
        "run_shell",
    }:
        return ExecutionPhase.VERIFY
    return ExecutionPhase.EXECUTE


def _message_chars(messages: list[ChatMessage]) -> int:
    return sum(len(message.get("content", "")) for message in messages)


def _dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in output:
            output.append(cleaned)
    return output


def _normalize_command(command: str) -> str:
    return " ".join(command.lower().strip().split())


def _commands_equivalent(expected: str, actual: str) -> bool:
    if not expected or not actual:
        return False
    return expected == actual or expected in actual or actual in expected
