from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace
import re
import time
from typing import Any, Literal, Protocol, TypeVar

import openai
from openai import OpenAI

from .model_profiles import ModelProfile
from .safety import redact_secrets
from .context_budget import bound_messages


ChatMessage = dict[str, str]
T = TypeVar("T")


class InsufficientCreditsError(RuntimeError):
    """Raised when the provider returns 402 and retries with reduced tokens are exhausted."""


ModelErrorKind = Literal[
    "rate_limit",
    "capacity",
    "credits",
    "timeout",
    "server",
    "connection",
    "auth",
    "bad_request",
    "not_found",
    "empty_response",
    "unknown",
]


@dataclass(frozen=True)
class ClassifiedModelError:
    kind: ModelErrorKind
    retryable: bool
    fallbackable: bool
    message: str


class ModelClient(Protocol):
    model: str

    def complete(self, messages: list[ChatMessage]) -> str:
        """Return the assistant message content."""

    def cancel(self, reason: str = "user stop") -> int:
        """Cancel any in-flight model request if the client supports it."""


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
    name: str = "openai-compatible"
    default_headers: dict[str, str] | None = None
    include_stream_usage: bool = True
    context_window_tokens: int = 65_536
    timeout_seconds: float = 180.0
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    credit_retry_count: int = 3
    transient_retry_count: int = 2
    retry_base_delay_seconds: float = 0.5
    retry_max_delay_seconds: float = 4.0
    min_viable_tokens: int = 64
    extra_body: dict[str, Any] | None = None


