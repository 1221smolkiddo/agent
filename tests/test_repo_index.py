from pathlib import Path
import sqlite3
import threading
import time

from code_agent.repo_index import (
    RepoIndexCache,
    build_dependency_graph,
    build_project_graph,
    build_repo_map,
    build_symbol_index,
    index_repo,
    rank_context,
    start_background_index_refresh,
)
from code_agent.schema import WriteFileAction
from code_agent.tools import ToolRegistry


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


def test_project_graph_persists_symbols_calls_references_tests_and_config(tmp_path: Path) -> None:
    cache_path = tmp_path / ".code-agent" / "agent.db"
    (tmp_path / "src" / "demo").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / "src" / "demo" / "models.py").write_text(
        "class User:\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "demo" / "service.py").write_text(
        "from demo.models import User\n\ndef load_user():\n    return User()\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_service.py").write_text(
        "from demo.service import load_user\n\ndef test_load_user():\n    assert load_user()\n",
        encoding="utf-8",
    )

    first = build_project_graph(tmp_path, cache=RepoIndexCache(cache_path))
    restored = build_project_graph(tmp_path, cache=RepoIndexCache(cache_path))

    assert any(symbol.name == "User" and symbol.path.endswith("models.py") for symbol in restored.symbols)
    assert any(
        edge.relation == "call"
        and edge.source_path.endswith("service.py")
        and edge.target_symbol == "User"
        for edge in restored.edges
    )
    assert any(
        edge.relation == "reference"
        and edge.source_path.endswith("service.py")
        and edge.target_symbol == "User"
        for edge in restored.edges
    )
    assert any(edge.relation == "test" and edge.source_path == "tests/test_service.py" for edge in restored.edges)
    assert any(edge.relation == "config" and edge.source_path == "pyproject.toml" for edge in restored.edges)
    assert restored.edges == first.edges
    assert restored.stats is not None
    assert restored.stats.reused_files == len(restored.files)
    with sqlite3.connect(cache_path) as conn:
        assert conn.execute("select count(*) from repo_index_symbols").fetchone()[0] == 3
        assert conn.execute("select count(*) from repo_index_edges").fetchone()[0] == len(restored.edges)


def test_incremental_index_only_reparses_changed_files_and_deletes_stale_graph(tmp_path: Path, monkeypatch) -> None:
    cache = RepoIndexCache(tmp_path / ".code-agent" / "agent.db")
    source = tmp_path / "src" / "service.py"
    stable = tmp_path / "src" / "stable.py"
    deleted = tmp_path / "src" / "deleted.py"
    source.parent.mkdir()
    source.write_text("def before():\n    pass\n", encoding="utf-8")
    stable.write_text("def stable():\n    pass\n", encoding="utf-8")
    deleted.write_text("def removed():\n    pass\n", encoding="utf-8")
    build_project_graph(tmp_path, cache=cache)
    original = __import__("code_agent.repo_index", fromlist=["_index_file"])._index_file
    parsed: list[str] = []

    def record_parse(path: Path, relative: str, *, size: int, mtime_ns: int):
        parsed.append(relative)
        return original(path, relative, size=size, mtime_ns=mtime_ns)

    monkeypatch.setattr("code_agent.repo_index._index_file", record_parse)
    source.write_text("def after():\n    return stable()\n", encoding="utf-8")
    deleted.unlink()

    graph = build_project_graph(tmp_path, cache=cache)

    assert parsed == ["src/service.py"]
    assert graph.stats is not None
    assert graph.stats.parsed_files == 1
    assert graph.stats.reused_files == 1
    assert graph.stats.deleted_files == 1
    assert all(edge.source_path != "src/deleted.py" for edge in graph.edges)
    assert all(edge.target_path != "src/deleted.py" for edge in graph.edges)


