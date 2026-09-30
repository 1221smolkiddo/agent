from code_agent.schema import (
    ApplyPatchAction,
    DeleteFileAction,
    DependencyGraphAction,
    DetectVerificationAction,
    EditFileAction,
    RankContextAction,
    RepoMapAction,
    RunShellAction,
    SearchAction,
    SuggestVerificationAction,
    SymbolIndexAction,
    UpdatePlanAction,
    WebSearchAction,
)
from code_agent.status import _semantic_stage
from code_agent.interactive import (
    is_casual_greeting,
    is_chat_request,
    should_use_lightweight_chat,
    task_with_transcript,
)
from code_agent.session import SessionState


def test_semantic_stage_for_editing() -> None:
    stage, detail = _semantic_stage(
        EditFileAction(type="edit_file", path="src/app.py", find="old", replace="new")
    )
    assert stage == "Editing Files"
    assert "src/app.py" in detail


def test_semantic_stage_for_apply_patch() -> None:
    stage, detail = _semantic_stage(ApplyPatchAction(type="apply_patch", patch="diff"))
    assert stage == "Editing Files"


def test_semantic_stage_for_delete_file() -> None:
    stage, detail = _semantic_stage(DeleteFileAction(type="delete_file", path="hello_world.py"))
    assert stage == "Editing Files"
    assert "hello_world.py" in detail


def test_semantic_stage_for_project_search() -> None:
    stage, detail = _semantic_stage(SearchAction(type="search", query="TODO"))
    assert stage == "Inspecting Project"
    assert detail == "Inspected project"


def test_semantic_stage_for_web_search() -> None:
    stage, detail = _semantic_stage(WebSearchAction(type="web_search", query="OpenRouter docs"))
    assert stage == "Understanding Request"
    assert detail == "Web search"


def test_semantic_stage_for_detect_verification() -> None:
    stage, detail = _semantic_stage(DetectVerificationAction(type="detect_verification"))
    assert stage == "Inspecting Project"


def test_semantic_stage_for_suggest_verification() -> None:
    stage, detail = _semantic_stage(
        SuggestVerificationAction(type="suggest_verification", changed_paths=["src/app.py"])
    )
    assert stage == "Inspecting Project"


def test_semantic_stage_for_update_plan() -> None:
    stage, detail = _semantic_stage(
        UpdatePlanAction(
            type="update_plan",
            steps=[{"step": "Inspect docs", "status": "in_progress"}],
        )
    )
    assert stage == "Planning Changes"


def test_semantic_stage_for_repo_map() -> None:
    stage, detail = _semantic_stage(RepoMapAction(type="repo_map"))
    assert stage == "Inspecting Project"


def test_semantic_stage_for_rank_context() -> None:
    stage, detail = _semantic_stage(RankContextAction(type="rank_context", task="fix cli"))
    assert stage == "Inspecting Project"


def test_semantic_stage_for_symbol_index() -> None:
    stage, detail = _semantic_stage(SymbolIndexAction(type="symbol_index"))
    assert stage == "Inspecting Project"


def test_semantic_stage_for_dependency_graph() -> None:
    stage, detail = _semantic_stage(DependencyGraphAction(type="dependency_graph"))
    assert stage == "Inspecting Project"


def test_semantic_stage_for_build() -> None:
    stage, detail = _semantic_stage(RunShellAction(type="run_shell", command="npm run build"))
    assert stage == "Applying Fixes"


def test_semantic_stage_for_test() -> None:
    stage, detail = _semantic_stage(RunShellAction(type="run_shell", command="uv run pytest"))
    assert stage == "Running Verification"


def test_semantic_stage_for_generic_shell() -> None:
    action = RunShellAction(type="run_shell", command="git status")
    stage, detail = _semantic_stage(action)
    assert stage == "Applying Fixes"


