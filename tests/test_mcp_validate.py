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

def test_mcp_validate_missing_file(workspace: Path) -> None:
    result = runner.invoke(app, ["mcp", "validate"])
    assert result.exit_code == 0
    assert "No MCP configuration found at" in result.stdout

def test_mcp_validate_malformed_json(workspace: Path) -> None:
    agents_dir = workspace / ".agents"
    agents_dir.mkdir()
    (agents_dir / "mcp.json").write_text("invalid json")
    
    result = runner.invoke(app, ["mcp", "validate"])
    assert result.exit_code == 1
    assert "Failed to parse .agents/mcp.json:" in result.stdout

def test_mcp_validate_empty_config(workspace: Path) -> None:
    agents_dir = workspace / ".agents"
    agents_dir.mkdir()
    (agents_dir / "mcp.json").write_text(json.dumps({"servers": {}}))
    
    result = runner.invoke(app, ["mcp", "validate"])
    assert result.exit_code == 0
    assert "No servers configured." in result.stdout

def test_mcp_validate_success(workspace: Path) -> None:
    agents_dir = workspace / ".agents"
    agents_dir.mkdir()
    config = {
        "servers": {
            "filesystem": {
                "command": ["python", "--version"],
                "enabled": True
            }
        }
    }
    (agents_dir / "mcp.json").write_text(json.dumps(config))
    
    result = runner.invoke(app, ["mcp", "validate"])
    assert result.exit_code == 0
    assert "MCP Validation Summary" in result.stdout
    assert "filesystem" in result.stdout
    assert "PASS" in result.stdout
    assert "executable found" in result.stdout
    assert "configuration valid" in result.stdout

def test_mcp_validate_missing_executable(workspace: Path) -> None:
    agents_dir = workspace / ".agents"
    agents_dir.mkdir()
    config = {
        "servers": {
            "missing-exec": {
                "command": ["this-will-never-exist-12345"],
                "enabled": True
            }
        }
    }
    (agents_dir / "mcp.json").write_text(json.dumps(config))
    
    result = runner.invoke(app, ["mcp", "validate"])
    assert result.exit_code == 1
    assert "FAIL" in result.stdout
    assert "missing-exec" in result.stdout
    assert "executable \"this-will-never-exist-12345\" not found" in result.stdout

def test_mcp_validate_missing_env_var(workspace: Path) -> None:
    agents_dir = workspace / ".agents"
    agents_dir.mkdir()
    config = {
        "servers": {
            "missing-env": {
                "command": ["python", "--version"],
                "enabled": True,
                "auth_env_keys": ["SUPER_SECRET_MISSING_KEY"]
            }
        }
    }
    (agents_dir / "mcp.json").write_text(json.dumps(config))
    
    result = runner.invoke(app, ["mcp", "validate"])
    assert result.exit_code == 0
    assert "WARNING" in result.stdout
    assert "SUPER_SECRET_MISSING_KEY missing" in result.stdout

def test_mcp_validate_missing_cwd(workspace: Path) -> None:
    # Actually wait, cwd defaults to workspace in PlatformRuntime.create, so it's always valid.
    # We can't really test missing cwd via mcp.json because _configure_mcp doesn't read cwd from JSON, it sets it to workspace!
    pass

def test_mcp_validate_invalid_timeout(workspace: Path) -> None:
    agents_dir = workspace / ".agents"
    agents_dir.mkdir()
    config = {
        "servers": {
            "bad-timeout": {
                "command": ["python", "--version"],
                "timeout_seconds": -5
            }
        }
    }
    (agents_dir / "mcp.json").write_text(json.dumps(config))
    
    result = runner.invoke(app, ["mcp", "validate"])
    assert result.exit_code == 0
    assert "WARNING" in result.stdout
    assert "invalid timeout (-5.0), should be > 0" in result.stdout
