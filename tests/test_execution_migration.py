from __future__ import annotations

import sqlite3

import pytest

from code_agent.agent import CodingAgent
from code_agent.config import Settings
from code_agent.durable_execution import (
    Command,
    DurableExecutionRuntime,
    EffectState,
    ExecutionEvent,
    EventUpcasterRegistry,
    ExecutionStatus,
    InvariantError,
    ProjectionBuilder,
    TaskState,
)
from code_agent.execution_adapters import (
    CallableTransactionalAdapter,
    FilesystemAdapter,
    GitAdapter,
    McpAdapter,
    ModelCallAdapter,
    ShellAdapter,
    TransactionalEffectRunner,
)
from code_agent.execution_contracts import (
    AdapterCapabilities,
    AdapterRegistry,
    CapabilityNegotiationError,
    CapabilityRequirement,
    EffectOutcome,
    GuaranteeLevel,
    IsolationLevel,
)
from code_agent.execution_observability import ExecutionInspector
from code_agent.execution_planning import MutationOnlyPlanner, PlanningService
from code_agent.execution_qualification import (
    FaultPoint,
    FaultInjectingAdapter,
    FaultScript,
    InjectedCrash,
    ReliabilityQualifier,
    ReliabilityTarget,
)
from code_agent.runtime_migration import (
    ExecutionControlPlane,
    ExecutionPlane,
    MigrationController,
    MigrationStateStore,
    PromotionPolicy,
    PromotionStage,
    ShadowDivergenceStore,
    ShadowRuntime,
    default_shadow_decision,
)
from code_agent.schema import ToolResult
from code_agent.storage import AgentStorage


def _runtime(tmp_path):
    return DurableExecutionRuntime(tmp_path / "executions.db")


def _execution(runtime, *, risk=0.0):
    return runtime.create_planned("goal", tasks=[{
        "id": "task", "title": "Task", "risk": risk, "criteria": ["done"],
    }])


