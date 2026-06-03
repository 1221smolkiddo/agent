from pathlib import Path

from code_agent.sandbox import create_sandbox_workspace


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
