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


def test_classify_shell_command_blocks_compound_and_inline_code() -> None:
    compound = classify_shell_command("uv run pytest && git status")
    redirected = classify_shell_command("uv run pytest > out.txt")
    inline_python = classify_shell_command("python -c \"print('hi')\"")
    unknown = classify_shell_command("echo hello")

    assert compound.category == "compound-shell"
    assert not compound.allowed
    assert redirected.category == "compound-shell"
    assert not redirected.allowed
    assert inline_python.category == "arbitrary-code"
    assert not inline_python.allowed
    assert unknown.category == "unknown"
    assert not unknown.allowed


def test_classify_shell_command_blocks_workspace_escape() -> None:
    policy = classify_shell_command("cd ..")

    assert policy.category == "workspace-escape"
    assert policy.risk == "critical"
    assert not policy.allowed


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
    assert "May write files: yes" in approval_details[0]
    assert "May access network: yes" in approval_details[0]
    assert "Arbitrary code: no" in approval_details[0]
    assert "Timeout: 180s" in approval_details[0]
    assert "Command: npm install" in approval_details[0]


def test_run_shell_blocks_unclassified_command_without_prompt(tmp_path: Path) -> None:
    approvals: list[str] = []
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=False,
        approval_callback=lambda action, _detail: approvals.append(action) or True,
    )

    result = tools.run(RunShellAction(type="run_shell", command="echo hello"))

    assert not result.ok
    assert "Blocked unknown shell command" in result.output
    assert approvals == []


def test_run_shell_blocks_compound_command_without_prompt(tmp_path: Path) -> None:
    approvals: list[str] = []
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=False,
        approval_callback=lambda action, _detail: approvals.append(action) or True,
    )

    result = tools.run(RunShellAction(type="run_shell", command="uv run pytest && git status"))

    assert not result.ok
    assert "Blocked compound-shell shell command" in result.output
    assert approvals == []


def test_run_shell_redacts_secret_output(tmp_path: Path, monkeypatch) -> None:
    def fake_run(*_args, **_kwargs):
        return (
            subprocess.CompletedProcess(
            args=["pwsh"],
            returncode=0,
            stdout="TOKEN=super-secret-token\n",
            stderr="",
            ),
            False,
            "",
        )

    tools = ToolRegistry(workspace=tmp_path, dry_run=False, approval_callback=lambda _a, _d: True)
    monkeypatch.setattr(tools, "_run_shell_process", fake_run)

    result = tools.run(RunShellAction(type="run_shell", command="uv run pytest"))

    assert result.ok
    assert "super-secret-token" not in result.output
    assert result.output == "TOKEN=[REDACTED]"


def test_run_shell_uses_scrubbed_environment_and_policy_timeout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    captured: dict[str, object] = {}

    def fake_run(*_args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args=["uv"], returncode=0, stdout="ok", stderr=""), False, ""

    tools = ToolRegistry(workspace=tmp_path, dry_run=False, approval_callback=lambda _a, _d: True)
    monkeypatch.setattr(tools, "_run_shell_process", fake_run)

    result = tools.run(RunShellAction(type="run_shell", command="uv run pytest"))

    assert result.ok
    assert captured["timeout_seconds"] == 120
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["AGENT47_SANDBOXED_SHELL"] == "1"
    assert "OPENROUTER_API_KEY" not in env


def test_run_shell_reports_timeout_with_capped_redacted_output(tmp_path: Path, monkeypatch) -> None:
    def timeout_run(*_args, **_kwargs):
        return (
            subprocess.CompletedProcess(args=["uv"], returncode=-9, stdout="", stderr=""),
            True,
            "TOKEN=super-secret-token\n" + "x" * 30000,
        )

    tools = ToolRegistry(workspace=tmp_path, dry_run=False, approval_callback=lambda _a, _d: True)
    monkeypatch.setattr(tools, "_run_shell_process", timeout_run)

    result = tools.run(RunShellAction(type="run_shell", command="uv run pytest"))

    assert not result.ok
    assert "timed out after 120s" in result.output
    assert "super-secret-token" not in result.output
    assert "<truncated" in result.output
    assert result.metadata["process_tree_cleanup"] is True


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
