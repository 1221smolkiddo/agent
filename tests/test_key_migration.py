"""Tests for the keys migrate command and _migrate_env_keys helper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


class FakeCredentialStore:
    """In-memory credential store for testing migration logic."""

    def __init__(self) -> None:
        self._keys: dict[str, str] = {}

    def get_provider_key(self, provider: str) -> str | None:
        return self._keys.get(provider)

    def set_provider_key(self, provider: str, value: str) -> None:
        self._keys[provider] = value.strip()

    def delete_provider_key(self, provider: str) -> bool:
        if provider in self._keys:
            del self._keys[provider]
            return True
        return False


class TestKeyMigration:
    def test_detects_env_keys(self, tmp_path: Path, monkeypatch) -> None:
        env = tmp_path / ".env"
        env.write_text("OPENAI_API_KEY=sk-test-123\nGEMINI_API_KEY=\n", encoding="utf-8")

        from code_agent.cli import _migrate_env_keys

        store = FakeCredentialStore()
        monkeypatch.setattr("code_agent.cli.CredentialStore", lambda: store)
        # Auto-confirm.
        monkeypatch.setattr("code_agent.cli.typer.confirm", lambda _msg: True)

        migrated, skipped = _migrate_env_keys(env, remove=False)

        assert "OpenAI" in migrated
        assert store.get_provider_key("openai") == "sk-test-123"

    def test_skips_already_configured(self, tmp_path: Path, monkeypatch) -> None:
        env = tmp_path / ".env"
        env.write_text("OPENAI_API_KEY=sk-new-key\n", encoding="utf-8")

        from code_agent.cli import _migrate_env_keys

        store = FakeCredentialStore()
        store.set_provider_key("openai", "sk-existing")
        monkeypatch.setattr("code_agent.cli.CredentialStore", lambda: store)
        monkeypatch.setattr("code_agent.cli.typer.confirm", lambda _msg: True)

        migrated, skipped = _migrate_env_keys(env, remove=False)

        assert not migrated
        assert any("already configured" in reason for _, reason in skipped)
        # Original key preserved.
        assert store.get_provider_key("openai") == "sk-existing"

    def test_removes_from_env(self, tmp_path: Path, monkeypatch) -> None:
        env = tmp_path / ".env"
        env.write_text(
            "AGENT_PROVIDER=openai\nOPENAI_API_KEY=sk-test-123\nAGENT_MODEL=gpt-4\n",
            encoding="utf-8",
        )

        from code_agent.cli import _migrate_env_keys

        store = FakeCredentialStore()
        monkeypatch.setattr("code_agent.cli.CredentialStore", lambda: store)
        monkeypatch.setattr("code_agent.cli.typer.confirm", lambda _msg: True)

        migrated, skipped = _migrate_env_keys(env, remove=True)

        assert "OpenAI" in migrated
        content = env.read_text(encoding="utf-8")
        assert "OPENAI_API_KEY" not in content
        # Non-key lines preserved.
        assert "AGENT_PROVIDER" in content
        assert "AGENT_MODEL" in content

    def test_no_keys_found(self, tmp_path: Path, monkeypatch) -> None:
        env = tmp_path / ".env"
        env.write_text("AGENT_PROVIDER=openai\n", encoding="utf-8")

        from code_agent.cli import _migrate_env_keys

        store = FakeCredentialStore()
        monkeypatch.setattr("code_agent.cli.CredentialStore", lambda: store)

        migrated, skipped = _migrate_env_keys(env, remove=False)

        assert not migrated
        assert not skipped

    def test_missing_env_file(self, tmp_path: Path) -> None:
        from code_agent.cli import _migrate_env_keys

        migrated, skipped = _migrate_env_keys(tmp_path / ".env", remove=False)

        assert not migrated
        assert not skipped

    def test_user_declines(self, tmp_path: Path, monkeypatch) -> None:
        env = tmp_path / ".env"
        env.write_text("OPENAI_API_KEY=sk-test-123\n", encoding="utf-8")

        from code_agent.cli import _migrate_env_keys

        store = FakeCredentialStore()
        monkeypatch.setattr("code_agent.cli.CredentialStore", lambda: store)
        monkeypatch.setattr("code_agent.cli.typer.confirm", lambda _msg: False)

        migrated, skipped = _migrate_env_keys(env, remove=False)

        assert not migrated
        assert any("user declined" in reason for _, reason in skipped)
        assert store.get_provider_key("openai") is None
