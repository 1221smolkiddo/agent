from __future__ import annotations

import re
from dataclasses import dataclass, field

from .agent import AgentRunResult


FILE_REF_PATTERN = re.compile(
    r"\b[\w./-]+\.(?:py|md|txt|rst|json|toml|yaml|yml|js|jsx|ts|tsx|css|html)\b",
    re.IGNORECASE,
)


@dataclass
class SessionState:
    current_task: str | None = None
    pending_user_info: str | None = None
    target_files: list[str] = field(default_factory=list)
    last_created_files: list[str] = field(default_factory=list)
    last_edited_files: list[str] = field(default_factory=list)
    last_deleted_files: list[str] = field(default_factory=list)
    last_tool_results: list[str] = field(default_factory=list)
    last_blocker: str | None = None
    last_run_id: int | None = None
    previous_status: str | None = None

    def update(self, user_input: str, result: AgentRunResult) -> None:
        self.last_run_id = result.run_id
        self.last_blocker = self._blocker_from_result(result)
        self.previous_status = "blocked" if self.last_blocker else "completed"
        if self._looks_like_workspace_request(user_input):
            self.current_task = user_input

        changed_paths = result.changed_paths
        if changed_paths:
            self.target_files = self._merge_unique(self.target_files, changed_paths)
            self.last_created_files = [
                record["path"]
                for record in result.mutation_records
                if record.get("ok") is True and record.get("action") == "write_file"
            ]
            self.last_edited_files = [
                record["path"]
                for record in result.mutation_records
                if record.get("ok") is True and record.get("action") in {"edit_file", "apply_patch"}
            ]
            self.last_deleted_files = [
                record["path"]
                for record in result.mutation_records
                if record.get("ok") is True and record.get("action") == "delete_file"
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
        rows: list[tuple[str, object]] = []
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
        if not rows:
            return ""

        lines = ["Current interactive session state:"]
        lines.extend(f"- {key}: {value}" for key, value in rows)
        return "\n".join(lines)

    @staticmethod
    def _merge_unique(existing: list[str], incoming: list[str]) -> list[str]:
        merged = list(existing)
        for item in incoming:
            if item not in merged:
                merged.append(item)
        return merged[-12:]

    @staticmethod
    def _looks_like_workspace_request(text: str) -> bool:
        lowered = text.lower()
        return bool(
            extract_file_refs(text)
            or any(
                term in lowered
                for term in [
                    "project",
                    "repo",
                    "workspace",
                    "file",
                    "create",
                    "edit",
                    "update",
                    "fix",
                    "test",
                    "commit",
                ]
            )
        )

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
        return summaries


def extract_file_refs(text: str) -> list[str]:
    seen: list[str] = []
    for match in FILE_REF_PATTERN.finditer(text):
        value = match.group(0).strip(".,;:()[]{}'\"")
        if value not in seen:
            seen.append(value)
    return seen
