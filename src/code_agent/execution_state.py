from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .failure_types import (
    CONTEXT_BUDGET_WARNING_RATIO,
    LOW_CONFIDENCE_HARD_THRESHOLD,
    LOW_CONFIDENCE_MUTATION_THRESHOLD,
    MAX_RECOVERY_ATTEMPTS,
    FailureCategory,
    FailureFingerprint,
    RecoveryEvent,
    RunDisposition,
    classify_failure,
    recovery_instruction,
)
from .safety import blocked_command_strategy, redact_command_for_display, redact_command_output_for_display, redact_secrets
from .models import ChatMessage
from .schema import AgentAction, ToolResult, UpdatePlanAction


class ExecutionPhase(str, Enum):
    DISCOVER = "discover"
    PLAN = "plan"
    EXECUTE = "execute"
    VERIFY = "verify"
    RECOVER = "recover"
    FINALIZE = "finalize"
    WAITING = "waiting"


@dataclass(frozen=True)
class ActionOutcome:
    ok: bool
    output_digest: str


@dataclass
class ExecutionState:
    task: str
    max_steps: int | None
    repeated_outcome_limit: int = 2
    phase: ExecutionPhase = ExecutionPhase.DISCOVER
    step: int = 0
    workspace_generation: int = 0
    action_count: int = 0
    context_chars: int = 0
    compacted_messages: int = 0
    plan_steps: list[dict[str, Any]] = field(default_factory=list)
    plan_revision: int = 0
    plan_history: list[dict[str, Any]] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    changed_paths: list[str] = field(default_factory=list)
    failed_hypotheses: list[str] = field(default_factory=list)
    failed_approaches: list[dict[str, Any]] = field(default_factory=list)
    hypotheses: list[dict[str, Any]] = field(default_factory=list)
    evidence_records: list[dict[str, Any]] = field(default_factory=list)
    verification_records: list[dict[str, Any]] = field(default_factory=list)
    context_packs: list[dict[str, Any]] = field(default_factory=list)
    model_handoffs: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[dict[str, Any]] = field(default_factory=list)
    replan_required: bool = False
    replan_reason: str = ""
    checkpoint_count: int = 0
    checkpoint_reason: str = ""
    resume_count: int = 0
    resumed_from_run_id: int | None = None
    _outcomes: dict[str, list[ActionOutcome]] = field(default_factory=dict)
    _failure_fingerprints: dict[FailureFingerprint, int] = field(default_factory=dict)
    recovery_attempt_count: int = 0
    disposition: RunDisposition = RunDisposition.RECOVERABLE
    run_records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    policy_denials: dict[str, dict[str, Any]] = field(default_factory=dict)
    recovery_counts: dict[str, int] = field(default_factory=dict)
    failure_streaks: dict[str, int] = field(default_factory=dict)

    def denied_command(self, action: AgentAction) -> dict[str, Any] | None:
        if action.type not in {"run_shell", "start_process"}:
            return None
        return self.policy_denials.get(blocked_command_strategy(action.command))

    def record_policy_denial(self, action: AgentAction, result: ToolResult) -> int:
        key = blocked_command_strategy(action.command)
        entry = self.policy_denials.setdefault(key, {
            "command": redact_command_for_display(action.command),
            "policy_class": result.metadata.get("policy_class", "permission"),
            "reason": redact_secrets(str(result.metadata.get("policy_reason", result.output))),
            "count": 0,
            "policy_context": result.metadata.get("policy_context"),
        })
        entry["count"] += 1
        self.phase = ExecutionPhase.RECOVER
        self.checkpoint("policy_denial")
        return entry["count"]

    def record_recovery(self, event: RecoveryEvent) -> int:
        key = event.value
        self.recovery_counts[key] = self.recovery_counts.get(key, 0) + 1
        self.failure_streaks[key] = self.failure_streaks.get(key, 0) + 1
        self.disposition = RunDisposition.RECOVERABLE
        return self.failure_streaks[key]

    def pause(self, reason: str) -> None:
        self.phase = ExecutionPhase.WAITING
        self.disposition = RunDisposition.WAITING
        self.blockers.append({"kind": "waiting", "detail": reason, "active": True})
        self.checkpoint("waiting")

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict[str, Any],
        *,
        task: str,
        max_steps: int | None,
        resumed_from_run_id: int | None = None,
    ) -> "ExecutionState":
        state = cls(task=task, max_steps=max_steps)
        for name in (
            "step",
            "workspace_generation",
            "action_count",
            "context_chars",
            "compacted_messages",
            "plan_revision",
            "checkpoint_count",
            "resume_count",
        ):
            value = snapshot.get(name)
            if isinstance(value, int) and value >= 0:
                setattr(state, name, value)
        for name in (
            "plan_steps",
            "plan_history",
            "acceptance_criteria",
            "changed_paths",
            "failed_hypotheses",
            "failed_approaches",
            "hypotheses",
            "evidence_records",
            "verification_records",
            "context_packs",
            "model_handoffs",
            "blockers",
        ):
            value = snapshot.get(name)
            if isinstance(value, list):
                setattr(state, name, value.copy())
        state.policy_denials = deepcopy(snapshot.get("policy_denials", {}))
        state.run_records = deepcopy(snapshot.get("run_records", {}))
        for name in ("recovery_counts", "failure_streaks"):
            value = snapshot.get(name, {})
            if isinstance(value, dict):
                setattr(state, name, {
                    str(key): count for key, count in value.items()
                    if isinstance(count, int) and count >= 0
                })
        for key, outcomes in snapshot.get("action_outcomes", {}).items():
            state._outcomes[key] = [ActionOutcome(**item) for item in outcomes]
        for item in snapshot.get("failure_fingerprints", []):
            target = str(item["target"])
            if not target.startswith("sha256:"):
                target = "sha256:" + hashlib.sha256(target[:200].encode()).hexdigest()
            fp = FailureFingerprint(
                item["action_type"], target, FailureCategory(item["category"]),
                item.get("strategy_digest", "")
            )
            state._failure_fingerprints[fp] = item["count"]
        state.recovery_attempt_count = snapshot.get("recovery_attempt_count", 0)
        state.replan_required = bool(snapshot.get("replan_required", False))
        state.replan_reason = str(snapshot.get("replan_reason", ""))
        state.resume_count += 1
        state.resumed_from_run_id = resumed_from_run_id
        state.phase = ExecutionPhase.RECOVER
        state.checkpoint("resumed")
        return state

    def begin_step(self, step: int) -> None:
        self.step = step

    def update_plan(self, action: UpdatePlanAction) -> None:
        self.phase = ExecutionPhase.PLAN
        self.record_recovery(RecoveryEvent.REPLAN)
        self.failure_streaks[RecoveryEvent.EXECUTABLE.value] = 0
        self.failure_streaks[RecoveryEvent.GUARD.value] = 0
        self.recovery_attempt_count = 0
        normalized: list[dict[str, Any]] = []
        acceptance_blockers: list[dict[str, Any]] = []
        for item in action.steps:
            payload = item.model_dump(exclude_none=True, exclude_defaults=True)
            criteria = payload.get("acceptance_criteria", [])
            if payload.get("status") == "completed" and criteria:
                unmet = [criterion for criterion in criteria if not self._criterion_met(criterion)]
                payload["acceptance_status"] = "passed" if not unmet else "blocked"
                if unmet:
                    payload["status"] = "blocked"
                    payload["note"] = "Missing acceptance evidence: " + ", ".join(unmet)
                    acceptance_blockers.append(
                        {
                            "kind": "acceptance",
                            "detail": f"{payload.get('step')}: {', '.join(unmet)}",
                            "active": True,
                        }
                    )
            normalized.append(payload)
        statuses = {item.get("id"): item.get("status") for item in normalized if item.get("id")}
        for payload in normalized:
            if payload.get("status") != "in_progress":
                continue
            unmet_dependencies = [
                dependency
                for dependency in payload.get("depends_on", [])
                if statuses.get(dependency) != "completed"
            ]
            if unmet_dependencies:
                payload["status"] = "blocked"
                payload["note"] = "Unmet gated dependencies: " + ", ".join(unmet_dependencies)
                acceptance_blockers.append(
                    {
                        "kind": "dependency",
                        "detail": f"{payload.get('step')}: {', '.join(unmet_dependencies)}",
                        "active": True,
                    }
                )
        if normalized != self.plan_steps:
            self.plan_revision += 1
            self.plan_history.append(
                {
                    "revision": self.plan_revision,
                    "rationale": action.rationale or "",
                    "steps": normalized,
                }
            )
            self.plan_history = self.plan_history[-20:]
        if normalized != self.plan_steps:
            self.record_evidence(source="plan", summary=action.rationale or "Plan revised from current evidence.")
        self.plan_steps = normalized
        step_checks = [
            criterion
            for item in normalized
            for criterion in item.get("acceptance_criteria", [])
        ]
        self.acceptance_criteria = _dedupe([*action.checks, *step_checks])
        if action.hypotheses:
            updates = {item.id: item.model_dump() for item in action.hypotheses}
            existing = {str(item.get("id")): item for item in self.hypotheses}
            existing.update(updates)
            self.hypotheses = list(existing.values())[-20:]
        self.blockers = acceptance_blockers + [
            {"kind": "plan", "detail": blocker, "active": True}
            for blocker in action.blockers
        ]
        self.replan_required = False
        self.replan_reason = ""
        self.checkpoint("plan_updated")

    def replan_blocker(self, action: AgentAction) -> str | None:
        if not self.replan_required or action.type == "update_plan":
            return None
        discovery_actions = {
            "list_files", "read_file", "search", "summarize_code", "inspect_git_diff",
            "repo_map", "rank_context", "symbol_index", "dependency_graph", "read_memory",
            "lsp_status", "lsp_definition", "lsp_references", "lsp_hover",
            "lsp_workspace_symbols", "lsp_diagnostics", "detect_verification",
            "suggest_verification", "read_process_logs", "inspect_process", "process_events",
        }
        if action.type in discovery_actions:
            return None
        return (
            "Execution assumptions changed and the durable plan must be revised before further "
            f"mutation, execution, or finalization. Reason: {self.replan_reason}"
        )

    def require_replan(self, reason: str) -> None:
        cleaned = " ".join(reason.split())[:500]
        self.phase = ExecutionPhase.RECOVER
        self.disposition = RunDisposition.REPLAN_REQUIRED
        self.replan_required = True
        self.replan_reason = cleaned
        self.blockers.append({"kind": "replan", "detail": cleaned, "active": True})
        self.blockers = self.blockers[-20:]
        self.checkpoint("replan_required")

    def record_evidence(
        self,
        *,
        source: str,
        summary: str,
        paths: list[str] | None = None,
        confidence: str = "medium",
    ) -> None:
        cleaned = " ".join(summary.split())[:1000]
        if not cleaned:
            return
        self.evidence_records.append(
            {
                "source": source,
                "summary": cleaned,
                "paths": _dedupe(paths or []),
                "confidence": confidence,
                "workspace_generation": self.workspace_generation,
            }
        )
        self.evidence_records = self.evidence_records[-40:]

    def record_context_pack(self, pack: dict[str, Any]) -> None:
        self.context_packs.append(pack)
        self.context_packs = self.context_packs[-6:]
        self.record_evidence(
            source="context_pack",
            summary=f"Ranked {len(pack.get('files', []))} files and {len(pack.get('relationships', []))} relationships.",
            paths=[str(item.get("path")) for item in pack.get("files", []) if item.get("path")],
            confidence="high",
        )

    def record_model_handoff(self, record: dict[str, Any]) -> None:
        self.model_handoffs.append(record.copy())
        self.model_handoffs = self.model_handoffs[-12:]
        self.checkpoint("provider_handoff")

    def checkpoint(self, reason: str) -> None:
        self.checkpoint_count += 1
        self.checkpoint_reason = reason

    def begin_action(self, action: AgentAction) -> None:
        self.phase = _phase_for_action(action)

    @staticmethod
    def strategy_fingerprint(action: AgentAction) -> str:
        """Hash executable action parameters, not rewordable plan prose."""
        payload = action.model_dump(exclude_none=True)
        payload.pop("rationale", None)
        if isinstance(payload.get("command"), str):
            payload["command"] = " ".join(payload["command"].split())
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def failed_strategy_blocker(self, action: AgentAction) -> str | None:
        fingerprint = self.strategy_fingerprint(action)
        for item in reversed(self.failed_approaches):
            if (
                item.get("strategy_fingerprint") == fingerprint
                and item.get("status") == "failed"
                and item.get("workspace_generation") == self.workspace_generation
            ):
                return (
                    "That strategy already failed repeatedly. Gather new evidence or choose "
                    "a materially different tool, target, command, or patch before retrying."
                )
        return None

    def _record_failed_approach(self, action: AgentAction, result: ToolResult, category: str) -> None:
        fingerprint = self.strategy_fingerprint(action)
        now = datetime.now(timezone.utc).isoformat()
        payload = action.model_dump(exclude_none=True)
        target = next(
            (str(payload[key]) for key in ("path", "command", "query", "symbol")
             if payload.get(key)), "",
        )
        target = (redact_command_for_display(target) if payload.get("command") else redact_secrets(target))[:200]
        existing = next(
            (item for item in self.failed_approaches
             if item.get("strategy_fingerprint") == fingerprint
             and item.get("workspace_generation") == self.workspace_generation),
            None,
        )
        if existing is None:
            existing = {
                "id": fingerprint[:16], "strategy_fingerprint": fingerprint,
                "action_type": action.type, "target": target,
                "failure_category": category,
                "evidence": {"exit_code": result.metadata.get("exit_code"),
                             "output_digest": hashlib.sha256(result.output.encode("utf-8")).hexdigest()},
                "attempts": 0, "first_seen": now,
                "workspace_generation": self.workspace_generation,
                "invalidated_reason": None, "superseded_by_strategy": None,
            }
            self.failed_approaches.append(existing)
        existing["attempts"] += 1
        existing["last_seen"] = now
        existing["status"] = "failed" if existing["attempts"] >= 2 else "watching"
        self.failed_approaches = self.failed_approaches[-16:]
        if existing["status"] == "failed":
            self.checkpoint("failed_approach")

    def _supersede_failed_approach(self, action: AgentAction) -> None:
        fingerprint = self.strategy_fingerprint(action)
        for item in reversed(self.failed_approaches):
            if item.get("status") == "failed" and item.get("action_type") == action.type:
                if item.get("strategy_fingerprint") != fingerprint:
                    item["status"] = "superseded"
                    item["superseded_by_strategy"] = fingerprint
                    item["invalidated_reason"] = "A materially different action succeeded."
                    self.checkpoint("failed_approach_superseded")
                break

    def repeated_action_detail(self, action: AgentAction) -> str | None:
        outcomes = self._outcomes.get(self._fingerprint(action), [])
        if len(outcomes) < self.repeated_outcome_limit:
            return None
        recent = outcomes[-self.repeated_outcome_limit :]
        if any(item.ok for item in recent):
            return None
        status = "failed"
        return (
            f"Blocked unchanged failed strategy: this attempt {status} "
            f"{self.repeated_outcome_limit} times without any workspace change. Choose a materially "
            "different action after diagnosing the evidence and updating the plan."
        )

    def observation_already_known(self, action: AgentAction) -> bool:
        # Only stable workspace discovery is reusable; live process/network reads are not.
        if action.type not in {
            "read_file", "list_files", "search", "repo_map", "rank_context",
            "symbol_index", "dependency_graph", "summarize_code", "inspect_git_diff",
        }:
            return False
        outcomes = self._outcomes.get(self._fingerprint(action), [])
        recent = outcomes[-self.repeated_outcome_limit:]
        return (
            len(recent) == self.repeated_outcome_limit
            and all(item.ok for item in recent) and len(set(recent)) == 1
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
        self._outcomes[fingerprint] = self._outcomes[fingerprint][-self.repeated_outcome_limit:]
        self.action_count += 1
        if not result.ok:
            self.phase = ExecutionPhase.RECOVER
            self.recovery_attempt_count += 1
            self.record_recovery(RecoveryEvent.EXECUTABLE)
            safe_output = (redact_command_output_for_display(action.command, result.output)
                           if isinstance(getattr(action, "command", None), str)
                           else redact_secrets(result.output))
            hypothesis = f"{action.type}: {' '.join(safe_output.split())[:240]}"
            self.failed_hypotheses = _dedupe([*self.failed_hypotheses, hypothesis])[-8:]
            self.record_evidence(source=action.type, summary=hypothesis, confidence="high")
            # Track structured failure fingerprint (action + target + category)
            category = classify_failure(
                action.type,
                result.output,
                result.metadata if hasattr(result, "metadata") else None,
            )
            action_payload = action.model_dump(exclude_none=True)
            fp = FailureFingerprint.from_action(action.type, action_payload, category)
            self._failure_fingerprints[fp] = self._failure_fingerprints.get(fp, 0) + 1
            self._record_failed_approach(action, result, category.value)
        else:
            self.recovery_attempt_count = 0
            self._supersede_failed_approach(action)
            self.failure_streaks[RecoveryEvent.EXECUTABLE.value] = 0
            self.record_evidence(source=action.type, summary=result.output[:240])
        if changed_paths or (result.ok and action.type in {
            "run_shell", "start_process", "send_process_input", "restart_process",
            "undo_transaction", "redo_transaction", "restore_snapshot",
        }):
            self.changed_paths = _dedupe([*self.changed_paths, *changed_paths])
            self.workspace_generation += 1
            self._outcomes.clear()
            self._failure_fingerprints.clear()

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
        failures = [item for item in results if not bool(item.get("ok"))]
        if failures:
            self.record_recovery(RecoveryEvent.VERIFICATION)
            first = failures[0]
            self.record_evidence(
                source="verification",
                summary=f"{first.get('command', 'verification')} failed: {first.get('output', '')}",
                confidence="high",
            )
            self.require_replan(f"Verification failed: {first.get('command', 'planned check')}")
        else:
            self.failure_streaks[RecoveryEvent.VERIFICATION.value] = 0
            for item in results:
                self.record_evidence(
                    source="verification",
                    summary=f"Passed {item.get('command', item.get('purpose', 'verification'))}",
                    confidence="high",
                )
            self.checkpoint("verification_passed")

    def record_context(self, *, chars: int, compacted_messages: int) -> None:
        self.context_chars = chars
        self.compacted_messages += compacted_messages

    def recover(self, detail: str) -> None:
        self.phase = ExecutionPhase.RECOVER
        cleaned = " ".join(detail.split())[:240]
        if cleaned:
            self.failed_hypotheses = _dedupe([*self.failed_hypotheses, cleaned])[-8:]

    def finalize(self, disposition: RunDisposition = RunDisposition.SUCCESS) -> None:
        self.disposition = disposition
        self.phase = ExecutionPhase.FINALIZE
        self.checkpoint("finalized")

    # ------------------------------------------------------------------
    # Structured failure recovery (Production Readiness Pass 1)
    # ------------------------------------------------------------------

    def failure_recovery_instruction(
        self,
        action: AgentAction,
        result: ToolResult,
        current_recorded: bool = True,
    ) -> str | None:
        """Return a category-specific recovery instruction, or trigger replan.

        Returns ``None`` if the failure does not need special handling (i.e. the
        existing generic recovery path is fine).  Returns a non-empty string to
        override the generic recovery instruction.  As a side effect, may set
        ``replan_required`` when the same fingerprint has repeated too many times.
        """
        if result.ok:
            return None

        category = classify_failure(
            action.type,
            result.output,
            result.metadata if hasattr(result, "metadata") else None,
        )
        action_payload = action.model_dump(exclude_none=True)
        fp = FailureFingerprint.from_action(action.type, action_payload, category)
        occurrences = self._failure_fingerprints.get(fp, 0)
        effective_occurrences = occurrences if current_recorded else occurrences + 1

        # A local streak changes the strategy; it does not end the task.
        if self.recovery_attempt_count >= MAX_RECOVERY_ATTEMPTS:
            self.require_replan(
                f"Reached {MAX_RECOVERY_ATTEMPTS} recovery attempts. "
                "The current approach is not working."
            )
            return (
                f"Recovery limit reached ({MAX_RECOVERY_ATTEMPTS} attempts). "
                "Stop the current approach. Revise the plan, choose a completely "
                "different strategy and continue the task."
            )

        # Same fingerprint repeated >= 2 times => force replan
        if effective_occurrences >= 2:
            self.require_replan(
                f"Repeated {category.value} failure on {fp.action_type}. "
                "A different strategy is needed."
            )
            return (
                f"The same {category.value} failure has occurred {effective_occurrences} times "
                f"for `{fp.action_type}`. The plan must be revised before retrying. "
                "Update the plan with new information from the failed attempts."
            )

        return recovery_instruction(category)

    def low_confidence_blocker(self, action: AgentAction) -> str | None:
        """Block mutating actions when planning confidence is too low.

        Returns ``None`` if the action should proceed, or a blocking message
        explaining why more evidence is needed.
        """
        confidence = self._confidence()
        score = confidence["score"]

        # Discovery and planning actions are never blocked
        discovery_actions = {
            "list_files", "read_file", "search", "summarize_code",
            "inspect_git_diff", "repo_map", "rank_context", "symbol_index",
            "dependency_graph", "read_memory", "lsp_status", "lsp_definition",
            "lsp_references", "lsp_hover", "lsp_workspace_symbols",
            "lsp_diagnostics", "detect_verification", "suggest_verification",
            "read_process_logs", "inspect_process", "process_events",
            "update_plan",
        }
        if action.type in discovery_actions:
            return None

        mutating_actions = {
            "write_file", "edit_file", "apply_patch", "delete_file",
            "move_file", "run_shell", "start_process",
        }

        if score < LOW_CONFIDENCE_HARD_THRESHOLD:
            return (
                f"Planning confidence is critically low ({score:.2f}). "
                "Gather more evidence, inspect additional files, search for "
                "project symbols, or ask clarifying questions before continuing. "
                "Do not attempt any action until confidence improves."
            )

        if score < LOW_CONFIDENCE_MUTATION_THRESHOLD and action.type in mutating_actions:
            return (
                f"Planning confidence is low ({score:.2f}). "
                "Before modifying files or running commands, inspect additional "
                "repository context, search for relevant symbols, or collect "
                "more evidence to increase confidence."
            )

        return None

    def proactive_budget_check(
        self, context_chars: int, max_chars: int
    ) -> str | None:
        """Return a compaction hint when context usage approaches the budget."""
        if max_chars <= 0:
            return None
        ratio = context_chars / max_chars
        if ratio >= CONTEXT_BUDGET_WARNING_RATIO:
            return (
                f"Context budget is at {ratio:.0%} capacity ({context_chars:,} / "
                f"{max_chars:,} chars). Summarize earlier reasoning and discard "
                "obsolete information before the next action."
            )
        return None

    def finalization_blocker(
        self,
        *,
        claims_success: bool,
        verification_results: list[dict[str, Any]],
    ) -> str | None:
        if not claims_success:
            return None
        if self.replan_required:
            return "The run cannot claim completion until the required replanning is completed: " + self.replan_reason
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
        unmet = [criterion for criterion in self.acceptance_criteria if not self._criterion_met(criterion, passed_commands)]
        if unmet:
            return (
                "The final answer claims completion before these planned checks passed: "
                + ", ".join(unmet)
                + ". Run the checks or update the plan with an honest blocker."
            )
        return None

    def _criterion_met(
        self,
        criterion: str,
        passed_commands: list[str] | None = None,
    ) -> bool:
        kind, separator, detail = criterion.partition(":")
        if separator and kind.strip().lower() == "evidence":
            expected = detail.strip().lower()
            return any(expected in str(item.get("summary", "")).lower() for item in self.evidence_records)
        if separator and kind.strip().lower() == "file":
            return detail.strip().replace("\\", "/") in self.changed_paths
        expected_command = detail if separator and kind.strip().lower() == "command" else criterion
        commands = passed_commands or [
            _normalize_command(str(item.get("command", "")))
            for item in self.verification_records
            if item.get("status") == "passed"
        ]
        return any(
            _commands_equivalent(_normalize_command(expected_command), passed)
            for passed in commands
        )

    def snapshot(self, *, include_records: bool = True) -> dict[str, Any]:
        return {
            "type": "execution_state",
            "disposition": self.disposition.value,
            "run_records": {
                name: [{key: deepcopy(value) for key, value in record.items()
                        if key != "execution_state"} for record in records]
                for name, records in self.run_records.items()
            } if include_records else {},
            "policy_denials": deepcopy(self.policy_denials),
            "recovery_counts": self.recovery_counts.copy(),
            "failure_streaks": self.failure_streaks.copy(),
            "recovery_attempt_count": self.recovery_attempt_count,
            "action_outcomes": {
                key: [{"ok": item.ok, "output_digest": item.output_digest} for item in values]
                for key, values in self._outcomes.items()
            },
            "failure_fingerprints": [
                {"action_type": fp.action_type, "target": fp.target,
                 "category": fp.category.value, "strategy_digest": fp.strategy_digest, "count": count}
                for fp, count in self._failure_fingerprints.items()
            ],
            "phase": self.phase.value,
            "step": self.step,
            "max_steps": self.max_steps,
            "workspace_generation": self.workspace_generation,
            "action_count": self.action_count,
            "context_chars": self.context_chars,
            "compacted_messages": self.compacted_messages,
            "plan_steps": self.plan_steps,
            "plan_revision": self.plan_revision,
            "plan_history": self.plan_history,
            "acceptance_criteria": self.acceptance_criteria,
            "changed_paths": self.changed_paths,
            "failed_hypotheses": self.failed_hypotheses,
            "failed_approaches": self.failed_approaches,
            "hypotheses": self.hypotheses,
            "evidence_records": self.evidence_records,
            "verification_records": self.verification_records,
            "context_packs": self.context_packs,
            "model_handoffs": self.model_handoffs,
            "blockers": self.blockers,
            "replan_required": self.replan_required,
            "replan_reason": self.replan_reason,
            "checkpoint_count": self.checkpoint_count,
            "checkpoint_reason": self.checkpoint_reason,
            "resume_count": self.resume_count,
            "resumed_from_run_id": self.resumed_from_run_id,
            "verification_confidence": self._verification_confidence(),
            "confidence": self._confidence(),
        }

    def _fingerprint(self, action: AgentAction) -> str:
        payload = json.dumps(
            action.model_dump(exclude_none=True, exclude={"rationale"}),
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

    def _confidence(self) -> dict[str, Any]:
        score = 0.5
        reasons: list[str] = []
        if self.plan_steps:
            completed = sum(item.get("status") == "completed" for item in self.plan_steps)
            score += 0.2 * completed / len(self.plan_steps)
            reasons.append(f"{completed}/{len(self.plan_steps)} plan steps completed")
        if self.evidence_records:
            score += min(0.2, len(self.evidence_records) * 0.02)
            reasons.append(f"{len(self.evidence_records)} evidence records")
        latest = {item["purpose"]: item for item in self.verification_records}
        passed = sum(item.get("status") == "passed" for item in latest.values())
        failed = sum(item.get("status") != "passed" for item in latest.values())
        score += min(0.35, passed * 0.12)
        score -= min(0.5, failed * 0.25)
        if self.replan_required:
            score -= 0.25
            reasons.append("replanning required")
        score = max(0.0, min(1.0, score))
        band = "high" if score >= 0.75 else "medium" if score >= 0.45 else "low"
        return {"score": round(score, 2), "band": band, "reasons": reasons}


def compact_message_history(
    messages: list[ChatMessage],
    *,
    max_chars: int,
) -> tuple[list[ChatMessage], int]:
    from .context_budget import bound_messages
    return bound_messages(messages, max_chars=max_chars)


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
        "list_transactions",
        "list_processes",
        "inspect_process",
        "read_process_logs",
        "process_events",
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
    if action.type in {"send_process_input", "stop_process", "restart_process"}:
        return ExecutionPhase.EXECUTE
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
