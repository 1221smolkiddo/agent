from pathlib import Path
import sqlite3
import json

from code_agent.resume import build_resume_task, compact_run_context, format_run_detail
from code_agent.storage import CURRENT_SCHEMA_VERSION, AgentStorage
from code_agent.debug_bundle import export_debug_bundle


def test_storage_returns_run_and_decoded_steps(tmp_path: Path) -> None:
    storage = AgentStorage(tmp_path / "agent.db")
    run_id = storage.create_run("fix the parser", "fake-model", tmp_path)
    storage.add_step(run_id, "assistant", {"type": "read_file", "path": "src/parser.py"})
    storage.add_step(
        run_id,
        "tool",
        {
            "type": "tool_result",
            "ok": True,
            "output": "parser source",
        },
    )

    run = storage.get_run(run_id)
    steps = storage.run_steps_payloads(run_id)

    assert run is not None
    assert run["task"] == "fix the parser"
    assert steps[0]["role"] == "assistant"
    assert steps[0]["payload"] == {"type": "read_file", "path": "src/parser.py"}
    assert steps[1]["payload"]["output"] == "parser source"
    assert storage.schema_version == CURRENT_SCHEMA_VERSION
    with sqlite3.connect(storage.db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
    assert "repo_index_files" in tables
    assert "repo_index_meta" in tables


def test_storage_counts_recurring_diagnostic_signatures(tmp_path: Path) -> None:
    storage = AgentStorage(tmp_path / "agent.db")
    first_run = storage.create_run("first", "fake-model", tmp_path)
    storage.add_step(
        first_run,
        "tool",
        {
            "type": "tool_result",
            "metadata": {
                "diagnostics": {
                    "signature": "same-failure",
                    "summary": "type error",
                }
            },
        },
    )
    second_run = storage.create_run("second", "fake-model", tmp_path)

    assert (
        storage.diagnostic_occurrence_count(
            "same-failure",
            before_run_id=second_run,
        )
        == 1
    )


def test_storage_migrates_legacy_database_and_creates_backup(tmp_path: Path) -> None:
    db_path = tmp_path / "agent.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            create table runs (
                id integer primary key autoincrement,
                created_at text not null default current_timestamp,
                task text not null,
                model text not null,
                cwd text not null
            );
            create table steps (
                id integer primary key autoincrement,
                run_id integer not null references runs(id),
                created_at text not null default current_timestamp,
                role text not null,
                payload text not null
            );
            create table work_reports (
                id integer primary key autoincrement,
                run_id integer not null unique references runs(id),
                created_at text not null default current_timestamp,
                body text not null,
                payload text not null
            );
            create table model_usage (
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

    storage = AgentStorage(db_path)

    assert storage.schema_version == CURRENT_SCHEMA_VERSION
    assert list(tmp_path.glob("agent.db.bak-*"))
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("pragma table_info(model_usage)").fetchall()}
    assert "estimated_cost_usd" in columns


def test_debug_bundle_export_redacts_saved_payloads(tmp_path: Path) -> None:
    storage = AgentStorage(tmp_path / "agent.db")
    run_id = storage.create_run("debug TOKEN=super-secret-token", "fake-model", tmp_path)
    storage.add_step(
        run_id,
        "tool",
        {"type": "tool_result", "ok": False, "output": "API_KEY=super-secret-token"},
    )

    path = export_debug_bundle(storage, run_id, tmp_path / "bundles")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["type"] == "agent47_debug_bundle"
    assert payload["run"]["id"] == run_id
    assert "super-secret-token" not in path.read_text(encoding="utf-8")
    assert payload["summary"]["failed_tool_step_count"] == 1


def test_storage_saves_and_updates_work_report(tmp_path: Path) -> None:
    storage = AgentStorage(tmp_path / "agent.db")
    run_id = storage.create_run("fix the parser", "fake-model", tmp_path)

    storage.save_work_report(
        run_id,
        "Current Task:\n  fix the parser",
        {"type": "work_report", "sections": {"current_task": "fix the parser"}},
    )
    storage.save_work_report(
        run_id,
        "Current Task:\n  fix the parser\nFinal Outcome:\n  done",
        {"type": "work_report", "sections": {"final_outcome": "done"}},
    )

    report = storage.get_work_report(run_id)

    assert report is not None
    assert report["run_id"] == run_id
    assert "Final Outcome" in report["body"]
    assert report["payload"]["sections"]["final_outcome"] == "done"


def test_resume_task_includes_original_task_prior_steps_and_instruction(tmp_path: Path) -> None:
    storage = AgentStorage(tmp_path / "agent.db")
    run_id = storage.create_run("fix the parser", "fake-model", tmp_path)
    storage.add_step(run_id, "assistant", {"type": "edit_file", "path": "src/parser.py"})
    storage.add_step(
        run_id,
        "tool",
        {
            "type": "tool_result",
            "ok": True,
            "changed_paths": ["src/parser.py"],
            "output": "diff",
        },
    )
    run = storage.get_run(run_id)
    assert run is not None

    storage.save_work_report(
        run_id,
        "Current Task:\n  fix the parser\nModified Files:\n  src/parser.py",
        {"type": "work_report"},
    )

    task = build_resume_task(
        run,
        storage.run_steps_payloads(run_id),
        "run focused tests",
        storage.get_work_report(run_id),
    )

    assert f"Resume Agent47 run {run_id}." in task
    assert "Original task:\nfix the parser" in task
    assert "action edit_file path=src/parser.py" in task
    assert "changed_paths=src/parser.py" in task
    assert "Prior work report:" in task
    assert "Modified Files:\n  src/parser.py" in task
    assert "New user instruction for this resume:\nrun focused tests" in task


