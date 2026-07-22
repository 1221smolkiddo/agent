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
from code_agent.execution_host import (
    CallablePlanProvider,
    ExecutionRuntimeHost,
    ModelPlanProvider,
)
from code_agent.models import ModelUsageRecord
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
    MigrationMode,
    MigrationStateStore,
    PromotionPolicy,
    PromotionStage,
    ShadowDivergenceStore,
    ShadowRuntime,
    default_shadow_decision,
)
from code_agent.schema import ToolResult, UpdatePlanAction
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
    assert Settings(agent_shadow_planner="model").shadow_planner == "model"
    with pytest.raises(RuntimeError, match="AGENT_EXECUTION_MODE"):
        Settings(agent_execution_mode="invalid").execution_mode
    with pytest.raises(RuntimeError, match="AGENT_SHADOW_PLANNER"):
        Settings(agent_shadow_planner="invalid").shadow_planner


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


def test_runtime_host_builds_independent_graph_and_transactions_legacy_actions(tmp_path):
    class Tools:
        def __init__(self):
            self.calls = 0

        def run(self, action):
            self.calls += 1
            return ToolResult(ok=True, output=f"read:{action.path}")

    tasks = [
        {"id": "inspect", "title": "Inspect", "criteria": ["Context captured"]},
        {
            "id": "report", "title": "Report", "dependencies": ["inspect"],
            "criteria": ["Report complete"],
        },
    ]
    tools = Tools()
    host = ExecutionRuntimeHost(
        tmp_path / "host.db",
        MigrationMode.SHADOW,
        tools=tools,
        plan_provider=CallablePlanProvider(lambda _goal: tasks),
        recover_on_start=False,
    )
    adapter = host.begin_legacy_run("inspect README")
    state = host.runtime.engine.state(adapter.execution_id)
    assert list(state.tasks) == ["inspect", "report"]
    assert state.tasks["inspect"].state == TaskState.RUNNING

    first = host.execute_action(
        adapter.execution_id,
        adapter.task_id,
        1,
        {"type": "read_file", "path": "README.md"},
        criterion_ids=(adapter.criterion_id,),
    )
    replay = host.execute_action(
        adapter.execution_id,
        adapter.task_id,
        1,
        {"type": "read_file", "path": "README.md"},
        criterion_ids=(adapter.criterion_id,),
    )
    assert first.result.ok and replay.replayed
    assert replay.effect_id == first.effect_id
    assert replay.evidence_ids == first.evidence_ids
    assert tools.calls == 1
    mutation = host.execute_action(
        adapter.execution_id,
        adapter.task_id,
        2,
        {"type": "write_file", "path": "report.md", "content": "done"},
        criterion_ids=(adapter.criterion_id,),
    )
    assert mutation.result.metadata["execution_task_id"] == "report"
    assert tools.calls == 2

    adapter.finish(blocked=False, summary="README inspected")
    completed = host.runtime.engine.replay(adapter.execution_id)
    assert completed.status == ExecutionStatus.COMPLETE
    assert all(task.state == TaskState.COMPLETE for task in completed.tasks.values())
    assert all(criterion.satisfied for criterion in completed.criteria.values())
    assert all(criterion.evidence_ids for criterion in completed.criteria.values())


def test_coding_agent_runs_through_host_and_records_an_independent_plan_sample(tmp_path):
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
        def __init__(self):
            self.calls = 0

        def run(self, _action):
            self.calls += 1
            return ToolResult(ok=True, output="README")

        def close(self):
            return 0

    tools = Tools()
    host = ExecutionRuntimeHost(
        tmp_path / "host.db", "shadow", tools=tools, recover_on_start=False
    )
    agent = CodingAgent(
        cwd=tmp_path,
        dry_run=False,
        max_steps=3,
        max_failures=2,
        model_client=Model(),  # type: ignore[arg-type]
        tools=tools,  # type: ignore[arg-type]
        storage=AgentStorage(tmp_path / "agent.db"),
        runtime_host=host,
    )
    result = agent.run_detailed("inspect README.md")
    state = host.runtime.engine.replay(result.durable_execution_id)
    assert result.message == "inspected" and tools.calls == 1
    assert state.status == ExecutionStatus.COMPLETE
    run_steps = agent.storage.run_steps_payloads(result.run_id)
    assert run_steps[0]["payload"] == {
        "type": "durable_execution_link",
        "execution_id": result.durable_execution_id,
        "goal": "inspect README.md",
        "resumed": False,
    }
    assert host.divergences.has_comparison(result.durable_execution_id, "planning")
    assert {item["decision_type"] for item in host.divergences.list(
        result.durable_execution_id
    )} >= {"planning"}


