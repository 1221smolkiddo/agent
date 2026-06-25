from __future__ import annotations

import pytest
from typer.testing import CliRunner

from code_agent.cli import app
from code_agent.config import Settings
from code_agent.factory import create_agent
from code_agent.model_presets import format_model_presets, resolve_model_preset


def test_openrouter_is_default_provider() -> None:
    settings = Settings(_env_file=None, openrouter_api_key="router-key")

    assert settings.provider_name == "openrouter"
    assert settings.model_api_key == "router-key"
    assert settings.model_base_url == "https://openrouter.ai/api/v1"
    assert settings.model_headers == {"X-Title": "code-agent"}


def test_openai_fallback_still_works_when_openrouter_key_is_missing() -> None:
    settings = Settings(_env_file=None, openrouter_api_key="", openai_api_key="openai-key")

    assert settings.provider_name == "openai"
    assert settings.model_api_key == "openai-key"
    assert settings.model_base_url == "https://api.openai.com/v1"
    assert settings.model_headers == {}


def test_gemini_provider_uses_openai_compatible_endpoint() -> None:
    settings = Settings(_env_file=None, agent_provider="gemini", gemini_api_key="gemini-key")

    assert settings.provider_name == "gemini"
    assert settings.model_api_key == "gemini-key"
    assert settings.model_base_url == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert settings.model_headers == {}


def test_deepseek_provider_uses_openai_compatible_endpoint() -> None:
    settings = Settings(_env_file=None, agent_provider="deepseek", deepseek_api_key="deepseek-key")

    assert settings.provider_name == "deepseek"
    assert settings.model_api_key == "deepseek-key"
    assert settings.model_base_url == "https://api.deepseek.com"
    assert settings.model_headers == {}


def test_missing_provider_key_has_targeted_error() -> None:
    settings = Settings(_env_file=None, agent_provider="gemini", gemini_api_key=None)

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is required"):
        _ = settings.model_api_key


def test_unknown_provider_is_rejected() -> None:
    settings = Settings(_env_file=None, agent_provider="mystery")

    with pytest.raises(RuntimeError, match="AGENT_PROVIDER"):
        _ = settings.provider_name


def test_model_preset_resolves_provider_and_model() -> None:
    preset = resolve_model_preset("gemini-flash")

    assert preset is not None
    assert preset.provider == "gemini"
    assert preset.model == "gemini-3.5-flash"
    assert "gemini-flash" in format_model_presets()


def test_unknown_model_preset_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown model preset"):
        resolve_model_preset("not-real")


def test_create_agent_uses_preset_provider_and_model(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        agent_model_preset="deepseek-pro",
        deepseek_api_key="deepseek-key",
        agent_reviewer_pass=False,
        agent_db_path=tmp_path / "agent.db",
    )

    agent = create_agent(
        settings=settings,
        cwd=tmp_path,
        model=None,
        profile=None,
        dry_run=True,
        max_steps=1,
    )

    assert agent.model_client.model == "deepseek-v4-pro"


def test_model_override_keeps_preset_provider(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        gemini_api_key="gemini-key",
        agent_reviewer_pass=False,
        agent_db_path=tmp_path / "agent.db",
    )

    agent = create_agent(
        settings=settings,
        cwd=tmp_path,
        model="gemini-custom",
        preset="gemini-flash",
        profile=None,
        dry_run=True,
        max_steps=1,
    )

    assert agent.model_client.model == "gemini-custom"


def test_preset_missing_key_has_targeted_error(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        openrouter_api_key="router-key",
        agent_reviewer_pass=False,
        agent_db_path=tmp_path / "agent.db",
    )

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is required"):
        create_agent(
            settings=settings,
            cwd=tmp_path,
            model=None,
            preset="gemini-pro",
            profile=None,
            dry_run=True,
            max_steps=1,
        )


def test_cli_models_lists_presets() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["models"])

    assert result.exit_code == 0
    assert "gemini-flash" in result.output
    assert "deepseek-pro" in result.output
