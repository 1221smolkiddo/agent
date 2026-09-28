from __future__ import annotations

import pytest

from code_agent.agent import CodingAgent
from code_agent.experience_memory.config import ExperienceMemoryConfig
from code_agent.experience_memory.recall_policy import MemoryRecallPolicy


@pytest.fixture
def policy():
    return MemoryRecallPolicy(ExperienceMemoryConfig(enabled=True, api_key="private-key"))


@pytest.mark.parametrize(("task", "workspace", "reason"), [
    ("fix timeout across multiple services", True, "repair"),
    ("this failed again", False, "continuity"),
    ("continue migration", False, "continuity"),
    ("why did we reject Redis?", False, "historical_reasoning"),
    ("refactor the existing architecture", True, "substantial_change"),
    ("implement authentication across multiple files", True, "substantial_change"),
    ("fix this simple bug again", True, "continuity"),
    ("repair recurring CI failure", True, "continuity"),
    ("investigate flaky tests", True, "continuity"),
    ("migrate the database schema", True, "long_lived_work"),
    ("upgrade the dependency", True, "long_lived_work"),
    ("release the service", True, "long_lived_work"),
    ("rollback the release", True, "long_lived_work"),
    ("investigate the incident", True, "long_lived_work"),
    ("explain the historical rationale", False, "historical_reasoning"),
    ("recall the architecture decision", False, "historical_reasoning"),
    ("change the architecture", True, "substantial_change"),
    ("implement a cross-cutting cache change", True, "substantial_change"),
    ("fix a distributed race condition", True, "repair"),
])
def test_recall_for_historical_engineering_signals(policy, task, workspace, reason):
    decision = policy.decide(task, workspace_task=workspace)
    assert decision.should_recall and decision.reason == reason
    assert decision.memory_types == ("observation", "experience")
    assert decision.requested_budget == "low"
    assert decision.query == task


@pytest.mark.parametrize("task", [
    "rename the local variable", "formatting only", "fix whitespace",
    "What is a binary search?", "write an isolated sorting algorithm",
    "fix this timeout", "fix the parser timeout", "fix a simple off-by-one bug",
    "repair an isolated simple bug", "debug this local error",
    "fix this failing unit test", "repair the first-time parser crash",
    "fix the error in a single function", "CI is failing",
    "rename a parameter again", "formatting-only change to the service",
])
@pytest.mark.parametrize("workspace", [True, False])
def test_trivial_or_unrelated_tasks_skip(policy, task, workspace):
    decision = policy.decide(task, workspace_task=workspace)
    assert not decision.should_recall and decision.query == ""


def test_query_uses_latest_task_only_and_sanitizes_secrets(policy):
    raw = "Fix recurring parser timeout API_KEY=hidden-value sk-123456789012345678901234"
    task = raw + "\nRecent interactive transcript for reference:\nA long private transcript"
    decision = policy.decide(CodingAgent._extract_user_task(task), workspace_task=True)
    assert decision.should_recall
    assert "hidden-value" not in decision.query
    assert "sk-123" not in decision.query
    assert "private transcript" not in decision.query
    assert len(decision.query) <= 800


def test_disabled_or_zero_request_budget_skips():
    disabled = MemoryRecallPolicy(ExperienceMemoryConfig())
    assert disabled.decide("fix timeout", workspace_task=True).reason == "disabled"
    zero = MemoryRecallPolicy(ExperienceMemoryConfig(
        enabled=True, api_key="key", automatic_recall_max_requests=0,
    ))
    assert zero.decide("fix timeout", workspace_task=True).reason == "budget_disabled"


def test_known_recurring_diagnostic_uses_structured_history(policy):
    first = policy.decide("fix this local error", workspace_task=True)
    recurring = policy.decide(
        "fix this local error", workspace_task=True, prior_diagnostic_occurrences=1,
    )
    assert not first.should_recall
    assert recurring.should_recall and recurring.reason == "recurring_diagnostic_history"


@pytest.mark.parametrize("occurrences", [0, -1, True, "1", None])
def test_invalid_or_empty_diagnostic_history_does_not_enable_recall(policy, occurrences):
    assert not policy.decide(
        "fix this local error", workspace_task=True, prior_diagnostic_occurrences=occurrences,
    ).should_recall


def test_structured_history_does_not_override_mechanical_control(policy):
    assert not policy.decide(
        "rename the local variable", workspace_task=True, prior_diagnostic_occurrences=4,
    ).should_recall


def test_decision_repr_omits_task_and_sanitized_query(policy):
    decision = policy.decide("fix recurring private-parser diagnostic", workspace_task=True)
    assert decision.should_recall
    assert "private-parser" not in repr(decision)
