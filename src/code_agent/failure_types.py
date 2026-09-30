"""Structured failure classification and recovery strategies for Agent47.

Provides a taxonomy of failure types and maps each to an appropriate recovery
strategy.  The classifier prefers structured metadata from tool results over
keyword matching, falling back to heuristics only when no structured signal is
available.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Configurable constants — tune without touching logic
# ---------------------------------------------------------------------------

MAX_RECOVERY_ATTEMPTS: int = 5
"""Local failure streak requiring a new plan, never task termination."""


class RunDisposition(str, Enum):
    SUCCESS = "success"
    RECOVERABLE = "recoverable"
    REPLAN_REQUIRED = "replan_required"
    WAITING = "waiting"
    TERMINAL = "terminal"


class RecoveryEvent(str, Enum):
    PROVIDER = "provider"
    EXECUTABLE = "executable"
    PROTOCOL = "protocol"
    VERIFICATION = "verification"
    GUARD = "guard"
    REPLAN = "replan"


LOW_CONFIDENCE_MUTATION_THRESHOLD: float = 0.30
"""Confidence score below which mutating actions are blocked."""

LOW_CONFIDENCE_HARD_THRESHOLD: float = 0.15
"""Confidence score below which *all* non-discovery actions are blocked."""

CONTEXT_BUDGET_WARNING_RATIO: float = 0.80
"""Ratio of context_max_chars at which a compaction hint is emitted."""

CONTEXT_BUDGET_FORCE_COMPACT_RATIO: float = 0.85
"""Ratio of context_max_chars that triggers a forced double-compaction pass."""

CONTEXT_BUDGET_TIGHT_TARGET_RATIO: float = 0.70
"""Target ratio after forced compaction."""

SQLITE_RETRY_ATTEMPTS: int = 3
"""Number of retries for transient SQLite locking errors."""

SQLITE_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (0.1, 0.5, 2.0)
"""Backoff delays between SQLite retry attempts."""

EVIDENCE_RECORDS_LIMIT_COMPACTED: int = 20
"""Evidence record cap after proactive budget compaction."""


# ---------------------------------------------------------------------------
# Failure categories
# ---------------------------------------------------------------------------

class FailureCategory(str, Enum):
    """Structured failure types replacing generic failures."""

    PATCH_FAILURE = "patch_failure"
    FILE_MISSING = "file_missing"
    TOOL_FAILURE = "tool_failure"
    PROVIDER_FAILURE = "provider_failure"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    DATABASE_LOCKED = "database_locked"
    CONTEXT_MISSING = "context_missing"
    VERIFICATION_FAILURE = "verification_failure"
    BUDGET_EXCEEDED = "budget_exceeded"
    PARSE_FAILURE = "parse_failure"
    SYMBOL_MISSING = "symbol_missing"
    SEARCH_NO_RESULTS = "search_no_results"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Recovery strategies
# ---------------------------------------------------------------------------

class RecoveryStrategy(str, Enum):
    """What the engine should do when a particular failure category occurs."""

    REREAD_FILE = "reread_file"
    GENERATE_NEW_PATCH = "generate_new_patch"
    SWITCH_TOOL = "switch_tool"
    GATHER_EVIDENCE = "gather_evidence"
    ASK_USER = "ask_user"
    REPLAN = "replan"
    COMPACT_CONTEXT = "compact_context"
    STOP = "stop"
    RETRY_WITH_BACKOFF = "retry_with_backoff"
    BROADEN_SEARCH = "broaden_search"


# ---------------------------------------------------------------------------
# Recovery instruction text per strategy
# ---------------------------------------------------------------------------

_STRATEGY_INSTRUCTIONS: dict[RecoveryStrategy, str] = {
    RecoveryStrategy.REREAD_FILE: (
        "The target file is missing or stale. List the nearest directory or search "
        "for the filename, then read the discovered path. Do not retry the same "
        "path unchanged."
    ),
    RecoveryStrategy.GENERATE_NEW_PATCH: (
        "The patch or edit did not apply cleanly. Re-read the affected files to "
        "observe current content, then generate a fresh minimal patch against the "
        "observed content. Do not reuse the previous patch."
    ),
    RecoveryStrategy.SWITCH_TOOL: (
        "The tool failed. Choose a different tool or approach that avoids the "
        "failure, or gather more context before retrying with a materially "
        "different action."
    ),
    RecoveryStrategy.GATHER_EVIDENCE: (
        "Insufficient context. Read more files, search for symbols, inspect the "
        "repo map, or run diagnostics before attempting the action again."
    ),
    RecoveryStrategy.ASK_USER: (
        "Recovery is not possible without user input. Explain the blocker clearly "
        "and ask the user how to proceed."
    ),
    RecoveryStrategy.REPLAN: (
        "Multiple recovery attempts have failed with the same issue. Revise the "
        "execution plan before continuing. Update the plan with new information "
        "from the failed attempts."
    ),
    RecoveryStrategy.COMPACT_CONTEXT: (
        "Context budget is under pressure. Summarize earlier reasoning and discard "
        "obsolete evidence before continuing."
    ),
    RecoveryStrategy.STOP: (
        "The failure is unrecoverable. Stop execution and report the issue to the "
        "user with a clear explanation."
    ),
    RecoveryStrategy.RETRY_WITH_BACKOFF: (
        "A transient infrastructure error occurred. The system will retry "
        "automatically."
    ),
    RecoveryStrategy.BROADEN_SEARCH: (
        "The search returned no results. Broaden the query using a filename "
        "fragment, symbol, or related concept. Do not repeat the identical search."
    ),
}


# ---------------------------------------------------------------------------
# Failure → strategy mapping
# ---------------------------------------------------------------------------

_CATEGORY_STRATEGY: dict[FailureCategory, RecoveryStrategy] = {
    FailureCategory.PATCH_FAILURE: RecoveryStrategy.GENERATE_NEW_PATCH,
    FailureCategory.FILE_MISSING: RecoveryStrategy.REREAD_FILE,
    FailureCategory.TOOL_FAILURE: RecoveryStrategy.SWITCH_TOOL,
    FailureCategory.PROVIDER_FAILURE: RecoveryStrategy.RETRY_WITH_BACKOFF,
    FailureCategory.PERMISSION_DENIED: RecoveryStrategy.ASK_USER,
    FailureCategory.TIMEOUT: RecoveryStrategy.RETRY_WITH_BACKOFF,
    FailureCategory.DATABASE_LOCKED: RecoveryStrategy.RETRY_WITH_BACKOFF,
    FailureCategory.CONTEXT_MISSING: RecoveryStrategy.GATHER_EVIDENCE,
    FailureCategory.VERIFICATION_FAILURE: RecoveryStrategy.GENERATE_NEW_PATCH,
    FailureCategory.BUDGET_EXCEEDED: RecoveryStrategy.COMPACT_CONTEXT,
    FailureCategory.PARSE_FAILURE: RecoveryStrategy.SWITCH_TOOL,
    FailureCategory.SYMBOL_MISSING: RecoveryStrategy.GATHER_EVIDENCE,
    FailureCategory.SEARCH_NO_RESULTS: RecoveryStrategy.BROADEN_SEARCH,
    FailureCategory.UNKNOWN: RecoveryStrategy.SWITCH_TOOL,
}


def recovery_strategy(category: FailureCategory) -> RecoveryStrategy:
    """Return the recovery strategy appropriate for *category*."""
    return _CATEGORY_STRATEGY.get(category, RecoveryStrategy.SWITCH_TOOL)


def recovery_instruction(category: FailureCategory) -> str:
    """Return a human-readable recovery instruction for *category*."""
    strategy = recovery_strategy(category)
    return _STRATEGY_INSTRUCTIONS.get(strategy, _STRATEGY_INSTRUCTIONS[RecoveryStrategy.SWITCH_TOOL])


# ---------------------------------------------------------------------------
# Failure fingerprinting
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FailureFingerprint:
    """Uniquely identifies a failure by action, target, and category.

    This avoids treating unrelated failures (e.g. two different missing files)
    as the same recovery case.
    """

    action_type: str
    target: str
    category: FailureCategory
    strategy_digest: str = ""

    @classmethod
    def from_action(
        cls,
        action_type: str,
        action_payload: dict[str, Any],
        category: FailureCategory,
    ) -> "FailureFingerprint":
        target = (
            action_payload.get("path", "")
            or action_payload.get("command", "")
            or action_payload.get("query", "")
            or action_payload.get("symbol", "")
            or ""
        )
        strategy = {key: value for key, value in action_payload.items() if key != "rationale"}
        digest = hashlib.sha256(json.dumps(strategy, sort_keys=True).encode()).hexdigest()
        return cls(action_type=action_type,
                   target="sha256:" + hashlib.sha256(str(target)[:200].encode()).hexdigest(),
                   category=category,
                   strategy_digest=digest)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

def classify_failure(
    action_type: str,
    output: str,
    metadata: dict[str, Any] | None = None,
) -> FailureCategory:
    """Classify a tool failure into a structured category.

    Prefers structured ``metadata`` signals (error codes, typed fields) and
    falls back to keyword matching on *output* only when no structured signal
    is available.
    """
    metadata = metadata or {}

    # ----- Structured metadata first -----
    error_code = metadata.get("error_code") or metadata.get("error_type") or ""
    if isinstance(error_code, str) and error_code:
        structured = _classify_from_error_code(error_code)
        if structured is not None:
            return structured

    if metadata.get("timeout") is True:
        return FailureCategory.TIMEOUT

    if metadata.get("budget_exceeded") is True:
        return FailureCategory.BUDGET_EXCEEDED

    if metadata.get("permission_denied") is True:
        return FailureCategory.PERMISSION_DENIED

    exit_code = metadata.get("exit_code")
    if isinstance(exit_code, int) and exit_code == 126:
        return FailureCategory.PERMISSION_DENIED

    # ----- Keyword fallback -----
    lowered = output.lower()

    if action_type in ("apply_patch", "edit_file"):
        patch_signals = (
            "find text was not found",
            "missing exact text",
            "patch does not apply",
            "hunk failed",
            "hunk",
            "did not apply cleanly",
            "content mismatch",
        )
        if any(signal in lowered for signal in patch_signals):
            return FailureCategory.PATCH_FAILURE

    if action_type == "read_file" or any(
        phrase in lowered
        for phrase in ("does not exist", "missing file", "no such file", "file not found", "filenotfounderror")
    ):
        if "does not exist" in lowered or "missing file" in lowered or "no such file" in lowered or "file not found" in lowered or "filenotfounderror" in lowered:
            return FailureCategory.FILE_MISSING

    if "permission denied" in lowered or "access denied" in lowered:
        return FailureCategory.PERMISSION_DENIED

    if "dry-run mode skipped" in lowered:
        return FailureCategory.PERMISSION_DENIED

    if any(phrase in lowered for phrase in ("timed out", "timeout", "deadline exceeded")):
        return FailureCategory.TIMEOUT

    if any(phrase in lowered for phrase in ("database is locked", "database locked", "sqlite")):
        return FailureCategory.DATABASE_LOCKED

    if any(phrase in lowered for phrase in ("budget exhausted", "budget exceeded", "token limit")):
        return FailureCategory.BUDGET_EXCEEDED

    if action_type == "search" and any(phrase in lowered for phrase in ("no matches", "no results")):
        return FailureCategory.SEARCH_NO_RESULTS

    if "no web results" in lowered:
        return FailureCategory.SEARCH_NO_RESULTS

    if any(phrase in lowered for phrase in (
        "symbol not found", "undefined reference", "name error", "not defined",
    )):
        return FailureCategory.SYMBOL_MISSING

    if action_type == "run_shell" and any(phrase in lowered for phrase in (
        "failed", "error", "assertion", "traceback",
    )):
        return FailureCategory.VERIFICATION_FAILURE

    if any(phrase in lowered for phrase in ("provider", "api error", "rate limit", "quota")):
        return FailureCategory.PROVIDER_FAILURE

    return FailureCategory.UNKNOWN


def _classify_from_error_code(code: str) -> FailureCategory | None:
    """Map a structured error code to a failure category."""
    code_lower = code.lower()
    mapping: dict[str, FailureCategory] = {
        "patch_failure": FailureCategory.PATCH_FAILURE,
        "file_missing": FailureCategory.FILE_MISSING,
        "file_not_found": FailureCategory.FILE_MISSING,
        "permission_denied": FailureCategory.PERMISSION_DENIED,
        "timeout": FailureCategory.TIMEOUT,
        "database_locked": FailureCategory.DATABASE_LOCKED,
        "budget_exceeded": FailureCategory.BUDGET_EXCEEDED,
        "provider_error": FailureCategory.PROVIDER_FAILURE,
        "rate_limit": FailureCategory.PROVIDER_FAILURE,
        "symbol_missing": FailureCategory.SYMBOL_MISSING,
        "verification_failure": FailureCategory.VERIFICATION_FAILURE,
        "parse_error": FailureCategory.PARSE_FAILURE,
        "search_empty": FailureCategory.SEARCH_NO_RESULTS,
    }
    return mapping.get(code_lower)
