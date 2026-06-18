from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

import code_agent.cli as cli
from code_agent.agent import AgentRunResult
from code_agent.cli import app
from code_agent.protocol import (
    PROTOCOL_VERSION,
    JsonEventEmitter,
    JsonProtocolReporter,
    event_to_json_line,
    json_approval_callback,
    protocol_event,
    result_payload,
)
from code_agent.schema import ReadFileAction


def parse_json_lines(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def test_protocol_event_is_versioned_and_json_serializable() -> None:
    event = protocol_event("status", label="THINKING", detail="step 1")
    line = event_to_json_line(event)
    decoded = json.loads(line)

    assert decoded["protocol"] == PROTOCOL_VERSION
    assert decoded["event"] == "status"
    assert decoded["label"] == "THINKING"
    assert "timestamp" in decoded


def test_json_protocol_reporter_emits_action_events() -> None:
    stream = StringIO()
    emitter = JsonEventEmitter(stream)
    reporter = JsonProtocolReporter(emitter)

    reporter.thinking(1)
    reporter.action(ReadFileAction(type="read_file", path="README.md"))
    reporter.recovery("try again")
    reporter.done()

    events = parse_json_lines(stream.getvalue())
    assert [event["event"] for event in events] == [
        "status",
        "action_started",
        "recovery",
        "status",
    ]
    assert events[1]["action_type"] == "read_file"
    assert events[1]["label"] == "READING"
    assert events[1]["action"] == {"type": "read_file", "path": "README.md"}


def test_json_approval_callback_fails_closed_by_default() -> None:
    stream = StringIO()
    emitter = JsonEventEmitter(stream)
    approve = json_approval_callback(emitter)

    assert approve("read_file", "README.md") is False
    event = parse_json_lines(stream.getvalue())[0]
    assert event["event"] == "approval_requested"
    assert event["approved"] is False
    assert event["mode"] == "default_deny"


def test_result_payload_contains_frontend_fields() -> None:
    result = AgentRunResult(
        message="done",
        run_id=42,
        task="inspect",
        changed_paths=["README.md"],
        context_records=[{"action": "repo_map", "ok": True}],
    )

    payload = result_payload(result)

    assert payload["run_id"] == 42
    assert payload["message"] == "done"
    assert payload["changed_paths"] == ["README.md"]
    assert payload["context_records"] == [{"action": "repo_map", "ok": True}]
    assert payload["failed_actions"] == []


def test_run_json_command_emits_ndjson_events(monkeypatch, tmp_path: Path) -> None:
    class FakeAgent:
        def run_detailed(self, task: str) -> AgentRunResult:
            return AgentRunResult(message="json done", run_id=7, task=task)

    captured: dict[str, Any] = {}

    def fake_create_agent(**kwargs: Any) -> FakeAgent:
        captured.update(kwargs)
        return FakeAgent()

    monkeypatch.setattr(cli, "create_agent", fake_create_agent)
    runner = CliRunner()

    result = runner.invoke(app, ["run-json", "say hi", "--cwd", str(tmp_path), "--no-stream"])

    assert result.exit_code == 0
    events = parse_json_lines(result.output)
    assert [event["event"] for event in events] == ["run_started", "run_finished"]
    assert events[0]["task"] == "say hi"
    assert events[0]["cwd"] == str(tmp_path.resolve())
    assert events[1]["message"] == "json done"
    assert events[1]["run_id"] == 7
    assert isinstance(captured["reporter"], JsonProtocolReporter)
    assert captured["stream_model"] is False


def test_run_json_command_reports_failures(monkeypatch, tmp_path: Path) -> None:
    def fake_create_agent(**_kwargs: Any) -> Any:
        raise RuntimeError("missing key")

    monkeypatch.setattr(cli, "create_agent", fake_create_agent)
    runner = CliRunner()

    result = runner.invoke(app, ["run-json", "say hi", "--cwd", str(tmp_path)])

    assert result.exit_code == 1
    events = parse_json_lines(result.output)
    assert [event["event"] for event in events] == ["run_started", "run_failed"]
    assert events[1]["code"] == "RuntimeError"
    assert events[1]["message"] == "missing key"