def test_runtime_host_compares_independent_plan_semantics(tmp_path):
    class Tools:
        def run(self, _action):
            return ToolResult(ok=True, output="ok")

    tasks = [
        {"id": "a", "title": "Inspect", "criteria": ["Context captured"]},
        {
            "id": "b", "title": "Report", "dependencies": ["a"],
            "criteria": ["Report complete"],
        },
    ]
    host = ExecutionRuntimeHost(
        tmp_path / "host.db",
        "shadow",
        tools=Tools(),
        plan_provider=CallablePlanProvider(lambda _goal: tasks),
        recover_on_start=False,
    )
    adapter = host.begin_legacy_run("goal")
    host.observe_plan(
        adapter.execution_id,
        UpdatePlanAction.model_validate({
            "type": "update_plan",
            "steps": [
                {
                    "id": "legacy-a", "step": "Inspect", "status": "in_progress",
                    "acceptance_criteria": ["Context captured"],
                },
                {
                    "id": "legacy-b", "step": "Report", "status": "pending",
                    "depends_on": ["legacy-a"],
                    "acceptance_criteria": ["Report complete"],
                },
            ],
        }),
        task_id=adapter.task_id,
    )
    assert host.divergences.metrics(
        adapter.execution_id, decision_types=("planning",)
    ) == {
        "samples": 1, "unexpected": 0, "critical": 0, "divergence_rate": 0.0,
    }


def test_model_plan_provider_is_independent_accounted_and_fail_safe(tmp_path):
    class Model:
        model = "planner-model"

        def __init__(self, response):
            self.response = response

        def complete(self, _messages):
            if isinstance(self.response, Exception):
                raise self.response
            return self.response

        def drain_usage_records(self):
            return [ModelUsageRecord(
                model=self.model,
                provider="test",
                ok=not isinstance(self.response, Exception),
                total_tokens=42,
                estimated_cost_usd=0.01,
            )]

    class Tools:
        def run(self, _action):
            return ToolResult(ok=True, output="ok")

    response = """```json
    {"tasks":[
      {"id":"inspect","title":"Inspect","dependencies":[],"criteria":["Known"]},
      {"id":"verify","title":"Verify","dependencies":["inspect"],"criteria":["Checked"]}
    ]}
    ```"""
    host = ExecutionRuntimeHost(
        tmp_path / "model.db",
        "shadow",
        tools=Tools(),
        plan_provider=ModelPlanProvider(Model(response)),  # type: ignore[arg-type]
        recover_on_start=False,
    )
    adapter = host.begin_legacy_run(
        "goal", budgets={"tokens": 1000, "dollars": 1}
    )
    state = host.runtime.engine.state(adapter.execution_id)
    assert list(state.tasks) == ["inspect", "verify"]
    assert state.budgets["execution"].consumed == {"tokens": 42.0, "dollars": 0.01}
    assert state.model_decisions[-1]["model"] == "planner-model"

    fallback = ExecutionRuntimeHost(
        tmp_path / "fallback.db",
        "shadow",
        tools=Tools(),
        plan_provider=ModelPlanProvider(  # type: ignore[arg-type]
            Model(TimeoutError("planner timed out"))
        ),
        recover_on_start=False,
    )
    fallback_adapter = fallback.begin_legacy_run("goal")
    fallback_state = fallback.runtime.engine.state(fallback_adapter.execution_id)
    assert list(fallback_state.tasks) == [
        "engine-discover", "engine-execute", "engine-verify",
    ]
    assert fallback.planning_diagnostics[0]["error"].startswith("TimeoutError")
    assert any(
        item["kind"] == "failed_approach" for item in fallback_state.memory_records
    )


