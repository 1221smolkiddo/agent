from __future__ import annotations

import json

import pytest

from code_agent.agent import CodingAgent
from code_agent.durable_execution import DurableExecutionRuntime, ExecutionStatus
from code_agent.execution_state import ExecutionState, compact_message_history
from code_agent.failure_types import RunDisposition
from code_agent.schema import ReadFileAction, ToolResult, UpdatePlanAction
from code_agent.storage import AgentStorage
from test_agent_recovery import FakeModel, RecoveringTools, make_agent


PLAN = json.dumps({"type": "update_plan", "steps": [
    {"step": "Use a different approach", "status": "completed"}
]})
FINAL = '{"type":"final","message":"inspection complete"}'


@pytest.mark.parametrize("action", [
    '{"type":"read_file","path":"app.py"}',
    '{"type":"list_files","path":"."}',
    '{"type":"search","query":"function"}',
])
def test_successful_repeated_discovery_is_not_a_failure(tmp_path, action):
    model = FakeModel([action] * 6 + [FINAL])
    tools = RecoveringTools(tmp_path)
    tools.results[json.loads(action)["type"]] = ToolResult(ok=True, output="known observation")
    agent = make_agent(tmp_path, model, tools)
    agent.max_steps = 10
    agent.max_failures = 1
    result = agent.run_detailed("inspect this project")
    assert not result.blocked
    assert tools.calls == 2
    assert result.failed_actions == []
    assert result.execution_state["recovery_counts"].get("executable", 0) == 0
    assert "observation_already_known" in model.messages_seen[3][-1]["content"]


def test_two_failed_strategies_are_blocked_then_different_strategy_succeeds(tmp_path):
    actions = []
    for path in ("first.py", "second.py"):
        actions.extend([json.dumps({"type": "read_file", "path": path})] * 3)
        actions.append(PLAN)
    actions += ['{"type":"list_files","path":"."}', FINAL]
    model = FakeModel(actions)
    tools = RecoveringTools(tmp_path)
    agent = make_agent(tmp_path, model, tools)
    agent.max_steps = 12
    agent.max_failures = 1
    result = agent.run_detailed("inspect missing project files")
    assert not result.blocked
    assert tools.calls == 5
    assert sum(item.get("type") == "action_loop" for item in result.failed_actions) == 2
    assert result.execution_state["recovery_counts"]["executable"] == 4
    assert result.execution_state["recovery_counts"]["guard"] == 2
    assert result.execution_state["failure_streaks"]["executable"] == 0


def test_failed_executable_requires_material_change_even_after_replan(tmp_path):
    old = '{"type":"run_shell","command":"build --old"}'
    new = '{"type":"run_shell","command":"build --new"}'

    class Tools(RecoveringTools):
        commands = []
        def run(self, action):
            self.commands.append(action.command)
            return ToolResult(ok=action.command.endswith("--new"), output="unsupported option")

    tools = Tools(tmp_path)
    model = FakeModel([old, old, PLAN, old, PLAN, new, FINAL])
    agent = make_agent(tmp_path, model, tools)
    agent.max_steps = 10
    agent.max_failures = 1
    result = agent.run_detailed("run project build")
    assert tools.commands == ["build --old", "build --old", "build --new"]
    assert not result.blocked
    assert result.execution_state["recovery_counts"]["executable"] == 2


def test_more_than_one_hundred_productive_actions(tmp_path):
    model = FakeModel([
        json.dumps({"type": "list_files", "path": f"src/{index}"})
        for index in range(110)
    ] + [FINAL])
    tools = RecoveringTools(tmp_path)
    agent = make_agent(tmp_path, model, tools)
    agent.max_steps = 120
    # Preserve pinned safety instructions under Session 2 token budgeting.
    agent.context_max_chars = None
    agent.context_window_tokens = 32000
    result = agent.run_detailed("inspect this project")
    assert not result.blocked
    assert tools.calls == 110
    assert result.execution_state["action_count"] == 110
    assert result.execution_state["compacted_messages"] > 0