def test_format_run_detail_summarizes_steps(tmp_path: Path) -> None:
    storage = AgentStorage(tmp_path / "agent.db")
    run_id = storage.create_run("run tests", "fake-model", tmp_path)
    storage.add_step(run_id, "assistant", {"type": "run_shell", "command": "uv run pytest"})
    storage.add_step(
        run_id,
        "tool",
        {
            "type": "tool_result",
            "ok": False,
            "output": "failed tests",
            "verification_result": {
                "purpose": "test",
                "command": "uv run pytest",
                "status": "failed",
            },
        },
    )
    run = storage.get_run(run_id)
    assert run is not None

    storage.save_work_report(
        run_id,
        "Current Task:\n  run tests\nValidation Status:\n  - test `uv run pytest`: failed",
        {"type": "work_report"},
    )

    detail = format_run_detail(run, storage.run_steps_payloads(run_id), storage.get_work_report(run_id))

    assert f"Run {run_id}" in detail
    assert "Task: run tests" in detail
    assert "Work Report:" in detail
    assert "Validation Status:" in detail
    assert "action run_shell command=uv run pytest" in detail
    assert "verification=test `uv run pytest` failed" in detail


def test_run_detail_and_resume_context_include_plan_updates(tmp_path: Path) -> None:
    storage = AgentStorage(tmp_path / "agent.db")
    run_id = storage.create_run("build planner", "fake-model", tmp_path)
    storage.add_step(
        run_id,
        "assistant",
        {
            "type": "update_plan",
            "steps": [
                {"step": "Inspect docs", "status": "completed"},
                {"step": "Add durable plan action", "status": "in_progress"},
            ],
            "target_files": ["src/code_agent/resume.py"],
            "owned_files": ["src/code_agent/resume.py"],
            "checks": ["uv run pytest tests/test_resume.py"],
            "blockers": ["none"],
            "risk_notes": ["preserve compact history"],
        },
    )
    storage.add_step(
        run_id,
        "tool",
        {
            "type": "plan_updated",
            "ok": True,
            "steps": [
                {"step": "Inspect docs", "status": "completed"},
                {"step": "Add durable plan action", "status": "in_progress"},
            ],
            "target_files": ["src/code_agent/resume.py"],
            "owned_files": ["src/code_agent/resume.py"],
            "checks": ["uv run pytest tests/test_resume.py"],
            "blockers": ["none"],
            "risk_notes": ["preserve compact history"],
            "output": "Plan updated",
        },
    )
    run = storage.get_run(run_id)
    assert run is not None
    steps = storage.run_steps_payloads(run_id)

    detail = format_run_detail(run, steps)
    resume_task = build_resume_task(run, steps, "continue")

    assert "action update_plan completed:Inspect docs; in_progress:Add durable plan action" in detail
    assert "targets=src/code_agent/resume.py" in detail
    assert "checks=uv run pytest tests/test_resume.py" in detail
    assert "risks=preserve compact history" in detail
    assert "plan updated completed:Inspect docs; in_progress:Add durable plan action" in detail
    assert "plan updated completed:Inspect docs" in resume_task
    assert "owned=src/code_agent/resume.py" in resume_task
    assert "blockers=none" in resume_task


def test_compact_run_context_truncates_large_history() -> None:
    steps = [
        {
            "id": 1,
            "created_at": "now",
            "role": "tool",
            "payload": {"type": "tool_result", "ok": True, "output": "x" * 2000},
        }
    ]

    output = compact_run_context(steps, max_chars=1000)

    assert "<truncated" in output


def test_storage_redacts_secrets_before_persistence(tmp_path: Path) -> None:
    storage = AgentStorage(tmp_path / "agent.db")
    run_id = storage.create_run(
        task="debug token=super-secret-value",
        model="test",
        cwd=tmp_path,
    )
    storage.add_step(
        run_id,
        "tool",
        {"output": "Authorization: Bearer super-secret-value", "nested": ["password=hunter22"]},
    )

    run = storage.get_run(run_id)
    steps = storage.run_steps_payloads(run_id)

    assert run is not None
    assert "super-secret-value" not in run["task"]
    assert "super-secret-value" not in str(steps)
    assert "hunter22" not in str(steps)
    assert "[REDACTED]" in str(steps)


def test_storage_delete_and_prune_remove_related_run_data(tmp_path: Path) -> None:
    storage = AgentStorage(tmp_path / "agent.db")
    run_ids = [storage.create_run(f"task {index}", "test", tmp_path) for index in range(3)]
    for run_id in run_ids:
        storage.add_step(run_id, "tool", {"output": "ok"})
        storage.add_model_usage(run_id, {"model": "test", "ok": True})
        storage.save_work_report(run_id, "report", {"ok": True})

    assert storage.delete_run(run_ids[0]) is True
    assert storage.delete_run(run_ids[0]) is False
    assert storage.get_run(run_ids[0]) is None
    assert storage.run_steps(run_ids[0]) == []
    assert storage.model_usage(run_ids[0]) == []
    assert storage.get_work_report(run_ids[0]) is None

    assert storage.prune_runs(keep_last=1) == 1
    assert storage.get_run(run_ids[1]) is None
    assert storage.get_run(run_ids[2]) is not None
