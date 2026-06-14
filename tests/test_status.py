from code_agent.schema import (
    ApplyPatchAction,
    DetectVerificationAction,
    EditFileAction,
    RunShellAction,
    SearchAction,
    SuggestVerificationAction,
    WebSearchAction,
)
from code_agent.status import format_action_status, format_shell_status
from code_agent.interactive import is_casual_greeting, should_include_transcript, task_with_transcript


def test_format_action_status_for_editing() -> None:
    status = format_action_status(
        EditFileAction(type="edit_file", path="src/app.py", find="old", replace="new")
    )

    assert status == "EDITING src/app.py"


def test_format_action_status_for_apply_patch() -> None:
    status = format_action_status(ApplyPatchAction(type="apply_patch", patch="diff"))

    assert status == "EDITING applying patch"


def test_format_action_status_for_project_search() -> None:
    status = format_action_status(SearchAction(type="search", query="TODO"))

    assert status == "SEARCHING project for TODO"


def test_format_action_status_for_web_search() -> None:
    status = format_action_status(WebSearchAction(type="web_search", query="OpenRouter docs"))

    assert status == "SEARCHING WEB for OpenRouter docs"


def test_format_action_status_for_detect_verification() -> None:
    status = format_action_status(DetectVerificationAction(type="detect_verification"))

    assert status == "CHECKING project verification commands"


def test_format_action_status_for_suggest_verification() -> None:
    status = format_action_status(
        SuggestVerificationAction(type="suggest_verification", changed_paths=["src/app.py"])
    )

    assert status == "CHECKING suggested verification"


def test_format_shell_status_for_install() -> None:
    assert format_shell_status("uv add rich") == "INSTALLING packages with uv add rich"


def test_format_shell_status_for_build() -> None:
    assert format_shell_status("npm run build") == "BUILDING with npm run build"


def test_format_shell_status_for_test() -> None:
    assert format_shell_status("uv run pytest") == "TESTING with uv run pytest"


def test_format_shell_status_for_generic_shell() -> None:
    action = RunShellAction(type="run_shell", command="git status")

    assert format_action_status(action) == "RUNNING shell command git status"


def test_is_casual_greeting() -> None:
    assert is_casual_greeting("hey")
    assert is_casual_greeting(" Hello ")
    assert not is_casual_greeting("hey inspect this project")


def test_should_include_transcript_for_summary_request() -> None:
    assert should_include_transcript("summarise your responses into 2 lines")
    assert should_include_transcript("recap our conversation")
    assert should_include_transcript("the names are sm and sv")
    assert should_include_transcript("there is already a contributing.md name it something else")
    assert not should_include_transcript("inspect this project")


def test_task_with_transcript_includes_recent_turns() -> None:
    task = task_with_transcript(
        "summarize your responses",
        [("hi", "hello"), ("explain recursion", "Recursion is a function calling itself.")],
    )

    assert "Recent interactive transcript for reference:" in task
    assert "Turn 1 user: hi" in task
    assert "Turn 2 Agent47: Recursion is a function calling itself." in task


def test_task_with_transcript_includes_contextual_file_followup() -> None:
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
