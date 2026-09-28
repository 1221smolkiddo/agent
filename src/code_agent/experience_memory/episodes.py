"""Typed, bounded observations derived from Agent47's recorded run evidence."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from .scope import RepositoryScope

if TYPE_CHECKING:
    from ..agent import AgentRunResult
    from ..durable_execution import ExecutionProjection


class EpisodeOutcome(StrEnum):
    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    FAILED = "failed"
    BLOCKED = "blocked"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class VerificationObservation:
    purpose: str
    command: str
    passed: bool
    diagnostic: str = ""


@dataclass(frozen=True)
class EngineeringEpisode:
    schema_version: int
    agent_version: str
    execution_id: str
    run_id: int
    task_id: str | None
    started_at: str | None
    ended_at: str | None
    goal: str
    changed_paths: tuple[str, ...]
    plan: tuple[str, ...]
    diagnostics: tuple[str, ...]
    failed_approaches: tuple[str, ...]
    successful_approach: str
    verification: tuple[VerificationObservation, ...]
    reviewer: str
    blockers: tuple[str, ...]
    final_result: str
    outcome: EpisodeOutcome
    runtime_status: str
    branch: str | None
    head: str | None


def _bounded(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + " <truncated>"


def _strings(items: object, *, count: int, length: int) -> tuple[str, ...]:
    if not isinstance(items, (list, tuple)):
        return ()
    values = tuple(_bounded(value, length) for value in items[:count] if value)
    return values + ("<additional items truncated>",) if len(items) > count else values


class EngineeringEpisodeBuilder:
    """Extract only structured observations, never transcripts, raw diffs, or stdout."""

    def build(
        self, result: AgentRunResult, state: ExecutionProjection | None,
        task_id: str | None, scope: RepositoryScope,
    ) -> EngineeringEpisode:
        verification = self._verification(result.verification_results)
        outcome = self._outcome(result, state, verification)
        plans = self._plans(result.plan_updates)
        diagnostics = self._diagnostics(result)
        approaches = tuple(
            _bounded(f"{item.get('action', 'attempt')}: {item.get('output', '')}", 240)
            for item in result.failed_actions[:5] if isinstance(item, dict)
        )
        if len(result.failed_actions) > 5:
            approaches += ("<additional failed approaches truncated>",)
        blockers = self._blockers(result)
        status = state.status.value if state is not None else "unknown"
        try:
            agent_version = version("agent47")
        except PackageNotFoundError:
            agent_version = "unknown"
        return EngineeringEpisode(
            schema_version=1, agent_version=agent_version,
            execution_id=_bounded(result.durable_execution_id, 100), run_id=result.run_id,
            task_id=_bounded(task_id, 100) or None,
            started_at=_bounded(state.created_at, 40) or None if state else None,
            ended_at=_bounded(state.updated_at, 40) or None if state else None,
            goal=_bounded(result.clean_task or result.task, 600),
            changed_paths=_strings(result.changed_paths, count=12, length=160),
            plan=plans, diagnostics=diagnostics, failed_approaches=approaches,
            successful_approach=_bounded(
                plans[-1] if plans else result.message, 500,
            ) if outcome == EpisodeOutcome.VERIFIED else "",
            verification=verification, reviewer=self._reviewer(result.review_records),
            blockers=blockers, final_result=_bounded(result.message, 700),
            outcome=outcome, runtime_status=status, branch=scope.branch, head=scope.head,
        )

    @staticmethod
    def _verification(items: list[dict[str, Any]]) -> tuple[VerificationObservation, ...]:
        latest: dict[str, dict[str, Any]] = {}
        for item in items:
            if isinstance(item, dict):
                key = str(item.get("purpose") or item.get("command") or "check")
                latest[key] = item
        observations = []
        for item in list(latest.values())[:8]:
            diagnostics = item.get("diagnostics")
            detail = diagnostics.get("summary", "") if isinstance(diagnostics, dict) else ""
            observations.append(VerificationObservation(
                _bounded(item.get("purpose", "check"), 40),
                _bounded(item.get("command", ""), 180), item.get("ok") is True,
                _bounded(detail, 240),
            ))
        return tuple(observations)

    @staticmethod
    def _plans(updates: list[dict[str, Any]]) -> tuple[str, ...]:
        if not updates:
            return ()
        steps = updates[-1].get("steps", [])
        if not isinstance(steps, list):
            return ()
        return _strings(
            [f"{step.get('status', 'unknown')}: {step.get('step', '')}"
             for step in steps if isinstance(step, dict)], count=6, length=220,
        )

    @staticmethod
    def _diagnostics(result: AgentRunResult) -> tuple[str, ...]:
        collected: list[str] = []
        for record in result.command_records:
            report = record.get("diagnostics") if isinstance(record, dict) else None
            if not isinstance(report, dict):
                continue
            for item in report.get("diagnostics", [])[:5]:
                if isinstance(item, dict):
                    collected.append(_bounded(
                        f"{item.get('category', '')} {item.get('rule', '')}: "
                        f"{item.get('message', '')}", 240,
                    ))
            history = report.get("history")
            if isinstance(history, dict) and history.get("recurring"):
                collected.append("Recurring diagnostic signature observed in prior runs.")
        for check in result.verification_results:
            detail = check.get("diagnostics") if isinstance(check, dict) else None
            if isinstance(detail, dict) and detail.get("summary"):
                collected.append(_bounded(detail["summary"], 240))
        return _strings(collected, count=8, length=240)

    @staticmethod
    def _reviewer(records: list[dict[str, Any]]) -> str:
        if not records:
            return "not_run"
        latest = records[-1]
        if latest.get("error"):
            return "unavailable"
        return "passed" if latest.get("ok") is True else "rejected"

    @staticmethod
    def _blockers(result: AgentRunResult) -> tuple[str, ...]:
        values: list[str] = []
        if result.plan_updates:
            raw = result.plan_updates[-1].get("blockers", [])
            if isinstance(raw, list):
                values.extend(str(item) for item in raw)
        if result.blocked:
            values.extend(str(item.get("output", "")) for item in result.failed_actions[-2:])
        return _strings(values, count=4, length=200)

    @staticmethod
    def _outcome(
        result: AgentRunResult, state: ExecutionProjection | None,
        checks: tuple[VerificationObservation, ...],
    ) -> EpisodeOutcome:
        latest_review = result.review_records[-1] if result.review_records else None
        reviewer_rejected = bool(latest_review and latest_review.get("ok") is False)
        reviewer_unavailable = bool(latest_review and latest_review.get("error"))
        runtime_decisions = []
        if state is not None:
            for criterion in state.criteria.values():
                if criterion.verification_ids:
                    latest_id = criterion.verification_ids[-1]
                    decision = state.verifications.get(latest_id)
                    if isinstance(decision, dict):
                        runtime_decisions.append(decision)
        if any(not check.passed for check in checks) or any(
            decision.get("decision") == "failed" for decision in runtime_decisions
        ):
            return EpisodeOutcome.FAILED
        if result.blocked or (state is not None and state.status.value == "paused"):
            return EpisodeOutcome.BLOCKED
        if state is not None and state.status.value == "failed":
            return EpisodeOutcome.FAILED
        if reviewer_rejected or reviewer_unavailable or any(
            decision.get("decision") in {"blocked", "inconclusive"}
            for decision in runtime_decisions
        ):
            return EpisodeOutcome.PARTIALLY_VERIFIED
        if not checks or not all(check.passed for check in checks):
            return EpisodeOutcome.UNVERIFIED
        if state is None or state.status.value != "complete":
            return EpisodeOutcome.PARTIALLY_VERIFIED
        if state.criteria and len(runtime_decisions) != len(state.criteria):
            return EpisodeOutcome.PARTIALLY_VERIFIED
        if runtime_decisions and not all(
            decision.get("decision") == "verified" for decision in runtime_decisions
        ):
            return EpisodeOutcome.PARTIALLY_VERIFIED
        successful_mutations = [
            record for record in result.mutation_records if record.get("ok") is True
        ]
        if not result.changed_paths or not successful_mutations or not all(
            record.get("verified") is True or record.get("transaction_state") == "committed"
            for record in successful_mutations
        ):
            return EpisodeOutcome.PARTIALLY_VERIFIED
        return EpisodeOutcome.VERIFIED
