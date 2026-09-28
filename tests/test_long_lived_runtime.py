from __future__ import annotations

import io
import sqlite3
from types import SimpleNamespace

import pytest
from rich.console import Console

from code_agent.agent import AgentRunResult, CodingAgent
from code_agent.config import Settings
from code_agent.context_budget import ContextBudgetExceeded, bound_messages, estimate_tokens
from code_agent.durable_execution import Command, DurableExecutionRuntime
from code_agent.interactive import run_interactive_turn, task_with_context
from code_agent.models import OpenAICompatibleChatClient
from code_agent.protocol import JsonEventEmitter
from code_agent.safety import sanitize_payload
from code_agent.schema import RunShellAction, ToolResult
from code_agent.session import SessionState
from code_agent.status import StatusReporter
from code_agent.storage import AgentStorage
from code_agent.work_report import build_work_report_payload

SECRETS = ["sk-test-secret-value", "Authorization: Bearer abc123",
           "authorization: bearer short", "API_KEY=supersecret", "password=hunter2",
           "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.fake-signature"]
PRIVATE = "private-provider-prompt-response-marker"


class Clock:
    now = 0.0
    def advance(self, duration):
        self.now += duration


class Tools:
    def __init__(self):
        self.calls = 0
        self.closed = False
    def run(self, action):
        self.calls += 1
        return ToolResult(ok=True, output="passed", metadata={"exit_code": 0})
    def close(self):
        self.closed = True


class Model:
    model = "fake"
    def __init__(self, responses):
        self.responses = iter(responses)
        self.seen = []
    def complete(self, messages):
        self.seen.append([m.copy() for m in messages])
        return next(self.responses)


def agent(tmp_path, model, tools=None, **kwargs):
    return CodingAgent(cwd=tmp_path, dry_run=False, max_steps=8, max_failures=3,
                       model_client=model, tools=tools or Tools(),
                       storage=AgentStorage(tmp_path / "agent.db"), stream_model=False, **kwargs)


@pytest.mark.parametrize("legacy", [None, 300, 600])
def test_healthy_active_workflow_exceeds_ten_minutes(tmp_path, monkeypatch, legacy):
    clock = Clock()
    monkeypatch.setattr("code_agent.agent.perf_counter", lambda: clock.now)
    class WorkingTools(Tools):
        def run(self, action):
            clock.advance(180)
            clock.advance(60)
            return super().run(action)
    class WorkingModel(Model):
        def complete_with_timeout(self, messages, timeout_seconds):
            assert timeout_seconds == 180
            clock.advance(50 if not self.seen else 45)
            return self.complete(messages)
    class PlanningAgent(CodingAgent):
        def _phase(self, label):
            if label == "Planning":
                clock.advance(40)
            return super()._phase(label)
    tools = WorkingTools()
    model = WorkingModel(['{"type":"read_file","path":"one.py"}',
                          '{"type":"read_file","path":"two.py"}',
                          '{"type":"final","message":"done"}'])
    runner = agent(tmp_path, model, tools, run_timeout_seconds=legacy)
    runner.__class__ = PlanningAgent
    result = runner.run_detailed("inspect the project")
    assert not result.blocked and tools.calls == 2 and tools.closed
    assert clock.now == 660
    assert all(item.get("kind") != "run_deadline" for item in result.failed_actions)


def test_legacy_environment_setting_does_not_supply_default_deadline(monkeypatch):
    monkeypatch.delenv("AGENT_RUN_TIMEOUT_SECONDS", raising=False)
    with pytest.warns(DeprecationWarning):
        assert Settings(_env_file=None).agent_run_timeout_seconds is None
    monkeypatch.setenv("AGENT_RUN_TIMEOUT_SECONDS", "300")
    with pytest.warns(DeprecationWarning):
        assert Settings(_env_file=None).agent_run_timeout_seconds == 300


def test_cancelled_late_model_cannot_mutate(tmp_path):
    tools = Tools()
    class CancellingModel(Model):
        def complete(self, messages):
            runner.cancel()
            return '{"type":"write_file","path":"unsafe.txt","content":"late"}'
    runner = agent(tmp_path, CancellingModel([]), tools)
    with pytest.raises(KeyboardInterrupt):
        runner.run_detailed("write a project file")
    assert tools.calls == 0 and tools.closed
    assert not (tmp_path / "unsafe.txt").exists()


