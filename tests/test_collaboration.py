from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from code_agent.cli import app
from code_agent.collaboration import (
    build_collaboration_context,
    build_commit_message,
    build_pr_summary,
    commit_changes,
    create_branch,
)
from code_agent.patches import git_style_unified_diff
from code_agent.storage import AgentStorage


def test_collaboration_context_uses_run_history_and_git_state(tmp_path: Path) -> None:
    workspace = _git_repo(tmp_path)
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("print('new')\n", encoding="utf-8")
    storage = AgentStorage(tmp_path / "agent.db")
    run_id = _record_agent_run(storage, workspace)

    context = build_collaboration_context(workspace, storage, run_id=run_id)
    message = build_commit_message(context)

    assert "feat(src): add collaboration workflow" in message
    assert "Patched src/app.py" in message
    assert "test `uv run pytest`: passed" in message
    assert context.snapshot.changed_files == ["src/app.py"]


def test_pr_summary_fills_local_template(tmp_path: Path) -> None:
    workspace = _git_repo(tmp_path)
    template_dir = workspace / ".github"
    template_dir.mkdir()
    template = "## What Changed\n\n## How I Tested\n\n## Notes / Follow-Up\n"
    storage = AgentStorage(tmp_path / "agent.db")
    run_id = _record_agent_run(storage, workspace)

    context = build_collaboration_context(workspace, storage, run_id=run_id)
    summary = build_pr_summary(context, template)

    assert "## What Changed" in summary
    assert "- Patched src/app.py" in summary
    assert "- test `uv run pytest`: passed" in summary
    assert "No follow-up risks recorded" in summary


def test_create_branch_preview_does_not_mutate_repo(tmp_path: Path) -> None:
    workspace = _git_repo(tmp_path)

    result = create_branch(workspace, "feature/collab", apply=False)
    branch = _git(workspace, "branch", "--show-current")

    assert result.ok
    assert "Preview" in result.output
    assert branch == "main"


def test_commit_changes_preview_and_commit_with_approval(tmp_path: Path) -> None:
    workspace = _git_repo(tmp_path)
    (workspace / "README.md").write_text("# Updated\n", encoding="utf-8")

    preview = commit_changes(workspace, "docs: update readme", commit=False)
    result = commit_changes(
        workspace,
        "docs: update readme",
        approval_callback=lambda _action, _detail: "y",
        commit=True,
    )
    log = _git(workspace, "log", "-1", "--pretty=%s")

    assert preview.ok
    assert "Preview" in preview.output
    assert result.ok
    assert result.committed
    assert log == "docs: update readme"


def test_cli_collab_commit_message_uses_run_context(tmp_path: Path) -> None:
    workspace = _git_repo(tmp_path)
    storage = AgentStorage(tmp_path / "agent.db")
    run_id = _record_agent_run(storage, workspace)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["collab", "commit-message", "--cwd", str(workspace), "--run-id", str(run_id)],
        env={"AGENT_DB_PATH": str(tmp_path / "agent.db")},
    )

    assert result.exit_code == 0, result.output
    assert "feat(src): add collaboration workflow" in result.output
    assert "Verification:" in result.output


def test_cli_collab_status_reports_git_files(tmp_path: Path) -> None:
    workspace = _git_repo(tmp_path)
    (workspace / "notes.md").write_text("notes\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, ["collab", "status", "--cwd", str(workspace)])

    assert result.exit_code == 0, result.output
    assert "Agent47 collaboration status:" in result.output
    assert "notes.md" in result.output


def _git_repo(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _run(workspace, "git", "init", "-b", "main")
    _run(workspace, "git", "config", "user.email", "test@example.com")
    _run(workspace, "git", "config", "user.name", "Test User")
    (workspace / "README.md").write_text("# Project\n", encoding="utf-8")
    _run(workspace, "git", "add", ".")
    _run(workspace, "git", "commit", "-m", "init")
    return workspace


def _record_agent_run(storage: AgentStorage, workspace: Path) -> int:
    run_id = storage.create_run("add collaboration workflow", "fake-model", workspace)
    storage.add_step(run_id, "assistant", {"type": "apply_patch", "patch": "forward"})
    storage.add_step(
        run_id,
        "tool",
        {
            "type": "tool_result",
            "ok": True,
            "changed_paths": ["src/app.py"],
            "mutation_records": [
                {
                    "action": "apply_patch",
                    "path": "src/app.py",
                    "ok": True,
                    "verified": True,
                    "output": "patched",
                    "inverse_patch": git_style_unified_diff(
                        "src/app.py",
                        "print('new')\n",
                        "print('old')\n",
                        before_exists=True,
                        after_exists=True,
                    ),
                }
            ],
            "automatic_verification_results": [
                {
                    "purpose": "test",
                    "command": "uv run pytest",
                    "ok": True,
                    "status": "passed",
                }
            ],
        },
    )
    return run_id


def _git(cwd: Path, *args: str) -> str:
    return _run(cwd, "git", *args).stdout.strip()


def _run(cwd: Path, *command: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return completed
