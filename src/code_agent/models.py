from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI


ChatMessage = dict[str, str]


class ModelClient(Protocol):
    model: str

    def complete(self, messages: list[ChatMessage]) -> str:
        """Return the assistant message content."""


@dataclass
class OpenAIChatClient:
    api_key: str
    base_url: str
    model: str

    def __post_init__(self) -> None:
        self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def complete(self, messages: list[ChatMessage]) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.2,
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("Model returned an empty response.")
        return content
