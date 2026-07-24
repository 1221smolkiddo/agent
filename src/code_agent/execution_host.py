from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .durable_execution import (
    AgentExecutionAdapter,
    BlockedReason,
    Command,
    DurableExecutionRuntime,
    ExecutionProjection,
    ExecutionStatus,
    TaskState,
    VerificationStatus,
)
from .execution_adapters import (
    FilesystemAdapter,
    GitAdapter,
    McpAdapter,
    ShellAdapter,
    ToolRegistryAdapter,
    TransactionalEffectRunner,
)
from .execution_contracts import AdapterRegistry, CapabilityRequirement
from .execution_planning import MutationOnlyPlanner, PlanningService
from .models import ModelClient
from .runtime_migration import (
    ExecutionControlPlane,
    MigrationMode,
    MigrationStateStore,
    PromotionStage,
    ShadowDivergenceStore,
    ShadowRuntime,
    default_shadow_decision,
    plan_decision_from_legacy,
)
from .schema import ToolResult, UpdatePlanAction


class IndependentPlanProvider(Protocol):
    def plan(self, goal: str) -> list[dict[str, Any]]: ...


class DeterministicPlanProvider:
    """A provider-independent baseline planner used to qualify runtime semantics.

    It deliberately does not inspect the legacy model's plan. Model-backed planners can
    replace it through the same interface after the host boundary is stable.
    """

    READ_ONLY_TERMS = frozenset({
        "inspect", "review", "explain", "summarize", "analyse", "analyze", "find",
        "identify", "check", "audit", "investigate", "diagnose",
    })

    def plan(self, goal: str) -> list[dict[str, Any]]:
        normalized = " ".join(goal.split())
        lowered = normalized.lower()
        read_only = any(term in lowered.split() for term in self.READ_ONLY_TERMS)
        middle_title = (
            f"Produce findings for: {normalized}"
            if read_only
            else f"Implement the requested outcome: {normalized}"
        )
        middle_criterion = (
            "Findings directly answer the requested goal."
            if read_only
            else "The requested behavior is implemented in the workspace."
        )
        return [
            {
                "id": "engine-discover",
                "title": "Inspect the relevant workspace context and constraints",
                "criteria": ["Relevant context, constraints, and existing behavior are identified."],
                "dependencies": [],
                "priority": 30,
                "risk": 0.1,
                "metadata": {"planner": "deterministic-v1", "phase": "discover"},
            },
            {
                "id": "engine-execute",
                "title": middle_title,
                "criteria": [middle_criterion],
                "dependencies": ["engine-discover"],
                "priority": 20,
                "risk": 0.3 if read_only else 0.55,
                "metadata": {"planner": "deterministic-v1", "phase": "execute"},
            },
            {
                "id": "engine-verify",
                "title": "Verify the outcome against explicit acceptance evidence",
                "criteria": ["Verification evidence supports the reported outcome."],
                "dependencies": ["engine-execute"],
                "priority": 10,
                "risk": 0.15,
                "metadata": {"planner": "deterministic-v1", "phase": "verify"},
            },
        ]


