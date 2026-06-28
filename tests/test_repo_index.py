from pathlib import Path
import sqlite3
import time

from code_agent.repo_index import (
    RepoIndexCache,
    build_dependency_graph,
    build_repo_map,
    build_symbol_index,
    index_repo,
    rank_context,
    start_background_index_refresh,
)


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


def test_dependency_graph_extracts_python_and_javascript_imports(tmp_path: Path) -> None:
    (tmp_path / "src" / "code_agent").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "web").mkdir()
    (tmp_path / "src" / "code_agent" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "code_agent" / "tools.py").write_text(
        "import json\nfrom code_agent.repo_index import build_repo_map\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "code_agent" / "repo_index.py").write_text(
        "def build_repo_map(): pass\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_tools.py").write_text(
        "from code_agent.tools import ToolRegistry\n",
        encoding="utf-8",
    )
    (tmp_path / "web" / "app.ts").write_text(
        "import { helper } from './helper';\nimport React from 'react';\n",
        encoding="utf-8",
    )
    (tmp_path / "web" / "helper.ts").write_text("export const helper = 1;\n", encoding="utf-8")

    output = build_dependency_graph(tmp_path)

    assert "Dependency graph:" in output
    assert "src/code_agent/tools.py -> src/code_agent/repo_index.py (code_agent.repo_index)" in output
    assert "tests/test_tools.py -> src/code_agent/tools.py (code_agent.tools)" in output
    assert "web/app.ts -> web/helper.ts (./helper)" in output
    assert "src/code_agent/tools.py: json" in output
    assert "web/app.ts: react" in output


def test_repo_index_cache_persists_file_metadata_and_symbols(tmp_path: Path) -> None:
    cache = RepoIndexCache(tmp_path / ".code-agent" / "agent.db")
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("import json\n\ndef main(): pass\n", encoding="utf-8")

    files = index_repo(tmp_path, cache=cache)

    item = next(file for file in files if file.path == "src/app.py")
    assert item.sha256
    assert item.symbols == ("L3 def main",)
    assert item.imports == ("json",)
    with sqlite3.connect(cache.db_path) as conn:
        count = conn.execute("select count(*) from repo_index_files").fetchone()[0]
    assert count == 1


def test_repo_index_cache_reuses_unchanged_symbols(tmp_path: Path, monkeypatch) -> None:
    cache = RepoIndexCache(tmp_path / ".code-agent" / "agent.db")
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("def cached_symbol(): pass\n", encoding="utf-8")
    index_repo(tmp_path, cache=cache)

    def fail_if_reparsed(_path: Path) -> list[str]:
        raise AssertionError("unchanged file should use cached symbols")

    monkeypatch.setattr("code_agent.repo_index._symbols_for_file", fail_if_reparsed)

    output = build_symbol_index(tmp_path, cache=cache)

    assert "cached_symbol" in output


def test_repo_index_cache_refreshes_changed_files_and_removes_deleted_files(tmp_path: Path) -> None:
    cache = RepoIndexCache(tmp_path / ".code-agent" / "agent.db")
    source = tmp_path / "src" / "app.py"
    deleted = tmp_path / "src" / "old.py"
    source.parent.mkdir()
    source.write_text("def before(): pass\n", encoding="utf-8")
    deleted.write_text("def old(): pass\n", encoding="utf-8")
    index_repo(tmp_path, cache=cache)

    source.write_text("def after(): pass\n", encoding="utf-8")
    deleted.unlink()

    output = build_symbol_index(tmp_path, cache=cache)
    cached_paths = set(cache.load_workspace(tmp_path))

    assert "after" in output
    assert "before" not in output
    assert cached_paths == {"src/app.py"}


def test_background_index_refresh_populates_cache(tmp_path: Path) -> None:
    cache = RepoIndexCache(tmp_path / ".code-agent" / "agent.db")
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("def background(): pass\n", encoding="utf-8")
    worker = start_background_index_refresh(tmp_path, cache, interval_seconds=0.05)

    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if "src/app.py" in cache.load_workspace(tmp_path):
                break
            time.sleep(0.02)
    finally:
        worker.stop()

    assert "src/app.py" in cache.load_workspace(tmp_path)
