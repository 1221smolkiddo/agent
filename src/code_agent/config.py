from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    openai_api_key: str
    openai_base_url: str = "https://api.openai.com/v1"
    agent_model: str = "gpt-4o-mini"
    agent_db_path: Path = Path(".code-agent/agent.db")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
