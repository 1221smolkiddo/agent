from __future__ import annotations

from collections import defaultdict
from typing import Any

from .durable_execution import (
    BudgetProjection,
    CriticalPathPolicy,
    ExecutionProjection,
    ExecutionTrace,
    SQLiteEventStore,
    TaskState,
    _critical_path_weights,
)


class ExecutionInspector:
    """Queryable observability model derived entirely from immutable execution state."""

    def __init__(self, store: SQLiteEventStore) -> None:
        self.store = store
        self.trace = ExecutionTrace(store)

    def explain(self, state: ExecutionProjection) -> dict[str, Any]:
        result: dict[str, Any] = {
            "execution": self.summary(state),
            "blocked": self.blockers(state),
            "unsatisfied_criteria": self.unsatisfied_criteria(state),
            "critical_path": self.critical_path(state),
            "budget_hotspots": self.budget_hotspots(state),
            "recent_replans": self.replans(state.id)[-10:],
            "model_decisions": state.model_decisions[-20:],
        }
        if state.active_profile:
            result["active_profile"] = state.active_profile
            result["execution_profile"] = state.active_profile
        if state.stats:
            result["stats"] = state.stats
            result["execution_stats"] = state.stats
        return result

    @staticmethod
    def summary(state: ExecutionProjection) -> dict[str, Any]:
        counts: dict[str, int] = defaultdict(int)
        for task in state.tasks.values():
            counts[task.state.value] += 1
        return {
            "execution_id": state.id, "goal": state.goal, "status": state.status.value,
            "sequence": state.sequence, "graph_version": state.graph_version,
            "engine_version": state.engine_version,
            "compatibility_version": state.compatibility_version,
            "schema_version": state.schema_version,
            "task_counts": dict(sorted(counts.items())),
        }

    @staticmethod
    def blockers(state: ExecutionProjection) -> list[dict[str, Any]]:
        blocked: list[dict[str, Any]] = []
        for task in state.tasks.values():
            unmet_dependencies = [
                dependency for dependency in task.dependencies
                if state.tasks[dependency].state != TaskState.COMPLETE
            ]
            unmet_criteria = [cid for cid in task.criteria if not state.criteria[cid].satisfied]
            if task.state in {TaskState.BLOCKED, TaskState.WAITING} or unmet_dependencies:
                approvals = [
                    approval.id for approval in state.approvals.values()
                    if task.id in approval.task_ids and not approval.granted_at
                ]
                blocked.append({
                    "task_id": task.id, "state": task.state.value,
                    "dependencies": unmet_dependencies, "criteria": unmet_criteria,
                    "pending_approvals": approvals,
                })
        return blocked

    @staticmethod
    def unsatisfied_criteria(state: ExecutionProjection) -> list[dict[str, Any]]:
        return [
            {
                "criterion_id": criterion.id, "task_id": criterion.task_id,
                "description": criterion.description,
                "evidence_ids": list(criterion.evidence_ids),
                "latest_verification": (
                    state.verifications[criterion.verification_ids[-1]]
                    if criterion.verification_ids else None
                ),
            }
            for criterion in state.criteria.values() if not criterion.satisfied
        ]

    @staticmethod
    def evidence_for_task(state: ExecutionProjection, task_id: str) -> list[dict[str, Any]]:
        return [
            {
                "evidence_id": item.id, "kind": item.kind, "summary": item.summary,
                "version": item.version, "supersedes": item.supersedes,
                "criteria": [
                    criterion.id for criterion in state.criteria.values()
                    if item.id in criterion.evidence_ids
                ],
            }
            for item in state.evidence.values() if item.task_id == task_id
        ]

    def replans(self, execution_id: str) -> list[dict[str, Any]]:
        return [
            {"sequence": event.sequence, **event.payload}
            for event in self.store.load(execution_id)
            if event.type == "GraphVersionCreated"
        ]

    @staticmethod
    def critical_path(state: ExecutionProjection) -> list[dict[str, Any]]:
        if not state.tasks:
            return []
        weights = _critical_path_weights(state.tasks)
        ordered = CriticalPathPolicy().order(list(state.tasks.values()), state)
        return [
            {"task_id": task.id, "weight": weights[task.id], "state": task.state.value}
            for task in ordered
        ]

    @staticmethod
    def budget_hotspots(state: ExecutionProjection) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for scope, budget in state.budgets.items():
            rows.extend(_budget_rows(scope, budget))
        return sorted(rows, key=lambda item: item["utilization"], reverse=True)

    def trace_summary(self, execution_id: str) -> dict[str, Any]:
        trace = self.trace.export(execution_id)
        by_type: dict[str, int] = defaultdict(int)
        for event in trace["events"]:
            by_type[event["type"]] += 1
        return {
            "execution_id": execution_id, "event_count": trace["event_count"],
            "events_by_type": dict(sorted(by_type.items())), "cost": trace["cost"],
            "timings": trace["timings"],
        }


def _budget_rows(scope: str, budget: BudgetProjection) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for kind, limit in budget.limits.items():
        consumed = budget.consumed.get(kind, 0)
        reserved = budget.reserved.get(kind, 0)
        rows.append({
            "scope": scope, "kind": kind, "limit": limit,
            "consumed": consumed, "reserved": reserved,
            "remaining": budget.available(kind),
            "utilization": (consumed + reserved) / limit if limit else 1.0,
        })
    return rows