def test_runtime_host_recovery_never_repeats_ambiguous_external_action(tmp_path):
    class CrashingTools:
        def __init__(self):
            self.calls = 0

        def run(self, _action):
            self.calls += 1
            raise RuntimeError("process lost after external action")

    path = tmp_path / "host.db"
    tools = CrashingTools()
    first_host = ExecutionRuntimeHost(
        path, "shadow", tools=tools, recover_on_start=False
    )
    adapter = first_host.begin_legacy_run("read file")
    first = first_host.execute_action(
        adapter.execution_id,
        adapter.task_id,
        1,
        {"type": "read_file", "path": "README.md"},
        criterion_ids=(adapter.criterion_id,),
    )
    assert first.result.metadata["effect_status"] == "unknown"

    recovered_host = ExecutionRuntimeHost(path, "shadow", tools=tools)
    resumed = recovered_host.begin_legacy_run(
        "read file", execution_id=adapter.execution_id
    )
    recovery_context = recovered_host.recovery_context(adapter.execution_id)
    assert adapter.execution_id in recovery_context
    assert "ambiguous_effects" in recovery_context
    reconciled = recovered_host.execute_action(
        resumed.execution_id,
        resumed.task_id,
        1,
        {"type": "read_file", "path": "README.md"},
        criterion_ids=(resumed.criterion_id,),
    )
    assert reconciled.result.metadata["effect_status"] == "unknown"
    assert tools.calls == 1


def test_primary_host_requires_planning_promotion_and_owns_only_the_graph(tmp_path):
    class Tools:
        def run(self, _action):
            return ToolResult(ok=True, output="ok")

    path = tmp_path / "host.db"
    with pytest.raises(RuntimeError, match="promotion to the planning stage"):
        ExecutionRuntimeHost(path, "primary", tools=Tools(), recover_on_start=False)

    divergences = ShadowDivergenceStore(path)
    for _index in range(10):
        divergences.record_comparison("run", "planning", matched=True)
    MigrationStateStore(path).promote(
        PromotionStage.PLANNING,
        divergences,
        PromotionPolicy(minimum_samples=10, maximum_divergence_rate=0),
    )
    primary = ExecutionRuntimeHost(
        path, "primary", tools=Tools(), recover_on_start=False
    )
    adapter = primary.begin_legacy_run("goal")
    assert primary.engine_owns("planning")
    assert not primary.engine_owns("side_effects")
    assert "authoritative" in primary.planning_context(adapter.execution_id)


def test_primary_scheduling_assigns_one_task_and_enforces_task_budget(tmp_path):
    class Tools:
        def run(self, _action):
            return ToolResult(ok=True, output="ok")

    path = tmp_path / "host.db"
    divergences = ShadowDivergenceStore(path)
    for _index in range(10):
        divergences.record_comparison("run", "planning", matched=True)
    migration = MigrationStateStore(path)
    migration.promote(
        PromotionStage.PLANNING, divergences,
        PromotionPolicy(minimum_samples=10, maximum_divergence_rate=0),
    )
    for _index in range(10):
        divergences.record_comparison("run", "scheduling", matched=True)
        divergences.record_comparison("run", "budget", matched=True)
    migration.promote(
        PromotionStage.SCHEDULING_BUDGETS, divergences,
        PromotionPolicy(minimum_samples=10, maximum_divergence_rate=0),
    )

    host = ExecutionRuntimeHost(path, "primary", tools=Tools(), recover_on_start=False)
    adapter = host.begin_legacy_run("inspect README", budgets={"tool_calls": 1})
    state = host.runtime.engine.state(adapter.execution_id)
    assert host.engine_owns("scheduling")
    assert host.engine_owns("budgets")
    assert state.scheduling_records[-1]["task_id"] == adapter.task_id
    assert state.tasks[adapter.task_id].state.value == "running"
    assert "worker assignment" in host.planning_context(adapter.execution_id)

    host.execute_action(
        adapter.execution_id, adapter.task_id, 1,
        {"type": "read_file", "path": "README.md"},
    )
    with pytest.raises(InvariantError, match="Budget exhausted"):
        host.execute_action(
            adapter.execution_id, adapter.task_id, 2,
            {"type": "read_file", "path": "README.md"},
        )
    adapter.finish(blocked=False, summary="first scheduled task complete")
    state = host.runtime.engine.state(adapter.execution_id)
    assert state.status == ExecutionStatus.ACTIVE
    assert state.tasks[adapter.task_id].state.value == "complete"


def test_planning_promotion_ignores_unrelated_shadow_samples(tmp_path):
    path = tmp_path / "host.db"
    divergences = ShadowDivergenceStore(path)
    for _index in range(100):
        divergences.record_comparison("run", "tool_selection", matched=True)
    with pytest.raises(RuntimeError, match="Insufficient"):
        MigrationStateStore(path).promote(
            PromotionStage.PLANNING,
            divergences,
            PromotionPolicy(minimum_samples=10, maximum_divergence_rate=0),
        )


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
