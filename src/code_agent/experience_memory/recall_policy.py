"""Deterministic selection and bounded query construction for historical recall."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import ExperienceMemoryConfig
from .episode_sanitizer import MemorySanitizer

_CONTINUITY = re.compile(r"\b(continue|resume|again|previous|previously|earlier|last time|recurring|regression)\b", re.I)
_REPAIR = re.compile(r"\b(fix|debug|bug|error|failing|failed|failure|flaky|crash|timeout|broken|regression)\b", re.I)
_HISTORY = re.compile(r"\b(why did we|what did we try|rejected approach|decision|previous solution|earlier implementation|last time)\b", re.I)
_LONG_LIVED = re.compile(r"\b(migration|migrate|upgrade|release|rollback|incident|refactor|dependency migration|performance regression|ci problem)\b", re.I)
_MECHANICAL = re.compile(
    r"\b(rename (?:a |the )?(?:variable|parameter|local)|format(?:ting)? only|fix whitespace|fix indentation)\b", re.I,
)
_SUBSTANTIAL = re.compile(r"\b(implement|redesign|integrate|replace|rework)\b.*\b(architecture|system|module|service|workflow|multiple files)\b", re.I)


@dataclass(frozen=True)
class RecallDecision:
    should_recall: bool
    reason: str
    query: str = ""
    requested_budget: str = "low"
    memory_types: tuple[str, ...] = ("observation", "experience")


class MemoryRecallPolicy:
    def __init__(self, config: ExperienceMemoryConfig) -> None:
        self.config = config
        self.sanitizer = MemorySanitizer(config)

    def decide(self, clean_task: str, *, workspace_task: bool) -> RecallDecision:
        if not self.config.enabled or self.config.provider == "none":
            return RecallDecision(False, "disabled")
        if self.config.automatic_recall_max_requests == 0:
            return RecallDecision(False, "budget_disabled")
        task = " ".join(clean_task.split())
        if not task:
            return RecallDecision(False, "empty_task")
        if _MECHANICAL.search(task):
            return RecallDecision(False, "mechanical_edit")
        reason = next((name for name, pattern in (
            ("historical_reasoning", _HISTORY),
            ("continuity", _CONTINUITY),
            ("repair", _REPAIR),
            ("long_lived_work", _LONG_LIVED),
            ("substantial_change", _SUBSTANTIAL),
        ) if pattern.search(task)), "")
        if not reason:
            return RecallDecision(False, "no_historical_signal")
        if not workspace_task and reason not in {
            "historical_reasoning", "continuity", "long_lived_work",
        }:
            return RecallDecision(False, "not_engineering_workspace_work")
        # Only this task, never interactive transcript, file content or environment.
        query = self.sanitizer.sanitize_text(task[:1600])
        query = " ".join(query.split())[:800].rstrip()
        if not query or query == "[source or credential material omitted]":
            return RecallDecision(False, "unsafe_query")
        return RecallDecision(True, reason, query, self.config.budget)
