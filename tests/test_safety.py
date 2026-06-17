from pathlib import Path
import subprocess

import code_agent.tools as tools_module
from code_agent.safety import classify_network_url, classify_shell_command, redact_secrets
from code_agent.schema import ReadFileAction, RunShellAction, SearchAction, WebSearchAction, WriteFileAction
from code_agent.tools import ToolRegistry


def test_classify_shell_command_blocks_destructive_commands() -> None:
    policy = classify_shell_command("git reset --hard HEAD")

    assert policy.category == "destructive"
    assert policy.risk == "critical"
    assert not policy.allowed


def test_classify_shell_command_labels_install_network_commands() -> None:
    policy = classify_shell_command("npm install")

    assert policy.category == "install/network"
    assert policy.risk == "high"
    assert policy.allowed


def test_classify_network_url_blocks_localhost_and_private_addresses() -> None:
    localhost = classify_network_url("http://localhost:8000")
    private_ip = classify_network_url("http://192.168.1.10/admin")

    assert localhost.category == "local-network"
    assert localhost.risk == "critical"
    assert not localhost.allowed
    assert private_ip.category == "local-network"
    assert not private_ip.allowed


def test_classify_network_url_allows_public_https() -> None:
    policy = classify_network_url("https://example.com/docs")

    assert policy.category == "public-web"
    assert policy.risk == "medium"
    assert policy.allowed


def test_redact_secrets_hides_key_values_and_bearer_tokens() -> None:
    output = redact_secrets(
        "OPENAI_API_KEY=sk-example-secret-token\nAuthorization: Bearer abcdefghijklmnop"
    )

    assert "sk-example-secret-token" not in output
    assert "abcdefghijklmnop" not in output
    assert "OPENAI_API_KEY=[REDACTED]" in output
    assert "Bearer [REDACTED]" in output


def test_read_file_refuses_sensitive_files_before_permission(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    approvals: list[str] = []
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=True,
        approval_callback=lambda action, _detail: approvals.append(action) or True,
    )

    result = tools.run(ReadFileAction(type="read_file", path=".env"))

    assert not result.ok
    assert "Refusing to read sensitive file" in result.output
    assert approvals == []


def test_write_file_refuses_sensitive_files(tmp_path: Path) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=False, approval_callback=lambda _a, _d: True)

    result = tools.run(WriteFileAction(type="write_file", path=".env", content="TOKEN=secret\n"))

    assert not result.ok
    assert result.output == "Refusing to write sensitive file: .env."
    assert not (tmp_path / ".env").exists()


def test_run_shell_blocks_destructive_command_without_prompt(tmp_path: Path) -> None:
    approvals: list[str] = []
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=False,
        approval_callback=lambda action, _detail: approvals.append(action) or True,
    )

    result = tools.run(RunShellAction(type="run_shell", command="git reset --hard HEAD"))

    assert not result.ok
    assert "Blocked destructive shell command" in result.output
    assert approvals == []


def test_run_shell_permission_detail_includes_risk_label(tmp_path: Path) -> None:
    approval_details: list[str] = []
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=False,
        approval_callback=lambda _action, detail: approval_details.append(detail) or False,
    )

    result = tools.run(RunShellAction(type="run_shell", command="npm install"))

    assert not result.ok
    assert result.output == "Permission denied for run_shell."
    assert "Risk: high" in approval_details[0]
    assert "Category: install/network" in approval_details[0]
    assert "Command: npm install" in approval_details[0]


def test_run_shell_redacts_secret_output(tmp_path: Path, monkeypatch) -> None:
    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["pwsh"],
            returncode=0,
            stdout="TOKEN=super-secret-token\n",
            stderr="",
        )

    monkeypatch.setattr(tools_module.subprocess, "run", fake_run)
    tools = ToolRegistry(workspace=tmp_path, dry_run=False, approval_callback=lambda _a, _d: True)

    result = tools.run(RunShellAction(type="run_shell", command="echo TOKEN=super-secret-token"))

    assert result.ok
    assert "super-secret-token" not in result.output
    assert result.output == "TOKEN=[REDACTED]"


def test_search_redacts_secret_matches_when_fallback_runs(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "settings.txt").write_text("API_KEY=super-secret-token\n", encoding="utf-8")

    def missing_rg(*_args, **_kwargs):
        raise FileNotFoundError("[WinError 2] The system cannot find the file specified")

    monkeypatch.setattr(tools_module.subprocess, "run", missing_rg)
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: True)

    result = tools.run(SearchAction(type="search", query="API_KEY"))

    assert result.ok
    assert "super-secret-token" not in result.output
    assert "settings.txt:1:API_KEY=[REDACTED]" in result.output


def test_web_search_permission_detail_includes_provider_audit(tmp_path: Path) -> None:
    approval_details: list[str] = []
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=True,
        approval_callback=lambda _action, detail: approval_details.append(detail) or False,
    )

    result = tools.run(WebSearchAction(type="web_search", query="Agent47 docs"))

    assert not result.ok
    assert result.output == "Permission denied for web_search."
    assert "Category: public-web-search" in approval_details[0]
    assert "Providers: www.bing.com, duckduckgo.com" in approval_details[0]
    assert "Query: Agent47 docs" in approval_details[0]


def test_web_search_filters_private_network_results(tmp_path: Path, monkeypatch) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: True)
    monkeypatch.setattr(
        tools,
        "_search_bing",
        lambda _query: [
            ("Local Admin", "http://127.0.0.1/admin"),
            ("Public Result", "https://example.com/docs"),
        ],
    )
    monkeypatch.setattr(tools, "_search_duckduckgo", lambda _query: [])

    result = tools.run(WebSearchAction(type="web_search", query="docs"))

    assert result.ok
    assert "Public Result" in result.output
    assert "https://example.com/docs" in result.output
    assert "127.0.0.1" not in result.output


def test_fetch_url_blocks_local_network_targets() -> None:
    try:
        ToolRegistry._fetch_url("http://127.0.0.1:8000")
    except ValueError as exc:
        assert "Blocked local-network web target" in str(exc)
    else:
        raise AssertionError("expected local-network web target to be blocked")