def test_context_budget_preserves_pinned_evidence_and_recent_instruction():
    messages = [{"role":"system", "content":"instructions"},
                {"role":"user", "content":"CURRENT instruction"}]
    messages += [{"role":"user", "content":"old redundant " + "x"*1000} for _ in range(50)]
    messages += [{"role":"user", "content":"LATEST instruction"}]
    checkpoint = {"plan": [{"step":"finish parser", "status":"in_progress"}],
                  "verified": "pytest passed", "secret": SECRETS,
                  "reasoning_content": PRIVATE}
    bounded, omitted = bound_messages(messages, max_chars=8000, window_tokens=6000,
                                      output_tokens=1024, checkpoint=checkpoint)
    text = str(bounded)
    assert omitted > 0
    assert all(value in text for value in ["CURRENT instruction", "LATEST instruction", "finish parser", "pytest passed"])
    assert estimate_tokens(bounded) + 1024 <= 6000
    assert PRIVATE not in text and all(secret not in text for secret in SECRETS)


def test_oversized_pinned_context_is_explicit_and_never_sent(monkeypatch):
    client = OpenAICompatibleChatClient(api_key="fake", base_url="https://example.invalid", model="fake",
                                      context_window_tokens=2048, max_tokens=512)
    calls = []
    monkeypatch.setattr(client._client.chat.completions, "create", lambda **kwargs: calls.append(kwargs))
    with pytest.raises(ContextBudgetExceeded, match="State is preserved"):
        client.complete([{"role":"system","content":"s"}, {"role":"user","content":"x"*3000}])
    assert not calls
    client.cancel()


def test_many_turns_keep_session_and_restore_verified_checkpoint(tmp_path, monkeypatch):
    import code_agent.interactive as ui
    monkeypatch.setattr(ui, "print_work_report_panel", lambda *args: None)
    monkeypatch.setattr(ui, "print_response", lambda *args: None)
    state = SessionState(current_plan=[{"step":"finish parser", "status":"in_progress"}],
                         verified_evidence=[{"command":"pytest tests/parser", "status":"passed", "run_id":0}])
    identity = state.session_id
    transcript = []
    for turn in range(35):
        model = Model(['{"type":"final","message":"done"}'])
        runner = agent(tmp_path, model, context_max_chars=28_000, context_window_tokens=32_000)
        transcript = run_interactive_turn(f"inspect project iteration {turn} " + "a"*600, runner, transcript, state)
        transcript.append(("old redundant discussion", "z"*4000 + " " + SECRETS[0]))
        sent = str(model.seen[-1])
        assert f"iteration {turn}" in sent and "finish parser" in sent and "pytest tests/parser" in sent
        assert estimate_tokens(model.seen[-1]) + 4096 <= 32_000
        assert SECRETS[0] not in sent and PRIVATE not in sent
        assert state.session_id == identity
    restored = SessionState.restore(AgentStorage(tmp_path / "agent.db"), tmp_path)
    assert restored.session_id == identity
    assert restored.current_plan == state.current_plan
    assert restored.verified_evidence == state.verified_evidence
    assert "iteration 34" in task_with_context("fix the tests", [], restored)


def test_event_and_snapshot_privacy_preserves_replay_and_normal_data(tmp_path):
    path = tmp_path / "events.db"
    runtime = DurableExecutionRuntime(path)
    eid = runtime.engine.create("goal " + " ".join(SECRETS))
    nested = {"normal": {"enabled": True, "count": 7, "items": [None, 2.5]},
              "metadata": [{"exception": " ".join(SECRETS), "api_key":"structured-secret"}],
              "reasoning_content": PRIVATE}
    runtime.engine.dispatch(Command("AddTasks", eid, {"tasks":[{"id":"task", "title":"work", "criteria":["done"]}]}))
    state = runtime.engine.state(eid)
    events = runtime.store.append(eid, state.sequence, [("AuditMetadata", nested)], Command("AuditMetadata", eid, {}))
    assert events[0].payload["normal"] == nested["normal"]
    assert events[0].payload == sanitize_payload(nested)
    runtime.store.save_snapshot(state)
    with sqlite3.connect(path) as conn:
        rows = list(conn.execute("select payload from execution_events")) + list(conn.execute("select payload from execution_snapshots"))
    raw = str(rows)
    assert all(secret not in raw for secret in SECRETS)
    assert "structured-secret" not in raw and PRIVATE not in raw
    other = DurableExecutionRuntime(tmp_path / "ordinary.db")
    ordinary = other.create_planned("normal goal", tasks=[{"id":"a", "title":"A", "criteria":["done"]}])
    before = other.engine.state(ordinary).canonical()
    other.store.save_snapshot(other.engine.state(ordinary))
    assert DurableExecutionRuntime(tmp_path / "ordinary.db").engine.state(ordinary).canonical() == before


