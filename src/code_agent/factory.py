from __future__ import annotations

from pathlib import Path
from collections.abc import Callable

from .agent import CodingAgent
from .config import Settings
from .model_profiles import ModelProfile, resolve_model_profile
from .model_presets import resolve_model_preset
from .model_registry import (
    DEFAULT_CAPABILITIES,
    ModelRuntimeDefaults,
    find_registered_model,
    validate_model_selection,
)
from .models import (
    FallbackModelSpec,
    ModelClient,
    ModelProviderConfig,
    create_fallback_client,
    create_openai_compatible_client,
)
from .repo_index import RepoIndexCache
from .storage import AgentStorage
from .status import StatusReporter
from .tools import ToolRegistry


def create_agent(
    settings: Settings,
    cwd: Path,
    model: str | None,
    dry_run: bool,
    max_steps: int,
    max_failures: int | None = None,
    approval_callback: Callable[[str, str], bool] | None = None,
    reporter: StatusReporter | None = None,
    stream_model: bool | None = None,
    profile: str | None = None,
    provider: str | None = None,
    preset: str | None = None,
    reviewer_client: ModelClient | None = None,
    shell_network_policy: str | None = None,
) -> CodingAgent:
    workspace = cwd.resolve()
    configured_preset = None if model or provider else settings.agent_model_preset
    selected_preset = resolve_model_preset(preset if preset is not None else configured_preset)
    default_model = model or (selected_preset.model if selected_preset else settings.agent_model)
    registered_default_model = find_registered_model(default_model)
    selected_provider = (
        provider
        or (selected_preset.provider if selected_preset else None)
        or (registered_default_model.provider if registered_default_model else None)
    )
    resolved_provider_name = settings.provider_name_for(selected_provider)
    validate_model_selection(
        provider=resolved_provider_name,
        model=default_model,
        preset_name=selected_preset.name if selected_preset else None,
        stream=settings.agent_stream if stream_model is None else stream_model,
    )
    selected_profile = apply_runtime_defaults(
        resolve_model_profile(
            profile or settings.agent_profile,
            default_model=default_model,
            max_tokens=settings.agent_max_tokens,
            planner_model=None if model or selected_preset else settings.agent_planner_model,
            coder_model=None if model or selected_preset else settings.agent_coder_model,
            reviewer_model=None if model or selected_preset else settings.agent_reviewer_model,
            fast_model=None if model or selected_preset else settings.agent_fast_model,
        ),
        registered_default_model.runtime if registered_default_model else None,
    )
    provider = model_provider_config(
        settings,
        selected_provider,
        registered_default_model.capabilities.stream_usage
        if registered_default_model
        else DEFAULT_CAPABILITIES.stream_usage,
        registered_default_model.runtime if registered_default_model else None,
    )
    client = create_fallback_client(
        provider,
        selected_profile,
        fallback_model_specs(
            settings,
            settings.fallback_model_list,
            stream=settings.agent_stream if stream_model is None else stream_model,
        ),
    )
    storage = AgentStorage(settings.agent_db_path)
    index_cache = RepoIndexCache(storage.db_path)

    # Build reviewer client when the reviewer pass is enabled and no explicit
    # client was supplied (e.g. by tests). The reviewer uses its own profile
    # with a lower temperature and review-focused purpose, but shares the
    # same provider configuration.
    resolved_reviewer = reviewer_client
    if resolved_reviewer is None and settings.agent_reviewer_pass:
        reviewer_profile = resolve_model_profile(
            "reviewer",
            default_model=default_model,
            max_tokens=settings.agent_max_tokens,
            reviewer_model=None if model or selected_preset else settings.agent_reviewer_model,
        )
        reviewer_profile = apply_runtime_defaults(
            reviewer_profile,
            registered_default_model.runtime if registered_default_model else None,
        )
        resolved_reviewer = create_openai_compatible_client(provider, reviewer_profile)

    return CodingAgent(
        cwd=workspace,
        dry_run=dry_run,
        max_steps=max_steps,
        max_failures=max_failures or settings.agent_max_failures,
        model_client=client,
        tools=ToolRegistry(
            workspace=workspace,
            dry_run=dry_run,
            approval_callback=approval_callback,
            shell_network_policy=shell_network_policy or settings.shell_network_policy,
            index_cache=index_cache,
        ),
        storage=storage,
        reporter=reporter,
        stream_model=settings.agent_stream if stream_model is None else stream_model,
        reviewer_client=resolved_reviewer,
    )


