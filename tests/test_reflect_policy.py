from dataclasses import replace

import pytest

from code_agent.experience_memory.config import ExperienceMemoryConfig
from code_agent.experience_memory.reflect_policy import ReflectionEvidence, ReflectPolicy


@pytest.fixture
def policy():
    return ReflectPolicy(ExperienceMemoryConfig(enabled=True, api_key="private-memory-key"))


def evidence(**kwargs):
    return ReflectionEvidence("Callback assertion failed", category="test", **kwargs)


def test_first_ordinary_failure_does_not_reflect(policy):
    assert policy.decide("fix callback", evidence()).reason == "first_ordinary_failure"


@pytest.mark.parametrize(("kwargs", "reason"), [
    ({"occurrences": 2}, "repeated_diagnostic"),
    ({"failed_repair": True, "verification_failed": True}, "verification_after_failed_repair"),
    ({"prior_occurrences": 3}, "recurring_diagnostic_history"),
    ({"conflicting_history": True, "recovery_attempts": 1}, "conflicting_historical_experiences"),
    ({"historical_summaries": ("Earlier fix",), "recovery_attempts": 1},
     "unresolved_with_recalled_history"),
])
def test_strong_escalation_triggers(policy, kwargs, reason):
    decision = policy.decide("fix callback", evidence(**kwargs))
    assert decision.should_reflect and decision.reason == reason


@pytest.mark.parametrize("kwargs", [
    {"unresolved": False}, {"cancelled_or_denied": True},
    {"deterministic_recovery_available": True}, {"diagnosis_complete": False},
])
def test_existing_recovery_and_resolved_cases_skip(policy, kwargs):
    assert not policy.decide("fix callback", evidence(occurrences=3, **kwargs)).should_reflect


@pytest.mark.parametrize("category", ["syntax", "style", "provider", "network", "permission",
                                      "environment", "infrastructure", "cancelled"])
def test_non_code_and_obvious_fixes_skip(policy, category):
    assert not policy.decide("fix callback", replace(evidence(occurrences=3),
                                                     category=category)).should_reflect


def test_query_is_bounded_structured_and_sanitized(policy):
    import json

    decision = policy.decide(
        "Fix callback TOKEN=secret private-memory-key\nRecent interactive transcript:\nprivate transcript",
        ReflectionEvidence("Assertion API_KEY=secret private-memory-key", occurrences=2,
                           attempted_strategies=("repair TOKEN=secret",),
                           historical_summaries=("Earlier cause private-memory-key",)),
    )
    assert decision.should_reflect and len(decision.query) <= 1800
    assert "secret" not in decision.query and "private-memory-key" not in decision.query
    assert "private transcript" not in decision.query
    assert json.loads(decision.query)["attempted_strategies"]


def test_reflect_can_be_disabled_independently():
    for config in [ExperienceMemoryConfig(), ExperienceMemoryConfig(
        enabled=True, api_key="key", automatic_reflect_enabled=False,
    ), ExperienceMemoryConfig(enabled=True, api_key="key", automatic_reflect_max_requests=0)]:
        assert not ReflectPolicy(config).decide("fix callback", evidence(occurrences=2)).should_reflect


@pytest.mark.parametrize("task", ["fix callback\ndef callback():\n    return private_data",
                                  "fix callback\nconst credential = private_data;",
                                  "fix callback\n```python\nprivate_data\n```", "x" * 4001])
def test_source_and_dump_inputs_are_rejected_before_json_encoding(policy, task):
    decision = policy.decide(task, evidence(occurrences=2))
    assert not decision.should_reflect and decision.reason == "unsafe_query"