def test_execution_freezes_compatibility_contract_and_replays_legacy_payload(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id = _execution(runtime)
    state = runtime.engine.state(execution_id)
    assert state.engine_version == "1.0.0"
    assert state.compatibility_version == "1"
    assert state.schema_version == 1
    legacy = ProjectionBuilder().replay("legacy", [ExecutionEvent(
        "legacy", 1, "ExecutionCreated", {"goal": "old", "budgets": {}},
        "event", "command", None, "correlation", "2026-01-01T00:00:00+00:00",
    )])
    assert legacy.compatibility_version == "1"
    with pytest.raises(InvariantError, match="Unsupported"):
        runtime.engine.create("future", compatibility_version="999")


def test_execution_mode_defaults_to_shadow_and_validates_values():
    assert Settings(agent_execution_mode="shadow").execution_mode == "shadow"
    assert Settings(agent_execution_mode="legacy").execution_mode == "legacy"
    with pytest.raises(RuntimeError, match="AGENT_EXECUTION_MODE"):
        Settings(agent_execution_mode="invalid").execution_mode


def test_snapshot_retains_execution_compatibility_versions(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id = _execution(runtime)
    runtime.engine.checkpoint(execution_id)
    runtime.engine._cache.clear()
    state = runtime.engine.state(execution_id)
    assert (state.engine_version, state.compatibility_version, state.schema_version) == (
        "1.0.0", "1", 1,
    )


def test_event_schema_upcaster_preserves_original_compatibility_semantics():
    upcasters = EventUpcasterRegistry()
    upcasters.register(
        "ExecutionCreated", 0,
        lambda payload: {
            "goal": payload["objective"], "budgets": {},
            "compatibility_version": "1", "engine_version": "0.9.0",
            "schema_version": 1,
        },
    )
    state = ProjectionBuilder(upcasters).replay("old", [ExecutionEvent(
        "old", 1, "ExecutionCreated", {"objective": "legacy objective"},
        "event", "command", None, "correlation", "2026-01-01T00:00:00+00:00", 0,
    )])
    assert state.goal == "legacy objective"
    assert state.compatibility_version == "1"
    assert state.engine_version == "0.9.0"


def test_capability_contract_validation_and_negotiation():
    with pytest.raises(ValueError, match="Retry-safe"):
        AdapterCapabilities(
            name="bad", effect_kinds=("shell",), retry_safe=True,
        ).validate()
    adapter = CallableTransactionalAdapter(
        AdapterCapabilities(
            name="shell", effect_kinds=("shell",), permissions=("shell.execute",),
            isolation_level=IsolationLevel.PROCESS,
            idempotency=GuaranteeLevel.PROVIDER,
            reconciliation=GuaranteeLevel.ADAPTER,
            compensation=GuaranteeLevel.BEST_EFFORT,
            retry_safe=True, durable=True,
        ),
        lambda request, _context: EffectOutcome(True, "committed", request["command"]),
    )
    registry = AdapterRegistry()
    registry.register(adapter)
    selected = registry.resolve(CapabilityRequirement(
        "shell", permissions=("shell.execute",), compensation=True,
    ))
    assert selected is adapter
    with pytest.raises(CapabilityNegotiationError):
        registry.resolve(CapabilityRequirement("mcp"))


def test_major_runtime_adapters_publish_complete_capability_contracts():
    class Tools:
        pass

    registry = AdapterRegistry()
    for adapter in (
        FilesystemAdapter(Tools()), ShellAdapter(Tools()), GitAdapter(Tools()),
        McpAdapter(Tools()), ModelCallAdapter(lambda _messages: "ok"),
    ):
        registry.register(adapter)
    capabilities = {item["name"]: item for item in registry.discover()}
    assert set(capabilities) == {"filesystem", "shell", "git", "mcp", "model-call"}
    assert capabilities["filesystem"]["compensation"] == "adapter"
    assert capabilities["shell"]["idempotency"] == "unsupported"
    assert capabilities["mcp"]["resource_accounting"] == (
        "wall_seconds", "network_requests", "tool_calls",
    )


def test_transactional_runner_journals_verifies_and_replays(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id = _execution(runtime)
    calls = {"count": 0}

    def execute(request, _context):
        calls["count"] += 1
        return EffectOutcome(True, "committed", request["value"], {"verified": True})

    registry = AdapterRegistry()
    registry.register(CallableTransactionalAdapter(
        AdapterCapabilities(
            name="generic", effect_kinds=("tool",),
            idempotency=GuaranteeLevel.ENGINE,
            reconciliation=GuaranteeLevel.ADAPTER,
            verification=GuaranteeLevel.ADAPTER,
            retry_safe=True, durable=True,
        ),
        execute,
        reconcile=lambda _prepared: EffectOutcome(True, "committed", "reconciled"),
    ))
    runner = TransactionalEffectRunner(runtime.engine, registry)
    criterion_id = runtime.engine.state(execution_id).tasks["task"].criteria[0]
    first = runner.run(
        execution_id, "task", {"value": "ok"}, CapabilityRequirement("tool"),
        idempotency_key="stable", criterion_ids=(criterion_id,),
    )
    second = runner.run(
        execution_id, "task", {"value": "ok"}, CapabilityRequirement("tool"),
        idempotency_key="stable", criterion_ids=(criterion_id,),
    )
    assert first.outcome.ok and second.replayed
    assert calls["count"] == 1
    assert first.evidence_ids


def test_unknown_adapter_effect_is_reconciled_before_retry(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id = _execution(runtime)
    calls = {"count": 0}

    def execute(_request, _context):
        calls["count"] += 1
        raise ConnectionError("response lost")

    registry = AdapterRegistry()
    registry.register(CallableTransactionalAdapter(
        AdapterCapabilities(
            name="network", effect_kinds=("network",),
            idempotency=GuaranteeLevel.PROVIDER,
            reconciliation=GuaranteeLevel.PROVIDER,
            retry_safe=True, durable=True,
        ),
        execute,
        reconcile=lambda _prepared: EffectOutcome(True, "committed", "provider confirms success"),
    ))
    runner = TransactionalEffectRunner(runtime.engine, registry)
    first = runner.run(
        execution_id, "task", {}, CapabilityRequirement("network"),
        idempotency_key="request-1",
    )
    assert first.outcome.status == "unknown"
    assert runtime.engine.state(execution_id).effects[first.effect_id].state == EffectState.UNKNOWN
    second = runner.run(
        execution_id, "task", {}, CapabilityRequirement("network"),
        idempotency_key="request-1",
    )
    assert second.outcome.ok and second.outcome.output == "provider confirms success"
    assert calls["count"] == 1


def test_shadow_mode_compares_decisions_without_running_mutations(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id = _execution(runtime)
    store = ShadowDivergenceStore(tmp_path / "executions.db")
    shadow = ShadowRuntime(
        runtime, store,
        lambda _kind, _legacy, _state: {"action": "read_file", "allowed": True},
    )
    divergence = shadow.observe(
        execution_id, "tool_selection", {"action": "write_file", "allowed": True},
        task_id="task",
    )
    assert divergence and store.metrics(execution_id) == {
        "samples": 1, "unexpected": 1, "critical": 0, "divergence_rate": 1.0,
    }
    assert not shadow.may_execute_independently({"type": "write_file"}, isolated=False)
    assert shadow.may_execute_independently({"type": "read_file"}, isolated=False)
    assert runtime.engine.state(execution_id).effects == {}


def test_legacy_agent_runs_authoritatively_while_shadow_records_comparisons(tmp_path):
    class Model:
        model = "fake"

        def __init__(self):
            self.responses = [
                '{"type":"read_file","path":"README.md"}',
                '{"type":"final","message":"inspected"}',
            ]

        def complete(self, _messages):
            return self.responses.pop(0)

    class Tools:
        def run(self, _action):
            return ToolResult(ok=True, output="read")

        def close(self):
            return 0

    runtime = _runtime(tmp_path)
    divergence_store = ShadowDivergenceStore(runtime.store.path)
    shadow = ShadowRuntime(runtime, divergence_store, default_shadow_decision)
    agent = CodingAgent(
        cwd=tmp_path, dry_run=False, max_steps=3, max_failures=2,
        model_client=Model(), tools=Tools(),  # type: ignore[arg-type]
        storage=AgentStorage(tmp_path / "agent.db"),
        durable_runtime=runtime, shadow_runtime=shadow,
    )
    result = agent.run_detailed("inspect README.md")
    assert result.message == "inspected"
    assert divergence_store.metrics(result.durable_execution_id) == {
        "samples": 3, "unexpected": 0, "critical": 0, "divergence_rate": 0.0,
    }
    assert runtime.engine.state(result.durable_execution_id).status == ExecutionStatus.COMPLETE


def test_promotion_is_sequential_and_metric_gated():
    controller = MigrationController()
    policy = PromotionPolicy(minimum_samples=10, maximum_divergence_rate=0.01)
    with pytest.raises(RuntimeError, match="Insufficient"):
        controller.promote(
            PromotionStage.PLANNING,
            {"samples": 2, "critical": 0, "divergence_rate": 0}, policy,
        )
    controller.promote(
        PromotionStage.PLANNING,
        {"samples": 100, "critical": 0, "divergence_rate": 0.005}, policy,
    )
    assert controller.engine_owns("planning")
    assert not controller.engine_owns("side_effects")
    with pytest.raises(ValueError, match="exactly one"):
        controller.promote(
            PromotionStage.SIDE_EFFECTS,
            {"samples": 100, "critical": 0, "divergence_rate": 0}, policy,
        )


def test_promotion_stage_is_durable_and_uses_shadow_metrics(tmp_path):
    path = tmp_path / "migration.db"
    divergences = ShadowDivergenceStore(path)
    for _index in range(10):
        divergences.record_comparison("execution", "planning", matched=True)
    state_store = MigrationStateStore(path)
    state = state_store.promote(
        PromotionStage.PLANNING, divergences,
        PromotionPolicy(minimum_samples=10, maximum_divergence_rate=0),
    )
    assert state["stage"] == "planning"
    assert MigrationStateStore(path).get()["stage_value"] == 2


def test_planner_uses_mutations_for_initial_and_incremental_planning(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id = runtime.engine.create("goal")
    planner = MutationOnlyPlanner()
    service = PlanningService(runtime.engine, planner)
    version = service.apply(execution_id, planner.initial("goal", [{
        "id": "a", "title": "A", "criteria": ["A done"],
    }]))
    assert version == 1
    proposal = planner.parse({
        "operation": "insert", "affected_subtree": "a", "rationale": "add check",
        "tasks": [{"id": "b", "title": "B", "dependencies": ["a"], "criteria": ["B done"]}],
    }, runtime.engine.state(execution_id))
    assert service.apply(execution_id, proposal) == 2
    with pytest.raises(ValueError, match="mutation"):
        planner.parse({"tasks": []}, runtime.engine.state(execution_id))


def test_control_and_execution_planes_have_separate_authority(tmp_path):
    runtime = _runtime(tmp_path)
    control = ExecutionControlPlane(runtime)
    execution_id = control.create("goal", tasks=[{
        "id": "task", "title": "Task", "criteria": ["done"],
    }])
    with pytest.raises(PermissionError):
        control.command(execution_id, "TransitionTask", {"task_id": "task", "to": "ready"})
    plane = ExecutionPlane(
        runtime,
        lambda _execution_id: (
            lambda task, _state, _cancel: {"ok": True, "summary": f"{task.id} done"}
        ),
        max_parallel=1,
    )
    assert plane.start(execution_id)
    assert not plane.start(execution_id)
    completed = plane.wait(execution_id, timeout=5)
    assert completed.status == ExecutionStatus.COMPLETE


def test_observability_answers_blocker_evidence_replan_model_and_budget_questions(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id = runtime.create_planned("goal", tasks=[
        {"id": "a", "title": "A", "estimated_cost": 3, "criteria": ["A done"]},
        {"id": "b", "title": "B", "dependencies": ["a"], "criteria": ["B done"]},
    ], budgets={"tokens": 1000})
    runtime.engine.dispatch(Command("RecordModelRoute", execution_id, {
        "task_id": "a", "model": "fast", "reason": "low risk",
    }))
    runtime.engine.dispatch(Command("ConsumeBudget", execution_id, {
        "scope": "execution", "kind": "tokens", "amount": 200,
    }))
    inspector = ExecutionInspector(runtime.store)
    state = runtime.engine.state(execution_id)
    report = inspector.explain(state)
    assert report["blocked"][0]["task_id"] == "b"
    assert len(report["unsatisfied_criteria"]) == 2
    assert report["model_decisions"][0]["model"] == "fast"
    assert report["budget_hotspots"][0]["utilization"] == 0.2
    assert report["critical_path"][0]["task_id"] == "a"
    assert inspector.replans(execution_id)[0]["version"] == 1


def test_corrupt_snapshot_is_quarantined_by_checksum(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id = _execution(runtime)
    runtime.engine.checkpoint(execution_id)
    with sqlite3.connect(runtime.store.path) as conn:
        conn.execute(
            "update execution_snapshots set payload='corrupt' where execution_id=?",
            (execution_id,),
        )
    runtime.engine._cache.clear()
    with pytest.raises(InvariantError, match="checksum"):
        runtime.engine.state(execution_id)
    assert runtime.engine.replay(execution_id).tasks["task"].state == TaskState.QUEUED


def test_execution_plane_reports_worker_failure_without_corrupting_stream(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id = _execution(runtime)

    def fail(_execution_id):
        def worker(_task, _state, _cancel):
            raise RuntimeError("worker lost")
        return worker

    plane = ExecutionPlane(runtime, fail, max_parallel=1)
    plane.start(execution_id)
    with pytest.raises(RuntimeError, match="worker lost"):
        plane.wait(execution_id, timeout=5)
    state = runtime.engine.replay(execution_id)
    assert state.tasks["task"].state == TaskState.RUNNING
    assert plane.status(execution_id)["error"] == "worker lost"


def test_execution_plane_recovers_orphaned_running_worker(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id = _execution(runtime)
    attempts = {"count": 0}

    def factory(_execution_id):
        def worker(_task, _state, _cancel):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("crashed")
            return {"ok": True, "summary": "recovered"}
        return worker

    plane = ExecutionPlane(runtime, factory, max_parallel=1)
    plane.start(execution_id)
    with pytest.raises(RuntimeError, match="crashed"):
        plane.wait(execution_id, timeout=5)
    assert plane.recover_active() == [execution_id]
    assert plane.wait(execution_id, timeout=5).status == ExecutionStatus.COMPLETE


def test_reliability_qualification_covers_every_event_boundary(tmp_path):
    qualifier = ReliabilityQualifier()
    report = qualifier.qualify_event_boundaries(tmp_path)
    target = ReliabilityTarget(scenarios=3)
    qualified, failures = report.qualifies(target)
    assert qualified, failures
    assert set(report.injected_faults) == {
        "before_persistence", "before_commit", "after_persistence",
    }


def test_fault_script_is_seeded_and_single_shot():
    script = FaultScript({FaultPoint.BEFORE_ADAPTER_EXECUTE}, probability=1)
    with pytest.raises(InjectedCrash):
        script.inject("before_adapter_execute")
    script.inject("before_adapter_execute")


def test_adapter_crash_after_external_call_becomes_unknown_then_reconciles(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id = _execution(runtime)
    base = CallableTransactionalAdapter(
        AdapterCapabilities(
            name="mcp-fault", effect_kinds=("mcp",),
            idempotency=GuaranteeLevel.PROVIDER,
            reconciliation=GuaranteeLevel.PROVIDER,
            retry_safe=True, durable=True,
        ),
        lambda _request, _context: EffectOutcome(True, "committed", "server changed state"),
        reconcile=lambda _prepared: EffectOutcome(True, "committed", "server confirms state"),
    )
    script = FaultScript({FaultPoint.AFTER_ADAPTER_EXECUTE})
    registry = AdapterRegistry()
    registry.register(FaultInjectingAdapter(base, script))
    runner = TransactionalEffectRunner(runtime.engine, registry)
    first = runner.run(
        execution_id, "task", {}, CapabilityRequirement("mcp"),
        idempotency_key="mcp-request",
    )
    assert first.outcome.status == "unknown"
    second = runner.run(
        execution_id, "task", {}, CapabilityRequirement("mcp"),
        idempotency_key="mcp-request",
    )
    assert second.outcome.ok and second.outcome.output == "server confirms state"