def test_sensitive_effect_cannot_execute_redacted_request():
    from code_agent.execution_adapters import CallableTransactionalAdapter
    from code_agent.execution_contracts import AdapterCapabilities, AdapterContext
    adapter = CallableTransactionalAdapter(AdapterCapabilities(name="test", effect_kinds=("shell",)),
                                           lambda request, context: pytest.fail("must not execute"))
    context = AdapterContext("e", "t", "1", "primary", "key", 30, ())
    with pytest.raises(ValueError, match="credential references"):
        adapter.prepare({"command":"echo " + SECRETS[0]}, context)


def test_status_protocol_and_work_report_are_sanitized(monkeypatch):
    import code_agent.status as status
    output = io.StringIO()
    monkeypatch.setattr(status, "console", Console(file=output, width=200))
    reporter = StatusReporter()
    reporter._current_label = " ".join(SECRETS)
    reporter._current_detail = " ".join(SECRETS)
    reporter._stages = [(" ".join(SECRETS), "in-progress")]
    status.console.print(reporter._render())
    reporter.action(RunShellAction(type="run_shell", command="echo " + PRIVATE + " " + SECRETS[0]))
    status.console.print(reporter._render())
    reporter.done()
    assert PRIVATE not in output.getvalue()
    assert all(secret not in output.getvalue() for secret in SECRETS)
    JsonEventEmitter(output).emit("status", detail={"nested": SECRETS, "reasoning_details": PRIVATE})
    report = build_work_report_payload(AgentRunResult(message=" ".join(SECRETS), run_id=1, task=" ".join(SECRETS)))
    assert all(secret not in output.getvalue() + str(report) for secret in SECRETS)
    assert PRIVATE not in output.getvalue()


def test_worker_error_and_cli_debug_omit_raw_body(tmp_path, monkeypatch, capsys):
    import threading
    import code_agent.cli as cli
    from code_agent.runtime_migration import ExecutionPlane
    runtime = DurableExecutionRuntime(tmp_path / "worker.db")
    eid = runtime.engine.create("goal")
    plane = ExecutionPlane.__new__(ExecutionPlane)
    plane._lock, plane._threads = threading.Lock(), {}
    plane._results, plane.runtime = {eid: RuntimeError(PRIVATE + " ".join(SECRETS))}, runtime
    assert PRIVATE not in plane.status(eid)["error"]
    def fail():
        raise RuntimeError(PRIVATE + " ".join(SECRETS))
    monkeypatch.setattr(cli, "app", fail)
    monkeypatch.setenv("AGENT47_DEBUG", "1")
    with pytest.raises(SystemExit):
        cli.main()
    text = capsys.readouterr().err
    assert PRIVATE not in text and all(secret not in text for secret in SECRETS)


def test_sdk_budget_keeps_agent_checkpoint(monkeypatch):
    checkpoint = {"role":"user", "content":"Deterministic execution-history checkpoint. current plan and verified evidence"}
    sent = []
    client = OpenAICompatibleChatClient(api_key="fake", base_url="https://example.invalid", model="fake")
    def create(**kwargs):
        sent.extend(kwargs["messages"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="done"))], usage=None)
    monkeypatch.setattr(client._client.chat.completions, "create", create)
    client.complete([{"role":"system","content":"system"}, {"role":"user","content":"task"}, checkpoint])
    assert checkpoint in sent
    client.cancel()


def test_checkpoint_restore_is_not_limited_to_recent_run_list(tmp_path):
    storage = AgentStorage(tmp_path / "agent.db")
    state = SessionState(current_task="maintain project")
    state.last_run_id = storage.create_run("goal", "fake", tmp_path)
    state.save(storage)
    with storage._connect() as conn:
        conn.executemany("insert into runs (task, model, cwd) values (?,?,?)",
                         [("interrupted before checkpoint", "fake", str(tmp_path))] * 105)
    assert SessionState.restore(storage, tmp_path).session_id == state.session_id
    assert SessionState.restore(storage, tmp_path / "other").session_id != state.session_id


def test_cancel_after_tool_prevents_success_and_retention(tmp_path):
    class CancellingTools(Tools):
        def run(self, action):
            runner.cancel()
            return super().run(action)
    tools = CancellingTools()
    runner = agent(tmp_path, Model(['{"type":"read_file","path":"a.py"}',
                                    '{"type":"final","message":"must not run"}']), tools)
    with pytest.raises(KeyboardInterrupt):
        runner.run_detailed("inspect project")
    assert tools.calls == 1 and tools.closed
    assert len(runner.model_client.seen) == 1


