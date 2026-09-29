from __future__ import annotations

import re
import json
from uuid import uuid4
from .safety import sanitize_payload, redact_secrets
from dataclasses import dataclass, field

from .agent import AgentRunResult
from .diff_types import DiffViewMode


FILE_REF_PATTERN = re.compile(
    r"\b[\w./-]+\.(?:py|md|txt|rst|json|toml|yaml|yml|js|jsx|ts|tsx|css|html)\b",
    re.IGNORECASE,
)


@dataclass
class SessionState:
    session_id: str = field(default_factory=lambda: uuid4().hex)
    current_plan: list[dict] = field(default_factory=list)
    verified_evidence: list[dict] = field(default_factory=list)
    durable_execution_id: str | None = None
    persistent_goal: str | None = None
    current_task: str | None = None
    architecture_decisions: list[str] = field(default_factory=list)
    failed_approaches: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    recent_corrections: list[str] = field(default_factory=list)
    pending_user_info: str | None = None
    target_files: list[str] = field(default_factory=list)
    last_created_files: list[str] = field(default_factory=list)
    last_edited_files: list[str] = field(default_factory=list)
    last_deleted_files: list[str] = field(default_factory=list)
    last_tool_results: list[str] = field(default_factory=list)
    last_blocker: str | None = None
    last_run_id: int | None = None
    previous_status: str | None = None
    conversation_steering: str | None = None
    last_diff: str | None = None
    preferred_diff_mode: DiffViewMode = DiffViewMode.UNIFIED

    def set_last_diff(self, diff_text: str | None) -> None:
        if diff_text and diff_text.strip():
            self.last_diff = diff_text.strip() + "\n"

    def set_preferred_diff_mode(self, mode: DiffViewMode | str) -> None:
        self.preferred_diff_mode = DiffViewMode.normalize(mode)

    def update(self, user_input: str, result: AgentRunResult) -> None:
        self.last_run_id = result.run_id
        self.durable_execution_id = result.durable_execution_id
        state = result.execution_state or {}
        if state.get("plan_steps"):
            self.current_plan = _bounded_checkpoint_value(sanitize_payload(state["plan_steps"]))[-12:]
            self.next_actions = [
                redact_secrets(str(item.get("step", ""))[:200])
                for item in self.current_plan if item.get("status") in {"pending", "in_progress"}
            ][:6]
        for item in state.get("verification_records", []):
            if item.get("status") == "passed":
                evidence = _bounded_checkpoint_value(sanitize_payload({**item, "run_id": result.run_id}))
                if evidence not in self.verified_evidence:
                    self.verified_evidence.append(evidence)
        self.verified_evidence = self.verified_evidence[-12:]
        self.last_blocker = self._blocker_from_result(result)
        self.previous_status = "blocked" if self.last_blocker else "completed"
        if self.persistent_goal is None:
            self.set_goal(user_input)
        self.current_task = redact_secrets(user_input[:2000])
        if user_input.strip().lower().startswith(("correction:", "actually,", "instead,", "please change")):
            correction = redact_secrets(" ".join(user_input.split())[:500])
            self.recent_corrections = (self.recent_corrections + [correction])[-4:]
        if result.failed_actions:
            last_failure = result.failed_actions[-1]
            self.record_failed_approach(
                str(last_failure.get("action", last_failure.get("kind", "action")))
                + ": " + str(last_failure.get("output", "failed"))
            )

        changed_paths = result.changed_paths
        if changed_paths:
            self.target_files = self._merge_unique(self.target_files, changed_paths)
            self.last_created_files = [
                record["path"]
                for record in result.mutation_records
                if record.get("ok") is True
                and (
                    record.get("action") == "write_file"
                    or (
                        record.get("action") == "move_file"
                        and record.get("operation") == "move_destination"
                    )
                )
            ]
            self.last_edited_files = [
                record["path"]
                for record in result.mutation_records
                if record.get("ok") is True and record.get("action") in {"edit_file", "apply_patch"}
            ]
            self.last_deleted_files = [
                record["path"]
                for record in result.mutation_records
                if record.get("ok") is True
                and (
                    record.get("action") == "delete_file"
                    or (
                        record.get("action") == "move_file"
                        and record.get("operation") == "move_source"
                    )
                )
            ]
            if self.last_deleted_files:
                self.last_created_files = [
                    path for path in self.last_created_files if path not in self.last_deleted_files
                ]
                self.last_edited_files = [
                    path for path in self.last_edited_files if path not in self.last_deleted_files
                ]
            self.pending_user_info = None
        else:
            mentioned_files = extract_file_refs(user_input + "\n" + result.message)
            self.target_files = self._merge_unique(self.target_files, mentioned_files)
            if self._asks_for_more_info(result.message):
                self.current_task = self.current_task or user_input
                self.pending_user_info = self._pending_info_summary(result.message)

        self.last_tool_results = self._summarize_tool_results(result)

    def render(self) -> str:
        rows: list[tuple[str, object]] = [("session_id", self.session_id)]
        if self.current_plan:
            rows.append(("current_plan", json.dumps(sanitize_payload(self.current_plan))))
        if self.verified_evidence:
            rows.append(("verified_evidence", json.dumps(sanitize_payload(self.verified_evidence))))
        if self.durable_execution_id:
            rows.append(("durable_execution_id", self.durable_execution_id))
        if self.persistent_goal:
            rows.append(("persistent_goal", self.persistent_goal))
        if self.architecture_decisions:
            rows.append(("architecture_decisions", json.dumps(self.architecture_decisions[-6:])))
        if self.failed_approaches:
            rows.append(("failed_approaches", json.dumps(self.failed_approaches[-6:])))
        if self.recent_corrections:
            rows.append(("recent_corrections", json.dumps(self.recent_corrections[-4:])))
        if self.next_actions:
            rows.append(("next_actions", json.dumps(self.next_actions[-6:])))
        if self.current_task:
            rows.append(("current_task", self.current_task))
        if self.pending_user_info:
            rows.append(("pending_user_info", self.pending_user_info))
        if self.target_files:
            rows.append(("target_files", ", ".join(self.target_files[-6:])))
        if self.last_created_files:
            rows.append(("last_created_files", ", ".join(self.last_created_files)))
        if self.last_edited_files:
            rows.append(("last_edited_files", ", ".join(self.last_edited_files)))
        if self.last_deleted_files:
            rows.append(("last_deleted_files", ", ".join(self.last_deleted_files)))
        if self.last_blocker:
            rows.append(("last_blocker", self.last_blocker))
        if self.last_tool_results:
            rows.append(("last_tool_results", " | ".join(self.last_tool_results[-4:])))
        if self.last_run_id is not None:
            rows.append(("last_run_id", self.last_run_id))
        if self.previous_status:
            rows.append(("previous_status", self.previous_status))
        if self.conversation_steering:
            rows.append(("conversation_steering", self.conversation_steering))
        if not rows:
            return ""

        lines = ["Current interactive session state:"]
        lines.extend(f"- {key}: {value}" for key, value in rows)
        return redact_secrets("\n".join(lines))

    def save(self, storage) -> None:
        if self.last_run_id is None:
            return
        # Explicit structured checkpoint: no raw transcript, diff, traceback or reasoning.
        fields = ("session_id", "persistent_goal", "architecture_decisions", "failed_approaches",
                  "next_actions", "recent_corrections", "current_task", "pending_user_info", "target_files",
                  "last_created_files", "last_edited_files", "last_deleted_files",
                  "last_tool_results", "last_blocker", "last_run_id", "previous_status",
                  "conversation_steering", "current_plan", "verified_evidence", "durable_execution_id")
        storage.add_step(self.last_run_id, "tool", {
            "type": "session_checkpoint", "state": sanitize_payload({k: getattr(self, k) for k in fields}),
        })

    @classmethod
    def restore(cls, storage, cwd):
        values = storage.latest_session_checkpoint(cwd)
        if values is not None:
            return cls(**{k: v for k, v in values.items() if k in cls.__dataclass_fields__})
        return cls()

    @staticmethod
    def _merge_unique(existing: list[str], incoming: list[str]) -> list[str]:
        merged = list(existing)
        for item in incoming:
            if item not in merged:
                merged.append(redact_secrets(str(item)[:240]))
        return merged[-12:]

    @staticmethod
    def _asks_for_more_info(message: str) -> bool:
        lowered = message.lower()
        return any(
            phrase in lowered
            for phrase in [
                "please provide",
                "provide the",
                "need the",
                "need to know",
                "i need",
                "could you provide",
                "what would you like",
            ]
        )

    @staticmethod
    def _pending_info_summary(message: str) -> str:
        first_line = next((line.strip() for line in message.splitlines() if line.strip()), message)
        return first_line[:240]

    @staticmethod
    def _blocker_from_result(result: AgentRunResult) -> str | None:
        if result.denied_actions:
            return "permission denied"
        if result.blocked and result.failed_actions:
            output = str(result.failed_actions[-1].get("output", "blocked"))
            return output[:240]
        if result.blocked:
            return "blocked"
        return None

    @staticmethod
    def _summarize_tool_results(result: AgentRunResult) -> list[str]:
        summaries: list[str] = []
        if result.changed_paths:
            summaries.append("changed " + ", ".join(result.changed_paths))
        for item in result.verification_results:
            summaries.append(f"{item['purpose']} {item['status']}")
        if result.failed_actions:
            action = result.failed_actions[-1].get("action", "tool")
            summaries.append(f"{action} failed")
        return [redact_secrets(summary[:300]) for summary in summaries]

    def set_goal(self, goal: str) -> None:
        """An explicit goal edit replaces the stable project objective."""
        cleaned = " ".join(goal.strip().split())
        self.persistent_goal = redact_secrets(cleaned[:1000]) if cleaned else None

    def record_decision(self, decision: str) -> None:
        cleaned = redact_secrets(" ".join(decision.strip().split())[:300])
        if cleaned and cleaned not in self.architecture_decisions:
            self.architecture_decisions = (self.architecture_decisions + [cleaned])[-6:]

    def record_failed_approach(self, approach: str) -> None:
        cleaned = redact_secrets(" ".join(approach.strip().split())[:300])
        if cleaned and cleaned not in self.failed_approaches:
            self.failed_approaches = (self.failed_approaches + [cleaned])[-6:]

    def set_steering(self, guidance: str) -> None:
        cleaned = " ".join(guidance.strip().split())
        self.conversation_steering = cleaned[:500] if cleaned else None

    def clear_steering(self) -> None:
        self.conversation_steering = None


def extract_file_refs(text: str) -> list[str]:
    seen: list[str] = []
    for match in FILE_REF_PATTERN.finditer(text):
        value = match.group(0).strip(".,;:()[]{}'\"")
        if value not in seen:
            seen.append(value)
    return seen


def _bounded_checkpoint_value(value, depth: int = 0):
    """Keep checkpoint facts structured while bounding each nested field."""
    if depth >= 3:
        return redact_secrets(str(value)[:120])
    if isinstance(value, str):
        return redact_secrets(value[:200])
    if isinstance(value, list):
        return [_bounded_checkpoint_value(item, depth + 1) for item in value[-12:]]
    if isinstance(value, dict):
        return {
            str(key)[:80]: _bounded_checkpoint_value(item, depth + 1)
            for key, item in list(value.items())[:8]
        }
    return value
