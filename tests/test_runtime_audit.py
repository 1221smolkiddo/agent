"""Deterministic pre-release regressions: no credentials or provider network calls."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from code_agent.models import OpenAICompatibleChatClient
from code_agent.reviewer import run_reviewer_pass


SECRETS = (
    "sk-test-secret-value",
    "Authorization: Bearer abc123",
    "API_KEY=supersecret",
    "password=hunter2",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.fake-signature",
)
PRIVATE = "private-prompt-and-hidden-reasoning-marker"


class Clock:
    now = 0.0

    def advance(self, seconds):
        self.now += seconds


def sdk_client(monkeypatch, create, **kwargs):
    client = OpenAICompatibleChatClient(
        api_key="fake", base_url="https://example.invalid", model="fake",
        retry_base_delay_seconds=0, transient_retry_count=0, **kwargs,
    )
    monkeypatch.setattr(client._client.chat.completions, "create", create)
    return client


def response(content="done"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=content, reasoning_content=PRIVATE, reasoning_details=[PRIVATE],
    ))], usage=None)


@pytest.mark.parametrize("delay", [0, 0.2])
def test_fast_and_slow_below_deadline(monkeypatch, delay):
    clock = Clock()
    monkeypatch.setattr("code_agent.models.time.monotonic", lambda: clock.now)
    def create(**kwargs):
        clock.advance(delay)
        return response()
    client = sdk_client(monkeypatch, create)
    assert client.complete_with_timeout([], 1) == "done"


def test_late_sdk_response_is_rejected(monkeypatch):
    clock = Clock()
    monkeypatch.setattr("code_agent.models.time.monotonic", lambda: clock.now)
    def create(**kwargs):
        clock.advance(1.1)
        return response()
    client = sdk_client(monkeypatch, create)
    with pytest.raises(TimeoutError):
        client.complete_with_timeout([], 1)


def test_stream_deadline_closes_stream_and_drops_late_token(monkeypatch):
    clock = Clock()
    monkeypatch.setattr("code_agent.models.time.monotonic", lambda: clock.now)
    class Stream:
        closed = False
        def __iter__(self):
            clock.advance(1.1)
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=PRIVATE))])
        def close(self):
            self.closed = True
    stream = Stream()
    client = sdk_client(monkeypatch, lambda **kwargs: stream)
    seen = []
    with pytest.raises(TimeoutError):
        client.stream_complete_with_timeout([], seen.append, 1)
    assert stream.closed and seen == []


def test_hidden_sdk_fields_are_not_retained(monkeypatch):
    client = sdk_client(monkeypatch, lambda **kwargs: response())
    assert client.complete([]) == "done"
    assert PRIVATE not in str(client.drain_usage_records())


def test_provider_retry_diagnostics_drop_private_body(monkeypatch):
    attempts = []
    def create(**kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise TimeoutError(PRIVATE + " " + " ".join(SECRETS))
        return response()
    client = sdk_client(monkeypatch, create)
    client.transient_retry_count = 1
    assert client.complete([]) == "done"
    records = str(client.drain_usage_records())
    assert PRIVATE not in records
    for secret in SECRETS:
        assert secret not in records


def test_reviewer_does_not_persist_raw_reasoning_fields():
    class Client:
        def complete(self, messages):
            return json.dumps({"ok": True, "summary": "Checked", "reasoning_content": PRIVATE})
    result = run_reviewer_pass(
        Client(), task="audit", final_message="done", changed_paths=["x.py"],
        mutation_records=[], command_records=[], verification_results=[],
    )
    assert result.ok
    assert PRIVATE not in str(result.as_record())


class Tools:
    def __init__(self):
        self.approval_callback = None
        self.calls = 0

    def run(self, action):
        from code_agent.schema import ToolResult
        self.calls += 1
        if self.approval_callback:
            self.approval_callback(action.type, "safe command")
        return ToolResult(ok=True, output="checked")


class Model:
    model = "fake"
    def __init__(self, responses, clock=None, delay=0):
        self.responses, self.clock, self.delay = iter(responses), clock, delay
    def complete(self, messages):
        if self.clock:
            self.clock.advance(self.delay)
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


def agent_for(tmp_path, model, tools=None, **kwargs):
    from code_agent.agent import CodingAgent
    from code_agent.storage import AgentStorage
    return CodingAgent(
        cwd=tmp_path, dry_run=False, max_steps=8, max_failures=3,
        model_client=model, tools=tools or Tools(),
        storage=AgentStorage(tmp_path / "audit.db"), stream_model=False, **kwargs,
    )


def test_approval_pause_applies_to_post_model_deadline(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr("code_agent.agent.perf_counter", lambda: clock.now)
    tools = Tools()
    def callback(*args):
        clock.advance(400)
        return True
    tools.approval_callback = callback
    model = Model(['{"type":"run_shell","command":"echo safe"}',
                   '{"type":"final","message":"done"}'], clock, 1)
    result = agent_for(tmp_path, model, tools).run_detailed("execute a safe command in the repository")
    assert not result.blocked, result.message
    assert tools.approval_callback is callback


def test_model_failure_does_not_echo_provider_body(tmp_path):
    agent = agent_for(tmp_path, Model([TimeoutError(PRIVATE + " " + " ".join(SECRETS))]))
    result = agent.run_detailed("explain something")
    assert result.blocked and PRIVATE not in result.message
    for secret in SECRETS:
        assert secret not in result.message
    assert result.failed_actions[0]["category"] == "MODEL_TIMEOUT"


def test_malformed_action_is_not_persisted_verbatim(tmp_path):
    agent = agent_for(tmp_path, Model([PRIVATE + " {not valid}", '{"type":"final","message":"done"}']))
    result = agent.run_detailed("explain something")
    assert PRIVATE not in str(agent.storage.run_steps_payloads(result.run_id))


def test_terminal_response_and_error_card_redact_secrets(monkeypatch):
    from io import StringIO
    from rich.console import Console
    from code_agent import terminal_ui
    output = StringIO()
    monkeypatch.setattr(terminal_ui, "console", Console(file=output, width=180))
    terminal_ui.print_response("Agent47", "\n".join(SECRETS))
    terminal_ui.print_error_card("Failure", [("Reason:", "\n".join(SECRETS))], [])
    for secret in SECRETS:
        assert secret not in output.getvalue()



def test_fallback_shares_one_deadline(monkeypatch):
    from code_agent.models import FallbackModelClient
    clock = Clock()
    monkeypatch.setattr("code_agent.models.time.monotonic", lambda: clock.now)
    calls = []
    class Client:
        def __init__(self, model, delay, fail):
            self.model, self.delay, self.fail = model, delay, fail
        def complete_with_timeout(self, messages, timeout):
            calls.append((self.model, timeout))
            clock.advance(self.delay)
            if self.fail:
                raise TimeoutError("provider network timeout")
            return "done"
        def drain_usage_records(self):
            return []
    client = FallbackModelClient([Client("primary", .6, True), Client("fallback", .2, False)])
    assert client.complete_with_timeout([], 1) == "done"
    assert calls[1][1] == pytest.approx(.4)


def test_fallback_error_does_not_echo_raw_provider_body():
    from code_agent.models import FallbackModelClient
    class Client:
        model = "fake"
        def complete(self, messages):
            raise TimeoutError(PRIVATE + " " + " ".join(SECRETS))
        def drain_usage_records(self):
            return []
    client = FallbackModelClient([Client()])
    with pytest.raises(RuntimeError) as error:
        client.complete([])
    assert PRIVATE not in str(error.value)
    assert PRIVATE not in str(client.drain_usage_records())


def test_planner_timeout_uses_deterministic_fallback():
    from code_agent.execution_host import ModelPlanProvider
    class Client:
        def complete_with_timeout(self, messages, timeout):
            raise TimeoutError(PRIVATE)
    planner = ModelPlanProvider(Client(), timeout_seconds=.01)
    assert planner.plan("fix a parser") and planner.fallback_used
    assert planner.last_error == "TimeoutError"


@pytest.mark.parametrize("budget,blocked,tool_calls", [(300,False,3),(600,False,3)])
def test_multicycle_task_ignores_deprecated_run_budget(tmp_path, monkeypatch, budget, blocked, tool_calls):
    clock = Clock()
    monkeypatch.setattr("code_agent.agent.perf_counter", lambda: clock.now)
    model = Model(['{"type":"read_file","path":"a.py"}',
                   '{"type":"read_file","path":"b.py"}',
                   '{"type":"read_file","path":"c.py"}',
                   '{"type":"final","message":"done"}'], clock, 80)
    tools = Tools()
    result = agent_for(tmp_path, model, tools, run_timeout_seconds=budget, model_timeout_seconds=100).run_detailed("inspect files")
    assert result.blocked == blocked and tools.calls == tool_calls
    assert clock.now == 320



def test_model_phase_telemetry_is_metadata_only(tmp_path):
    agent = agent_for(tmp_path, Model(['{"type":"final","message":"done"}']))
    result = agent.run_detailed("explain " + PRIVATE)
    timings = [item["payload"] for item in agent.storage.run_steps_payloads(result.run_id)
               if item["payload"].get("type") == "runtime_timing"]
    assert [item["event"] for item in timings] == ["start", "end"]
    assert all(item["phase"] == "model_request" for item in timings)
    assert PRIVATE not in str(timings)
    assert timings[-1]["elapsed_ms"] >= 0


def test_status_model_wait_does_not_keep_previous_tool_label(monkeypatch):
    from code_agent.status import StatusReporter
    reporter = object.__new__(StatusReporter)
    reporter._current_label = "Editing Files"
    reporter._current_detail = "private path"
    reporter._stages = [("previous tool", "done")]
    monkeypatch.setattr(reporter, "_update", lambda: None)
    reporter.thinking(2)
    assert reporter._current_label == "Thinking"
    assert reporter._current_detail == "Waiting for model"


def test_actual_process_timeout_cleans_registered_process(tmp_path):
    import os
    import sys
    from code_agent.processes import ProcessSupervisor
    class Supervisor(ProcessSupervisor):
        def _register(self, process):
            self.spawned = process
            super()._register(process)
    supervisor = Supervisor()
    result = supervisor.run_shell(
        "safe test", cwd=tmp_path, timeout_seconds=.05, env=os.environ.copy(),
        argv=[sys.executable, "-c", "import time; time.sleep(10)"],
    )
    assert result.timed_out and result.cleanup_attempted
    assert supervisor.spawned.poll() is not None and not supervisor._active


def test_model_request_redacts_recognized_secrets(monkeypatch):
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        return response()
    client = sdk_client(monkeypatch, create)
    client.complete([{"role": "user", "content": "\n".join(SECRETS)}])
    for secret in SECRETS:
        assert secret not in str(requests[0]["messages"])



def test_registered_tool_results_are_redacted_before_runtime_use(tmp_path, monkeypatch):
    from code_agent.tools import ToolRegistry
    from code_agent.schema import ListFilesAction, ToolResult
    tools = ToolRegistry(workspace=tmp_path, dry_run=False)
    monkeypatch.setattr(tools, "_dispatch", lambda action: ToolResult(
        ok=False, output="\n".join(SECRETS), metadata={"stderr": "\n".join(SECRETS)},
    ))
    try:
        result = tools.run(ListFilesAction(type="list_files", path="."))
        for secret in SECRETS:
            assert secret not in result.output and secret not in str(result.metadata)
    finally:
        tools.close()


def test_run_storage_and_work_report_redact_secrets(tmp_path):
    from code_agent.storage import AgentStorage
    storage = AgentStorage(tmp_path / "storage.db")
    text = "\n".join(SECRETS)
    run = storage.create_run(text, "fake", tmp_path)
    storage.add_step(run, "tool", {"output": text, "nested": {"stderr": text}})
    storage.add_model_usage(run, {"model": "fake", "ok": False, "error": text})
    storage.save_work_report(run, text, {"body": text})
    stored = str(dict(storage.get_run(run))) + str(storage.run_steps_payloads(run))
    stored += str(storage.model_usage(run)) + str(storage.get_work_report(run))
    for secret in SECRETS:
        assert secret not in stored


def test_stream_ignores_hidden_delta_fields(monkeypatch):
    event = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
        content="visible", reasoning_content=PRIVATE, reasoning_details=[PRIVATE],
    ))])
    client = sdk_client(monkeypatch, lambda **kwargs: iter([event]))
    visible = []
    assert client.stream_complete([], visible.append) == "visible"
    assert visible == ["visible"] and PRIVATE not in str(client.drain_usage_records())



def test_windows_ctrl_break_systemerror_uses_taskkill_fallback(monkeypatch):
    from code_agent import processes
    calls = []
    class Process:
        pid = 1234
        returncode = None
        def poll(self):
            return self.returncode
        def send_signal(self, value):
            raise SystemError("Windows CTRL_BREAK_EVENT failed")
        def wait(self, timeout):
            assert self.returncode == 0
    process = Process()
    def run(args, **kwargs):
        calls.append(args)
        process.returncode = 0
    monkeypatch.setattr(processes, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(processes.signal, "CTRL_BREAK_EVENT", 1, raising=False)
    monkeypatch.setattr(processes, "windows_creation_flags", lambda **kwargs: 0)
    monkeypatch.setattr(processes.subprocess, "run", run)
    processes.terminate_process_tree(process)
    assert calls == [["taskkill", "/T", "/PID", "1234"]]
