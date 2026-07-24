from __future__ import annotations

import json
import pytest
from typer.testing import CliRunner

from code_agent.cli import app
from code_agent.durable_execution import (
    DurableExecutionRuntime,
)
from code_agent.execution_observability import ExecutionInspector
from code_agent.execution_profiles import (
    BudgetPolicy,
    ComplexityAssessor,
    ExecutionComplexity,
    ExecutionPolicySelector,
    ExecutionProfile,
    PlanningPolicy,
    ProfileEscalator,
    TaskIntent,
    ToolPolicy,
    VerificationPolicy,
)

runner = CliRunner()


def test_strongly_typed_policy_objects():
    planning = PlanningPolicy(depth=3, replanning_mode="limited", allow_graph_mutations=True, max_graph_size=25)
    assert planning.depth == 3
    assert planning.replanning_mode == "limited"
    assert planning.allow_graph_mutations is True

    budget = BudgetPolicy(
        execution_tokens=50_000, planning_tokens=10_000, tool_budget=30,
        shell_budget=10, verification_budget=5, retries=2, max_graph_size=25,
    )
    engine_budgets = budget.to_engine_budgets()
    assert engine_budgets["tokens"] == 50000.0
    assert engine_budgets["tool_calls"] == 30.0

    tools = ToolPolicy(max_fanout=4, concurrency=4, timeout_seconds=300.0)
    assert tools.concurrency == 4

    verification = VerificationPolicy(mode="standard", require_test_pass=True, max_verification_retries=1)
    assert verification.require_test_pass is True

    profile = ExecutionProfile(
        complexity=ExecutionComplexity.MEDIUM,
        planning=planning,
        budgets=budget,
        tools=tools,
        verification=verification,
        scheduling_policy="dependency_aware",
    )
    assert "MEDIUM" in profile.display_summary()
    d = profile.as_dict()
    assert d["complexity"] == "medium"
    assert d["planning"]["depth"] == 3


def test_invalid_policy_validations():
    with pytest.raises(ValueError, match="replanning"):
        PlanningPolicy(replanning_mode="invalid_mode")

    with pytest.raises(ValueError, match="non-negative"):
        BudgetPolicy(execution_tokens=-100)

    with pytest.raises(ValueError, match="mode"):
        VerificationPolicy(mode="invalid_mode")


def test_objective_complexity_assessor_scoring():
    assessor = ComplexityAssessor()

    # Simple intent: short prompt, single file/symbol, low radius
    simple_intent = TaskIntent(goal="Fix typo in docstring of helper.py")
    complexity_simple, signals_simple = assessor.assess(simple_intent)
    assert complexity_simple == ExecutionComplexity.SIMPLE
    assert signals_simple.composite < 0.35

    # Complex intent: multi-step prompt, multiple symbols and path hints, cross-module
    complex_intent = TaskIntent(
        goal="Implement OAuth2 authentication subsystem. First, add token validation in auth/jwt.py, "
             "then update user model in models/user.py, after that add middleware in server/middleware.py, "
             "and finally write test cases in tests/test_auth.py and run full verification test suite.",
        symbol_hints=("OAuth2Client", "JWTValidator", "AuthMiddleware", "UserSession"),
        file_hints=("auth/jwt.py", "models/user.py", "server/middleware.py", "tests/test_auth.py"),
        expected_tool_kinds=("read_file", "write_file", "shell", "run_command"),
        tests_required=True,
        build_required=True,
    )
    complexity_complex, signals_complex = assessor.assess(complex_intent)
    assert complexity_complex == ExecutionComplexity.COMPLEX
    assert signals_complex.composite >= 0.70


def test_policy_selector_and_explicit_overrides():
    selector = ExecutionPolicySelector()

    # Explicit override via intent
    intent = TaskIntent(goal="Simple goal", explicit_complexity=ExecutionComplexity.COMPLEX)
    profile, signals = selector.select(intent)
    assert profile.complexity == ExecutionComplexity.COMPLEX

    # Direct override dictionary
    intent_norm = TaskIntent(goal="Simple goal")
    profile_override, _ = selector.select(intent_norm, overrides={"planning": {"depth": 5}})
    assert profile_override.planning.depth == 5


