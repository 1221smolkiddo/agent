from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .execution_profiles import TaskIntent


ENGINE_VERSION = "1.0.0"
CURRENT_COMPATIBILITY_VERSION = "1"
CURRENT_EVENT_SCHEMA_VERSION = 1
SUPPORTED_COMPATIBILITY_VERSIONS = frozenset({"1"})

# A worker may submit observations and invoke its pre-authorized effects, but it
# cannot turn those observations into durable lifecycle truth.
WORKER_FORBIDDEN_COMMANDS = frozenset({
    "TransitionTask", "CompleteExecution", "FailExecution", "VerifyCriterion",
    "RecordVerificationDecision", "RecordDiagnosis", "RecordRepairDecision",
    "MutateGraph", "AddTasks", "ScheduleTask", "ConfigureBudget", "ReserveBudget",
    "ConsumeBudget", "ReleaseBudget", "RequestApproval", "GrantApproval", "RevokeApproval",
})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class ConcurrencyError(RuntimeError):
    pass


class InvariantError(RuntimeError):
    pass


class LeaseError(RuntimeError):
    pass


class TaskState(str, Enum):
    QUEUED = "queued"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    BLOCKED = "blocked"
    VERIFYING = "verifying"
    DIAGNOSING = "diagnosing"
    REPLANNING = "replanning"
    VERIFIED = "verified"
    COMPLETE = "complete"
    SUPERSEDED = "superseded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EffectState(str, Enum):
    REQUESTED = "requested"
    AUTHORIZED = "authorized"
    PREPARED = "prepared"
    DISPATCHED = "dispatched"
    # PENDING and RUNNING are retained solely to replay pre-effect-pipeline streams.
    PENDING = "pending"
    RUNNING = "running"
    COMMITTED = "committed"
    COMPENSATED = "compensated"
    CANCELLED = "cancelled"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    UNKNOWN = "unknown"


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    BLOCKED = "blocked"


class BlockedReason(str, Enum):
    APPROVAL = "approval"
    EXTERNAL_SERVICE = "external_service"
    MISSING_RESOURCE = "missing_resource"
    DEPENDENCY = "dependency"
    LEASE = "lease"
    BUDGET = "budget"
    POLICY = "policy"
    USER_INPUT = "user_input"
    OTHER = "other"


