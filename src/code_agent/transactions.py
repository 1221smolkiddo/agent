from __future__ import annotations

import codecs
import difflib
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .patches import git_style_unified_diff
from .safety import is_sensitive_path
from .sandbox_security import validate_workspace_boundary


TRANSACTION_DIR = Path(".code-agent") / "transactions"
IGNORED_SNAPSHOT_NAMES = {
    ".code-agent",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
}


class TransactionError(RuntimeError):
    pass


class TransactionConflict(TransactionError):
    pass


@dataclass(frozen=True)
class FileState:
    path: str
    exists: bool
    kind: str = "missing"
    sha256: str | None = None
    size: int = 0
    mode: int | None = None
    atime_ns: int | None = None
    mtime_ns: int | None = None
    symlink_target: str | None = None
    encoding: str | None = None
    newline: str | None = None
    blob: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "exists": self.exists,
            "kind": self.kind,
            "sha256": self.sha256,
            "size": self.size,
            "mode": self.mode,
            "atime_ns": self.atime_ns,
            "mtime_ns": self.mtime_ns,
            "symlink_target": self.symlink_target,
            "encoding": self.encoding,
            "newline": self.newline,
            "blob": self.blob,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> FileState:
        return cls(
            path=str(payload["path"]),
            exists=bool(payload.get("exists")),
            kind=str(payload.get("kind") or "missing"),
            sha256=str(payload["sha256"]) if payload.get("sha256") else None,
            size=int(payload.get("size") or 0),
            mode=int(payload["mode"]) if payload.get("mode") is not None else None,
            atime_ns=int(payload["atime_ns"])
            if payload.get("atime_ns") is not None
            else None,
            mtime_ns=int(payload["mtime_ns"])
            if payload.get("mtime_ns") is not None
            else None,
            symlink_target=str(payload["symlink_target"])
            if payload.get("symlink_target") is not None
            else None,
            encoding=str(payload["encoding"]) if payload.get("encoding") else None,
            newline=str(payload["newline"]) if payload.get("newline") else None,
            blob=str(payload["blob"]) if payload.get("blob") else None,
        )


@dataclass(frozen=True)
class FileMutation:
    path: str
    base: FileState
    before: FileState
    after_exists: bool
    after_bytes: bytes | None
    operation: str
    allow_merge: bool = True
    after_kind: str = "file"
    after_symlink_target: str | None = None


@dataclass
class TransactionPlan:
    manager: WorkspaceTransactionManager
    transaction_id: str
    action: str
    entries: list[FileMutation]
    workspace_snapshot: dict[str, FileState]
    metadata: dict[str, Any] = field(default_factory=dict)
    state: str = "proposed"

    @property
    def paths(self) -> list[str]:
        return [entry.path for entry in self.entries]

    def abort(self, reason: str) -> None:
        self.manager.abort(self, reason)

    def commit(
        self,
        *,
        validator: Callable[[TransactionPlan], tuple[bool, str]] | None = None,
    ) -> TransactionResult:
        return self.manager.commit(self, validator=validator)


@dataclass(frozen=True)
class TransactionResult:
    ok: bool
    transaction_id: str
    action: str
    state: str
    paths: tuple[str, ...]
    records: tuple[dict[str, Any], ...]
    output: str
    conflicts: tuple[str, ...] = ()
    recovered: bool = False

    def as_metadata(self) -> dict[str, Any]:
        return {
            "transaction": {
                "id": self.transaction_id,
                "action": self.action,
                "state": self.state,
                "paths": list(self.paths),
                "records": list(self.records),
                "conflicts": list(self.conflicts),
                "recovered": self.recovered,
            }
        }


