from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .durable_execution import (
    Command,
    ExecutionEngine,
    ExecutionProjection,
    SQLiteEventStore,
)
from .execution_contracts import (
    AdapterEvidence,
    CompensationOutcome,
    EffectOutcome,
    PreparedEffect,
    TransactionalAdapter,
)


class FaultPoint(str, Enum):
    BEFORE_PERSISTENCE = "before_persistence"
    BEFORE_COMMIT = "before_commit"
    AFTER_PERSISTENCE = "after_persistence"
    BEFORE_ADAPTER_EXECUTE = "before_adapter_execute"
    AFTER_ADAPTER_EXECUTE = "after_adapter_execute"
    BEFORE_CHECKPOINT = "before_checkpoint"
    AFTER_CHECKPOINT = "after_checkpoint"


@dataclass(frozen=True)
class ReliabilityTarget:
    scenarios: int = 1000
    maximum_projection_divergences: int = 0
    maximum_duplicate_effects: int = 0
    maximum_invariant_failures: int = 0
    maximum_unrecovered_scenarios: int = 0


@dataclass
class QualificationReport:
    scenarios: int = 0
    projection_divergences: int = 0
    duplicate_effects: int = 0
    invariant_failures: int = 0
    unrecovered_scenarios: int = 0
    injected_faults: dict[str, int] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)

    def qualifies(self, target: ReliabilityTarget) -> tuple[bool, list[str]]:
        failures: list[str] = []
        if self.scenarios < target.scenarios:
            failures.append("insufficient scenarios")
        for name in (
            "projection_divergences", "duplicate_effects", "invariant_failures",
            "unrecovered_scenarios",
        ):
            if getattr(self, name) > getattr(target, f"maximum_{name}"):
                failures.append(name)
        return not failures, failures


class FaultScript:
    def __init__(
        self, points: set[FaultPoint], *, seed: int = 47, probability: float = 1.0
    ) -> None:
        self.points = set(points)
        self.random = random.Random(seed)
        self.probability = probability
        self.counts: dict[str, int] = {}
        self.armed = True

    def inject(self, stage: str, _execution_id: str = "") -> None:
        try:
            point = FaultPoint(stage)
        except ValueError:
            return
        if self.armed and point in self.points and self.random.random() <= self.probability:
            self.counts[stage] = self.counts.get(stage, 0) + 1
            self.armed = False
            raise InjectedCrash(stage)


class InjectedCrash(RuntimeError):
    pass


class LatencyEventStore(SQLiteEventStore):
    def __init__(self, path: Path, *, latency_seconds: float, **kwargs: Any) -> None:
        self.latency_seconds = latency_seconds
        super().__init__(path, **kwargs)

    def append(self, *args: Any, **kwargs: Any):
        time.sleep(self.latency_seconds)
        return super().append(*args, **kwargs)


class FaultInjectingAdapter:
    def __init__(self, adapter: TransactionalAdapter, script: FaultScript) -> None:
        self.adapter = adapter
        self.script = script
        self.capabilities = adapter.capabilities

    def prepare(self, request, context):
        return self.adapter.prepare(request, context)

    def execute(self, prepared: PreparedEffect) -> EffectOutcome:
        self.script.inject(FaultPoint.BEFORE_ADAPTER_EXECUTE.value, prepared.context.execution_id)
        outcome = self.adapter.execute(prepared)
        self.script.inject(FaultPoint.AFTER_ADAPTER_EXECUTE.value, prepared.context.execution_id)
        return outcome

    def reconcile(self, prepared: PreparedEffect) -> EffectOutcome:
        return self.adapter.reconcile(prepared)

    def compensate(
        self, prepared: PreparedEffect, outcome: EffectOutcome
    ) -> CompensationOutcome:
        return self.adapter.compensate(prepared, outcome)

    def verify(
        self, prepared: PreparedEffect, outcome: EffectOutcome
    ) -> list[AdapterEvidence]:
        return self.adapter.verify(prepared, outcome)

    def cancel(self, prepared: PreparedEffect) -> bool:
        return self.adapter.cancel(prepared)


class ReliabilityQualifier:
    """Runs reproducible crash/retry workloads and compares their canonical projections."""

    EVENT_BOUNDARIES = (
        FaultPoint.BEFORE_PERSISTENCE,
        FaultPoint.BEFORE_COMMIT,
        FaultPoint.AFTER_PERSISTENCE,
    )

    def qualify_event_boundaries(self, root: Path) -> QualificationReport:
        report = QualificationReport()
        for boundary in self.EVENT_BOUNDARIES:
            report.scenarios += 1
            script = FaultScript({boundary})
            store = SQLiteEventStore(
                root / f"qualification-{boundary.value}.db",
                fault_injector=script.inject,
            )
            engine = ExecutionEngine(store)
            script.armed = False
            execution_id = engine.create("qualification", execution_id=f"exec-{boundary.value}")
            command = Command(
                "MutateGraph", execution_id,
                {
                    "base_version": 0, "operation": "insert",
                    "rationale": "qualification", "affected_subtree": None,
                    "tasks": [{"id": "task", "title": "Task", "criteria": ["done"]}],
                },
                command_id="stable-mutation",
            )
            script.armed = True
            try:
                engine.dispatch(command)
            except InjectedCrash:
                pass
            engine._cache.clear()
            try:
                engine.dispatch(command)
                recovered = engine.replay(execution_id)
                self._check_stream(store, recovered, report)
            except Exception as exc:
                report.unrecovered_scenarios += 1
                report.failures.append({"boundary": boundary.value, "error": str(exc)})
            report.injected_faults.update(script.counts)
        return report

    @staticmethod
    def compare(
        uninterrupted: ExecutionProjection, recovered: ExecutionProjection
    ) -> tuple[bool, str]:
        left = _stable_projection(uninterrupted)
        right = _stable_projection(recovered)
        if left == right:
            return True, "identical"
        return False, "canonical projections diverged"

    @staticmethod
    def _check_stream(
        store: SQLiteEventStore,
        state: ExecutionProjection,
        report: QualificationReport,
    ) -> None:
        events = store.load(state.id)
        sequences = [event.sequence for event in events]
        if sequences != list(range(1, len(events) + 1)):
            report.invariant_failures += 1
        effect_keys = [
            event.payload.get("idempotency_key")
            for event in events if event.type == "EffectRequested"
        ]
        if len(effect_keys) != len(set(effect_keys)):
            report.duplicate_effects += 1


def _stable_projection(state: ExecutionProjection) -> str:
    payload = state.canonical()
    payload["id"] = "normalized"
    payload["created_at"] = "normalized"
    payload["updated_at"] = "normalized"
    return json.dumps(payload, sort_keys=True, default=str)
