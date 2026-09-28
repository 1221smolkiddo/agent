from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import re
import uuid

from .config import ExperienceMemoryConfig
from .contracts import (
    Experience, ExperienceMemoryProvider, MemoryResult, MemoryStatus, OperationLookup, RecalledExperience,
)
from .privacy import safe_text, valid_experience, valid_query
from .episode_sanitizer import PreparedEpisode
from .providers import NullExperienceMemoryProvider
from .scope import RepositoryScope, repository_scope


class ExperienceMemoryService:
    """Explicit historical-memory API. No lifecycle hooks or automatic operations.

    Future runtime use must supply authorization and effect accounting before calling
    this service. Recalled information cannot prove current facts or completion.
    """

    def __init__(
        self, config: ExperienceMemoryConfig, workspace: Path, *,
        provider: ExperienceMemoryProvider | None = None,
    ) -> None:
        self._config = config
        self._workspace = workspace
        if config.availability != MemoryStatus.OK:
            self._provider: ExperienceMemoryProvider = NullExperienceMemoryProvider(
                config.availability,
            )
        elif provider is not None:
            self._provider = provider
        else:
            from .providers.hindsight import HindsightExperienceMemoryProvider

            self._provider = HindsightExperienceMemoryProvider(config)

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def config(self) -> ExperienceMemoryConfig:
        return self._config

    @property
    def enabled(self) -> bool:
        return self._config.enabled and self._config.provider != "none"

    @property
    def availability(self) -> MemoryStatus:
        return self._config.availability

    def scope(self) -> RepositoryScope:
        """Explicit Git inspection; not invoked during service construction."""
        return repository_scope(self._workspace)

    def health(self) -> MemoryResult:
        return self._safe(self._provider.health)

    def recall(self, query: str) -> MemoryResult:
        return self._query(query, reflect=False)

    def reflect(self, query: str) -> MemoryResult:
        return self._query(query, reflect=True)

    def _query(self, query: str, *, reflect: bool) -> MemoryResult:
        def operation() -> MemoryResult:
            if self._config.availability != MemoryStatus.OK:
                return MemoryResult(self._config.availability)
            scope = self.scope()
            if not valid_query(scope.bank_id, query, self._config):
                return MemoryResult(MemoryStatus.INVALID_REQUEST)
            method = self._provider.reflect if reflect else self._provider.recall
            return method(scope.bank_id, query)
        return self._safe(operation)

    def retain(self, summary: str) -> MemoryResult:
        """Explicit, reviewed prose summary only; no arbitrary metadata or file API."""
        def operation() -> MemoryResult:
            if self._config.availability != MemoryStatus.OK:
                return MemoryResult(self._config.availability)
            scope = self.scope()
            experience = Experience(summary, scope.branch, scope.head)
            if not valid_experience(scope.bank_id, experience, self._config):
                return MemoryResult(MemoryStatus.INVALID_REQUEST)
            return self._provider.retain(scope.bank_id, experience)
        return self._safe(operation)

    def retain_episode(self, prepared: PreparedEpisode) -> MemoryResult:
        """Compatibility path for an explicitly prepared episode."""
        try:
            return self.submit_retention(self.scope().bank_id, prepared)
        except Exception:
            return MemoryResult(MemoryStatus.UNAVAILABLE)

    def submit_retention(self, bank_id: str, prepared: PreparedEpisode) -> MemoryResult:
        """Submit the outbox's sanitized request to its recorded repository bank."""
        def operation() -> MemoryResult:
            if self._config.availability != MemoryStatus.OK:
                return MemoryResult(self._config.availability)
            experience = Experience(
                summary=prepared.content, branch=prepared.branch, head=prepared.head,
                kind="engineering_episode", document_id=prepared.document_id,
                operation_id=prepared.operation_id, outcome=prepared.outcome,
                agent_version=prepared.agent_version,
            )
            if not valid_experience(bank_id, experience, self._config):
                return MemoryResult(MemoryStatus.INVALID_REQUEST)
            return self._provider.retain(bank_id, experience)
        return self._safe(operation)

    def get_operation(self, bank_id: str, operation_id: str) -> OperationLookup:
        """Look up provider truth without exposing SDK responses or exception text."""
        if self._config.availability != MemoryStatus.OK:
            return OperationLookup(self._config.availability)
        if not re.fullmatch(r"agent47-repo-[0-9a-f]{64}", bank_id):
            return OperationLookup(MemoryStatus.INVALID_REQUEST)
        try:
            uuid.UUID(operation_id)
            return self._provider.get_operation(bank_id, operation_id)
        except TimeoutError:
            return OperationLookup(MemoryStatus.TIMEOUT)
        except Exception:
            return OperationLookup(MemoryStatus.UNAVAILABLE)

    def _safe(self, operation: Callable[[], MemoryResult]) -> MemoryResult:
        try:
            result = operation()
            status = MemoryStatus(result.status)
            if status != MemoryStatus.OK:
                return MemoryResult(status)
            remaining = self._config.recall_max_tokens
            memories: list[RecalledExperience] = []
            for item in result.memories[:self._config.recall_max_results]:
                raw = safe_text(item.text, self._config).encode("utf-8")[:remaining]
                remaining -= len(raw)
                if raw:
                    memories.append(RecalledExperience(raw.decode("utf-8", errors="ignore")))
            raw = safe_text(result.text, self._config).encode("utf-8")[:remaining]
            return MemoryResult(status, tuple(memories), raw.decode("utf-8", errors="ignore"))
        except TimeoutError:
            return MemoryResult(MemoryStatus.TIMEOUT)
        except Exception:
            return MemoryResult(MemoryStatus.UNAVAILABLE)
