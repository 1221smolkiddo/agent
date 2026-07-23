from __future__ import annotations

from pathlib import Path

import pytest

from code_agent.agent import CodingAgent
from code_agent.config import Settings
from code_agent.factory import create_agent
from code_agent.model_profiles import resolve_model_profile
from code_agent.models import (
    FallbackModelClient,
    ModelProviderConfig,
    ModelUsageRecord,
    classify_model_error,
    create_fallback_client,
)
from code_agent.schema import AgentAction, ToolResult
from code_agent.storage import AgentStorage


class FakeUsageClient:
    def __init__(self, model: str, responses: list[str] | None = None, error: str | None = None) -> None:
        self.model = model
        self.responses = responses or []
        self.error = error
        self.records: list[ModelUsageRecord] = []

    def complete(self, _messages):
        if self.error:
            raise RuntimeError(self.error)
        self.records.append(
            ModelUsageRecord(
                model=self.model,
                ok=True,
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            )
        )
        return self.responses.pop(0)

    def stream_complete(self, messages, _on_token):
        return self.complete(messages)

    def drain_usage_records(self) -> list[ModelUsageRecord]:
        records = self.records
        self.records = []
        return records


class FakeAuthErrorClient(FakeUsageClient):
    def complete(self, _messages):
        raise PermissionError("authentication failed")


class NoopTools:
    def run(self, _action: AgentAction) -> ToolResult:
        return ToolResult(ok=True, output="ok")


class FakeReporter:
    def __init__(self) -> None:
        self.done_calls = 0

    def workspace_analysis(self, _summary: str) -> None:
        pass

    def thinking(self, _step: int) -> None:
        pass

    def recovery(self, _detail: str) -> None:
        pass

    def done(self) -> None:
        self.done_calls += 1


def test_fallback_model_client_uses_next_model_after_failure() -> None:
    client = FallbackModelClient(
        [
            FakeUsageClient("primary", error="rate limited"),
            FakeUsageClient("fallback", responses=['{"type":"final","message":"done"}']),
        ]
    )

    response = client.complete([])
    records = [item.as_dict() for item in client.drain_usage_records()]

    assert response == '{"type":"final","message":"done"}'
    assert client.model == "fallback"
    assert records == [
        {
            "provider": "openai-compatible",
            "model": "primary",
            "ok": False,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "estimated_cost_usd": None,
            "error": "RuntimeError: rate limited",
            "fallback_from": None,
        },
        {
            "provider": "openai-compatible",
            "model": "fallback",
            "ok": True,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "estimated_cost_usd": None,
            "error": None,
            "fallback_from": "primary",
        },
    ]


def test_create_fallback_client_returns_single_client_without_fallbacks() -> None:
    profile = resolve_model_profile("default", default_model="primary", max_tokens=1000)
    provider = ModelProviderConfig(
        api_key="test",
        base_url="https://example.com",
        input_cost_per_million=2.0,
        output_cost_per_million=4.0,
    )

    client = create_fallback_client(provider, profile, [])

    assert client.model == "primary"
    assert client._estimate_cost(1_000_000, 500_000) == 4.0


def test_factory_warns_when_no_fallback_model_is_configured(tmp_path: Path) -> None:
    settings = Settings(
        agent_model_preset=None,
        openrouter_api_key="test-key",
        agent_model="primary",
        agent_fallback_models="",
        agent_reviewer_pass=False,
        agent_db_path=tmp_path / "agent.db",
    )

    with pytest.warns(RuntimeWarning, match="no fallback model configured"):
        create_agent(settings, tmp_path, None, True, 1)


def test_create_fallback_client_passes_timeout_to_model_client() -> None:
    profile = resolve_model_profile("default", default_model="primary", max_tokens=1000)
    provider = ModelProviderConfig(
        api_key="test",
        base_url="https://example.com",
        timeout_seconds=12.5,
    )

    client = create_fallback_client(provider, profile, [])

    assert client.timeout_seconds == 12.5


def test_create_fallback_client_passes_retry_policy_to_model_client() -> None:
    profile = resolve_model_profile("default", default_model="primary", max_tokens=1000)
    provider = ModelProviderConfig(
        api_key="test",
        base_url="https://example.com",
        transient_retry_count=4,
        retry_base_delay_seconds=0.25,
        retry_max_delay_seconds=3.0,
    )

    client = create_fallback_client(provider, profile, [])

    assert client.transient_retry_count == 4
    assert client.retry_base_delay_seconds == 0.25
    assert client.retry_max_delay_seconds == 3.0


def test_model_client_rejects_negative_retry_policy() -> None:
    profile = resolve_model_profile("default", default_model="primary", max_tokens=1000)
    provider = ModelProviderConfig(
        api_key="test",
        base_url="https://example.com",
        transient_retry_count=-1,
    )

    with pytest.raises(ValueError, match="retry counts"):
        create_fallback_client(provider, profile, [])


