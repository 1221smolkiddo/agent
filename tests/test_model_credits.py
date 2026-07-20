"""Tests for the OpenAICompatibleChatClient 402 insufficient credits retry logic."""

from __future__ import annotations

import httpx
import pytest
import openai

from code_agent.models import (
    InsufficientCreditsError,
    OpenAICompatibleChatClient,
    _parse_affordable_tokens,
)


def test_parse_affordable_tokens_extracts_number():
    msg = (
        "This request requires more credits, or fewer max_tokens. You requested "
        "up to 4096 tokens, but can only afford 748. To increase, visit ..."
    )
    assert _parse_affordable_tokens(msg) == 748


def test_parse_affordable_tokens_returns_none_if_missing():
    msg = "This request requires more credits, please top up."
    assert _parse_affordable_tokens(msg) is None


class MockCompletions:
    def __init__(self, responses, is_stream=False):
        self.responses = responses
        self.is_stream = is_stream
        self.call_count = 0
        self.requested_tokens = []
        self.requested_timeouts = []

    def create(self, **kwargs):
        self.call_count += 1
        self.requested_tokens.append(kwargs.get("max_tokens"))
        self.requested_timeouts.append(kwargs.get("timeout"))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class MockChat:
    def __init__(self, completions):
        self.completions = completions


class MockClient:
    def __init__(self, completions):
        self.chat = MockChat(completions)


class MockChoice:
    def __init__(self, content):
        class Message:
            def __init__(self, content):
                self.content = content
        class Delta:
            def __init__(self, content):
                self.content = content
        self.message = Message(content)
        self.delta = Delta(content)


class MockResponse:
    def __init__(self, content, usage=None):
        self.choices = [MockChoice(content)]
        self.usage = usage


def make_402_error(affordable: int | None = None) -> openai.APIStatusError:
    if affordable is not None:
        msg = f"can only afford {affordable}"
    else:
        msg = "Payment Required"
    
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(402, request=request)
    return openai.APIStatusError(message=msg, response=response, body=None)


def make_500_error() -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(500, request=request)
    return openai.APIStatusError(message="Internal Server Error", response=response, body=None)


def test_complete_retries_with_parsed_affordable_tokens(monkeypatch):
    client = OpenAICompatibleChatClient(api_key="test", base_url="test", model="test", max_tokens=4096)
    
    mock_completions = MockCompletions([
        make_402_error(748),
        MockResponse("Success!")
    ])
    monkeypatch.setattr(client, "_client", MockClient(mock_completions))

    result = client.complete([{"role": "user", "content": "hi"}])
    
    assert result == "Success!"
    assert mock_completions.call_count == 2
    assert mock_completions.requested_tokens == [4096, 748]
    
    records = client.drain_usage_records()
    assert len(records) == 2
    assert records[0].ok is False
    assert "402 Payment Required" in records[0].error
    assert records[1].ok is True


def test_complete_retries_with_halved_tokens_if_not_parsed(monkeypatch):
    client = OpenAICompatibleChatClient(api_key="test", base_url="test", model="test", max_tokens=4096)
    
    mock_completions = MockCompletions([
        make_402_error(None), # No affordable number
        make_402_error(None),
        MockResponse("Success!")
    ])
    monkeypatch.setattr(client, "_client", MockClient(mock_completions))

    result = client.complete([{"role": "user", "content": "hi"}])
    
    assert result == "Success!"
    assert mock_completions.call_count == 3
    assert mock_completions.requested_tokens == [4096, 2048, 1024]


def test_complete_raises_insufficient_credits_if_below_min_tokens(monkeypatch):
    client = OpenAICompatibleChatClient(api_key="test", base_url="test", model="test", max_tokens=4096)
    
    mock_completions = MockCompletions([
        make_402_error(32), # Below min_viable_tokens (64)
    ])
    monkeypatch.setattr(client, "_client", MockClient(mock_completions))

    with pytest.raises(InsufficientCreditsError) as exc_info:
        client.complete([{"role": "user", "content": "hi"}])
    
    assert "cannot afford even 64 output tokens" in str(exc_info.value)
    assert mock_completions.call_count == 1


