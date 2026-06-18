from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

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


@dataclass
class OpenAICompatibleChatClient:
    api_key: str
    base_url: str
    model: str
    max_tokens: int = 4096
    temperature: float = 0.2
    default_headers: dict[str, str] | None = None

    def __post_init__(self) -> None:
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
            raise RuntimeError("Model returned an empty response.")
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
        )
        for event in stream:
            token = event.choices[0].delta.content or ""
            if not token:
                continue
            chunks.append(token)
            on_token(token)
        content = "".join(chunks)
        if not content:
            raise RuntimeError("Model returned an empty streamed response.")
        return content


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
    )


OpenAIChatClient = OpenAICompatibleChatClient
