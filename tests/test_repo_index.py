from pathlib import Path

from code_agent.repo_index import build_repo_map, build_symbol_index, index_repo, rank_context


def test_repo_index_ignores_local_state_and_classifies_files(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=hidden", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("private", encoding="utf-8")
    (tmp_path / "src" / "code_agent").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'", encoding="utf-8")
    (tmp_path / "src" / "code_agent" / "cli.py").write_text("def main(): pass", encoding="utf-8")
    (tmp_path / "tests" / "test_cli.py").write_text("def test_cli(): pass", encoding="utf-8")
    (tmp_path / "docs" / "ROADMAP.md").write_text("# Roadmap", encoding="utf-8")

    files = index_repo(tmp_path)

    paths = {item.path for item in files}
    assert ".env" not in paths
    assert ".git/config" not in paths
    assert "pyproject.toml" in paths
    assert any(item.path == "src/code_agent/cli.py" and item.kind == "source" for item in files)
    assert any(item.path == "tests/test_cli.py" and item.kind == "test" for item in files)
    assert any(item.path == "docs/ROADMAP.md" and item.kind == "doc" for item in files)


def test_repo_map_summarizes_important_files_and_layout(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("# Example", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]", encoding="utf-8")
    (tmp_path / "src" / "agent.py").write_text("class Agent: pass", encoding="utf-8")

    output = build_repo_map(tmp_path)

    assert "Repository map:" in output
    assert "- README.md (config)" in output
    assert "- pyproject.toml (config)" in output
    assert "- src: 1 files" in output


def test_rank_context_prefers_task_terms_and_tests(tmp_path: Path) -> None:
    (tmp_path / "src" / "code_agent").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "code_agent" / "verification.py").write_text("def verify(): pass", encoding="utf-8")
    (tmp_path / "tests" / "test_verification.py").write_text("def test_verify(): pass", encoding="utf-8")
    (tmp_path / "src" / "code_agent" / "storage.py").write_text("def save(): pass", encoding="utf-8")

    output = rank_context(tmp_path, "fix verification tests")

    assert "Ranked context:" in output
    assert "tests/test_verification.py" in output
    assert output.index("tests/test_verification.py") < output.index("src/code_agent/storage.py")


def test_symbol_index_extracts_python_and_javascript_declarations(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "\n".join(
            [
                "class Agent:",
                "    async def run(self):",
                "        pass",
                "",
                "def helper():",
                "    pass",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "src" / "ui.ts").write_text(
        "\n".join(
            [
                "export class Panel {}",
                "export function render() {}",
                "const localState = {};",
            ]
        ),
        encoding="utf-8",
    )

    output = build_symbol_index(tmp_path)

    assert "Symbol index:" in output
    assert "src/app.py:" in output
    assert "- L1 class Agent" in output
    assert "- L2 async def run" in output
    assert "- L5 def helper" in output
    assert "src/ui.ts:" in output
    assert "- L1 class Panel" in output
    assert "- L2 function render" in output
    assert "- L3 binding localState" in output
