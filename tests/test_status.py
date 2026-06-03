from code_agent.schema import EditFileAction, RunShellAction, SearchAction, WebSearchAction
from code_agent.status import format_action_status, format_shell_status


def test_format_action_status_for_editing() -> None:
    status = format_action_status(
        EditFileAction(type="edit_file", path="src/app.py", find="old", replace="new")
    )

    assert status == "EDITING src/app.py"


def test_format_action_status_for_project_search() -> None:
    status = format_action_status(SearchAction(type="search", query="TODO"))

    assert status == "SEARCHING project for TODO"


def test_format_action_status_for_web_search() -> None:
    status = format_action_status(WebSearchAction(type="web_search", query="OpenRouter docs"))

    assert status == "SEARCHING WEB for OpenRouter docs"


def test_format_shell_status_for_install() -> None:
    assert format_shell_status("uv add rich") == "INSTALLING packages with uv add rich"


def test_format_shell_status_for_build() -> None:
    assert format_shell_status("npm run build") == "BUILDING with npm run build"


def test_format_shell_status_for_test() -> None:
    assert format_shell_status("uv run pytest") == "TESTING with uv run pytest"


def test_format_shell_status_for_generic_shell() -> None:
    action = RunShellAction(type="run_shell", command="git status")

    assert format_action_status(action) == "RUNNING shell command git status"