@dataclass(frozen=True)
class FallbackModelSpec:
    provider: ModelProviderConfig
    model: str
    max_tokens: int | None = None


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
    provider_name: str = "openai-compatible"
    include_stream_usage: bool = True
    context_window_tokens: int = 65_536
    timeout_seconds: float = 180.0
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    credit_retry_count: int = 3
    transient_retry_count: int = 2
    retry_base_delay_seconds: float = 0.5
    retry_max_delay_seconds: float = 4.0
    min_viable_tokens: int = 64
    extra_body: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.credit_retry_count < 0 or self.transient_retry_count < 0:
            raise ValueError("Model retry counts must be zero or greater.")
        if self.retry_base_delay_seconds < 0 or self.retry_max_delay_seconds < 0:
            raise ValueError("Model retry delays must be zero or greater.")
        self._usage_records: list[ModelUsageRecord] = []
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            default_headers=self.default_headers,
            timeout=self.timeout_seconds,
            # Agent47 owns retry policy. SDK retries would multiply the configured
            # attempts and make a logical turn timeout last several minutes.
            max_retries=0,
        )

    def complete(self, messages: list[ChatMessage]) -> str:
        return self.complete_with_timeout(messages, self.timeout_seconds)

    def complete_with_timeout(self, messages: list[ChatMessage], timeout_seconds: float) -> str:
        messages, _ = bound_messages(messages, max_chars=self.context_window_tokens,
                                     window_tokens=self.context_window_tokens, output_tokens=self.max_tokens)
        def _make_request(current_max_tokens: int, remaining_seconds: float) -> str:
            request: dict[str, Any] = {
                "model": self.model,
                "messages": [{**message, "content": redact_secrets(message["content"])} for message in messages],
                "temperature": self.temperature,
                "max_tokens": current_max_tokens,
                "timeout": remaining_seconds,
            }
            if self.extra_body:
                request["extra_body"] = self.extra_body
            response = self._client.chat.completions.create(**request)  # type: ignore[arg-type]
            content = response.choices[0].message.content
            if not content:
                self._record_failure("Model returned an empty response.")
                raise RuntimeError("Model returned an empty response.")
            self._record_success(_usage_from_response(response))
            return content

        return self._call_with_retry(_make_request, timeout_seconds=timeout_seconds)

    def stream_complete(
        self,
        messages: list[ChatMessage],
        on_token: Callable[[str], None],
    ) -> str:
        return self.stream_complete_with_timeout(messages, on_token, self.timeout_seconds)

    def stream_complete_with_timeout(
        self,
        messages: list[ChatMessage],
        on_token: Callable[[str], None],
        timeout_seconds: float,
    ) -> str:
        messages, _ = bound_messages(messages, max_chars=self.context_window_tokens,
                                     window_tokens=self.context_window_tokens, output_tokens=self.max_tokens)
        def _make_request(current_max_tokens: int, remaining_seconds: float) -> str:
            chunks: list[str] = []
            request: dict[str, Any] = {
                "model": self.model,
                "messages": [{**message, "content": redact_secrets(message["content"])} for message in messages],
                "temperature": self.temperature,
                "max_tokens": current_max_tokens,
                "stream": True,
                "timeout": remaining_seconds,
            }
            if self.include_stream_usage:
                request["stream_options"] = {"include_usage": True}
            if self.extra_body:
                request["extra_body"] = self.extra_body
            stream_deadline = time.monotonic() + remaining_seconds
            stream = self._client.chat.completions.create(**request)  # type: ignore[arg-type]
            try:
                for event in stream:
                    if time.monotonic() >= stream_deadline:
                        raise TimeoutError("Model stream exceeded its turn deadline.")
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
            finally:
                close = getattr(stream, "close", None)
                if callable(close):
                    close()
            content = "".join(chunks)
            if not content:
                self._record_failure("Model returned an empty streamed response.")
                raise RuntimeError("Model returned an empty streamed response.")
            if not self._usage_records or self._usage_records[-1].ok is not True:
                self._record_success({})
            return content

        return self._call_with_retry(_make_request, timeout_seconds=timeout_seconds)

    def drain_usage_records(self) -> list[ModelUsageRecord]:
        records = self._usage_records
        self._usage_records = []
        return records

    def cancel(self, reason: str = "user stop") -> int:
        close = getattr(self._client, "close", None)
        if close is None:
            return 0
        try:
            close()
        except Exception:
            return 0
        return 1

    def _call_with_retry(
        self,
        make_request: Callable[[int, float], T],
        *,
        timeout_seconds: float,
    ) -> T:
        if timeout_seconds <= 0:
            raise TimeoutError("Model turn deadline expired before the request started.")
        deadline = time.monotonic() + timeout_seconds
        current_tokens = self.max_tokens
        credit_retries = 0
        transient_retries = 0
        while True:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Model turn exceeded its {timeout_seconds:g}s absolute deadline."
                    )
                result = make_request(current_tokens, remaining)
                if time.monotonic() >= deadline:
                    raise TimeoutError("Model response arrived after its turn deadline.")
                return result
            except Exception as exc:
                classified = classify_model_error(exc)
                if classified.kind == "credits" and classified.retryable:
                    if credit_retries >= self.credit_retry_count:
                        raise InsufficientCreditsError("Exhausted credit retries.") from exc
                    credit_retries += 1
                    affordable = _parse_affordable_tokens(str(exc))
                    current_tokens = affordable if affordable is not None else current_tokens // 2
                    if current_tokens < self.min_viable_tokens:
                        raise InsufficientCreditsError(
                            f"Insufficient credits: the provider cannot afford even "
                            f"{self.min_viable_tokens} output tokens. "
                            f"Visit https://openrouter.ai/settings/credits to add credits."
                        ) from exc
                    self._record_failure(
                        f"402 Payment Required (retrying with max_tokens={current_tokens})"
                    )
                    continue
                if not classified.retryable or transient_retries >= self.transient_retry_count:
                    raise
                transient_retries += 1
                delay = min(
                    self.retry_max_delay_seconds,
                    self.retry_base_delay_seconds * (2 ** (transient_retries - 1)),
                )
                remaining = deadline - time.monotonic()
                if remaining <= delay:
                    raise TimeoutError(
                        f"Model turn exceeded its {timeout_seconds:g}s absolute deadline."
                    ) from exc
                self._record_failure(
                    f"{classified.kind} provider failure "
                    f"(retry {transient_retries}/{self.transient_retry_count} in {delay:g}s)"
                )
                if delay > 0:
                    time.sleep(delay)

    def _record_success(self, usage: dict[str, int | None]) -> None:
        self._usage_records.append(
            ModelUsageRecord(
                model=self.model,
                ok=True,
                provider=self.provider_name,
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
        self._usage_records.append(
            ModelUsageRecord(model=self.model, ok=False, provider=self.provider_name, error=error)
        )

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

    def complete_with_timeout(self, messages: list[ChatMessage], timeout_seconds: float) -> str:
        return self._try_clients_with_deadline(messages, timeout_seconds, stream_callback=None)

    def stream_complete(
        self,
        messages: list[ChatMessage],
        on_token: Callable[[str], None],
    ) -> str:
        return self._try_clients(lambda client: client.stream_complete(messages, on_token))

    def stream_complete_with_timeout(
        self,
        messages: list[ChatMessage],
        on_token: Callable[[str], None],
        timeout_seconds: float,
    ) -> str:
        return self._try_clients_with_deadline(messages, timeout_seconds, stream_callback=on_token)

    def _try_clients_with_deadline(
        self,
        messages: list[ChatMessage],
        timeout_seconds: float,
        *,
        stream_callback: Callable[[str], None] | None,
    ) -> str:
        deadline = time.monotonic() + timeout_seconds

        def call(client: UsageTrackingModelClient) -> str:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Model turn exceeded its {timeout_seconds:g}s absolute deadline."
                )
            if stream_callback is not None:
                method = getattr(client, "stream_complete_with_timeout", None)
                if method is not None:
                    return method(messages, stream_callback, remaining)
                return client.stream_complete(messages, stream_callback)
            method = getattr(client, "complete_with_timeout", None)
            if method is not None:
                return method(messages, remaining)
            return client.complete(messages)

        return self._try_clients(call)

    def drain_usage_records(self) -> list[ModelUsageRecord]:
        records = self._usage_records
        self._usage_records = []
        return records

    def cancel(self, reason: str = "user stop") -> int:
        cancelled = 0
        for client in self.clients:
            cancel = getattr(client, "cancel", None)
            if cancel is not None:
                cancelled += int(cancel(reason) or 0)
        return cancelled

    def _try_clients(self, call: Callable[[UsageTrackingModelClient], str]) -> str:
        errors: list[str] = []
        previous_model: str | None = None
        for client in self.clients:
            try:
                content = call(client)
            except Exception as exc:
                error = safe_model_error(exc)
                classified = classify_model_error(exc)
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
                if not classified.fallbackable:
                    raise RuntimeError(
                        f"Model {client.model} failed with non-fallbackable {classified.kind}: {error}"
                    ) from exc
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
        provider_name=provider.name,
        include_stream_usage=provider.include_stream_usage,
        context_window_tokens=provider.context_window_tokens,
        timeout_seconds=provider.timeout_seconds,
        input_cost_per_million=provider.input_cost_per_million,
        output_cost_per_million=provider.output_cost_per_million,
        credit_retry_count=provider.credit_retry_count,
        transient_retry_count=provider.transient_retry_count,
        retry_base_delay_seconds=provider.retry_base_delay_seconds,
        retry_max_delay_seconds=provider.retry_max_delay_seconds,
        min_viable_tokens=provider.min_viable_tokens,
        extra_body=provider.extra_body,
    )


