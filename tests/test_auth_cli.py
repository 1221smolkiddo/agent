"""Tests for auth CLI commands."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from code_agent.cli import app
from code_agent.auth.models import Account
from code_agent.account.profile import AccountStore

runner = CliRunner()


@pytest.fixture
def fake_session(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.tokens = None
            self.clear_calls = 0

        @property
        def credentials(self):
            return self

        def get_oauth_tokens(self):
            return self.tokens

        def clear(self):
            self.clear_calls += 1
            self.tokens = None
            return True, True

    session = FakeSession()
    monkeypatch.setattr("code_agent.cli.LocalSession", lambda: session)
    return session

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

def test_logout_cli_cancelled(mock_signed_in_account, fake_session):
    result = runner.invoke(app, ["auth", "logout"], input="n\n")
    assert result.exit_code == 0
    assert "You are currently signed in as" in result.stdout
    assert "Ultima" in result.stdout
    assert "formajor112@gmail.com" in result.stdout
    assert "Logout cancelled." in result.stdout

def test_logout_cli_confirmed(mock_signed_in_account, fake_session):
    result = runner.invoke(app, ["auth", "logout"], input="y\n")
    assert result.exit_code == 0
    assert "You are currently signed in as" in result.stdout
    assert "Successfully signed out" in result.stdout
    assert "You're now using Agent47 anonymously." in result.stdout

def test_logout_cli_yes_flag(mock_signed_in_account, fake_session):
    result = runner.invoke(app, ["auth", "logout", "--yes"])
    assert result.exit_code == 0
    assert "You are currently signed in as" in result.stdout
    assert "Are you sure you want to sign out?" not in result.stdout
    assert "Successfully signed out" in result.stdout

def test_logout_cli_not_signed_in(mock_not_signed_in, fake_session):
    result = runner.invoke(app, ["auth", "logout"])
    assert result.exit_code == 0
    assert "You are not currently signed in." in result.stdout


def test_logout_clears_orphaned_credentials(mock_not_signed_in, fake_session):
    fake_session.tokens = {"access_token": "token"}

    result = runner.invoke(app, ["auth", "logout", "--yes"])

    assert result.exit_code == 0
    assert "Account metadata is missing" in result.stdout
    assert fake_session.clear_calls == 1
    assert fake_session.tokens is None


def test_legacy_logout_forwards_yes(mock_signed_in_account, fake_session):
    result = runner.invoke(app, ["logout", "--yes"])

    assert result.exit_code == 0
    assert "Are you sure you want to sign out?" not in result.stdout
    assert fake_session.clear_calls == 1
