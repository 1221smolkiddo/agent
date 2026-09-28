"""One best-effort finalization boundary for outbound historical episodes."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .contracts import MemoryStatus
from .episode_sanitizer import MemorySanitizer
from .episodes import EngineeringEpisodeBuilder
from .retention_policy import RetentionPolicy
from .service import ExperienceMemoryService

if TYPE_CHECKING:
    from ..agent import AgentRunResult
    from ..durable_execution import ExecutionProjection
    from ..storage import AgentStorage


class EpisodeRetentionCoordinator:
    def __init__(self, service: ExperienceMemoryService, storage: AgentStorage) -> None:
        self.service = service
        self.storage = storage
        self.builder = EngineeringEpisodeBuilder()
        self.policy = RetentionPolicy()

    def after_run(
        self, result: AgentRunResult, state: ExecutionProjection | None,
        task_id: str | None, *, dry_run: bool,
    ) -> None:
        if not self.service.enabled or dry_run:
            return
        status = "unavailable"
        reason = "retention_error"
        document_id = ""
        try:
            if state is None:
                status = "unavailable"
                reason = "runtime_state_unavailable"
            elif self.service.availability != MemoryStatus.OK:
                status = self.service.availability.value
                reason = "provider_not_configured"
            else:
                # Deliberate order: evidence -> policy -> sanitizer -> service.
                episode = self.builder.build(result, state, task_id, self.service.scope())
                decision = self.policy.decide(episode)
                if not decision.retain:
                    return
                prepared = MemorySanitizer(self.service.config).prepare(episode)
                document_id = prepared.document_id
                memory_result = self.service.retain_episode(prepared)
                status = memory_result.status.value
                reason = decision.reason
        except ValueError:
            status = "invalid_request"
            reason = "sanitization_rejected"
        except Exception:
            # Neither provider exceptions nor Git/storage errors may change run success.
            status = "unavailable"
            reason = "retention_error"
        try:
            self.storage.add_step(result.run_id, "tool", {
                "type": "experience_memory_retention", "status": status,
                "reason": reason, "document_id": document_id,
            })
        except Exception:
            pass
