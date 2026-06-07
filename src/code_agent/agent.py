from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from .models import ChatMessage, ModelClient
from .prompts import system_prompt
from .schema import AgentAction, FinalAction, ToolResult
from .storage import AgentStorage
from .status import StatusReporter
from .tools import ToolRegistry

ACTION_ADAPTER = TypeAdapter(AgentAction)


class CodingAgent:
    def __init__(
        self,
        cwd: Path,
        dry_run: bool,
        max_steps: int,
        max_failures: int,
        model_client: ModelClient,
        tools: ToolRegistry,
        storage: AgentStorage,
        reporter: StatusReporter | None = None,
    ) -> None:
        self.cwd = cwd
        self.dry_run = dry_run
        self.max_steps = max_steps
        self.max_failures = max_failures
        self.model_client = model_client
        self.tools = tools
        self.storage = storage
        self.reporter = reporter

    def run(self, task: str) -> str:
        run_id = self.storage.create_run(task=task, model=self.model_client.model, cwd=self.cwd)
        consecutive_failures = 0
        previous_tool_failed = False
        previous_failure_allows_final = False
        blocked_mutation_failure = False
        messages: list[ChatMessage] = [
            {"role": "system", "content": system_prompt(self.cwd, self.dry_run)},
            {"role": "user", "content": task},
        ]

        for step in range(1, self.max_steps + 1):
            self._report_thinking(step)
            response = self.model_client.complete(messages)
            action, parse_error = self._parse_action(response)
            if parse_error:
                consecutive_failures += 1
                payload = self._failure_payload(
                    step=step,
                    kind="parse_failure",
                    output=parse_error,
                    consecutive_failures=consecutive_failures,
                )
                self.storage.add_step(run_id, "assistant", {"raw": response})
                self.storage.add_step(run_id, "tool", payload)
                self._report_recovery("model returned invalid action JSON")
                if consecutive_failures >= self.max_failures:
                    return self._failure_summary(consecutive_failures, parse_error)
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": json.dumps(payload)})
                previous_tool_failed = True
                previous_failure_allows_final = False
                blocked_mutation_failure = False
                continue

            self.storage.add_step(run_id, "assistant", action.model_dump())

            if isinstance(action, FinalAction):
                if blocked_mutation_failure and self._final_claims_mutation_success(action.message):
                    consecutive_failures += 1
                    payload = self._failure_payload(
                        step=step,
                        kind="false_completion",
                        output=(
                            "A file write/edit/patch was blocked, but the final answer claimed the change was completed. "
                            "Do not claim success. Explain that the file was not created/edited/patched and tell the user "
                            "to enable /write or use /sandbox plus /write."
                        ),
                        consecutive_failures=consecutive_failures,
                    )
                    self.storage.add_step(run_id, "tool", payload)
                    self._report_recovery("blocked false completion after failed write/edit/patch")
                    if consecutive_failures >= self.max_failures:
                        return self._failure_summary(consecutive_failures, payload["output"])
                    messages.append({"role": "assistant", "content": action.model_dump_json()})
                    messages.append({"role": "user", "content": json.dumps(payload)})
                    continue
                if (
                    previous_tool_failed
                    and not previous_failure_allows_final
                    and consecutive_failures < self.max_failures
                ):
                    consecutive_failures += 1
                    payload = self._failure_payload(
                        step=step,
                        kind="premature_final",
                        output="A tool failed on the previous step. Diagnose and try another action before finalizing.",
                        consecutive_failures=consecutive_failures,
                    )
                    self.storage.add_step(run_id, "tool", payload)
                    self._report_recovery("previous tool failed; continuing instead of finalizing")
                    messages.append({"role": "assistant", "content": action.model_dump_json()})
                    messages.append({"role": "user", "content": json.dumps(payload)})
                    continue
                self._report_done()
                return action.message

            self._report_action(action)
            result = self._run_tool(action)
            changed_paths = self._changed_paths_from_action(action) if result.ok else []
            if result.ok:
                consecutive_failures = 0
                previous_tool_failed = False
                previous_failure_allows_final = False
                blocked_mutation_failure = False
            else:
                consecutive_failures += 1
                previous_tool_failed = True
                previous_failure_allows_final = self._can_finalize_after_failure(result)
                blocked_mutation_failure = self._is_blocked_mutation(action, result)

            tool_payload = {
                "type": "tool_result",
                "step": step,
                "ok": result.ok,
                "output": result.output,
                "recovery_instruction": self._recovery_instruction(result)
                if not result.ok
                else "Continue with the task.",
                "consecutive_failures": consecutive_failures,
            }
            if changed_paths:
                tool_payload["changed_paths"] = changed_paths
                tool_payload["verification_instruction"] = (
                    "Call suggest_verification with these changed_paths, then run the most focused "
                    "suggested command when verification is useful for the task."
                )
            self.storage.add_step(run_id, "tool", tool_payload)
            if consecutive_failures >= self.max_failures:
                return self._failure_summary(consecutive_failures, result.output)
            if not result.ok:
                self._report_recovery("tool failed; asking model for another attempt")
            messages.append({"role": "assistant", "content": action.model_dump_json()})
            messages.append({"role": "user", "content": json.dumps(tool_payload)})

        return f"Stopped after {self.max_steps} steps. Increase --max-steps if the task needs more work."

    def _parse_action(self, raw: str) -> tuple[AgentAction | None, str | None]:
        try:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            data: Any = json.loads(raw[start:end])
            return ACTION_ADAPTER.validate_python(data), None
        except (ValueError, json.JSONDecodeError, ValidationError) as exc:
            return None, (
                "The model response was not a valid action JSON object. "
                f"Error: {exc}. Reply with one valid action JSON object and continue solving the task."
            )

    def _run_tool(self, action: AgentAction) -> ToolResult:
        try:
            return self.tools.run(action)
        except Exception as exc:
            return ToolResult(ok=False, output=str(exc))

    @staticmethod
    def _recovery_instruction(result: ToolResult) -> str:
        if "Permission denied" in result.output:
            return (
                "The user denied permission. Respect the denial, choose a read-only alternative, "
                "or explain why the task cannot proceed without permission."
            )
        if "Dry-run mode skipped" in result.output:
            return (
                "Dry-run prevented the requested action. Explain that the user must enable /write, "
                "or use sandbox plus write mode, then finalize with clear next steps."
            )
        return (
            "The tool failed. Diagnose the failure from the output, inspect more context if needed, "
            "then try a different action. Do not finalize until the task is solved or the failure budget is exhausted."
        )

    @staticmethod
    def _failure_payload(
        step: int,
        kind: str,
        output: str,
        consecutive_failures: int,
    ) -> dict[str, Any]:
        return {
            "type": kind,
            "step": step,
            "ok": False,
            "output": output,
            "consecutive_failures": consecutive_failures,
            "recovery_instruction": (
                "Diagnose the failure, choose a different valid action, and continue. "
                "Do not produce a final answer yet."
            ),
        }

    @staticmethod
    def _failure_summary(consecutive_failures: int, output: str) -> str:
        return (
            f"Stopped after {consecutive_failures} consecutive failures. "
            f"Last failure: {output}"
        )

    @staticmethod
    def _can_finalize_after_failure(result: ToolResult) -> bool:
        return "Permission denied" in result.output or "Dry-run mode skipped" in result.output

    @staticmethod
    def _is_blocked_mutation(action: AgentAction, result: ToolResult) -> bool:
        if action.type not in {"write_file", "edit_file", "apply_patch"}:
            return False
        return "Permission denied" in result.output or "Dry-run mode skipped" in result.output

    @staticmethod
    def _changed_paths_from_action(action: AgentAction) -> list[str]:
        if action.type in {"write_file", "edit_file"}:
            path = getattr(action, "path", "")
            return [path] if path else []
        if action.type == "apply_patch":
            patch = getattr(action, "patch", "")
            return sorted(ToolRegistry._paths_from_patch(patch))
        return []

    @staticmethod
    def _final_claims_mutation_success(message: str) -> bool:
        lowered = message.lower()
        honest_failure_terms = [
            "could not",
            "couldn't",
            "cannot",
            "can't",
            "did not",
            "didn't",
            "not created",
            "not edited",
            "not written",
            "failed",
            "skipped",
            "dry-run",
            "permission",
            "denied",
            "/write",
            "write mode",
        ]
        if any(term in lowered for term in honest_failure_terms):
            return False

        success_terms = [
            "created",
            "made",
            "wrote",
            "written",
            "saved",
            "updated",
            "edited",
            "committed",
            "you can find",
            "file is named",
            "file named",
            "the file",
        ]
        return any(term in lowered for term in success_terms)

    def _report_thinking(self, step: int) -> None:
        if self.reporter:
            self.reporter.thinking(step)

    def _report_action(self, action: AgentAction) -> None:
        if self.reporter:
            self.reporter.action(action)

    def _report_recovery(self, detail: str) -> None:
        if self.reporter:
            self.reporter.recovery(detail)

    def _report_done(self) -> None:
        if self.reporter:
            self.reporter.done()
