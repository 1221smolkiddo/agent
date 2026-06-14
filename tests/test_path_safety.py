from pathlib import Path

import pytest

from code_agent.schema import DeleteFileAction
from code_agent.tools import ToolRegistry


def test_resolve_inside_workspace_allows_child(tmp_path: Path) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=True)

    resolved = tools.resolve_inside_workspace("src/example.py")

    assert tmp_path in resolved.parents


def test_resolve_inside_workspace_rejects_parent_escape(tmp_path: Path) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=True)

    with pytest.raises(ValueError):
        tools.resolve_inside_workspace("../outside.py")


def test_delete_file_rejects_parent_escape(tmp_path: Path) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=False, approval_callback=lambda _a, _d: True)

    result = tools.run(DeleteFileAction(type="delete_file", path="../outside.py"))

    assert not result.ok
    assert "Path escapes workspace" in result.output


def test_delete_file_refuses_directory(tmp_path: Path) -> None:
    (tmp_path / "folder").mkdir()
    tools = ToolRegistry(workspace=tmp_path, dry_run=False, approval_callback=lambda _a, _d: True)

    result = tools.run(DeleteFileAction(type="delete_file", path="folder"))

    assert not result.ok
    assert result.output == "Refusing to delete non-file path: folder"
    assert (tmp_path / "folder").exists()
