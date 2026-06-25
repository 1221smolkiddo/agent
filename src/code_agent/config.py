from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    agent_provider: str = "openrouter"

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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def provider_name(self) -> str:
        provider = self.agent_provider.strip().lower()
        if provider == "openrouter" and not self.openrouter_api_key and self.openai_api_key:
            return "openai"
        if provider in {"openrouter", "openai", "gemini", "deepseek"}:
            return provider
        raise RuntimeError(
            "AGENT_PROVIDER must be one of: openrouter, openai, gemini, deepseek."
        )

    @property
    def model_api_key(self) -> str:
        provider = self.provider_name
        key_by_provider = {
            "openrouter": self.openrouter_api_key,
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "deepseek": self.deepseek_api_key,
        }
        key = key_by_provider[provider]
        if not key:
            env_by_provider = {
                "openrouter": "OPENROUTER_API_KEY",
                "openai": "OPENAI_API_KEY",
                "gemini": "GEMINI_API_KEY",
                "deepseek": "DEEPSEEK_API_KEY",
            }
            raise RuntimeError(
                f"{env_by_provider[provider]} is required for AGENT_PROVIDER={provider}."
            )
        return key

    @property
    def model_base_url(self) -> str:
        provider = self.provider_name
        if provider == "openrouter":
            return self.openrouter_base_url
        if provider == "openai":
            return self.openai_base_url
        if provider == "gemini":
            return self.gemini_base_url
        if provider == "deepseek":
            return self.deepseek_base_url
        raise RuntimeError(
            "AGENT_PROVIDER must be one of: openrouter, openai, gemini, deepseek."
        )

    @property
    def model_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.provider_name != "openrouter":
            return headers
        if self.openrouter_site_url:
            headers["HTTP-Referer"] = self.openrouter_site_url
        if self.openrouter_app_name:
            headers["X-Title"] = self.openrouter_app_name
        return headers

    @property
    def fallback_model_list(self) -> list[str]:
        return [item.strip() for item in self.agent_fallback_models.split(",") if item.strip()]
