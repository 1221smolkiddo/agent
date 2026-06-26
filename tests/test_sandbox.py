from pathlib import Path

from typer.testing import CliRunner

from code_agent.cli import app
from code_agent.sandbox import (
    create_sandbox_workspace,
    diff_sandbox_workspace,
    format_sandbox_diff,
    format_sandbox_limits,
    promote_sandbox_changes,
)


def test_create_sandbox_workspace_copies_project_files(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hello')", encoding="utf-8")

    sandbox = create_sandbox_workspace(tmp_path)

    assert sandbox.source == tmp_path.resolve()
    assert (sandbox.path / "src" / "app.py").read_text(encoding="utf-8") == "print('hello')"


def test_create_sandbox_workspace_excludes_local_state(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=value", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("git", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("venv", encoding="utf-8")

    sandbox = create_sandbox_workspace(tmp_path)

    assert not (sandbox.path / ".env").exists()
    assert not (sandbox.path / ".git").exists()
    assert not (sandbox.path / ".venv").exists()


def test_sandbox_diff_reports_update_create_and_delete(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('base')\n", encoding="utf-8")
    (tmp_path / "old.txt").write_text("remove me\n", encoding="utf-8")
    sandbox = create_sandbox_workspace(tmp_path)
    (sandbox.path / "src" / "app.py").write_text("print('sandbox')\n", encoding="utf-8")
    (sandbox.path / "new.txt").write_text("add me\n", encoding="utf-8")
    (sandbox.path / "old.txt").unlink()

    diff = diff_sandbox_workspace(tmp_path, sandbox.path)
    rendered = format_sandbox_diff(diff)

    assert diff.changed_paths == ["new.txt", "old.txt", "src/app.py"]
    assert "--- /dev/null" in diff.patch
    assert "+++ b/new.txt" in diff.patch
    assert "--- a/old.txt" in diff.patch
    assert "+++ /dev/null" in diff.patch
    assert "-print('base')" in diff.patch
    assert "+print('sandbox')" in diff.patch
    assert "changed files: 3" in rendered


def test_sandbox_apply_promotes_changes_after_approval(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('base')\n", encoding="utf-8")
    sandbox = create_sandbox_workspace(tmp_path)
    (sandbox.path / "src" / "app.py").write_text("print('sandbox')\n", encoding="utf-8")
    (sandbox.path / "new.txt").write_text("add me\n", encoding="utf-8")

    approvals: list[str] = []
    result = promote_sandbox_changes(
        tmp_path,
        sandbox.path,
        approval_callback=lambda _action, detail, _metadata: approvals.append(detail) or True,
    )

    assert result.ok
    assert result.changed_paths == ["new.txt", "src/app.py"]
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "print('sandbox')\n"
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "add me\n"
    assert "Patch preview:" in approvals[0]
    assert result.metadata["paths"] == ["new.txt", "src/app.py"]


def test_sandbox_apply_denial_leaves_base_unchanged(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("base\n", encoding="utf-8")
    sandbox = create_sandbox_workspace(tmp_path)
    (sandbox.path / "app.py").write_text("sandbox\n", encoding="utf-8")

    result = promote_sandbox_changes(
        tmp_path,
        sandbox.path,
        approval_callback=lambda _action, _detail, _metadata: False,
    )

    assert not result.ok
    assert "Permission denied for apply_patch" in result.output
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "base\n"


def test_sandbox_diff_cli_outputs_patch(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("base\n", encoding="utf-8")
    sandbox = create_sandbox_workspace(tmp_path)
    (sandbox.path / "app.py").write_text("sandbox\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["sandbox", "diff", str(sandbox.path), "--base", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "Sandbox diff:" in result.output
    assert "-base" in result.output
    assert "+sandbox" in result.output


def test_format_sandbox_limits_describes_process_and_network_boundaries() -> None:
    output = format_sandbox_limits()

    assert "not OS-level process isolation" in output
    assert "shell commands still run as local processes" in output
    assert "--deny-network-shell" in output
    assert "explicit sandbox apply promotion" in output
