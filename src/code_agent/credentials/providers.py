from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    display_name: str
    environment_variable: str | None


_PROVIDERS = (
    ProviderSpec("openai", "OpenAI", "OPENAI_API_KEY"),
    ProviderSpec("anthropic", "Anthropic", "ANTHROPIC_API_KEY"),
    ProviderSpec("gemini", "Google Gemini", "GEMINI_API_KEY"),
    ProviderSpec("openrouter", "OpenRouter", "OPENROUTER_API_KEY"),
    ProviderSpec("groq", "Groq", "GROQ_API_KEY"),
    ProviderSpec("deepseek", "DeepSeek", "DEEPSEEK_API_KEY"),
    ProviderSpec("nvidia", "NVIDIA NIM", "NVIDIA_API_KEY"),
    ProviderSpec("compatible", "OpenAI-Compatible", None),
)
_ALIASES = {"google": "gemini", "google-gemini": "gemini", "openai-compatible": "compatible"}


def provider_specs() -> tuple[ProviderSpec, ...]:
    return _PROVIDERS


def provider_spec(name: str) -> ProviderSpec:
    normalized = _ALIASES.get(name.strip().lower(), name.strip().lower())
    for spec in _PROVIDERS:
        if spec.name == normalized:
            return spec
    allowed = ", ".join(spec.name for spec in _PROVIDERS)
    raise ValueError(f"Unknown provider '{name}'. Expected one of: {allowed}.")


def validate_provider_key(provider: str, key: str, *, base_url: str | None = None) -> tuple[bool, str]:
    """Perform a no-cost provider authentication check without emitting the key."""
    spec = provider_spec(provider)
    endpoint_by_provider = {
        "openai": "https://api.openai.com/v1/models",
        "anthropic": "https://api.anthropic.com/v1/models",
        "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
        "openrouter": "https://openrouter.ai/api/v1/auth/key",
        "groq": "https://api.groq.com/openai/v1/models",
        "deepseek": "https://api.deepseek.com/models",
        "nvidia": "https://integrate.api.nvidia.com/v1/models",
    }
    endpoint = base_url.rstrip("/") + "/models" if base_url else endpoint_by_provider.get(spec.name)
    if not endpoint:
        return False, "OpenAI-compatible providers require --base-url."
    headers = {"Authorization": f"Bearer {key}"}
    if spec.name == "anthropic":
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    elif spec.name == "gemini":
        headers = {"x-goog-api-key": key}
    request = Request(endpoint, headers=headers)
    try:
        with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed provider endpoint or explicit user URL
            if 200 <= response.status < 300:
                return True, "provider accepted the key"
            return False, f"provider returned HTTP {response.status}"
    except HTTPError as exc:
        if exc.code in {401, 403}:
            return False, "provider rejected the key"
        return False, f"provider returned HTTP {exc.code}"
    except URLError:
        return False, "could not reach provider"
