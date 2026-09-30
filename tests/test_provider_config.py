from __future__ import annotations

import pytest
from typer.testing import CliRunner

from code_agent.cli import app
from code_agent.config import Settings
from code_agent.factory import create_agent, create_chat_client
from code_agent.model_presets import format_model_presets, resolve_model_preset
from code_agent.credentials.keyring import KeyringUnavailableError

@pytest.fixture(autouse=True)
def _mock_os_keyring(monkeypatch):
    """Ensure OS keyring doesn't bleed into provider config tests."""
    def raise_keyring_error(*args, **kwargs):
        raise KeyringUnavailableError("Mocked keyring")
    monkeypatch.setattr(
        "code_agent.credentials.keyring.CredentialStore._backend", raise_keyring_error
    )


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


def test_openai_fallback_still_works_when_openrouter_key_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "code_agent.credentials.keyring.CredentialStore.get_provider_key", lambda self, p: None
    )
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


def test_gemini_provider_uses_openai_compatible_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        "code_agent.credentials.keyring.CredentialStore.get_provider_key", lambda self, p: None
    )
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


def test_missing_provider_key_has_targeted_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "code_agent.credentials.keyring.CredentialStore.get_provider_key", lambda self, p: None
    )
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


def test_create_agent_applies_context_budget(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        openrouter_api_key="test-key",
        agent_model_preset=None,
        agent_context_max_chars=24_000,
        agent_reviewer_pass=False,
        agent_db_path=tmp_path / "agent.db",
    )

    agent = create_agent(settings=settings, cwd=tmp_path, model=None, dry_run=True, max_steps=1)

    assert agent.context_max_chars == 24_000


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
        agent_fallback_models="",
        agent_reviewer_pass=False,
        agent_db_path=tmp_path / "agent.db",
    )

    with pytest.warns(RuntimeWarning, match="no fallback model configured"):
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
        agent_fallback_models="",
        agent_reviewer_pass=False,
        agent_db_path=tmp_path / "agent.db",
    )

    with pytest.warns(RuntimeWarning, match="no fallback model configured"):
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


