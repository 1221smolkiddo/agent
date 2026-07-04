from pathlib import Path

from code_agent.agent import AgentRunResult, CodingAgent
from code_agent.interactive import task_with_context
from code_agent.models import ChatMessage
from code_agent.schema import AgentAction, ToolResult, WriteFileAction
from code_agent.session import SessionState, extract_file_refs
from code_agent.storage import AgentStorage


class FakeModel:
    model = "fake-model"

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.messages_seen: list[list[ChatMessage]] = []

    def complete(self, messages: list[ChatMessage]) -> str:
        self.messages_seen.append([message.copy() for message in messages])
        return self.responses.pop(0)


class FakeTools:
    def __init__(self, workspace: Path | None = None) -> None:
        self.actions: list[AgentAction] = []
        self.workspace = workspace

    def run(self, action: AgentAction) -> ToolResult:
        self.actions.append(action)
        if isinstance(action, WriteFileAction):
            target = self.resolve_inside_workspace(action.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(action.content, encoding="utf-8")
        return ToolResult(ok=True, output="changed")

    def resolve_inside_workspace(self, requested_path: str | None = None) -> Path:
        if self.workspace is None:
            raise ValueError("test workspace is not configured")
        target = (self.workspace / (requested_path or ".")).resolve()
        if target != self.workspace and self.workspace not in target.parents:
            raise ValueError(f"Path escapes workspace: {requested_path}")
        return target


def make_agent(tmp_path: Path, model: FakeModel, tools: FakeTools) -> CodingAgent:
    tools.workspace = tmp_path
    return CodingAgent(
        cwd=tmp_path,
        dry_run=False,
        max_steps=5,
        max_failures=3,
        model_client=model,
        tools=tools,  # type: ignore[arg-type]
        storage=AgentStorage(tmp_path / "agent.db"),
    )


def test_extract_file_refs() -> None:
    assert extract_file_refs("Created CONTRIBUTORS.md and src/app.py.") == [
        "CONTRIBUTORS.md",
        "src/app.py",
    ]


def test_session_state_tracks_pending_info_and_target_file() -> None:
    state = SessionState()

    state.update(
        "create a contributors file for this project with the names i give",
        AgentRunResult(
            message="I need the contributor names before creating CONTRIBUTORS.md.",
            run_id=1,
        ),
    )

    rendered = state.render()
    assert "current_task: create a contributors file" in rendered
    assert "pending_user_info: I need the contributor names" in rendered
    assert "target_files: CONTRIBUTORS.md" in rendered
    assert "previous_status: completed" in rendered


def test_session_state_tracks_created_files() -> None:
    state = SessionState()

    state.update(
        "the names are sm and sv",
        AgentRunResult(
            message="Created CONTRIBUTORS.md.",
            run_id=2,
            changed_paths=["CONTRIBUTORS.md"],
            mutation_records=[
                {
                    "action": "write_file",
                    "path": "CONTRIBUTORS.md",
                    "ok": True,
                    "output": "diff",
                }
            ],
        ),
    )

    assert state.pending_user_info is None
    assert state.last_created_files == ["CONTRIBUTORS.md"]
    assert "last_created_files: CONTRIBUTORS.md" in state.render()


def test_session_state_tracks_deleted_files() -> None:
    state = SessionState(last_created_files=["hello_world.py"], target_files=["hello_world.py"])

    state.update(
        "remove the file you just created",
        AgentRunResult(
            message="Removed hello_world.py.",
            run_id=3,
            changed_paths=["hello_world.py"],
            mutation_records=[
                {
                    "action": "delete_file",
                    "path": "hello_world.py",
                    "ok": True,
                    "output": "deleted",
                }
            ],
        ),
    )

    assert state.last_created_files == []
    assert state.last_deleted_files == ["hello_world.py"]
    assert "last_deleted_files: hello_world.py" in state.render()


def test_task_with_context_includes_session_state_and_transcript() -> None:
    state = SessionState(
        current_task="create a contributors file",
        pending_user_info="Need contributor names",
        target_files=["CONTRIBUTORS.md"],
        conversation_steering="Keep answers concise and implementation-first.",
    )

    task = task_with_context("the names are sm and sv", [("previous", "response")], state)

    assert task.startswith("the names are sm and sv")
    assert "Current interactive session state:" in task
    assert "pending_user_info: Need contributor names" in task
    assert "conversation_steering: Keep answers concise" in task
    assert "Recent interactive transcript for reference:" in task


def test_contributors_followup_flow_uses_session_state(tmp_path: Path) -> None:
    state = SessionState()
    first_model = FakeModel(
        ['{"type":"final","message":"I need the contributor names before creating CONTRIBUTORS.md."}']
    )
    first_agent = make_agent(tmp_path, first_model, FakeTools())

    first = first_agent.run_detailed("create a contributors file for this project with the names i give")
    state.update("create a contributors file for this project with the names i give", first)
    second_task = task_with_context("the names are sm and sv", [], state)

    second_model = FakeModel(
        [
            (
                '{"type":"write_file","path":"CONTRIBUTORS.md",'
                '"content":"# Contributors\\n\\n- sm: ★★★★\\n- sv: ★★★★\\n"}'
            ),
            '{"type":"final","message":"Created CONTRIBUTORS.md with sm and sv."}',
        ]
    )
    tools = FakeTools()
    second_agent = make_agent(tmp_path, second_model, tools)

    second = second_agent.run_detailed(second_task)
    state.update("the names are sm and sv", second)

    assert "current_task: create a contributors file" in second_task
    assert second.changed_paths == ["CONTRIBUTORS.md"]
    assert state.last_created_files == ["CONTRIBUTORS.md"]
