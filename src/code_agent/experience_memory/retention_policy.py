"""Deterministic selection of meaningful historical engineering lessons."""
from __future__ import annotations

from dataclasses import dataclass

from .episodes import EngineeringEpisode, EpisodeOutcome


@dataclass(frozen=True)
class RetentionDecision:
    retain: bool
    reason: str


class RetentionPolicy:
    def decide(self, episode: EngineeringEpisode) -> RetentionDecision:
        if not episode.execution_id or episode.run_id <= 0:
            return RetentionDecision(False, "missing_execution_identity")
        if episode.outcome == EpisodeOutcome.VERIFIED and episode.changed_paths:
            return RetentionDecision(True, "verified_change")
        if episode.diagnostics and episode.failed_approaches:
            return RetentionDecision(True, "diagnosed_failed_strategy")
        if any("Recurring diagnostic" in item for item in episode.diagnostics):
            return RetentionDecision(True, "recurring_failure")
        if episode.outcome == EpisodeOutcome.BLOCKED and episode.blockers:
            if any(len(item) > 35 for item in episode.blockers):
                return RetentionDecision(True, "meaningful_blocker")
        if episode.changed_paths and episode.outcome == EpisodeOutcome.FAILED:
            return RetentionDecision(True, "failed_change")
        return RetentionDecision(False, "no_durable_lesson")
