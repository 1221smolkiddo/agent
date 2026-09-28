"""Deterministic selection and bounded query construction for historical recall."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import ExperienceMemoryConfig
from .episode_sanitizer import MemorySanitizer

_CONTINUITY = re.compile(
    r"\b(continue|resume|again|previous|previously|earlier|last time|recurring|recurrent|"
    r"repeated|regression|flaky|intermittent)\b", re.I,
)
_REPAIR = re.compile(r"\b(fix|repair|debug|bug|error|failing|failed|failure|crash|timeout|broken)\b", re.I)
_HISTORY = re.compile(
    r"\b(why (?:did|do|have) we|what did we try|rejected approach|"
    r"(?:architecture|architectural|design|historical|prior|previous|earlier) decision|"
    r"(?:historical|design|architecture) rationale|previous solution|"
    r"earlier implementation|last time)\b", re.I,
)
_LONG_LIVED = re.compile(
    r"\b(migration|migrate|upgrade|release|rollback|incident|dependency migration|"
    r"performance regression)\b", re.I,
)
_MECHANICAL = re.compile(
    r"\b(rename (?:a |the )?(?:variable|parameter|local|identifier)|"
    r"format(?:ting)?[- ]only|"
    r"format (?:the )?(?:file|code)|fix whitespace|fix indentation)\b", re.I,
)
_SUBSTANTIAL = re.compile(
    r"\b(implement|change|design|redesign|integrate|replace|rework|refactor|restructure)\b.*"
    r"\b(architecture|architectural|system|module|service|workflow|multiple files|"
    r"cross[- ]cutting|system[- ]wide)\b", re.I,
)
_CROSS_CUTTING = re.compile(
    r"\b(cross[- ]cutting|system[- ]wide|architecture|distributed|concurrency|"
    r"multiple (?:files|modules|services)|across (?:modules|services|packages)|"
    r"race condition|deadlock)\b", re.I,
)


@dataclass(frozen=True)
class RecallDecision:
    should_recall: bool
    reason: str
    query: str = field(default="", repr=False)
    requested_budget: str = "low"
    memory_types: tuple[str, ...] = ("observation", "experience")


class MemoryRecallPolicy:
    def __init__(self, config: ExperienceMemoryConfig) -> None:
        self.config = config
        self.sanitizer = MemorySanitizer(config)

    def decide(
        self, clean_task: str, *, workspace_task: bool,
        prior_diagnostic_occurrences: int = 0,
    ) -> RecallDecision:
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
            ("long_lived_work", _LONG_LIVED),
            ("substantial_change", _SUBSTANTIAL),
        ) if pattern.search(task)), "")
        # This optional input is trusted structured diagnostic history, not recalled
        # claims. No model, file inspection, or database query is used to classify.
        known_recurrence = (
            isinstance(prior_diagnostic_occurrences, int)
            and not isinstance(prior_diagnostic_occurrences, bool)
            and prior_diagnostic_occurrences > 0
        )
        if not reason and known_recurrence:
            reason = "recurring_diagnostic_history"
        if not reason and _REPAIR.search(task) and _CROSS_CUTTING.search(task):
            reason = "repair"
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
