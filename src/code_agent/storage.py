from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class AgentStorage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def create_run(self, task: str, model: str, cwd: Path) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "insert into runs (task, model, cwd) values (?, ?, ?)",
                (task, model, str(cwd)),
            )
            return int(cursor.lastrowid)

    def add_step(self, run_id: int, role: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "insert into steps (run_id, role, payload) values (?, ?, ?)",
                (run_id, role, json.dumps(payload)),
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
                    payload.get("error"),
                    json.dumps(payload),
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
                (run_id, body, json.dumps(payload)),
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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
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
                    estimated_cost_usd real,
                    fallback_from text,
                    error text,
                    payload text not null
                );
                """
            )
            self._ensure_column(conn, "model_usage", "estimated_cost_usd", "real")

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
