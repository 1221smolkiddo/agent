from pathlib import Path

import pytest

from code_agent.config import Settings
from code_agent.factory import create_agent
from code_agent.model_profiles import resolve_model_profile, validate_profile_name


def test_resolve_model_profile_uses_named_profile_override() -> None:
    profile = resolve_model_profile(
        "planner",
        default_model="default-model",
        max_tokens=4096,
        planner_model="planner-model",
    )

    assert profile.name == "planner"
    assert profile.model == "planner-model"
    assert profile.temperature == 0.25
    assert profile.max_tokens == 4096


def test_fast_profile_respects_explicit_output_override() -> None:
    profile = resolve_model_profile("fast", default_model="default-model", max_tokens=4096)

    assert profile.name == "fast"
    assert profile.model == "default-model"
    assert profile.max_tokens == 4096


def test_validate_profile_name_rejects_unknown_profile() -> None:
    with pytest.raises(ValueError, match="Unknown model profile"):
        validate_profile_name("chaos")


def test_create_agent_uses_profile_model_from_settings(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        openrouter_api_key="test-key",
        agent_model_preset=None,
        agent_model="default-model",
        agent_coder_model="coder-model",
        agent_db_path=tmp_path / "agent.db",
    )

    agent = create_agent(
        settings=settings,
        cwd=tmp_path,
        model=None,
        profile="coder",
        dry_run=True,
        max_steps=1,
    )

    assert agent.model_client.model == "coder-model"


def test_create_agent_model_override_wins_over_profile_specific_model(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        openrouter_api_key="test-key",
        agent_model_preset=None,
        agent_model="default-model",
        agent_coder_model="coder-model",
        agent_db_path=tmp_path / "agent.db",
    )

    agent = create_agent(
        settings=settings,
        cwd=tmp_path,
        model="explicit-model",
        profile="coder",
        dry_run=True,
        max_steps=1,
    )

    assert agent.model_client.model == "explicit-model"


def test_profile_without_output_override_uses_provider_default():
    profile = resolve_model_profile("default", default_model="model")
    assert profile.max_tokens is None
    assert profile.request_max_output_tokens is None
