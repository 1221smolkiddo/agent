from __future__ import annotations

from pathlib import Path

from code_agent.agent import CodingAgent
from code_agent.models import ChatMessage
from code_agent.schema import AgentAction, ReadFileAction, RunShellAction, ToolResult
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

    result = agent.run("recover from invalid json while inspecting this project")

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


def test_agent_rejects_created_claim_without_any_mutation(tmp_path: Path) -> None:
    model = FakeModel(
        [
            '{"type":"final","message":"I created notes.md with the requested content."}',
            '{"type":"final","message":"I did not create notes.md because no write action was run."}',
        ]
    )
    tools = RecoveringTools()
    agent = make_agent(tmp_path, model, tools)

    result = agent.run("create a notes.md file in this project")

    assert result == "I did not create notes.md because no write action was run."
    assert tools.calls == 0
    assert "no verified file mutation" in model.messages_seen[1][-1]["content"]


def test_agent_rejects_template_claim_after_failed_write(tmp_path: Path) -> None:
    model = FakeModel(
        [
            '{"type":"write_file","path":"project_notes.md","content":"# Project Notes"}',
            (
                '{"type":"final","message":"I created a new markdown file called '
                "'project_notes.md' in the project root directory. Here is the template." + '"}'
            ),
            '{"type":"final","message":"I could not create project_notes.md because the write failed."}',
        ]
    )

    class FailedWriteTools(RecoveringTools):
        def run(self, action: AgentAction) -> ToolResult:
            self.calls += 1
            return ToolResult(ok=False, output="Dry-run mode skipped write_file.")

    tools = FailedWriteTools()
    agent = make_agent(tmp_path, model, tools)

    result = agent.run("make an md file in the project")

    assert result == "I could not create project_notes.md because the write failed."
    assert tools.calls == 1
    assert "no mutation succeeded" in model.messages_seen[2][-1]["content"]


def test_agent_adds_verification_hint_after_successful_mutation(tmp_path: Path) -> None:
    model = FakeModel(
        [
            '{"type":"write_file","path":"src/app.py","content":"print(\\"hi\\")"}',
            '{"type":"final","message":"updated src/app.py; verification not run."}',
        ]
    )
    tools = RecoveringTools()
    agent = make_agent(tmp_path, model, tools)

    result = agent.run("update a file")

    assert result == "updated src/app.py; verification not run."
    tool_payload = model.messages_seen[1][-1]["content"]
    assert '"changed_paths": ["src/app.py"]' in tool_payload
    assert "No automatic verification command was selected" in tool_payload


def test_agent_runs_automatic_verification_after_successful_mutation(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
dependencies = ["pytest"]

[tool.pytest.ini_options]
testpaths = ["tests"]
""".strip(),
        encoding="utf-8",
    )
    model = FakeModel(
        [
            '{"type":"write_file","path":"src/app.py","content":"print(\\"hi\\")"}',
            '{"type":"final","message":"updated src/app.py"}',
        ]
    )

    class VerifyingTools(RecoveringTools):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []

        def run(self, action: AgentAction) -> ToolResult:
            self.calls += 1
            if isinstance(action, RunShellAction):
                self.commands.append(action.command)
                return ToolResult(ok=True, output="tests passed")
            return ToolResult(ok=True, output="changed")

    tools = VerifyingTools()
    agent = make_agent(tmp_path, model, tools)

    result = agent.run("update a Python file")

    assert result == (
        "updated src/app.py\n"
        "Verification outcomes:\n"
        "- test `uv run pytest`: passed."
    )
    assert tools.commands == ["uv run pytest"]
    tool_payload = model.messages_seen[1][-1]["content"]
    assert '"automatic_verification_results"' in tool_payload
    assert "Automatic focused verification passed" in tool_payload


def test_agent_recovers_after_failed_automatic_verification(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
dependencies = ["pytest"]

[tool.pytest.ini_options]
testpaths = ["tests"]
""".strip(),
        encoding="utf-8",
    )
    model = FakeModel(
        [
            '{"type":"write_file","path":"src/app.py","content":"print(\\"hi\\")"}',
            '{"type":"edit_file","path":"src/app.py","find":"hi","replace":"hello"}',
            '{"type":"final","message":"fixed src/app.py"}',
        ]
    )

    class FailingVerificationTools(RecoveringTools):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []

        def run(self, action: AgentAction) -> ToolResult:
            self.calls += 1
            if isinstance(action, RunShellAction):
                self.commands.append(action.command)
                ok = len(self.commands) > 1
                return ToolResult(ok=ok, output="passed" if ok else "failed tests")
            return ToolResult(ok=True, output="changed")

    tools = FailingVerificationTools()
    agent = make_agent(tmp_path, model, tools)

    result = agent.run("update and verify a Python file")

    assert result == (
        "fixed src/app.py\n"
        "Verification outcomes:\n"
        "- test `uv run pytest`: failed.\n"
        "- test `uv run pytest`: passed."
    )
    assert tools.commands == ["uv run pytest", "uv run pytest"]
    assert "Automatic verification failed" in model.messages_seen[1][-1]["content"]


