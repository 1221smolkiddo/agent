from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .durable_execution import (
    AutonomousExecutor,
    Command,
    DurableExecutionRuntime,
    ExecutionProjection,
    ExecutionStatus,
    TaskProjection,
)
from .schema import UpdatePlanAction


class MigrationMode(str, Enum):
    LEGACY = "legacy"
    SHADOW = "shadow"
    PRIMARY = "primary"
    ENGINE_ONLY = "engine_only"


class PromotionStage(int, Enum):
    TRACE_PROJECTION = 1
    PLANNING = 2
    SCHEDULING_BUDGETS = 3
    VERIFICATION_REPLANNING = 4
    SIDE_EFFECTS = 5
    RECOVERY_COMPLETION = 6
    ENGINE_ONLY = 7


PROMOTION_DECISION_TYPES: dict[PromotionStage, tuple[str, ...]] = {
    PromotionStage.PLANNING: ("planning",),
    PromotionStage.SCHEDULING_BUDGETS: ("scheduling", "budget"),
    PromotionStage.VERIFICATION_REPLANNING: (
        "verification", "diagnosis", "replanning",
    ),
    PromotionStage.SIDE_EFFECTS: ("tool_selection", "tool_result", "approval"),
    PromotionStage.RECOVERY_COMPLETION: ("recovery", "completion"),
    PromotionStage.ENGINE_ONLY: ("engine_only",),
}


@dataclass(frozen=True)
class Divergence:
    execution_id: str
    task_id: str | None
    decision_type: str
    legacy_decision: dict[str, Any]
    engine_decision: dict[str, Any]
    relevant_state: dict[str, Any]
    evidence_ids: tuple[str, ...]
    severity: str
    expected: bool
    probable_cause: str
    created_at: float


class ShadowDivergenceStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.executescript(
                """create table if not exists shadow_divergences (
                id integer primary key autoincrement, execution_id text not null,
                task_id text, decision_type text not null, severity text not null,
                expected integer not null, payload text not null, created_at real not null);
                create table if not exists shadow_comparisons (
                id integer primary key autoincrement, execution_id text not null,
                decision_type text not null, matched integer not null, created_at real not null);
                """
            )

    def record(self, divergence: Divergence) -> int:
        with sqlite3.connect(self.path) as conn:
            cursor = conn.execute(
                """insert into shadow_divergences
                (execution_id,task_id,decision_type,severity,expected,payload,created_at)
                values (?,?,?,?,?,?,?)""",
                (
                    divergence.execution_id, divergence.task_id,
                    divergence.decision_type, divergence.severity,
                    int(divergence.expected), json.dumps(asdict(divergence), sort_keys=True),
                    divergence.created_at,
                ),
            )
            return int(cursor.lastrowid)

    def record_comparison(
        self, execution_id: str, decision_type: str, *, matched: bool
    ) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """insert into shadow_comparisons
                (execution_id,decision_type,matched,created_at) values (?,?,?,?)""",
                (execution_id, decision_type, int(matched), time.time()),
            )

    def list(
        self,
        execution_id: str | None = None,
        *,
        decision_types: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        query = "select id,payload from shadow_divergences"
        conditions: list[str] = []
        params: list[Any] = []
        if execution_id:
            conditions.append("execution_id=?")
            params.append(execution_id)
        if decision_types:
            placeholders = ",".join("?" for _item in decision_types)
            conditions.append(f"decision_type in ({placeholders})")
            params.extend(decision_types)
        if conditions:
            query += " where " + " and ".join(conditions)
        query += " order by id"
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [{"id": row[0], **json.loads(row[1])} for row in rows]

    def has_comparison(self, execution_id: str, decision_type: str) -> bool:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                """select 1 from shadow_comparisons
                where execution_id=? and decision_type=? limit 1""",
                (execution_id, decision_type),
            ).fetchone()
        return row is not None

    def metrics(
        self,
        execution_id: str | None = None,
        *,
        decision_types: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        items = self.list(execution_id, decision_types=decision_types)
        unexpected = [item for item in items if not item["expected"]]
        critical = [item for item in unexpected if item["severity"] == "critical"]
        query = "select count(*) from shadow_comparisons"
        conditions: list[str] = []
        params: list[Any] = []
        if execution_id:
            conditions.append("execution_id=?")
            params.append(execution_id)
        if decision_types:
            placeholders = ",".join("?" for _item in decision_types)
            conditions.append(f"decision_type in ({placeholders})")
            params.extend(decision_types)
        if conditions:
            query += " where " + " and ".join(conditions)
        with sqlite3.connect(self.path) as conn:
            samples = int(conn.execute(query, tuple(params)).fetchone()[0])
        return {
            "samples": samples, "unexpected": len(unexpected), "critical": len(critical),
            "divergence_rate": len(unexpected) / samples if samples else 0.0,
        }


class ShadowComparator:
    def compare(
        self,
        *,
        execution_id: str,
        decision_type: str,
        legacy: dict[str, Any],
        engine: dict[str, Any],
        state: ExecutionProjection,
        task_id: str | None = None,
        evidence_ids: tuple[str, ...] = (),
    ) -> Divergence | None:
        left = _normalized(legacy)
        right = _normalized(engine)
        if left == right:
            return None
        severity = _divergence_severity(decision_type, left, right)
        expected = bool(legacy.get("shadow_expected") or engine.get("shadow_expected"))
        return Divergence(
            execution_id, task_id, decision_type, left, right,
            {
                "sequence": state.sequence, "status": state.status.value,
                "graph_version": state.graph_version,
                "task_state": state.tasks[task_id].state.value if task_id in state.tasks else None,
            }, evidence_ids, severity, expected,
            _probable_cause(decision_type, left, right), time.time(),
        )


ShadowDecisionProvider = Callable[
    [str, dict[str, Any], ExecutionProjection], dict[str, Any]
]


class ShadowRuntime:
    """Decision-only shadow runtime; it never dispatches mutating external effects."""

    MUTATING_ACTIONS = frozenset({
        "write_file", "edit_file", "apply_patch", "delete_file", "move_file",
        "run_shell", "start_process", "send_process_input", "stop_process",
        "restart_process", "update_memory", "invoke_tool",
    })

    def __init__(
        self,
        runtime: DurableExecutionRuntime,
        divergences: ShadowDivergenceStore,
        decision_provider: ShadowDecisionProvider,
    ) -> None:
        self.runtime = runtime
        self.divergences = divergences
        self.decision_provider = decision_provider
        self.comparator = ShadowComparator()

    def observe(
        self,
        execution_id: str,
        decision_type: str,
        legacy_decision: dict[str, Any],
        *,
        task_id: str | None = None,
        evidence_ids: tuple[str, ...] = (),
    ) -> Divergence | None:
        state = self.runtime.engine.state(execution_id)
        engine_decision = self.decision_provider(decision_type, legacy_decision, state)
        divergence = self.comparator.compare(
            execution_id=execution_id, decision_type=decision_type,
            legacy=legacy_decision, engine=engine_decision, state=state,
            task_id=task_id, evidence_ids=evidence_ids,
        )
        self.divergences.record_comparison(
            execution_id, decision_type, matched=divergence is None
        )
        if divergence:
            self.divergences.record(divergence)
        return divergence

    @classmethod
    def may_execute_independently(cls, action: dict[str, Any], *, isolated: bool) -> bool:
        action_type = str(action.get("type", ""))
        return action_type not in cls.MUTATING_ACTIONS or isolated


@dataclass(frozen=True)
class PromotionPolicy:
    minimum_samples: int = 100
    maximum_divergence_rate: float = 0.01
    maximum_critical_divergences: int = 0

    def evaluate(self, metrics: dict[str, Any]) -> tuple[bool, str]:
        if metrics["samples"] < self.minimum_samples:
            return False, "Insufficient shadow samples."
        if metrics["critical"] > self.maximum_critical_divergences:
            return False, "Critical shadow divergences remain."
        if metrics["divergence_rate"] > self.maximum_divergence_rate:
            return False, "Shadow divergence rate exceeds the promotion threshold."
        return True, "Promotion gate satisfied."


class MigrationController:
    OWNERSHIP = {
        PromotionStage.TRACE_PROJECTION: frozenset({"trace", "projection"}),
        PromotionStage.PLANNING: frozenset({"trace", "projection", "planning", "graph"}),
        PromotionStage.SCHEDULING_BUDGETS: frozenset({
            "trace", "projection", "planning", "graph", "scheduling", "budgets",
        }),
        PromotionStage.VERIFICATION_REPLANNING: frozenset({
            "trace", "projection", "planning", "graph", "scheduling", "budgets",
            "verification", "diagnosis", "replanning",
        }),
        PromotionStage.SIDE_EFFECTS: frozenset({
            "trace", "projection", "planning", "graph", "scheduling", "budgets",
            "verification", "diagnosis", "replanning", "side_effects", "approvals",
        }),
        PromotionStage.RECOVERY_COMPLETION: frozenset({
            "trace", "projection", "planning", "graph", "scheduling", "budgets",
            "verification", "diagnosis", "replanning", "side_effects", "approvals",
            "recovery", "completion",
        }),
        PromotionStage.ENGINE_ONLY: frozenset({"*"}),
    }

    def __init__(self, stage: PromotionStage = PromotionStage.TRACE_PROJECTION) -> None:
        self.stage = stage

    def engine_owns(self, concern: str) -> bool:
        owned = self.OWNERSHIP[self.stage]
        return "*" in owned or concern in owned

    def promote(
        self, target: PromotionStage, metrics: dict[str, Any], policy: PromotionPolicy
    ) -> None:
        if target.value != self.stage.value + 1:
            raise ValueError("Promotion must advance exactly one authority stage.")
        ok, reason = policy.evaluate(metrics)
        if not ok:
            raise RuntimeError(reason)
        self.stage = target


class MigrationStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        with sqlite3.connect(path) as conn:
            conn.execute(
                """create table if not exists runtime_migration_state (
                id integer primary key check(id=1), stage integer not null,
                updated_at real not null, metrics text not null)"""
            )
            conn.execute(
                """insert or ignore into runtime_migration_state
                (id,stage,updated_at,metrics) values (1,?,?,?)""",
                (PromotionStage.TRACE_PROJECTION.value, time.time(), "{}"),
            )

    def get(self) -> dict[str, Any]:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "select stage,updated_at,metrics from runtime_migration_state where id=1"
            ).fetchone()
        stage = PromotionStage(int(row[0]))
        return {
            "stage": stage.name.lower(), "stage_value": stage.value,
            "updated_at": row[1], "metrics": json.loads(row[2]),
        }

    def promote(
        self,
        target: PromotionStage,
        divergence_store: ShadowDivergenceStore,
        policy: PromotionPolicy,
    ) -> dict[str, Any]:
        current = self.get()
        controller = MigrationController(PromotionStage(current["stage_value"]))
        decision_types = PROMOTION_DECISION_TYPES.get(target)
        metrics = divergence_store.metrics(decision_types=decision_types)
        metrics["decision_types"] = list(decision_types or ())
        controller.promote(target, metrics, policy)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "update runtime_migration_state set stage=?,updated_at=?,metrics=? where id=1",
                (target.value, time.time(), json.dumps(metrics, sort_keys=True)),
            )
        return self.get()