def test_failed_strategy_survives_serialization_compaction_and_restart(tmp_path):
    state = ExecutionState(task="inspect project", max_steps=10)
    action = ReadFileAction(type="read_file", path="missing.py")
    for _ in range(2):
        state.record_action(action, ToolResult(ok=False, output="missing"), [])
    state.failure_recovery_instruction(action, ToolResult(ok=False, output="missing"))
    snapshot = json.loads(json.dumps(state.snapshot()))
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "task"}]
    messages.extend({"role": "user", "content": "x" * 1000} for _ in range(20))
    _, omitted = compact_message_history(messages, max_chars=8000)
    assert omitted > 0
    model = FakeModel([PLAN, action.model_dump_json(), PLAN,
                       '{"type":"list_files","path":"."}', FINAL])
    tools = RecoveringTools(tmp_path)
    agent = make_agent(tmp_path, model, tools)
    agent.execution_state_snapshot = snapshot
    result = agent.run_detailed("inspect this project")
    assert not result.blocked
    assert tools.calls == 1
    assert "missing.py" in json.dumps(model.messages_seen[0])
    assert any(item.get("type") == "failed_strategy" for item in result.failed_actions)
    assert result.execution_state["failure_fingerprints"]


def test_protocol_and_guard_events_do_not_consume_executable_budget(tmp_path):
    model = FakeModel(["invalid"] * 5 + [
        '{"type":"final","message":"Created app.py"}',
        '{"type":"final","message":"Created app.py"}',
        '{"type":"list_files","path":"."}', FINAL,
    ])
    agent = make_agent(tmp_path, model, RecoveringTools(tmp_path))
    agent.max_steps = 12
    agent.max_failures = 1
    result = agent.run_detailed("inspect this project")
    assert not result.blocked
    counts = result.execution_state["recovery_counts"]
    assert counts["protocol"] == 5
    assert counts["guard"] == 2
    assert counts.get("executable", 0) == 0


def test_unresponsive_planner_pauses_instead_of_hot_looping(tmp_path):
    model = FakeModel(["invalid"] * 100)
    agent = make_agent(tmp_path, model, RecoveringTools(tmp_path))
    agent.max_steps = 100
    result = agent.run_detailed("inspect project")
    assert len(model.messages_seen) == 24
    assert result.disposition == RunDisposition.WAITING
    assert result.execution_state["phase"] == "waiting"
    assert "additional guidance" in result.message


def test_retryable_model_outage_recovers_with_same_task_and_plan(tmp_path):
    class Model(FakeModel):
        calls = 0
        def complete(self, messages):
            self.calls += 1
            if self.calls == 2:
                raise TimeoutError("service timed out")
            return super().complete(messages)

    model = Model([PLAN, FINAL])
    agent = make_agent(tmp_path, model, RecoveringTools(tmp_path))
    result = agent.run_detailed("inspect this project")
    assert not result.blocked
    assert model.calls == 3
    assert result.execution_state["recovery_counts"]["provider"] == 1
    assert result.execution_state["plan_steps"][0]["status"] == "completed"
    assert result.execution_state["failure_streaks"]["provider"] == 0


def test_model_outage_is_durably_paused_and_resumable(tmp_path):
    class Unavailable(FakeModel):
        calls = 0
        def complete(self, messages):
            self.calls += 1
            raise TimeoutError("service timed out")

    runtime = DurableExecutionRuntime(tmp_path / "execution.db")
    model = Unavailable([])
    agent = make_agent(tmp_path, model, RecoveringTools(tmp_path))
    agent.durable_runtime = runtime
    result = agent.run_detailed("inspect this project")
    assert model.calls == 2
    assert result.disposition == RunDisposition.WAITING
    assert result.execution_state["phase"] == "waiting"
    state = runtime.engine.state(result.durable_execution_id)
    assert state.status == ExecutionStatus.PAUSED
    assert all(task.state.value != "failed" for task in state.tasks.values())
    stored = next(item["payload"] for item in reversed(agent.storage.run_steps_payloads(result.run_id))
                  if item["payload"].get("type") == "execution_state")
    assert stored["disposition"] == "waiting"
    resumed = CodingAgent(
        tmp_path, False, 5, 1, FakeModel([FINAL]), RecoveringTools(tmp_path),
        AgentStorage(tmp_path / "agent.db"), durable_runtime=runtime,
        durable_execution_id=result.durable_execution_id,
        execution_state_snapshot=stored, resumed_from_run_id=result.run_id,
    ).run_detailed("inspect this project")
    assert resumed.disposition == RunDisposition.SUCCESS
    assert resumed.execution_state["resume_count"] == 1
    assert runtime.engine.state(result.durable_execution_id).status == ExecutionStatus.COMPLETE


