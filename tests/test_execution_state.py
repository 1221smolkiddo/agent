from __future__ import annotations

import pytest
from pydantic import ValidationError

from code_agent.execution_state import ExecutionPhase, ExecutionState, compact_message_history
from code_agent.schema import FinalAction, ReadFileAction, ToolResult, UpdatePlanAction, WriteFileAction


def test_execution_state_blocks_repeated_identical_outcomes() -> None:
    state = ExecutionState(task="inspect app.py", max_steps=10)
    action = ReadFileAction(type="read_file", path="app.py")
    result = ToolResult(ok=False, output="missing file")

    state.record_action(action, result, [])
    state.record_action(action, result, [])

    assert "Blocked repeated action loop" in str(state.repeated_action_detail(action))
    assert state.phase is ExecutionPhase.RECOVER
    assert state.failed_hypotheses == ["read_file: missing file"]


def test_workspace_change_resets_action_loop_generation() -> None:
    state = ExecutionState(task="update app.py", max_steps=10)
    action = ReadFileAction(type="read_file", path="app.py")
    result = ToolResult(ok=True, output="old")
    state.record_action(action, result, [])
    state.record_action(action, result, [])
    assert state.repeated_action_detail(action) is not None

    state.record_action(action, ToolResult(ok=True, output="changed"), ["app.py"])

    assert state.repeated_action_detail(action) is None
    assert state.workspace_generation == 1


def test_plan_updates_durable_acceptance_criteria() -> None:
    state = ExecutionState(task="fix parser", max_steps=10)
    action = UpdatePlanAction(
        type="update_plan",
        steps=[{"step": "Fix parser", "status": "in_progress"}],
        checks=["pytest tests/test_parser.py"],
    )

    state.update_plan(action)

    assert state.snapshot()["phase"] == "plan"
    assert state.snapshot()["acceptance_criteria"] == ["pytest tests/test_parser.py"]


def test_context_compaction_preserves_task_and_recent_evidence() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "original task"},
    ]
    for index in range(20):
        messages.extend(
            [
                {"role": "assistant", "content": '{"type":"read_file","path":"app.py"}'},
                {
                    "role": "user",
                    "content": (
                        '{"type":"tool_result","ok":false,"output":"failure '
                        + ("x" * 700)
                        + '"}'
                    ),
                },
            ]
        )

    compacted, omitted = compact_message_history(messages, max_chars=8_000)

    assert omitted > 0
    assert compacted[0]["content"] == "system"
    assert compacted[1]["content"] == "original task"
    assert "execution-history checkpoint" in compacted[2]["content"]
    assert compacted[-1] == messages[-1]


def test_finalization_blocks_unfinished_plan_and_unmet_checks() -> None:
    state = ExecutionState(task="fix parser", max_steps=10)
    state.update_plan(
        UpdatePlanAction(
            type="update_plan",
            steps=[{"step": "Fix parser", "status": "in_progress"}],
            checks=["pytest tests/test_parser.py"],
        )
    )

    blocker = state.finalization_blocker(claims_success=True, verification_results=[])
    assert "unfinished steps" in str(blocker)

    state.update_plan(
        UpdatePlanAction(
            type="update_plan",
            steps=[{"step": "Fix parser", "status": "completed"}],
            checks=["pytest tests/test_parser.py"],
        )
    )
    blocker = state.finalization_blocker(claims_success=True, verification_results=[])
    assert "planned checks" in str(blocker)

    blocker = state.finalization_blocker(
        claims_success=True,
        verification_results=[
            {"command": "python -m pytest tests/test_parser.py", "ok": True}
        ],
    )
    assert blocker is None


def test_verification_confidence_tracks_latest_purposes() -> None:
    state = ExecutionState(task="fix parser", max_steps=10)
    state.record_verification(
        [
            {"purpose": "test", "command": "pytest", "ok": True, "status": "passed"},
            {"purpose": "lint", "command": "ruff check .", "ok": True, "status": "passed"},
        ]
    )

    assert state.snapshot()["verification_confidence"] == "high"

    state.record_verification(
        [{"purpose": "test", "command": "pytest", "ok": False, "status": "failed"}]
    )

    assert state.snapshot()["verification_confidence"] == "failed"