def test_is_casual_greeting() -> None:
    assert is_casual_greeting("hey")
    assert is_casual_greeting(" Hello ")
    assert is_casual_greeting("hii")
    assert not is_casual_greeting("hey inspect this project")
    assert is_chat_request("hey")
    assert is_chat_request(" Hello ")
    assert is_chat_request("hii")
    assert is_chat_request("so how far away are we to make this an industry agent")
    assert not is_chat_request("hey inspect this project")


def test_contextual_repo_followup_uses_workspace_route() -> None:
    state = SessionState(current_task="inspect the repository", last_run_id=76)
    transcript = [
        (
            "inspect the repository",
            "I inspected the project and summarized its current state.",
        )
    ]

    followup = "how much work is left before this is production ready"
    assert is_chat_request(followup)
    assert not should_use_lightweight_chat(
        followup,
        transcript,
        state,
    )


def test_contextual_routing_keeps_greetings_lightweight() -> None:
    state = SessionState(current_task="read the readme and summarise it", last_run_id=76)

    assert should_use_lightweight_chat("hey", [], state)


def test_unrelated_chat_question_stays_lightweight_after_workspace_turn() -> None:
    state = SessionState(current_task="read the readme and summarise it", last_run_id=76)

    assert should_use_lightweight_chat("what is recursion", [], state)


def test_task_with_transcript_includes_recent_turns() -> None:
    task = task_with_transcript(
        "summarize your responses",
        [("hi", "hello"), ("explain recursion", "Recursion is a function calling itself.")],
    )
    assert "Recent interactive transcript for reference:" in task
    assert "Turn 1 user: hi" in task
    assert "Turn 2 Agent47: Recursion is a function calling itself." in task


def test_task_with_transcript_includes_context_for_normal_followup() -> None:
    task = task_with_transcript(
        "the names are sm and sv",
        [
            (
                "create a contributors file for this project with the names i give",
                "I need the contributor names before creating CONTRIBUTORS.md.",
            )
        ],
    )
    assert task.startswith("the names are sm and sv")
    assert "Recent interactive transcript for reference:" in task
    assert "create a contributors file" in task


def test_task_with_transcript_returns_user_input_without_transcript() -> None:
    assert task_with_transcript("inspect this project", []) == "inspect this project"


def test_normal_command_summary_hides_internal_action_names_and_secrets() -> None:
    from code_agent.status import StatusReporter

    assert StatusReporter._command_summary(
        "failed_strategy: read_file {\"type\": \"read_file\"} API_KEY=private-value",
        False,
    ) == "Command failed; details are in history"
    assert StatusReporter._command_summary("3 passed", True) == "3 passed"


def test_semantic_stage_does_not_show_protocol_identifiers() -> None:
    action = RepoMapAction(type="repo_map")
    stage, detail = _semantic_stage(action)
    assert stage == "Inspecting Project"
    assert "repo_map" not in detail


def test_debug_diagnostics_keep_detail_and_redact_secrets(monkeypatch):
    import io
    from rich.console import Console
    import code_agent.status as status
    from code_agent.schema import ToolResult

    output = io.StringIO()
    monkeypatch.setattr(status, "console", Console(file=output, width=160))
    action = RunShellAction(type="run_shell", command="pytest tests/routing.py")
    result = ToolResult(ok=False, metadata={"diagnostics": {
        "summary": "read_file failed API_KEY=private-value",
        "counts": {"error": 1},
        "diagnostics": [{"path": "src/routing.py", "message": "failed_strategy API_KEY=private-value"}],
    }})
    reporter = status.StatusReporter()
    monkeypatch.delenv("AGENT47_DEBUG", raising=False)
    reporter.tool_result(action, result, elapsed_ms=10)
    normal = output.getvalue()
    assert "Verification issues" in normal
    assert "read_file" not in normal and "failed_strategy" not in normal
    monkeypatch.setenv("AGENT47_DEBUG", "1")
    reporter.tool_result(action, result, elapsed_ms=10)
    debug = output.getvalue()
    assert "read_file" in debug and "src/routing.py" in debug
    assert "failed_strategy" in debug
    assert "private-value" not in debug
