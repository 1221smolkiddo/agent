from __future__ import annotations

from pathlib import Path

from code_agent.agent import CodingAgent
from code_agent.models import ChatMessage
from code_agent.schema import AgentAction, ReadFileAction, ToolResult
from code_agent.storage import AgentStorage


class FakeModel:
    model = "fake-model"

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.messages_seen: list[list[ChatMessage]] = []

    def complete(self, messages: list[ChatMessage]) -> str:
        self.messages_seen.append([message.copy() for message in messages])
        return self.responses.pop(0)


class RecoveringTools:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, action: AgentAction) -> ToolResult:
        self.calls += 1
        if isinstance(action, ReadFileAction):
            return ToolResult(ok=False, output="missing file")
        return ToolResult(ok=True, output="recovered")


def make_agent(tmp_path: Path, model: FakeModel, tools: RecoveringTools) -> CodingAgent:
    return CodingAgent(
        cwd=tmp_path,
        dry_run=False,
        max_steps=5,
        max_failures=3,
        model_client=model,
        tools=tools,  # type: ignore[arg-type]
        storage=AgentStorage(tmp_path / "agent.db"),
    )


def test_agent_recovers_after_failed_tool_result(tmp_path: Path) -> None:
    model = FakeModel(
        [
            '{"type":"read_file","path":"missing.txt"}',
            '{"type":"list_files"}',
            '{"type":"final","message":"done"}',
        ]
    )
    tools = RecoveringTools()
    agent = make_agent(tmp_path, model, tools)

    result = agent.run("recover from a failed read")

    assert result == "done"
    assert tools.calls == 2
    assert "recovery_instruction" in model.messages_seen[1][-1]["content"]


def test_agent_retries_after_invalid_model_action(tmp_path: Path) -> None:
    model = FakeModel(
        [
            "not json",
            '{"type":"list_files"}',
            '{"type":"final","message":"done"}',
        ]
    )
    tools = RecoveringTools()
    agent = make_agent(tmp_path, model, tools)

    result = agent.run("recover from invalid json")

    assert result == "done"
    assert tools.calls == 1
    assert "parse_failure" in model.messages_seen[1][-1]["content"]


def test_agent_can_finalize_after_dry_run_skip(tmp_path: Path) -> None:
    model = FakeModel(
        [
            '{"type":"write_file","path":"FEATURES.md","content":"features"}',
            '{"type":"final","message":"Enable write mode first."}',
        ]
    )

    class DryRunWriteTools(RecoveringTools):
        def run(self, action: AgentAction) -> ToolResult:
            self.calls += 1
            return ToolResult(ok=False, output="Dry-run mode skipped write_file.")

    dry_run_tools = DryRunWriteTools()
    agent = make_agent(tmp_path, model, dry_run_tools)

    result = agent.run("create a features file")

    assert result == "Enable write mode first."
    assert dry_run_tools.calls == 1


def test_agent_rejects_false_completion_after_blocked_write(tmp_path: Path) -> None:
    model = FakeModel(
        [
            '{"type":"write_file","path":"project_features.md","content":"features"}',
            '{"type":"final","message":"I created project_features.md and you can find it in the current directory."}',
            '{"type":"final","message":"I could not create the file because write mode is disabled. Use /write first."}',
        ]
    )

    class DryRunWriteTools(RecoveringTools):
        def run(self, action: AgentAction) -> ToolResult:
            self.calls += 1
            return ToolResult(ok=False, output="Dry-run mode skipped write_file.")

    dry_run_tools = DryRunWriteTools()
    agent = make_agent(tmp_path, model, dry_run_tools)

    result = agent.run("create a features file")

    assert result == "I could not create the file because write mode is disabled. Use /write first."
    assert dry_run_tools.calls == 1
    assert "false_completion" in model.messages_seen[2][-1]["content"]
