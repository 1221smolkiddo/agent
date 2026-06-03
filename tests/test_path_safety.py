from pathlib import Path

import pytest

from code_agent.tools import ToolRegistry


def test_resolve_inside_workspace_allows_child(tmp_path: Path) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=True)

    resolved = tools.resolve_inside_workspace("src/example.py")

    assert tmp_path in resolved.parents


def test_resolve_inside_workspace_rejects_parent_escape(tmp_path: Path) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=True)

    with pytest.raises(ValueError):
        tools.resolve_inside_workspace("../outside.py")