def test_multi_language_graph_resolves_imports_and_symbols(tmp_path: Path) -> None:
    files = {
        "rust/main.rs": "mod helper;\nfn main() { helper(); }\n",
        "rust/helper.rs": "pub fn helper() {}\n",
        "go/main.go": 'package main\nimport "example/project/util"\nfunc main() { util.Run() }\n',
        "go/util/util.go": "package util\nfunc Run() {}\n",
        "src/demo/App.java": "package demo;\nimport demo.Helper;\nclass App { void run() { help(); } }\n",
        "src/demo/Helper.java": "package demo;\nclass Helper { void help() {} }\n",
        "native/main.cpp": '#include "helper.h"\nint main() { return helper(); }\n',
        "native/helper.h": "int helper();\n",
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    graph = build_project_graph(tmp_path)
    languages = {item.language for item in graph.files}

    assert {"rust", "go", "java", "cpp", "c"} <= languages
    assert any(symbol.name == "helper" and symbol.path == "rust/helper.rs" for symbol in graph.symbols)
    assert any(symbol.name == "Run" and symbol.path == "go/util/util.go" for symbol in graph.symbols)
    assert any(symbol.name == "Helper" and symbol.path == "src/demo/Helper.java" for symbol in graph.symbols)
    assert any(
        edge.relation == "import"
        and edge.source_path == "rust/main.rs"
        and edge.target_path == "rust/helper.rs"
        for edge in graph.edges
    )
    assert any(
        edge.relation == "import"
        and edge.source_path == "go/main.go"
        and edge.target_path == "go/util/util.go"
        for edge in graph.edges
    )
    assert any(
        edge.relation == "import"
        and edge.source_path == "src/demo/App.java"
        and edge.target_path == "src/demo/Helper.java"
        for edge in graph.edges
    )


def test_rank_context_is_graph_aware_and_token_bounded(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "parser.py").write_text(
        "def parse_request(value):\n    return value\n" + "padding = 'x'\n" * 30,
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_parser.py").write_text(
        "from parser import parse_request\n\ndef test_parse_request():\n    assert parse_request('x')\n",
        encoding="utf-8",
    )
    for index in range(6):
        (tmp_path / "src" / f"unrelated_{index}.py").write_text(
            "value = '" + ("x" * 400) + "'\n",
            encoding="utf-8",
        )

    output = rank_context(
        tmp_path,
        "change parse_request behavior and its tests",
        max_results=10,
        max_tokens=500,
    )

    assert "src/parser.py" in output
    assert "tests/test_parser.py" in output
    assert "test to src/parser.py" in output or "test from tests/test_parser.py" in output
    selected_line = next(line for line in output.splitlines() if line.startswith("Selected estimated tokens:"))
    assert int(selected_line.split(":", 1)[1].split("/", 1)[0]) <= 500
    assert sum(line[:1].isdigit() for line in output.splitlines()) >= 2
    assert "over-budget files" in output


def test_changed_files_are_indexed_in_parallel(tmp_path: Path, monkeypatch) -> None:
    for index in range(6):
        path = tmp_path / "src" / f"module_{index}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"def function_{index}(): pass\n", encoding="utf-8")
    original = __import__("code_agent.repo_index", fromlist=["_index_file"])._index_file
    worker_names: set[str] = set()
    lock = threading.Lock()

    def record_worker(path: Path, relative: str, *, size: int, mtime_ns: int):
        with lock:
            worker_names.add(threading.current_thread().name)
        time.sleep(0.02)
        return original(path, relative, size=size, mtime_ns=mtime_ns)

    monkeypatch.setattr("code_agent.repo_index._index_file", record_worker)

    index_repo(tmp_path)

    assert len(worker_names) > 1
    assert all(name.startswith("agent47-index") for name in worker_names)


def test_transactional_mutation_invalidates_cached_semantics_immediately(tmp_path: Path) -> None:
    cache = RepoIndexCache(tmp_path / ".code-agent" / "agent.db")
    source = tmp_path / "src" / "service.py"
    source.parent.mkdir()
    source.write_text("def before(): pass\n", encoding="utf-8")
    build_project_graph(tmp_path, cache=cache)
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=False,
        approval_callback=lambda *_args: True,
        index_cache=cache,
    )

    result = tools.run(
        WriteFileAction(
            type="write_file",
            path="src/service.py",
            content="def after(): pass\n",
        )
    )

    assert result.ok is True
    assert "src/service.py" not in cache.load_workspace(tmp_path)
    refreshed = build_project_graph(tmp_path, cache=cache)
    assert any(symbol.name == "after" for symbol in refreshed.symbols)
    assert all(symbol.name != "before" for symbol in refreshed.symbols)


def test_background_refresh_can_invalidate_and_reindex_specific_paths(tmp_path: Path) -> None:
    cache = RepoIndexCache(tmp_path / ".code-agent" / "agent.db")
    source = tmp_path / "src" / "service.py"
    source.parent.mkdir()
    source.write_text("def before(): pass\n", encoding="utf-8")
    worker = start_background_index_refresh(tmp_path, cache, interval_seconds=60)
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and "src/service.py" not in cache.load_workspace(tmp_path):
            time.sleep(0.02)
        source.write_text("def after(): pass\n", encoding="utf-8")
        worker.request_refresh(["src/service.py"])
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            files = cache.load_workspace(tmp_path)
            item = files.get("src/service.py")
            if item and any("after" in symbol for symbol in item.symbols):
                break
            time.sleep(0.02)
    finally:
        worker.stop()

    assert any("after" in symbol for symbol in cache.load_workspace(tmp_path)["src/service.py"].symbols)


def test_index_connections_close_and_rank_output_supports_legacy_windows_console(tmp_path: Path) -> None:
    db_path = tmp_path / ".code-agent" / "agent.db"
    source = tmp_path / "src" / "service.py"
    source.parent.mkdir()
    source.write_text("def serve_request(): pass\n", encoding="utf-8")
    cache = RepoIndexCache(db_path)

    output = rank_context(tmp_path, "serve request", cache=cache)
    moved = db_path.with_name("moved.db")
    db_path.rename(moved)

    assert moved.exists()
    assert output.encode("cp1252")
