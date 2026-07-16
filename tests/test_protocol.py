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
from code_agent.schema import ReadFileAction, RunShellAction, ToolResult


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
    reporter.recovery("try again")  # First retry: suppressed
    reporter.done()  # Suppressed

    events = parse_json_lines(stream.getvalue())
    assert [event["event"] for event in events] == [
        "status",
        "action_started",
    ]
    assert events[0]["label"] == "THINKING"
    assert events[1]["action_type"] == "read_file"
    assert events[1]["label"] == "Inspecting Project"
    assert events[1]["action"] == {"type": "read_file", "path": "README.md"}


def test_json_protocol_emits_machine_readable_command_diagnostics() -> None:
    stream = StringIO()
    reporter = JsonProtocolReporter(JsonEventEmitter(stream))
    action = RunShellAction(type="run_shell", command="npm run build")
    result = ToolResult(
        ok=False,
        output="build failed",
        metadata={
            "execution": {"exit_code": 1, "duration_ms": 12.0},
            "diagnostics": {
                "summary": "typescript reported a type error",
                "diagnostics": [{"path": "src/app.ts", "line": 4, "column": 10}],
            },
        },
    )

    reporter.tool_result(action, result, 12.0)

    event = parse_json_lines(stream.getvalue())[0]
    assert event["event"] == "action_finished"
    assert event["action_type"] == "run_shell"
    assert event["metadata"]["execution"]["exit_code"] == 1
    assert event["metadata"]["diagnostics"]["diagnostics"][0]["path"] == "src/app.ts"


def test_json_approval_callback_fails_closed_by_default() -> None:
    stream = StringIO()
    emitter = JsonEventEmitter(stream)
    approve = json_approval_callback(emitter)

    assert approve("read_file", "README.md") is False
    events = parse_json_lines(stream.getvalue())
    assert [event["event"] for event in events] == ["approval_requested", "approval_resolved"]
    assert events[0]["approved"] is False
    assert events[0]["mode"] == "default_deny"
    assert events[0]["request_id"]
    assert events[1]["request_id"] == events[0]["request_id"]
    assert events[1]["approved"] is False


class ApprovalResponseStream(StringIO):
    def __init__(self, event_stream: StringIO, *, approved: bool = True, reason: str = "trusted") -> None:
        super().__init__("")
        self.event_stream = event_stream
        self.approved = approved
        self.reason = reason

    def readline(self, *_args: Any, **_kwargs: Any) -> str:
        request = parse_json_lines(self.event_stream.getvalue())[-1]
        return json.dumps(
            {
                "type": "approval_response",
                "request_id": request["request_id"],
                "approved": self.approved,
                "reason": self.reason,
            }
        ) + "\n"


def test_json_approval_callback_accepts_stdin_response() -> None:
    stream = StringIO()
    emitter = JsonEventEmitter(stream)
    input_stream = ApprovalResponseStream(stream)
    approve = json_approval_callback(emitter, input_stream=input_stream)

    assert approve("read_file", "README.md") is True
    events = parse_json_lines(stream.getvalue())
    assert [event["event"] for event in events] == ["approval_requested", "approval_resolved"]
    assert events[0]["mode"] == "stdin"
    assert events[0]["approved"] is None
    assert events[0]["response_schema"]["request_id"] == events[0]["request_id"]
    assert events[1]["request_id"] == events[0]["request_id"]
    assert events[1]["approved"] is True
    assert events[1]["reason"] == "trusted"


def test_json_approval_callback_approve_all_rejects_manual_actions() -> None:
    stream = StringIO()
    emitter = JsonEventEmitter(stream)
    approve = json_approval_callback(emitter, approve_all=True)

    assert approve("read_file", "README.md") is True
    assert approve("run_shell", "uv run pytest") is False

    events = parse_json_lines(stream.getvalue())
    assert events[0]["mode"] == "approve_all"
    assert events[0]["approved"] is True
    assert events[2]["mode"] == "manual_required"
    assert events[2]["approved"] is False
    assert events[3]["approved"] is False
    assert "requires explicit manual approval" in events[3]["reason"]


