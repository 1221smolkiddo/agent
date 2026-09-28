from __future__ import annotations

import ipaddress
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings

from .contracts import MemoryStatus


class ExperienceMemoryConfig(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True, extra="forbid")

    enabled: bool = False
    provider: Literal["none", "hindsight"] = "hindsight"
    deployment: Literal["cloud", "self_hosted"] = "cloud"
    api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    base_url: str | None = Field(default=None, repr=False, exclude=True)
    timeout_seconds: float = Field(default=10.0, gt=0, le=120, allow_inf_nan=False)
    recall_max_results: int = Field(default=5, ge=1, le=100)
    recall_max_tokens: int = Field(default=2048, ge=1, le=16384)
    budget: Literal["low", "mid", "high"] = "low"
    automatic_recall_max_requests: int = Field(default=1, ge=0, le=1)
    automatic_recall_timeout_seconds: float = Field(default=3.0, gt=0, le=10, allow_inf_nan=False)
    automatic_recall_max_tokens: int = Field(default=1024, ge=128, le=4096)
    automatic_recall_source_facts_max_tokens: int = Field(default=256, ge=0, le=1024)
    automatic_recall_context_max_chars: int = Field(default=4000, ge=500, le=8000)

    @field_validator("api_key", mode="before")
    @classmethod
    def blank_key(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if isinstance(value, str):
            return SecretStr(value.strip()) if value.strip() else None
        return value

    @field_validator("base_url", mode="before")
    @classmethod
    def validate_url(cls, value: object) -> object:
        if value is None or value == "":
            return None
        try:
            if not isinstance(value, str) or any(char.isspace() for char in value):
                raise ValueError
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment
            ):
                raise ValueError
            _ = parsed.port
        except ValueError:
            raise ValueError("Memory base URL must be HTTP(S) without credentials or query.") from None
        return value.rstrip("/")

    @model_validator(mode="after")
    def secure_transport(self) -> ExperienceMemoryConfig:
        if self.base_url and self.base_url.startswith("http:"):
            host = urlsplit(self.base_url).hostname
            try:
                loopback = ipaddress.ip_address(host or "").is_loopback
            except ValueError:
                loopback = host == "localhost"
            if self.deployment != "self_hosted" or not loopback or self.api_key:
                raise ValueError("Plain HTTP memory requires self-hosted loopback without a key.")
        return self

    @property
    def endpoint(self) -> str | None:
        return self.base_url or (
            "https://api.hindsight.vectorize.io" if self.deployment == "cloud" else None
        )

    @property
    def availability(self) -> MemoryStatus:
        if not self.enabled or self.provider == "none":
            return MemoryStatus.DISABLED
        if not self.endpoint or (self.deployment == "cloud" and not self.api_key):
            return MemoryStatus.MISSING_CONFIG
        return MemoryStatus.OK


class ExperienceMemorySettings(BaseSettings):
    """Flat environment settings inherited by Agent47's existing Settings."""

    agent_experience_memory_enabled: bool = False
    agent_experience_memory_provider: Literal["none", "hindsight"] = "hindsight"
    agent_experience_memory_deployment: Literal["cloud", "self_hosted"] = "cloud"
    hindsight_api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    hindsight_base_url: str | None = Field(default=None, repr=False, exclude=True)
    agent_experience_memory_timeout_seconds: float = Field(
        default=10.0, gt=0, le=120, allow_inf_nan=False,
    )
    agent_experience_memory_recall_max_results: int = Field(default=5, ge=1, le=100)
    agent_experience_memory_recall_max_tokens: int = Field(default=2048, ge=1, le=16384)
    agent_experience_memory_budget: Literal["low", "mid", "high"] = "low"
    agent_experience_memory_automatic_recall_max_requests: int = Field(default=1, ge=0, le=1)
    agent_experience_memory_automatic_recall_timeout_seconds: float = Field(
        default=3.0, gt=0, le=10, allow_inf_nan=False,
    )
    agent_experience_memory_automatic_recall_max_tokens: int = Field(default=1024, ge=128, le=4096)
    agent_experience_memory_automatic_recall_source_facts_max_tokens: int = Field(default=256, ge=0, le=1024)
    agent_experience_memory_automatic_recall_context_max_chars: int = Field(default=4000, ge=500, le=8000)

    @field_validator("hindsight_api_key", mode="before")
    @classmethod
    def blank_memory_key(cls, value: object) -> object:
        return ExperienceMemoryConfig.blank_key(value)

    @field_validator("hindsight_base_url", mode="before")
    @classmethod
    def memory_url(cls, value: object) -> object:
        return ExperienceMemoryConfig.validate_url(value)

    @model_validator(mode="after")
    def validate_memory_settings(self) -> ExperienceMemorySettings:
        self.experience_memory_config
        return self

    @property
    def experience_memory_config(self) -> ExperienceMemoryConfig:
        return ExperienceMemoryConfig(
            enabled=self.agent_experience_memory_enabled,
            provider=self.agent_experience_memory_provider,
            deployment=self.agent_experience_memory_deployment,
            api_key=self.hindsight_api_key,
            base_url=self.hindsight_base_url,
            timeout_seconds=self.agent_experience_memory_timeout_seconds,
            recall_max_results=self.agent_experience_memory_recall_max_results,
            recall_max_tokens=self.agent_experience_memory_recall_max_tokens,
            budget=self.agent_experience_memory_budget,
            automatic_recall_max_requests=self.agent_experience_memory_automatic_recall_max_requests,
            automatic_recall_timeout_seconds=self.agent_experience_memory_automatic_recall_timeout_seconds,
            automatic_recall_max_tokens=self.agent_experience_memory_automatic_recall_max_tokens,
            automatic_recall_source_facts_max_tokens=(
                self.agent_experience_memory_automatic_recall_source_facts_max_tokens
            ),
            automatic_recall_context_max_chars=self.agent_experience_memory_automatic_recall_context_max_chars,
        )
