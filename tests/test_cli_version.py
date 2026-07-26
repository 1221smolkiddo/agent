from typer.testing import CliRunner
from code_agent.cli import app
from unittest.mock import patch
import importlib.metadata

runner = CliRunner()
original_version = importlib.metadata.version

def mock_version(name):
    if name in ("agent47", "code-agent"):
        return "0.1.0b1"
    return original_version(name)

def test_version_flag_success():
    with patch("importlib.metadata.version", side_effect=mock_version):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "Agent47 0.1.0b1" in result.stdout

def test_help_flag():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "A CLI-first coding agent" in result.stdout

def test_subcommands_preserved():
    # Verify subcommands are still parsed correctly
    result = runner.invoke(app, ["keys", "--help"])
    assert result.exit_code == 0
    assert "keys" in result.stdout

def test_unrelated_flag_errors():
    # Verify that we didn't break missing command error handling
    result = runner.invoke(app, ["--nonexistent"])
    assert result.exit_code != 0
