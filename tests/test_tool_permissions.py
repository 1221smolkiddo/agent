from pathlib import Path

import code_agent.tools as tools_module
import code_agent.permissions as permissions_module
from code_agent.permissions import (
    ApprovalMode,
    PermissionPolicy,
    _format_permission_preview,
    confirm_permission,
    format_permission_detail,
)
from code_agent.schema import (
    DeleteFileAction,
    DependencyGraphAction,
    ListFilesAction,
    RankContextAction,
    ReadFileAction,
    RepoMapAction,
    RunShellAction,
    SearchAction,
    WebSearchAction,
    WriteFileAction,
    SymbolIndexAction,
)
from code_agent.terminal_ui import console
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


def test_dependency_graph_requires_permission(tmp_path: Path) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: False)

    result = tools.run(DependencyGraphAction(type="dependency_graph"))

    assert not result.ok
    assert result.output == "Permission denied for dependency_graph."


def test_delete_file_requires_permission(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("delete me", encoding="utf-8")
    tools = ToolRegistry(workspace=tmp_path, dry_run=False, approval_callback=lambda _a, _d: False)

    result = tools.run(DeleteFileAction(type="delete_file", path="notes.md"))

    assert not result.ok
    assert result.output == "Permission denied for delete_file."
    assert (tmp_path / "notes.md").exists()


def test_auto_approval_modes_keep_subprocess_actions_manual() -> None:
    requested: list[str] = []

    def approve(action: str, _detail: str) -> str:
        requested.append(action)
        return "a" if action == "read_file" else "n"

    policy = PermissionPolicy(approve, ApprovalMode.auto_read)

    assert policy.approve("read_file", "README.md") is True
    assert policy.approve("dependency_graph", "Graph imports") is True
    assert policy.approve("read_memory", "Read project memory") is True
    assert policy.approve("search", "Search .") is False
    assert requested == ["search"]

    policy.set_mode(ApprovalMode.approve_task)
    assert policy.approve("read_file", "README.md") is True
    assert policy.approve("repo_map", "Map repo") is True
    assert policy.approve("run_shell", "uv run pytest") is False
    assert policy.approve("apply_patch", "Patch preview") is False
    assert policy.approve("inspect_git_diff", "Git status") is False
    assert policy.approve("update_memory", "Memory preview") is False
    assert requested == [
        "search",
        "read_file",
        "run_shell",
        "apply_patch",
        "inspect_git_diff",
        "update_memory",
    ]


def test_read_file_with_permission_succeeds(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: True)

    result = tools.run(ReadFileAction(type="read_file", path="README.md"))

    assert result.ok
    assert result.output == "hello"


def test_string_permission_denial_is_not_treated_as_truthy(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: "n")

    result = tools.run(ReadFileAction(type="read_file", path="README.md"))

    assert not result.ok
    assert result.output == "Permission denied for read_file."


def test_delete_file_with_permission_succeeds(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("delete me", encoding="utf-8")
    tools = ToolRegistry(workspace=tmp_path, dry_run=False, approval_callback=lambda _a, _d: True)

    result = tools.run(DeleteFileAction(type="delete_file", path="notes.md"))

    assert result.ok
    assert "Deleted notes.md." in result.output
    assert not (tmp_path / "notes.md").exists()


def test_pytest_shell_command_clears_bytecode_cache_and_disables_new_bytecode(
    tmp_path: Path, monkeypatch
) -> None:
    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "app.cpython-312.pyc").write_bytes(b"stale")
    captured_env: dict[str, str] = {}

    def fake_run(*_args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return (
            tools_module.subprocess.CompletedProcess(
                args="python -m pytest",
                returncode=0,
                stdout="passed",
                stderr="",
            ),
            False,
            "",
        )

    tools = ToolRegistry(workspace=tmp_path, dry_run=False, approval_callback=lambda _a, _d: True)
    monkeypatch.setattr(tools, "_run_shell_process", fake_run)

    result = tools.run(RunShellAction(type="run_shell", command="python -m pytest"))

    assert result.ok
    assert result.output == "passed"
    assert not cache_dir.exists()
    assert captured_env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_permission_detail_truncates_large_preview() -> None:
    detail = "\n".join(f"line {index}" for index in range(200))

    rendered = format_permission_detail(detail, max_chars=300, max_lines=20)

    assert "line 0" in rendered
    assert "line 19" in rendered
    assert "line 20" not in rendered
    assert "<preview truncated:" in rendered


def test_permission_preview_summarizes_diff_without_raw_hunks() -> None:
    detail = "\n".join(
        [
            "--- a/app.py",
            "+++ b/app.py",
            "@@ -1 +1 @@",
            "-old_value = 1",
            "+new_value = 2",
        ]
    )

    rendered = _format_permission_preview(detail)

    assert "Files: app.py" in rendered
    assert "Changes: +1 -1" in rendered
    assert "new_value = 2" not in rendered
    assert "@@" not in rendered


def test_permission_preview_uses_patch_summary_before_hidden_diff() -> None:
    detail = "\n".join(
        [
            "Patch preview:",
            "Files: 1",
            "Total changes: +1 -1",
            "- app.py: update, +1 -1",
            "",
            "Unified diff:",
            "--- a/app.py",
            "+++ b/app.py",
            "@@ -1 +1 @@",
            "-old_value = 1",
            "+new_value = 2",
        ]
    )

    rendered = _format_permission_preview(detail)

    assert "Patch preview:" in rendered
    assert "- app.py: update, +1 -1" in rendered
    assert "new_value = 2" not in rendered


def test_permission_prompt_renders_visible_choice_labels(monkeypatch) -> None:
    monkeypatch.setattr(permissions_module.Prompt, "ask", lambda *_args, **_kwargs: "n")

    with console.capture() as capture:
        assert confirm_permission("read_file", "README.md") == "n"

    rendered = capture.get()
    assert "Choose:" in rendered
    assert "approve once" in rendered
    assert "deny" in rendered
    assert "view full detail" in rendered


def test_high_risk_permission_rejects_approve_all(monkeypatch) -> None:
    responses = iter(["a", "n"])
    monkeypatch.setattr(permissions_module.Prompt, "ask", lambda *_args, **_kwargs: next(responses))

    with console.capture() as capture:
        assert confirm_permission("run_shell", "uv run pytest") == "n"

    assert "Approve-all is disabled for high-risk actions" in capture.get()


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
    graph = tools.run(DependencyGraphAction(type="dependency_graph", max_files=20, max_edges=20))

    assert repo_map.ok
    assert "Repository map:" in repo_map.output
    assert ranked.ok
    assert "src/cli.py" in ranked.output
    assert symbols.ok
    assert "Symbol index:" in symbols.output
    assert graph.ok
    assert "Dependency graph:" in graph.output


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
