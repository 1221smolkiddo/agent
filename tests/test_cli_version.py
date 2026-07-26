from typer.testing import CliRunner
from code_agent.cli import app
from unittest.mock import patch

runner = CliRunner()

def test_version_flag_success():
    with patch("code_agent.interactive.agent_version", return_value="0.1.0b1"):
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