class WorkspaceTransactionManager:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.root = self.workspace / TRANSACTION_DIR
        self.journals = self.root / "journals"
        self.blobs = self.root / "blobs"
        self.audit_path = self.root / "audit.jsonl"
        self.lock_path = self.root / "workspace.lock"
        self.journals.mkdir(parents=True, exist_ok=True)
        self.blobs.mkdir(parents=True, exist_ok=True)
        self._restrict(self.root)
        self.recovery_results = self.recover_incomplete()

    def read_text(self, requested_path: str) -> tuple[FileState, str]:
        state = self.capture_state(requested_path, store_blob=True)
        if not state.exists:
            return state, ""
        if state.kind != "file":
            raise TransactionError(f"Refusing text mutation for {state.kind}: {requested_path}")
        data = self._read_blob(state.blob)
        try:
            text = data.decode(state.encoding or "utf-8")
        except UnicodeDecodeError as exc:
            raise TransactionError(f"File is not supported text: {requested_path}") from exc
        return state, text.replace("\r\n", "\n").replace("\r", "\n")

    def encode_text(self, content: str, state: FileState) -> bytes:
        encoding = state.encoding or "utf-8"
        newline = state.newline or "\n"
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        if newline != "\n":
            normalized = normalized.replace("\n", newline)
        return normalized.encode(encoding)

    def plan_text(
        self,
        action: str,
        requested_path: str,
        content: str,
        *,
        expected: FileState | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TransactionPlan:
        before = expected or self.capture_state(requested_path, store_blob=True)
        if before.kind == "symlink":
            raise TransactionError(f"Refusing to replace symlink path: {requested_path}")
        after_bytes = self.encode_text(content, before)
        operation = "create" if not before.exists else "update"
        return self.plan(
            action,
            [
                FileMutation(
                    path=before.path,
                    base=before,
                    before=before,
                    after_exists=True,
                    after_bytes=after_bytes,
                    operation=operation,
                )
            ],
            metadata=metadata,
        )

    def plan_delete(
        self,
        action: str,
        requested_path: str,
        *,
        expected: FileState | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TransactionPlan:
        before = expected or self.capture_state(requested_path, store_blob=True)
        if not before.exists:
            raise TransactionError(f"File does not exist: {requested_path}")
        if before.kind != "file":
            raise TransactionError(f"Refusing to delete non-file path: {requested_path}")
        return self.plan(
            action,
            [
                FileMutation(
                    path=before.path,
                    base=before,
                    before=before,
                    after_exists=False,
                    after_bytes=None,
                    operation="delete",
                    allow_merge=False,
                )
            ],
            metadata=metadata,
        )

    def plan_move(
        self,
        action: str,
        source_path: str,
        destination_path: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> TransactionPlan:
        source = self.capture_state(source_path, store_blob=True)
        destination = self.capture_state(destination_path, store_blob=True)
        if not source.exists or source.kind != "file":
            raise TransactionError(f"Move source is not a file: {source_path}")
        if destination.exists:
            raise TransactionError(f"Move destination already exists: {destination_path}")
        data = self._read_blob(source.blob)
        return self.plan(
            action,
            [
                FileMutation(
                    path=source.path,
                    base=source,
                    before=source,
                    after_exists=False,
                    after_bytes=None,
                    operation="move_source",
                    allow_merge=False,
                ),
                FileMutation(
                    path=destination.path,
                    base=replace(source, path=destination.path),
                    before=destination,
                    after_exists=True,
                    after_bytes=data,
                    operation="move_destination",
                    allow_merge=False,
                ),
            ],
            metadata={**(metadata or {}), "source": source.path, "destination": destination.path},
        )

    def plan_patch(
        self,
        action: str,
        patch: str,
        paths: Iterable[str],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> TransactionPlan:
        normalized_paths = sorted(dict.fromkeys(self._normalize_path(path) for path in paths))
        before = {
            path: self.capture_state(path, store_blob=True) for path in normalized_paths
        }
        for state in before.values():
            if state.kind == "symlink":
                raise TransactionError(f"Refusing to patch symlink path: {state.path}")
        with tempfile.TemporaryDirectory(prefix="agent47-patch-") as raw:
            staging = Path(raw)
            for state in before.values():
                target = staging / state.path
                if not state.exists:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(self._read_blob(state.blob))
                if state.mode is not None:
                    os.chmod(target, stat.S_IMODE(state.mode))
            completed = subprocess.run(
                ["git", "apply", "--whitespace=nowarn"],
                cwd=staging,
                input=patch,
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
            if completed.returncode != 0:
                output = "\n".join(
                    part for part in (completed.stdout, completed.stderr) if part
                ).strip()
                raise TransactionConflict(f"Patch check failed: {output or '<no output>'}")
            entries = []
            for path in normalized_paths:
                target = staging / path
                state = before[path]
                exists = target.is_file()
                after_bytes = target.read_bytes() if exists else None
                operation = (
                    "create"
                    if exists and not state.exists
                    else "delete"
                    if state.exists and not exists
                    else "update"
                )
                entries.append(
                    FileMutation(
                        path=path,
                        base=state,
                        before=state,
                        after_exists=exists,
                        after_bytes=after_bytes,
                        operation=operation,
                    )
                )
        return self.plan(action, entries, metadata=metadata)

    def plan(
        self,
        action: str,
        entries: list[FileMutation],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> TransactionPlan:
        if not entries:
            raise TransactionError("Transaction requires at least one file mutation.")
        transaction_id = self._transaction_id()
        snapshot = self.capture_workspace_snapshot()
        plan = TransactionPlan(
            manager=self,
            transaction_id=transaction_id,
            action=action,
            entries=entries,
            workspace_snapshot=snapshot,
            metadata=metadata or {},
        )
        self._write_plan(plan)
        self._audit("transaction_proposed", plan, paths=plan.paths)
        return plan

    def abort(self, plan: TransactionPlan, reason: str) -> None:
        if plan.state != "proposed":
            return
        plan.state = "aborted"
        plan.metadata["abort_reason"] = reason
        self._write_plan(plan)
        self._audit("transaction_aborted", plan, reason=reason)

    def format_preview(self, plan: TransactionPlan) -> str:
        lines = [
            "Transaction preview:",
            f"- id: {plan.transaction_id}",
            f"- action: {plan.action}",
            f"- files: {len(plan.paths)}",
            *[f"- {path}" for path in plan.paths],
        ]
        diffs = []
        for entry in plan.entries:
            before = self._decode_state(entry.before)
            after_state = self._after_state(entry)
            after = self._decode_state(after_state)
            if before is not None and after is not None:
                diff = git_style_unified_diff(
                    entry.path,
                    before,
                    after,
                    before_exists=entry.before.exists,
                    after_exists=entry.after_exists,
                )
                if diff:
                    diffs.append(diff.rstrip())
            else:
                lines.append(
                    f"- {entry.path}: {entry.operation} "
                    f"({entry.before.size} bytes -> {len(entry.after_bytes or b'')} bytes)"
                )
        if diffs:
            lines.extend(["", "Unified diff:", "\n".join(diffs)])
        return "\n".join(lines)

    def commit(
        self,
        plan: TransactionPlan,
        *,
        validator: Callable[[TransactionPlan], tuple[bool, str]] | None = None,
    ) -> TransactionResult:
        if plan.state != "proposed":
            raise TransactionError(f"Transaction {plan.transaction_id} is {plan.state}.")
        with self._workspace_lock():
            try:
                plan.entries = self._preflight(plan.entries)
                plan.state = "prepared"
                self._write_plan(plan)
                plan.state = "applying"
                self._write_plan(plan, applied_paths=[])
                applied: list[FileMutation] = []
                for entry in plan.entries:
                    self._apply_entry(plan, entry)
                    applied.append(entry)
                    self._write_plan(plan, applied_paths=[item.path for item in applied])
                self._verify_entries(plan.entries)
                if validator is not None:
                    ok, detail = validator(plan)
                    if not ok:
                        raise TransactionError(detail or "Transaction validation failed.")
                plan.state = "committed"
                records = self._records(plan.entries, ok=True)
                self._write_plan(plan, applied_paths=plan.paths, records=records)
                self._audit("transaction_committed", plan, records=records)
                return TransactionResult(
                    ok=True,
                    transaction_id=plan.transaction_id,
                    action=plan.action,
                    state=plan.state,
                    paths=tuple(plan.paths),
                    records=tuple(records),
                    output=(
                        f"Transaction {plan.transaction_id} committed atomically. "
                        f"Files: {', '.join(plan.paths)}."
                    ),
                )
            except Exception as exc:
                conflicts = self._rollback(plan)
                state = "rollback_conflict" if conflicts else "rolled_back"
                plan.state = state
                plan.metadata["failure"] = str(exc)
                records = self._records(plan.entries, ok=False)
                self._write_plan(plan, records=records, conflicts=conflicts)
                self._audit(
                    "transaction_failed",
                    plan,
                    error=str(exc),
                    conflicts=conflicts,
                )
                return TransactionResult(
                    ok=False,
                    transaction_id=plan.transaction_id,
                    action=plan.action,
                    state=state,
                    paths=tuple(plan.paths),
                    records=tuple(records),
                    output=(
                        f"Transaction {plan.transaction_id} failed and "
                        + (
                            "rollback stopped to protect newer user edits: "
                            + ", ".join(conflicts)
                            if conflicts
                            else "all applied changes were rolled back."
                        )
                        + f" Cause: {exc}"
                    ),
                    conflicts=tuple(conflicts),
                )

    def undo(
        self,
        transaction_id: str,
        *,
        paths: Iterable[str] | None = None,
    ) -> TransactionResult:
        return self.commit(self.plan_undo(transaction_id, paths=paths))

    def plan_undo(
        self,
        transaction_id: str,
        *,
        paths: Iterable[str] | None = None,
    ) -> TransactionPlan:
        journal = self.load_journal(transaction_id)
        if journal.get("state") != "committed":
            raise TransactionError(f"Transaction {transaction_id} is not committed.")
        selected = set(paths or [])
        conflicts = self._later_history_conflicts(transaction_id, selected)
        if conflicts:
            raise TransactionConflict(
                "Later committed transactions touched these paths: "
                + ", ".join(conflicts)
            )
        entries = []
        for raw in journal.get("entries", []):
            path = str(raw["path"])
            if selected and path not in selected:
                continue
            before = FileState.from_payload(raw["before"])
            after = FileState.from_payload(raw["after"])
            entries.append(
                FileMutation(
                    path=path,
                    base=after,
                    before=after,
                    after_exists=before.exists,
                    after_bytes=self._read_blob(before.blob)
                    if before.exists and before.kind == "file"
                    else None,
                    operation="undo",
                    allow_merge=False,
                    after_kind=before.kind,
                    after_symlink_target=before.symlink_target,
                )
            )
        return self.plan(
            "undo",
            entries,
            metadata={"source_transaction": transaction_id},
        )

    def redo(
        self,
        transaction_id: str,
        *,
        paths: Iterable[str] | None = None,
    ) -> TransactionResult:
        return self.commit(self.plan_redo(transaction_id, paths=paths))

    def plan_redo(
        self,
        transaction_id: str,
        *,
        paths: Iterable[str] | None = None,
    ) -> TransactionPlan:
        journal = self.load_journal(transaction_id)
        if journal.get("state") != "committed":
            raise TransactionError(f"Transaction {transaction_id} is not committed.")
        selected = set(paths or [])
        conflicts = self._later_history_conflicts(transaction_id, selected)
        if conflicts:
            raise TransactionConflict(
                "Later committed transactions touched these paths: "
                + ", ".join(conflicts)
            )
        entries = []
        for raw in journal.get("entries", []):
            path = str(raw["path"])
            if selected and path not in selected:
                continue
            before = FileState.from_payload(raw["before"])
            after = FileState.from_payload(raw["after"])
            entries.append(
                FileMutation(
                    path=path,
                    base=before,
                    before=before,
                    after_exists=after.exists,
                    after_bytes=self._read_blob(after.blob)
                    if after.exists and after.kind == "file"
                    else None,
                    operation="redo",
                    allow_merge=False,
                    after_kind=after.kind,
                    after_symlink_target=after.symlink_target,
                )
            )
        return self.plan(
            "redo",
            entries,
            metadata={"source_transaction": transaction_id},
        )

    def restore_snapshot(
        self,
        transaction_id: str,
        *,
        paths: Iterable[str] | None = None,
    ) -> TransactionResult:
        return self.commit(self.plan_restore_snapshot(transaction_id, paths=paths))

    def plan_restore_snapshot(
        self,
        transaction_id: str,
        *,
        paths: Iterable[str] | None = None,
    ) -> TransactionPlan:
        journal = self.load_journal(transaction_id)
        manifest = {
            path: FileState.from_payload(payload)
            for path, payload in dict(journal.get("workspace_snapshot", {})).items()
        }
        selected = set(paths or [])
        current = self.capture_workspace_snapshot()
        all_paths = sorted(set(manifest) | set(current))
        entries = []
        for path in all_paths:
            if selected and path not in selected:
                continue
            desired = manifest.get(path, FileState(path=path, exists=False))
            before = current.get(path, FileState(path=path, exists=False))
            if self._state_equivalent(before, desired):
                continue
            entries.append(
                FileMutation(
                    path=path,
                    base=before,
                    before=before,
                    after_exists=desired.exists,
                    after_bytes=self._read_blob(desired.blob)
                    if desired.exists and desired.kind == "file"
                    else None,
                    operation="snapshot_restore",
                    allow_merge=False,
                    after_kind=desired.kind,
                    after_symlink_target=desired.symlink_target,
                )
            )
        return self.plan(
            "snapshot_restore",
            entries,
            metadata={"source_transaction": transaction_id},
        )

    def list_transactions(self) -> list[dict[str, Any]]:
        transactions = []
        for path in sorted(self.journals.glob("*.json"), reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            transactions.append(
                {
                    "id": payload.get("id"),
                    "action": payload.get("action"),
                    "state": payload.get("state"),
                    "created_at": payload.get("created_at"),
                    "paths": [entry.get("path") for entry in payload.get("entries", [])],
                    "source_transaction": dict(payload.get("metadata", {})).get(
                        "source_transaction"
                    ),
                    "run_id": dict(payload.get("metadata", {})).get("run_id"),
                    "step": dict(payload.get("metadata", {})).get("step"),
                }
            )
        return transactions

    def consume_recovery_results(self) -> list[TransactionResult]:
        results = list(self.recovery_results)
        self.recovery_results.clear()
        return results

    def attach_run_context(self, transaction_id: str, *, run_id: int, step: int) -> None:
        path = self._journal_path(transaction_id)
        payload = self.load_journal(transaction_id)
        metadata = dict(payload.get("metadata", {}))
        metadata.update({"run_id": run_id, "step": step})
        payload["metadata"] = metadata
        payload["updated_at"] = datetime.now(UTC).isoformat()
        self._atomic_json(path, payload)

    def recover_incomplete(self) -> list[TransactionResult]:
        if self._lock_owner_active():
            return []
        recovered = []
        for path in sorted(self.journals.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            state = str(payload.get("state") or "")
            if state == "proposed":
                payload["state"] = "aborted"
                payload.setdefault("metadata", {})["abort_reason"] = (
                    "abandoned before mutation; recovered on startup"
                )
                self._atomic_json(path, payload)
                continue
            if state not in {"prepared", "applying", "rolling_back"}:
                continue
            plan = self._plan_from_journal(payload)
            conflicts = self._rollback(plan)
            plan.state = "recovery_conflict" if conflicts else "recovered_rolled_back"
            self._write_plan(plan, conflicts=conflicts)
            self._audit("transaction_recovered", plan, conflicts=conflicts)
            recovered.append(
                TransactionResult(
                    ok=not conflicts,
                    transaction_id=plan.transaction_id,
                    action=plan.action,
                    state=plan.state,
                    paths=tuple(plan.paths),
                    records=tuple(self._records(plan.entries, ok=False)),
                    output=(
                        "Recovered interrupted transaction."
                        if not conflicts
                        else "Recovery stopped to protect newer user edits."
                    ),
                    conflicts=tuple(conflicts),
                    recovered=True,
                )
            )
        if self.lock_path.exists():
            try:
                self.lock_path.unlink()
            except OSError:
                pass
        return recovered

    def _lock_owner_active(self) -> bool:
        if not self.lock_path.is_file():
            return False
        try:
            payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
            pid = int(payload.get("pid") or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return False
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _later_history_conflicts(
        self,
        transaction_id: str,
        selected: set[str],
    ) -> list[str]:
        source = self.load_journal(transaction_id)
        created_at = str(source.get("created_at") or "")
        source_paths = {
            str(entry.get("path"))
            for entry in source.get("entries", [])
            if entry.get("path")
        }
        if selected:
            source_paths &= selected
        conflicts = set()
        for path in self.journals.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("id") == transaction_id or payload.get("state") != "committed":
                continue
            if str(payload.get("created_at") or "") <= created_at:
                continue
            metadata = dict(payload.get("metadata", {}))
            if (
                metadata.get("source_transaction") == transaction_id
                and payload.get("action") in {"undo", "redo"}
            ):
                continue
            touched = {
                str(entry.get("path"))
                for entry in payload.get("entries", [])
                if entry.get("path")
            }
            conflicts.update(source_paths & touched)
        return sorted(conflicts)

    def capture_state(self, requested_path: str, *, store_blob: bool) -> FileState:
        path = self._normalize_path(requested_path)
        target = self._lexical_target(path)
        if target.is_symlink():
            info = target.lstat()
            return FileState(
                path=path,
                exists=True,
                kind="symlink",
                mode=info.st_mode,
                atime_ns=info.st_atime_ns,
                mtime_ns=info.st_mtime_ns,
                symlink_target=os.readlink(target),
            )
        if not target.exists():
            return FileState(path=path, exists=False)
        if not target.is_file():
            return FileState(path=path, exists=True, kind="directory")
        data = target.read_bytes()
        info = target.stat()
        encoding, newline = detect_text_format(data)
        blob = self._store_blob(data) if store_blob else None
        return FileState(
            path=path,
            exists=True,
            kind="file",
            sha256=_sha256(data),
            size=len(data),
            mode=info.st_mode,
            atime_ns=info.st_atime_ns,
            mtime_ns=info.st_mtime_ns,
            encoding=encoding,
            newline=newline,
            blob=blob,
        )

    def capture_workspace_snapshot(self) -> dict[str, FileState]:
        manifest: dict[str, FileState] = {}
        for path in self.workspace.rglob("*"):
            try:
                relative = path.relative_to(self.workspace)
            except ValueError:
                continue
            if any(part in IGNORED_SNAPSHOT_NAMES for part in relative.parts):
                continue
            if path.is_dir() and not path.is_symlink():
                continue
            if is_sensitive_path(path, self.workspace):
                continue
            normalized = relative.as_posix()
            manifest[normalized] = self.capture_state(normalized, store_blob=True)
        return manifest

    def load_journal(self, transaction_id: str) -> dict[str, Any]:
        path = self._journal_path(transaction_id)
        if not path.is_file():
            raise TransactionError(f"Unknown transaction: {transaction_id}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TransactionError(f"Unreadable transaction journal: {transaction_id}") from exc

    def _preflight(self, entries: list[FileMutation]) -> list[FileMutation]:
        prepared = []
        for entry in entries:
            current = self.capture_state(entry.path, store_blob=True)
            if current.kind == "symlink":
                raise TransactionConflict(f"Symlink changed at {entry.path}.")
            if self._state_equivalent(current, entry.before):
                prepared.append(entry)
                continue
            desired_state = self._after_state(entry)
            if self._state_equivalent(current, desired_state):
                prepared.append(replace(entry, before=current))
                continue
            if not entry.allow_merge:
                raise TransactionConflict(
                    f"{entry.path} changed after preview; refusing to overwrite newer edits."
                )
            merged = self._merge_entry(entry, current)
            prepared.append(merged)
        return prepared

    def _merge_entry(self, entry: FileMutation, current: FileState) -> FileMutation:
        if (
            not entry.base.exists
            or not current.exists
            or not entry.after_exists
            or entry.base.kind != "file"
            or current.kind != "file"
            or entry.after_bytes is None
        ):
            raise TransactionConflict(
                f"{entry.path} changed after preview and cannot be merged safely."
            )
        base_bytes = self._read_blob(entry.base.blob)
        current_bytes = self._read_blob(current.blob)
        encoding = entry.base.encoding or "utf-8"
        try:
            base = base_bytes.decode(encoding)
            ours = current_bytes.decode(current.encoding or encoding)
            theirs = entry.after_bytes.decode(encoding)
        except UnicodeDecodeError as exc:
            raise TransactionConflict(
                f"{entry.path} changed after preview and is not mergeable text."
            ) from exc
        merged = three_way_merge(base, ours, theirs)
        merged_bytes = self.encode_text(merged, current)
        return replace(entry, before=current, after_bytes=merged_bytes)

    def _apply_entry(self, plan: TransactionPlan, entry: FileMutation) -> None:
        target = self._lexical_target(entry.path)
        if entry.after_exists:
            if entry.after_kind == "symlink":
                target.parent.mkdir(parents=True, exist_ok=True)
                temp = target.with_name(f".{target.name}.agent47-{plan.transaction_id}.link")
                try:
                    if temp.exists() or temp.is_symlink():
                        temp.unlink()
                    os.symlink(entry.after_symlink_target or "", temp)
                    os.replace(temp, target)
                    self._fsync_parent(target)
                finally:
                    if temp.exists() or temp.is_symlink():
                        temp.unlink()
                return
            if entry.after_kind != "file" or entry.after_bytes is None:
                raise TransactionError(f"Missing after content for {entry.path}.")
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(f".{target.name}.agent47-{plan.transaction_id}.tmp")
            try:
                with temp.open("wb") as handle:
                    handle.write(entry.after_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                mode = (
                    entry.before.mode
                    if entry.before.exists
                    else entry.base.mode
                    if entry.base.exists
                    else None
                )
                if mode is not None:
                    os.chmod(temp, stat.S_IMODE(mode))
                os.replace(temp, target)
                if entry.before.exists and entry.before.atime_ns and entry.before.mtime_ns:
                    os.utime(
                        target,
                        ns=(entry.before.atime_ns, entry.before.mtime_ns),
                    )
                self._fsync_parent(target)
            finally:
                if temp.exists():
                    temp.unlink()
        else:
            if target.exists() or target.is_symlink():
                target.unlink()
                self._fsync_parent(target)

    def _verify_entries(self, entries: list[FileMutation]) -> None:
        failed = []
        for entry in entries:
            current = self.capture_state(entry.path, store_blob=False)
            if not self._state_equivalent(current, self._after_state(entry)):
                failed.append(entry.path)
        if failed:
            raise TransactionError(
                "Post-transaction hash verification failed for: " + ", ".join(failed)
            )

    def _rollback(self, plan: TransactionPlan) -> list[str]:
        plan.state = "rolling_back"
        self._write_plan(plan)
        conflicts = []
        for entry in reversed(plan.entries):
            current = self.capture_state(entry.path, store_blob=False)
            current_matches_after = self._state_equivalent(
                current,
                self._after_state(entry),
            )
            current_matches_before = self._state_equivalent(current, entry.before)
            if not current_matches_after and not current_matches_before:
                conflicts.append(entry.path)
                continue
            if current_matches_before:
                continue
            try:
                self._restore_state(entry.before)
            except OSError:
                conflicts.append(entry.path)
        return conflicts

    def _restore_state(self, state: FileState) -> None:
        target = self._lexical_target(state.path)
        if not state.exists:
            if target.exists() or target.is_symlink():
                target.unlink()
            return
        if state.kind == "symlink":
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                target.unlink()
            os.symlink(state.symlink_target or "", target)
            return
        if state.kind != "file":
            raise OSError(f"Unsupported rollback state: {state.kind}")
        data = self._read_blob(state.blob)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.agent47-restore.tmp")
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if state.mode is not None:
            os.chmod(temp, stat.S_IMODE(state.mode))
        os.replace(temp, target)
        if state.atime_ns is not None and state.mtime_ns is not None:
            os.utime(target, ns=(state.atime_ns, state.mtime_ns))
        self._fsync_parent(target)

    def _records(self, entries: list[FileMutation], *, ok: bool) -> list[dict[str, Any]]:
        records = []
        for entry in entries:
            after_state = (
                self.capture_state(entry.path, store_blob=True)
                if ok
                else self._after_state(entry)
            )
            inverse_patch = self._inverse_patch(entry, after_state)
            records.append(
                {
                    "path": entry.path,
                    "operation": entry.operation,
                    "ok": ok,
                    "verified": ok,
                    "exists_before": entry.before.exists,
                    "exists_after": entry.after_exists,
                    "before_sha256": entry.before.sha256,
                    "after_sha256": after_state.sha256,
                    "before": entry.before.as_payload(),
                    "after": after_state.as_payload(),
                    "content_changed": not self._state_equivalent(entry.before, after_state),
                    "inverse_patch": inverse_patch,
                }
            )
        return records

    def _inverse_patch(self, entry: FileMutation, after: FileState) -> str:
        before_text = self._decode_state(entry.before)
        after_text = self._decode_state(after)
        if before_text is None or after_text is None:
            return ""
        return git_style_unified_diff(
            entry.path,
            after_text,
            before_text,
            before_exists=after.exists,
            after_exists=entry.before.exists,
        )

    def _decode_state(self, state: FileState) -> str | None:
        if not state.exists:
            return ""
        if state.kind != "file" or not state.encoding:
            return None
        try:
            return self._read_blob(state.blob).decode(state.encoding)
        except UnicodeDecodeError:
            return None

    def _write_plan(
        self,
        plan: TransactionPlan,
        *,
        applied_paths: list[str] | None = None,
        records: list[dict[str, Any]] | None = None,
        conflicts: list[str] | None = None,
    ) -> None:
        existing = {}
        path = self._journal_path(plan.transaction_id)
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
        payload = {
            "version": 1,
            "id": plan.transaction_id,
            "workspace": str(self.workspace),
            "action": plan.action,
            "state": plan.state,
            "created_at": existing.get("created_at") or datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "metadata": plan.metadata,
            "entries": [
                {
                    "path": entry.path,
                    "operation": entry.operation,
                    "allow_merge": entry.allow_merge,
                    "after_kind": entry.after_kind,
                    "after_symlink_target": entry.after_symlink_target,
                    "base": entry.base.as_payload(),
                    "before": entry.before.as_payload(),
                    "after": self._after_state(entry).as_payload(),
                }
                for entry in plan.entries
            ],
            "workspace_snapshot": {
                path: state.as_payload()
                for path, state in sorted(plan.workspace_snapshot.items())
            },
            "applied_paths": applied_paths
            if applied_paths is not None
            else existing.get("applied_paths", []),
            "records": records if records is not None else existing.get("records", []),
            "conflicts": conflicts
            if conflicts is not None
            else existing.get("conflicts", []),
        }
        self._atomic_json(path, payload)

    def _plan_from_journal(self, payload: dict[str, Any]) -> TransactionPlan:
        entries = []
        for raw in payload.get("entries", []):
            after = FileState.from_payload(raw["after"])
            entries.append(
                FileMutation(
                    path=str(raw["path"]),
                    operation=str(raw.get("operation") or "update"),
                    allow_merge=bool(raw.get("allow_merge", True)),
                    base=FileState.from_payload(raw["base"]),
                    before=FileState.from_payload(raw["before"]),
                    after_exists=after.exists,
                    after_bytes=self._read_blob(after.blob)
                    if after.exists and after.kind == "file"
                    else None,
                    after_kind=str(raw.get("after_kind") or after.kind),
                    after_symlink_target=raw.get("after_symlink_target")
                    or after.symlink_target,
                )
            )
        snapshot = {
            path: FileState.from_payload(state)
            for path, state in dict(payload.get("workspace_snapshot", {})).items()
        }
        return TransactionPlan(
            manager=self,
            transaction_id=str(payload["id"]),
            action=str(payload.get("action") or "recovery"),
            entries=entries,
            workspace_snapshot=snapshot,
            metadata=dict(payload.get("metadata", {})),
            state=str(payload.get("state") or "applying"),
        )

    def _state_equivalent(self, left: FileState, right: FileState) -> bool:
        if left.exists != right.exists or left.kind != right.kind:
            return False
        if not left.exists:
            return True
        if left.kind == "symlink":
            return left.symlink_target == right.symlink_target
        return left.sha256 == right.sha256

    def _after_state(self, entry: FileMutation) -> FileState:
        if not entry.after_exists:
            return FileState(path=entry.path, exists=False)
        if entry.after_kind == "symlink":
            return FileState(
                path=entry.path,
                exists=True,
                kind="symlink",
                mode=entry.before.mode,
                symlink_target=entry.after_symlink_target,
            )
        data = entry.after_bytes or b""
        encoding, newline = detect_text_format(data)
        mode = (
            entry.before.mode
            if entry.before.exists
            else entry.base.mode
            if entry.base.exists
            else None
        )
        return FileState(
            path=entry.path,
            exists=True,
            kind="file",
            sha256=_sha256(data),
            size=len(data),
            mode=mode,
            encoding=encoding,
            newline=newline,
            blob=self._store_blob(data),
        )

    def _store_blob(self, data: bytes) -> str:
        digest = _sha256(data)
        path = self.blobs / digest
        if not path.exists():
            temp = path.with_suffix(".tmp")
            with temp.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
            self._restrict(path)
        return digest

    def _read_blob(self, blob: str | None) -> bytes:
        if not blob:
            raise TransactionError("Transaction journal is missing a content blob.")
        path = self.blobs / blob
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise TransactionError(f"Missing transaction blob: {blob}") from exc
        if _sha256(data) != blob:
            raise TransactionError(f"Corrupt transaction blob: {blob}")
        return data

    def _normalize_path(self, requested_path: str) -> str:
        normalized = requested_path.replace("\\", "/").strip()
        if not normalized or normalized.startswith("/") or ":" in normalized:
            raise TransactionError(f"Path must be workspace-relative: {requested_path}")
        if ".." in normalized.split("/"):
            raise TransactionError(f"Path escapes workspace: {requested_path}")
        return Path(normalized).as_posix()

    def _lexical_target(self, path: str) -> Path:
        target = self.workspace / self._normalize_path(path)
        resolved_parent = target.parent.resolve()
        validate_workspace_boundary(resolved_parent, self.workspace)
        if target.exists() and not target.is_symlink():
            validate_workspace_boundary(target.resolve(), self.workspace)
        return target

    def _transaction_id(self) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        return f"{stamp}-{uuid.uuid4().hex[:10]}"

    def _journal_path(self, transaction_id: str) -> Path:
        if not re_safe_identifier(transaction_id):
            raise TransactionError("Invalid transaction identifier.")
        return self.journals / f"{transaction_id}.json"

    def _atomic_json(self, path: Path, payload: dict[str, Any]) -> None:
        temp = path.with_suffix(".tmp")
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        self._restrict(path)
        self._fsync_parent(path)

    def _audit(self, event: str, plan: TransactionPlan, **payload: Any) -> None:
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            "transaction_id": plan.transaction_id,
            "action": plan.action,
            "state": plan.state,
            **payload,
        }
        with self.audit_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._restrict(self.audit_path)

    def _workspace_lock(self):
        return _WorkspaceLock(self.lock_path)

    @staticmethod
    def _restrict(path: Path) -> None:
        if os.name != "nt":
            try:
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
            except OSError:
                pass

    @staticmethod
    def _fsync_parent(path: Path) -> None:
        if os.name == "nt":
            return
        try:
            descriptor = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class _WorkspaceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.acquired = False

    def __enter__(self) -> _WorkspaceLock:
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise TransactionError("Another workspace transaction is active.") from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                )
            )
            handle.flush()
            os.fsync(handle.fileno())
        self.acquired = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.acquired:
            try:
                self.path.unlink()
            except OSError:
                pass


def detect_text_format(data: bytes) -> tuple[str | None, str | None]:
    encoding: str | None
    if data.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    elif data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        encoding = "utf-16"
    else:
        try:
            data.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            return None, None
    text = data.decode(encoding)
    newline = "\r\n" if "\r\n" in text else "\r" if "\r" in text else "\n"
    return encoding, newline


def three_way_merge(base: str, current: str, desired: str) -> str:
    if current == base:
        return desired
    if desired == base:
        return current
    line_merge = _merge_non_overlapping_lines(base, current, desired)
    if line_merge is not None:
        return line_merge
    with tempfile.TemporaryDirectory(prefix="agent47-merge-") as raw:
        root = Path(raw)
        current_path = root / "current"
        base_path = root / "base"
        desired_path = root / "desired"
        current_path.write_text(current, encoding="utf-8", newline="")
        base_path.write_text(base, encoding="utf-8", newline="")
        desired_path.write_text(desired, encoding="utf-8", newline="")
        completed = subprocess.run(
            ["git", "merge-file", "-p", str(current_path), str(base_path), str(desired_path)],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    if completed.returncode == 0:
        return completed.stdout
    if completed.returncode == 1:
        raise TransactionConflict("Three-way merge produced conflicts.")
    raise TransactionError(
        "Three-way merge failed: " + (completed.stderr.strip() or "unknown git merge-file error")
    )


def _merge_non_overlapping_lines(
    base: str,
    current: str,
    desired: str,
) -> str | None:
    base_lines = base.splitlines(keepends=True)
    current_lines = current.splitlines(keepends=True)
    desired_lines = desired.splitlines(keepends=True)
    current_changes = _line_changes(base_lines, current_lines)
    desired_changes = _line_changes(base_lines, desired_lines)
    if any(
        _changes_overlap(current_change, desired_change)
        for current_change in current_changes
        for desired_change in desired_changes
    ):
        return None
    merged = list(current_lines)
    for start, end, replacement in sorted(
        desired_changes,
        key=lambda item: (item[0], item[1]),
        reverse=True,
    ):
        mapped_start = start + _line_offset_before(current_changes, start)
        mapped_end = end + _line_offset_before(current_changes, end)
        merged[mapped_start:mapped_end] = replacement
    return "".join(merged)


def _line_changes(
    base: list[str],
    variant: list[str],
) -> list[tuple[int, int, list[str]]]:
    changes = []
    matcher = difflib.SequenceMatcher(a=base, b=variant, autojunk=False)
    for tag, start, end, variant_start, variant_end in matcher.get_opcodes():
        if tag != "equal":
            changes.append((start, end, variant[variant_start:variant_end]))
    return changes


def _changes_overlap(
    left: tuple[int, int, list[str]],
    right: tuple[int, int, list[str]],
) -> bool:
    left_start, left_end, _ = left
    right_start, right_end, _ = right
    if left_start == left_end and right_start == right_end:
        return left_start == right_start
    if left_start == left_end:
        return right_start < left_start < right_end
    if right_start == right_end:
        return left_start < right_start < left_end
    return max(left_start, right_start) < min(left_end, right_end)


def _line_offset_before(
    changes: list[tuple[int, int, list[str]]],
    position: int,
) -> int:
    return sum(
        len(replacement) - (end - start)
        for start, end, replacement in changes
        if end <= position
    )


def re_safe_identifier(value: str) -> bool:
    return bool(value) and all(character.isalnum() or character in "-_" for character in value)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