def create_fallback_client(
    provider: ModelProviderConfig,
    profile: ModelProfile,
    fallback_models: list[str] | list[FallbackModelSpec],
) -> UsageTrackingModelClient:
    clients: list[UsageTrackingModelClient] = [create_openai_compatible_client(provider, profile)]
    for fallback in fallback_models:
        if isinstance(fallback, FallbackModelSpec):
            fallback_profile = replace(
                profile,
                model=fallback.model,
                max_tokens=fallback.max_tokens or profile.max_tokens,
            )
            clients.append(create_openai_compatible_client(fallback.provider, fallback_profile))
        else:
            clients.append(create_openai_compatible_client(provider, replace(profile, model=fallback)))
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


def _parse_affordable_tokens(error_message: str) -> int | None:
    match = re.search(r"can only afford (\d+)", error_message)
    if match:
        return int(match.group(1))
    return None


def classify_model_error(exc: Exception) -> ClassifiedModelError:
    message = f"{type(exc).__name__}: {exc}"
    lowered = message.lower()
    status_code = getattr(exc, "status_code", None)

    if isinstance(exc, InsufficientCreditsError) or status_code == 402 or "insufficient credits" in lowered:
        return ClassifiedModelError("credits", retryable=True, fallbackable=True, message=message)
    if isinstance(exc, openai.RateLimitError) or status_code == 429 or "rate limit" in lowered:
        return ClassifiedModelError("rate_limit", retryable=True, fallbackable=True, message=message)
    if (
        "resourceexhausted" in lowered
        or "request limit reached" in lowered
        or "capacity" in lowered
        or "overloaded" in lowered
    ):
        return ClassifiedModelError("capacity", retryable=True, fallbackable=True, message=message)
    if isinstance(exc, (openai.APITimeoutError, TimeoutError)) or "timed out" in lowered or "timeout" in lowered:
        return ClassifiedModelError("timeout", retryable=True, fallbackable=True, message=message)
    if isinstance(exc, openai.APIConnectionError) or "connection" in lowered:
        return ClassifiedModelError("connection", retryable=True, fallbackable=True, message=message)
    if (
        isinstance(exc, openai.AuthenticationError)
        or status_code in {401, 403}
        or "api key" in lowered
        or "unauthorized" in lowered
        or "authentication" in lowered
    ):
        return ClassifiedModelError("auth", retryable=False, fallbackable=False, message=message)
    if isinstance(exc, openai.NotFoundError) or status_code == 404:
        return ClassifiedModelError("not_found", retryable=False, fallbackable=False, message=message)
    if isinstance(exc, openai.BadRequestError) or status_code == 400:
        return ClassifiedModelError("bad_request", retryable=False, fallbackable=False, message=message)
    if status_code is not None and int(status_code) >= 500:
        return ClassifiedModelError("server", retryable=True, fallbackable=True, message=message)
    if "empty response" in lowered or "empty streamed response" in lowered:
        return ClassifiedModelError("empty_response", retryable=True, fallbackable=True, message=message)
    return ClassifiedModelError("unknown", retryable=False, fallbackable=True, message=message)


OpenAIChatClient = OpenAICompatibleChatClient


def safe_model_error(exc: Exception) -> str:
    """A category only: provider exception bodies can contain prompts or headers."""
    return f"{type(exc).__name__}: Model provider {classify_model_error(exc).kind} failure."
