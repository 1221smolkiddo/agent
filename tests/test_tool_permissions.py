from pathlib import Path

import code_agent.tools as tools_module
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


def test_project_search_falls_back_when_ripgrep_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "README.md").write_text("hello agent47\n", encoding="utf-8")

    def missing_rg(*_args, **_kwargs):
        raise FileNotFoundError("[WinError 2] The system cannot find the file specified")

    monkeypatch.setattr(tools_module.subprocess, "run", missing_rg)
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: True)

    result = tools.run(SearchAction(type="search", query="agent47"))

    assert result.ok
    assert result.output == "README.md:1:hello agent47"


def test_project_search_fallback_ignores_local_secret_files(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / ".env").write_text("SECRET=agent47\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("agent47\n", encoding="utf-8")

    def missing_rg(*_args, **_kwargs):
        raise FileNotFoundError("[WinError 2] The system cannot find the file specified")

    monkeypatch.setattr(tools_module.subprocess, "run", missing_rg)
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: True)

    result = tools.run(SearchAction(type="search", query="agent47"))

    assert result.ok
    assert result.output == "README.md:1:agent47"