WorkerFactory = Callable[[str], Callable[[TaskProjection, ExecutionProjection, threading.Event], dict[str, Any]]]


class ExecutionControlPlane:
    def __init__(self, runtime: DurableExecutionRuntime) -> None:
        self.runtime = runtime

    def create(self, goal: str, **kwargs: Any) -> str:
        return self.runtime.create_planned(goal, **kwargs)

    def inspect(self, execution_id: str) -> dict[str, Any]:
        return self.runtime.engine.state(execution_id).canonical()

    def command(self, execution_id: str, command: str, payload: dict[str, Any] | None = None) -> None:
        allowed = {
            "PauseExecution", "ResumeExecution", "CancelExecution",
            "GrantApproval", "RevokeApproval", "RecordCheckpoint",
        }
        if command not in allowed:
            raise PermissionError("The control plane cannot issue execution-plane commands.")
        if command == "RecordCheckpoint":
            self.runtime.engine.checkpoint(
                execution_id, str((payload or {}).get("reason", "control-plane"))
            )
            return
        self.runtime.engine.dispatch(Command(command, execution_id, payload or {}, actor="control-plane"))


class ExecutionPlane:
    def __init__(
        self,
        runtime: DurableExecutionRuntime,
        worker_factory: WorkerFactory,
        *,
        max_parallel: int = 4,
    ) -> None:
        self.runtime = runtime
        self.worker_factory = worker_factory
        self.max_parallel = max_parallel
        self._threads: dict[str, threading.Thread] = {}
        self._results: dict[str, ExecutionProjection | BaseException] = {}
        self._lock = threading.RLock()

    def start(self, execution_id: str) -> bool:
        with self._lock:
            current = self._threads.get(execution_id)
            if current and current.is_alive():
                return False
            thread = threading.Thread(
                target=self._run, args=(execution_id,),
                name=f"agent47-execution-{execution_id[-8:]}", daemon=True,
            )
            self._threads[execution_id] = thread
            thread.start()
            return True

    def _run(self, execution_id: str) -> None:
        executor = AutonomousExecutor(
            self.runtime, self.worker_factory(execution_id),
            owner=f"execution-plane:{threading.get_ident()}",
            max_parallel=self.max_parallel,
        )
        try:
            result: ExecutionProjection | BaseException = executor.run(execution_id)
        except BaseException as exc:
            result = exc
        finally:
            executor.close()
        with self._lock:
            self._results[execution_id] = result

    def recover_active(self) -> list[str]:
        started: list[str] = []
        for item in self.runtime.store.executions(limit=10_000):
            execution_id = str(item["execution_id"])
            state = self.runtime.recover(execution_id)
            if state.status == ExecutionStatus.ACTIVE:
                self._recover_orphaned_tasks(execution_id)
            if state.status == ExecutionStatus.ACTIVE and self.start(execution_id):
                started.append(execution_id)
        return started

    def _recover_orphaned_tasks(self, execution_id: str) -> None:
        state = self.runtime.engine.state(execution_id)
        for task in list(state.tasks.values()):
            transitions: list[str] = []
            if task.state.value == "running":
                transitions = ["waiting", "ready"]
            elif task.state.value == "verifying":
                transitions = ["diagnosing", "replanning", "ready"]
            elif task.state.value == "diagnosing":
                transitions = ["replanning", "ready"]
            elif task.state.value == "replanning":
                transitions = ["ready"]
            for target in transitions:
                self.runtime.engine.dispatch(Command("TransitionTask", execution_id, {
                    "task_id": task.id, "to": target,
                    "reason": "Recovered orphaned execution-plane worker.",
                }))

    def status(self, execution_id: str) -> dict[str, Any]:
        with self._lock:
            thread = self._threads.get(execution_id)
            result = self._results.get(execution_id)
        return {
            "execution_id": execution_id,
            "worker_alive": bool(thread and thread.is_alive()),
            "status": self.runtime.engine.state(execution_id).status.value,
            "error": str(result) if isinstance(result, BaseException) else None,
        }

    def wait(self, execution_id: str, timeout: float | None = None) -> ExecutionProjection:
        thread = self._threads[execution_id]
        thread.join(timeout)
        if thread.is_alive():
            raise TimeoutError("Execution plane worker is still running.")
        result = self._results[execution_id]
        if isinstance(result, BaseException):
            raise result
        return result


