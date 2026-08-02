from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .safety import redact_secrets


CURRENT_SCHEMA_VERSION = 3


class AgentStorage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._restrict_database_permissions()

    def create_run(self, task: str, model: str, cwd: Path) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "insert into runs (task, model, cwd) values (?, ?, ?)",
                (redact_secrets(task), model, str(cwd)),
            )
            return int(cursor.lastrowid)

    def add_step(self, run_id: int, role: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "insert into steps (run_id, role, payload) values (?, ?, ?)",
                (run_id, role, self._safe_json(payload)),
            )

    def add_model_usage(self, run_id: int, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into model_usage (
                    run_id, provider, model, ok, prompt_tokens, completion_tokens,
                    total_tokens, estimated_cost_usd, fallback_from, error, payload
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    payload.get("provider"),
                    payload.get("model"),
                    1 if payload.get("ok") else 0,
                    payload.get("prompt_tokens"),
                    payload.get("completion_tokens"),
                    payload.get("total_tokens"),
                    payload.get("estimated_cost_usd"),
                    payload.get("fallback_from"),
                    redact_secrets(str(payload.get("error"))) if payload.get("error") else None,
                    self._safe_json(payload),
                ),
            )

    def model_usage(self, run_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select id, created_at, payload
                from model_usage
                where run_id = ?
                order by id
                """,
                (run_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]

    @property
    def schema_version(self) -> int:
        with self._connect() as conn:
            return self._current_schema_version(conn)

    def save_work_report(
        self,
        run_id: int,
        body: str,
        payload: dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into work_reports (run_id, body, payload)
                values (?, ?, ?)
                on conflict(run_id) do update set
                    created_at = current_timestamp,
                    body = excluded.body,
                    payload = excluded.payload
                """,
                (run_id, redact_secrets(body), self._safe_json(payload)),
            )

    def get_work_report(self, run_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                select id, created_at, run_id, body, payload
                from work_reports
                where run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "run_id": row["run_id"],
            "body": row["body"],
            "payload": json.loads(row["payload"]),
        }

    def recent_runs(self, limit: int) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select id, created_at, task, model, cwd
                from runs
                order by id desc
                limit ?
                """,
                (limit,),
            ).fetchall()
            return list(rows)

    def get_run(self, run_id: int) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                """
                select id, created_at, task, model, cwd
                from runs
                where id = ?
                """,
                (run_id,),
            ).fetchone()

    def run_steps(self, run_id: int) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select id, created_at, role, payload
                from steps
                where run_id = ?
                order by id
                """,
                (run_id,),
            ).fetchall()
            return list(rows)

    def run_steps_payloads(self, run_id: int) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for row in self.run_steps(run_id):
            payload = json.loads(row["payload"])
            payloads.append(
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "role": row["role"],
                    "payload": payload,
                }
            )
        return payloads

    def diagnostic_occurrence_count(
        self,
        signature: str,
        *,
        before_run_id: int,
        limit: int = 100,
    ) -> int:
        if not signature:
            return 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                select payload
                from steps
                where run_id < ? and payload like ?
                order by id desc
                limit ?
                """,
                (before_run_id, f"%{signature}%", limit),
            ).fetchall()
        count = 0
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                continue
            count += int(_contains_diagnostic_signature(payload, signature))
        return count

    def delete_run(self, run_id: int) -> bool:
        with self._connect() as conn:
            exists = conn.execute("select 1 from runs where id = ?", (run_id,)).fetchone()
            if exists is None:
                return False
            conn.execute("delete from model_usage where run_id = ?", (run_id,))
            conn.execute("delete from work_reports where run_id = ?", (run_id,))
            conn.execute("delete from steps where run_id = ?", (run_id,))
            conn.execute("delete from runs where id = ?", (run_id,))
            return True

    def prune_runs(self, keep_last: int) -> int:
        if keep_last < 0:
            raise ValueError("keep_last must be zero or greater")
        with self._connect() as conn:
            rows = conn.execute(
                "select id from runs order by id desc limit -1 offset ?",
                (keep_last,),
            ).fetchall()
        deleted = 0
        for row in rows:
            deleted += int(self.delete_run(int(row["id"])))
        return deleted

    def _connect(self) -> sqlite3.Connection:
        from .failure_types import SQLITE_RETRY_ATTEMPTS, SQLITE_RETRY_BACKOFF_SECONDS
        import time as _time

        last_error: sqlite3.OperationalError | None = None
        for attempt in range(SQLITE_RETRY_ATTEMPTS + 1):
            try:
                conn = sqlite3.connect(self.db_path, timeout=30)
                conn.row_factory = sqlite3.Row
                conn.execute("pragma foreign_keys = on")
                conn.execute("pragma busy_timeout = 30000")
                if str(self.db_path) == ":memory:" or self.db_path.name == "eval.db":
                    conn.execute("pragma synchronous = OFF")
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

    def _init_db(self) -> None:
        with self._connect() as conn:
            if str(self.db_path) == ":memory:" or self.db_path.name == "eval.db":
                conn.execute("pragma journal_mode = MEMORY")
            else:
                conn.execute("pragma journal_mode = wal")
            self._migrate(conn)

    def _restrict_database_permissions(self) -> None:
        if os.name != "nt" and self.db_path.exists():
            self.db_path.chmod(0o600)

    @classmethod
    def _safe_json(cls, payload: dict[str, Any]) -> str:
        return json.dumps(cls._redact_payload(payload))

    @classmethod
    def _redact_payload(cls, value: Any) -> Any:
        if isinstance(value, str):
            return redact_secrets(value)
        if isinstance(value, dict):
            return {str(key): cls._redact_payload(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._redact_payload(item) for item in value]
        if isinstance(value, tuple):
            return [cls._redact_payload(item) for item in value]
        return value

    def _migrate(self, conn: sqlite3.Connection) -> None:
        legacy_database = self._has_user_tables(conn) and not self._table_exists(
            conn, "schema_migrations"
        )
        if legacy_database:
            self._backup_before_migration()
            self._create_migration_table(conn)
            self._record_migration(conn, 1)
        else:
            self._create_migration_table(conn)

        version = self._current_schema_version(conn)
        if version < 1:
            self._migration_001_initial(conn)
            self._record_migration(conn, 1)
            version = 1
        if version < 2:
            self._migration_002_model_usage_cost(conn)
            self._record_migration(conn, 2)
            version = 2
        if version < 3:
            self._migration_003_repo_index_cache(conn)
            self._record_migration(conn, 3)

    def _migration_001_initial(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            create table if not exists runs (
                id integer primary key autoincrement,
                created_at text not null default current_timestamp,
                task text not null,
                model text not null,
                cwd text not null
            );

            create table if not exists steps (
                id integer primary key autoincrement,
                run_id integer not null references runs(id),
                created_at text not null default current_timestamp,
                role text not null,
                payload text not null
            );

            create table if not exists work_reports (
                id integer primary key autoincrement,
                run_id integer not null unique references runs(id),
                created_at text not null default current_timestamp,
                body text not null,
                payload text not null
            );

            create table if not exists model_usage (
                id integer primary key autoincrement,
                run_id integer not null references runs(id),
                created_at text not null default current_timestamp,
                provider text,
                model text not null,
                ok integer not null,
                prompt_tokens integer,
                completion_tokens integer,
                total_tokens integer,
                fallback_from text,
                error text,
                payload text not null
            );
            """
        )

    def _migration_002_model_usage_cost(self, conn: sqlite3.Connection) -> None:
        self._ensure_column(conn, "model_usage", "estimated_cost_usd", "real")

    def _migration_003_repo_index_cache(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            create table if not exists repo_index_files (
                workspace text not null,
                path text not null,
                kind text not null,
                size integer not null,
                importance integer not null,
                mtime_ns integer not null,
                sha256 text not null,
                module_name text not null default '',
                symbols_json text not null default '[]',
                imports_json text not null default '[]',
                updated_at text not null default current_timestamp,
                primary key (workspace, path)
            );

            create index if not exists idx_repo_index_files_workspace
                on repo_index_files(workspace);

            create table if not exists repo_index_meta (
                workspace text primary key,
                refreshed_at text not null default current_timestamp,
                file_count integer not null
            );
            """
        )

    def _create_migration_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            create table if not exists schema_migrations (
                version integer primary key,
                applied_at text not null default current_timestamp
            )
            """
        )

    def _record_migration(self, conn: sqlite3.Connection, version: int) -> None:
        conn.execute(
            "insert or ignore into schema_migrations (version) values (?)",
            (version,),
        )

    def _current_schema_version(self, conn: sqlite3.Connection) -> int:
        if not self._table_exists(conn, "schema_migrations"):
            return 0
        row = conn.execute("select max(version) as version from schema_migrations").fetchone()
        return int(row["version"] or 0)

    def _backup_before_migration(self) -> None:
        if not self.db_path.exists():
            return
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        backup_path = self.db_path.with_name(f"{self.db_path.name}.bak-{stamp}")
        shutil.copy2(self.db_path, backup_path)

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            "select name from sqlite_master where type = 'table' and name = ?",
            (table,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _has_user_tables(conn: sqlite3.Connection) -> bool:
        rows = conn.execute(
            """
            select name from sqlite_master
            where type = 'table' and name not like 'sqlite_%'
            """
        ).fetchall()
        return bool(rows)

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        table: str,
        column: str,
        column_type: str,
    ) -> None:
        columns = {
            row["name"]
            for row in conn.execute(f"pragma table_info({table})").fetchall()
        }
        if column not in columns:
            conn.execute(f"alter table {table} add column {column} {column_type}")


def _contains_diagnostic_signature(value: Any, signature: str) -> bool:
    if isinstance(value, dict):
        return value.get("signature") == signature or any(
            _contains_diagnostic_signature(item, signature) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_diagnostic_signature(item, signature) for item in value)
    return False
