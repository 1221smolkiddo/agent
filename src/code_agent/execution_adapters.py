from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter

from .durable_execution import Command, EffectState, ExecutionEngine
from .execution_contracts import (
    AdapterCapabilities,
    AdapterContext,
    AdapterEvidence,
    AdapterRegistry,
    CapabilityRequirement,
    CompensationOutcome,
    EffectOutcome,
    GuaranteeLevel,
    IsolationLevel,
    PreparedEffect,
)
from .schema import AgentAction, ToolResult


ACTION_ADAPTER = TypeAdapter(AgentAction)
ExecuteCallable = Callable[[dict[str, Any], AdapterContext], EffectOutcome]
ReconcileCallable = Callable[[PreparedEffect], EffectOutcome]
CompensateCallable = Callable[[PreparedEffect, EffectOutcome], CompensationOutcome]


class CallableTransactionalAdapter:
    def __init__(
        self,
        capabilities: AdapterCapabilities,
        execute: ExecuteCallable,
        *,
        reconcile: ReconcileCallable | None = None,
        compensate: CompensateCallable | None = None,
    ) -> None:
        self.capabilities = capabilities
        self._execute = execute
        self._reconcile = reconcile
        self._compensate = compensate
        self._cancelled: set[str] = set()
        self._lock = threading.RLock()

    def prepare(self, request: dict[str, Any], context: AdapterContext) -> PreparedEffect:
        if context.compatibility_version not in self.capabilities.compatibility_versions:
            raise ValueError("Adapter does not support this execution compatibility version.")
        encoded = json.dumps(request, sort_keys=True, default=str)
        fingerprint = hashlib.sha256(
            f"{self.capabilities.name}:{context.idempotency_key}:{encoded}".encode()
        ).hexdigest()
        return PreparedEffect(
            self.capabilities.name, context.metadata.get("effect_kind", "generic"),
            json.loads(encoded), context, fingerprint,
        )

    def execute(self, prepared: PreparedEffect) -> EffectOutcome:
        with self._lock:
            if prepared.fingerprint in self._cancelled:
                return EffectOutcome(False, "cancelled", "Effect was cancelled before execution.")
        return self._execute(dict(prepared.request), prepared.context)

    def reconcile(self, prepared: PreparedEffect) -> EffectOutcome:
        if self._reconcile is None:
            return EffectOutcome(False, "unknown", "Adapter cannot reconcile this effect.")
        return self._reconcile(prepared)

    def compensate(
        self, prepared: PreparedEffect, outcome: EffectOutcome
    ) -> CompensationOutcome:
        if self._compensate is None:
            return CompensationOutcome(False, "Adapter does not support compensation.")
        return self._compensate(prepared, outcome)

    def verify(
        self, prepared: PreparedEffect, outcome: EffectOutcome
    ) -> list[AdapterEvidence]:
        return [AdapterEvidence(
            kind=f"{prepared.kind}_result",
            summary=outcome.output[:1000] or f"{prepared.kind} returned no output",
            payload={"ok": outcome.ok, "status": outcome.status, **outcome.metadata},
        )]

    def cancel(self, prepared: PreparedEffect) -> bool:
        with self._lock:
            self._cancelled.add(prepared.fingerprint)
        return True