def test_agent_appends_verification_outcomes_to_final_answer(tmp_path: Path) -> None:
    model = FakeModel(
        [
            '{"type":"run_shell","command":"uv run pytest"}',
            '{"type":"final","message":"Implemented the change."}',
        ]
    )
    tools = RecoveringTools()
    agent = make_agent(tmp_path, model, tools)

    result = agent.run("run tests")

    assert result == (
        "Implemented the change.\n"
        "Verification outcomes:\n"
        "- test `uv run pytest`: passed."
    )
    tool_payload = model.messages_seen[1][-1]["content"]
    assert '"verification_result"' in tool_payload
    assert '"purpose": "test"' in tool_payload


def test_agent_blocks_workspace_tools_for_non_workspace_question(tmp_path: Path) -> None:
    model = FakeModel(
        [
            '{"type":"list_files","path":"."}',
            '{"type":"final","message":"It is in the China Standard Time zone."}',
        ]
    )
    tools = RecoveringTools()
    agent = make_agent(tmp_path, model, tools)

    result = agent.run("what time is it in Chongqing?")

    assert result == "It is in the China Standard Time zone."
    assert tools.calls == 0
    assert "non_workspace_tool_blocked" in model.messages_seen[1][-1]["content"]


def test_agent_allows_workspace_tools_for_project_question(tmp_path: Path) -> None:
    model = FakeModel(
        [
            '{"type":"list_files","path":"."}',
            '{"type":"final","message":"checked the project"}',
        ]
    )
    tools = RecoveringTools()
    agent = make_agent(tmp_path, model, tools)

    result = agent.run("inspect this project")

    assert result == "checked the project"
    assert tools.calls == 1


def test_agent_accepts_plain_text_answer_for_non_workspace_question(tmp_path: Path) -> None:
    model = FakeModel(["Here is the general answer."])
    tools = RecoveringTools()
    agent = make_agent(tmp_path, model, tools)

    result = agent.run("explain this idea simply")

    assert result == "Here is the general answer."
    assert tools.calls == 0


def test_workspace_classifier_does_not_match_run_inside_turn() -> None:
    assert not CodingAgent._is_workspace_task("summarize this conversation\nTurn 1 Agent47: hi")
    assert CodingAgent._is_workspace_task("run the tests")


def test_workspace_classifier_detects_file_extension_request() -> None:
    assert CodingAgent._is_workspace_task("there is already a contributing.md name it something else")


def test_workspace_classifier_uses_transcript_for_contextual_followup() -> None:
    task = (
        "the names are sm and sv\n"
        "Recent interactive transcript for reference:\n"
        "Turn 1 user: create a contributors file for this project with the names i give\n"
        "Turn 1 Agent47: I need the contributor names before creating CONTRIBUTORS.md."
    )

    assert CodingAgent._is_workspace_task(task)


def test_workspace_classifier_ignores_transcript_for_unrelated_latest_question() -> None:
    task = (
        "what time is it in Chongqing?\n"
        "Recent interactive transcript for reference:\n"
        "Turn 1 user: inspect this project\n"
        "Turn 1 Agent47: checked the project"
    )

    assert not CodingAgent._is_workspace_task(task)
