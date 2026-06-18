from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace
from typing import Any, Protocol

from openai import OpenAI

from .model_profiles import ModelProfile


ChatMessage = dict[str, str]


class ModelClient(Protocol):
    model: str

    def complete(self, messages: list[ChatMessage]) -> str:
        """Return the assistant message content."""


class StreamingModelClient(ModelClient, Protocol):
    def stream_complete(
        self,
        messages: list[ChatMessage],
        on_token: Callable[[str], None],
    ) -> str:
        """Stream assistant message chunks and return the full content."""


@dataclass
class ModelProviderConfig:
    api_key: str
    base_url: str
    default_headers: dict[str, str] | None = None
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None


@dataclass(frozen=True)
class ModelUsageRecord:
    model: str
    ok: bool
    provider: str = "openai-compatible"
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None
    error: str | None = None
    fallback_from: str | None = None

    def as_dict(self) -> dict[str, str | int | bool | None]:
        return {
            "provider": self.provider,
            "model": self.model,
            "ok": self.ok,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "error": self.error,
            "fallback_from": self.fallback_from,
        }


class UsageTrackingModelClient(ModelClient, Protocol):
    def drain_usage_records(self) -> list[ModelUsageRecord]:
        """Return and clear model attempt/usage records."""


@dataclass
class OpenAICompatibleChatClient:
    api_key: str
    base_url: str
    model: str
    max_tokens: int = 4096
    temperature: float = 0.2
    default_headers: dict[str, str] | None = None
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None

    def __post_init__(self) -> None:
        self._usage_records: list[ModelUsageRecord] = []
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            default_headers=self.default_headers,
        )

    def complete(self, messages: list[ChatMessage]) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        content = response.choices[0].message.content
        if not content:
            self._record_failure("Model returned an empty response.")
            raise RuntimeError("Model returned an empty response.")
        self._record_success(_usage_from_response(response))
        return content

    def stream_complete(
        self,
        messages: list[ChatMessage],
        on_token: Callable[[str], None],
    ) -> str:
        chunks: list[str] = []
        stream = self._client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
        for event in stream:
            usage = getattr(event, "usage", None)
            if usage is not None:
                self._record_success(_usage_from_object(usage))
            choices = getattr(event, "choices", []) or []
            if not choices:
                continue
            token = choices[0].delta.content or ""
            if not token:
                continue
            chunks.append(token)
            on_token(token)
        content = "".join(chunks)
        if not content:
            self._record_failure("Model returned an empty streamed response.")
            raise RuntimeError("Model returned an empty streamed response.")
        if not self._usage_records or self._usage_records[-1].ok is not True:
            self._record_success({})
        return content

    def drain_usage_records(self) -> list[ModelUsageRecord]:
        records = self._usage_records
        self._usage_records = []
        return records

    def _record_success(self, usage: dict[str, int | None]) -> None:
        self._usage_records.append(
            ModelUsageRecord(
                model=self.model,
                ok=True,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                estimated_cost_usd=self._estimate_cost(
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"),
                ),
            )
        )

    def _record_failure(self, error: str) -> None:
        self._usage_records.append(ModelUsageRecord(model=self.model, ok=False, error=error))

    def _estimate_cost(
        self,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> float | None:
        if (
            prompt_tokens is None
            or completion_tokens is None
            or self.input_cost_per_million is None
            or self.output_cost_per_million is None
        ):
            return None
        return round(
            (prompt_tokens / 1_000_000 * self.input_cost_per_million)
            + (completion_tokens / 1_000_000 * self.output_cost_per_million),
            8,
        )


class FallbackModelClient:
    def __init__(self, clients: list[UsageTrackingModelClient]) -> None:
        if not clients:
            raise ValueError("FallbackModelClient requires at least one model client.")
        self.clients = clients
        self.model = clients[0].model
        self._usage_records: list[ModelUsageRecord] = []

    def complete(self, messages: list[ChatMessage]) -> str:
        return self._try_clients(lambda client: client.complete(messages))

    def stream_complete(
        self,
        messages: list[ChatMessage],
        on_token: Callable[[str], None],
    ) -> str:
        return self._try_clients(lambda client: client.stream_complete(messages, on_token))

    def drain_usage_records(self) -> list[ModelUsageRecord]:
        records = self._usage_records
        self._usage_records = []
        return records

    def _try_clients(self, call: Callable[[UsageTrackingModelClient], str]) -> str:
        errors: list[str] = []
        previous_model: str | None = None
        for client in self.clients:
            try:
                content = call(client)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                errors.append(f"{client.model}: {error}")
                drained = client.drain_usage_records()
                if drained:
                    self._usage_records.extend(
                        ModelUsageRecord(
                            provider=record.provider,
                            model=record.model,
                            ok=record.ok,
                            prompt_tokens=record.prompt_tokens,
                            completion_tokens=record.completion_tokens,
                            total_tokens=record.total_tokens,
                            estimated_cost_usd=record.estimated_cost_usd,
                            error=record.error or error,
                            fallback_from=previous_model,
                        )
                        for record in drained
                    )
                else:
                    self._usage_records.append(
                        ModelUsageRecord(
                            model=client.model,
                            ok=False,
                            error=error,
                            fallback_from=previous_model,
                        )
                    )
                previous_model = client.model
                continue
            self.model = client.model
            records = client.drain_usage_records()
            if previous_model and records:
                latest = records[-1]
                records[-1] = ModelUsageRecord(
                    provider=latest.provider,
                    model=latest.model,
                    ok=latest.ok,
                    prompt_tokens=latest.prompt_tokens,
                    completion_tokens=latest.completion_tokens,
                    total_tokens=latest.total_tokens,
                    estimated_cost_usd=latest.estimated_cost_usd,
                    error=latest.error,
                    fallback_from=previous_model,
                )
            self._usage_records.extend(records)
            return content
        raise RuntimeError("All configured models failed. " + "; ".join(errors))


def create_openai_compatible_client(
    provider: ModelProviderConfig,
    profile: ModelProfile,
) -> OpenAICompatibleChatClient:
    return OpenAICompatibleChatClient(
        api_key=provider.api_key,
        base_url=provider.base_url,
        model=profile.model,
        max_tokens=profile.max_tokens,
        temperature=profile.temperature,
        default_headers=provider.default_headers,
        input_cost_per_million=provider.input_cost_per_million,
        output_cost_per_million=provider.output_cost_per_million,
    )


def create_fallback_client(
    provider: ModelProviderConfig,
    profile: ModelProfile,
    fallback_models: list[str],
) -> UsageTrackingModelClient:
    clients: list[UsageTrackingModelClient] = [
        create_openai_compatible_client(provider, profile),
        *[
            create_openai_compatible_client(
                provider,
                replace(profile, model=model),
            )
            for model in fallback_models
        ],
    ]
    return clients[0] if len(clients) == 1 else FallbackModelClient(clients)


def _usage_from_response(response: Any) -> dict[str, int | None]:
    return _usage_from_object(getattr(response, "usage", None))


def _usage_from_object(usage: Any) -> dict[str, int | None]:
    if usage is None:
        return {}
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


OpenAIChatClient = OpenAICompatibleChatClient
