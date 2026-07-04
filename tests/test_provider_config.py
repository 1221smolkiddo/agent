from __future__ import annotations

import pytest
from typer.testing import CliRunner

from code_agent.cli import app
from code_agent.config import Settings
from code_agent.factory import create_agent, create_chat_client
from code_agent.model_presets import format_model_presets, resolve_model_preset


def test_openrouter_is_default_provider() -> None:
    settings = Settings(
        _env_file=None,
        agent_provider="openrouter",
        agent_model_preset=None,
        openrouter_api_key="router-key",
    )

    assert settings.provider_name == "openrouter"
    assert settings.model_api_key == "router-key"
    assert settings.model_base_url == "https://openrouter.ai/api/v1"
    assert settings.model_headers == {"X-Title": "code-agent"}


def test_openai_fallback_still_works_when_openrouter_key_is_missing() -> None:
    settings = Settings(
        _env_file=None,
        agent_provider="openrouter",
        agent_model_preset=None,
        openrouter_api_key="",
        openai_api_key="openai-key",
    )

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


def test_nvidia_provider_uses_nim_openai_compatible_endpoint() -> None:
    settings = Settings(_env_file=None, agent_provider="nvidia", nvidia_api_key="nvidia-key")

    assert settings.provider_name == "nvidia"
    assert settings.model_api_key == "nvidia-key"
    assert settings.model_base_url == "https://integrate.api.nvidia.com/v1"
    assert settings.model_headers == {}


def test_missing_provider_key_has_targeted_error() -> None:
    settings = Settings(_env_file=None, agent_provider="gemini", gemini_api_key=None)

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is required"):
        _ = settings.model_api_key


def test_unknown_provider_is_rejected() -> None:
    settings = Settings(_env_file=None, agent_provider="mystery")

    with pytest.raises(RuntimeError, match="AGENT_PROVIDER"):
        _ = settings.provider_name


def test_shell_network_policy_accepts_allow_and_deny() -> None:
    assert Settings(_env_file=None, agent_shell_network="allow").shell_network_policy == "allow"
    assert Settings(_env_file=None, agent_shell_network="deny").shell_network_policy == "deny"


def test_shell_network_policy_rejects_unknown_values() -> None:
    settings = Settings(_env_file=None, agent_shell_network="maybe")

    with pytest.raises(RuntimeError, match="AGENT_SHELL_NETWORK"):
        _ = settings.shell_network_policy


def test_settings_ignores_unknown_dotenv_keys(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_PROVIDER", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("BASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AGENT_PROVIDER=nvidia\n"
        "NVIDIA_API_KEY=nvidia-key\n"
        "BASE_URL=https://integrate.api.nvidia.com/v1\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.provider_name == "nvidia"
    assert settings.model_api_key == "nvidia-key"


def test_settings_treats_blank_optional_cost_fields_as_none(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_INPUT_COST_PER_MILLION", raising=False)
    monkeypatch.delenv("AGENT_OUTPUT_COST_PER_MILLION", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AGENT_INPUT_COST_PER_MILLION=\n"
        "AGENT_OUTPUT_COST_PER_MILLION=\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.agent_input_cost_per_million is None
    assert settings.agent_output_cost_per_million is None


def test_model_preset_resolves_provider_and_model() -> None:
    preset = resolve_model_preset("glm-5.2")

    assert preset is not None
    assert preset.provider == "nvidia"
    assert preset.model == "z-ai/glm-5.2"
    assert "glm-5.2" in format_model_presets()
    assert "key=" not in format_model_presets()


def test_unknown_model_preset_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown model preset"):
        resolve_model_preset("not-real")


def test_create_agent_uses_preset_provider_and_model(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        agent_model_preset="glm-5.2",
        nvidia_api_key="nvidia-key",
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

    assert agent.model_client.model == "z-ai/glm-5.2"
    assert agent.model_client.include_stream_usage is False


def test_create_agent_passes_shell_network_policy_to_tools(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        openrouter_api_key="router-key",
        agent_shell_network="deny",
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

    assert agent.tools.shell_network_policy == "deny"


def test_model_override_keeps_preset_provider(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        agent_model_preset=None,
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


def test_registered_agent_model_infers_provider_over_agent_provider(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        agent_provider="nvidia",
        agent_model_preset=None,
        agent_model="qwen/qwen3-coder",
        openrouter_api_key="router-key",
        nvidia_api_key="nvidia-key",
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

    assert agent.model_client.model == "qwen/qwen3-coder"
    assert agent.model_client.provider_name == "openrouter"


def test_registered_chat_model_infers_provider_over_agent_provider() -> None:
    settings = Settings(
        _env_file=None,
        agent_provider="nvidia",
        agent_model_preset=None,
        agent_model="qwen/qwen3-coder",
        openrouter_api_key="router-key",
        nvidia_api_key="nvidia-key",
    )

    client = create_chat_client(settings=settings)

    assert client.model == "qwen/qwen3-coder"
    assert client.provider_name == "openrouter"


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


def test_provider_override_rejects_mismatched_preset(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        agent_model_preset=None,
        gemini_api_key="gemini-key",
        agent_reviewer_pass=False,
        agent_db_path=tmp_path / "agent.db",
    )

    with pytest.raises(RuntimeError, match="Preset glm-5.2 requires provider=nvidia"):
        create_agent(
            settings=settings,
            cwd=tmp_path,
            model=None,
            provider="gemini",
            preset="glm-5.2",
            profile=None,
            dry_run=True,
            max_steps=1,
        )


def test_registered_model_rejects_wrong_provider(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        gemini_api_key="gemini-key",
        agent_reviewer_pass=False,
        agent_db_path=tmp_path / "agent.db",
    )

    with pytest.raises(RuntimeError, match="z-ai/glm-5.2 is registered for provider=nvidia"):
        create_agent(
            settings=settings,
            cwd=tmp_path,
            model="z-ai/glm-5.2",
            provider="gemini",
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
    assert "glm-5.2" in result.output
    assert "key=" not in result.output
    assert "Available model presets:\n\n- qwen-coder: provider=openrouter, model=qwen/qwen3-coder\nDefault OpenRouter coding model." in result.output
