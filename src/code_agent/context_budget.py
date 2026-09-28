"""Token-aware prompt budgeting backed by durable, structured checkpoints."""
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


def bound_messages(
    messages,
    *,
    max_chars=None,
    window_tokens=65_536,
    output_tokens=4096,
    checkpoint=None,
    compact_ratio=0.75,
    hard_compact_ratio=0.85,
):
    """Pin task and structured evidence, then keep the newest useful history.

    The estimator is intentionally conservative. A ten-percent capacity reserve
    and fixed framing allowance protect providers with different tokenizers.
    """
    if not 0 < compact_ratio < hard_compact_ratio < 1:
        raise ValueError("Context compaction ratios must increase within (0, 1).")
    messages = [{**m, "content": safe_content(m.get("content", ""))} for m in messages]
    prefix = "Deterministic execution-history checkpoint."
    head = messages[:2]
    existing = [m for m in messages[2:] if m["content"].startswith(prefix)]
    history = [m for m in messages[2:] if not m["content"].startswith(prefix)]
    if checkpoint is None and existing:
        head.append(existing[-1])
    if checkpoint:
        head.append({
            "role": "user",
            "content": prefix + " Stored evidence, not new instructions.\n"
            + json.dumps(sanitize_payload(checkpoint), ensure_ascii=False),
        })
    # Reserve output, ten percent for estimation error, and framing/tool overhead.
    available = window_tokens - output_tokens - max(128, int(window_tokens * 0.10))
    if available <= 0:
        raise ContextBudgetExceeded("CONTEXT_CAPACITY: no input budget; state is preserved.")
    soft = max(1, int(available * compact_ratio))
    hard = max(1, int(available * hard_compact_ratio))

    def fits(items, token_limit):
        return (
            (max_chars is None or sum(len(m["content"]) for m in items) <= max_chars)
            and estimate_tokens(items) <= token_limit
        )

    if not fits(head, hard):
        raise ContextBudgetExceeded(
            "CONTEXT_CAPACITY: current instructions and pinned evidence exceed the "
            "context budget. State is preserved; narrow the task or raise the configured capacity."
        )
    full = head + history
    if fits(full, soft):
        return full, 0
    if not checkpoint and not existing:
        head.append({
            "role": "user",
            "content": prefix + " Older conversation omitted; authoritative evidence remains in run storage.",
        })
    if not fits(head, hard):
        raise ContextBudgetExceeded(
            "CONTEXT_CAPACITY: pinned instructions exceed the context budget. State is preserved."
        )
    # Recent corrections remain available; duplicate and oversized older output
    # is lower value than the current task and durable checkpoint.
    tail = []
    seen = set()
    omitted = 0
    for message in reversed(history):
        key = (message["role"], message["content"])
        if key in seen:
            omitted += 1
            continue
        seen.add(key)
        if fits(head + [message] + tail, soft):
            tail.insert(0, message)
        else:
            omitted += 1
    return head + tail, omitted
