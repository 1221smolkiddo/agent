from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Protocol

from openai import OpenAI


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
class OpenAICompatibleChatClient:
    api_key: str
    base_url: str
    model: str
    max_tokens: int = 4096
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
            temperature=0.2,
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
            temperature=0.2,
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


OpenAIChatClient = OpenAICompatibleChatClient
