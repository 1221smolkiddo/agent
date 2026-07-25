import json
import pytest
from pathlib import Path
from typer.testing import CliRunner
from code_agent.cli import app

runner = CliRunner()

@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path

def test_mcp_list_missing_config(workspace: Path) -> None:
    result = runner.invoke(app, ["mcp", "list"])
    assert result.exit_code == 0
    assert "No MCP configuration found at" in result.stdout
    assert ".agents/mcp.json" in result.stdout

def test_mcp_list_invalid_config(workspace: Path) -> None:
    agents_dir = workspace / ".agents"
    agents_dir.mkdir()
    (agents_dir / "mcp.json").write_text("invalid json")
    
    result = runner.invoke(app, ["mcp", "list"])
    assert result.exit_code == 0
    assert "Failed to parse .agents/mcp.json" in result.stdout

def test_mcp_list_empty_config(workspace: Path) -> None:
    agents_dir = workspace / ".agents"
    agents_dir.mkdir()
    (agents_dir / "mcp.json").write_text(json.dumps({"servers": {}}))
    
    result = runner.invoke(app, ["mcp", "list"])
    assert result.exit_code == 0
    assert "No servers configured in" in result.stdout

def test_mcp_list_valid_config(workspace: Path) -> None:
    agents_dir = workspace / ".agents"
    agents_dir.mkdir()
    config = {
        "servers": {
            "filesystem": {
                "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "."],
                "enabled": True
            },
            "disabled-server": {
                "command": ["echo", "offline"],
                "enabled": False
            }
        }
    }
    (agents_dir / "mcp.json").write_text(json.dumps(config))
    
    result = runner.invoke(app, ["mcp", "list"])
    assert result.exit_code == 0
    assert "Configured Servers" in result.stdout
    assert "filesystem" in result.stdout
    assert "disabled-server" in result.stdout
    assert "npx -y @modelcontextprotocol/server-filesystem ." in result.stdout
    assert "Working Directory:" in result.stdout

def test_mcp_status_offline(workspace: Path) -> None:
    agents_dir = workspace / ".agents"
    agents_dir.mkdir()
    config = {
        "servers": {
            "filesystem": {
                "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "."],
                "enabled": True
            }
        }
    }
    (agents_dir / "mcp.json").write_text(json.dumps(config))
    
    result = runner.invoke(app, ["mcp", "status"])
    assert result.exit_code == 0
    assert "MCP Server Status" in result.stdout
    assert "filesystem" in result.stdout
    assert "Yes" in result.stdout
    assert "No" in result.stdout
    assert "Not running" in result.stdout

def test_mcp_status_no_servers(workspace: Path) -> None:
    result = runner.invoke(app, ["mcp", "status"])
    assert result.exit_code == 0
    assert "No servers configured." in result.stdout


@pytest.mark.parametrize(
    ("command", "expected_text"),
    [
        (["mcp", "status"], "Failed to parse .agents/mcp.json"),
        (["mcp", "tools"], "Failed to parse .agents/mcp.json"),
    ],
)
def test_mcp_inspection_commands_handle_malformed_config(
    workspace: Path, command: list[str], expected_text: str
) -> None:
    agents_dir = workspace / ".agents"
    agents_dir.mkdir()
    (agents_dir / "mcp.json").write_text("invalid json")

    result = runner.invoke(app, command)
    assert result.exit_code == 0
    assert expected_text in result.stdout


def test_mcp_tools_listing_offline(workspace: Path) -> None:
    agents_dir = workspace / ".agents"
    agents_dir.mkdir()
    config = {
        "servers": {
            "filesystem": {
                "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "."],
                "enabled": True
            }
        }
    }
    (agents_dir / "mcp.json").write_text(json.dumps(config))
    
    # In a read-only scenario without start_enabled(), tools shouldn't be populated.
    result = runner.invoke(app, ["mcp", "tools"])
    assert result.exit_code == 0
    assert "No MCP tools currently registered." in result.stdout
    assert "Start the server to discover tools." in result.stdout

def test_mcp_start_unknown_server(workspace: Path) -> None:
    result = runner.invoke(app, ["mcp", "start", "unknown_server"])
    assert result.exit_code == 1
    assert 'Unknown MCP server "unknown_server".' in result.stdout
    assert "Starting" not in result.stdout

def test_mcp_stop_unknown_server(workspace: Path) -> None:
    result = runner.invoke(app, ["mcp", "stop", "unknown_server"])
    assert result.exit_code == 1
    assert 'Unknown MCP server "unknown_server".' in result.stdout
    assert "Stopping" not in result.stdout

def test_mcp_restart_unknown_server(workspace: Path) -> None:
    result = runner.invoke(app, ["mcp", "restart", "unknown_server"])
    assert result.exit_code == 1
    assert 'Unknown MCP server "unknown_server".' in result.stdout
    assert "Restarting" not in result.stdout
    assert "Starting" not in result.stdout

def test_mcp_resources_no_config(workspace: Path) -> None:
    result = runner.invoke(app, ["mcp", "resources"])
    assert result.exit_code == 0
    assert "No MCP configuration found at" in result.stdout

def test_mcp_resources_offline(workspace: Path) -> None:
    agents_dir = workspace / ".agents"
    agents_dir.mkdir()
    config = {
        "servers": {
            "test_server": {
                "command": ["echo", "offline"],
                "enabled": True
            }
        }
    }
    (agents_dir / "mcp.json").write_text(json.dumps(config))
    result = runner.invoke(app, ["mcp", "resources"])
    assert "No resources available" in result.stdout or "Failed to list resources" in result.stdout

def test_mcp_prompts_no_config(workspace: Path) -> None:
    result = runner.invoke(app, ["mcp", "prompts"])
    assert result.exit_code == 0
    assert "No MCP configuration found at" in result.stdout

def test_mcp_resource_read_no_config(workspace: Path) -> None:
    result = runner.invoke(app, ["mcp", "resource", "test://uri"])
    assert result.exit_code == 0
    assert "No MCP configuration found at" in result.stdout

def test_mcp_prompt_read_no_config(workspace: Path) -> None:
    result = runner.invoke(app, ["mcp", "prompt", "test-prompt"])
    assert result.exit_code == 0
    assert "No MCP configuration found at" in result.stdout

