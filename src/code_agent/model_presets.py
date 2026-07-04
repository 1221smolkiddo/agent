from __future__ import annotations

from dataclasses import dataclass

from .model_registry import ProviderName, REGISTERED_MODELS


@dataclass(frozen=True)
class ModelPreset:
    name: str
    provider: ProviderName
    model: str
    description: str
    required_key: str


MODEL_PRESETS: dict[str, ModelPreset] = {
    name: ModelPreset(
        name=model.name,
        provider=model.provider,
        model=model.model,
        description=model.description,
        required_key=model.required_key,
    )
    for name, model in REGISTERED_MODELS.items()
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
