from __future__ import annotations

import random
import subprocess
import sys
import threading

import pytest

from code_agent.durable_execution import (
    AdaptiveRouter,
    Command,
    ConcurrencyError,
    ContextCompressor,
    CriticalPathPolicy,
    DurableExecutionRuntime,
    EffectState,
    ExecutionEvent,
    ExecutionEngine,
    ExecutionStatus,
    HierarchicalPlanner,
    InvariantError,
    LeaseError,
    PriorityPolicy,
    Scheduler,
    SQLiteEventStore,
    TaskState,
)


def _runtime(tmp_path):
    return DurableExecutionRuntime(tmp_path / "execution.db")


def _one_task(runtime):
    execution_id = runtime.create_planned(
        "ship durable execution",
        tasks=[{"id": "build", "title": "Build engine", "criteria": ["tests pass"]}],
        budgets={"tokens": 10_000, "tool_calls": 20},
    )
    state = runtime.engine.state(execution_id)
    criterion_id = state.tasks["build"].criteria[0]
    return execution_id, criterion_id


def test_event_log_is_canonical_and_command_is_idempotent(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id = runtime.engine.create("goal", execution_id="exec-fixed")
    command = Command(
        "AddTasks", execution_id,
        {"tasks": [{"id": "a", "title": "A", "criteria": ["done"]}]},
        command_id="cmd-fixed",
    )
    first = runtime.engine.dispatch(command)
    second = runtime.engine.dispatch(command)
    assert [event.event_id for event in first] == [event.event_id for event in second]
    assert runtime.engine.replay(execution_id).canonical() == runtime.engine.state(execution_id).canonical()
    assert [event.sequence for event in runtime.store.load(execution_id)] == [1, 2, 3]


def test_engine_state_is_an_isolated_projection(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id, _ = _one_task(runtime)

    leaked = runtime.engine.state(execution_id)
    leaked.tasks["build"].state = TaskState.COMPLETE
    leaked.tasks["build"].metadata["corrupted"] = True

    persisted = runtime.engine.state(execution_id)
    assert persisted.tasks["build"].state == TaskState.QUEUED
    assert "corrupted" not in persisted.tasks["build"].metadata


def test_engine_refreshes_stale_cache_after_concurrent_writer_conflict(tmp_path):
    store = SQLiteEventStore(tmp_path / "execution.db")
    first = ExecutionEngine(store)
    second = ExecutionEngine(store)
    execution_id = first.create("goal")
    second.state(execution_id)

    first.dispatch(Command("AddTasks", execution_id, {
        "tasks": [{"id": "first", "title": "First", "criteria": ["done"]}],
    }))
    with pytest.raises(ConcurrencyError):
        second.dispatch(Command("AddTasks", execution_id, {
            "tasks": [{"id": "stale", "title": "Stale", "criteria": ["done"]}],
        }))

    second.dispatch(Command("AddTasks", execution_id, {
        "tasks": [{"id": "retry", "title": "Retry", "criteria": ["done"]}],
    }))
    assert set(second.state(execution_id).tasks) == {"first", "retry"}


def test_replay_rejects_unknown_event_types(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id, _ = _one_task(runtime)
    state = runtime.engine.replay(execution_id)
    unknown = ExecutionEvent(
        execution_id=execution_id,
        sequence=state.sequence + 1,
        type="FutureEvent",
        payload={},
        event_id="future-event",
        command_id="future-command",
        causation_id=None,
        correlation_id="future-command",
        created_at="2026-01-01T00:00:00+00:00",
    )

    with pytest.raises(InvariantError, match="Unknown execution event type"):
        runtime.engine.projector.replay(
            execution_id, [*runtime.store.load(execution_id), unknown]
        )


def test_task_lifecycle_requires_dependencies_evidence_and_verification(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id = runtime.create_planned(
        "goal",
        tasks=[
            {"id": "a", "title": "A", "criteria": ["A works"]},
            {"id": "b", "title": "B", "dependencies": ["a"], "criteria": ["B works"]},
        ],
    )
    engine = runtime.engine
    with pytest.raises(InvariantError, match="dependencies"):
        engine.dispatch(Command("TransitionTask", execution_id, {"task_id": "b", "to": "ready"}))
    for target in ["ready", "running", "verifying"]:
        engine.dispatch(Command("TransitionTask", execution_id, {"task_id": "a", "to": target}))
    with pytest.raises(InvariantError, match="criteria"):
        engine.dispatch(Command("TransitionTask", execution_id, {"task_id": "a", "to": "verified"}))
    criterion = engine.state(execution_id).tasks["a"].criteria[0]
    evidence_event = engine.dispatch(Command("RecordEvidence", execution_id, {
        "task_id": "a", "kind": "test", "summary": "tests passed", "payload": {"ok": True},
        "criterion_ids": [criterion],
    }))[0]
    evidence_id = evidence_event.payload["evidence_id"]
    engine.dispatch(Command("VerifyCriterion", execution_id, {
        "criterion_id": criterion, "evidence_ids": [evidence_id], "passed": True,
    }))
    engine.dispatch(Command("TransitionTask", execution_id, {"task_id": "a", "to": "verified"}))
    engine.dispatch(Command("TransitionTask", execution_id, {"task_id": "a", "to": "complete"}))
    assert engine.state(execution_id).tasks["a"].state == TaskState.COMPLETE


def test_graph_versions_are_immutable_and_mutations_validate_dag(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id, _ = _one_task(runtime)
    engine = runtime.engine
    assert engine.state(execution_id).graph_version == 1
    engine.dispatch(Command("MutateGraph", execution_id, {
        "base_version": 1, "operation": "insert", "rationale": "add verification",
        "tasks": [{"id": "verify", "title": "Verify", "dependencies": ["build"], "criteria": ["verified"]}],
    }))
    state = engine.state(execution_id)
    assert state.graph_version == 2 and state.graph_parent_versions[2] == 1
    with pytest.raises(ConcurrencyError):
        engine.dispatch(Command("MutateGraph", execution_id, {"base_version": 1, "operation": "delete", "task_ids": ["verify"]}))


def test_replanner_supports_dependency_change_split_and_merge(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id = runtime.create_planned("goal", tasks=[
        {"id": "a", "title": "A", "criteria": ["A"]},
        {"id": "b", "title": "B", "criteria": ["B"]},
    ])
    engine = runtime.engine
    engine.dispatch(Command("MutateGraph", execution_id, {
        "base_version": 1, "operation": "change_dependencies", "task_id": "b", "dependencies": ["a"],
    }))
    engine.dispatch(Command("MutateGraph", execution_id, {
        "base_version": 2, "operation": "split", "task_id": "a",
        "tasks": [
            {"id": "a1", "title": "A1", "criteria": ["A1"]},
            {"id": "a2", "title": "A2", "dependencies": ["a1"], "criteria": ["A2"]},
        ],
        "dependency_updates": [{"task_id": "b", "dependencies": ["a2"]}],
    }))
    engine.dispatch(Command("MutateGraph", execution_id, {
        "base_version": 3, "operation": "merge", "task_ids": ["a1", "a2"],
        "task": {"id": "a-merged", "title": "Merged A", "criteria": ["A merged"]},
        "dependency_updates": [{"task_id": "b", "dependencies": ["a-merged"]}],
    }))
    state = engine.state(execution_id)
    assert state.graph_version == 4
    assert set(state.tasks) == {"a-merged", "b"}
    assert state.tasks["b"].dependencies == ("a-merged",)


def test_effect_journal_prevents_duplicate_commit_and_tracks_unknown(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id, _ = _one_task(runtime)
    request = Command("RequestEffect", execution_id, {
        "task_id": "build", "kind": "http", "idempotency_key": "deploy:abc", "request": {"url": "https://example.com"},
    })
    effect_id = runtime.engine.dispatch(request)[0].payload["effect_id"]
    assert runtime.engine.dispatch(Command("RequestEffect", execution_id, {
        "task_id": "build", "kind": "http", "idempotency_key": "deploy:abc",
    })) == []
    runtime.engine.dispatch(Command("ChangeEffectState", execution_id, {"effect_id": effect_id, "state": "running"}))
    runtime.engine.dispatch(Command("ChangeEffectState", execution_id, {"effect_id": effect_id, "state": "unknown"}))
    assert runtime.engine.state(execution_id).effects[effect_id].state == EffectState.UNKNOWN
    runtime.engine.dispatch(Command("ChangeEffectState", execution_id, {"effect_id": effect_id, "state": "committed", "result": {"reconciled": True}}))
    with pytest.raises(InvariantError):
        runtime.engine.dispatch(Command("ChangeEffectState", execution_id, {"effect_id": effect_id, "state": "committed"}))


def test_recovery_marks_ambiguous_running_effect_unknown(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id, _ = _one_task(runtime)
    effect_id = runtime.engine.dispatch(Command("RequestEffect", execution_id, {
        "task_id": "build", "kind": "shell", "idempotency_key": "shell:1",
    }))[0].payload["effect_id"]
    runtime.engine.dispatch(Command("ChangeEffectState", execution_id, {
        "effect_id": effect_id, "state": "running",
    }))
    runtime.engine._cache.clear()
    state = runtime.recover(execution_id)
    assert state.effects[effect_id].state == EffectState.UNKNOWN


def test_recovery_requeues_interrupted_lifecycle_states_without_consuming_retry(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id, _ = _one_task(runtime)
    engine = runtime.engine
    for target in ("ready", "running", "verifying"):
        engine.dispatch(Command("TransitionTask", execution_id, {"task_id": "build", "to": target}))

    recovered = runtime.recover(execution_id, resume_interrupted_tasks=True)

    assert recovered.tasks["build"].state == TaskState.READY
    assert recovered.tasks["build"].retries == 0


def test_recovery_after_real_process_restart_requeues_lost_worker(tmp_path):
    db_path = tmp_path / "restart.db"
    script = """
import sys
from pathlib import Path
from code_agent.durable_execution import Command, DurableExecutionRuntime
runtime = DurableExecutionRuntime(Path(sys.argv[1]))
execution_id = runtime.create_planned('restart goal', tasks=[{
    'id': 'build', 'title': 'Build', 'criteria': ['done'],
}])
runtime.engine.dispatch(Command('TransitionTask', execution_id, {'task_id': 'build', 'to': 'ready'}))
runtime.engine.dispatch(Command('TransitionTask', execution_id, {'task_id': 'build', 'to': 'running'}))
print(execution_id)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(db_path)],
        check=True,
        capture_output=True,
        text=True,
    )

    restarted = DurableExecutionRuntime(db_path)
    recovered = restarted.recover(
        completed.stdout.strip(), resume_interrupted_tasks=True
    )

    assert recovered.tasks["build"].state == TaskState.READY
    assert recovered.tasks["build"].retries == 0


def test_approvals_and_hierarchical_budgets_are_resources(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id, _ = _one_task(runtime)
    engine = runtime.engine
    approval_id = engine.dispatch(Command("RequestApproval", execution_id, {
        "scope": "shell.execute", "risk": "high", "task_ids": ["build"], "expires_at": "2099-01-01T00:00:00Z",
    }))[0].payload["approval_id"]
    engine.dispatch(Command("GrantApproval", execution_id, {"approval_id": approval_id, "granted_by": "user"}))
    assert engine.state(execution_id).approvals[approval_id].granted_by == "user"
    engine.dispatch(Command("ConfigureBudget", execution_id, {"scope": "execution/build", "limits": {"tokens": 100}}))
    engine.dispatch(Command("ReserveBudget", execution_id, {"scope": "execution/build", "kind": "tokens", "amount": 60}))
    engine.dispatch(Command("ConsumeBudget", execution_id, {"scope": "execution/build", "kind": "tokens", "amount": 60, "from_reservation": True}))
    with pytest.raises(InvariantError, match="exhausted"):
        engine.dispatch(Command("ReserveBudget", execution_id, {"scope": "execution/build", "kind": "tokens", "amount": 41}))


def test_budget_reservation_paths_reject_negative_amounts(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id, _ = _one_task(runtime)
    engine = runtime.engine
    engine.dispatch(Command("ConfigureBudget", execution_id, {
        "scope": "execution/build", "limits": {"tokens": 100},
    }))
    engine.dispatch(Command("ReserveBudget", execution_id, {
        "scope": "execution/build", "kind": "tokens", "amount": 20,
    }))

    for command in ("ConsumeBudget", "ReleaseBudget"):
        with pytest.raises(InvariantError, match="Invalid budget consumption"):
            engine.dispatch(Command(command, execution_id, {
                "scope": "execution/build", "kind": "tokens", "amount": -1,
                "from_reservation": command == "ConsumeBudget",
            }))

    budget = engine.state(execution_id).budgets["execution/build"]
    assert budget.reserved["tokens"] == 20
    assert budget.consumed.get("tokens", 0) == 0


def test_only_runtime_recovery_can_bypass_retry_accounting(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id, _ = _one_task(runtime)
    engine = runtime.engine
    for target in ("ready", "running", "verifying", "diagnosing"):
        engine.dispatch(Command("TransitionTask", execution_id, {"task_id": "build", "to": target}))

    with pytest.raises(PermissionError, match="Only runtime recovery"):
        engine.dispatch(Command("TransitionTask", execution_id, {
            "task_id": "build", "to": "ready", "recovery": True,
        }))


def test_child_budget_consumption_is_accounted_against_execution_limit(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id, _ = _one_task(runtime)
    engine = runtime.engine
    engine.dispatch(Command("ConfigureBudget", execution_id, {
        "scope": "execution", "limits": {"tool_calls": 2},
    }))
    engine.dispatch(Command("ConsumeBudget", execution_id, {
        "scope": "execution/tasks/a", "kind": "tool_calls", "amount": 1,
    }))
    engine.dispatch(Command("ConsumeBudget", execution_id, {
        "scope": "execution/tasks/b", "kind": "tool_calls", "amount": 1,
    }))
    with pytest.raises(InvariantError, match="Budget exhausted"):
        engine.dispatch(Command("ConsumeBudget", execution_id, {
            "scope": "execution/tasks/c", "kind": "tool_calls", "amount": 1,
        }))
    assert engine.state(execution_id).budgets["execution"].consumed["tool_calls"] == 2


def test_worker_actor_cannot_emit_lifecycle_authority_commands(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id, _criterion_id = _one_task(runtime)
    with pytest.raises(PermissionError, match="Worker authority"):
        runtime.engine.dispatch(Command(
            "TransitionTask", execution_id,
            {"task_id": "build", "to": "ready"}, actor="worker:untrusted",
        ))


def test_execution_lease_uses_fencing_tokens(tmp_path):
    store = SQLiteEventStore(tmp_path / "execution.db")
    engine = ExecutionEngine(store)
    execution_id = engine.create("goal")
    lease = store.acquire_lease(execution_id, "worker-a", ttl_seconds=30)
    with pytest.raises(LeaseError):
        engine.dispatch(Command("PauseExecution", execution_id))
    engine.dispatch(Command("PauseExecution", execution_id, lease_token=lease.token))
    store.release_lease(lease)
    newer = store.acquire_lease(execution_id, "worker-b", ttl_seconds=30)
    with pytest.raises(LeaseError):
        engine.dispatch(Command("ResumeExecution", execution_id, lease_token=lease.token))
    engine.dispatch(Command("ResumeExecution", execution_id, lease_token=newer.token))


def test_scheduler_policies_never_run_blocked_tasks(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id = runtime.create_planned("goal", tasks=[
        {"id": "slow", "title": "Slow", "priority": 1, "estimated_cost": 10, "criteria": ["done"]},
        {"id": "urgent", "title": "Urgent", "priority": 10, "criteria": ["done"]},
        {"id": "blocked", "title": "Blocked", "dependencies": ["slow"], "criteria": ["done"]},
    ])
    state = runtime.engine.state(execution_id)
    assert Scheduler(PriorityPolicy()).ready(state)[0].id == "urgent"
    assert "blocked" not in {task.id for task in Scheduler(CriticalPathPolicy()).ready(state)}


def test_diagnosis_memory_routing_and_context_compression(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id, criterion_id = _one_task(runtime)
    engine = runtime.engine
    evidence_id = engine.dispatch(Command("RecordEvidence", execution_id, {
        "task_id": "build", "kind": "test", "summary": "parser test failed", "payload": {"ok": False}, "criterion_ids": [criterion_id],
    }))[0].payload["evidence_id"]
    state = engine.state(execution_id)
    diagnosis = runtime.diagnoser.diagnose(state.tasks["build"], [state.evidence[evidence_id]])
    engine.dispatch(Command("RecordDiagnosis", execution_id, diagnosis))
    engine.dispatch(Command("RecordMemory", execution_id, {"kind": "failed_approach", "content": "parser regex caused regression"}))
    assert runtime.memory.retrieve(engine.state(execution_id), "parser regression")
    route = AdaptiveRouter().choose(state.tasks["build"], confidence=0.3, remaining_tokens=5000)
    engine.dispatch(Command("RecordModelRoute", execution_id, route))
    compressed = ContextCompressor().compress(engine.state(execution_id))
    assert compressed["goal"] == "ship durable execution"
    assert compressed["latest_diagnoses"]
    assert runtime.memory.retrieve_global(runtime.store, "parser regression")[0]["execution_id"] == execution_id


def test_pause_resume_checkpoint_and_completion(tmp_path):
    runtime = _runtime(tmp_path)
    execution_id, criterion_id = _one_task(runtime)
    engine = runtime.engine
    engine.dispatch(Command("PauseExecution", execution_id))
    assert engine.state(execution_id).status == ExecutionStatus.PAUSED
    with pytest.raises(InvariantError, match="paused"):
        engine.dispatch(Command("TransitionTask", execution_id, {"task_id": "build", "to": "ready"}))
    engine.dispatch(Command("ResumeExecution", execution_id))
    for target in ["ready", "running", "verifying"]:
        engine.dispatch(Command("TransitionTask", execution_id, {"task_id": "build", "to": target}))
    evidence_id = engine.dispatch(Command("RecordEvidence", execution_id, {
        "task_id": "build", "kind": "test", "summary": "ok", "payload": {"ok": True}, "criterion_ids": [criterion_id],
    }))[0].payload["evidence_id"]
    engine.dispatch(Command("VerifyCriterion", execution_id, {"criterion_id": criterion_id, "evidence_ids": [evidence_id], "passed": True}))
    engine.dispatch(Command("TransitionTask", execution_id, {"task_id": "build", "to": "verified"}))
    engine.dispatch(Command("TransitionTask", execution_id, {"task_id": "build", "to": "complete"}))
    checksum = engine.checkpoint(execution_id)
    assert checksum
    engine.dispatch(Command("CompleteExecution", execution_id))
    assert runtime.recover(execution_id).status == ExecutionStatus.COMPLETE


def test_randomized_crash_replay_matches_uninterrupted_execution(tmp_path):
    randomizer = random.Random(47)
    baseline = _runtime(tmp_path / "baseline")
    recovered = _runtime(tmp_path / "recovered")
    baseline_id, baseline_criterion = _one_task(baseline)
    recovered_id, recovered_criterion = _one_task(recovered)

    def workload(runtime, execution_id, criterion_id):
        commands = [Command("TransitionTask", execution_id, {"task_id": "build", "to": target}) for target in ["ready", "running", "verifying"]]
        for command in commands:
            runtime.engine.dispatch(command)
            if randomizer.choice([True, False]):
                runtime.engine._cache.clear()
                runtime.recover(execution_id)
        evidence_id = runtime.engine.dispatch(Command("RecordEvidence", execution_id, {
            "task_id": "build", "kind": "test", "summary": "passed", "payload": {"ok": True}, "criterion_ids": [criterion_id],
        }))[0].payload["evidence_id"]
        runtime.engine.dispatch(Command("VerifyCriterion", execution_id, {"criterion_id": criterion_id, "evidence_ids": [evidence_id], "passed": True}))
        runtime.engine.dispatch(Command("TransitionTask", execution_id, {"task_id": "build", "to": "verified"}))
        runtime.engine.dispatch(Command("TransitionTask", execution_id, {"task_id": "build", "to": "complete"}))
        runtime.engine.dispatch(Command("CompleteExecution", execution_id))
        return runtime.engine.replay(execution_id)

    baseline_state = workload(baseline, baseline_id, baseline_criterion)
    recovered_state = workload(recovered, recovered_id, recovered_criterion)
    left = baseline_state.canonical()
    right = recovered_state.canonical()
    for value in [left, right]:
        value["id"] = "normalized"
        value["created_at"] = "normalized"
        value["updated_at"] = "normalized"
        value["criteria"] = {"criterion": next(iter(value["criteria"].values()))}
        value["criteria"]["criterion"]["id"] = "criterion"
        value["criteria"]["criterion"]["evidence_ids"] = ["evidence"]
        value["criteria"]["criterion"]["verification_ids"] = ["verification"]
        value["tasks"]["build"]["criteria"] = ["criterion"]
        value["evidence"] = {"evidence": next(iter(value["evidence"].values()))}
        value["evidence"]["evidence"]["id"] = "evidence"
        value["verifications"] = {"verification": next(iter(value["verifications"].values()))}
        value["verifications"]["verification"]["verification_id"] = "verification"
        value["verifications"]["verification"]["criterion_id"] = "criterion"
        value["verifications"]["verification"]["evidence_ids"] = ["evidence"]
    assert left == right


@pytest.mark.parametrize("boundary", ["before_persistence", "before_commit", "after_persistence"])
def test_failure_injection_at_event_boundaries_recovers_idempotently(tmp_path, boundary):
    armed = {"value": False}

    def inject(stage, _execution_id):
        if armed["value"] and stage == boundary:
            armed["value"] = False
            raise RuntimeError(f"crash:{stage}")

    store = SQLiteEventStore(tmp_path / f"{boundary}.db", fault_injector=inject)
    engine = ExecutionEngine(store)
    execution_id = engine.create("goal")
    command = Command("AddTasks", execution_id, {
        "tasks": [{"id": "task", "title": "Task", "criteria": ["done"]}],
    }, command_id="stable-command")
    armed["value"] = True
    with pytest.raises(RuntimeError, match="crash"):
        engine.dispatch(command)
    engine._cache.clear()
    engine.dispatch(command)
    state = engine.replay(execution_id)
    assert list(state.tasks) == ["task"]
    assert len([event for event in store.load(execution_id) if event.command_id == "stable-command"]) == 2


def test_background_worker_receives_cancellation(tmp_path):
    from code_agent.durable_execution import BackgroundWorkerPool

    started = threading.Event()

    def worker(_task, _state, cancel):
        started.set()
        cancel.wait(1)
        return {"cancelled": cancel.is_set()}

    runtime = _runtime(tmp_path)
    execution_id, _ = _one_task(runtime)
    state = runtime.engine.state(execution_id)
    pool = BackgroundWorkerPool(worker, max_workers=1)
    future = pool.submit(state.tasks["build"], state)
    assert started.wait(1)
    assert pool.cancel("build")
    assert future.result(timeout=2)["cancelled"] is True
    pool.close()


def test_planner_infers_hierarchy_defaults():
    tasks = HierarchicalPlanner().decompose(
        "goal", [{"title": "Inspect"}, {"title": "Implement", "after_previous": True}]
    )
    assert tasks[1]["dependencies"] == [tasks[0]["id"]]
    assert tasks[0]["criteria"]


def test_autonomous_executor_runs_parallel_workers_and_completes(tmp_path):
    from code_agent.durable_execution import AutonomousExecutor

    runtime = _runtime(tmp_path)
    execution_id = runtime.create_planned("goal", tasks=[
        {"id": "a", "title": "A", "criteria": ["A done"]},
        {"id": "b", "title": "B", "criteria": ["B done"]},
    ])
    executor = AutonomousExecutor(
        runtime,
        lambda task, _state, _cancel: {"ok": True, "summary": f"{task.id} passed"},
        max_parallel=2,
    )
    state = executor.run(execution_id)
    assert state.status == ExecutionStatus.COMPLETE
    assert all(task.state == TaskState.COMPLETE for task in state.tasks.values())
    assert len(state.model_decisions) == 2
    executor.close()


def test_autonomous_executor_retries_after_diagnosis(tmp_path):
    from code_agent.durable_execution import AutonomousExecutor

    runtime = _runtime(tmp_path)
    execution_id, _ = _one_task(runtime)
    attempts = {"count": 0}

    def worker(_task, _state, _cancel):
        attempts["count"] += 1
        return {"ok": attempts["count"] > 1, "summary": "pass" if attempts["count"] > 1 else "fail"}

    executor = AutonomousExecutor(runtime, worker, max_parallel=1)
    state = executor.run(execution_id)
    assert state.status == ExecutionStatus.COMPLETE
    assert state.tasks["build"].retries == 1
    assert state.diagnoses
    assert state.graph_version == 2
    assert state.critiques
    executor.close()


def test_human_checkpoint_blocks_then_resumes_after_durable_approval(tmp_path):
    from code_agent.durable_execution import AutonomousExecutor

    runtime = _runtime(tmp_path)
    execution_id = runtime.create_planned("deploy", tasks=[{
        "id": "deploy", "title": "Deploy", "risk": 0.9, "criteria": ["deployed"],
    }])
    executor = AutonomousExecutor(runtime, lambda *_args: {"ok": True, "summary": "deployed"})
    blocked = executor.run(execution_id)
    assert blocked.tasks["deploy"].state == TaskState.BLOCKED
    approval_id = next(iter(blocked.approvals))
    runtime.engine.dispatch(Command("GrantApproval", execution_id, {
        "approval_id": approval_id, "granted_by": "user",
    }))
    completed = executor.run(execution_id)
    assert completed.status == ExecutionStatus.COMPLETE
    executor.close()


def test_agent_adapter_journals_tool_effect_evidence_usage_and_completion(tmp_path):
    from code_agent.durable_execution import AgentExecutionAdapter

    runtime = _runtime(tmp_path)
    adapter = AgentExecutionAdapter(runtime, "fix parser", budgets={"tokens": 1000, "dollars": 1})
    effect_id, replay = adapter.action_started(1, {"type": "read_file", "path": "parser.py"})
    assert replay is None
    adapter.action_completed(effect_id, ok=True, output="parser source", elapsed_ms=12)
    adapter.model_usage({
        "model": "fast", "provider": "test", "ok": True,
        "total_tokens": 100, "estimated_cost_usd": 0.01, "latency_ms": 20,
    })
    adapter.finish(blocked=False, summary="Parser fixed and verified")
    state = runtime.engine.replay(adapter.execution_id)
    assert state.status == ExecutionStatus.COMPLETE
    assert state.effects[effect_id].state == EffectState.COMMITTED
    assert state.budgets["execution"].consumed["tokens"] == 100
    assert state.model_decisions[-1]["model"] == "fast"


def test_agent_adapter_replays_committed_effect_and_refuses_unknown(tmp_path):
    from code_agent.durable_execution import AgentExecutionAdapter

    runtime = _runtime(tmp_path)
    adapter = AgentExecutionAdapter(runtime, "goal")
    action = {"type": "run_shell", "command": "build"}
    effect_id, replay = adapter.action_started(1, action)
    assert replay is None
    adapter.action_completed(effect_id, ok=True, output="built", elapsed_ms=5)
    same_id, replay = adapter.action_started(1, action)
    assert same_id == effect_id and replay["output"] == "built"
    unknown_id, _ = adapter.action_started(2, action)
    runtime.engine._cache.clear()
    runtime.recover(adapter.execution_id)
    assert runtime.engine.state(adapter.execution_id).effects[unknown_id].state == EffectState.UNKNOWN
    with pytest.raises(InvariantError, match="reconcile"):
        adapter.action_started(2, action)


def test_subagent_worker_adapter_bridges_isolated_agent_manager(tmp_path):
    from code_agent.durable_execution import AutonomousExecutor, SubagentWorkerAdapter
    from code_agent.orchestration import AgentCatalog, SubagentManager

    catalog = AgentCatalog()
    manager = SubagentManager(
        catalog,
        lambda request, profile, _cancel: {
            "summary": f"{profile.name}:{request.task}", "ok": True,
        },
        max_workers=1,
    )
    runtime = _runtime(tmp_path)
    execution_id = runtime.create_planned("goal", tasks=[{
        "id": "child", "title": "Implement child task", "assigned_agent": "coder",
        "criteria": ["child complete"],
    }])
    executor = AutonomousExecutor(runtime, SubagentWorkerAdapter(manager), max_parallel=1)
    state = executor.run(execution_id)
    assert state.status == ExecutionStatus.COMPLETE
    assert next(iter(state.evidence.values())).payload["agent"] == "coder"
    executor.close()
    manager.close()
