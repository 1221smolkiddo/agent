"""One best-effort finalization boundary for outbound historical episodes."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .episode_sanitizer import MemorySanitizer
from .episodes import EngineeringEpisodeBuilder
from .outbox import ExperienceMemoryOutbox, start_recovery_worker
from .retention_policy import RetentionPolicy
from .service import ExperienceMemoryService

if TYPE_CHECKING:
    from ..agent import AgentRunResult
    from ..durable_execution import ExecutionProjection
    from ..storage import AgentStorage


class EpisodeRetentionCoordinator:
    def __init__(
        self, service: ExperienceMemoryService, storage: AgentStorage, *,
        background_recovery: bool = False,
    ) -> None:
        self.service = service
        self.storage = storage
        self.background_recovery = background_recovery
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
        operation_id = ""
        try:
            if state is None:
                status = "unavailable"
                reason = "runtime_state_unavailable"
            else:
                # Deliberate order: evidence -> policy -> sanitizer -> durable outbox.
                scope = self.service.scope()
                episode = self.builder.build(result, state, task_id, scope)
                decision = self.policy.decide(episode)
                if not decision.retain:
                    return
                prepared = MemorySanitizer(self.service.config).prepare(episode)
                document_id = prepared.document_id
                operation_id = prepared.operation_id
                outbox = ExperienceMemoryOutbox(self.storage.db_path, self.service)
                inserted = outbox.enqueue(scope.bank_id, prepared, result.run_id)
                entry = outbox.process(operation_id, first_submission=inserted)
                status = entry.state if entry is not None else "unknown"
                reason = entry.error_code if entry and entry.error_code else decision.reason
                if self.background_recovery and entry and entry.next_attempt_at is not None:
                    recovery_config = self.service.config.model_copy(update={
                        "timeout_seconds": min(self.service.config.timeout_seconds, 2.0),
                    })
                    recovery_service = ExperienceMemoryService(recovery_config, self.service.workspace)
                    start_recovery_worker(self.storage.db_path, recovery_service)
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
                "operation_id": operation_id,
            })
        except Exception:
            pass
