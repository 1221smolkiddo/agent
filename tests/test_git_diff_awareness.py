from pathlib import Path
import subprocess

from code_agent.schema import InspectGitDiffAction
from code_agent.tools import ToolRegistry


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True, text=True)


def test_inspect_git_diff_requires_permission(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: False)

    result = tools.run(InspectGitDiffAction(type="inspect_git_diff"))

    assert not result.ok
    assert result.output == "Permission denied for inspect_git_diff."


def test_inspect_git_diff_reports_status_without_hunks_by_default(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    (tmp_path / "notes.md").write_text("draft\n", encoding="utf-8")
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: True)

    result = tools.run(InspectGitDiffAction(type="inspect_git_diff"))

    assert result.ok
    assert "Status:\n?? notes.md" in result.output
    assert "Unstaged changes:\n<clean>" in result.output
    assert "Staged changes:\n<clean>" in result.output
    assert "diff --git" not in result.output


def test_inspect_git_diff_can_include_staged_diff(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    source = tmp_path / "agent.py"
    source.write_text("print('agent47')\n", encoding="utf-8")
    _git(tmp_path, "add", "agent.py")
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: True)

    result = tools.run(InspectGitDiffAction(type="inspect_git_diff", include_diff=True))

    assert result.ok
    assert "Staged changes:\nA\tagent.py" in result.output
    assert "Staged diff:\ndiff --git" in result.output
    assert "+print('agent47')" in result.output
