from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from .model_registry import provider_name_list, provider_names

load_dotenv()


class Settings(BaseSettings):
    agent_provider: str = "openrouter"
    agent_model_preset: str | None = None

    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_site_url: str | None = None
    openrouter_app_name: str = "code-agent"

    # Backward-compatible fallback while the project migrates to OpenRouter.
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"

    gemini_api_key: str | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"

    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"

    nvidia_api_key: str | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"

    agent_model: str = "qwen/qwen3-coder"
    agent_profile: str = "default"
    agent_planner_model: str | None = None
    agent_coder_model: str | None = None
    agent_reviewer_model: str | None = None
    agent_fast_model: str | None = None
    agent_fallback_models: str = ""
    agent_input_cost_per_million: float | None = None
    agent_output_cost_per_million: float | None = None
    agent_max_tokens: int = 4096
    agent_max_failures: int = 3
    agent_db_path: Path = Path(".code-agent/agent.db")
    agent_stream: bool = True
    agent_reviewer_pass: bool = True
    agent_shell_network: str = "allow"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def provider_name(self) -> str:
        return self.provider_name_for(None)

    def provider_name_for(self, provider_override: str | None) -> str:
        provider = (provider_override or self.agent_provider).strip().lower()
        if provider == "openrouter" and not self.openrouter_api_key and self.openai_api_key:
            return "openai"
        if provider in provider_names():
            return provider
        raise RuntimeError(f"AGENT_PROVIDER must be one of: {provider_name_list()}.")

    @property
    def model_api_key(self) -> str:
        return self.model_api_key_for(None)

    def model_api_key_for(self, provider_override: str | None) -> str:
        provider = self.provider_name_for(provider_override)
        key_by_provider = {
            "openrouter": self.openrouter_api_key,
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "deepseek": self.deepseek_api_key,
            "nvidia": self.nvidia_api_key,
        }
        key = key_by_provider[provider]
        if not key:
            env_by_provider = {
                "openrouter": "OPENROUTER_API_KEY",
                "openai": "OPENAI_API_KEY",
                "gemini": "GEMINI_API_KEY",
                "deepseek": "DEEPSEEK_API_KEY",
                "nvidia": "NVIDIA_API_KEY",
            }
            raise RuntimeError(
                f"{env_by_provider[provider]} is required for AGENT_PROVIDER={provider}."
            )
        return key

    @property
    def model_base_url(self) -> str:
        return self.model_base_url_for(None)

    def model_base_url_for(self, provider_override: str | None) -> str:
        provider = self.provider_name_for(provider_override)
        if provider == "openrouter":
            return self.openrouter_base_url
        if provider == "openai":
            return self.openai_base_url
        if provider == "gemini":
            return self.gemini_base_url
        if provider == "deepseek":
            return self.deepseek_base_url
        if provider == "nvidia":
            return self.nvidia_base_url
        raise RuntimeError(f"AGENT_PROVIDER must be one of: {provider_name_list()}.")

    @property
    def model_headers(self) -> dict[str, str]:
        return self.model_headers_for(None)

    def model_headers_for(self, provider_override: str | None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.provider_name_for(provider_override) != "openrouter":
            return headers
        if self.openrouter_site_url:
            headers["HTTP-Referer"] = self.openrouter_site_url
        if self.openrouter_app_name:
            headers["X-Title"] = self.openrouter_app_name
        return headers

    @property
    def fallback_model_list(self) -> list[str]:
        return [item.strip() for item in self.agent_fallback_models.split(",") if item.strip()]

    @property
    def shell_network_policy(self) -> str:
        value = self.agent_shell_network.strip().lower()
        if value in {"allow", "deny"}:
            return value
        raise RuntimeError("AGENT_SHELL_NETWORK must be one of: allow, deny.")
