from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from .models import ChatMessage, ModelClient
from .prompts import system_prompt
from .schema import AgentAction, FinalAction, RunShellAction, ToolResult, UpdatePlanAction
from .storage import AgentStorage
from .status import StatusReporter
from .tools import ToolRegistry
from .verification import select_verification_commands
from .work_report import build_work_report_payload, should_show_work_report

ACTION_ADAPTER = TypeAdapter(AgentAction)


@dataclass
class AgentRunResult:
    message: str
    run_id: int
    task: str = ""
    changed_paths: list[str] = field(default_factory=list)
    mutation_records: list[dict[str, Any]] = field(default_factory=list)
    command_records: list[dict[str, str | bool]] = field(default_factory=list)
    verification_results: list[dict[str, str | bool]] = field(default_factory=list)
    context_records: list[dict[str, Any]] = field(default_factory=list)
    model_usage_records: list[dict[str, Any]] = field(default_factory=list)
    plan_updates: list[dict[str, Any]] = field(default_factory=list)
    failed_actions: list[dict[str, Any]] = field(default_factory=list)
    denied_actions: list[dict[str, Any]] = field(default_factory=list)
    blocked: bool = False


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
        stream_model: bool = True,
    ) -> None:
        self.cwd = cwd
        self.dry_run = dry_run
        self.max_steps = max_steps
        self.max_failures = max_failures
        self.model_client = model_client
        self.tools = tools
        self.storage = storage
        self.reporter = reporter
        self.stream_model = stream_model

    def run(self, task: str) -> str:
        return self.run_detailed(task).message

    def run_detailed(self, task: str) -> AgentRunResult:
        run_id = self.storage.create_run(task=task, model=self.model_client.model, cwd=self.cwd)
        workspace_task = self._is_workspace_task(task)
        consecutive_failures = 0
        previous_tool_failed = False
        previous_failure_allows_final = False
        blocked_mutation_failure = False
        verification_results: list[dict[str, str | bool]] = []
        command_records: list[dict[str, str | bool]] = []
        context_records: list[dict[str, Any]] = []
        model_usage_records: list[dict[str, Any]] = []
        plan_updates: list[dict[str, Any]] = []
        mutation_records: list[dict[str, Any]] = []
        failed_actions: list[dict[str, Any]] = []
        denied_actions: list[dict[str, Any]] = []
        messages: list[ChatMessage] = [
            {"role": "system", "content": system_prompt(self.cwd, self.dry_run)},
            {"role": "user", "content": task},
        ]

        for step in range(1, self.max_steps + 1):
            self._report_thinking(step)
            try:
                response = self._complete_model(run_id, messages, step, model_usage_records)
            except Exception as exc:
                payload = self._failure_payload(
                    step=step,
                    kind="model_failure",
                    output=f"{type(exc).__name__}: {exc}",
                    consecutive_failures=consecutive_failures + 1,
                )
                self.storage.add_step(run_id, "tool", payload)
                failed_actions.append(payload)
                self._report_recovery("model failed; stopping run")
                return self._finalize_run(
                    AgentRunResult(
                        message=f"Stopped after a model failure: {payload['output']}",
                        run_id=run_id,
                        task=task,
                        command_records=command_records,
                        verification_results=verification_results,
                        context_records=context_records,
                        model_usage_records=model_usage_records,
                        plan_updates=plan_updates,
                        failed_actions=failed_actions,
                        denied_actions=denied_actions,
                        blocked=True,
                    )
                )
            action, parse_error = self._parse_action(response)
            if parse_error:
                if not workspace_task and self._can_use_raw_final(response):
                    self.storage.add_step(run_id, "assistant", {"raw": response})
                    self._report_done()
                    return self._finalize_run(
                        AgentRunResult(
                            message=response.strip(),
                            run_id=run_id,
                            task=task,
                            model_usage_records=model_usage_records,
                        )
                    )
                consecutive_failures += 1
                payload = self._failure_payload(
                    step=step,
                    kind="parse_failure",
                    output=parse_error,
                    consecutive_failures=consecutive_failures,
                )
                self.storage.add_step(run_id, "assistant", {"raw": response})
                self.storage.add_step(run_id, "tool", payload)
                failed_actions.append(payload)
                self._report_recovery("invalid model action; retrying")
                if consecutive_failures >= self.max_failures:
                    return self._finalize_run(
                        AgentRunResult(
                            message=self._failure_summary(consecutive_failures, parse_error),
                            run_id=run_id,
                            task=task,
                            model_usage_records=model_usage_records,
                            failed_actions=failed_actions,
                            blocked=True,
                        )
                    )
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": json.dumps(payload)})
                previous_tool_failed = True
                previous_failure_allows_final = not workspace_task
                blocked_mutation_failure = False
                continue

            self.storage.add_step(run_id, "assistant", action.model_dump())

            if not workspace_task and self._is_workspace_action(action):
                consecutive_failures += 1
                payload = self._failure_payload(
                    step=step,
                    kind="non_workspace_tool_blocked",
                    output=(
                        "This user request does not appear to be about the local workspace. "
                        "Do not inspect or modify project files. Answer directly with final, or use web_search "
                        "only if current external information is needed."
                    ),
                    consecutive_failures=consecutive_failures,
                )
                self.storage.add_step(run_id, "tool", payload)
                failed_actions.append(payload)
                self._report_recovery("blocked workspace tool for non-workspace request")
                if consecutive_failures >= self.max_failures:
                    return self._finalize_run(
                        AgentRunResult(
                            message=self._failure_summary(consecutive_failures, payload["output"]),
                            run_id=run_id,
                            task=task,
                            model_usage_records=model_usage_records,
                            failed_actions=failed_actions,
                            blocked=True,
                        )
                    )
                messages.append({"role": "assistant", "content": action.model_dump_json()})
                messages.append({"role": "user", "content": json.dumps(payload)})
                previous_tool_failed = True
                previous_failure_allows_final = True
                blocked_mutation_failure = False
                continue

            if isinstance(action, UpdatePlanAction):
                self._report_action(action)
                plan_payload = self._plan_payload(step, action)
                plan_updates.append(plan_payload)
                self.storage.add_step(run_id, "tool", plan_payload)
                consecutive_failures = 0
                previous_tool_failed = False
                previous_failure_allows_final = False
                blocked_mutation_failure = False
                messages.append({"role": "assistant", "content": action.model_dump_json()})
                messages.append({"role": "user", "content": json.dumps(plan_payload)})
                continue

            if isinstance(action, FinalAction):
                final_claim_rejection = self._final_claim_rejection(action.message, mutation_records)
                if final_claim_rejection or (
                    blocked_mutation_failure and self._final_claims_mutation_success(action.message)
                ):
                    consecutive_failures += 1
                    payload = self._failure_payload(
                        step=step,
                        kind="false_completion",
                        output=final_claim_rejection
                        or (
                            "A file write/edit/patch was blocked, but the final answer claimed the change was completed. "
                            "Do not claim success. Explain that the file was not created/edited/patched and tell the user "
                            "to enable /write or use /sandbox plus /write."
                        ),
                        consecutive_failures=consecutive_failures,
                    )
                    self.storage.add_step(run_id, "tool", payload)
                    failed_actions.append(payload)
                    self._report_recovery("blocked false completion after failed write/edit/patch")
                    if consecutive_failures >= self.max_failures:
                        return self._finalize_run(
                            AgentRunResult(
                                message=self._failure_summary(consecutive_failures, payload["output"]),
                                run_id=run_id,
                                task=task,
                                changed_paths=self._successful_mutation_paths(mutation_records),
                                mutation_records=mutation_records,
                                command_records=command_records,
                                verification_results=verification_results,
                                context_records=context_records,
                                model_usage_records=model_usage_records,
                                plan_updates=plan_updates,
                                failed_actions=failed_actions,
                                denied_actions=denied_actions,
                                blocked=True,
                            )
                        )
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
                    failed_actions.append(payload)
                    self._report_recovery("previous tool failed; continuing instead of finalizing")
                    messages.append({"role": "assistant", "content": action.model_dump_json()})
                    messages.append({"role": "user", "content": json.dumps(payload)})
                    continue
                self._report_done()
                return self._finalize_run(
                    AgentRunResult(
                        message=self._with_verification_summary(action.message, verification_results),
                        run_id=run_id,
                        task=task,
                        changed_paths=self._successful_mutation_paths(mutation_records),
                        mutation_records=mutation_records,
                        command_records=command_records,
                        verification_results=verification_results,
                        context_records=context_records,
                        model_usage_records=model_usage_records,
                        plan_updates=plan_updates,
                        failed_actions=failed_actions,
                        denied_actions=denied_actions,
                        blocked=bool(failed_actions and not mutation_records),
                    )
                )

            before_mutation = self._mutation_state_for_action(action)
            self._report_action(action)
            result = self._run_tool(action)
            new_mutation_records = self._mutation_records_from_action(action, result, before_mutation)
            changed_paths = self._successful_mutation_paths(new_mutation_records)
            mutation_records.extend(new_mutation_records)
            verification_result = self._verification_result_from_action(action, result)
            command_record = self._command_record_from_action(action, result)
            context_record = self._context_record_from_action(action, result)
            if command_record:
                command_records.append(command_record)
            if context_record:
                context_records.append(context_record)
            if verification_result:
                verification_results.append(verification_result)
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
                failed_record = {
                    "action": action.type,
                    "output": result.output,
                }
                failed_actions.append(failed_record)
                if "Permission denied" in result.output:
                    denied_actions.append(failed_record)

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
                automatic_results = self._run_automatic_verification(
                    run_id=run_id,
                    step=step,
                    changed_paths=changed_paths,
                )
                if automatic_results:
                    verification_results.extend(automatic_results)
                    tool_payload["automatic_verification_results"] = automatic_results
                    if any(not item["ok"] for item in automatic_results):
                        consecutive_failures += 1
                        previous_tool_failed = True
                        previous_failure_allows_final = False
                        tool_payload["recovery_instruction"] = (
                            "Automatic verification failed. Inspect the failing output, patch the issue, "
                            "and rerun focused verification before finalizing."
                        )
                    else:
                        tool_payload["verification_instruction"] = (
                            "Automatic focused verification passed. Continue with the task or finalize honestly."
                        )
                else:
                    tool_payload["verification_instruction"] = (
                        "No automatic verification command was selected for these changed paths. "
                        "Call suggest_verification if more confidence is needed before finalizing."
                    )
            if verification_result:
                tool_payload["verification_result"] = verification_result
            if new_mutation_records:
                tool_payload["mutation_records"] = new_mutation_records
            self.storage.add_step(run_id, "tool", tool_payload)
            if consecutive_failures >= self.max_failures:
                return self._finalize_run(
                    AgentRunResult(
                        message=self._failure_summary(consecutive_failures, result.output),
                        run_id=run_id,
                        task=task,
                        changed_paths=self._successful_mutation_paths(mutation_records),
                        mutation_records=mutation_records,
                        command_records=command_records,
                        verification_results=verification_results,
                        context_records=context_records,
                        model_usage_records=model_usage_records,
                        plan_updates=plan_updates,
                        failed_actions=failed_actions,
                        denied_actions=denied_actions,
                        blocked=True,
                    )
                )
            if not result.ok:
                self._report_recovery("tool failed; asking model for another attempt")
            messages.append({"role": "assistant", "content": action.model_dump_json()})
            messages.append({"role": "user", "content": json.dumps(tool_payload)})

        return self._finalize_run(
            AgentRunResult(
                message=f"Stopped after {self.max_steps} steps. Increase --max-steps if the task needs more work.",
                run_id=run_id,
                task=task,
                changed_paths=self._successful_mutation_paths(mutation_records),
                mutation_records=mutation_records,
                command_records=command_records,
                verification_results=verification_results,
                context_records=context_records,
                model_usage_records=model_usage_records,
                plan_updates=plan_updates,
                failed_actions=failed_actions,
                denied_actions=denied_actions,
                blocked=True,
            )
        )

    def _finalize_run(self, result: AgentRunResult) -> AgentRunResult:
        if should_show_work_report(result):
            payload = build_work_report_payload(result)
            self.storage.save_work_report(result.run_id, payload["body"], payload)
        return result

    def _parse_action(self, raw: str) -> tuple[AgentAction | None, str | None]:
        try:
            data = self._extract_first_json_object(raw)
            return ACTION_ADAPTER.validate_python(data), None
        except (ValueError, json.JSONDecodeError, ValidationError) as exc:
            return None, (
                "The model response was not a valid action JSON object. "
                f"Error: {exc}. Reply with one valid action JSON object and continue solving the task."
            )

    @staticmethod
    def _extract_first_json_object(raw: str) -> Any:
        decoder = json.JSONDecoder()
        for index, char in enumerate(raw):
            if char != "{":
                continue
            try:
                data, _end = decoder.raw_decode(raw[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
        raise ValueError("No JSON object found in model response.")

    def _complete_model(
        self,
        run_id: int,
        messages: list[ChatMessage],
        step: int,
        model_usage_records: list[dict[str, Any]],
    ) -> str:
        stream_complete = getattr(self.model_client, "stream_complete", None)
        stream_started = False
        try:
            if not self.stream_model or stream_complete is None:
                return self.model_client.complete(messages)

            if self.reporter:
                self.reporter.model_stream_start(step)
                stream_started = True
            return stream_complete(messages, self._report_model_stream_chunk)
        finally:
            self._drain_model_usage(run_id, model_usage_records)
            if self.reporter and stream_started:
                self.reporter.model_stream_end()

    def _drain_model_usage(
        self,
        run_id: int,
        model_usage_records: list[dict[str, Any]],
    ) -> None:
        drain = getattr(self.model_client, "drain_usage_records", None)
        if drain is None:
            return
        for record in drain():
            payload = record.as_dict()
            model_usage_records.append(payload)
            self.storage.add_model_usage(run_id, payload)

    def _report_model_stream_chunk(self, chunk: str) -> None:
        if self.reporter:
            self.reporter.model_stream_chunk(chunk)

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
        if "No web results were found" in result.output:
            return (
                "The web search did not find results. Revise the query once with clearer terms, "
                "or answer from stable general knowledge if the question does not require current information. "
                "Do not inspect workspace files for a non-workspace question."
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
    def _plan_payload(step: int, action: UpdatePlanAction) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "plan_updated",
            "step": step,
            "ok": True,
            "steps": [item.model_dump(exclude_none=True) for item in action.steps],
            "output": CodingAgent._plan_summary(action),
        }
        for field_name in ["target_files", "owned_files", "checks", "blockers", "risk_notes"]:
            values = getattr(action, field_name)
            if values:
                payload[field_name] = values
        return payload

    @staticmethod
    def _plan_summary(action: UpdatePlanAction) -> str:
        rendered = [
            f"{index}. {item.status}: {item.step}"
            for index, item in enumerate(action.steps, start=1)
        ]
        metadata: list[str] = []
        if action.target_files:
            metadata.append("targets=" + ", ".join(action.target_files))
        if action.owned_files:
            metadata.append("owned=" + ", ".join(action.owned_files))
        if action.checks:
            metadata.append("checks=" + ", ".join(action.checks))
        if action.blockers:
            metadata.append("blockers=" + ", ".join(action.blockers))
        if action.risk_notes:
            metadata.append("risks=" + ", ".join(action.risk_notes))
        suffix = " | " + " | ".join(metadata) if metadata else ""
        return "Plan updated: " + "; ".join(rendered) + suffix

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
    def _can_use_raw_final(response: str) -> bool:
        stripped = response.strip()
        return bool(stripped) and "{" not in stripped and "}" not in stripped

    @staticmethod
    def _is_workspace_task(task: str) -> bool:
        latest, transcript = CodingAgent._split_latest_task_and_transcript(task)
        lowered = latest.lower()
        context = f"{latest}\n{transcript}".lower() if transcript else lowered
        workspace_terms = [
            "repo",
            "repository",
            "project",
            "workspace",
            "codebase",
            "file",
            "read",
            "inspect",
            "search",
            "find",
            "folder",
            "directory",
            "readme",
            "docs",
            "test",
            "tests",
            "lint",
            "build",
            "fix",
            "bug",
            "implement",
            "add",
            "update",
            "edit",
            "change",
            "refactor",
            "commit",
            "diff",
            "patch",
            "run",
            "terminal",
            "cli",
        ]
        return CodingAgent._has_workspace_signal(context, workspace_terms)

    @staticmethod
    def _split_latest_task_and_transcript(task: str) -> tuple[str, str]:
        marker = "\nRecent interactive transcript for reference:\n"
        if marker not in task:
            return task, ""
        latest, transcript = task.split(marker, 1)
        return latest.strip(), transcript.strip()

    @staticmethod
    def _has_workspace_signal(text: str, workspace_terms: list[str]) -> bool:
        if any(CodingAgent._contains_workspace_term(text, term) for term in workspace_terms):
            return True
        return re.search(r"\b[\w.-]+\.(py|md|txt|rst|json|toml|yaml|yml|js|jsx|ts|tsx|css|html)\b", text) is not None

    @staticmethod
    def _contains_workspace_term(text: str, term: str) -> bool:
        if " " in term:
            return term in text
        return re.search(rf"\b{re.escape(term)}\b", text) is not None

    @staticmethod
    def _is_workspace_action(action: AgentAction) -> bool:
        return action.type in {
            "list_files",
            "read_file",
            "write_file",
            "edit_file",
            "apply_patch",
            "delete_file",
            "run_shell",
            "search",
            "summarize_code",
            "detect_verification",
            "suggest_verification",
            "inspect_git_diff",
            "repo_map",
            "rank_context",
        }

    @staticmethod
    def _is_blocked_mutation(action: AgentAction, result: ToolResult) -> bool:
        if action.type not in {"write_file", "edit_file", "apply_patch", "delete_file"}:
            return False
        return "Permission denied" in result.output or "Dry-run mode skipped" in result.output

    @staticmethod
    def _changed_paths_from_action(action: AgentAction) -> list[str]:
        if action.type in {"write_file", "edit_file"}:
            path = getattr(action, "path", "")
            return [path] if path else []
        if action.type == "delete_file":
            path = getattr(action, "path", "")
            return [path] if path else []
        if action.type == "apply_patch":
            patch = getattr(action, "patch", "")
            return sorted(ToolRegistry._paths_from_patch(patch))
        return []

    def _mutation_state_for_action(self, action: AgentAction) -> dict[str, dict[str, Any]]:
        if action.type not in {"write_file", "edit_file", "apply_patch", "delete_file"}:
            return {}
        state: dict[str, dict[str, Any]] = {}
        for path in self._changed_paths_from_action(action):
            try:
                target = self.tools.resolve_inside_workspace(path)
            except Exception:
                state[path] = {"exists": False, "content": None, "error": "path resolution failed"}
                continue
            try:
                state[path] = {
                    "exists": target.exists(),
                    "content": target.read_text(encoding="utf-8") if target.is_file() else None,
                }
            except (OSError, UnicodeDecodeError) as exc:
                state[path] = {"exists": target.exists(), "content": None, "error": str(exc)}
        return state

    def _mutation_records_from_action(
        self,
        action: AgentAction,
        result: ToolResult,
        before_mutation: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if action.type not in {"write_file", "edit_file", "apply_patch", "delete_file"}:
            return []
        paths = CodingAgent._changed_paths_from_action(action)
        if not paths:
            paths = ["<unknown>"]
        return [
            self._mutation_record_for_path(action, result, path, before_mutation.get(path, {}))
            for path in paths
        ]

    def _mutation_record_for_path(
        self,
        action: AgentAction,
        result: ToolResult,
        path: str,
        before: dict[str, Any],
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "action": action.type,
            "path": path,
            "ok": result.ok,
            "output": result.output,
        }
        if path == "<unknown>" or not result.ok:
            return record

        after = self._mutation_state_after_path(path)
        if "error" in after:
            record.update({"ok": False, "verified": False, "verification_error": after["error"]})
            return record

        before_exists = bool(before.get("exists"))
        before_content = before.get("content")
        after_exists = bool(after.get("exists"))
        after_content = after.get("content")
        content_changed = before_exists != after_exists or before_content != after_content
        record.update(
            {
                "verified": True,
                "exists_after": after_exists,
                "content_changed": content_changed,
            }
        )

        if action.type == "write_file":
            expected = getattr(action, "content", None)
            content_matches = after_exists and after_content == expected
            record["content_matches"] = content_matches
            record["ok"] = content_matches
            if not content_matches:
                record["verification_error"] = "write_file did not leave the requested content on disk"
        elif action.type in {"edit_file", "apply_patch"}:
            record["ok"] = after_exists and content_changed
            if not record["ok"]:
                record["verification_error"] = f"{action.type} did not change file content on disk"
        elif action.type == "delete_file":
            record["ok"] = before_exists and not after_exists
            record["content_changed"] = before_exists and not after_exists
            if after_exists:
                record["verification_error"] = "delete_file reported success but file still exists"
            elif not before_exists:
                record["verification_error"] = "delete_file target did not exist before mutation"
        return record

    def _mutation_state_after_path(self, path: str) -> dict[str, Any]:
        try:
            target = self.tools.resolve_inside_workspace(path)
        except Exception as exc:
            return {"exists": False, "content": None, "error": str(exc)}
        try:
            return {
                "exists": target.exists(),
                "content": target.read_text(encoding="utf-8") if target.is_file() else None,
            }
        except (OSError, UnicodeDecodeError) as exc:
            return {"exists": target.exists(), "content": None, "error": str(exc)}

    @staticmethod
    def _successful_mutation_paths(mutation_records: list[dict[str, Any]]) -> list[str]:
        return sorted(
            {
                str(record["path"])
                for record in mutation_records
                if record.get("ok") is True and record.get("path")
            }
        )

    @staticmethod
    def _final_claim_rejection(message: str, mutation_records: list[dict[str, Any]]) -> str | None:
        if not CodingAgent._final_claims_mutation_success(message):
            return None
        successful = [record for record in mutation_records if record.get("ok") is True]
        if successful:
            return None
        failed = [record for record in mutation_records if record.get("ok") is False]
        if failed:
            paths = ", ".join(str(record.get("path", "<unknown>")) for record in failed)
            return (
                "The final answer claimed a file was created, edited, written, saved, updated, deleted, or removed, "
                f"but no mutation succeeded. Failed mutation target(s): {paths}. "
                "Correct the final answer honestly and do not provide a template as if it were saved."
            )
        return (
            "The final answer claimed a file was created, edited, written, saved, updated, deleted, or removed, "
            "but this run has no verified file mutation. Use write_file, edit_file, apply_patch, or delete_file first, "
            "or explain the blocker honestly."
        )

    @staticmethod
    def _verification_result_from_action(
        action: AgentAction, result: ToolResult
    ) -> dict[str, str | bool] | None:
        if action.type != "run_shell":
            return None
        command = getattr(action, "command", "")
        purpose = CodingAgent._verification_purpose(command)
        if purpose is None:
            return None
        return {
            "purpose": purpose,
            "command": command,
            "ok": result.ok,
            "status": "passed" if result.ok else "failed",
        }

    @staticmethod
    def _verification_purpose(command: str) -> str | None:
        normalized = command.lower()
        if any(token in normalized for token in ["pytest", " test", "npm run test", "cargo test", "go test"]):
            return "test"
        if any(token in normalized for token in ["ruff check", "eslint", " lint", "cargo clippy"]):
            return "lint"
        if any(token in normalized for token in ["mypy", "tsc", "typecheck", "npm run check"]):
            return "typecheck"
        if any(token in normalized for token in [" build", "npm run build", "cargo build", "go build", "uv build"]):
            return "build"
        return None

    @staticmethod
    def _command_record_from_action(
        action: AgentAction, result: ToolResult
    ) -> dict[str, str | bool] | None:
        if action.type != "run_shell":
            return None
        command = getattr(action, "command", "")
        return {
            "command": command,
            "ok": result.ok,
            "status": "passed" if result.ok else "failed",
            "output": result.output,
        }

    @staticmethod
    def _context_record_from_action(action: AgentAction, result: ToolResult) -> dict[str, Any] | None:
        if action.type not in {"repo_map", "rank_context"}:
            return None
        item: dict[str, Any] = {
            "action": action.type,
            "ok": result.ok,
            "status": "ok" if result.ok else "failed",
            "output": result.output,
        }
        task = getattr(action, "task", "")
        if task:
            item["task"] = task
        return item

    def _run_automatic_verification(
        self,
        run_id: int,
        step: int,
        changed_paths: list[str],
    ) -> list[dict[str, str | bool]]:
        commands, reason = select_verification_commands(self.cwd, changed_paths)
        results: list[dict[str, str | bool]] = []
        for command in commands:
            action = RunShellAction(type="run_shell", command=command.command)
            self._report_action(action)
            result = self._run_tool(action)
            item: dict[str, str | bool] = {
                "purpose": command.purpose,
                "command": command.command,
                "ok": result.ok,
                "status": "passed" if result.ok else "failed",
                "reason": reason,
                "output": result.output,
                "automatic": True,
            }
            results.append(item)
            self.storage.add_step(
                run_id,
                "tool",
                {
                    "type": "automatic_verification_result",
                    "step": step,
                    **item,
                },
            )
            if not result.ok:
                break
        return results

    @staticmethod
    def _with_verification_summary(
        message: str, verification_results: list[dict[str, str | bool]]
    ) -> str:
        if not verification_results:
            return message
        if "verification outcomes:" in message.lower():
            return message

        lines = ["", "Verification outcomes:"]
        for item in verification_results:
            lines.append(f"- {item['purpose']} `{item['command']}`: {item['status']}.")
        return message.rstrip() + "\n".join(lines)

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
            "deleted",
            "removed",
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
