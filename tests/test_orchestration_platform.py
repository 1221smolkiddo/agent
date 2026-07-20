import threading

import pytest

from code_agent.orchestration import (
    AgentCatalog,
    MultiAgentOrchestrator,
    OrchestrationTask,
    SubagentManager,
    SubagentRequest,
)


def test_subagent_context_and_budgets_are_isolated_and_capped():
    parent = {"nested": {"value": 1}}
    seen = {}

    def runner(request, profile, _cancel: threading.Event):
        request.context["nested"]["value"] = 2
        seen["request"] = request
        return {"summary": profile.name}

    catalog = AgentCatalog()
    manager = SubagentManager(catalog, runner, max_workers=1)
    result = manager.spawn(
        SubagentRequest("implement", "coder", parent, token_budget=999999, execution_budget=999)
    ).result(timeout=2)
    assert result.ok
    assert parent["nested"]["value"] == 1
    assert seen["request"].token_budget == catalog.get("coder").token_budget
    assert seen["request"].execution_budget == catalog.get("coder").execution_budget
    manager.close()


def test_orchestrator_runs_dependency_dag_and_contains_failures():
    def runner(request, _profile, _cancel):
        if request.task == "fail":
            raise RuntimeError("contained")
        return {"summary": request.task}

    catalog = AgentCatalog()
    manager = SubagentManager(catalog, runner, max_workers=2)
    orchestrator = MultiAgentOrchestrator(catalog, manager)
    results = orchestrator.execute([
        OrchestrationTask("a", "plan", preferred_agent="planner"),
        OrchestrationTask("b", "implement", ("a",), "coder"),
    ])
    assert list(results) == ["a", "b"]
    failed = manager.spawn(SubagentRequest("fail", "reviewer")).result(timeout=2)
    assert not failed.ok and "contained" in failed.error
    with pytest.raises(ValueError, match="cycle"):
        orchestrator.execute([
            OrchestrationTask("x", "x", ("y",)),
            OrchestrationTask("y", "y", ("x",)),
        ])
    manager.close()