def test_factory_uses_configured_fallback_models(tmp_path: Path) -> None:
    settings = Settings(
        agent_model_preset=None,
        openrouter_api_key="test-key",
        agent_model="primary",
        agent_fallback_models="fallback-a, fallback-b",
        agent_db_path=tmp_path / "agent.db",
    )

    agent = create_agent(settings=settings, cwd=tmp_path, model=None, dry_run=True, max_steps=1)

    assert isinstance(agent.model_client, FallbackModelClient)
    assert [client.model for client in agent.model_client.clients] == [
        "primary",
        "fallback-a",
        "fallback-b",
    ]


def test_factory_uses_registered_fallback_model_provider(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        agent_model_preset="gemini-flash",
        gemini_api_key="gemini-key",
        nvidia_api_key="nvidia-key",
        agent_fallback_models="z-ai/glm-5.2",
        agent_reviewer_pass=False,
        agent_db_path=tmp_path / "agent.db",
    )

    agent = create_agent(settings=settings, cwd=tmp_path, model=None, dry_run=True, max_steps=1)

    assert isinstance(agent.model_client, FallbackModelClient)
    assert [client.model for client in agent.model_client.clients] == [
        "gemini-3.5-flash",
        "z-ai/glm-5.2",
    ]
    assert [client.provider_name for client in agent.model_client.clients] == ["gemini", "nvidia"]
    assert agent.model_client.clients[1].include_stream_usage is False


def test_registered_model_runtime_defaults_are_applied(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        agent_model_preset="deepseek-v4-flash",
        nvidia_api_key="nvidia-key",
        agent_max_tokens=16384,
        agent_fallback_models="",
        agent_reviewer_pass=False,
        agent_db_path=tmp_path / "agent.db",
    )

    with pytest.warns(RuntimeWarning, match="no fallback model configured"):
        agent = create_agent(settings=settings, cwd=tmp_path, model=None, dry_run=True, max_steps=1)

    assert agent.model_client.max_tokens == 8192
    assert agent.model_client.temperature == 0.2
    assert agent.model_client.include_stream_usage is False
    assert agent.model_client.extra_body == {
        "chat_template_kwargs": {"thinking": True, "reasoning_effort": "high"}
    }


def test_fallback_client_stops_on_non_fallbackable_auth_error() -> None:
    client = FallbackModelClient(
        [
            FakeAuthErrorClient("primary"),
            FakeUsageClient("fallback", responses=['{"type":"final","message":"done"}']),
        ]
    )

    try:
        client.complete([])
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected auth failure to stop fallback")

    assert "non-fallbackable" in message
    assert client.model == "primary"


def test_model_error_classifier_marks_capacity_as_fallbackable() -> None:
    classified = classify_model_error(
        RuntimeError("ResourceExhausted: Worker local total request limit reached (32/32)")
    )

    assert classified.kind == "capacity"
    assert classified.fallbackable is True


def test_agent_persists_model_usage_records(tmp_path: Path) -> None:
    model = FakeUsageClient("primary", responses=['{"type":"final","message":"done"}'])
    storage = AgentStorage(tmp_path / "agent.db")
    agent = CodingAgent(
        cwd=tmp_path,
        dry_run=True,
        max_steps=3,
        max_failures=2,
        model_client=model,
        tools=NoopTools(),  # type: ignore[arg-type]
        storage=storage,
        stream_model=False,
    )

    result = agent.run_detailed("hello")
    stored = storage.model_usage(result.run_id)

    assert len(result.model_usage_records) == 1
    usage_without_latency = dict(result.model_usage_records[0])
    latency_ms = usage_without_latency.pop("latency_ms")
    assert usage_without_latency == {
        "provider": "openai-compatible",
        "model": "primary",
        "ok": True,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "estimated_cost_usd": None,
        "error": None,
        "fallback_from": None,
    }
    assert latency_ms >= 0
    assert stored[0]["payload"] == result.model_usage_records[0]


def test_agent_turns_model_failure_into_blocked_result(tmp_path: Path) -> None:
    model = FakeUsageClient("primary", error="provider unavailable")
    storage = AgentStorage(tmp_path / "agent.db")
    reporter = FakeReporter()
    agent = CodingAgent(
        cwd=tmp_path,
        dry_run=True,
        max_steps=3,
        max_failures=2,
        model_client=model,
        tools=NoopTools(),  # type: ignore[arg-type]
        storage=storage,
        reporter=reporter,  # type: ignore[arg-type]
        stream_model=False,
    )

    result = agent.run_detailed("inspect this project")

    assert result.blocked
    assert "Stopped after a model failure" in result.message
    assert result.failed_actions[0]["type"] == "model_failure"
    assert reporter.done_calls == 1