def create_chat_client(
    settings: Settings,
    *,
    model: str | None = None,
    profile: str | None = None,
    provider: str | None = None,
    preset: str | None = None,
    stream_model: bool | None = None,
) -> ModelClient:
    configured_preset = None if model or provider else settings.agent_model_preset
    selected_preset = resolve_model_preset(preset if preset is not None else configured_preset)
    default_model = model or (selected_preset.model if selected_preset else settings.agent_model)
    registered_default_model = find_registered_model(default_model)
    selected_provider = (
        provider
        or (selected_preset.provider if selected_preset else None)
        or (registered_default_model.provider if registered_default_model else None)
    )
    resolved_provider_name = settings.provider_name_for(selected_provider)
    validate_model_selection(
        provider=resolved_provider_name,
        model=default_model,
        preset_name=selected_preset.name if selected_preset else None,
        stream=settings.agent_stream if stream_model is None else stream_model,
    )
    selected_profile = apply_runtime_defaults(
        resolve_model_profile(
            profile or settings.agent_profile,
            default_model=default_model,
            max_tokens=min(settings.agent_max_tokens, 1024),
        ),
        registered_default_model.runtime if registered_default_model else None,
    )
    provider_config = model_provider_config(
        settings,
        selected_provider,
        registered_default_model.capabilities.stream_usage
        if registered_default_model
        else DEFAULT_CAPABILITIES.stream_usage,
        registered_default_model.runtime if registered_default_model else None,
    )
    return create_openai_compatible_client(provider_config, selected_profile)


def model_provider_config(
    settings: Settings,
    provider: str | None,
    include_stream_usage: bool,
    runtime: ModelRuntimeDefaults | None = None,
) -> ModelProviderConfig:
    runtime = runtime or ModelRuntimeDefaults()
    resolved_provider_name = settings.provider_name_for(provider)
    return ModelProviderConfig(
        api_key=settings.model_api_key_for(provider),
        base_url=settings.model_base_url_for(provider),
        name=resolved_provider_name,
        default_headers=settings.model_headers_for(provider),
        include_stream_usage=include_stream_usage,
        timeout_seconds=runtime.timeout_seconds or settings.agent_model_timeout_seconds,
        input_cost_per_million=settings.agent_input_cost_per_million,
        output_cost_per_million=settings.agent_output_cost_per_million,
        credit_retry_count=runtime.credit_retry_count,
        min_viable_tokens=runtime.min_viable_tokens,
        extra_body=runtime.extra_body or None,
    )


def fallback_model_specs(
    settings: Settings,
    fallback_models: list[str],
    *,
    stream: bool,
) -> list[FallbackModelSpec]:
    specs: list[FallbackModelSpec] = []
    for model in fallback_models:
        registered = find_registered_model(model)
        provider = registered.provider if registered else None
        resolved_provider = settings.provider_name_for(provider)
        validate_model_selection(provider=resolved_provider, model=model, stream=stream)
        provider_config = model_provider_config(
            settings,
            provider,
            registered.capabilities.stream_usage if registered else DEFAULT_CAPABILITIES.stream_usage,
            registered.runtime if registered else None,
        )
        specs.append(
            FallbackModelSpec(
                provider=provider_config,
                model=model,
                max_tokens=registered.runtime.max_tokens if registered else None,
            )
        )
    return specs


def apply_runtime_defaults(profile: ModelProfile, runtime: ModelRuntimeDefaults | None) -> ModelProfile:
    if runtime is None:
        return profile
    max_tokens = min(profile.max_tokens, runtime.max_tokens) if runtime.max_tokens else profile.max_tokens
    temperature = runtime.temperature if runtime.temperature is not None else profile.temperature
    return profile.__class__(
        name=profile.name,
        model=profile.model,
        temperature=temperature,
        max_tokens=max_tokens,
        purpose=profile.purpose,
    )