def test_durable_runtime_auto_assesses_profile(tmp_path):
    db_path = tmp_path / "execution.db"
    runtime = DurableExecutionRuntime(db_path)

    # create_planned without explicit profile should assess goal complexity
    execution_id = runtime.create_planned("Fix typo in docstring")
    state = runtime.engine.state(execution_id)

    assert state.active_profile is not None
    assert state.active_profile["complexity"] == "simple"

    # Verify ProfileConfigured event in event log
    events = runtime.store.load(execution_id)
    event_types = [evt.type for evt in events]
    assert "ProfileConfigured" in event_types


def test_event_replay_and_snapshot_preserves_profile_and_stats(tmp_path):
    db_path = tmp_path / "execution.db"
    runtime = DurableExecutionRuntime(db_path)

    execution_id = runtime.create_planned(
        "Build authenticating reverse proxy with OAuth2",
        intent=TaskIntent(
            goal="Build authenticating reverse proxy with OAuth2",
            tests_required=True,
            build_required=True,
            file_hints=("proxy/auth.py", "proxy/server.py", "tests/test_proxy.py"),
            symbol_hints=("ProxyHandler", "OAuthManager", "TokenVerifier"),
        ),
    )

    # Record stats
    stats_data = {
        "estimated_complexity": "complex",
        "actual_complexity": "complex",
        "files_changed": 3,
        "planner_iterations": 1,
        "verification_failures": 0,
        "wall_seconds": 12.5,
    }
    runtime.record_execution_stats(execution_id, stats_data)

    # Verify event replay
    replayed = runtime.engine.replay(execution_id)
    assert replayed.active_profile is not None
    assert replayed.stats == stats_data

    # Checkpoint & snapshot recovery
    runtime.engine.checkpoint(execution_id, "test_snapshot")
    recovered = runtime.recover(execution_id)
    assert recovered.active_profile == replayed.active_profile
    assert recovered.stats == stats_data


def test_profile_escalation_triggers_and_runtime_escalate(tmp_path):
    escalator = ProfileEscalator()

    # Simple profile escalating on touching > 2 files
    target, trigger = escalator.evaluate(
        ExecutionComplexity.SIMPLE,
        affected_files=4,
    )
    assert target == ExecutionComplexity.MEDIUM
    assert trigger is not None
    assert "affected 4 files" in trigger.reason

    # Runtime escalation test
    db_path = tmp_path / "execution.db"
    runtime = DurableExecutionRuntime(db_path)
    execution_id = runtime.create_planned("Small update")

    state_before = runtime.engine.state(execution_id)
    assert state_before.active_profile["complexity"] == "simple"

    # Trigger escalation to MEDIUM
    escalated = runtime.escalate_profile(execution_id, affected_files=4)
    assert escalated is True

    state_after = runtime.engine.state(execution_id)
    assert state_after.active_profile["complexity"] == "medium"

    # Verify ProfileEscalated event in event log
    events = runtime.store.load(execution_id)
    event_types = [evt.type for evt in events]
    assert "ProfileEscalated" in event_types


def test_execution_observability_inspector_explain(tmp_path):
    db_path = tmp_path / "execution.db"
    runtime = DurableExecutionRuntime(db_path)
    execution_id = runtime.create_planned("Write a quick script")

    inspector = ExecutionInspector(runtime.store)
    explanation = inspector.explain(runtime.engine.state(execution_id))

    assert "active_profile" in explanation
    assert explanation["active_profile"]["complexity"] == "simple"
    assert "execution_profile" in explanation


def test_cli_integration_profile_options(tmp_path):
    db_path = tmp_path / "executions.db"

    # Create simple execution via CLI
    create_res = runner.invoke(app, [
        "execution", "create", "Fix typo in docstring",
        "--db", str(db_path),
    ])
    assert create_res.exit_code == 0
    assert "Execution Profile: SIMPLE" in create_res.output
    execution_id = create_res.output.splitlines()[-1].strip()

    # Create explicit complex execution via CLI
    create_complex = runner.invoke(app, [
        "execution", "create", "Refactor core compiler engine",
        "--profile", "complex",
        "--db", str(db_path),
    ])
    assert create_complex.exit_code == 0
    assert "Execution Profile: COMPLEX" in create_complex.output

    # Explain execution via CLI
    explain_res = runner.invoke(app, [
        "execution", "explain", execution_id,
        "--db", str(db_path),
    ])
    assert explain_res.exit_code == 0
    output_json = json.loads(explain_res.output)
    assert "active_profile" in output_json
    assert output_json["active_profile"]["complexity"] == "simple"
