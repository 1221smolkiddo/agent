"""Provider-neutral historical memory contracts; never execution authority."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable


class MemoryStatus(StrEnum):
    OK = "ok"
    DISABLED = "disabled"
    MISSING_CONFIG = "missing_config"
    MISSING_DEPENDENCY = "missing_dependency"
    INVALID_REQUEST = "invalid_request"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Experience:
    """A bounded, reviewed prose summary, never a source file or environment dump."""

    summary: str = field(repr=False)
    branch: str | None = field(default=None, repr=False)
    head: str | None = None
    kind: Literal["summary", "engineering_episode"] = "summary"
    document_id: str | None = None
    operation_id: str | None = None
    outcome: str | None = None
    agent_version: str | None = None


@dataclass(frozen=True)
class RecalledExperience:
    text: str = field(repr=False)


@dataclass(frozen=True)
class MemoryResult:
    status: MemoryStatus
    memories: tuple[RecalledExperience, ...] = ()
    text: str = field(default="", repr=False)
    # Historical memory is advisory, including reflect output.
    untrusted: bool = field(default=True, init=False)

    @property
    def ok(self) -> bool:
        return self.status == MemoryStatus.OK


@runtime_checkable
class ExperienceMemoryProvider(Protocol):
    def health(self) -> MemoryResult: ...

    def recall(self, bank_id: str, query: str) -> MemoryResult: ...

    def retain(self, bank_id: str, experience: Experience) -> MemoryResult: ...

    def reflect(self, bank_id: str, query: str) -> MemoryResult: ...