class ToolRegistryAdapter(CallableTransactionalAdapter):
    def __init__(
        self, tools: Any, capabilities: AdapterCapabilities | None = None
    ) -> None:
        self.tools = tools
        super().__init__(
            capabilities or AdapterCapabilities(
                name="agent-tools",
                effect_kinds=("tool", "filesystem", "shell", "git", "mcp", "network"),
                permissions=(
                    "file.read", "file.write", "file.delete", "shell.execute",
                    "git.write", "network.access", "mcp.resource", "external.api",
                ),
                isolation_level=IsolationLevel.WORKSPACE,
                idempotency=GuaranteeLevel.ENGINE,
                reconciliation=GuaranteeLevel.ADAPTER,
                compensation=GuaranteeLevel.ADAPTER,
                verification=GuaranteeLevel.ADAPTER,
                cancellation=GuaranteeLevel.ADAPTER,
                concurrency_safe=False,
                retry_safe=True,
                durable=True,
                resource_accounting=(
                    "wall_seconds", "tool_calls", "shell_commands", "network_requests",
                ),
            ),
            self._run_tool,
            reconcile=self._reconcile_tool,
            compensate=self._compensate_tool,
        )

    def _run_tool(self, request: dict[str, Any], _context: AdapterContext) -> EffectOutcome:
        action = ACTION_ADAPTER.validate_python(request["action"])
        result: ToolResult = self.tools.run(action)
        return EffectOutcome(
            result.ok, "committed" if result.ok else "failed", result.output,
            dict(result.metadata),
        )

    @staticmethod
    def _reconcile_tool(prepared: PreparedEffect) -> EffectOutcome:
        transaction = prepared.request.get("transaction")
        if transaction:
            return EffectOutcome(True, "committed", "Workspace transaction is durably recorded.", {"transaction": transaction})
        return EffectOutcome(False, "unknown", "Tool effect requires operator reconciliation.")

    def _compensate_tool(
        self, _prepared: PreparedEffect, outcome: EffectOutcome
    ) -> CompensationOutcome:
        transaction = outcome.metadata.get("transaction")
        transaction_id = transaction.get("id") if isinstance(transaction, dict) else None
        if not transaction_id:
            return CompensationOutcome(False, "Effect has no compensatable workspace transaction.")
        manager = getattr(self.tools, "transaction_manager", None)
        if manager is None:
            return CompensationOutcome(False, "Transaction manager is unavailable.")
        result = manager.undo(str(transaction_id))
        return CompensationOutcome(
            bool(result.ok), result.output, {"transaction_id": result.transaction_id}
        )


class FilesystemAdapter(ToolRegistryAdapter):
    def __init__(self, tools: Any) -> None:
        super().__init__(tools, AdapterCapabilities(
            name="filesystem", effect_kinds=("filesystem",),
            permissions=("file.read", "file.write", "file.delete"),
            isolation_level=IsolationLevel.WORKSPACE,
            idempotency=GuaranteeLevel.ENGINE,
            reconciliation=GuaranteeLevel.ADAPTER,
            compensation=GuaranteeLevel.ADAPTER,
            verification=GuaranteeLevel.ADAPTER,
            cancellation=GuaranteeLevel.BEST_EFFORT,
            retry_safe=True, durable=True,
            resource_accounting=("wall_seconds", "tool_calls"),
        ))


class ShellAdapter(ToolRegistryAdapter):
    def __init__(self, tools: Any) -> None:
        super().__init__(tools, AdapterCapabilities(
            name="shell", effect_kinds=("shell",), permissions=("shell.execute",),
            isolation_level=IsolationLevel.PROCESS,
            idempotency=GuaranteeLevel.UNSUPPORTED,
            reconciliation=GuaranteeLevel.BEST_EFFORT,
            compensation=GuaranteeLevel.UNSUPPORTED,
            verification=GuaranteeLevel.ADAPTER,
            cancellation=GuaranteeLevel.ADAPTER,
            retry_safe=False, durable=False,
            resource_accounting=("wall_seconds", "cpu_seconds", "shell_commands"),
        ))


class GitAdapter(ToolRegistryAdapter):
    def __init__(self, tools: Any) -> None:
        super().__init__(tools, AdapterCapabilities(
            name="git", effect_kinds=("git",), permissions=("git.write",),
            isolation_level=IsolationLevel.WORKSPACE,
            idempotency=GuaranteeLevel.PROVIDER,
            reconciliation=GuaranteeLevel.ADAPTER,
            compensation=GuaranteeLevel.BEST_EFFORT,
            verification=GuaranteeLevel.ADAPTER,
            cancellation=GuaranteeLevel.BEST_EFFORT,
            retry_safe=True, durable=True,
            resource_accounting=("wall_seconds", "tool_calls"),
        ))


class McpAdapter(ToolRegistryAdapter):
    def __init__(self, tools: Any) -> None:
        super().__init__(tools, AdapterCapabilities(
            name="mcp", effect_kinds=("mcp",),
            permissions=("mcp.resource", "external.api", "network.access"),
            isolation_level=IsolationLevel.PROCESS,
            idempotency=GuaranteeLevel.BEST_EFFORT,
            reconciliation=GuaranteeLevel.BEST_EFFORT,
            compensation=GuaranteeLevel.UNSUPPORTED,
            verification=GuaranteeLevel.ADAPTER,
            cancellation=GuaranteeLevel.BEST_EFFORT,
            streaming=True, concurrency_safe=True, retry_safe=True, durable=False,
            resource_accounting=("wall_seconds", "network_requests", "tool_calls"),
        ))


