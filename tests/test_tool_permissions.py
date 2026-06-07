from pathlib import Path

import code_agent.tools as tools_module
from code_agent.schema import ListFilesAction, ReadFileAction, SearchAction, WebSearchAction
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


def test_read_file_with_permission_succeeds(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: True)

    result = tools.run(ReadFileAction(type="read_file", path="README.md"))

    assert result.ok
    assert result.output == "hello"


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
