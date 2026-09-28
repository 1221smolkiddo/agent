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


class OperationState(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class OperationLookup:
    status: MemoryStatus
    state: OperationState = OperationState.UNKNOWN
    error_code: str = ""


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
class RecallRequest:
    query: str = field(repr=False)
    max_tokens: int = 1024
    max_results: int = 5
    source_fact_tokens: int = 256
    timeout_seconds: float = 3.0
    budget: Literal["low", "mid", "high"] = "low"


@dataclass(frozen=True)
class MemoryProvenance:
    memory_id: str = ""
    document_id: str = ""
    repository_bank_id: str = ""
    head: str = ""
    branch: str = ""
    changed_paths: tuple[str, ...] = ()
    occurred_at: str = ""


@dataclass(frozen=True)
class RecalledMemory:
    text: str = field(repr=False)
    memory_type: Literal["observation", "experience"] = "experience"
    provenance: MemoryProvenance = field(default_factory=MemoryProvenance)
    source_facts: tuple[MemoryProvenance, ...] = ()
    source_fact_ids: tuple[str, ...] = ()
    relevance: float | None = None
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ExperienceMemoryRecall:
    status: MemoryStatus
    memories: tuple[RecalledMemory, ...] = ()
    source_facts_truncated: bool = False
    untrusted: bool = field(default=True, init=False)


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
    def recall_detailed(self, bank_id: str, request: RecallRequest) -> ExperienceMemoryRecall: ...

    def retain(self, bank_id: str, experience: Experience) -> MemoryResult: ...

    def reflect(self, bank_id: str, query: str) -> MemoryResult: ...
    def get_operation(self, bank_id: str, operation_id: str) -> OperationLookup: ...
