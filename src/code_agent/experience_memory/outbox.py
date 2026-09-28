"""Crash-safe, provider-neutral outbox for sanitized engineering episodes.

The execution journal cannot accept effects after terminal completion. This
separate journal owns only historical-memory delivery, never task truth.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .contracts import Experience, MemoryStatus, OperationState
from .episode_sanitizer import PreparedEpisode
from .privacy import valid_experience
from .service import ExperienceMemoryService

TERMINAL = frozenset({"completed", "failed", "cancelled"})
MAX_ATTEMPTS = 6
MAX_AMBIGUITY_AGE_SECONDS = 86_400
MAX_WORKER_LIFETIME_SECONDS = 3_600
SAFE_ERROR_CODES = frozenset({
    "", "provider_failed", "timeout", "unavailable", "missing_config",
    "missing_dependency", "invalid_request", "disabled", "retry_limit",
    "operation_missing_after_ack", "ambiguity_window_expired",
    "invalid_provider_state", "local_dispatch_error",
})


@dataclass(frozen=True)
class OutboxEntry:
    bank_id: str
    prepared: PreparedEpisode
    state: str
    attempts: int
    submitted: bool
    created_at: float
    next_attempt_at: float | None
    error_code: str
    network_requests: int
    wall_seconds: float


class ExperienceMemoryOutbox:
    """SQLite-first submission with leases, bounded retry, and status polling."""

    def __init__(self, db_path: Path, service: ExperienceMemoryService) -> None:
        self.db_path = db_path
        self.service = service
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma busy_timeout = 5000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                create table if not exists experience_memory_outbox (
                    operation_id text primary key,
                    bank_id text not null,
                    document_id text not null,
                    content text not null,
                    branch text,
                    head text,
                    outcome text not null,
                    agent_version text not null,
                    fingerprint text not null,
                    run_id integer not null,
                    state text not null,
                    submitted integer not null default 0,
                    attempts integer not null default 0,
                    next_attempt_at real,
                    lease_owner text,
                    lease_until real,
                    created_at real not null,
                    updated_at real not null,
                    submitted_at real,
                    completed_at real,
                    error_code text not null default '',
                    network_requests integer not null default 0,
                    wall_seconds real not null default 0
                )
            """)
            conn.execute("""
                create index if not exists experience_memory_outbox_due
                on experience_memory_outbox(state, next_attempt_at)
            """)
            conn.execute("""
                create index if not exists experience_memory_outbox_document
                on experience_memory_outbox(bank_id, document_id, created_at)
            """)

    @staticmethod
    def _fingerprint(bank_id: str, prepared: PreparedEpisode) -> str:
        material = "\0".join((
            bank_id, prepared.document_id, prepared.operation_id,
            hashlib.sha256(prepared.content.encode("utf-8")).hexdigest(),
        ))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def enqueue(self, bank_id: str, prepared: PreparedEpisode, run_id: int) -> bool:
        """Persist before network I/O. Return whether this exact operation is new."""
        experience = Experience(
            summary=prepared.content, branch=prepared.branch, head=prepared.head,
            kind="engineering_episode", document_id=prepared.document_id,
            operation_id=prepared.operation_id, outcome=prepared.outcome,
            agent_version=prepared.agent_version,
        )
        if not valid_experience(bank_id, experience, self.service.config):
            raise ValueError("Invalid sanitized memory request.")
        payload_hash = hashlib.sha256(prepared.content.encode("utf-8")).hexdigest()
        expected_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL, prepared.document_id + ":" + payload_hash,
        ))
        if prepared.operation_id != expected_id:
            raise ValueError("Memory operation ID is not derived from sanitized content.")
        fingerprint = self._fingerprint(bank_id, prepared)
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute("""
                insert or ignore into experience_memory_outbox (
                    operation_id, bank_id, document_id, content, branch, head,
                    outcome, agent_version, fingerprint, run_id, state,
                    next_attempt_at, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', ?, ?, ?)
            """, (
                prepared.operation_id, bank_id, prepared.document_id,
                prepared.content, prepared.branch, prepared.head, prepared.outcome,
                prepared.agent_version, fingerprint, run_id, now, now, now,
            ))
            row = conn.execute(
                "select fingerprint from experience_memory_outbox where operation_id = ?",
                (prepared.operation_id,),
            ).fetchone()
            if row["fingerprint"] != fingerprint:
                raise ValueError("Operation identity has conflicting content.")
            return cursor.rowcount == 1

    @staticmethod
    def _entry(row: sqlite3.Row) -> OutboxEntry:
        return OutboxEntry(
            bank_id=row["bank_id"],
            prepared=PreparedEpisode(
                content=row["content"], document_id=row["document_id"],
                operation_id=row["operation_id"], branch=row["branch"],
                head=row["head"], outcome=row["outcome"],
                agent_version=row["agent_version"],
            ),
            state=row["state"], attempts=row["attempts"],
            submitted=bool(row["submitted"]), created_at=row["created_at"],
            next_attempt_at=row["next_attempt_at"], error_code=row["error_code"],
            network_requests=row["network_requests"], wall_seconds=row["wall_seconds"],
        )

    def get(self, operation_id: str) -> OutboxEntry | None:
        with self._connect() as conn:
            row = conn.execute(
                "select * from experience_memory_outbox where operation_id = ?",
                (operation_id,),
            ).fetchone()
        return self._entry(row) if row else None

    def pending_count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("""
                select count(*) from experience_memory_outbox
                where state not in ('completed', 'failed', 'cancelled')
                  and next_attempt_at is not null
            """).fetchone()[0])

    def _claim(self, operation_id: str, *, force: bool = False) -> tuple[OutboxEntry, str] | None:
        now = time.time()
        owner = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute("begin immediate")
            row = conn.execute("""
                select * from experience_memory_outbox
                where operation_id = ? and state not in ('completed', 'failed', 'cancelled')
                  and next_attempt_at is not null
                  and (lease_until is null or lease_until < ?)
                  and (next_attempt_at <= ? or ?)
                  and not exists (
                      select 1 from experience_memory_outbox older
                      where older.bank_id = experience_memory_outbox.bank_id
                        and older.document_id = experience_memory_outbox.document_id
                        and older.rowid < experience_memory_outbox.rowid
                        and older.state not in ('completed', 'failed', 'cancelled')
                  )
            """, (operation_id, now, now, int(force))).fetchone()
            if row is None:
                return None
            conn.execute("""
                update experience_memory_outbox
                set lease_owner = ?, lease_until = ? where operation_id = ?
            """, (owner, now + max(10.0, self.service.config.timeout_seconds + 5.0), operation_id))
        return self._entry(row), owner

    def _record(
        self, entry: OutboxEntry, owner: str, state: str, error_code: str = "",
        *, network_calls: int = 0, elapsed: float = 0.0,
    ) -> None:
        if error_code not in SAFE_ERROR_CODES:
            error_code = "provider_failed"
        now = time.time()
        attempts = (entry.attempts + 1) if state in {"unknown", "prepared"} else 0
        terminal = state in TERMINAL
        if state in {"unknown", "prepared"} and attempts >= MAX_ATTEMPTS:
            state = "unknown"
            error_code = "retry_limit"
        next_at = None if terminal or error_code in {
            "retry_limit", "operation_missing_after_ack", "ambiguity_window_expired",
        } else now + (5.0 if state in {"submitted", "processing"} else min(3600.0, 2 ** max(1, attempts)))
        with self._connect() as conn:
            conn.execute("""
                update experience_memory_outbox set
                    state = ?, submitted = ?, attempts = ?, next_attempt_at = ?,
                    lease_owner = null, lease_until = null, updated_at = ?,
                    submitted_at = case when ? = 'submitted' and submitted_at is null
                        then ? else submitted_at end,
                    completed_at = case when ? in ('completed', 'failed', 'cancelled')
                        then ? else completed_at end,
                    error_code = ?, network_requests = network_requests + ?,
                    wall_seconds = wall_seconds + ?
                where operation_id = ? and lease_owner = ?
            """, (
                state, int(entry.submitted or state in {"submitted", "processing", "completed"}),
                attempts, next_at, now, state, now, state, now, error_code,
                network_calls, elapsed, entry.prepared.operation_id, owner,
            ))

    def process(self, operation_id: str, *, first_submission: bool = False) -> OutboxEntry | None:
        claim = self._claim(operation_id, force=first_submission)
        if claim is None:
            return self.get(operation_id)
        entry, owner = claim
        started = time.monotonic()
        network_calls = 0
        try:
            if not first_submission:
                network_calls += 1
                lookup = self.service.get_operation(entry.bank_id, operation_id)
                if lookup.status in {MemoryStatus.MISSING_CONFIG, MemoryStatus.MISSING_DEPENDENCY}:
                    network_calls -= 1
                if lookup.status == MemoryStatus.OK:
                    if lookup.state == OperationState.COMPLETED:
                        self._record(entry, owner, "completed", network_calls=network_calls,
                                     elapsed=time.monotonic() - started)
                        return self.get(operation_id)
                    if lookup.state in {OperationState.PENDING, OperationState.PROCESSING}:
                        state = "submitted" if lookup.state == OperationState.PENDING else "processing"
                        self._record(entry, owner, state, network_calls=network_calls,
                                     elapsed=time.monotonic() - started)
                        return self.get(operation_id)
                    if lookup.state in {OperationState.FAILED, OperationState.CANCELLED}:
                        self._record(entry, owner, lookup.state.value, lookup.error_code,
                                     network_calls=network_calls, elapsed=time.monotonic() - started)
                        return self.get(operation_id)
                    if lookup.state == OperationState.NOT_FOUND and entry.submitted:
                        self._record(entry, owner, "unknown", "operation_missing_after_ack",
                                     network_calls=network_calls, elapsed=time.monotonic() - started)
                        return self.get(operation_id)
                    if lookup.state != OperationState.NOT_FOUND:
                        self._record(entry, owner, "unknown", "invalid_provider_state",
                                     network_calls=network_calls, elapsed=time.monotonic() - started)
                        return self.get(operation_id)
                else:
                    self._record(entry, owner, "unknown", lookup.status.value,
                                 network_calls=network_calls, elapsed=time.monotonic() - started)
                    return self.get(operation_id)
            if time.time() - entry.created_at > MAX_AMBIGUITY_AGE_SECONDS:
                self._record(entry, owner, "unknown", "ambiguity_window_expired",
                             network_calls=network_calls, elapsed=time.monotonic() - started)
                return self.get(operation_id)
            network_calls += 1
            result = self.service.submit_retention(entry.bank_id, entry.prepared)
            if result.status in {MemoryStatus.MISSING_CONFIG, MemoryStatus.MISSING_DEPENDENCY}:
                network_calls -= 1
            if result.status == MemoryStatus.OK:
                state, error = "submitted", ""
            elif result.status == MemoryStatus.INVALID_REQUEST:
                state, error = "failed", "invalid_request"
            elif result.status in {MemoryStatus.MISSING_CONFIG, MemoryStatus.MISSING_DEPENDENCY}:
                state, error = "prepared", result.status.value
            else:
                state, error = "unknown", result.status.value
            self._record(entry, owner, state, error, network_calls=network_calls,
                         elapsed=time.monotonic() - started)
        except Exception:
            self._record(entry, owner, "unknown", "local_dispatch_error",
                         network_calls=network_calls, elapsed=time.monotonic() - started)
        return self.get(operation_id)

    def recover(self, *, max_items: int = 3) -> list[OutboxEntry]:
        """Bounded startup pass; each candidate reconciles its ID before resend."""
        if not self.service.enabled or self.service.availability != MemoryStatus.OK:
            return []
        now = time.time()
        with self._connect() as conn:
            rows = conn.execute("""
                select operation_id from experience_memory_outbox
                where state not in ('completed', 'failed', 'cancelled')
                  and next_attempt_at <= ?
                  and (lease_until is null or lease_until < ?)
                order by created_at, operation_id limit ?
            """, (now, now, max_items)).fetchall()
        results = []
        for row in rows:
            result = self.process(str(row["operation_id"]))
            if result is not None:
                results.append(result)
        return results


_workers: dict[str, threading.Thread] = {}
_workers_lock = threading.Lock()


def start_recovery_worker(db_path: Path, service: ExperienceMemoryService) -> None:
    """Poll outstanding operations without delaying startup or task completion."""
    if not service.enabled or service.availability != MemoryStatus.OK:
        return
    outbox = ExperienceMemoryOutbox(db_path, service)
    if not outbox.pending_count():
        return
    key = str(db_path.resolve())
    with _workers_lock:
        prior = _workers.get(key)
        if prior is not None and prior.is_alive():
            return

        def run() -> None:
            deadline = time.monotonic() + MAX_WORKER_LIFETIME_SECONDS
            failures = 0
            try:
                while time.monotonic() < deadline:
                    try:
                        outbox.recover(max_items=3)
                        if not outbox.pending_count():
                            return
                        failures = 0
                    except Exception:
                        failures += 1
                        if failures >= 3:
                            return
                    time.sleep(5)
            finally:
                with _workers_lock:
                    if _workers.get(key) is threading.current_thread():
                        del _workers[key]

        worker = threading.Thread(target=run, name="agent47-memory-recovery", daemon=True)
        _workers[key] = worker
        worker.start()