def _normalized(value: dict[str, Any]) -> dict[str, Any]:
    ignored = {"latency_ms", "timestamp", "created_at", "event_id", "request_id"}
    return {key: value[key] for key in sorted(value) if key not in ignored}


def _divergence_severity(
    decision_type: str, legacy: dict[str, Any], engine: dict[str, Any]
) -> str:
    if decision_type in {"approval", "side_effect", "completion"}:
        return "critical"
    if legacy.get("ok") != engine.get("ok") or legacy.get("blocked") != engine.get("blocked"):
        return "high"
    if decision_type in {"planning", "verification", "model_route"}:
        return "medium"
    return "low"


def _probable_cause(
    decision_type: str, legacy: dict[str, Any], engine: dict[str, Any]
) -> str:
    differing = sorted(key for key in set(legacy) | set(engine) if legacy.get(key) != engine.get(key))
    return f"{decision_type} fields differ: {', '.join(differing) or 'representation'}"


def default_shadow_decision(
    decision_type: str, legacy: dict[str, Any], state: ExecutionProjection
) -> dict[str, Any]:
    if decision_type == "planning":
        return plan_decision_from_state(state)
    if decision_type == "scheduling":
        active = [task for task in state.tasks.values() if task.state.value == "running"]
        return {
            "task_id": active[0].id if active else None,
            "policy": legacy.get("policy", "priority"),
        }
    if decision_type == "budget":
        return dict(legacy)
    if decision_type == "tool_selection":
        active = any(task.state.value == "running" for task in state.tasks.values())
        return {"action": legacy.get("action"), "allowed": active}
    if decision_type == "tool_result":
        effect_id = next(reversed(state.effects), None)
        effect = state.effects.get(effect_id) if effect_id else None
        return {
            "ok": bool(effect and effect.state.value == "committed"),
            "effect_state": effect.state.value if effect else "missing",
        }
    if decision_type == "completion":
        return {
            "blocked": state.status in {ExecutionStatus.FAILED, ExecutionStatus.CANCELLED},
            "status": state.status.value,
        }
    return dict(legacy)


