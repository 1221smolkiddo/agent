"""Bounded summary boundary, using Agent47's existing secret checks/redaction."""
from __future__ import annotations

import re

from ..memory import reject_sensitive_memory
from ..safety import redact_secrets
from .config import ExperienceMemoryConfig
from .contracts import Experience

_BANK = re.compile(r"agent47-repo-[0-9a-f]{64}\Z")
_DUMP = re.compile(
    r"(?m)^\s*(?:[A-Z][A-Z0-9_]*\s*=|(?:export|import|from|def|class)\s+)|```|"
    r"-----BEGIN .*PRIVATE KEY-----"
)


def safe_text(value: str, config: ExperienceMemoryConfig) -> str:
    if config.api_key:
        value = value.replace(config.api_key.get_secret_value(), "[REDACTED]")
    return redact_secrets(value)


def valid_query(bank_id: str, query: str, config: ExperienceMemoryConfig) -> bool:
    return bool(
        _BANK.fullmatch(bank_id) and query.strip() and len(query) <= 2000
        and safe_text(query, config) == query and not _DUMP.search(query)
    )


def valid_experience(bank_id: str, experience: Experience, config: ExperienceMemoryConfig) -> bool:
    value = experience.summary
    if (
        not _BANK.fullmatch(bank_id) or not value.strip() or len(value) > 1000
        or "\n" in value or "\r" in value or _DUMP.search(value)
        or safe_text(value, config) != value
    ):
        return False
    try:
        reject_sensitive_memory(value)
    except ValueError:
        return False
    if experience.branch is not None and (
        len(experience.branch) > 200 or safe_text(experience.branch, config) != experience.branch
        or not re.fullmatch(r"[A-Za-z0-9_./-]+", experience.branch)
    ):
        return False
    return experience.head is None or bool(re.fullmatch(r"[0-9a-f]{40,64}", experience.head))
