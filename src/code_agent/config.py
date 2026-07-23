from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, field_validator
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

    compatible_api_key: str | None = None
    compatible_base_url: str | None = None

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
    agent_model_timeout_seconds: float = 60.0
    agent_run_timeout_seconds: float = Field(default=300.0, ge=1, le=86_400)
    agent_model_retry_count: int = Field(default=2, ge=0, le=10)
    agent_model_retry_base_seconds: float = Field(default=0.5, ge=0, le=60)
    agent_model_retry_max_seconds: float = Field(default=4.0, ge=0, le=300)
    agent_max_failures: int = 3
    agent_context_max_chars: int = Field(default=60_000, ge=8_000, le=1_000_000)
    agent_db_path: Path = Path(".code-agent/agent.db")
    agent_execution_db_path: Path = Path(".code-agent/executions.db")
    agent_execution_mode: str = "shadow"
    agent_shadow_planner: str = "deterministic"
    agent_stream: bool = True
    agent_reviewer_pass: bool = True
    agent_shell_network: str = "deny"
    agent_sandbox_backend: str = "auto"
    agent_sandbox_image: str = "python:3.13-slim"
    agent_trust_workspace_extensions: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator(
        "agent_input_cost_per_million",
        "agent_output_cost_per_million",
        mode="before",
    )
    @classmethod
    def blank_optional_float(cls, value: object) -> object:
        if value == "":
            return None
        return value

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
        # A locally managed BYOK credential takes precedence over .env. The
        # keyring facade deliberately falls back to environment configuration
        # if the OS credential manager is not available on this machine.
        try:
            from .credentials.keyring import CredentialStore, KeyringUnavailableError

            stored_key = CredentialStore().get_provider_key(provider)
        except KeyringUnavailableError:
            stored_key = None
        if stored_key:
            return stored_key
        key_by_provider = {
            "openrouter": self.openrouter_api_key,
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "deepseek": self.deepseek_api_key,
            "nvidia": self.nvidia_api_key,
            "compatible": self.compatible_api_key,
        }
        key = key_by_provider[provider]
        if not key:
            env_by_provider = {
                "openrouter": "OPENROUTER_API_KEY",
                "openai": "OPENAI_API_KEY",
                "gemini": "GEMINI_API_KEY",
                "deepseek": "DEEPSEEK_API_KEY",
                "nvidia": "NVIDIA_API_KEY",
                "compatible": "COMPATIBLE_API_KEY",
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
        if provider == "compatible":
            if not self.compatible_base_url:
                raise RuntimeError("COMPATIBLE_BASE_URL is required for AGENT_PROVIDER=compatible.")
            return self.compatible_base_url
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

    @property
    def sandbox_backend(self) -> str:
        value = self.agent_sandbox_backend.strip().lower()
        if value in {"auto", "local", "docker", "podman", "container"}:
            return value
        raise RuntimeError(
            "AGENT_SANDBOX_BACKEND must be one of: auto, local, docker, podman, container."
        )

    @property
    def execution_mode(self) -> str:
        value = self.agent_execution_mode.strip().lower()
        if value in {"legacy", "shadow", "primary", "engine_only"}:
            return value
        raise RuntimeError(
            "AGENT_EXECUTION_MODE must be one of: legacy, shadow, primary, engine_only."
        )

    @property
    def shadow_planner(self) -> str:
        value = self.agent_shadow_planner.strip().lower()
        if value in {"deterministic", "model"}:
            return value
        raise RuntimeError(
            "AGENT_SHADOW_PLANNER must be one of: deterministic, model."
        )
