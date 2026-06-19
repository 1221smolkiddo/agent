from pathlib import Path

import code_agent.tools as tools_module
from code_agent.permissions import format_permission_detail
from code_agent.schema import (
    DeleteFileAction,
    ListFilesAction,
    RankContextAction,
    ReadFileAction,
    RepoMapAction,
    SearchAction,
    WebSearchAction,
    WriteFileAction,
    SymbolIndexAction,
)
from code_agent.tools import BingParser, ToolRegistry


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


def test_repo_map_requires_permission(tmp_path: Path) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: False)

    result = tools.run(RepoMapAction(type="repo_map"))

    assert not result.ok
    assert result.output == "Permission denied for repo_map."


def test_rank_context_requires_permission(tmp_path: Path) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: False)

    result = tools.run(RankContextAction(type="rank_context", task="fix cli"))

    assert not result.ok
    assert result.output == "Permission denied for rank_context."


def test_symbol_index_requires_permission(tmp_path: Path) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: False)

    result = tools.run(SymbolIndexAction(type="symbol_index"))

    assert not result.ok
    assert result.output == "Permission denied for symbol_index."


def test_delete_file_requires_permission(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("delete me", encoding="utf-8")
    tools = ToolRegistry(workspace=tmp_path, dry_run=False, approval_callback=lambda _a, _d: False)

    result = tools.run(DeleteFileAction(type="delete_file", path="notes.md"))

    assert not result.ok
    assert result.output == "Permission denied for delete_file."
    assert (tmp_path / "notes.md").exists()


def test_read_file_with_permission_succeeds(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: True)

    result = tools.run(ReadFileAction(type="read_file", path="README.md"))

    assert result.ok
    assert result.output == "hello"


def test_delete_file_with_permission_succeeds(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("delete me", encoding="utf-8")
    tools = ToolRegistry(workspace=tmp_path, dry_run=False, approval_callback=lambda _a, _d: True)

    result = tools.run(DeleteFileAction(type="delete_file", path="notes.md"))

    assert result.ok
    assert "Deleted notes.md." in result.output
    assert not (tmp_path / "notes.md").exists()


def test_permission_detail_truncates_large_preview() -> None:
    detail = "\n".join(f"line {index}" for index in range(200))

    rendered = format_permission_detail(detail, max_chars=300, max_lines=20)

    assert "line 0" in rendered
    assert "line 19" in rendered
    assert "line 20" not in rendered
    assert "<preview truncated:" in rendered


def test_write_file_returns_truncated_large_diff_to_model(tmp_path: Path) -> None:
    captured_details: list[str] = []

    def approve(_action: str, detail: str) -> bool:
        captured_details.append(detail)
        return True

    tools = ToolRegistry(workspace=tmp_path, dry_run=False, approval_callback=approve)
    content = "\n".join(f"long generated line {index}" for index in range(2000))

    result = tools.run(WriteFileAction(type="write_file", path="long.md", content=content))

    assert result.ok
    assert len(result.output) < len(captured_details[0])
    assert "<truncated" in result.output
    assert (tmp_path / "long.md").read_text(encoding="utf-8") == content


def test_delete_file_dry_run_does_not_remove_file(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("delete me", encoding="utf-8")
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: True)

    result = tools.run(DeleteFileAction(type="delete_file", path="notes.md"))

    assert not result.ok
    assert "Dry-run mode skipped delete_file" in result.output
    assert (tmp_path / "notes.md").exists()


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


def test_repo_map_and_rank_context_succeed_with_permission(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "cli.py").write_text("def main(): pass", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Project", encoding="utf-8")
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: True)

    repo_map = tools.run(RepoMapAction(type="repo_map", max_files=20))
    ranked = tools.run(RankContextAction(type="rank_context", task="fix cli", max_results=5))
    symbols = tools.run(SymbolIndexAction(type="symbol_index", max_files=20, max_symbols=20))

    assert repo_map.ok
    assert "Repository map:" in repo_map.output
    assert ranked.ok
    assert "src/cli.py" in ranked.output
    assert symbols.ok
    assert "Symbol index:" in symbols.output


def test_bing_parser_extracts_general_web_results() -> None:
    parser = BingParser()
    parser.feed(
        """
<li class="b_algo">
  <div><a class="tilk" href="https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9ub2lzZS5leGFtcGxlLw">noise.example</a></div>
  <h2><a href="https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9h">Example Result</a></h2>
</li>
<li class="b_algo"><h2><a href="https://example.com/b">Second Result</a></h2></li>
""".strip()
    )

    assert parser.results == [
        ("Example Result", "https://example.com/a"),
        ("Second Result", "https://example.com/b"),
    ]


def test_web_search_uses_duckduckgo_when_bing_has_no_results(tmp_path: Path, monkeypatch) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: True)
    monkeypatch.setattr(tools, "_search_bing", lambda _query: [])
    monkeypatch.setattr(
        tools,
        "_search_duckduckgo",
        lambda _query: [("Fallback Result", "https://example.com/fallback")],
    )

    result = tools.run(WebSearchAction(type="web_search", query="general question"))

    assert result.ok
    assert "Fallback Result" in result.output


def test_web_search_reports_provider_failure_when_no_results(tmp_path: Path, monkeypatch) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: True)
    monkeypatch.setattr(tools, "_search_bing", lambda _query: [])
    monkeypatch.setattr(tools, "_search_duckduckgo", lambda _query: [])

    result = tools.run(WebSearchAction(type="web_search", query="general question"))

    assert not result.ok
    assert "No web results were found" in result.output
