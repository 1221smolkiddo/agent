from code_agent.failure_types import RunDisposition
from test_agent_recovery import FakeModel, RecoveringTools, make_agent, make_real_tool_agent


def test_user_cancellation_prevents_next_model_and_tool_call(tmp_path):
    model = FakeModel(['{"type":"write_file","path":"x.txt","content":"x"}'])
    tools = RecoveringTools(tmp_path)
    agent = make_agent(tmp_path, model, tools)
    agent.cancel()
    result = agent.run_detailed("write x.txt")
    assert result.disposition == RunDisposition.TERMINAL
    assert tools.calls == 0
    assert not model.messages_seen


def test_cancellation_during_model_call_prevents_action(tmp_path):
    class Model(FakeModel):
        def complete(self, messages):
            agent.cancel()
            return super().complete(messages)
    model = Model(['{"type":"write_file","path":"x.txt","content":"x"}'])
    tools = RecoveringTools(tmp_path)
    agent = make_agent(tmp_path, model, tools)
    result = agent.run_detailed("write x.txt")
    assert result.disposition == RunDisposition.TERMINAL
    assert tools.calls == 0
    assert not (tmp_path / "x.txt").exists()


def test_explicit_step_limit_does_not_claim_completion_from_a_write(tmp_path):
    model = FakeModel(['{"type":"write_file","path":"x.txt","content":"x"}'])
    agent = make_agent(tmp_path, model, RecoveringTools(tmp_path))
    agent.max_steps = 1
    result = agent.run_detailed("write x.txt and inspect the project")
    assert result.disposition == RunDisposition.WAITING
    assert result.changed_paths == ["x.txt"]
    assert "limit of 1 steps" in result.message
    assert result.execution_state["phase"] == "waiting"


def test_real_approval_denial_is_not_bypassed_by_recovery(tmp_path):
    model = FakeModel([
        '{"type":"write_file","path":"x.txt","content":"x"}',
        '{"type":"final","message":"Created x.txt"}',
        '{"type":"final","message":"Permission is required to create the file."}',
    ])
    agent = make_real_tool_agent(tmp_path, model)
    agent.tools.approval_callback = lambda *_: False
    result = agent.run_detailed("create x.txt in this project")
    assert not (tmp_path / "x.txt").exists()
    assert result.disposition == RunDisposition.WAITING
    assert any(item.get("type") == "false_completion" for item in result.failed_actions)