def test_unicode_context_reserves_output_and_reports_pressure():
    from code_agent.interactive import friendly_model_error
    messages = [{"role":"system","content":"rules"}, {"role":"user","content":"current"}]
    messages += [{"role":"user","content":"\u754c"*1000} for _ in range(10)]
    bounded, omitted = bound_messages(messages, max_chars=8000, window_tokens=5000, output_tokens=1000)
    assert omitted and estimate_tokens(bounded) + 1000 <= 5000
    error = ContextBudgetExceeded("Current task exceeds context budget; state is preserved.")
    assert "state is preserved" in friendly_model_error(error)


def test_unlimited_healthy_actions_continue_for_two_mocked_hours(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr("code_agent.agent.perf_counter", lambda: clock.now)

    class LongTools(Tools):
        def run(self, action):
            clock.advance(61)
            self.calls += 1
            return ToolResult(ok=True, output=f"new evidence for {action.path}", metadata={})

    class LongModel(Model):
        def complete_with_timeout(self, messages, timeout_seconds):
            assert timeout_seconds == 180
            clock.advance(1)
            return self.complete(messages)

    actions = [f'{{"type":"read_file","path":"file_{i}.py"}}' for i in range(121)]
    model = LongModel(actions + ['{"type":"final","message":"complete"}'])
    tools = LongTools()
    runner = agent(tmp_path, model, tools)
    runner.max_steps = None
    result = runner.run_detailed("inspect the project and report findings")

    assert not result.blocked
    assert tools.calls == 121, (result.message, result.failed_actions[-3:], result.context_records[-3:], len(model.seen))
    assert clock.now >= 7200
    assert result.execution_state["max_steps"] is None
    assert tools.closed


def test_default_model_turn_accepts_150_seconds_and_rejects_181(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr("code_agent.agent.perf_counter", lambda: clock.now)

    class SlowModel(Model):
        def __init__(self, duration):
            super().__init__(['{"type":"final","message":"complete"}'])
            self.duration = duration

        def complete_with_timeout(self, messages, timeout_seconds):
            assert timeout_seconds == 180
            clock.advance(self.duration)
            return self.complete(messages)

    assert not agent(tmp_path, SlowModel(150)).run_detailed("inspect project").blocked
    blocked = agent(tmp_path, SlowModel(181)).run_detailed("inspect project").blocked
    assert blocked


def test_100_turn_checkpoint_compaction_and_restart(tmp_path):
    storage = AgentStorage(tmp_path / "agent.db")
    state = SessionState()
    transcript = []
    compactions = 0
    (tmp_path / "migration.py").write_text("SCHEMA_VERSION = 2\n", encoding="utf-8")
    for turn in range(100):
        user = (
            "Build a production issue tracker" if turn == 0
            else f"Continue project turn {turn} " + ("old discussion " * 100)
        )
        if turn == 10:
            state.record_decision("SQLite is the source of truth for project records")
        if turn == 25:
            state.record_failed_approach("Manual schema patch failed; use migration.py")
        if turn == 70:
            user = "Correction: keep SQLite and preserve existing account IDs"
        run_id = storage.create_run(user, "fake", tmp_path)
        result = AgentRunResult(
            message="done", run_id=run_id, task=user,
            blocked=turn == 99,
            execution_state={
                "plan_steps": [{"step": "finish migration", "status": "in_progress"}],
                "verification_records": [{"command": "pytest tests/migration", "status": "passed"}],
            },
        )
        state.update(user, result)
        if turn == 99:
            state.last_blocker = "migration test needs fresh repository verification"
        state.save(storage)
        transcript.append(("old " + "x" * 800, "history " + "y" * 800 + " " + SECRETS[0]))
        messages = [
            {"role": "system", "content": "Use current repository files as truth"},
            {"role": "user", "content": user},
        ] + [{"role": "user", "content": str(pair)} for pair in transcript]
        bounded, omitted = bound_messages(
            messages, window_tokens=16_384, output_tokens=1024,
            checkpoint={"session": state.render()},
        )
        compactions += int(omitted > 0)
        assert estimate_tokens(bounded) + 1024 <= 16_384
    restored = SessionState.restore(AgentStorage(tmp_path / "agent.db"), tmp_path)
    prompt = task_with_context("Verify migration.py against repository", [], restored)
    assert compactions > 50
    assert restored.persistent_goal == "Build a production issue tracker"
    assert "keep SQLite" in prompt
    assert "finish migration" in prompt
    assert "needs fresh repository verification" in prompt
    assert "SQLite is the source of truth" in prompt
    assert "Manual schema patch failed" in prompt
    assert (tmp_path / "migration.py").read_text(encoding="utf-8") == "SCHEMA_VERSION = 2\n"
    assert SECRETS[0] not in str(bounded) + prompt
    assert PRIVATE not in str(bounded) + prompt