TERMINAL_TASK_STATES = {TaskState.COMPLETE, TaskState.SUPERSEDED, TaskState.FAILED, TaskState.CANCELLED}
TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.QUEUED: frozenset({TaskState.READY, TaskState.BLOCKED, TaskState.CANCELLED}),
    TaskState.READY: frozenset({TaskState.RUNNING, TaskState.BLOCKED, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset({TaskState.WAITING, TaskState.BLOCKED, TaskState.VERIFYING, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.WAITING: frozenset({TaskState.READY, TaskState.CANCELLED}),
    TaskState.BLOCKED: frozenset({TaskState.READY, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.VERIFYING: frozenset({TaskState.VERIFIED, TaskState.DIAGNOSING}),
    TaskState.DIAGNOSING: frozenset({TaskState.READY, TaskState.REPLANNING, TaskState.FAILED}),
    TaskState.REPLANNING: frozenset({TaskState.READY, TaskState.SUPERSEDED, TaskState.FAILED}),
    TaskState.VERIFIED: frozenset({TaskState.COMPLETE}),
    TaskState.COMPLETE: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


@dataclass(frozen=True)
class ExecutionEvent:
    execution_id: str
    sequence: int
    type: str
    payload: dict[str, Any]
    event_id: str
    command_id: str
    causation_id: str | None
    correlation_id: str
    created_at: str
    schema_version: int = CURRENT_EVENT_SCHEMA_VERSION


EventUpcaster = Callable[[dict[str, Any]], dict[str, Any]]


class EventUpcasterRegistry:
    """Versioned schema conversion without changing compatibility semantics."""

    def __init__(self, current_schema_version: int = CURRENT_EVENT_SCHEMA_VERSION) -> None:
        self.current_schema_version = current_schema_version
        self._upcasters: dict[tuple[str, int], EventUpcaster] = {}

    def register(
        self, event_type: str, from_version: int, upcaster: EventUpcaster
    ) -> None:
        key = (event_type, from_version)
        if key in self._upcasters:
            raise ValueError(f"Event upcaster already registered: {event_type} v{from_version}")
        self._upcasters[key] = upcaster

    def upcast(self, event: ExecutionEvent) -> ExecutionEvent:
        payload = dict(event.payload)
        version = event.schema_version
        while version < self.current_schema_version:
            upcaster = self._upcasters.get((event.type, version))
            if upcaster is None:
                raise InvariantError(
                    f"Missing event upcaster for {event.type} schema v{version}."
                )
            payload = upcaster(payload)
            version += 1
        if version > self.current_schema_version:
            raise InvariantError(
                f"Event {event.type} uses unsupported future schema v{version}."
            )
        if version == event.schema_version:
            return event
        return ExecutionEvent(
            event.execution_id, event.sequence, event.type, payload,
            event.event_id, event.command_id, event.causation_id,
            event.correlation_id, event.created_at, version,
        )


@dataclass(frozen=True)
class Command:
    type: str
    execution_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    command_id: str = field(default_factory=lambda: _id("cmd"))
    expected_sequence: int | None = None
    actor: str = "primary"
    lease_token: int | None = None
    causation_id: str | None = None
    correlation_id: str | None = None


@dataclass
class TaskProjection:
    id: str
    title: str
    parent_id: str | None = None
    dependencies: tuple[str, ...] = ()
    state: TaskState = TaskState.QUEUED
    priority: int = 0
    risk: float = 0.0
    estimated_cost: float = 0.0
    criteria: tuple[str, ...] = ()
    retry_limit: int = 2
    retries: int = 0
    assigned_agent: str | None = None
    blocked_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CriterionProjection:
    id: str
    task_id: str
    description: str
    evidence_ids: list[str] = field(default_factory=list)
    satisfied: bool = False
    verification_ids: list[str] = field(default_factory=list)


@dataclass
class EvidenceProjection:
    id: str
    task_id: str
    kind: str
    summary: str
    payload: dict[str, Any]
    version: int
    supersedes: str | None = None


@dataclass(frozen=True)
class VerificationDecision:
    """Policy-owned, immutable acceptance decision; never a worker state request."""

    task_id: str
    criterion_id: str
    evidence_ids: tuple[str, ...]
    policy: str
    decision: VerificationStatus
    confidence: float
    reasoning_summary: str
    latency_ms: float = 0.0
    cost: float = 0.0
    blocked_reason: str | None = None


@dataclass
class EffectProjection:
    id: str
    task_id: str
    kind: str
    idempotency_key: str
    state: EffectState = EffectState.PENDING
    request: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApprovalProjection:
    id: str
    scope: str
    risk: str
    task_ids: tuple[str, ...]
    granted_by: str | None = None
    granted_at: str | None = None
    expires_at: str | None = None
    revoked: bool = False


BUDGET_KINDS = (
    "tokens", "dollars", "wall_seconds", "cpu_seconds", "tool_calls",
    "shell_commands", "network_requests", "retries",
)


@dataclass
class BudgetProjection:
    scope: str
    limits: dict[str, float] = field(default_factory=dict)
    consumed: dict[str, float] = field(default_factory=dict)
    reserved: dict[str, float] = field(default_factory=dict)

    def available(self, kind: str) -> float:
        limit = self.limits.get(kind, float("inf"))
        return limit - self.consumed.get(kind, 0.0) - self.reserved.get(kind, 0.0)


@dataclass
class ExecutionProjection:
    id: str
    engine_version: str = ENGINE_VERSION
    compatibility_version: str = CURRENT_COMPATIBILITY_VERSION
    schema_version: int = CURRENT_EVENT_SCHEMA_VERSION
    goal: str = ""
    status: ExecutionStatus = ExecutionStatus.ACTIVE
    sequence: int = 0
    graph_version: int = 0
    graph_parent_versions: dict[int, int | None] = field(default_factory=dict)
    tasks: dict[str, TaskProjection] = field(default_factory=dict)
    criteria: dict[str, CriterionProjection] = field(default_factory=dict)
    evidence: dict[str, EvidenceProjection] = field(default_factory=dict)
    verifications: dict[str, dict[str, Any]] = field(default_factory=dict)
    diagnoses: list[dict[str, Any]] = field(default_factory=list)
    effects: dict[str, EffectProjection] = field(default_factory=dict)
    effect_keys: dict[str, str] = field(default_factory=dict)
    approvals: dict[str, ApprovalProjection] = field(default_factory=dict)
    budgets: dict[str, BudgetProjection] = field(default_factory=dict)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    scheduling_records: list[dict[str, Any]] = field(default_factory=list)
    task_execution_results: list[dict[str, Any]] = field(default_factory=list)
    repair_decisions: list[dict[str, Any]] = field(default_factory=list)
    model_decisions: list[dict[str, Any]] = field(default_factory=list)
    memory_records: list[dict[str, Any]] = field(default_factory=list)
    critiques: list[dict[str, Any]] = field(default_factory=list)
    active_profile: dict[str, Any] | None = None
    stats: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""

    def canonical(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        for task in value["tasks"].values():
            task["state"] = task["state"].value
        for effect in value["effects"].values():
            effect["state"] = effect["state"].value
        return value


@dataclass(frozen=True)
class Lease:
    execution_id: str
    owner: str
    token: int
    expires_at: float


class SQLiteEventStore:
    """Append-only event store with optimistic concurrency, leases, and snapshots."""

    def __init__(
        self,
        path: Path,
        *,
        fault_injector: Callable[[str, str], None] | None = None,
    ) -> None:
        self.path = path
        self.fault_injector = fault_injector
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        from .failure_types import SQLITE_RETRY_ATTEMPTS, SQLITE_RETRY_BACKOFF_SECONDS
        import time as _time

        last_error: sqlite3.OperationalError | None = None
        for attempt in range(SQLITE_RETRY_ATTEMPTS + 1):
            try:
                conn = sqlite3.connect(self.path, timeout=30)
                conn.row_factory = sqlite3.Row
                conn.execute("pragma foreign_keys=on")
                conn.execute("pragma busy_timeout=30000")
                return conn
            except sqlite3.OperationalError as exc:
                last_error = exc
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                if attempt < SQLITE_RETRY_ATTEMPTS:
                    backoff = (
                        SQLITE_RETRY_BACKOFF_SECONDS[attempt]
                        if attempt < len(SQLITE_RETRY_BACKOFF_SECONDS)
                        else SQLITE_RETRY_BACKOFF_SECONDS[-1]
                    )
                    _time.sleep(backoff)
        raise last_error  # type: ignore[misc]

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute("pragma journal_mode=wal")
            conn.executescript(
                """
                create table if not exists execution_events (
                    execution_id text not null,
                    sequence integer not null,
                    event_id text not null unique,
                    command_id text not null,
                    type text not null,
                    payload text not null,
                    causation_id text,
                    correlation_id text not null,
                    schema_version integer not null,
                    created_at text not null,
                    primary key (execution_id, sequence)
                );
                create table if not exists execution_snapshots (
                    execution_id text not null,
                    sequence integer not null,
                    payload text not null,
                    checksum text not null,
                    created_at text not null,
                    primary key (execution_id, sequence)
                );
                create table if not exists execution_leases (
                    execution_id text primary key,
                    owner text not null,
                    fencing_token integer not null,
                    expires_at real not null,
                    heartbeat_at real not null
                );
                create index if not exists idx_execution_events_correlation
                    on execution_events(correlation_id);
                """
            )

    def append(
        self,
        execution_id: str,
        expected_sequence: int,
        facts: list[tuple[str, dict[str, Any]]],
        command: Command,
    ) -> list[ExecutionEvent]:
        if not facts:
            return []
        events: list[ExecutionEvent] = []
        with self._lock, self._connect() as conn:
            conn.execute("begin immediate")
            duplicate = conn.execute(
                "select * from execution_events where execution_id=? and command_id=? order by sequence",
                (execution_id, command.command_id),
            ).fetchall()
            if duplicate:
                return [self._row_event(row) for row in duplicate]
            if self.fault_injector:
                self.fault_injector("before_persistence", execution_id)
            row = conn.execute(
                "select coalesce(max(sequence), 0) sequence from execution_events where execution_id=?",
                (execution_id,),
            ).fetchone()
            current = int(row["sequence"])
            if current != expected_sequence:
                raise ConcurrencyError(
                    f"Expected sequence {expected_sequence}, current sequence is {current}."
                )
            self._validate_lease(conn, execution_id, command)
            correlation = command.correlation_id or command.command_id
            for offset, (event_type, payload) in enumerate(facts, 1):
                event = ExecutionEvent(
                    execution_id, current + offset, event_type,
                    json.loads(json.dumps(payload)), _id("evt"), command.command_id,
                    command.causation_id, correlation, _now(),
                )
                conn.execute(
                    """insert into execution_events
                    (execution_id,sequence,event_id,command_id,type,payload,causation_id,
                     correlation_id,schema_version,created_at) values (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        event.execution_id, event.sequence, event.event_id, event.command_id,
                        event.type, json.dumps(event.payload, sort_keys=True), event.causation_id,
                        event.correlation_id, event.schema_version, event.created_at,
                    ),
                )
                events.append(event)
            if self.fault_injector:
                self.fault_injector("before_commit", execution_id)
        if self.fault_injector:
            self.fault_injector("after_persistence", execution_id)
        return events

    def load(self, execution_id: str, *, after: int = 0) -> list[ExecutionEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "select * from execution_events where execution_id=? and sequence>? order by sequence",
                (execution_id, after),
            ).fetchall()
        return [self._row_event(row) for row in rows]

    def command_events(self, execution_id: str, command_id: str) -> list[ExecutionEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "select * from execution_events where execution_id=? and command_id=? order by sequence",
                (execution_id, command_id),
            ).fetchall()
        return [self._row_event(row) for row in rows]

    def executions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """select execution_id, max(sequence) sequence, min(created_at) created_at,
                max(created_at) updated_at from execution_events group by execution_id
                order by updated_at desc limit ?""", (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_snapshot(self, projection: ExecutionProjection) -> str:
        payload = json.dumps(projection.canonical(), sort_keys=True, separators=(",", ":"))
        checksum = hashlib.sha256(payload.encode()).hexdigest()
        with self._connect() as conn:
            conn.execute(
                "insert or replace into execution_snapshots values (?,?,?,?,?)",
                (projection.id, projection.sequence, payload, checksum, _now()),
            )
        return checksum

    def latest_snapshot(self, execution_id: str) -> tuple[int, dict[str, Any]] | None:
        with self._connect() as conn:
            row = conn.execute(
                "select * from execution_snapshots where execution_id=? order by sequence desc limit 1",
                (execution_id,),
            ).fetchone()
        if row is None:
            return None
        payload = str(row["payload"])
        if hashlib.sha256(payload.encode()).hexdigest() != row["checksum"]:
            raise InvariantError("Execution snapshot checksum mismatch.")
        return int(row["sequence"]), json.loads(payload)

    def acquire_lease(self, execution_id: str, owner: str, ttl_seconds: float = 30) -> Lease:
        now = time.time()
        with self._connect() as conn:
            conn.execute("begin immediate")
            row = conn.execute(
                "select * from execution_leases where execution_id=?", (execution_id,)
            ).fetchone()
            if row and float(row["expires_at"]) > now and row["owner"] != owner:
                raise LeaseError(f"Execution is leased by {row['owner']}.")
            token = int(row["fencing_token"] + 1) if row else 1
            conn.execute(
                """insert into execution_leases values (?,?,?,?,?) on conflict(execution_id)
                do update set owner=excluded.owner,fencing_token=excluded.fencing_token,
                expires_at=excluded.expires_at,heartbeat_at=excluded.heartbeat_at""",
                (execution_id, owner, token, now + ttl_seconds, now),
            )
        return Lease(execution_id, owner, token, now + ttl_seconds)

    def heartbeat(self, lease: Lease, ttl_seconds: float = 30) -> Lease:
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                """update execution_leases set expires_at=?, heartbeat_at=?
                where execution_id=? and owner=? and fencing_token=? and expires_at>?""",
                (now + ttl_seconds, now, lease.execution_id, lease.owner, lease.token, now),
            )
            if cursor.rowcount != 1:
                raise LeaseError("Execution lease is stale or expired.")
        return Lease(lease.execution_id, lease.owner, lease.token, now + ttl_seconds)

    def release_lease(self, lease: Lease) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """update execution_leases set expires_at=0, heartbeat_at=?
                where execution_id=? and owner=? and fencing_token=?""",
                (time.time(), lease.execution_id, lease.owner, lease.token),
            )
        return cursor.rowcount == 1

    @staticmethod
    def _row_event(row: sqlite3.Row) -> ExecutionEvent:
        return ExecutionEvent(
            row["execution_id"], row["sequence"], row["type"], json.loads(row["payload"]),
            row["event_id"], row["command_id"], row["causation_id"], row["correlation_id"],
            row["created_at"], row["schema_version"],
        )

    @staticmethod
    def _validate_lease(conn: sqlite3.Connection, execution_id: str, command: Command) -> None:
        row = conn.execute(
            "select * from execution_leases where execution_id=?", (execution_id,)
        ).fetchone()
        if row is None:
            return
        if float(row["expires_at"]) <= time.time():
            return
        if command.lease_token != row["fencing_token"]:
            raise LeaseError("A current execution lease and fencing token are required.")


class ProjectionBuilder:
    def __init__(self, upcasters: EventUpcasterRegistry | None = None) -> None:
        self.upcasters = upcasters or EventUpcasterRegistry()

    def replay(
        self, execution_id: str, events: Iterable[ExecutionEvent],
        base: ExecutionProjection | None = None,
    ) -> ExecutionProjection:
        state = base or ExecutionProjection(execution_id)
        for raw_event in events:
            event = self.upcasters.upcast(raw_event)
            if event.sequence != state.sequence + 1:
                raise InvariantError("Event stream contains a sequence gap.")
            self.apply(state, event)
            state.sequence = event.sequence
            state.updated_at = event.created_at
        self.validate(state)
        return state

    def apply(self, state: ExecutionProjection, event: ExecutionEvent) -> None:
        p = event.payload
        kind = event.type
        if kind == "ExecutionCreated":
            state.goal = p["goal"]
            state.engine_version = str(p.get("engine_version", ENGINE_VERSION))
            state.compatibility_version = str(
                p.get("compatibility_version", CURRENT_COMPATIBILITY_VERSION)
            )
            state.schema_version = int(p.get("schema_version", CURRENT_EVENT_SCHEMA_VERSION))
            if state.compatibility_version not in SUPPORTED_COMPATIBILITY_VERSIONS:
                raise InvariantError(
                    f"Unsupported execution compatibility version: {state.compatibility_version}"
                )
            state.created_at = event.created_at
            state.budgets["execution"] = BudgetProjection("execution", dict(p.get("budgets", {})))
        elif kind == "GraphVersionCreated":
            state.graph_version = int(p["version"])
            state.graph_parent_versions[state.graph_version] = p.get("parent_version")
        elif kind == "TaskAdded":
            task = TaskProjection(
                id=p["task_id"], title=p["title"], parent_id=p.get("parent_id"),
                dependencies=tuple(p.get("dependencies", [])), priority=int(p.get("priority", 0)),
                risk=float(p.get("risk", 0)), estimated_cost=float(p.get("estimated_cost", 0)),
                criteria=tuple(p.get("criteria", [])), retry_limit=int(p.get("retry_limit", 2)),
                assigned_agent=p.get("assigned_agent"), metadata=dict(p.get("metadata", {})),
            )
            state.tasks[task.id] = task
            for criterion_id, description in zip(task.criteria, p.get("criterion_descriptions", [])):
                state.criteria[criterion_id] = CriterionProjection(criterion_id, task.id, description)
        elif kind == "TaskRemoved":
            task = state.tasks.pop(p["task_id"])
            for criterion_id in task.criteria:
                state.criteria.pop(criterion_id, None)
        elif kind == "TaskUpdated":
            task = state.tasks[p["task_id"]]
            for name in ("title", "parent_id", "priority", "risk", "estimated_cost", "assigned_agent"):
                if name in p:
                    setattr(task, name, p[name])
            if "dependencies" in p:
                task.dependencies = tuple(p["dependencies"])
            if "metadata" in p:
                task.metadata = {**task.metadata, **dict(p["metadata"])}
        elif kind == "TaskTransitioned":
            task = state.tasks[p["task_id"]]
            task.state = TaskState(p["to"])
            task.blocked_reason = p.get("blocked_reason") if task.state == TaskState.BLOCKED else None
            if p["to"] == TaskState.READY.value and p.get("retry"):
                task.retries += 1
        elif kind == "TaskScheduled":
            state.scheduling_records.append(dict(p))
        elif kind == "EvidenceRecorded":
            evidence = EvidenceProjection(
                p["evidence_id"], p["task_id"], p["kind"], p["summary"],
                dict(p.get("payload", {})), int(p["version"]), p.get("supersedes"),
            )
            state.evidence[evidence.id] = evidence
            for criterion_id in p.get("criterion_ids", []):
                state.criteria[criterion_id].evidence_ids.append(evidence.id)
        elif kind in {"VerificationPassed", "VerificationFailed"}:
            record = dict(p)
            record["passed"] = kind == "VerificationPassed"
            record["decision"] = (
                VerificationStatus.VERIFIED.value
                if kind == "VerificationPassed" else VerificationStatus.FAILED.value
            )
            record.setdefault("policy", "legacy")
            record.setdefault("reasoning_summary", str(p.get("reason", "")))
            state.verifications[p["verification_id"]] = record
            criterion = state.criteria[p["criterion_id"]]
            criterion.verification_ids.append(p["verification_id"])
            criterion.satisfied = kind == "VerificationPassed"
        elif kind == "VerificationDecisionRecorded":
            record = dict(p)
            state.verifications[p["verification_id"]] = record
            criterion = state.criteria[p["criterion_id"]]
            criterion.verification_ids.append(p["verification_id"])
            criterion.satisfied = p["decision"] == VerificationStatus.VERIFIED.value
        elif kind == "DiagnosisRecorded":
            state.diagnoses.append(dict(p))
        elif kind == "TaskExecutionResultRecorded":
            state.task_execution_results.append(dict(p))
        elif kind == "RepairDecisionRecorded":
            state.repair_decisions.append(dict(p))
        elif kind == "EffectRequested":
            effect = EffectProjection(
                p["effect_id"], p["task_id"], p["kind"], p["idempotency_key"],
                state=EffectState(p.get("state", EffectState.REQUESTED.value)),
                request=dict(p.get("request", {})),
            )
            state.effects[effect.id] = effect
            state.effect_keys[effect.idempotency_key] = effect.id
        elif kind == "EffectStateChanged":
            effect = state.effects[p["effect_id"]]
            effect.state = EffectState(p["state"])
            effect.result = dict(p.get("result", {}))
        elif kind == "ApprovalRequested":
            approval = ApprovalProjection(
                p["approval_id"], p["scope"], p["risk"], tuple(p.get("task_ids", [])),
                expires_at=p.get("expires_at"),
            )
            state.approvals[approval.id] = approval
        elif kind == "ApprovalGranted":
            approval = state.approvals[p["approval_id"]]
            approval.granted_by = p["granted_by"]
            approval.granted_at = event.created_at
        elif kind == "ApprovalRevoked":
            state.approvals[p["approval_id"]].revoked = True
        elif kind in {"BudgetReserved", "BudgetConsumed", "BudgetReleased"}:
            # Account every event at its scope and each ancestor. This makes a task
            # reservation visible to its subtree and prevents sibling workers from
            # collectively exceeding an execution-level limit.
            scopes = _scope_lineage(str(p["scope"]))
            target = "reserved" if kind != "BudgetConsumed" else "consumed"
            sign = -1 if kind == "BudgetReleased" else 1
            for scope in scopes:
                budget = state.budgets.setdefault(scope, BudgetProjection(scope))
                values = getattr(budget, target)
                values[p["kind"]] = values.get(p["kind"], 0) + sign * float(p["amount"])
                if kind == "BudgetConsumed" and p.get("from_reservation"):
                    budget.reserved[p["kind"]] = budget.reserved.get(p["kind"], 0) - float(p["amount"])
        elif kind == "BudgetConfigured":
            state.budgets[p["scope"]] = BudgetProjection(p["scope"], dict(p["limits"]))
        elif kind == "ModelRouted":
            state.model_decisions.append(dict(p))
        elif kind == "MemoryRecorded":
            state.memory_records.append(dict(p))
        elif kind == "CritiqueRecorded":
            state.critiques.append(dict(p))
        elif kind == "ProfileConfigured":
            state.active_profile = dict(p.get("profile", p))
        elif kind == "ProfileEscalated":
            state.active_profile = dict(p.get("profile", p))
        elif kind == "ExecutionStatsRecorded":
            state.stats = dict(p.get("stats", p))
        elif kind == "CheckpointCreated":
            state.checkpoints.append(dict(p))
        elif kind == "ExecutionPaused":
            state.status = ExecutionStatus.PAUSED
        elif kind == "ExecutionResumed":
            state.status = ExecutionStatus.ACTIVE
        elif kind == "ExecutionCompleted":
            state.status = ExecutionStatus.COMPLETE
        elif kind == "ExecutionFailed":
            state.status = ExecutionStatus.FAILED
        elif kind == "ExecutionCancelled":
            state.status = ExecutionStatus.CANCELLED
        else:
            raise InvariantError(f"Unknown execution event type: {kind}")

    def validate(self, state: ExecutionProjection) -> None:
        _validate_dag(state.tasks)
        for task in state.tasks.values():
            if task.parent_id and task.parent_id not in state.tasks:
                raise InvariantError(f"Task {task.id} has an unknown parent.")
            if task.state == TaskState.COMPLETE:
                unmet = [cid for cid in task.criteria if not state.criteria[cid].satisfied]
                if unmet:
                    raise InvariantError(f"Completed task {task.id} has unmet criteria.")
        for key, effect_id in state.effect_keys.items():
            if state.effects[effect_id].idempotency_key != key:
                raise InvariantError("Effect idempotency index is inconsistent.")
        for budget in state.budgets.values():
            for kind in BUDGET_KINDS:
                if budget.available(kind) < -1e-9:
                    raise InvariantError(f"Budget exceeded: {budget.scope}/{kind}")


class ExecutionEngine:
    def __init__(self, store: SQLiteEventStore, *, snapshot_interval: int = 50) -> None:
        self.store = store
        self.projector = ProjectionBuilder()
        self.snapshot_interval = snapshot_interval
        self._cache: dict[str, ExecutionProjection] = {}
        self._lock = threading.RLock()

    def state(self, execution_id: str, *, use_snapshot: bool = True) -> ExecutionProjection:
        with self._lock:
            return _clone_projection(self._state(execution_id, use_snapshot=use_snapshot))

    def _state(self, execution_id: str, *, use_snapshot: bool = True) -> ExecutionProjection:
        """Return the engine-owned projection; callers must not mutate it."""
        if execution_id in self._cache:
            return self._cache[execution_id]
        base = None
        after = 0
        if use_snapshot:
            snapshot = self.store.latest_snapshot(execution_id)
            if snapshot:
                after, raw = snapshot
                base = projection_from_dict(raw)
        events = self.store.load(execution_id, after=after)
        state = self.projector.replay(execution_id, events, base)
        self._cache[execution_id] = state
        return state

    def dispatch(self, command: Command) -> list[ExecutionEvent]:
        with self._lock:
            prior = self.store.command_events(command.execution_id, command.command_id)
            if prior:
                self._cache.pop(command.execution_id, None)
                self._state(command.execution_id)
                return prior
            state = self._state(command.execution_id)
            expected = command.expected_sequence if command.expected_sequence is not None else state.sequence
            facts = self._decide(state, command)
            try:
                events = self.store.append(command.execution_id, expected, facts, command)
            except ConcurrencyError:
                # A separate runtime committed first.  Never retain a stale
                # projection after an optimistic-concurrency failure: callers
                # must be able to reload and safely retry their command.
                self._cache.pop(command.execution_id, None)
                raise
            if events and events[0].sequence <= state.sequence:
                self._cache.pop(command.execution_id, None)
                return events
            self.projector.replay(command.execution_id, events, state)
            if self.snapshot_interval and state.sequence % self.snapshot_interval == 0:
                self.store.save_snapshot(state)
            return events

    def create(
        self, goal: str, *, execution_id: str | None = None,
        budgets: dict[str, float] | None = None,
        compatibility_version: str = CURRENT_COMPATIBILITY_VERSION,
    ) -> str:
        execution_id = execution_id or _id("exec")
        self.dispatch(Command("CreateExecution", execution_id, {
            "goal": goal, "budgets": budgets or {},
            "compatibility_version": compatibility_version,
        }))
        return execution_id

    def checkpoint(self, execution_id: str, reason: str = "periodic") -> str:
        state = self.state(execution_id)
        checksum = self.store.save_snapshot(state)
        self.dispatch(Command("RecordCheckpoint", execution_id, {"reason": reason, "checksum": checksum}))
        return checksum

    def replay(self, execution_id: str) -> ExecutionProjection:
        return self.projector.replay(execution_id, self.store.load(execution_id))

    def _decide(self, state: ExecutionProjection, command: Command) -> list[tuple[str, dict[str, Any]]]:
        p = command.payload
        t = command.type
        if command.actor.startswith("worker") and t in WORKER_FORBIDDEN_COMMANDS:
            raise PermissionError(f"Worker authority cannot issue {t}.")
        if p.get("recovery") and command.actor != "runtime-recovery":
            raise PermissionError("Only runtime recovery may suppress a task retry.")
        if t == "CreateExecution":
            if state.sequence:
                raise InvariantError("Execution already exists.")
            compatibility_version = str(
                p.get("compatibility_version", CURRENT_COMPATIBILITY_VERSION)
            )
            if compatibility_version not in SUPPORTED_COMPATIBILITY_VERSIONS:
                raise InvariantError(
                    f"Unsupported execution compatibility version: {compatibility_version}"
                )
            return [("ExecutionCreated", {
                "goal": p["goal"], "budgets": p.get("budgets", {}),
                "engine_version": ENGINE_VERSION,
                "compatibility_version": compatibility_version,
                "schema_version": CURRENT_EVENT_SCHEMA_VERSION,
            })]
        if not state.sequence:
            raise InvariantError("Execution does not exist.")
        if state.status in {ExecutionStatus.COMPLETE, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED}:
            raise InvariantError("Terminal executions are immutable.")
        if state.status == ExecutionStatus.PAUSED and t not in {"ResumeExecution", "CancelExecution", "RecordCheckpoint"}:
            raise InvariantError("Execution is paused.")
        handlers: dict[str, Callable[[ExecutionProjection, dict[str, Any]], list[tuple[str, dict[str, Any]]]]] = {
            "AddTasks": self._add_tasks, "MutateGraph": self._mutate_graph,
            "TransitionTask": self._transition_task, "RecordEvidence": self._record_evidence,
            "ScheduleTask": self._schedule_task,
            "VerifyCriterion": self._verify_criterion,
            "RecordVerificationDecision": self._record_verification_decision,
            "RecordDiagnosis": self._record_diagnosis,
            "RecordTaskExecutionResult": self._record_task_execution_result,
            "RecordRepairDecision": self._record_repair_decision,
            "RequestEffect": self._request_effect, "ChangeEffectState": self._change_effect,
            "RequestApproval": self._request_approval, "GrantApproval": self._grant_approval,
            "RevokeApproval": self._revoke_approval, "ConfigureBudget": self._configure_budget,
            "ReserveBudget": self._reserve_budget, "ConsumeBudget": self._consume_budget,
            "ReleaseBudget": self._release_budget, "RecordModelRoute": self._model_route,
            "RecordMemory": self._record_memory,
            "RecordCritique": self._record_critique,
            "ConfigureProfile": self._configure_profile,
            "EscalateProfile": self._escalate_profile,
            "RecordExecutionStats": self._record_execution_stats,
        }
        if t in handlers:
            return handlers[t](state, p)
        simple = {
            "PauseExecution": "ExecutionPaused", "ResumeExecution": "ExecutionResumed",
            "CancelExecution": "ExecutionCancelled", "FailExecution": "ExecutionFailed",
            "RecordCheckpoint": "CheckpointCreated",
        }
        if t in simple:
            return [(simple[t], dict(p))]
        if t == "CompleteExecution":
            blockers = self.completion_blockers(state)
            if blockers:
                raise InvariantError("Execution completion invariants are not satisfied: " + "; ".join(blockers))
            return [("ExecutionCompleted", dict(p))]
        raise ValueError(f"Unsupported execution command: {t}")

    @staticmethod
    def completion_blockers(state: ExecutionProjection) -> list[str]:
        blockers: list[str] = []
        if any(task.state not in {TaskState.COMPLETE, TaskState.SUPERSEDED} for task in state.tasks.values()):
            blockers.append("graph has incomplete tasks")
        unresolved = [
            effect.id for effect in state.effects.values()
            if effect.state in {
                EffectState.REQUESTED, EffectState.AUTHORIZED, EffectState.PREPARED,
                EffectState.DISPATCHED, EffectState.PENDING, EffectState.RUNNING, EffectState.UNKNOWN,
            }
        ]
        if unresolved:
            blockers.append("unresolved effects: " + ", ".join(sorted(unresolved)))
        pending_approvals = [
            approval.id for approval in state.approvals.values()
            if not approval.granted_at and not approval.revoked
        ]
        if pending_approvals:
            blockers.append("pending approvals: " + ", ".join(sorted(pending_approvals)))
        return blockers

    def _add_tasks(self, state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        tasks = list(p.get("tasks", []))
        version = state.graph_version + 1
        facts: list[tuple[str, dict[str, Any]]] = [("GraphVersionCreated", {
            "version": version, "parent_version": state.graph_version or None,
            "rationale": p.get("rationale", "initial plan"), "mutation": "add",
        })]
        known = set(state.tasks)
        for raw in tasks:
            task_id = str(raw.get("id") or _id("task"))
            if task_id in known:
                raise InvariantError(f"Duplicate task ID: {task_id}")
            criteria_raw = list(raw.get("criteria", []))
            if not criteria_raw:
                raise InvariantError(f"Task {task_id} requires at least one acceptance criterion.")
            criterion_ids = [str(item.get("id") or _id("criterion")) if isinstance(item, dict) else _id("criterion") for item in criteria_raw]
            descriptions = [str(item.get("description", "")) if isinstance(item, dict) else str(item) for item in criteria_raw]
            facts.append(("TaskAdded", {
                "task_id": task_id, "title": str(raw["title"]), "parent_id": raw.get("parent_id"),
                "dependencies": list(raw.get("dependencies", [])), "priority": raw.get("priority", 0),
                "risk": raw.get("risk", 0), "estimated_cost": raw.get("estimated_cost", 0),
                "criteria": criterion_ids, "criterion_descriptions": descriptions,
                "retry_limit": raw.get("retry_limit", 2), "assigned_agent": raw.get("assigned_agent"),
                "metadata": raw.get("metadata", {}), "graph_version": version,
            }))
            known.add(task_id)
        candidate = self.projector.replay(state.id, _synthetic_events(state, facts), _clone_projection(state))
        self.projector.validate(candidate)
        return facts

    def _mutate_graph(self, state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        if int(p.get("base_version", -1)) != state.graph_version:
            raise ConcurrencyError("Graph mutation is based on a stale version.")
        repair_decision_id = p.get("repair_decision_id")
        if repair_decision_id and repair_decision_id not in {
            item["repair_id"] for item in state.repair_decisions
        }:
            raise InvariantError("Graph mutation references an unknown repair decision.")
        version = state.graph_version + 1
        facts: list[tuple[str, dict[str, Any]]] = [("GraphVersionCreated", {
            "version": version, "parent_version": state.graph_version,
            "rationale": p.get("rationale", "replan"), "mutation": p.get("operation", "update"),
            "affected_subtree": p.get("affected_subtree"), "repair_decision_id": p.get("repair_decision_id"),
        })]
        operation = p.get("operation")
        if operation == "delete":
            for task_id in p.get("task_ids", []):
                task = state.tasks.get(task_id)
                if task is None or task.state in TERMINAL_TASK_STATES:
                    raise InvariantError("Only non-terminal existing tasks can be deleted.")
                facts.append(("TaskRemoved", {"task_id": task_id, "graph_version": version}))
        elif operation == "insert":
            extra = self._add_tasks(state, {"tasks": p.get("tasks", [])})[1:]
            facts.extend(extra)
        elif operation in {"change_dependencies", "update"}:
            task_id = p["task_id"]
            task = state.tasks.get(task_id)
            if task is None or task.state in TERMINAL_TASK_STATES:
                raise InvariantError("Only non-terminal existing tasks can be updated.")
            changes = dict(p.get("changes", {}))
            if operation == "change_dependencies":
                changes = {"dependencies": list(p.get("dependencies", []))}
            allowed = {"title", "parent_id", "priority", "risk", "estimated_cost", "assigned_agent", "dependencies", "metadata"}
            if set(changes) - allowed:
                raise InvariantError("Graph update contains unsupported task fields.")
            facts.append(("TaskUpdated", {"task_id": task_id, **changes, "graph_version": version}))
        elif operation in {"split", "merge"}:
            removed = [p["task_id"]] if operation == "split" else list(p.get("task_ids", []))
            if not removed:
                raise InvariantError(f"Graph {operation} requires source tasks.")
            for task_id in removed:
                task = state.tasks.get(task_id)
                if task is None or task.state in TERMINAL_TASK_STATES:
                    raise InvariantError("Only non-terminal existing tasks can be replaced.")
                facts.append(("TaskRemoved", {"task_id": task_id, "graph_version": version}))
            new_tasks = p.get("tasks", []) if operation == "split" else [p["task"]]
            facts.extend(self._add_tasks(state, {"tasks": new_tasks})[1:])
        else:
            raise ValueError("Unsupported graph mutation operation.")
        for update in p.get("dependency_updates", []):
            facts.append(("TaskUpdated", {
                "task_id": update["task_id"],
                "dependencies": list(update.get("dependencies", [])),
                "graph_version": version,
            }))
        candidate = self.projector.replay(state.id, _synthetic_events(state, facts), _clone_projection(state))
        self.projector.validate(candidate)
        return facts

    def _transition_task(self, state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        task = state.tasks[p["task_id"]]
        target = TaskState(p["to"])
        if target not in TASK_TRANSITIONS[task.state]:
            raise InvariantError(f"Illegal task transition: {task.state.value} -> {target.value}")
        if target in {TaskState.READY, TaskState.RUNNING}:
            unmet = [dep for dep in task.dependencies if state.tasks[dep].state != TaskState.COMPLETE]
            if unmet:
                raise InvariantError("Task dependencies are not complete: " + ", ".join(unmet))
        if target in {TaskState.VERIFIED, TaskState.COMPLETE}:
            unmet = [cid for cid in task.criteria if not state.criteria[cid].satisfied]
            if unmet:
                raise InvariantError("Task criteria are not verified: " + ", ".join(unmet))
            without_decision = [
                criterion_id for criterion_id in task.criteria
                if not any(
                    item.get("criterion_id") == criterion_id
                    and item.get("decision") == VerificationStatus.VERIFIED.value
                    for item in state.verifications.values()
                )
            ]
            if without_decision:
                raise InvariantError(
                    "Task criteria lack a persisted verified decision: " + ", ".join(without_decision)
                )
        blocked_reason = p.get("blocked_reason")
        if target == TaskState.BLOCKED:
            if blocked_reason not in {item.value for item in BlockedReason}:
                raise InvariantError("Blocked tasks require a typed blocked_reason.")
        retry = (
            task.state in {TaskState.DIAGNOSING, TaskState.REPLANNING}
            and target == TaskState.READY
            and not bool(p.get("recovery", False))
        )
        if retry and task.retries >= task.retry_limit:
            raise InvariantError("Task retry budget is exhausted.")
        return [("TaskTransitioned", {
            "task_id": task.id, "from": task.state.value, "to": target.value,
            "reason": p.get("reason", ""), "blocked_reason": blocked_reason, "retry": retry,
        })]

    def _schedule_task(self, state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        task = state.tasks[p["task_id"]]
        if task.state not in {TaskState.QUEUED, TaskState.READY}:
            raise InvariantError("Only queued or ready tasks can be scheduled.")
        unmet = [dep for dep in task.dependencies if state.tasks[dep].state != TaskState.COMPLETE]
        if unmet:
            raise InvariantError("Scheduler cannot run a dependency-blocked task: " + ", ".join(unmet))
        facts: list[tuple[str, dict[str, Any]]] = [("TaskScheduled", {
            "task_id": task.id, "policy": p.get("policy", "priority"),
            "worker": p.get("worker", "legacy-worker"), "reason": p.get("reason", ""),
        })]
        if task.state == TaskState.QUEUED:
            facts.append(("TaskTransitioned", {
                "task_id": task.id, "from": task.state.value, "to": "ready",
                "reason": "Selected by the execution scheduler.", "retry": False,
            }))
        facts.append(("TaskTransitioned", {
            "task_id": task.id, "from": "ready", "to": "running",
            "reason": "Assigned to the execution worker.", "retry": False,
        }))
        return facts

    def _record_evidence(self, state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        task_id = p["task_id"]
        if task_id not in state.tasks:
            raise InvariantError("Evidence task does not exist.")
        criterion_ids = list(p.get("criterion_ids", []))
        if any(cid not in state.criteria or state.criteria[cid].task_id != task_id for cid in criterion_ids):
            raise InvariantError("Evidence references an invalid criterion.")
        supersedes = p.get("supersedes")
        version = 1
        if supersedes:
            prior = state.evidence[supersedes]
            if prior.task_id != task_id:
                raise InvariantError("Evidence can only supersede evidence for the same task.")
            version = prior.version + 1
        return [("EvidenceRecorded", {
            "evidence_id": p.get("evidence_id") or _id("evidence"), "task_id": task_id,
            "kind": p["kind"], "summary": p["summary"], "payload": p.get("payload", {}),
            "criterion_ids": criterion_ids, "version": version, "supersedes": supersedes,
        })]

    def _verify_criterion(self, state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        criterion = state.criteria[p["criterion_id"]]
        evidence_ids = list(p.get("evidence_ids") or criterion.evidence_ids)
        if not evidence_ids or any(eid not in criterion.evidence_ids for eid in evidence_ids):
            raise InvariantError("Verification requires linked immutable evidence.")
        passed = bool(p["passed"])
        return [("VerificationPassed" if passed else "VerificationFailed", {
            "verification_id": p.get("verification_id") or _id("verify"),
            "criterion_id": criterion.id, "evidence_ids": evidence_ids,
            "verifier": p.get("verifier", "deterministic"), "reason": p.get("reason", ""),
        })]

    @staticmethod
    def _record_verification_decision(
        state: ExecutionProjection, p: dict[str, Any]
    ) -> list[tuple[str, dict[str, Any]]]:
        criterion = state.criteria[p["criterion_id"]]
        evidence_ids = list(p.get("evidence_ids", []))
        if any(eid not in criterion.evidence_ids for eid in evidence_ids):
            raise InvariantError("Verification decision references unlinked evidence.")
        decision = VerificationStatus(p["decision"])
        confidence = float(p.get("confidence", 0))
        if not 0 <= confidence <= 1:
            raise InvariantError("Verification confidence must be between zero and one.")
        blocked_reason = p.get("blocked_reason")
        if decision == VerificationStatus.BLOCKED and blocked_reason not in {item.value for item in BlockedReason}:
            raise InvariantError("Blocked verification requires a typed blocked_reason.")
        return [("VerificationDecisionRecorded", {
            "verification_id": p.get("verification_id") or _id("verify"),
            "task_id": criterion.task_id, "criterion_id": criterion.id,
            "evidence_ids": evidence_ids, "policy": str(p.get("policy", "normal")),
            "decision": decision.value, "confidence": confidence,
            "reasoning_summary": str(p.get("reasoning_summary", "")),
            "latency_ms": float(p.get("latency_ms", 0)), "cost": float(p.get("cost", 0)),
            "blocked_reason": blocked_reason,
        })]

    @staticmethod
    def _record_diagnosis(state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        if p["task_id"] not in state.tasks:
            raise InvariantError("Diagnosis task does not exist.")
        confidence = float(p.get("confidence", 0))
        if not 0 <= confidence <= 1:
            raise InvariantError("Diagnosis confidence must be between zero and one.")
        decision_ids = list(p.get("verification_ids", []))
        if any(item not in state.verifications for item in decision_ids):
            raise InvariantError("Diagnosis references an unknown verification decision.")
        return [("DiagnosisRecorded", {
            **p, "verification_ids": decision_ids,
            "diagnosis_id": p.get("diagnosis_id") or _id("diagnosis"),
        })]

    @staticmethod
    def _record_task_execution_result(
        state: ExecutionProjection, p: dict[str, Any]
    ) -> list[tuple[str, dict[str, Any]]]:
        task = state.tasks[p["task_id"]]
        if task.state != TaskState.RUNNING:
            raise InvariantError("Only the running assigned task may report observations.")
        assessment = str(p.get("worker_assessment", "unknown"))
        if assessment not in {"completed", "partial", "blocked", "failed", "unknown"}:
            raise InvariantError("Task execution result has an invalid worker_assessment.")
        return [("TaskExecutionResultRecorded", {
            "result_id": p.get("result_id") or _id("task-result"), "task_id": task.id,
            "evidence_ids": list(p.get("evidence_ids", [])),
            "artifacts": list(p.get("artifacts", [])), "observed_effects": list(p.get("observed_effects", [])),
            "metrics": dict(p.get("metrics", {})), "warnings": list(p.get("warnings", [])),
            "worker_assessment": assessment,
        })]

    @staticmethod
    def _record_repair_decision(
        state: ExecutionProjection, p: dict[str, Any]
    ) -> list[tuple[str, dict[str, Any]]]:
        if p["task_id"] not in state.tasks or p["diagnosis_id"] not in {item["diagnosis_id"] for item in state.diagnoses}:
            raise InvariantError("Repair decision requires an existing task and diagnosis.")
        return [("RepairDecisionRecorded", {
            **p, "repair_id": p.get("repair_id") or _id("repair"),
            "expected_criteria": list(p.get("expected_criteria", [])),
            "approver": p.get("approver"),
        })]

    @staticmethod
    def _request_effect(state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        key = p["idempotency_key"]
        if key in state.effect_keys:
            return []
        if p["task_id"] not in state.tasks:
            raise InvariantError("Effect task does not exist.")
        return [("EffectRequested", {**p, "effect_id": p.get("effect_id") or _id("effect")})]

    @staticmethod
    def _change_effect(state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        effect = state.effects[p["effect_id"]]
        target = EffectState(p["state"])
        allowed = {
            EffectState.REQUESTED: {EffectState.AUTHORIZED, EffectState.RUNNING, EffectState.CANCELLED, EffectState.FAILED},
            EffectState.AUTHORIZED: {EffectState.PREPARED, EffectState.CANCELLED, EffectState.FAILED},
            EffectState.PREPARED: {EffectState.DISPATCHED, EffectState.CANCELLED, EffectState.FAILED},
            EffectState.DISPATCHED: {EffectState.COMMITTED, EffectState.FAILED, EffectState.UNKNOWN},
            EffectState.PENDING: {EffectState.RUNNING, EffectState.FAILED},
            EffectState.RUNNING: {EffectState.COMMITTED, EffectState.FAILED, EffectState.UNKNOWN},
            EffectState.UNKNOWN: {EffectState.COMMITTED, EffectState.FAILED, EffectState.ROLLED_BACK, EffectState.COMPENSATED},
            EffectState.COMMITTED: {EffectState.ROLLED_BACK, EffectState.COMPENSATED},
            EffectState.FAILED: set(), EffectState.ROLLED_BACK: set(),
            EffectState.COMPENSATED: set(), EffectState.CANCELLED: set(),
        }
        if target not in allowed[effect.state]:
            raise InvariantError(f"Illegal effect transition: {effect.state.value} -> {target.value}")
        return [("EffectStateChanged", {"effect_id": effect.id, "state": target.value, "result": p.get("result", {})})]

    @staticmethod
    def _request_approval(state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        task_ids = list(p.get("task_ids", []))
        if any(task_id not in state.tasks for task_id in task_ids):
            raise InvariantError("Approval references an unknown task.")
        return [("ApprovalRequested", {**p, "approval_id": p.get("approval_id") or _id("approval")})]

    @staticmethod
    def _grant_approval(state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        approval = state.approvals[p["approval_id"]]
        if approval.revoked or approval.granted_at:
            raise InvariantError("Approval is no longer grantable.")
        return [("ApprovalGranted", dict(p))]

    @staticmethod
    def _revoke_approval(state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        if p["approval_id"] not in state.approvals:
            raise InvariantError("Approval does not exist.")
        return [("ApprovalRevoked", dict(p))]

    @staticmethod
    def _configure_budget(_state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        unknown = set(p["limits"]) - set(BUDGET_KINDS)
        if unknown or any(float(value) < 0 for value in p["limits"].values()):
            raise InvariantError("Budget contains invalid limits.")
        return [("BudgetConfigured", dict(p))]

    def _reserve_budget(self, state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        self._check_budget(state, p)
        return [("BudgetReserved", dict(p))]

    def _consume_budget(self, state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        self._validate_budget_amount(p)
        if not p.get("from_reservation"):
            self._check_budget(state, p)
        elif state.budgets.get(str(p["scope"]), BudgetProjection(str(p["scope"]))).reserved.get(
            p["kind"], 0
        ) < float(p["amount"]):
            raise InvariantError("Budget reservation is insufficient.")
        return [("BudgetConsumed", dict(p))]

    @staticmethod
    def _release_budget(state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        ExecutionEngine._validate_budget_amount(p)
        if state.budgets.get(str(p["scope"]), BudgetProjection(str(p["scope"]))).reserved.get(
            p["kind"], 0
        ) < float(p["amount"]):
            raise InvariantError("Cannot release more than reserved.")
        return [("BudgetReleased", dict(p))]

    @staticmethod
    def _check_budget(state: ExecutionProjection, p: dict[str, Any]) -> None:
        ExecutionEngine._validate_budget_amount(p)
        kind = str(p["kind"])
        amount = float(p["amount"])
        scopes = _scope_lineage(str(p["scope"]))
        for scope in scopes:
            budget = state.budgets.get(scope)
            if budget and budget.available(kind) < amount:
                raise InvariantError(f"Budget exhausted at {scope}/{kind}.")

    @staticmethod
    def _validate_budget_amount(p: dict[str, Any]) -> None:
        try:
            kind = str(p["kind"])
            amount = float(p["amount"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvariantError("Invalid budget consumption.") from exc
        if kind not in BUDGET_KINDS or amount < 0:
            raise InvariantError("Invalid budget consumption.")

    @staticmethod
    def _model_route(_state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        return [("ModelRouted", {**p, "decision_id": p.get("decision_id") or _id("route")})]

    @staticmethod
    def _record_memory(_state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        return [("MemoryRecorded", {**p, "memory_id": p.get("memory_id") or _id("memory")})]

    @staticmethod
    def _record_critique(_state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        return [("CritiqueRecorded", {**p, "critique_id": p.get("critique_id") or _id("critique")})]

    @staticmethod
    def _configure_profile(_state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        if "profile" not in p or not isinstance(p["profile"], dict):
            raise InvariantError("ConfigureProfile requires a profile dict.")
        return [("ProfileConfigured", dict(p))]

    @staticmethod
    def _escalate_profile(state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        if "profile" not in p or not isinstance(p["profile"], dict):
            raise InvariantError("EscalateProfile requires a profile dict.")
        if state.active_profile is None:
            raise InvariantError("Cannot escalate before a profile is configured.")
        return [("ProfileEscalated", {**p, "previous_complexity": state.active_profile.get("complexity", "unknown")})]

    @staticmethod
    def _record_execution_stats(_state: ExecutionProjection, p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        if "stats" not in p or not isinstance(p["stats"], dict):
            raise InvariantError("RecordExecutionStats requires a stats dict.")
        return [("ExecutionStatsRecorded", dict(p))]


class SchedulingPolicy:
    name = "fifo"

    def order(self, tasks: list[TaskProjection], state: ExecutionProjection) -> list[TaskProjection]:
        return sorted(tasks, key=lambda task: task.id)


class PriorityPolicy(SchedulingPolicy):
    name = "priority"

    def order(self, tasks: list[TaskProjection], state: ExecutionProjection) -> list[TaskProjection]:
        return sorted(tasks, key=lambda task: (-task.priority, task.estimated_cost, task.id))


class CriticalPathPolicy(SchedulingPolicy):
    name = "critical_path"

    def order(self, tasks: list[TaskProjection], state: ExecutionProjection) -> list[TaskProjection]:
        weights = _critical_path_weights(state.tasks)
        return sorted(tasks, key=lambda task: (-weights[task.id], -task.priority, task.id))


class CostOptimizedPolicy(SchedulingPolicy):
    name = "cost_optimized"

    def order(self, tasks: list[TaskProjection], state: ExecutionProjection) -> list[TaskProjection]:
        return sorted(tasks, key=lambda task: (task.estimated_cost, -task.priority, task.id))


class VerificationFirstPolicy(SchedulingPolicy):
    name = "verification_first"

    def order(self, tasks: list[TaskProjection], state: ExecutionProjection) -> list[TaskProjection]:
        return sorted(tasks, key=lambda task: (task.state != TaskState.VERIFYING, -task.priority, task.id))


class Scheduler:
    def __init__(self, policy: SchedulingPolicy | None = None, *, max_parallel: int = 4) -> None:
        self.policy = policy or PriorityPolicy()
        self.max_parallel = max_parallel

    def ready(self, state: ExecutionProjection) -> list[TaskProjection]:
        if state.status != ExecutionStatus.ACTIVE:
            return []
        candidates = [
            task for task in state.tasks.values()
            if task.state in {TaskState.QUEUED, TaskState.READY}
            and all(state.tasks[dep].state == TaskState.COMPLETE for dep in task.dependencies)
        ]
        return self.policy.order(candidates, state)[: self.max_parallel]


class CriterionVerifier:
    policy = "normal"

    def evaluate(
        self, criterion: CriterionProjection, evidence: list[EvidenceProjection]
    ) -> VerificationDecision:
        if not evidence:
            return VerificationDecision(
                criterion.task_id, criterion.id, (), self.policy,
                VerificationStatus.INCONCLUSIVE, 0.0,
                "No immutable evidence is linked to this criterion.",
            )
        failed = [item for item in evidence if item.payload.get("ok") is False]
        if failed:
            return VerificationDecision(
                criterion.task_id, criterion.id, tuple(item.id for item in evidence), self.policy,
                VerificationStatus.FAILED, min(0.95, 0.6 + 0.1 * len(failed)),
                f"{len(failed)} linked evidence record(s) report failure.",
            )
        return VerificationDecision(
            criterion.task_id, criterion.id, tuple(item.id for item in evidence), self.policy,
            VerificationStatus.VERIFIED, min(0.99, 0.8 + 0.03 * len(evidence)),
            f"Satisfied by {len(evidence)} immutable evidence record(s).",
        )

    def verify(
        self, criterion: CriterionProjection, evidence: list[EvidenceProjection]
    ) -> tuple[bool, str]:
        decision = self.evaluate(criterion, evidence)
        return decision.decision == VerificationStatus.VERIFIED, decision.reasoning_summary


class Diagnoser:
    def diagnose(self, task: TaskProjection, decisions: list[dict[str, Any] | EvidenceProjection]) -> dict[str, Any]:
        """Explain persisted verification decisions.

        EvidenceProjection input remains a compatibility fallback for callers from
        compatibility version 1; the verification-primary host never uses it.
        """
        legacy_evidence = [item for item in decisions if isinstance(item, EvidenceProjection)]
        records = [item for item in decisions if isinstance(item, dict)]
        failed = [item for item in records if item.get("decision") == VerificationStatus.FAILED.value]
        evidence_failures = [item for item in legacy_evidence if item.payload.get("ok") is False]
        category = "verification" if failed else "insufficient_evidence"
        if evidence_failures:
            category = "verification"
        confidence = min(0.95, 0.5 + 0.1 * max(len(failed), len(evidence_failures)))
        return {
            "task_id": task.id, "classification": category,
            "hypothesis": (
                failed[-1].get("reasoning_summary", "Verification failed.") if failed
                else (evidence_failures[-1].summary if evidence_failures else "Required evidence is missing.")
            ),
            "confidence": confidence,
            "repair_strategy": "Repair the narrow failing scope and regenerate only affected evidence.",
            "verification_ids": [item["verification_id"] for item in records],
            "evidence_ids": (
                [evidence_id for item in records for evidence_id in item.get("evidence_ids", [])]
                or [item.id for item in legacy_evidence]
            ),
        }


class HierarchicalPlanner:
    """Deterministic plan normalizer; an LLM planner may supply richer candidate tasks."""

    def decompose(self, goal: str, candidates: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        if candidates:
            return self._infer_dependencies(candidates)
        return [{
            "id": "task-root", "title": goal, "dependencies": [], "priority": 100,
            "risk": 0.5, "criteria": [f"Goal achieved: {goal}"],
        }]

    @staticmethod
    def _infer_dependencies(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        prior: str | None = None
        for index, raw in enumerate(tasks):
            item = dict(raw)
            item["id"] = str(item.get("id") or f"task-{index + 1}")
            if "dependencies" not in item:
                item["dependencies"] = [prior] if prior and item.get("after_previous") else []
            item.setdefault("priority", len(tasks) - index)
            item.setdefault("risk", 0.5)
            item.setdefault("criteria", [f"{item.get('title', item['id'])} is verified"])
            normalized.append(item)
            prior = item["id"]
        return normalized


class AdaptiveRouter:
    def choose(
        self, task: TaskProjection, *, confidence: float, remaining_tokens: float,
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        success = [item for item in history or [] if item.get("ok")]
        difficult = task.risk >= 0.7 or confidence < 0.5 or task.retries > 0
        profile = "planner" if "plan" in task.title.lower() else ("reviewer" if task.state == TaskState.VERIFYING else "coder")
        tier = "strong" if difficult and remaining_tokens >= 2000 else "fast"
        return {
            "task_id": task.id, "profile": profile, "model_tier": tier,
            "reasoning_depth": "high" if difficult else "normal",
            "parallelism": 1 if difficult else 2,
            "retry_budget": min(task.retry_limit - task.retries, 2),
            "verification_level": "exhaustive" if task.risk >= 0.8 else "focused",
            "historical_successes": len(success),
        }


class ExecutionMemory:
    def retrieve(self, state: ExecutionProjection, query: str, limit: int = 8) -> list[dict[str, Any]]:
        terms = set(query.lower().split())
        ranked = sorted(
            state.memory_records,
            key=lambda item: len(terms & set(str(item.get("content", "")).lower().split())),
            reverse=True,
        )
        return [item for item in ranked if terms & set(str(item.get("content", "")).lower().split())][:limit]

    def retrieve_global(
        self, store: SQLiteEventStore, query: str, limit: int = 8
    ) -> list[dict[str, Any]]:
        terms = set(query.lower().split())
        records: list[dict[str, Any]] = []
        for execution in store.executions(limit=500):
            for event in store.load(str(execution["execution_id"])):
                if event.type == "MemoryRecorded":
                    record = dict(event.payload)
                    record["execution_id"] = event.execution_id
                    record["relevance"] = len(
                        terms & set(str(record.get("content", "")).lower().split())
                    )
                    records.append(record)
        return sorted(
            (item for item in records if item["relevance"]),
            key=lambda item: (-item["relevance"], item["execution_id"]),
        )[:limit]


class ContextCompressor:
    def compress(self, state: ExecutionProjection, max_tasks: int = 25) -> dict[str, Any]:
        active = [task for task in state.tasks.values() if task.state not in TERMINAL_TASK_STATES]
        completed = [task for task in state.tasks.values() if task.state == TaskState.COMPLETE]
        return {
            "execution_id": state.id, "goal": state.goal, "status": state.status.value,
            "graph_version": state.graph_version,
            "active_tasks": [asdict(task) for task in active[:max_tasks]],
            "completed_summary": {"count": len(completed), "ids": [task.id for task in completed[-10:]]},
            "verified_criteria": [criterion.id for criterion in state.criteria.values() if criterion.satisfied],
            "latest_diagnoses": state.diagnoses[-5:], "model_decisions": state.model_decisions[-5:],
            "latest_critiques": state.critiques[-5:],
            "budgets": {scope: asdict(budget) for scope, budget in state.budgets.items()},
        }


class SelfCritic:
    def review(self, state: ExecutionProjection) -> dict[str, Any]:
        findings: list[dict[str, Any]] = []
        for task in state.tasks.values():
            if task.state == TaskState.RUNNING and task.risk >= 0.8:
                findings.append({"task_id": task.id, "severity": "high", "issue": "High-risk task requires exhaustive verification."})
            if task.retries and not any(item.get("task_id") == task.id for item in state.diagnoses):
                findings.append({"task_id": task.id, "severity": "medium", "issue": "Retried task lacks a recorded diagnosis."})
        return {
            "ok": not any(item["severity"] == "high" for item in findings),
            "findings": findings,
            "recommendation": "Continue" if not findings else "Address findings before completion.",
        }


Worker = Callable[[TaskProjection, ExecutionProjection, threading.Event], dict[str, Any]]


class SubagentWorkerAdapter:
    """Bridges the durable scheduler to the isolated subagent platform."""

    def __init__(self, manager: Any, *, default_agent: str = "coder") -> None:
        self.manager = manager
        self.default_agent = default_agent

    def __call__(
        self, task: TaskProjection, state: ExecutionProjection, cancellation: threading.Event
    ) -> dict[str, Any]:
        from .orchestration import SubagentRequest

        request = SubagentRequest(
            task=task.title,
            agent=task.assigned_agent or self.default_agent,
            context=ContextCompressor().compress(state),
            token_budget=int(min(
                state.budgets.get("execution", BudgetProjection("execution")).available("tokens"),
                2**31 - 1,
            )),
            execution_budget=max(1, task.retry_limit - task.retries + 1),
            parent_id=state.id,
        )
        future = self.manager.spawn(request)
        while not future.done():
            if cancellation.wait(0.05):
                future.cancel()
                return {"ok": False, "summary": "Subagent cancelled."}
        result = future.result()
        return {
            "ok": result.ok,
            "summary": result.summary if result.ok else result.error,
            "evidence": [{
                "kind": "subagent_result",
                "summary": result.summary if result.ok else result.error,
                "payload": {"ok": result.ok, "agent": result.agent, "subagent_id": result.id},
            }],
            "output": result.output,
        }


class BackgroundWorkerPool:
    def __init__(self, worker: Worker, *, max_workers: int = 4) -> None:
        self.worker = worker
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="agent47-engine")
        self._cancellations: dict[str, threading.Event] = {}

    def submit(self, task: TaskProjection, state: ExecutionProjection) -> Future[dict[str, Any]]:
        cancellation = threading.Event()
        self._cancellations[task.id] = cancellation
        return self.executor.submit(self._run, task, state, cancellation)

    def _run(self, task: TaskProjection, state: ExecutionProjection, cancel: threading.Event) -> dict[str, Any]:
        try:
            return self.worker(task, state, cancel)
        finally:
            self._cancellations.pop(task.id, None)

    def cancel(self, task_id: str) -> bool:
        event = self._cancellations.get(task_id)
        if event is None:
            return False
        event.set()
        return True

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)


class AutonomousExecutor:
    """Lease-protected scheduler loop for deterministic workers and replaceable reasoning services."""

    def __init__(
        self,
        runtime: "DurableExecutionRuntime",
        worker: Worker,
        *,
        owner: str | None = None,
        max_parallel: int = 4,
        human_checkpoint_risk: float = 0.8,
    ) -> None:
        self.runtime = runtime
        self.owner = owner or _id("worker")
        self.scheduler = Scheduler(PriorityPolicy(), max_parallel=max_parallel)
        self.pool = BackgroundWorkerPool(worker, max_workers=max_parallel)
        self.human_checkpoint_risk = human_checkpoint_risk

    def run(self, execution_id: str, *, max_cycles: int = 100) -> ExecutionProjection:
        lease = self.runtime.store.acquire_lease(execution_id, self.owner)

        def dispatch(kind: str, payload: dict[str, Any]) -> list[ExecutionEvent]:
            return self.runtime.engine.dispatch(Command(kind, execution_id, payload, lease_token=lease.token))

        try:
            for _cycle in range(max_cycles):
                state = self.runtime.engine.state(execution_id)
                if state.status != ExecutionStatus.ACTIVE:
                    return state
                for task in list(state.tasks.values()):
                    if task.state == TaskState.BLOCKED and self._approved(state, task.id):
                        dispatch("TransitionTask", {"task_id": task.id, "to": "ready", "reason": "human checkpoint granted"})
                state = self.runtime.engine.state(execution_id)
                ready = self.scheduler.ready(state)
                if not ready:
                    if state.tasks and all(task.state == TaskState.COMPLETE for task in state.tasks.values()):
                        dispatch("CompleteExecution", {})
                    return self.runtime.engine.state(execution_id)
                futures: dict[str, Future[dict[str, Any]]] = {}
                for task in ready:
                    if task.risk >= self.human_checkpoint_risk and not self._approved(state, task.id):
                        dispatch("RequestApproval", {
                            "scope": "task.execute", "risk": "high", "task_ids": [task.id],
                            "reason": f"Human checkpoint for risk score {task.risk:g}",
                        })
                        dispatch("TransitionTask", {
                            "task_id": task.id, "to": "blocked",
                            "reason": "awaiting human checkpoint",
                            "blocked_reason": BlockedReason.APPROVAL.value,
                        })
                        continue
                    if task.state == TaskState.QUEUED:
                        dispatch("TransitionTask", {"task_id": task.id, "to": "ready"})
                    dispatch("TransitionTask", {"task_id": task.id, "to": "running"})
                    current = self.runtime.engine.state(execution_id)
                    route = self.runtime.router.choose(
                        current.tasks[task.id], confidence=0.7,
                        remaining_tokens=current.budgets.get("execution", BudgetProjection("execution")).available("tokens"),
                        history=current.model_decisions,
                    )
                    dispatch("RecordModelRoute", route)
                    futures[task.id] = self.pool.submit(current.tasks[task.id], current)
                for task_id, future in futures.items():
                    result = future.result()
                    self._record_result(dispatch, execution_id, task_id, result)
                critique = self.runtime.critic.review(self.runtime.engine.state(execution_id))
                dispatch("RecordCritique", critique)
                lease = self.runtime.store.heartbeat(lease)
            dispatch("PauseExecution", {"reason": "maximum scheduler cycles reached"})
            return self.runtime.engine.state(execution_id)
        finally:
            self.runtime.store.release_lease(lease)

    def _record_result(
        self,
        dispatch: Callable[[str, dict[str, Any]], list[ExecutionEvent]],
        execution_id: str,
        task_id: str,
        result: dict[str, Any],
    ) -> None:
        state = self.runtime.engine.state(execution_id)
        task = state.tasks[task_id]
        evidence_ids: list[str] = []
        evidence_items = list(result.get("evidence", [])) or [{
            "kind": "worker_result", "summary": str(result.get("summary", "worker completed")),
            "payload": {"ok": bool(result.get("ok", True))},
        }]
        for item in evidence_items:
            event = dispatch("RecordEvidence", {
                "task_id": task_id, "kind": item.get("kind", "worker_result"),
                "summary": str(item.get("summary", "evidence")), "payload": dict(item.get("payload", {})),
                "criterion_ids": list(task.criteria),
            })[0]
            evidence_ids.append(event.payload["evidence_id"])
        dispatch("TransitionTask", {"task_id": task_id, "to": "verifying"})
        current = self.runtime.engine.state(execution_id)
        decisions: list[dict[str, Any]] = []
        passed_all = True
        for criterion_id in task.criteria:
            criterion = current.criteria[criterion_id]
            linked = [current.evidence[eid] for eid in evidence_ids]
            decision = self.runtime.verifier.evaluate(criterion, linked)
            event = dispatch("RecordVerificationDecision", {
                "criterion_id": criterion_id, "evidence_ids": list(decision.evidence_ids),
                "policy": decision.policy, "decision": decision.decision.value,
                "confidence": decision.confidence, "reasoning_summary": decision.reasoning_summary,
                "latency_ms": decision.latency_ms, "cost": decision.cost,
            })[0]
            decisions.append(event.payload)
            passed_all &= decision.decision == VerificationStatus.VERIFIED
        if passed_all:
            dispatch("TransitionTask", {"task_id": task_id, "to": "verified"})
            dispatch("TransitionTask", {"task_id": task_id, "to": "complete"})
            return
        dispatch("TransitionTask", {"task_id": task_id, "to": "diagnosing"})
        current = self.runtime.engine.state(execution_id)
        diagnosis = self.runtime.diagnoser.diagnose(current.tasks[task_id], decisions)
        diagnosis = dispatch("RecordDiagnosis", diagnosis)[0].payload
        if current.tasks[task_id].retries < current.tasks[task_id].retry_limit:
            dispatch("TransitionTask", {"task_id": task_id, "to": "replanning"})
            refreshed = self.runtime.engine.state(execution_id)
            repair = dispatch("RecordRepairDecision", {
                "task_id": task_id, "diagnosis_id": diagnosis["diagnosis_id"],
                "repair_policy": {"max_retries": current.tasks[task_id].retry_limit},
                "reason": diagnosis["repair_strategy"], "expected_criteria": list(task.criteria),
            })[0].payload
            dispatch("MutateGraph", {
                "base_version": refreshed.graph_version,
                "operation": "update",
                "task_id": task_id,
                "affected_subtree": task_id,
                "rationale": diagnosis["repair_strategy"], "repair_decision_id": repair["repair_id"],
                "changes": {"metadata": {
                    "last_diagnosis": diagnosis["diagnosis_id"],
                    "repair_strategy": diagnosis["repair_strategy"],
                }},
            })
            dispatch("TransitionTask", {"task_id": task_id, "to": "ready", "reason": diagnosis["repair_strategy"]})
        else:
            dispatch("TransitionTask", {"task_id": task_id, "to": "failed"})

    @staticmethod
    def _approved(state: ExecutionProjection, task_id: str) -> bool:
        now = datetime.now(UTC)
        return any(
            approval.granted_at
            and not approval.revoked
            and task_id in approval.task_ids
            and (
                not approval.expires_at
                or datetime.fromisoformat(approval.expires_at.replace("Z", "+00:00")) > now
            )
            for approval in state.approvals.values()
        )

    def close(self) -> None:
        self.pool.close()


class ExecutionTrace:
    def __init__(self, store: SQLiteEventStore) -> None:
        self.store = store

    def export(self, execution_id: str) -> dict[str, Any]:
        events = self.store.load(execution_id)
        return {
            "execution_id": execution_id,
            "event_count": len(events),
            "events": [asdict(event) for event in events],
            "cost": sum(float(event.payload.get("amount", 0)) for event in events if event.type == "BudgetConsumed" and event.payload.get("kind") == "dollars"),
            "timings": {
                "started_at": events[0].created_at if events else None,
                "finished_at": events[-1].created_at if events else None,
            },
        }


class DurableExecutionRuntime:
    """Facade joining planning, scheduling, verification, diagnosis, tracing, and recovery."""

    def __init__(self, db_path: Path) -> None:
        self.store = SQLiteEventStore(db_path)
        self.engine = ExecutionEngine(self.store)
        self.scheduler = Scheduler()
        self.planner = HierarchicalPlanner()
        self.verifier = CriterionVerifier()
        self.diagnoser = Diagnoser()
        self.router = AdaptiveRouter()
        self.memory = ExecutionMemory()
        self.compressor = ContextCompressor()
        self.critic = SelfCritic()
        self.trace = ExecutionTrace(self.store)
        # Adaptive execution profile components
        from .execution_profiles import (
            ComplexityAssessor,
            ExecutionPolicySelector,
            ProfileEscalator,
        )
        self.complexity_assessor = ComplexityAssessor()
        self.policy_selector = ExecutionPolicySelector(assessor=self.complexity_assessor)
        self.profile_escalator = ProfileEscalator()

    def create_planned(
        self, goal: str, *, tasks: list[dict[str, Any]] | None = None,
        budgets: dict[str, float] | None = None,
        compatibility_version: str = CURRENT_COMPATIBILITY_VERSION,
        intent: "TaskIntent | None" = None,
        profile_overrides: dict[str, Any] | None = None,
    ) -> str:
        from .execution_profiles import TaskIntent as _TaskIntent

        task_intent = intent or _TaskIntent(goal=goal)

        # Run complexity assessment and select profile inside the runtime
        profile, signals = self.policy_selector.select(task_intent, overrides=profile_overrides)

        # Use profile budgets unless caller provided explicit overrides
        effective_budgets = budgets if budgets else profile.budgets.to_engine_budgets()

        execution_id = self.engine.create(
            goal, budgets=effective_budgets, compatibility_version=compatibility_version
        )

        # Record the selected profile as a durable event
        self.engine.dispatch(Command("ConfigureProfile", execution_id, {
            "profile": profile.as_dict(),
            "signals": {"composite": signals.composite, "prompt": signals.prompt,
                        "symbols": signals.symbols, "dependency_radius": signals.dependency_radius,
                        "tools": signals.tools, "verification": signals.verification},
        }))

        plan = self.planner.decompose(goal, tasks)
        self.engine.dispatch(Command("MutateGraph", execution_id, {
            "base_version": 0, "operation": "insert", "tasks": plan,
            "rationale": "goal decomposition", "affected_subtree": None,
        }))
        return execution_id

    def escalate_profile(
        self, execution_id: str, *,
        affected_files: int = 0,
        test_failures: int = 0,
        graph_size: int = 0,
        budget_utilization: float = 0.0,
    ) -> bool:
        """Evaluate and apply profile escalation, returning True if escalated."""
        from .execution_profiles import ExecutionComplexity

        state = self.engine.state(execution_id)
        if state.active_profile is None:
            return False

        current = ExecutionComplexity(state.active_profile.get("complexity", "medium"))
        target, trigger = self.profile_escalator.evaluate(
            current,
            affected_files=affected_files,
            test_failures=test_failures,
            graph_size=graph_size,
            budget_utilization=budget_utilization,
        )
        if target is None or trigger is None:
            return False

        new_profile = self.policy_selector.for_complexity(target)

        # Expand budgets to match the new profile
        for kind, limit in new_profile.budgets.to_engine_budgets().items():
            current_budget = state.budgets.get("execution")
            if current_budget and current_budget.limits.get(kind, 0) < limit:
                self.engine.dispatch(Command("ConfigureBudget", execution_id, {
                    "scope": "execution", "limits": {kind: limit},
                }))

        self.engine.dispatch(Command("EscalateProfile", execution_id, {
            "profile": new_profile.as_dict(),
            "trigger": {"reason": trigger.reason, "affected_files": trigger.affected_files,
                        "test_failures": trigger.test_failures, "graph_growth": trigger.graph_growth,
                        "budget_pressure": trigger.budget_pressure},
        }))
        return True

    def record_execution_stats(
        self, execution_id: str, stats: dict[str, Any]
    ) -> None:
        """Record final execution statistics for future complexity feedback."""
        self.engine.dispatch(Command("RecordExecutionStats", execution_id, {"stats": stats}))

    def recover(
        self,
        execution_id: str,
        *,
        reconcile_orphaned_effects: bool = True,
        resume_interrupted_tasks: bool = False,
    ) -> ExecutionProjection:
        self.engine._cache.pop(execution_id, None)
        full = self.engine.replay(execution_id)
        accelerated = self.engine.state(execution_id, use_snapshot=True)
        if full.canonical() != accelerated.canonical():
            raise InvariantError("Snapshot recovery diverged from full event replay.")
        if resume_interrupted_tasks and full.status == ExecutionStatus.ACTIVE:
            self._recover_interrupted_tasks(execution_id, full)
            full = self.engine.replay(execution_id)
        if reconcile_orphaned_effects and full.status == ExecutionStatus.ACTIVE:
            for effect in list(full.effects.values()):
                if effect.state in {EffectState.DISPATCHED, EffectState.RUNNING}:
                    self.engine.dispatch(Command("ChangeEffectState", execution_id, {
                        "effect_id": effect.id,
                        "state": "unknown",
                        "result": {"reason": "Recovered after an ambiguous external side effect."},
                    }))
            full = self.engine.replay(execution_id)
        return full

    def _recover_interrupted_tasks(
        self, execution_id: str, state: ExecutionProjection
    ) -> None:
        """Return states owned by a lost worker to the scheduler after restart.

        These transitions are intentionally journaled rather than mutating the
        projection, so replay remains authoritative.  Recovery is not a failed
        work attempt and therefore must not consume a task retry.
        """
        for task in state.tasks.values():
            if task.state == TaskState.RUNNING:
                self.engine.dispatch(Command("TransitionTask", execution_id, {
                    "task_id": task.id, "to": "waiting",
                    "reason": "Worker interrupted; recovering durable execution.",
                }))
                self.engine.dispatch(Command("TransitionTask", execution_id, {
                    "task_id": task.id, "to": "ready",
                    "reason": "Recovered interrupted worker task.", "recovery": True,
                }, actor="runtime-recovery"))
            elif task.state == TaskState.VERIFYING:
                self.engine.dispatch(Command("TransitionTask", execution_id, {
                    "task_id": task.id, "to": "diagnosing",
                    "reason": "Verification interrupted; recovering durable execution.",
                }))
                self.engine.dispatch(Command("TransitionTask", execution_id, {
                    "task_id": task.id, "to": "ready",
                    "reason": "Recovered interrupted verification.", "recovery": True,
                }, actor="runtime-recovery"))
            elif task.state in {TaskState.DIAGNOSING, TaskState.REPLANNING}:
                self.engine.dispatch(Command("TransitionTask", execution_id, {
                    "task_id": task.id, "to": "ready",
                    "reason": "Recovered interrupted planning step.", "recovery": True,
                }, actor="runtime-recovery"))


class AgentExecutionAdapter:
    """Journals the legacy model loop as a worker inside the durable engine."""

    def __init__(
        self,
        runtime: DurableExecutionRuntime,
        goal: str,
        *,
        execution_id: str | None = None,
        budgets: dict[str, float] | None = None,
        mirror_all_tasks: bool = False,
        task_id: str | None = None,
    ) -> None:
        self.runtime = runtime
        self.execution_id = execution_id or runtime.create_planned(goal, budgets=budgets)
        self.mirror_all_tasks = mirror_all_tasks
        state = runtime.engine.state(self.execution_id)
        candidates = [
            task for task in state.tasks.values()
            if task.state == TaskState.RUNNING
        ] or [
            task for task in state.tasks.values()
            if task.state in {TaskState.READY, TaskState.QUEUED}
            and all(state.tasks[item].state == TaskState.COMPLETE for item in task.dependencies)
        ] or [
            task for task in state.tasks.values() if task.state not in TERMINAL_TASK_STATES
        ]
        if task_id is not None:
            task = state.tasks.get(task_id)
            if task is None or task.state not in {TaskState.READY, TaskState.RUNNING}:
                raise InvariantError("Assigned execution task is not ready for the worker.")
            candidates = [task]
        if not candidates:
            raise InvariantError("Execution has no resumable task for the legacy worker.")
        self.task_id = candidates[0].id
        self.criterion_id = state.tasks[self.task_id].criteria[0]
        self.budget_scope = f"execution/tasks/{self.task_id}"
        self._finished = False
        task = state.tasks[self.task_id]
        if task.state == TaskState.QUEUED:
            runtime.engine.dispatch(Command("TransitionTask", self.execution_id, {"task_id": self.task_id, "to": "ready"}))
            task = runtime.engine.state(self.execution_id).tasks[self.task_id]
        if task.state == TaskState.READY:
            runtime.engine.dispatch(Command("TransitionTask", self.execution_id, {"task_id": self.task_id, "to": "running"}))

    def action_started(
        self, step: int, action: dict[str, Any]
    ) -> tuple[str, dict[str, Any] | None]:
        encoded = json.dumps(action, sort_keys=True, default=str)
        key = hashlib.sha256(f"{self.execution_id}:{step}:{encoded}".encode()).hexdigest()
        events = self.runtime.engine.dispatch(Command("RequestEffect", self.execution_id, {
            "task_id": self.task_id, "kind": f"agent_tool:{action.get('type', 'unknown')}",
            "idempotency_key": key, "request": action,
        }))
        if events:
            effect_id = events[0].payload["effect_id"]
        else:
            effect_id = self.runtime.engine.state(self.execution_id).effect_keys[key]
        effect = self.runtime.engine.state(self.execution_id).effects[effect_id]
        if effect.state == EffectState.UNKNOWN:
            raise InvariantError(
                "External effect outcome is unknown; reconcile it before retrying the action."
            )
        if effect.state in {EffectState.COMMITTED, EffectState.FAILED}:
            return effect_id, {
                "ok": effect.state == EffectState.COMMITTED and bool(effect.result.get("ok", True)),
                "output": str(effect.result.get("output", "Recovered journaled tool result.")),
                "elapsed_ms": float(effect.result.get("elapsed_ms", 0)),
                "replayed": True,
            }
        if effect.state in {EffectState.REQUESTED, EffectState.PENDING}:
            self.runtime.engine.dispatch(Command("ChangeEffectState", self.execution_id, {
                "effect_id": effect_id, "state": "running" if effect.state == EffectState.PENDING else "authorized",
            }))
            effect = self.runtime.engine.state(self.execution_id).effects[effect_id]
            if effect.state == EffectState.AUTHORIZED:
                self.runtime.engine.dispatch(Command("ChangeEffectState", self.execution_id, {
                    "effect_id": effect_id, "state": "prepared",
                }))
                self.runtime.engine.dispatch(Command("ChangeEffectState", self.execution_id, {
                    "effect_id": effect_id, "state": "dispatched",
                }))
        return effect_id, None

    def model_usage(self, payload: dict[str, Any]) -> None:
        engine = self.runtime.engine
        route = {
            "task_id": self.task_id,
            "model": payload.get("model"),
            "provider": payload.get("provider"),
            "ok": bool(payload.get("ok")),
            "fallback_from": payload.get("fallback_from"),
            "reasoning_depth": "observed",
        }
        engine.dispatch(Command("RecordModelRoute", self.execution_id, route))
        usage = {
            "tokens": payload.get("total_tokens"),
            "dollars": payload.get("estimated_cost_usd"),
            "wall_seconds": (
                float(payload["latency_ms"]) / 1000 if payload.get("latency_ms") is not None else None
            ),
        }
        for kind, amount in usage.items():
            if amount is not None and float(amount) >= 0:
                engine.dispatch(Command("ConsumeBudget", self.execution_id, {
                    "scope": self.budget_scope, "kind": kind, "amount": float(amount),
                }))

    def action_completed(self, effect_id: str, *, ok: bool, output: str, elapsed_ms: float) -> str:
        effect = self.runtime.engine.state(self.execution_id).effects[effect_id]
        if effect.state in {EffectState.DISPATCHED, EffectState.RUNNING}:
            self.runtime.engine.dispatch(Command("ChangeEffectState", self.execution_id, {
                "effect_id": effect_id, "state": "committed" if ok else "failed",
                "result": {"ok": ok, "output": output[:4000], "elapsed_ms": elapsed_ms},
            }))
        event = self.runtime.engine.dispatch(Command("RecordEvidence", self.execution_id, {
            "task_id": self.task_id, "kind": "tool_output", "summary": output[:1000] or "No output",
            "payload": {"ok": ok, "effect_id": effect_id, "elapsed_ms": elapsed_ms},
            "criterion_ids": [self.criterion_id],
        }))[0]
        return str(event.payload["evidence_id"])

    def finish(self, *, blocked: bool, summary: str) -> None:
        if self._finished:
            return
        self._finished = True
        engine = self.runtime.engine
        task = engine.state(self.execution_id).tasks[self.task_id]
        if blocked:
            if task.state == TaskState.RUNNING:
                engine.dispatch(Command("TransitionTask", self.execution_id, {
                    "task_id": self.task_id, "to": "failed", "reason": summary[:500],
                }))
            engine.dispatch(Command("FailExecution", self.execution_id, {"reason": summary[:1000]}))
            return
        task_ids = (
            list(engine.state(self.execution_id).tasks)
            if self.mirror_all_tasks
            else [self.task_id]
        )
        remaining = set(task_ids)
        while remaining:
            state = engine.state(self.execution_id)
            progressed = False
            for task_id in task_ids:
                if task_id not in remaining:
                    continue
                current = state.tasks[task_id]
                if current.state == TaskState.COMPLETE:
                    remaining.remove(task_id)
                    progressed = True
                    continue
                if any(
                    state.tasks[item].state != TaskState.COMPLETE
                    for item in current.dependencies
                ):
                    continue
                self._complete_task_from_authoritative_outcome(task_id, summary)
                remaining.remove(task_id)
                progressed = True
                state = engine.state(self.execution_id)
            if not progressed:
                raise InvariantError(
                    "Authoritative legacy outcome could not be projected across the execution DAG."
                )
        engine.checkpoint(self.execution_id, "agent_run_finalized")
        if all(task.state == TaskState.COMPLETE for task in engine.state(self.execution_id).tasks.values()):
            engine.dispatch(Command("CompleteExecution", self.execution_id))

    def _complete_task_from_authoritative_outcome(
        self, task_id: str, summary: str
    ) -> None:
        engine = self.runtime.engine
        task = engine.state(self.execution_id).tasks[task_id]
        if task.state == TaskState.QUEUED:
            engine.dispatch(Command("TransitionTask", self.execution_id, {
                "task_id": task_id, "to": "ready",
                "reason": "Projected from the authoritative legacy run.",
            }))
            task = engine.state(self.execution_id).tasks[task_id]
        if task.state == TaskState.READY:
            engine.dispatch(Command("TransitionTask", self.execution_id, {
                "task_id": task_id, "to": "running",
                "reason": "Projected from the authoritative legacy run.",
            }))
            task = engine.state(self.execution_id).tasks[task_id]
        if task.state == TaskState.RUNNING:
            engine.dispatch(Command("TransitionTask", self.execution_id, {
                "task_id": task_id, "to": "verifying",
            }))
            task = engine.state(self.execution_id).tasks[task_id]
        if task.state == TaskState.VERIFYING:
            evidence_event = engine.dispatch(Command("RecordEvidence", self.execution_id, {
                "task_id": task_id,
                "kind": "legacy_authoritative_outcome",
                "summary": summary[:1000],
                "payload": {
                    "ok": True,
                    "authority": "legacy",
                    "projection": "shadow" if self.mirror_all_tasks else "adapter",
                },
                "criterion_ids": list(task.criteria),
            }))[0]
            evidence_id = evidence_event.payload["evidence_id"]
            for criterion_id in task.criteria:
                engine.dispatch(Command("VerifyCriterion", self.execution_id, {
                    "criterion_id": criterion_id,
                    "evidence_ids": [evidence_id],
                    "passed": True,
                    "verifier": "legacy-authoritative-projector",
                    "reason": "The authoritative legacy run reported successful completion.",
                }))
            engine.dispatch(Command("TransitionTask", self.execution_id, {
                "task_id": task_id, "to": "verified",
            }))
            task = engine.state(self.execution_id).tasks[task_id]
        if task.state == TaskState.VERIFIED:
            engine.dispatch(Command("TransitionTask", self.execution_id, {
                "task_id": task_id, "to": "complete",
            }))
            return
        if task.state != TaskState.COMPLETE:
            raise InvariantError(
                f"Task {task_id} cannot mirror completion from {task.state.value}."
            )

    def cancel(self, reason: str) -> None:
        if self._finished:
            return
        self._finished = True
        engine = self.runtime.engine
        for task in list(engine.state(self.execution_id).tasks.values()):
            if TaskState.CANCELLED in TASK_TRANSITIONS[task.state]:
                engine.dispatch(Command("TransitionTask", self.execution_id, {
                    "task_id": task.id, "to": "cancelled", "reason": reason[:500],
                }))
        engine.dispatch(Command("CancelExecution", self.execution_id, {"reason": reason[:1000]}))


def projection_from_dict(raw: dict[str, Any]) -> ExecutionProjection:
    state = ExecutionProjection(
        id=raw["id"], engine_version=raw.get("engine_version", ENGINE_VERSION),
        compatibility_version=raw.get(
            "compatibility_version", CURRENT_COMPATIBILITY_VERSION
        ),
        schema_version=int(raw.get("schema_version", CURRENT_EVENT_SCHEMA_VERSION)),
        goal=raw.get("goal", ""), status=ExecutionStatus(raw.get("status", "active")),
        sequence=int(raw.get("sequence", 0)), graph_version=int(raw.get("graph_version", 0)),
        graph_parent_versions={int(k): v for k, v in raw.get("graph_parent_versions", {}).items()},
        created_at=raw.get("created_at", ""), updated_at=raw.get("updated_at", ""),
    )
    for key, item in raw.get("tasks", {}).items():
        state.tasks[key] = TaskProjection(
            id=item["id"], title=item["title"], parent_id=item.get("parent_id"),
            dependencies=tuple(item.get("dependencies", [])), state=TaskState(item["state"]),
            priority=item.get("priority", 0), risk=item.get("risk", 0),
            estimated_cost=item.get("estimated_cost", 0), criteria=tuple(item.get("criteria", [])),
            retry_limit=item.get("retry_limit", 2), retries=item.get("retries", 0),
            assigned_agent=item.get("assigned_agent"), blocked_reason=item.get("blocked_reason"),
            metadata=dict(item.get("metadata", {})),
        )
    for key, item in raw.get("criteria", {}).items():
        state.criteria[key] = CriterionProjection(**item)
    for key, item in raw.get("evidence", {}).items():
        state.evidence[key] = EvidenceProjection(**item)
    state.verifications = dict(raw.get("verifications", {}))
    state.diagnoses = list(raw.get("diagnoses", []))
    for key, item in raw.get("effects", {}).items():
        state.effects[key] = EffectProjection(
            item["id"], item["task_id"], item["kind"], item["idempotency_key"],
            EffectState(item["state"]), dict(item.get("request", {})), dict(item.get("result", {})),
        )
    state.effect_keys = dict(raw.get("effect_keys", {}))
    for key, item in raw.get("approvals", {}).items():
        item = dict(item)
        item["task_ids"] = tuple(item.get("task_ids", []))
        state.approvals[key] = ApprovalProjection(**item)
    for key, item in raw.get("budgets", {}).items():
        state.budgets[key] = BudgetProjection(**item)
    state.checkpoints = list(raw.get("checkpoints", []))
    state.scheduling_records = list(raw.get("scheduling_records", []))
    state.task_execution_results = list(raw.get("task_execution_results", []))
    state.repair_decisions = list(raw.get("repair_decisions", []))
    state.model_decisions = list(raw.get("model_decisions", []))
    state.memory_records = list(raw.get("memory_records", []))
    state.critiques = list(raw.get("critiques", []))
    state.active_profile = raw.get("active_profile")
    state.stats = raw.get("stats")
    return state


def _scope_lineage(scope: str) -> list[str]:
    parts = scope.split("/")
    return ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]


def _validate_dag(tasks: dict[str, TaskProjection]) -> None:
    for task in tasks.values():
        unknown = set(task.dependencies) - set(tasks)
        if unknown:
            raise InvariantError(f"Task {task.id} has unknown dependencies: {', '.join(sorted(unknown))}")
        if task.id in task.dependencies:
            raise InvariantError(f"Task {task.id} depends on itself.")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise InvariantError("Execution graph contains a cycle.")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in tasks[task_id].dependencies:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in tasks:
        visit(task_id)


def _critical_path_weights(tasks: dict[str, TaskProjection]) -> dict[str, float]:
    children: dict[str, list[str]] = defaultdict(list)
    for task in tasks.values():
        for dependency in task.dependencies:
            children[dependency].append(task.id)
    memo: dict[str, float] = {}

    def weight(task_id: str) -> float:
        if task_id not in memo:
            own = max(1.0, tasks[task_id].estimated_cost)
            memo[task_id] = own + max((weight(child) for child in children[task_id]), default=0)
        return memo[task_id]

    return {task_id: weight(task_id) for task_id in tasks}


def _clone_projection(state: ExecutionProjection) -> ExecutionProjection:
    return projection_from_dict(json.loads(json.dumps(state.canonical())))


def _synthetic_events(
    state: ExecutionProjection, facts: list[tuple[str, dict[str, Any]]]
) -> list[ExecutionEvent]:
    return [
        ExecutionEvent(state.id, state.sequence + index, kind, payload, f"synthetic-{index}",
                       "synthetic", None, "synthetic", _now())
        for index, (kind, payload) in enumerate(facts, 1)
    ]
