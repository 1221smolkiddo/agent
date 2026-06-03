from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_site_url: str | None = None
    openrouter_app_name: str = "code-agent"

    # Backward-compatible fallback while the project migrates to OpenRouter.
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"

    agent_model: str = "qwen/qwen3-coder"
    agent_max_tokens: int = 4096
    agent_db_path: Path = Path(".code-agent/agent.db")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def model_api_key(self) -> str:
        key = self.openrouter_api_key or self.openai_api_key
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is required. OPENAI_API_KEY is still accepted as a fallback.")
        return key

    @property
    def model_base_url(self) -> str:
        if self.openrouter_api_key:
            return self.openrouter_base_url
        return self.openai_base_url

    @property
    def model_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.openrouter_site_url:
            headers["HTTP-Referer"] = self.openrouter_site_url
        if self.openrouter_app_name:
            headers["X-Title"] = self.openrouter_app_name
        return headers
