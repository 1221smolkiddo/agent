import pytest
from pathlib import Path
from code_agent.interactive import _handle_mcp_command

def test_interactive_mcp_no_config(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _handle_mcp_command("status", tmp_path)
    captured = capsys.readouterr()
    assert "MCP Configuration Error" in captured.out or "MCP Error" in captured.out or "No MCP configuration found at" in captured.out

def test_interactive_mcp_invalid_command(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    agents_dir = tmp_path / ".agents"
    agents_dir.mkdir()
    (agents_dir / "mcp.json").write_text('{"servers": {}}')
    
    _handle_mcp_command("invalid_subcommand", tmp_path)
    captured = capsys.readouterr()
    assert "Invalid MCP Command" in captured.out
    assert "Unknown subcommand:" in captured.out

def test_interactive_mcp_status_empty(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    agents_dir = tmp_path / ".agents"
    agents_dir.mkdir()
    (agents_dir / "mcp.json").write_text('{"servers": {}}')
    
    _handle_mcp_command("status", tmp_path)
    captured = capsys.readouterr()
    assert "No servers configured." in captured.out

def test_interactive_mcp_tools_empty(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    agents_dir = tmp_path / ".agents"
    agents_dir.mkdir()
    (agents_dir / "mcp.json").write_text('{"servers": {}}')
    
    _handle_mcp_command("tools", tmp_path)
    captured = capsys.readouterr()
    assert "No MCP tools discovered." in captured.out

def test_interactive_mcp_resources_empty(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    agents_dir = tmp_path / ".agents"
    agents_dir.mkdir()
    (agents_dir / "mcp.json").write_text('{"servers": {}}')
    
    _handle_mcp_command("resources", tmp_path)
    captured = capsys.readouterr()
    assert "No resources available." in captured.out

def test_interactive_mcp_prompts_empty(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    agents_dir = tmp_path / ".agents"
    agents_dir.mkdir()
    (agents_dir / "mcp.json").write_text('{"servers": {}}')
    
    _handle_mcp_command("prompts", tmp_path)
    captured = capsys.readouterr()
    assert "No prompts available." in captured.out
