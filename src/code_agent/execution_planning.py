from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .durable_execution import Command, ExecutionEngine, ExecutionProjection, InvariantError


MutationOperation = Literal[
    "insert", "delete", "split", "merge", "update", "change_dependencies"
]


@dataclass(frozen=True)
class GraphMutationProposal:
    operation: MutationOperation
    rationale: str
    affected_subtree: str | None
    task_id: str | None = None
    task_ids: tuple[str, ...] = ()
    tasks: tuple[dict[str, Any], ...] = ()
    task: dict[str, Any] | None = None
    dependencies: tuple[str, ...] = ()
    changes: dict[str, Any] = field(default_factory=dict)
    dependency_updates: tuple[dict[str, Any], ...] = ()

    def command_payload(self, base_version: int) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "base_version": base_version,
            "operation": self.operation,
            "rationale": self.rationale,
            "affected_subtree": self.affected_subtree,
        }
        if self.task_id:
            payload["task_id"] = self.task_id
        if self.task_ids:
            payload["task_ids"] = list(self.task_ids)
        if self.tasks:
            payload["tasks"] = [dict(item) for item in self.tasks]
        if self.task is not None:
            payload["task"] = dict(self.task)
        if self.dependencies:
            payload["dependencies"] = list(self.dependencies)
        if self.changes:
            payload["changes"] = dict(self.changes)
        if self.dependency_updates:
            payload["dependency_updates"] = [dict(item) for item in self.dependency_updates]
        return payload


class MutationOnlyPlanner:
    """Normalizes both initial planning and replanning into graph mutations."""

    OPERATIONS = frozenset({
        "insert", "delete", "split", "merge", "update", "change_dependencies",
    })

    def initial(self, goal: str, tasks: list[dict[str, Any]]) -> GraphMutationProposal:
        if not tasks:
            tasks = [{"id": "task-root", "title": goal, "criteria": [f"Goal achieved: {goal}"]}]
        return GraphMutationProposal(
            "insert", "initial goal decomposition", None,
            tasks=tuple(self._validate_tasks(tasks)),
        )

    def parse(self, raw: dict[str, Any], state: ExecutionProjection) -> GraphMutationProposal:
        operation = str(raw.get("operation", ""))
        if operation not in self.OPERATIONS:
            raise ValueError("Planner output must be a supported graph mutation, not a full graph.")
        proposal = GraphMutationProposal(
            operation=operation,  # type: ignore[arg-type]
            rationale=str(raw.get("rationale") or "planner mutation"),
            affected_subtree=raw.get("affected_subtree"),
            task_id=raw.get("task_id"),
            task_ids=tuple(str(item) for item in raw.get("task_ids", [])),
            tasks=tuple(self._validate_tasks(list(raw.get("tasks", [])))),
            task=dict(raw["task"]) if isinstance(raw.get("task"), dict) else None,
            dependencies=tuple(str(item) for item in raw.get("dependencies", [])),
            changes=dict(raw.get("changes", {})),
            dependency_updates=tuple(
                dict(item) for item in raw.get("dependency_updates", []) if isinstance(item, dict)
            ),
        )
        if state.graph_version and operation == "insert" and not proposal.affected_subtree:
            raise ValueError("Replanning insertions must identify an affected subtree.")
        return proposal

    @staticmethod
    def _validate_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, task in enumerate(tasks, 1):
            if not isinstance(task, dict) or not str(task.get("title", "")).strip():
                raise ValueError("Planner tasks require titles.")
            item = dict(task)
            item.setdefault("id", f"task-{index}")
            if not item.get("criteria"):
                raise ValueError(f"Planner task {item['id']} requires acceptance criteria.")
            item.setdefault("dependencies", [])
            normalized.append(item)
        return normalized


class PlanningService:
    def __init__(self, engine: ExecutionEngine, planner: MutationOnlyPlanner | None = None) -> None:
        self.engine = engine
        self.planner = planner or MutationOnlyPlanner()

    def apply(self, execution_id: str, proposal: GraphMutationProposal) -> int:
        state = self.engine.state(execution_id)
        events = self.engine.dispatch(Command(
            "MutateGraph", execution_id, proposal.command_payload(state.graph_version),
            expected_sequence=state.sequence,
        ))
        if not events or events[0].type != "GraphVersionCreated":
            raise InvariantError("Planner mutation did not create a graph version.")
        return int(events[0].payload["version"])
