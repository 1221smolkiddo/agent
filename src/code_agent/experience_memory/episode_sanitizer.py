"""Final privacy and size boundary for outbound engineering episodes."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from ..safety import redact_secrets
from .config import ExperienceMemoryConfig
from .episodes import EngineeringEpisode

MAX_EPISODE_BYTES = 16_384
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
_ENV_ASSIGNMENT = re.compile(r"(?i)\b[A-Z][A-Z0-9_]{2,}\s*=\s*(?:['\"]?)[^\s,'\"}]+")
_CREDENTIAL = re.compile(
    r"(?i)\b(api[_-]?key|password|passwd|secret|token|authorization)\b"
    r"\s*(?:is|:|=)\s*['\"]?[^\s,'\"}]+"
)
_URL_QUERY = re.compile(r"(?i)https?://[^\s]+\?[^\s]+")
_OPAQUE = re.compile(r"\b[A-Za-z0-9_+/=-]{48,}\b")


@dataclass(frozen=True)
class PreparedEpisode:
    content: str
    document_id: str
    operation_id: str
    branch: str | None
    head: str | None
    outcome: str
    agent_version: str

    def __repr__(self) -> str:
        return "PreparedEpisode(<redacted>)"



class MemorySanitizer:
    def __init__(self, config: ExperienceMemoryConfig) -> None:
        self.config = config

    def prepare(self, episode: EngineeringEpisode) -> PreparedEpisode:
        if not _IDENTIFIER.fullmatch(episode.execution_id):
            raise ValueError("Unsafe execution identity for memory.")
        subject = episode.task_id or str(episode.run_id)
        if not _IDENTIFIER.fullmatch(subject):
            raise ValueError("Unsafe task identity for memory.")
        document_id = f"agent47:{episode.execution_id}:{subject}:episode:v1"
        payload = self._sanitize(asdict(episode))
        # A Git SHA-256 HEAD is a validated provenance hash, not an opaque secret.
        if episode.head is not None and re.fullmatch(r"[0-9a-f]{40,64}", episode.head):
            payload["head"] = episode.head
        payload["kind"] = "engineering_episode"
        payload["source"] = "agent47_runtime_evidence"
        # A bounded JSON document preserves the causal order and explicit labels.
        content = self._encode(payload)
        while len(content.encode("utf-8")) > MAX_EPISODE_BYTES:
            reduced = False
            for field in ("failed_approaches", "diagnostics", "verification", "plan",
                          "blockers", "changed_paths"):
                values = payload[field]
                if isinstance(values, list) and len(values) > 1:
                    if values[-1] == "<additional items truncated>":
                        values.pop(-2)
                    else:
                        values.pop()
                        values.append("<additional items truncated>")
                    reduced = True
                    break
            if not reduced:
                payload["final_result"] = "<truncated for episode size>"
                payload["goal"] = "<truncated for episode size>"
                content = self._encode(payload)
                if len(content.encode("utf-8")) > MAX_EPISODE_BYTES:
                    raise ValueError("Episode exceeds the outbound size limit.")
                break
            content = self._encode(payload)
        fingerprint = hashlib.sha256(content.encode("utf-8")).hexdigest()
        operation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, document_id + ":" + fingerprint))
        return PreparedEpisode(
            content=content, document_id=document_id, operation_id=operation_id,
            branch=payload["branch"], head=payload["head"],
            outcome=payload["outcome"], agent_version=payload["agent_version"],
        )

    def sanitize_text(self, value: str) -> str:
        return self._sanitize(value)

    def _sanitize(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._sanitize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._sanitize(item) for item in value]
        if isinstance(value, str):
            if "```" in value or "-----BEGIN" in value:
                return "[source or credential material omitted]"
            value = redact_secrets(value)
            if self.config.api_key:
                value = value.replace(self.config.api_key.get_secret_value(), "[REDACTED]")
            value = _URL_QUERY.sub("[URL query omitted]", value)
            value = _ENV_ASSIGNMENT.sub("[environment assignment omitted]", value)
            value = _CREDENTIAL.sub(lambda match: f"{match[1]}=[REDACTED]", value)
            value = _OPAQUE.sub("[opaque value omitted]", value)
            value = re.sub(r"(?i)(?:^|[/\\])\.env(?:\.[A-Za-z0-9_-]+)?", "[sensitive path]", value)
            return value
        return value

    @staticmethod
    def _encode(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