def test_changed_patch_is_a_distinct_failure_fingerprint():
    from code_agent.schema import EditFileAction
    state = ExecutionState(task="fix", max_steps=10)
    first = EditFileAction(type="edit_file", path="app.py", find="old", replace="new")
    second = EditFileAction(type="edit_file", path="app.py", find="other", replace="new")
    for action in (first, second):
        failure = ToolResult(ok=False, output="missing exact text")
        state.record_action(action, failure, [])
        state.failure_recovery_instruction(action, failure)
    assert not state.replan_required
    assert len(state.snapshot()["failure_fingerprints"]) == 2


def test_replanning_resets_streak_but_does_not_erase_invalid_strategy():
    state = ExecutionState(task="inspect", max_steps=10)
    action = ReadFileAction(type="read_file", path="missing.py")
    for output in ("missing at 10:00", "missing at 10:01"):
        state.record_action(action, ToolResult(ok=False, output=output), [])
    state.update_plan(UpdatePlanAction.model_validate_json(PLAN))
    assert state.failure_streaks["executable"] == 0
    assert state.repeated_action_detail(action)


def test_reviewer_corrections_do_not_exhaust_task_failure_budget(tmp_path):
    from unittest.mock import patch
    model = FakeModel([FINAL] * 5)
    agent = make_agent(tmp_path, model, RecoveringTools(tmp_path))
    agent.max_failures = 1
    corrections = [{"type": "review_rejection", "output": "Address missing evidence"}] * 4 + [None]
    with patch.object(agent, "_review_final_answer", side_effect=corrections):
        result = agent.run_detailed("inspect project")
    assert not result.blocked
    assert len(model.messages_seen) == 5
    assert result.execution_state["recovery_counts"]["guard"] == 4
    assert result.execution_state["recovery_counts"].get("executable", 0) == 0


def test_changed_context_request_is_executed_after_automatic_preflight(tmp_path):
    from test_agent_recovery import make_real_tool_agent
    (tmp_path / "focused.py").write_text("def focused(): return 1\n", encoding="utf-8")
    model = FakeModel([
        '{"type":"rank_context","task":"different focused search","max_results":3}', FINAL,
    ])
    agent = make_real_tool_agent(tmp_path, model)
    result = agent.run_detailed("inspect this project")
    assert not result.blocked
    requested = [item for item in result.context_records if not item.get("automatic")]
    assert len(requested) == 1
    assert requested[0]["task"] == "different focused search"


def test_resumed_mutation_evidence_and_step_numbers_are_retained(tmp_path):
    agent = make_agent(tmp_path, FakeModel([
        '{"type":"write_file","path":"x.txt","content":"x"}',
    ]), RecoveringTools(tmp_path))
    agent.max_steps = 1
    paused = agent.run_detailed("create x.txt in this project")
    assert paused.disposition == RunDisposition.WAITING
    resumed = make_agent(tmp_path, FakeModel([
        '{"type":"final","message":"Created x.txt"}',
    ]), RecoveringTools(tmp_path))
    resumed.execution_state_snapshot = json.loads(json.dumps(paused.execution_state))
    result = resumed.run_detailed("create x.txt in this project")
    assert not result.blocked
    assert result.changed_paths == ["x.txt"]
    assert result.execution_state["step"] == 2
    assert result.mutation_records == paused.mutation_records
