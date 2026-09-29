from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from typing import Literal


ProviderName = Literal["openrouter", "openai", "gemini", "deepseek", "nvidia", "compatible"]

PROVIDER_NAMES: tuple[ProviderName, ...] = (
    "openrouter",
    "openai",
    "gemini",
    "deepseek",
    "nvidia",
    "compatible",
)


@dataclass(frozen=True)
class ModelCapabilities:
    chat_completions: bool = True
    streaming: bool = True
    json_actions: bool = True
    token_usage: bool = True
    stream_usage: bool = True


@dataclass(frozen=True)
class ModelRuntimeDefaults:
    max_tokens: int | None = None
    temperature: float | None = None
    timeout_seconds: float | None = None
    credit_retry_count: int = 3
    min_viable_tokens: int = 64
    extra_body: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RegisteredModel:
    name: str
    provider: ProviderName
    model: str
    description: str
    required_key: str
    capabilities: ModelCapabilities
    context_window: str = "unknown"
    context_window_tokens: int | None = None
    quality: int = 3
    speed: int = 3
    cost: int = 3
    reasoning: int = 3
    runtime: ModelRuntimeDefaults = field(default_factory=ModelRuntimeDefaults)


DEFAULT_CAPABILITIES = ModelCapabilities()

REGISTERED_MODELS: dict[str, RegisteredModel] = {
    "qwen-coder": RegisteredModel(
        name="qwen-coder",
        provider="openrouter",
        model="qwen/qwen3-coder",
        description="Default OpenRouter coding model.",
        required_key="OPENROUTER_API_KEY",
        capabilities=DEFAULT_CAPABILITIES,
        context_window="varies",
        quality=4,
        speed=4,
        cost=4,
        reasoning=4,
        runtime=ModelRuntimeDefaults(max_tokens=4096),
    ),
    "gemini-flash": RegisteredModel(
        name="gemini-flash",
        provider="gemini",
        model="gemini-3.5-flash",
        description="Fast Gemini model for agentic and coding tasks.",
        required_key="GEMINI_API_KEY",
        capabilities=DEFAULT_CAPABILITIES,
        context_window="large",
        quality=4,
        speed=5,
        cost=4,
        reasoning=3,
        runtime=ModelRuntimeDefaults(max_tokens=4096),
    ),
    "gemini-pro": RegisteredModel(
        name="gemini-pro",
        provider="gemini",
        model="gemini-3.1-pro-preview",
        description="Higher-capability Gemini model for deeper coding work.",
        required_key="GEMINI_API_KEY",
        capabilities=DEFAULT_CAPABILITIES,
        context_window="large",
        quality=5,
        speed=3,
        cost=3,
        reasoning=5,
        runtime=ModelRuntimeDefaults(max_tokens=8192, temperature=0.2),
    ),
    "deepseek-flash": RegisteredModel(
        name="deepseek-flash",
        provider="deepseek",
        model="deepseek-v4-flash",
        description="Fast DeepSeek model with OpenAI-compatible access.",
        required_key="DEEPSEEK_API_KEY",
        capabilities=DEFAULT_CAPABILITIES,
        context_window="large",
        quality=4,
        speed=5,
        cost=5,
        reasoning=4,
        runtime=ModelRuntimeDefaults(max_tokens=4096),
    ),
    "deepseek-pro": RegisteredModel(
        name="deepseek-pro",
        provider="deepseek",
        model="deepseek-v4-pro",
        description="DeepSeek pro model for larger coding and reasoning tasks.",
        required_key="DEEPSEEK_API_KEY",
        capabilities=DEFAULT_CAPABILITIES,
        context_window="large",
        quality=5,
        speed=3,
        cost=5,
        reasoning=5,
        runtime=ModelRuntimeDefaults(max_tokens=8192, temperature=0.2),
    ),
    "glm-5.2": RegisteredModel(
        name="glm-5.2",
        provider="nvidia",
        model="z-ai/glm-5.2",
        description="Z.ai GLM-5.2 on NVIDIA NIM for agentic coding and long-horizon reasoning.",
        required_key="NVIDIA_API_KEY",
        capabilities=ModelCapabilities(stream_usage=False),
        context_window="large",
        quality=4,
        speed=5,
        cost=5,
        reasoning=4,
        runtime=ModelRuntimeDefaults(max_tokens=8192, temperature=0.2),
    ),
}


def provider_names() -> tuple[ProviderName, ...]:
    return PROVIDER_NAMES


def provider_name_list() -> str:
    return ", ".join(PROVIDER_NAMES)


def registered_model_names() -> tuple[str, ...]:
    return tuple(REGISTERED_MODELS)


def find_registered_model(model: str) -> RegisteredModel | None:
    normalized = model.strip()
    for item in REGISTERED_MODELS.values():
        if item.model == normalized:
            return item
    return None


def validate_provider_name(value: str) -> ProviderName:
    normalized = value.strip().lower()
    if normalized in PROVIDER_NAMES:
        return normalized  # type: ignore[return-value]
    raise ValueError(f"Expected one of: {provider_name_list()}.")


def validate_model_selection(
    *,
    provider: str,
    model: str,
    preset_name: str | None = None,
    stream: bool = True,
) -> None:
    if not model.strip():
        raise RuntimeError("Model id must not be empty.")

    normalized_provider = validate_provider_name(provider)
    preset = REGISTERED_MODELS.get(preset_name or "")
    if preset and preset.provider != normalized_provider:
        raise RuntimeError(
            f"Preset {preset.name} requires provider={preset.provider}; "
            f"got provider={normalized_provider}."
        )

    registered = find_registered_model(model)
    if registered and registered.provider != normalized_provider:
        raise RuntimeError(
            f"Model {registered.model} is registered for provider={registered.provider}; "
            f"got provider={normalized_provider}. Set AGENT_PROVIDER={registered.provider} "
            f"or use AGENT_MODEL_PRESET={registered.name}."
        )
    capabilities = registered.capabilities if registered else DEFAULT_CAPABILITIES
    if not capabilities.chat_completions:
        raise RuntimeError(f"Model {model} does not support chat completions.")
    if stream and not capabilities.streaming:
        raise RuntimeError(f"Model {model} does not support streaming.")
    if not capabilities.json_actions:
        raise RuntimeError(f"Model {model} is not marked as supporting JSON action responses.")