class ModelCallAdapter(CallableTransactionalAdapter):
    def __init__(self, complete: Callable[[list[dict[str, str]]], str]) -> None:
        super().__init__(
            AdapterCapabilities(
                name="model-call", effect_kinds=("model",), permissions=("external.api",),
                isolation_level=IsolationLevel.PROCESS,
                idempotency=GuaranteeLevel.ENGINE,
                reconciliation=GuaranteeLevel.BEST_EFFORT,
                verification=GuaranteeLevel.ADAPTER,
                cancellation=GuaranteeLevel.BEST_EFFORT,
                timeout_support=True, streaming=True, concurrency_safe=True,
                retry_safe=True, durable=False,
                resource_accounting=("tokens", "dollars", "wall_seconds", "network_requests"),
            ),
            lambda request, _context: EffectOutcome(
                True, "committed", complete(list(request.get("messages", [])))
            ),
        )


@dataclass(frozen=True)
class AdapterExecutionResult:
    effect_id: str
    outcome: EffectOutcome
    evidence_ids: tuple[str, ...]
    replayed: bool = False


class TransactionalEffectRunner:
    def __init__(self, engine: ExecutionEngine, registry: AdapterRegistry) -> None:
        self.engine = engine
        self.registry = registry

    def run(
        self,
        execution_id: str,
        task_id: str,
        request: dict[str, Any],
        requirement: CapabilityRequirement,
        *,
        idempotency_key: str,
        criterion_ids: tuple[str, ...] = (),
        preferred_adapter: str | None = None,
        actor: str = "primary",
        timeout_seconds: float = 30,
        lease_token: int | None = None,
    ) -> AdapterExecutionResult:
        state = self.engine.state(execution_id)
        adapter = self.registry.resolve(requirement, preferred=preferred_adapter)
        context = AdapterContext(
            execution_id, task_id, state.compatibility_version, actor,
            idempotency_key, timeout_seconds, requirement.permissions,
            {"effect_kind": requirement.effect_kind},
        )
        prepared = adapter.prepare(request, context)
        def command(kind: str, payload: dict[str, Any]) -> Command:
            return Command(
                kind, execution_id, payload, lease_token=lease_token, actor=actor
            )
        events = self.engine.dispatch(command("RequestEffect", {
            "task_id": task_id, "kind": requirement.effect_kind,
            "idempotency_key": idempotency_key,
            "request": {"adapter": adapter.capabilities.name, "prepared": prepared.request,
                        "fingerprint": prepared.fingerprint},
        }))
        effect_id = (
            events[0].payload["effect_id"] if events
            else self.engine.state(execution_id).effect_keys[idempotency_key]
        )
        effect = self.engine.state(execution_id).effects[effect_id]
        if effect.state in {EffectState.COMMITTED, EffectState.FAILED}:
            return AdapterExecutionResult(
                effect_id,
                EffectOutcome(
                    effect.state == EffectState.COMMITTED,
                    effect.state.value,
                    str(effect.result.get("output", "Replayed effect outcome.")),
                    dict(effect.result.get("metadata", {})),
                    effect.result.get("external_id"),
                ),
                (),
                replayed=True,
            )
        if effect.state == EffectState.UNKNOWN:
            outcome = adapter.reconcile(prepared)
        else:
            if effect.state == EffectState.PENDING:
                self.engine.dispatch(command("ChangeEffectState", {
                    "effect_id": effect_id, "state": "running",
                }))
            try:
                outcome = adapter.execute(prepared)
            except Exception as exc:
                outcome = EffectOutcome(False, "unknown", f"{type(exc).__name__}: {exc}")
        target = (
            "committed" if outcome.ok and outcome.status != "unknown"
            else "unknown" if outcome.status == "unknown"
            else "failed"
        )
        current = self.engine.state(execution_id).effects[effect_id]
        if current.state in {EffectState.RUNNING, EffectState.UNKNOWN}:
            self.engine.dispatch(command("ChangeEffectState", {
                "effect_id": effect_id, "state": target,
                "result": {
                    "ok": outcome.ok, "status": outcome.status, "output": outcome.output,
                    "metadata": outcome.metadata, "external_id": outcome.external_id,
                },
            }))
        evidence_ids: list[str] = []
        for evidence in adapter.verify(prepared, outcome):
            event = self.engine.dispatch(command("RecordEvidence", {
                "task_id": task_id, "kind": evidence.kind, "summary": evidence.summary,
                "payload": evidence.payload, "criterion_ids": list(criterion_ids),
            }))[0]
            evidence_ids.append(event.payload["evidence_id"])
        return AdapterExecutionResult(effect_id, outcome, tuple(evidence_ids))