def plan_decision_from_legacy(action: UpdatePlanAction) -> dict[str, Any]:
    tasks = []
    for index, step in enumerate(action.steps, 1):
        tasks.append({
            "id": step.id or f"legacy-{index}",
            "title": step.step,
            "parent_id": step.parent_id,
            "dependencies": list(step.depends_on),
            "criteria": list(step.acceptance_criteria) or [f"{step.step} complete"],
        })
    return _canonical_plan(tasks)


def plan_decision_from_state(state: ExecutionProjection) -> dict[str, Any]:
    tasks = []
    for task in state.tasks.values():
        tasks.append({
            "id": task.id,
            "title": task.title,
            "parent_id": task.parent_id,
            "dependencies": list(task.dependencies),
            "criteria": [state.criteria[item].description for item in task.criteria],
        })
    return _canonical_plan(tasks)


def _canonical_plan(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    aliases = {
        str(task.get("id") or f"task-{index}"): f"task-{index}"
        for index, task in enumerate(tasks, 1)
    }
    normalized: list[dict[str, Any]] = []
    for index, task in enumerate(tasks, 1):
        task_id = str(task.get("id") or f"task-{index}")
        parent_id = task.get("parent_id")
        normalized.append({
            "id": aliases[task_id],
            "title": " ".join(str(task.get("title", "")).lower().split()),
            "parent_id": aliases.get(str(parent_id)) if parent_id else None,
            "dependencies": sorted(
                aliases.get(str(item), str(item))
                for item in task.get("dependencies", [])
            ),
            "criteria": sorted(
                " ".join(str(item).lower().split())
                for item in task.get("criteria", [])
            ),
        })
    titles = [item["title"] for item in normalized]
    roots = [item for item in normalized if not item["dependencies"]]
    edge_count = sum(len(item["dependencies"]) for item in normalized)
    maximum_dependencies = max(
        (len(item["dependencies"]) for item in normalized), default=0
    )
    phase_terms = {
        "discovery": ("inspect", "discover", "analyze", "analyse", "investigate", "review"),
        "implementation": ("implement", "change", "build", "fix", "write", "create", "update"),
        "verification": ("verify", "test", "check", "validate", "confirm"),
    }
    phases = {
        phase: any(any(term in title for term in terms) for title in titles)
        for phase, terms in phase_terms.items()
    }
    if not normalized:
        count_band = "empty"
    elif len(normalized) == 1:
        count_band = "single"
    elif len(normalized) <= 4:
        count_band = "compact"
    else:
        count_band = "extended"
    if edge_count == 0:
        dependency_shape = "independent"
    elif len(roots) == 1 and maximum_dependencies <= 1:
        dependency_shape = "linear"
    else:
        dependency_shape = "branched"
    return {
        "task_count_band": count_band,
        "dependency_shape": dependency_shape,
        "hierarchical": any(item["parent_id"] for item in normalized),
        "criteria_complete": bool(normalized) and all(item["criteria"] for item in normalized),
        "phases": phases,
    }
