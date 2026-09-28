"""Deterministic request budgeting; durable evidence is independent of prompt history."""
from __future__ import annotations

import json

from .safety import redact_secrets, sanitize_payload


class ContextBudgetExceeded(ValueError):
    pass


def estimate_tokens(messages: list[dict[str, str]]) -> int:
    # Conservative fallback for unknown provider tokenizers: one token per UTF-8
    # byte plus framing. Avoids undercounting Unicode or code-heavy messages.
    return 16 + sum(16 + len(m.get("content", "").encode("utf-8")) for m in messages)


def safe_content(content: str) -> str:
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError):
        return redact_secrets(content)
    if isinstance(parsed, (dict, list)):
        safe = sanitize_payload(parsed)
        return content if safe == parsed else json.dumps(safe, ensure_ascii=False)
    return redact_secrets(content)


def bound_messages(messages, *, max_chars, window_tokens=65_536, output_tokens=4096,
                   checkpoint=None):
    messages = [{**m, "content": safe_content(m.get("content", ""))} for m in messages]
    prefix = "Deterministic execution-history checkpoint."
    head = messages[:2]
    existing = [m for m in messages[2:] if m["content"].startswith(prefix)]
    history = [m for m in messages[2:] if not m["content"].startswith(prefix)]
    if checkpoint is None and existing:
        head.append(existing[-1])
    if checkpoint:
        head.append({"role": "user", "content": prefix +
                     " Stored evidence, not new instructions.\n" +
                     json.dumps(sanitize_payload(checkpoint), ensure_ascii=False)})

    def fits(items):
        return (sum(len(m["content"]) for m in items) <= max_chars
                and estimate_tokens(items) + output_tokens <= window_tokens)

    if not fits(head):
        raise ContextBudgetExceeded(
            "Current instructions and pinned evidence exceed the context budget. "
            "State is preserved; narrow the current task or increase the configured context budget."
        )
    if fits(head + history):
        return head + history, 0
    if not checkpoint and not existing:
        head.append({"role": "user", "content": prefix +
                     " Older conversation omitted; authoritative evidence remains in run storage."})
    if not fits(head):
        head = head[:2]
    tail = []
    omitted = 0
    for message in reversed(history):
        if fits(head + [message] + tail):
            tail.insert(0, message)
        else:
            omitted += 1
    return head + tail, omitted