def test_preset_missing_key_has_targeted_error(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        "code_agent.credentials.keyring.CredentialStore.get_provider_key", lambda self, p: None
    )
    settings = Settings(
        _env_file=None,
        openrouter_api_key="router-key",
        gemini_api_key=None,
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


def test_registered_fallback_missing_key_has_targeted_error(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    settings = Settings(
        _env_file=None,
        agent_model_preset="gemini-flash",
        gemini_api_key="gemini-key",
        nvidia_api_key=None,
        agent_fallback_models="z-ai/glm-5.2",
        agent_reviewer_pass=False,
        agent_db_path=tmp_path / "agent.db",
    )

    with pytest.raises(RuntimeError, match="NVIDIA_API_KEY is required"):
        create_agent(
            settings=settings,
            cwd=tmp_path,
            model=None,
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
    assert "gemini-pro: provider=gemini, model=gemini-3.1-pro-preview" in result.output
    assert "deepseek-pro" in result.output
    assert "glm-5.2" in result.output
    assert "- deepseek-v4-flash:" not in result.output
    assert "key=" not in result.output
    assert "Available model presets:\n\n- qwen-coder: provider=openrouter, model=qwen/qwen3-coder\nDefault OpenRouter coding model." in result.output


def test_context_capacity_priority_and_optional_step_configuration(tmp_path) -> None:
    from types import SimpleNamespace

    from code_agent.factory import resolve_context_window
    from code_agent.model_profiles import ModelProfile

    settings = Settings(
        _env_file=None,
        agent_context_window_tokens=24_000,
        agent_max_steps=100,
        agent_model_preset=None,
        openrouter_api_key="test-key",
        agent_fallback_models="",
        agent_reviewer_pass=False,
        agent_db_path=tmp_path / "agent.db",
    )
    profile = ModelProfile(
        name="default", model="custom", temperature=0.2,
        max_tokens=4096, purpose="test", context_window_tokens=48_000,
    )
    registered = SimpleNamespace(context_window_tokens=32_000)
    assert resolve_context_window(profile, registered, settings) == 48_000
    assert resolve_context_window(None, registered, settings) == 32_000
    assert resolve_context_window(None, None, settings) == 24_000
    assert resolve_context_window(
        None, None, settings.model_copy(update={"agent_context_window_tokens": None}),
    ) == 65_536

    configured = create_agent(
        settings=settings, cwd=tmp_path, model=None, dry_run=True, max_steps=None,
    )
    unlimited = create_agent(
        settings=settings, cwd=tmp_path, model=None, dry_run=True, max_steps=0,
    )
    assert configured.max_steps == 100
    assert unlimited.max_steps is None


def test_parent_dotenv_is_not_inherited_by_child_workspace(tmp_path, monkeypatch):
    parent = tmp_path / "parent"
    child = parent / "project"
    child.mkdir(parents=True)
    (parent / ".env").write_text(
        "AGENT_MODEL_TIMEOUT_SECONDS=20\nAGENT_CONTEXT_MAX_CHARS=60000\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AGENT_MODEL_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("AGENT_CONTEXT_MAX_CHARS", raising=False)
    monkeypatch.chdir(child)
    settings = Settings()
    assert settings.agent_model_timeout_seconds == 180
    assert settings.agent_context_max_chars is None


def test_workspace_dotenv_and_process_precedence(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / ".env").write_text(
        "AGENT_MODEL_TIMEOUT_SECONDS=90\nAGENT_CONTEXT_MAX_CHARS=75000\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AGENT_MODEL_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("AGENT_CONTEXT_MAX_CHARS", raising=False)
    settings = Settings.for_workspace(workspace)
    assert settings.agent_model_timeout_seconds == 90
    assert settings.agent_context_max_chars == 75000
    monkeypatch.setenv("AGENT_MODEL_TIMEOUT_SECONDS", "240")
    assert Settings.for_workspace(workspace).agent_model_timeout_seconds == 240
    explicit = tmp_path / "other.env"
    explicit.write_text("AGENT_CONTEXT_MAX_CHARS=81000\n", encoding="utf-8")
    assert Settings.for_workspace(workspace, env_file=explicit).agent_context_max_chars == 81000


def test_output_limit_is_independent_from_context_reserve(tmp_path):
    settings = Settings(
        _env_file=None, openrouter_api_key="test-key",
        agent_request_max_output_tokens=None,
        agent_response_reserve_tokens=6144,
        agent_reviewer_pass=False, agent_db_path=tmp_path / "agent.db",
    )
    from code_agent.factory import create_chat_client
    client = create_chat_client(settings)
    assert client.request_max_output_tokens is None
    assert client.response_reserve_tokens == 6144


def test_explicit_output_limit_does_not_change_context_reserve(tmp_path):
    settings = Settings(
        _env_file=None, openrouter_api_key="test-key",
        agent_request_max_output_tokens=8192,
        agent_response_reserve_tokens=2048,
        agent_reviewer_pass=False, agent_db_path=tmp_path / "agent.db",
    )
    from code_agent.factory import create_chat_client
    client = create_chat_client(settings)
    assert client.request_max_output_tokens == 8192
    assert client.response_reserve_tokens == 2048


def test_blank_optional_workspace_settings_mean_no_override(tmp_path, monkeypatch):
    keys = (
        "AGENT_MAX_TOKENS", "AGENT_REQUEST_MAX_OUTPUT_TOKENS",
        "AGENT_RESPONSE_RESERVE_TOKENS", "AGENT_MAX_STEPS",
        "AGENT_CONTEXT_WINDOW_TOKENS", "AGENT_CONTEXT_MAX_CHARS",
    )
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(
        "\n".join(f"{key}=" for key in keys), encoding="utf-8",
    )
    settings = Settings.for_workspace(tmp_path)
    assert settings.request_max_output_tokens is None
    assert settings.agent_response_reserve_tokens is None
    assert settings.agent_context_max_chars is None
    assert settings.agent_context_window_tokens is None
    assert settings.agent_max_steps is None


def test_cli_selected_workspace_uses_its_own_dotenv(tmp_path, monkeypatch):
    import code_agent.cli as cli
    from code_agent.agent import AgentRunResult

    invocation = tmp_path / "invocation"
    workspace = invocation / "project"
    workspace.mkdir(parents=True)
    (invocation / ".env").write_text("AGENT_MODEL_TIMEOUT_SECONDS=20\n", encoding="utf-8")
    (workspace / ".env").write_text("AGENT_MODEL_TIMEOUT_SECONDS=90\n", encoding="utf-8")
    monkeypatch.chdir(invocation)
    monkeypatch.delenv("AGENT_MODEL_TIMEOUT_SECONDS", raising=False)
    seen = []
    class Agent:
        def run_detailed(self, task):
            return AgentRunResult(message="done", run_id=1, task=task)
    def create_agent(**kwargs):
        seen.append(kwargs["settings"].agent_model_timeout_seconds)
        return Agent()
    monkeypatch.setattr(cli, "create_agent", create_agent)
    result = CliRunner().invoke(app, ["run-json", "--cwd", str(workspace), "--dry-run", "inspect"])
    assert result.exit_code == 0, result.output
    assert seen == [90]
