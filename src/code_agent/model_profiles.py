from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ModelProfileName = Literal["default", "planner", "coder", "reviewer", "fast"]


@dataclass(frozen=True)
class ModelProfile:
    name: ModelProfileName
    model: str
    temperature: float
    max_tokens: int
    purpose: str
    context_window_tokens: int | None = None


PROFILE_PURPOSES: dict[ModelProfileName, str] = {
    "default": "Balanced coding-agent work.",
    "planner": "Deeper planning, decomposition, and tool-selection reasoning.",
    "coder": "Careful implementation and code-editing work.",
    "reviewer": "Bug, regression, safety, and test-risk review.",
    "fast": "Low-latency answers and small tasks.",
}


def profile_names() -> tuple[ModelProfileName, ...]:
    return ("default", "planner", "coder", "reviewer", "fast")


def validate_profile_name(value: str) -> ModelProfileName:
    normalized = value.strip().lower()
    if normalized in profile_names():
        return normalized  # type: ignore[return-value]
    allowed = ", ".join(profile_names())
    raise ValueError(f"Unknown model profile: {value}. Expected one of: {allowed}.")


def resolve_model_profile(
    profile: str | None,
    *,
    default_model: str,
    max_tokens: int,
    planner_model: str | None = None,
    coder_model: str | None = None,
    reviewer_model: str | None = None,
    fast_model: str | None = None,
) -> ModelProfile:
    name = validate_profile_name(profile or "default")
    model_by_profile: dict[ModelProfileName, str] = {
        "default": default_model,
        "planner": planner_model or default_model,
        "coder": coder_model or default_model,
        "reviewer": reviewer_model or default_model,
        "fast": fast_model or default_model,
    }
    temperature_by_profile: dict[ModelProfileName, float] = {
        "default": 0.2,
        "planner": 0.25,
        "coder": 0.15,
        "reviewer": 0.1,
        "fast": 0.2,
    }
    token_by_profile: dict[ModelProfileName, int] = {
        "default": max_tokens,
        "planner": max_tokens,
        "coder": max_tokens,
        "reviewer": max_tokens,
        "fast": min(max_tokens, 2048),
    }
    return ModelProfile(
        name=name,
        model=model_by_profile[name],
        temperature=temperature_by_profile[name],
        max_tokens=token_by_profile[name],
        purpose=PROFILE_PURPOSES[name],
    )