def test_json_approval_callback_emits_metadata_for_frontends() -> None:
    stream = StringIO()
    emitter = JsonEventEmitter(stream)
    input_stream = ApprovalResponseStream(stream)
    approve = json_approval_callback(emitter, input_stream=input_stream)

    assert approve("apply_patch", "Patch preview", {"paths": ["a.py", "b.py"]}) is True
    events = parse_json_lines(stream.getvalue())

    assert events[0]["metadata"] == {"paths": ["a.py", "b.py"]}
    assert events[1]["request_id"] == events[0]["request_id"]


def test_json_approval_callback_rejects_invalid_stdin_response() -> None:
    stream = StringIO()
    emitter = JsonEventEmitter(stream)
    approve = json_approval_callback(
        emitter,
        input_stream=StringIO('{"type":"approval_response","request_id":"wrong","approved":true}\n'),
    )

    assert approve("write_file", "README.md") is False
    events = parse_json_lines(stream.getvalue())
    assert events[1]["event"] == "approval_resolved"
    assert events[1]["approved"] is False
    assert "request_id did not match" in events[1]["reason"]


def test_result_payload_contains_frontend_fields() -> None:
    result = AgentRunResult(
        message="done",
        run_id=42,
        task="inspect",
        changed_paths=["README.md"],
        context_records=[{"action": "repo_map", "ok": True}],
        model_usage_records=[{"model": "primary", "ok": True, "total_tokens": 12}],
        plan_updates=[{"target_files": ["README.md"], "owned_files": ["README.md"]}],
    )

    payload = result_payload(result)

    assert payload["run_id"] == 42
    assert payload["message"] == "done"
    assert payload["changed_paths"] == ["README.md"]
    assert payload["context_records"] == [{"action": "repo_map", "ok": True}]
    assert payload["model_usage_records"] == [{"model": "primary", "ok": True, "total_tokens": 12}]
    assert payload["plan_updates"] == [{"target_files": ["README.md"], "owned_files": ["README.md"]}]
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


def test_run_json_command_can_read_approval_from_stdin(monkeypatch, tmp_path: Path) -> None:
    class FakeAgent:
        def __init__(self, approval_callback: Any) -> None:
            self.approval_callback = approval_callback

        def run_detailed(self, task: str) -> AgentRunResult:
            approved = self.approval_callback("read_file", "README.md")
            return AgentRunResult(message=f"approved={approved}", run_id=9, task=task, blocked=not approved)

    def fake_create_agent(**kwargs: Any) -> FakeAgent:
        return FakeAgent(kwargs["approval_callback"])

    monkeypatch.setattr(cli, "create_agent", fake_create_agent)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["run-json", "read docs", "--cwd", str(tmp_path), "--approval-stdin", "--no-stream"],
        input='{"type":"approval_response","request_id":"wrong","approved":true,"reason":"editor approved"}\n',
    )

    assert result.exit_code == 1
    events = parse_json_lines(result.output)
    assert [event["event"] for event in events] == [
        "run_started",
        "approval_requested",
        "approval_resolved",
        "run_finished",
    ]
    assert events[1]["mode"] == "stdin"
    assert events[2]["approved"] is False
    assert "request_id did not match" in events[2]["reason"]
    assert events[3]["message"] == "approved=False"


def test_run_json_command_rejects_conflicting_approval_modes(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "run-json",
            "read docs",
            "--cwd",
            str(tmp_path),
            "--approve-all",
            "--approval-stdin",
        ],
    )

    assert result.exit_code == 2
    events = parse_json_lines(result.output)
    assert events[0]["event"] == "run_failed"
    assert events[0]["code"] == "invalid_approval_mode"


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
