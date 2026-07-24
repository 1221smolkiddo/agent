"""Tests for auth CLI commands."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from code_agent.cli import app
from code_agent.auth.models import Account
from code_agent.account.profile import AccountStore

runner = CliRunner()

@pytest.fixture
def mock_signed_in_account(monkeypatch):
    """Mocks AccountStore to return a logged-in account."""
    def mock_load(self):
        return Account(
            name="Ultima",
            email="formajor112@gmail.com",
            created_at="2026-01-01T00:00:00Z",
            user_id="123",
            picture_url="http://example.com/pic.jpg",
            provider="google",
            last_login_at="2026-01-01T00:00:00Z"
        )
    
    monkeypatch.setattr(AccountStore, "load", mock_load)

@pytest.fixture
def mock_not_signed_in(monkeypatch):
    """Mocks AccountStore to return no account."""
    def mock_load(self):
        return None
    
    monkeypatch.setattr(AccountStore, "load", mock_load)

@pytest.fixture
def mock_session_clear(monkeypatch):
    """Mocks LocalSession.clear."""
    from code_agent.auth.session import LocalSession
    def mock_clear(self):
        return True, True
    
    monkeypatch.setattr(LocalSession, "clear", mock_clear)

def test_logout_cli_cancelled(mock_signed_in_account):
    result = runner.invoke(app, ["auth", "logout"], input="n\n")
    assert result.exit_code == 0
    assert "You are currently signed in as" in result.stdout
    assert "Ultima" in result.stdout
    assert "formajor112@gmail.com" in result.stdout
    assert "Logout cancelled." in result.stdout

def test_logout_cli_confirmed(mock_signed_in_account, mock_session_clear):
    result = runner.invoke(app, ["auth", "logout"], input="y\n")
    assert result.exit_code == 0
    assert "You are currently signed in as" in result.stdout
    assert "Successfully signed out" in result.stdout
    assert "You're now using Agent47 anonymously." in result.stdout

def test_logout_cli_yes_flag(mock_signed_in_account, mock_session_clear):
    result = runner.invoke(app, ["auth", "logout", "--yes"])
    assert result.exit_code == 0
    assert "You are currently signed in as" in result.stdout
    assert "Are you sure you want to sign out?" not in result.stdout
    assert "Successfully signed out" in result.stdout

def test_logout_cli_not_signed_in(mock_not_signed_in):
    result = runner.invoke(app, ["auth", "logout"])
    assert result.exit_code == 0
    assert "You are not currently signed in." in result.stdout
