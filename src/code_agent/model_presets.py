from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ProviderName = Literal["openrouter", "openai", "gemini", "deepseek"]


@dataclass(frozen=True)
class ModelPreset:
    name: str
    provider: ProviderName
    model: str
    description: str
    required_key: str


MODEL_PRESETS: dict[str, ModelPreset] = {
    "qwen-coder": ModelPreset(
        name="qwen-coder",
        provider="openrouter",
        model="qwen/qwen3-coder",
        description="Default OpenRouter coding model.",
        required_key="OPENROUTER_API_KEY",
    ),
    "gemini-flash": ModelPreset(
        name="gemini-flash",
        provider="gemini",
        model="gemini-3.5-flash",
        description="Fast Gemini model for agentic and coding tasks.",
        required_key="GEMINI_API_KEY",
    ),
    "gemini-pro": ModelPreset(
        name="gemini-pro",
        provider="gemini",
        model="gemini-3.1-pro",
        description="Higher-capability Gemini model for deeper coding work.",
        required_key="GEMINI_API_KEY",
    ),
    "deepseek-flash": ModelPreset(
        name="deepseek-flash",
        provider="deepseek",
        model="deepseek-v4-flash",
        description="Fast DeepSeek model with OpenAI-compatible access.",
        required_key="DEEPSEEK_API_KEY",
    ),
    "deepseek-pro": ModelPreset(
        name="deepseek-pro",
        provider="deepseek",
        model="deepseek-v4-pro",
        description="DeepSeek pro model for larger coding and reasoning tasks.",
        required_key="DEEPSEEK_API_KEY",
    ),
}


def preset_names() -> tuple[str, ...]:
    return tuple(MODEL_PRESETS)


def resolve_model_preset(name: str | None) -> ModelPreset | None:
    if not name:
        return None
    normalized = name.strip().lower()
    try:
        return MODEL_PRESETS[normalized]
    except KeyError as exc:
        allowed = ", ".join(preset_names())
        raise ValueError(f"Unknown model preset: {name}. Expected one of: {allowed}.") from exc


def format_model_presets() -> str:
    lines = ["Available model presets:", ""]
    for preset in MODEL_PRESETS.values():
        lines.append(f"- {preset.name}: provider={preset.provider}, model={preset.model}")
        lines.append(f"{preset.description}")
        lines.append("")
    return "\n".join(lines).rstrip()
