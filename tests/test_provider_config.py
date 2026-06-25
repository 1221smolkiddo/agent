from __future__ import annotations

import pytest

from code_agent.config import Settings


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