class ModelPlanProvider:
    """Independent structured planner with a deterministic fail-safe."""

    def __init__(
        self,
        client: ModelClient,
        *,
        timeout_seconds: float = 60,
        fallback: IndependentPlanProvider | None = None,
    ) -> None:
        self.client = client
        self.timeout_seconds = timeout_seconds
        self.fallback = fallback or DeterministicPlanProvider()
        self.last_error: str | None = None
        self.fallback_used = False

    def plan(self, goal: str) -> list[dict[str, Any]]:
        prompt = (
            "Build an execution DAG for the supplied coding-agent goal. Return exactly one JSON "
            "object with a `tasks` array. Each task requires: id, title, dependencies (task IDs), "
            "criteria (concrete acceptance criteria), priority from 0-100, and risk from 0-1. "
            "Use 2-8 tasks, preserve a DAG, include repository discovery before mutation, and "
            "include verification after implementation. Do not return prose or tool calls.\n\n"
            f"Goal: {goal}"
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "You are Agent47's independent execution planner. You propose versioned graph "
                    "content but never perform side effects."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        try:
            complete_with_timeout = getattr(self.client, "complete_with_timeout", None)
            raw = (
                complete_with_timeout(messages, self.timeout_seconds)
                if callable(complete_with_timeout)
                else self.client.complete(messages)
            )
            payload = self._first_json_object(raw)
            tasks = payload.get("tasks")
            if not isinstance(tasks, list) or not 2 <= len(tasks) <= 8:
                raise ValueError("Planner response must contain between 2 and 8 tasks.")
            if any(not isinstance(item, dict) for item in tasks):
                raise ValueError("Every planner task must be a JSON object.")
            self.last_error = None
            self.fallback_used = False
            return [dict(item) for item in tasks]
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.fallback_used = True
            return self.fallback.plan(goal)

    def drain_usage_records(self) -> list[dict[str, Any]]:
        drain = getattr(self.client, "drain_usage_records", None)
        if not callable(drain):
            return []
        return [record.as_dict() for record in drain()]

    @staticmethod
    def _first_json_object(raw: str) -> dict[str, Any]:
        decoder = json.JSONDecoder()
        for index, char in enumerate(raw):
            if char != "{":
                continue
            try:
                payload, _end = decoder.raw_decode(raw[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        raise ValueError("No JSON object found in independent planner response.")


@dataclass(frozen=True)
class HostedActionResult:
    result: ToolResult
    effect_id: str
    evidence_ids: tuple[str, ...]
    replayed: bool


class ExecutionRuntimeHost:
    """Composition root for staged adoption of the durable execution runtime."""

    FILESYSTEM_ACTIONS = frozenset({
        "list_files", "read_file", "write_file", "edit_file", "apply_patch",
        "delete_file", "move_file", "list_transactions", "undo_transaction",
        "redo_transaction", "restore_snapshot", "recover_transactions",
    })
    FILE_WRITE_ACTIONS = frozenset({
        "write_file", "edit_file", "apply_patch", "delete_file", "move_file",
        "undo_transaction", "redo_transaction", "restore_snapshot", "recover_transactions",
    })
    SHELL_ACTIONS = frozenset({
        "run_shell", "start_process", "list_processes", "inspect_process",
        "read_process_logs", "process_events", "send_process_input", "stop_process",
        "restart_process",
    })
    MCP_ACTIONS = frozenset({"invoke_tool", "web_search"})
    GIT_ACTIONS = frozenset({"inspect_git_diff"})
    VERIFICATION_ACTIONS = frozenset({
        "detect_verification", "suggest_verification", "inspect_git_diff",
        "lsp_diagnostics", "lsp_status",
    })

    def __init__(
        self,
        db_path: Path,
        mode: MigrationMode | str,
        *,
        tools: Any,
        plan_provider: IndependentPlanProvider | None = None,
        recover_on_start: bool = True,
    ) -> None:
        self.mode = mode if isinstance(mode, MigrationMode) else MigrationMode(mode)
        if self.mode == MigrationMode.LEGACY:
            raise ValueError("The legacy runtime does not require an execution host.")
        self.runtime = DurableExecutionRuntime(db_path)
        self.divergences = ShadowDivergenceStore(self.runtime.store.path)
        self.migration = MigrationStateStore(self.runtime.store.path)
        self.plan_provider = plan_provider or DeterministicPlanProvider()
        self.planning = PlanningService(self.runtime.engine, MutationOnlyPlanner())
        self.shadow = ShadowRuntime(
            self.runtime, self.divergences, default_shadow_decision
        )
        self.control = ExecutionControlPlane(self.runtime)
        self.adapters = self._adapter_registry(tools)
        self.effects = TransactionalEffectRunner(self.runtime.engine, self.adapters)
        self.planning_diagnostics: list[dict[str, Any]] = []
        self._validate_authority()
        self.recovery_report = self.recover_active() if recover_on_start else []

    @property
    def stage(self) -> PromotionStage:
        return PromotionStage(self.migration.get()["stage_value"])

    def engine_owns(self, concern: str) -> bool:
        if self.mode != MigrationMode.PRIMARY:
            return False
        ownership = {
            PromotionStage.TRACE_PROJECTION: {"trace", "projection"},
            PromotionStage.PLANNING: {"trace", "projection", "planning", "graph"},
            PromotionStage.SCHEDULING_BUDGETS: {
                "trace", "projection", "planning", "graph", "scheduling", "budgets",
            },
            PromotionStage.VERIFICATION_REPLANNING: {
                "trace", "projection", "planning", "graph", "scheduling", "budgets",
                "verification", "diagnosis", "replanning",
            },
            PromotionStage.SIDE_EFFECTS: {
                "trace", "projection", "planning", "graph", "scheduling", "budgets",
                "verification", "diagnosis", "replanning", "side_effects", "approvals",
            },
            PromotionStage.RECOVERY_COMPLETION: {
                "trace", "projection", "planning", "graph", "scheduling", "budgets",
                "verification", "diagnosis", "replanning", "completion", "side_effects", "approvals", "recovery",
            },
            PromotionStage.ENGINE_ONLY: {"*"},
        }
        owned = ownership.get(self.stage, set())
        return "*" in owned or concern in owned

    def begin_legacy_run(
        self,
        goal: str,
        *,
        budgets: dict[str, float] | None = None,
        execution_id: str | None = None,
    ) -> AgentExecutionAdapter:
        if execution_id:
            state = self.runtime.recover(execution_id, resume_interrupted_tasks=True)
            if state.status != ExecutionStatus.ACTIVE:
                raise RuntimeError(
                    f"Execution {execution_id} cannot resume from {state.status.value}."
                )
            if state.goal != goal:
                raise RuntimeError("The resumed execution goal does not match the requested task.")
        else:
            execution_id = self.runtime.engine.create(goal, budgets=budgets)
            provider_error: str | None = None
            try:
                tasks = self.plan_provider.plan(goal)
                proposal = self.planning.planner.initial(goal, tasks)
                self.planning.apply(execution_id, proposal)
            except Exception as exc:
                provider_error = f"{type(exc).__name__}: {exc}"
                tasks = DeterministicPlanProvider().plan(goal)
                proposal = self.planning.planner.initial(goal, tasks)
                self.planning.apply(execution_id, proposal)
            self._record_planner_trace(execution_id, forced_error=provider_error)
        assigned_task_id = self._schedule_next(execution_id) if self.engine_owns("scheduling") else None
        return AgentExecutionAdapter(
            self.runtime,
            goal,
            execution_id=execution_id,
            budgets=budgets,
            mirror_all_tasks=not self.engine_owns("scheduling"),
            task_id=assigned_task_id,
        )

    def observe_plan(
        self,
        execution_id: str,
        action: UpdatePlanAction,
        *,
        task_id: str | None = None,
    ) -> None:
        self.shadow.observe(
            execution_id,
            "planning",
            plan_decision_from_legacy(action),
            task_id=task_id,
        )

    def ensure_plan_observed(
        self,
        execution_id: str,
        goal: str,
        *,
        task_id: str | None = None,
    ) -> None:
        if self.divergences.has_comparison(execution_id, "planning"):
            return
        implicit = UpdatePlanAction.model_validate({
            "type": "update_plan",
            "steps": [{
                "id": "legacy-root",
                "step": goal,
                "status": "in_progress",
                "acceptance_criteria": [f"Goal achieved: {goal}"],
            }],
            "rationale": "implicit legacy plan",
        })
        self.observe_plan(execution_id, implicit, task_id=task_id)

    def authoritative_plan(
        self,
        execution_id: str,
        progress: UpdatePlanAction | None = None,
    ) -> UpdatePlanAction:
        state = self.runtime.engine.state(execution_id)
        statuses = {
            "queued": "pending", "ready": "in_progress", "running": "in_progress",
            "waiting": "blocked", "blocked": "blocked", "verifying": "in_progress",
            "diagnosing": "blocked", "replanning": "in_progress", "verified": "completed",
            "complete": "completed", "failed": "blocked", "cancelled": "blocked",
        }
        progress_statuses = [step.status for step in progress.steps] if progress else []
        progress_is_complete = bool(progress_statuses) and all(
            status == "completed" for status in progress_statuses
        )
        steps: list[dict[str, Any]] = []
        in_progress_seen = False
        for index, task in enumerate(state.tasks.values()):
            status = statuses[task.state.value]
            if len(progress_statuses) == len(state.tasks):
                status = progress_statuses[index]
            elif progress_is_complete:
                status = "completed"
            if status == "in_progress":
                if in_progress_seen:
                    status = "pending"
                in_progress_seen = True
            steps.append({
                "id": task.id,
                "step": task.title,
                "status": status,
                "parent_id": task.parent_id,
                "depends_on": list(task.dependencies),
                "acceptance_criteria": [
                    state.criteria[criterion_id].description
                    for criterion_id in task.criteria
                ],
            })
        return UpdatePlanAction.model_validate({
            "type": "update_plan",
            "steps": steps,
            "rationale": "Engine-owned versioned execution graph.",
        })

    def planning_context(self, execution_id: str) -> str:
        if not self.engine_owns("planning"):
            return ""
        plan = self.authoritative_plan(execution_id)
        context = (
            "\n\nExecution engine authority: the following versioned plan is authoritative. "
            "Follow it and do not replace its task graph. Report discoveries through normal tool "
            "results; proposed replans are observations until accepted by the engine.\n"
            + plan.model_dump_json(exclude_none=True)
        )
        if self.engine_owns("scheduling"):
            state = self.runtime.engine.state(execution_id)
            active = [task for task in state.tasks.values() if task.state.value == "running"]
            if active:
                task = active[0]
                context += (
                    "\n\nExecution engine worker assignment: work only on task "
                    f"`{task.id}` ({task.title}). Its acceptance criteria are authoritative. "
                    "When that task is complete, stop; the scheduler will assign dependent work "
                    "on a later resume."
                )
        return context

    def recovery_context(self, execution_id: str) -> str:
        state = self.runtime.engine.state(execution_id)
        compressed = self.runtime.compressor.compress(state)
        compressed["ambiguous_effects"] = [
            {"effect_id": effect.id, "kind": effect.kind, "state": effect.state.value}
            for effect in state.effects.values()
            if effect.state.value == "unknown"
        ]
        encoded = json.dumps(compressed, sort_keys=True, default=str)
        if len(encoded) > 12_000:
            encoded = encoded[:12_000] + "<truncated>"
        return (
            "\n\nRecovered durable execution context (event log is authoritative). "
            "Do not repeat ambiguous effects. Reconcile them or choose a demonstrably safe "
            "alternative before continuing.\n" + encoded
        )

    def execute_action(
        self,
        execution_id: str,
        task_id: str,
        step: int,
        action: dict[str, Any],
        *,
        criterion_ids: tuple[str, ...] = (),
        timeout_seconds: float = 30,
    ) -> HostedActionResult:
        requirement, preferred = self._requirement(action)
        state = self.runtime.engine.state(execution_id)
        if self.engine_owns("scheduling"):
            task = state.tasks.get(task_id)
            if task is None or task.state.value != "running":
                raise RuntimeError("The execution scheduler has not assigned a running task.")
        else:
            task_id = self._task_for_action(state, task_id, action)
        criterion_ids = tuple(state.tasks[task_id].criteria) or criterion_ids
        self._consume_action_budget(execution_id, task_id, action)
        encoded = json.dumps(action, sort_keys=True, default=str)
        idempotency_key = hashlib.sha256(
            f"{execution_id}:{step}:{encoded}".encode()
        ).hexdigest()
        result = self.effects.run(
            execution_id,
            task_id,
            {"action": action},
            requirement,
            idempotency_key=idempotency_key,
            criterion_ids=criterion_ids,
            preferred_adapter=preferred,
            actor=(
                "legacy-authoritative-shadow"
                if self.mode == MigrationMode.SHADOW
                else "engine-plan-legacy-worker"
            ),
            timeout_seconds=timeout_seconds,
            authorize=(
                (lambda _effect_id, prepared: self._authorize_effect(
                    execution_id, task_id, prepared.kind, action
                )) if self.engine_owns("side_effects") else None
            ),
        )
        metadata = dict(result.outcome.metadata)
        metadata.update({
            "durable_effect_id": result.effect_id,
            "durable_replay": result.replayed,
            "effect_status": result.outcome.status,
            "evidence_ids": list(result.evidence_ids),
            "execution_task_id": task_id,
        })
        return HostedActionResult(
            ToolResult(
                ok=result.outcome.ok,
                output=result.outcome.output,
                metadata=metadata,
            ),
            result.effect_id,
            result.evidence_ids,
            result.replayed,
        )

    def finalize_worker_result(
        self,
        execution_id: str,
        task_id: str,
        *,
        worker_assessment: str,
        summary: str,
    ) -> ExecutionProjection:
        """Accept worker observations, then let the engine decide the task outcome.

        This is deliberately the only host path that can advance a worker-assigned
        task beyond RUNNING in verification-primary mode.
        """
        state = self.runtime.engine.state(execution_id)
        task = state.tasks[task_id]
        if task.state != TaskState.RUNNING:
            raise RuntimeError("Only the currently assigned running task can be finalized.")
        evidence_ids = sorted({
            evidence_id
            for criterion_id in task.criteria
            for evidence_id in state.criteria[criterion_id].evidence_ids
        })
        self.runtime.engine.dispatch(Command("RecordTaskExecutionResult", execution_id, {
            "task_id": task_id,
            "evidence_ids": evidence_ids,
            "artifacts": [], "observed_effects": evidence_ids,
            "metrics": {}, "warnings": [summary[:1000]] if summary else [],
            "worker_assessment": worker_assessment,
        }, actor="worker:legacy"))
        self.runtime.engine.dispatch(Command("TransitionTask", execution_id, {
            "task_id": task_id, "to": "verifying", "reason": "Worker observations submitted.",
        }))
        decisions: list[dict[str, Any]] = []
        state = self.runtime.engine.state(execution_id)
        for criterion_id in task.criteria:
            criterion = state.criteria[criterion_id]
            evidence = [state.evidence[item] for item in criterion.evidence_ids]
            decision = self.runtime.verifier.evaluate(criterion, evidence)
            event = self.runtime.engine.dispatch(Command("RecordVerificationDecision", execution_id, {
                "criterion_id": criterion_id,
                "evidence_ids": list(decision.evidence_ids),
                "policy": decision.policy, "decision": decision.decision.value,
                "confidence": decision.confidence,
                "reasoning_summary": decision.reasoning_summary,
                "latency_ms": decision.latency_ms, "cost": decision.cost,
                "blocked_reason": decision.blocked_reason,
            }))[0]
            decisions.append(event.payload)
        if all(item["decision"] == VerificationStatus.VERIFIED.value for item in decisions):
            self.runtime.engine.dispatch(Command("TransitionTask", execution_id, {
                "task_id": task_id, "to": "verified", "reason": "All immutable criteria verified.",
            }))
            self.runtime.engine.dispatch(Command("TransitionTask", execution_id, {
                "task_id": task_id, "to": "complete", "reason": "Engine accepted verification decisions.",
            }))
            if self.engine_owns("completion"):
                self._complete_execution_if_ready(execution_id)
        elif any(item["decision"] == VerificationStatus.BLOCKED.value for item in decisions):
            reason = next(item.get("blocked_reason") for item in decisions if item["decision"] == VerificationStatus.BLOCKED.value)
            self.runtime.engine.dispatch(Command("TransitionTask", execution_id, {
                "task_id": task_id, "to": "blocked", "reason": "Verification is blocked.",
                "blocked_reason": reason or BlockedReason.OTHER.value,
            }))
        else:
            self._diagnose_and_replan(execution_id, task_id, decisions)
        self.shadow.observe(
            execution_id, "verification",
            {"task_id": task_id, "decisions": [item["decision"] for item in decisions]},
            task_id=task_id, evidence_ids=tuple(evidence_ids),
        )
        return self.runtime.engine.state(execution_id)

    def _diagnose_and_replan(
        self, execution_id: str, task_id: str, decisions: list[dict[str, Any]]
    ) -> None:
        self.runtime.engine.dispatch(Command("TransitionTask", execution_id, {
            "task_id": task_id, "to": "diagnosing", "reason": "Verification did not accept the worker result.",
        }))
        state = self.runtime.engine.state(execution_id)
        diagnosis = self.runtime.diagnoser.diagnose(state.tasks[task_id], decisions)
        diagnosis_event = self.runtime.engine.dispatch(Command("RecordDiagnosis", execution_id, diagnosis))[0]
        diagnosis = diagnosis_event.payload
        self.shadow.observe(
            execution_id, "diagnosis",
            {"task_id": task_id, "classification": diagnosis["classification"]}, task_id=task_id,
        )
        task = self.runtime.engine.state(execution_id).tasks[task_id]
        if task.retries >= task.retry_limit:
            self.runtime.engine.dispatch(Command("TransitionTask", execution_id, {
                "task_id": task_id, "to": "failed", "reason": "Repair policy retry budget exhausted.",
            }))
            return
        self.runtime.engine.dispatch(Command("TransitionTask", execution_id, {
            "task_id": task_id, "to": "replanning", "reason": diagnosis["repair_strategy"],
        }))
        state = self.runtime.engine.state(execution_id)
        repair_task_id = f"repair-{task_id}-{task.retries + 1}"
        repair = self.runtime.engine.dispatch(Command("RecordRepairDecision", execution_id, {
            "task_id": task_id, "diagnosis_id": diagnosis["diagnosis_id"],
            "repair_policy": {"max_retries": task.retry_limit, "attempt": task.retries + 1},
            "reason": diagnosis["repair_strategy"], "expected_criteria": list(task.criteria),
        }))[0].payload
        dependents = [
            {"task_id": child.id, "dependencies": [repair_task_id if dep == task_id else dep for dep in child.dependencies]}
            for child in state.tasks.values() if task_id in child.dependencies
        ]
        criteria = [state.criteria[item].description for item in task.criteria]
        self.runtime.engine.dispatch(Command("MutateGraph", execution_id, {
            "base_version": state.graph_version, "operation": "insert",
            "affected_subtree": task_id, "rationale": diagnosis["repair_strategy"],
            "repair_decision_id": repair["repair_id"],
            "tasks": [{
                "id": repair_task_id, "title": f"Repair: {task.title}",
                "parent_id": task.parent_id, "dependencies": list(task.dependencies),
                "priority": task.priority, "risk": task.risk, "criteria": criteria,
                "retry_limit": task.retry_limit, "metadata": {"supersedes": task_id, "repair_id": repair["repair_id"]},
            }], "dependency_updates": dependents,
        }))
        self.runtime.engine.dispatch(Command("TransitionTask", execution_id, {
            "task_id": task_id, "to": "superseded", "reason": diagnosis["repair_strategy"],
        }))
        self.shadow.observe(
            execution_id, "replanning",
            {"task_id": task_id, "repair_task_id": repair_task_id, "repair_id": repair["repair_id"]},
            task_id=task_id,
        )

    def _complete_execution_if_ready(self, execution_id: str) -> None:
        state = self.runtime.engine.state(execution_id)
        if state.tasks and all(task.state in {TaskState.COMPLETE, TaskState.SUPERSEDED} for task in state.tasks.values()):
            self.runtime.engine.checkpoint(execution_id, "engine_verified_completion")
            self.runtime.engine.dispatch(Command("CompleteExecution", execution_id))

    def recover_active(self) -> list[dict[str, Any]]:
        recovered: list[dict[str, Any]] = []
        for item in self.runtime.store.executions(limit=10_000):
            execution_id = str(item["execution_id"])
            state = self.runtime.engine.state(execution_id)
            if state.status != ExecutionStatus.ACTIVE:
                continue
            restored = self.runtime.recover(execution_id, resume_interrupted_tasks=True)
            reconciled: list[str] = []
            if self.engine_owns("recovery"):
                reconciled = self._reconcile_unknown_effects(execution_id, restored)
                restored = self.runtime.engine.state(execution_id)
            recovered.append({
                "execution_id": execution_id,
                "status": restored.status.value,
                "unknown_effects": sorted(
                    effect.id
                    for effect in restored.effects.values()
                    if effect.state.value == "unknown"
                ),
                "reconciled_effects": reconciled,
            })
        return recovered

    def _reconcile_unknown_effects(
        self, execution_id: str, state: ExecutionProjection
    ) -> list[str]:
        reconciled: list[str] = []
        for effect in state.effects.values():
            if effect.state.value != "unknown":
                continue
            prepared = effect.request.get("prepared", {})
            action = prepared.get("action") if isinstance(prepared, dict) else None
            if not isinstance(action, dict):
                continue
            requirement, preferred = self._requirement(action)
            result = self.effects.run(
                execution_id, effect.task_id, {"action": action}, requirement,
                idempotency_key=effect.idempotency_key,
                criterion_ids=tuple(self.runtime.engine.state(execution_id).tasks[effect.task_id].criteria),
                preferred_adapter=preferred, actor="runtime-recovery",
                authorize=lambda _effect_id, prepared_effect: self._authorize_effect(
                    execution_id, effect.task_id, prepared_effect.kind, action
                ),
            )
            if result.outcome.status != "unknown":
                reconciled.append(effect.id)
        return reconciled

    def status(self) -> dict[str, Any]:
        migration = self.migration.get()
        return {
            "mode": self.mode.value,
            "stage": migration["stage"],
            "planning_authoritative": self.engine_owns("planning"),
            "scheduling_authoritative": self.engine_owns("scheduling"),
            "budgets_authoritative": self.engine_owns("budgets"),
            "verification_authoritative": self.engine_owns("verification"),
            "diagnosis_authoritative": self.engine_owns("diagnosis"),
            "replanning_authoritative": self.engine_owns("replanning"),
            "completion_authoritative": self.engine_owns("completion"),
            "side_effect_selection_authoritative": self.engine_owns("side_effects"),
            "recovered_executions": self.recovery_report,
            "planning_diagnostics": list(self.planning_diagnostics),
            "plan_provider": type(self.plan_provider).__name__,
            "adapters": self.adapters.discover(),
        }

    def _validate_authority(self) -> None:
        stage = self.stage
        if self.mode == MigrationMode.PRIMARY and stage.value < PromotionStage.PLANNING.value:
            raise RuntimeError(
                "AGENT_EXECUTION_MODE=primary requires promotion to the planning stage. "
                "Qualify shadow planning and run `code-agent execution promote planning` first."
            )
        if self.mode == MigrationMode.PRIMARY and stage.value > PromotionStage.RECOVERY_COMPLETION.value:
            raise RuntimeError(
                "This runtime host currently supports primary authority through effects, recovery, "
                "and completion; engine-only mode requires its final promotion gate."
            )
        if self.mode == MigrationMode.ENGINE_ONLY and stage != PromotionStage.ENGINE_ONLY:
            raise RuntimeError("Engine-only operation requires promotion through every authority stage.")

    def _record_planner_trace(
        self, execution_id: str, *, forced_error: str | None = None
    ) -> None:
        state = self.runtime.engine.state(execution_id)
        task_id = next(iter(state.tasks))
        error = forced_error or getattr(self.plan_provider, "last_error", None)
        fallback_used = bool(error) or bool(
            getattr(self.plan_provider, "fallback_used", False)
        )
        self.runtime.engine.dispatch(Command("RecordModelRoute", execution_id, {
            "task_id": task_id,
            "model": getattr(getattr(self.plan_provider, "client", None), "model", None)
            or type(self.plan_provider).__name__,
            "provider": "execution-plan-provider",
            "ok": not fallback_used,
            "reason": (
                "Independent planner fallback was used."
                if fallback_used
                else "Independent execution graph generated."
            ),
            "fallback_from": type(self.plan_provider).__name__ if fallback_used else None,
        }))
        if error:
            diagnostic = {
                "execution_id": execution_id,
                "provider": type(self.plan_provider).__name__,
                "fallback": type(getattr(self.plan_provider, "fallback", None)).__name__,
                "error": error,
            }
            self.planning_diagnostics.append(diagnostic)
            self.runtime.engine.dispatch(Command("RecordMemory", execution_id, {
                "kind": "failed_approach",
                "task_id": task_id,
                "summary": "Independent planner failed and used a deterministic fallback.",
                "payload": diagnostic,
            }))
        drain = getattr(self.plan_provider, "drain_usage_records", None)
        if not callable(drain):
            return
        for usage in drain():
            self.runtime.engine.dispatch(Command("RecordModelRoute", execution_id, {
                "task_id": task_id,
                "model": usage.get("model"),
                "provider": usage.get("provider"),
                "ok": bool(usage.get("ok")),
                "fallback_from": usage.get("fallback_from"),
                "reason": "Independent execution planning call.",
            }))
            for kind, value in (
                ("tokens", usage.get("total_tokens")),
                ("dollars", usage.get("estimated_cost_usd")),
            ):
                if value is not None and float(value) >= 0:
                    self.runtime.engine.dispatch(Command("ConsumeBudget", execution_id, {
                        "scope": "execution", "kind": kind, "amount": float(value),
                    }))

    def _schedule_next(self, execution_id: str) -> str:
        state = self.runtime.engine.state(execution_id)
        running = [task for task in state.tasks.values() if task.state.value == "running"]
        if running:
            return running[0].id
        ready = self.runtime.scheduler.ready(state)
        if not ready:
            raise RuntimeError("The execution scheduler found no dependency-ready task to assign.")
        task = ready[0]
        self.runtime.engine.dispatch(Command("ScheduleTask", execution_id, {
            "task_id": task.id,
            "policy": self.runtime.scheduler.policy.name,
            "worker": "legacy-worker",
            "reason": "Engine-owned scheduling and budget authority.",
        }))
        self.shadow.observe(
            execution_id,
            "scheduling",
            {"task_id": task.id, "policy": self.runtime.scheduler.policy.name},
            task_id=task.id,
        )
        return task.id

    def _consume_action_budget(
        self, execution_id: str, task_id: str, action: dict[str, Any]
    ) -> None:
        if not self.engine_owns("budgets"):
            return
        action_type = str(action.get("type", ""))
        kinds = ["tool_calls"]
        if action_type in self.SHELL_ACTIONS:
            kinds.append("shell_commands")
        if action_type in self.MCP_ACTIONS:
            kinds.append("network_requests")
        scope = f"execution/tasks/{task_id}"
        for kind in kinds:
            self.runtime.engine.dispatch(Command("ConsumeBudget", execution_id, {
                "scope": scope, "kind": kind, "amount": 1,
            }))
        self.shadow.observe(
            execution_id,
            "budget",
            {"scope": scope, "kinds": kinds, "amount": 1},
            task_id=task_id,
        )

    def _authorize_effect(
        self, execution_id: str, task_id: str, effect_kind: str, action: dict[str, Any]
    ) -> bool:
        """Runtime-only policy and approval gate for dispatched external effects."""
        action_type = str(action.get("type", ""))
        sensitive = (
            action_type in self.FILE_WRITE_ACTIONS
            or action_type in self.SHELL_ACTIONS
            or action_type in self.MCP_ACTIONS
            or action_type in self.GIT_ACTIONS
        )
        if not sensitive:
            return True
        scope = f"effect.{effect_kind}"
        state = self.runtime.engine.state(execution_id)
        approved = any(
            approval.granted_at and not approval.revoked
            and task_id in approval.task_ids and approval.scope == scope
            for approval in state.approvals.values()
        )
        if approved:
            self.shadow.observe(
                execution_id, "approval",
                {"task_id": task_id, "scope": scope, "authorized": True}, task_id=task_id,
            )
            return True
        pending = any(
            not approval.granted_at and not approval.revoked
            and task_id in approval.task_ids and approval.scope == scope
            for approval in state.approvals.values()
        )
        if not pending:
            self.runtime.engine.dispatch(Command("RequestApproval", execution_id, {
                "scope": scope, "risk": "high", "task_ids": [task_id],
                "reason": f"Runtime authorization is required before {effect_kind} dispatch.",
            }, actor="runtime-policy"))
        self.shadow.observe(
            execution_id, "approval",
            {"task_id": task_id, "scope": scope, "authorized": False}, task_id=task_id,
        )
        return False

    @staticmethod
    def _adapter_registry(tools: Any) -> AdapterRegistry:
        registry = AdapterRegistry()
        for adapter in (
            FilesystemAdapter(tools),
            ShellAdapter(tools),
            GitAdapter(tools),
            McpAdapter(tools),
            ToolRegistryAdapter(tools),
        ):
            registry.register(adapter)
        return registry

    def _requirement(
        self, action: dict[str, Any]
    ) -> tuple[CapabilityRequirement, str]:
        action_type = str(action.get("type", ""))
        if action_type in self.FILESYSTEM_ACTIONS:
            permissions = (
                ("file.read", "file.write", "file.delete")
                if action_type in self.FILE_WRITE_ACTIONS
                else ("file.read",)
            )
            return CapabilityRequirement(
                "filesystem", permissions=permissions, compensation=False
            ), "filesystem"
        if action_type in self.SHELL_ACTIONS:
            return CapabilityRequirement(
                "shell", permissions=("shell.execute",), idempotency=False,
                compensation=False, cancellation=True,
            ), "shell"
        if action_type in self.GIT_ACTIONS:
            return CapabilityRequirement(
                "git", permissions=("git.write",), compensation=False
            ), "git"
        if action_type in self.MCP_ACTIONS:
            return CapabilityRequirement(
                "mcp", permissions=("mcp.resource", "external.api", "network.access"),
                compensation=False,
            ), "mcp"
        return CapabilityRequirement("tool", compensation=False), "agent-tools"

    def _task_for_action(
        self,
        state: ExecutionProjection,
        fallback_task_id: str,
        action: dict[str, Any],
    ) -> str:
        action_type = str(action.get("type", ""))
        phase = "discover"
        if action_type in self.FILE_WRITE_ACTIONS or action_type == "update_memory":
            phase = "execute"
        elif action_type in self.VERIFICATION_ACTIONS:
            phase = "verify"
        elif action_type == "run_shell":
            command = str(action.get("command", "")).lower()
            phase = (
                "verify"
                if any(term in command for term in ("test", "lint", "check", "build", "pytest"))
                else "execute"
            )
        phase_terms = {
            "discover": ("discover", "inspect", "analyze", "analyse", "review"),
            "execute": ("execute", "implement", "change", "build", "fix", "write"),
            "verify": ("verify", "test", "check", "validate", "confirm"),
        }[phase]
        for task in state.tasks.values():
            if str(task.metadata.get("phase", "")) == phase:
                return task.id
        for task in state.tasks.values():
            title = task.title.lower()
            if any(term in title for term in phase_terms):
                return task.id
        task_ids = list(state.tasks)
        if phase == "verify" and task_ids:
            return task_ids[-1]
        if phase == "execute" and len(task_ids) > 1:
            return task_ids[len(task_ids) // 2]
        return fallback_task_id


PlanProviderCallable = Callable[[str], list[dict[str, Any]]]


class CallablePlanProvider:
    def __init__(self, provider: PlanProviderCallable) -> None:
        self.provider = provider

    def plan(self, goal: str) -> list[dict[str, Any]]:
        return self.provider(goal)
