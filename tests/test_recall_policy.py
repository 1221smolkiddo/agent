from __future__ import annotations

import pytest

from code_agent.agent import CodingAgent
from code_agent.experience_memory.config import ExperienceMemoryConfig
from code_agent.experience_memory.recall_policy import MemoryRecallPolicy


@pytest.fixture
def policy():
    return MemoryRecallPolicy(ExperienceMemoryConfig(enabled=True, api_key="private-key"))


@pytest.mark.parametrize(("task", "workspace", "reason"), [
    ("fix this timeout", True, "repair"),
    ("this failed again", False, "continuity"),
    ("continue migration", False, "continuity"),
    ("why did we reject Redis?", False, "historical_reasoning"),
    ("refactor the existing architecture", True, "long_lived_work"),
    ("implement authentication across multiple files", True, "substantial_change"),
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
])
def test_trivial_or_unrelated_tasks_skip(policy, task):
    assert not policy.decide(task, workspace_task=False).should_recall


def test_query_uses_latest_task_only_and_sanitizes_secrets(policy):
    raw = "Fix parser timeout API_KEY=hidden-value sk-123456789012345678901234"
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
