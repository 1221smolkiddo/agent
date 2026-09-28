"""Deterministic escalation after ordinary diagnosis, never model-selected."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .config import ExperienceMemoryConfig
from .contracts import ReflectDecision
from .episode_sanitizer import MemorySanitizer


@dataclass(frozen=True)
class ReflectionEvidence:
    diagnostic: str = field(repr=False)
    category: str = "unknown"
    occurrences: int = 1
    prior_occurrences: int = 0
    recovery_attempts: int = 0
    failed_repair: bool = False
    verification_failed: bool = False
    unresolved: bool = True
    diagnosis_complete: bool = True
    deterministic_recovery_available: bool = False
    cancelled_or_denied: bool = False
    conflicting_history: bool = False
    historical_summaries: tuple[str, ...] = field(default=(), repr=False)
    attempted_strategies: tuple[str, ...] = field(default=(), repr=False)


class ReflectPolicy:
    def __init__(self, config: ExperienceMemoryConfig) -> None:
        self.config = config
        self.sanitizer = MemorySanitizer(config)

    def decide(self, clean_task: str, evidence: ReflectionEvidence) -> ReflectDecision:
        if (not self.config.enabled or self.config.provider == "none"
                or not self.config.automatic_reflect_enabled):
            return ReflectDecision(False, "disabled")
        if self.config.automatic_reflect_max_requests == 0:
            return ReflectDecision(False, "budget_disabled")
        if evidence.cancelled_or_denied:
            return ReflectDecision(False, "cancelled_or_denied")
        if not evidence.unresolved:
            return ReflectDecision(False, "resolved")
        if not evidence.diagnosis_complete or not evidence.diagnostic.strip():
            return ReflectDecision(False, "diagnosis_required")
        if evidence.category in {
            "permission", "network", "provider", "infrastructure", "environment", "cancelled",
        }:
            return ReflectDecision(False, "non_code_failure")
        if evidence.deterministic_recovery_available or evidence.category in {"syntax", "style"}:
            return ReflectDecision(False, "deterministic_recovery")
        reason = ""
        if evidence.occurrences > 1:
            reason = "repeated_diagnostic"
        elif evidence.failed_repair and evidence.verification_failed:
            reason = "verification_after_failed_repair"
        elif evidence.prior_occurrences > 0:
            reason = "recurring_diagnostic_history"
        elif evidence.conflicting_history and evidence.recovery_attempts > 0:
            reason = "conflicting_historical_experiences"
        elif evidence.historical_summaries and evidence.recovery_attempts > 0:
            reason = "unresolved_with_recalled_history"
        if not reason:
            return ReflectDecision(False, "first_ordinary_failure")
        query = self._query(clean_task, evidence)
        if not query:
            return ReflectDecision(False, "unsafe_query")
        return ReflectDecision(True, reason, query)

    def _query(self, clean_task: str, evidence: ReflectionEvidence) -> str:
        def clean(value: str, limit: int) -> str:
            # Inspect before flattening: JSON quoting would otherwise hide source lines
            # from the service's plain-text dump check. Oversized inputs fail closed.
            if len(value) > 4000 or re.search(
                r"(?m)^\s*(?:async\s+def|def|class|import|from|function|const|let|var)\s+",
                value,
            ):
                return "[source or credential material omitted]"
            return " ".join(self.sanitizer.sanitize_text(value).split())[:limit]

        # Protect callers that accidentally supply the interactive wrapper.
        for marker in ("\nRecent interactive transcript", "\nCurrent interactive session state"):
            clean_task = clean_task.split(marker, 1)[0]
        task = clean(clean_task, 400)
        diagnostic = clean(evidence.diagnostic, 250)
        if not task or "[source or credential material omitted]" in {task, diagnostic}:
            return ""
        payload = {
            "question": (
                "Using historical observations and engineering experiences, which prior causes "
                "and failed strategies match this unresolved diagnostic, and which hypotheses "
                "should be validated against current files and tests? Be highly skeptical and "
                "literal. Separate historical evidence from inference; never claim current success."
            ),
            "task": task,
            "diagnostic": diagnostic,
            "attempted_strategies": [clean(item, 90) for item in evidence.attempted_strategies[-3:]],
            "verification": "failed" if evidence.verification_failed else "unresolved diagnosis",
            "historical_summaries": [clean(item, 130) for item in evidence.historical_summaries[:2]],
        }
        query = json.dumps(payload, ensure_ascii=False)
        if len(query) > 1800:
            payload["historical_summaries"] = []
            query = json.dumps(payload, ensure_ascii=False)
        return query if len(query) <= 1800 else ""