def test_complete_raises_insufficient_credits_after_max_retries(monkeypatch):
    client = OpenAICompatibleChatClient(api_key="test", base_url="test", model="test", max_tokens=4096)
    
    mock_completions = MockCompletions([
        make_402_error(None),
        make_402_error(None),
        make_402_error(None),
        make_402_error(None), # 4th attempt fails
    ])
    monkeypatch.setattr(client, "_client", MockClient(mock_completions))

    with pytest.raises(InsufficientCreditsError) as exc_info:
        client.complete([{"role": "user", "content": "hi"}])
    
    assert "Exhausted credit retries" in str(exc_info.value)
    assert mock_completions.call_count == 4


def test_complete_retries_transient_server_errors(monkeypatch):
    client = OpenAICompatibleChatClient(
        api_key="test",
        base_url="test",
        model="test",
        max_tokens=4096,
        retry_base_delay_seconds=0,
    )
    
    mock_completions = MockCompletions([
        make_500_error(),
        MockResponse("Recovered"),
    ])
    monkeypatch.setattr(client, "_client", MockClient(mock_completions))

    assert client.complete([{"role": "user", "content": "hi"}]) == "Recovered"
    assert mock_completions.call_count == 2
    records = client.drain_usage_records()
    assert records[0].ok is False
    assert "server provider failure" in str(records[0].error)


def test_client_disables_sdk_retries_and_owns_retry_policy():
    client = OpenAICompatibleChatClient(
        api_key="test",
        base_url="https://example.com",
        model="test",
    )

    assert client._client.max_retries == 0


def test_logical_turn_deadline_is_forwarded_to_each_request(monkeypatch):
    client = OpenAICompatibleChatClient(
        api_key="test",
        base_url="test",
        model="test",
        retry_base_delay_seconds=0,
    )
    mock_completions = MockCompletions([make_500_error(), MockResponse("Recovered")])
    monkeypatch.setattr(client, "_client", MockClient(mock_completions))

    assert client.complete_with_timeout([], 12.5) == "Recovered"
    assert len(mock_completions.requested_timeouts) == 2
    assert all(0 < timeout <= 12.5 for timeout in mock_completions.requested_timeouts)
    assert mock_completions.requested_timeouts[1] <= mock_completions.requested_timeouts[0]


def test_complete_stops_after_transient_retry_budget(monkeypatch):
    client = OpenAICompatibleChatClient(
        api_key="test",
        base_url="test",
        model="test",
        max_tokens=4096,
        transient_retry_count=2,
        retry_base_delay_seconds=0,
    )
    mock_completions = MockCompletions([make_500_error(), make_500_error(), make_500_error()])
    monkeypatch.setattr(client, "_client", MockClient(mock_completions))

    with pytest.raises(openai.APIStatusError):
        client.complete([{"role": "user", "content": "hi"}])

    assert mock_completions.call_count == 3


def test_stream_complete_retries_on_402(monkeypatch):
    client = OpenAICompatibleChatClient(api_key="test", base_url="test", model="test", max_tokens=4096)
    
    def mock_stream():
        yield MockResponse("Stream")
        yield MockResponse("ed!")
    
    mock_completions = MockCompletions([
        make_402_error(748),
        mock_stream()
    ])
    monkeypatch.setattr(client, "_client", MockClient(mock_completions))

    chunks = []
    result = client.stream_complete([{"role": "user", "content": "hi"}], chunks.append)
    
    assert result == "Streamed!"
    assert chunks == ["Stream", "ed!"]
    assert mock_completions.call_count == 2
    assert mock_completions.requested_tokens == [4096, 748]
