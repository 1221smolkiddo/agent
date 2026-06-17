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
                """
            )
