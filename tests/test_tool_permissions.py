from pathlib import Path

from code_agent.schema import ListFilesAction, ReadFileAction, SearchAction
from code_agent.tools import ToolRegistry


def test_read_file_requires_permission(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: False)

    result = tools.run(ReadFileAction(type="read_file", path="README.md"))

    assert not result.ok
    assert result.output == "Permission denied for read_file."


def test_list_files_requires_permission(tmp_path: Path) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: False)

    result = tools.run(ListFilesAction(type="list_files"))

    assert not result.ok
    assert result.output == "Permission denied for list_files."


def test_project_search_requires_permission(tmp_path: Path) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: False)

    result = tools.run(SearchAction(type="search", query="hello"))

    assert not result.ok
    assert result.output == "Permission denied for search."


def test_read_file_with_permission_succeeds(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: True)

    result = tools.run(ReadFileAction(type="read_file", path="README.md"))

    assert result.ok
    assert result.output == "hello"
