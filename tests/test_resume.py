from pathlib import Path

from code_agent.resume import build_resume_task, compact_run_context, format_run_detail
from code_agent.storage import AgentStorage


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

    task = build_resume_task(run, storage.run_steps_payloads(run_id), "run focused tests")

    assert f"Resume Agent47 run {run_id}." in task
    assert "Original task:\nfix the parser" in task
    assert "action edit_file path=src/parser.py" in task
    assert "changed_paths=src/parser.py" in task
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

    detail = format_run_detail(run, storage.run_steps_payloads(run_id))

    assert f"Run {run_id}" in detail
    assert "Task: run tests" in detail
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
            "output": "Plan updated",
        },
    )
    run = storage.get_run(run_id)
    assert run is not None
    steps = storage.run_steps_payloads(run_id)

    detail = format_run_detail(run, steps)
    resume_task = build_resume_task(run, steps, "continue")

    assert "action update_plan completed:Inspect docs; in_progress:Add durable plan action" in detail
    assert "plan updated completed:Inspect docs; in_progress:Add durable plan action" in detail
    assert "plan updated completed:Inspect docs" in resume_task


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