def test_hierarchical_plan_validates_dependencies_and_acceptance() -> None:
    action = UpdatePlanAction(
        type="update_plan",
        steps=[
            {
                "id": "inspect",
                "step": "Inspect parser",
                "status": "completed",
                "acceptance_criteria": ["evidence: parser symbols inspected"],
            },
            {
                "id": "patch",
                "parent_id": "inspect",
                "depends_on": ["inspect"],
                "step": "Patch parser",
                "status": "in_progress",
                "target_files": ["src/parser.py"],
                "acceptance_criteria": ["pytest tests/test_parser.py"],
            },
        ],
        hypotheses=[
            {
                "id": "root-cause",
                "statement": "The parser drops escaped delimiters.",
                "status": "testing",
                "confidence": "medium",
            }
        ],
    )
    state = ExecutionState(task="fix parser", max_steps=10)

    state.update_plan(action)
    snapshot = state.snapshot()

    assert snapshot["plan_revision"] == 1
    assert snapshot["plan_steps"][1]["depends_on"] == ["inspect"]
    assert snapshot["hypotheses"][0]["id"] == "root-cause"
    assert "pytest tests/test_parser.py" in snapshot["acceptance_criteria"]


def test_hierarchical_plan_rejects_cycles_and_unmet_dependencies() -> None:
    with pytest.raises(ValidationError, match="cycle"):
        UpdatePlanAction(
            type="update_plan",
            steps=[
                {"id": "one", "step": "One", "status": "pending", "depends_on": ["two"]},
                {"id": "two", "step": "Two", "status": "pending", "depends_on": ["one"]},
            ],
        )
    with pytest.raises(ValidationError, match="unmet dependencies"):
        UpdatePlanAction(
            type="update_plan",
            steps=[
                {"id": "inspect", "step": "Inspect", "status": "pending"},
                {
                    "id": "patch",
                    "step": "Patch",
                    "status": "in_progress",
                    "depends_on": ["inspect"],
                },
            ],
        )


def test_failed_planned_verification_requires_replan_and_resume_preserves_it() -> None:
    state = ExecutionState(task="fix parser", max_steps=10)
    state.update_plan(
        UpdatePlanAction(
            type="update_plan",
            steps=[{"id": "verify", "step": "Verify", "status": "in_progress"}],
        )
    )
    state.record_verification(
        [{"purpose": "test", "command": "pytest", "ok": False, "status": "failed"}]
    )

    assert "must be revised" in str(
        state.replan_blocker(FinalAction(type="final", message="done"))
    )
    resumed = ExecutionState.from_snapshot(
        state.snapshot(), task="fix parser", max_steps=8, resumed_from_run_id=42
    )
    assert resumed.replan_required is True
    assert resumed.resume_count == 1
    assert resumed.resumed_from_run_id == 42
    assert resumed.snapshot()["confidence"]["band"] == "low"


def test_failure_recovery_instruction_triggers_replan() -> None:
    state = ExecutionState(task="test recovery", max_steps=10)
    action = ReadFileAction(type="read_file", path="app.py")
    result = ToolResult(ok=False, output="No such file or directory")
    
    # First failure -> record action and check instruction
    state.record_action(action, result, [])
    instruction = state.failure_recovery_instruction(action, result)
    assert instruction is not None
    assert "target file is missing" in instruction.lower()
    
    # Second identical failure -> record action and check replan trigger
    state.record_action(action, result, [])
    instruction2 = state.failure_recovery_instruction(action, result)
    assert instruction2 is not None
    assert "plan must be revised" in instruction2.lower()
    
    # Should flag state for replan
    assert state.replan_required is True


def test_low_confidence_blocker() -> None:
    state = ExecutionState(task="test block", max_steps=10)
    # Trigger low confidence by forcing replan required
    state.require_replan("failed hypothesis")
    assert state._confidence()["score"] < 0.30
    
    # Discovery allowed
    read_action = ReadFileAction(type="read_file", path="app.py")
    assert state.low_confidence_blocker(read_action) is None
    
    # Mutation blocked
    write_action = WriteFileAction(type="write_file", path="app.py", content="x")
    blocker = state.low_confidence_blocker(write_action)
    assert blocker is not None
    assert "confidence is low" in blocker.lower()


def test_proactive_budget_check() -> None:
    state = ExecutionState(task="test budget", max_steps=10)
    
    # Below warning threshold
    assert state.proactive_budget_check(1000, 10000) is None
    
    # Above warning threshold (>= 80%)
    warning = state.proactive_budget_check(8500, 10000)
    assert warning is not None
    assert "budget is at 85%" in warning
