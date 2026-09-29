from __future__ import annotations

import io
import subprocess

from rich.console import Console

from code_agent.agent import CodingAgent
from code_agent.execution_state import ExecutionState
from code_agent.interactive import ActiveShortcutMonitor
from code_agent.safety import redact_command_for_display
from code_agent.schema import PlanStep, ReadFileAction, RunShellAction, ToolResult, UpdatePlanAction
from code_agent.status import StatusReporter
from code_agent.storage import AgentStorage
from code_agent.tools import ToolRegistry


class FakeLive:
    def __init__(self, *args, **kwargs):
        self.stopped = False

    def start(self):
        pass

    def update(self, renderable):
        self.renderable = renderable

    def stop(self):
        self.stopped = True


def _reporter(monkeypatch, *, width=80):
    import code_agent.status as status

    output = io.StringIO()
    monkeypatch.setattr(status, "console", Console(file=output, width=width, force_terminal=False))
    monkeypatch.setattr(status, "Live", FakeLive)
    return StatusReporter(), output


def test_failed_strategy_checkpoint_blocks_replay_and_allows_alternative(tmp_path):
    action_a = RunShellAction(type="run_shell", command="uv run pytest tests/a -q")
    action_b = RunShellAction(type="run_shell", command="uv run pytest tests/b -q")
    failure = ToolResult(ok=False, output="1 failed", metadata={"exit_code": 1})
    state = ExecutionState(task="fix project", max_steps=None)
    state.record_action(action_a, failure, [])
    state.failure_recovery_instruction(action_a, failure)
    state.record_action(action_a, failure, [])
    state.failure_recovery_instruction(action_a, failure)
    assert state.replan_required
    assert state.failed_approaches[0]["attempts"] == 2
    assert state.failed_approaches[0]["status"] == "failed"
    snapshot = state.snapshot()
    assert "1 failed" not in str(snapshot["failed_approaches"])

    restored = ExecutionState.from_snapshot(snapshot, task="fix project", max_steps=None)
    restored.update_plan(UpdatePlanAction(
        type="update_plan", steps=[PlanStep(step="Try another check", status="in_progress")],
    ))
    assert "already failed repeatedly" in restored.failed_strategy_blocker(action_a)
    assert restored.failed_strategy_blocker(action_b) is None
    restored.record_action(action_b, ToolResult(ok=True, output="passed"), [])
    assert restored.failed_approaches[0]["status"] == "superseded"
    assert restored.failed_approaches[0]["superseded_by_strategy"] == restored.strategy_fingerprint(action_b)


def test_failed_strategy_survives_resume_and_never_executes_again(tmp_path):
    action = RunShellAction(type="run_shell", command="uv run pytest tests/a -q")
    state = ExecutionState(task="fix project", max_steps=None)
    for _ in range(2):
        failure = ToolResult(ok=False, output="1 failed", metadata={"exit_code": 1})
        state.record_action(action, failure, [])
        state.failure_recovery_instruction(action, failure)

    class Model:
        model = "fake"
        responses = iter([
            '{"type":"update_plan","steps":[{"step":"try another strategy","status":"in_progress"}]}',
            '{"type":"run_shell","command":"uv run pytest tests/a -q"}',
            '{"type":"run_shell","command":"uv run pytest tests/b -q"}',
            '{"type":"final","message":"done"}',
        ])

        def complete(self, messages):
            return next(self.responses)

    class Tools:
        calls = []

        def run(self, proposed):
            self.calls.append(proposed.command)
            return ToolResult(ok=True, output="passed")

        def close(self):
            pass

    tools = Tools()
    agent = CodingAgent(
        cwd=tmp_path, dry_run=False, max_steps=None, max_failures=4,
        model_client=Model(), tools=tools, storage=AgentStorage(tmp_path / "agent.db"),
        stream_model=False, execution_state_snapshot=state.snapshot(),
    )
    result = agent.run_detailed("fix this project")
    assert result.verification_results and result.verification_results[-1]["status"] == "passed"
    assert tools.calls == ["uv run pytest tests/b -q"]
    assert any(item.get("type") == "failed_strategy" for item in result.failed_actions)


def test_failed_approach_history_is_bounded_and_does_not_store_secret():
    state = ExecutionState(task="fix project", max_steps=None)
    for index in range(20):
        action = RunShellAction(
            type="run_shell", command=f"uv run pytest tests/{index} --api-key sk-test-secret-value",
        )
        state.record_action(action, ToolResult(ok=False, output="password=hunter2"), [])
    assert len(state.failed_approaches) == 16
    text = str(state.snapshot()["failed_approaches"])
    assert "sk-test-secret-value" not in text
    assert "hunter2" not in text


def test_display_copy_redacts_command_and_real_execution_receives_original(tmp_path, monkeypatch):
    command = "uv run pytest --api-key sk-test-secret-value --password hunter2"
    assert "sk-test-secret-value" not in redact_command_for_display(command)
    assert "hunter2" not in redact_command_for_display(command)
    calls = []
    approvals = []
    tools = ToolRegistry(
        workspace=tmp_path, dry_run=False,
        approval_callback=lambda _action, detail: approvals.append(detail) or True,
    )

    def run_shell_process(actual, **kwargs):
        calls.append(actual)
        return subprocess.CompletedProcess(actual, 0, "1 passed", ""), False, ""

    monkeypatch.setattr(tools, "_run_shell_process", run_shell_process)
    result = tools.run(RunShellAction(type="run_shell", command=command))
    assert result.ok
    assert calls == [command]
    assert approvals and "sk-test-secret-value" not in approvals[0]
    assert "hunter2" not in approvals[0]
    assert "[REDACTED]" in approvals[0]


def test_non_tty_shows_sanitized_command_before_execution_and_summary(monkeypatch):
    reporter, output = _reporter(monkeypatch)
    command = 'curl -H "Authorization: Bearer sk-test-secret-value" --password hunter2'
    action = RunShellAction(type="run_shell", command=command)
    reporter.action(action)
    before = output.getvalue()
    assert "RUN:" in before and "[REDACTED]" in before
    assert "sk-test-secret-value" not in before and "hunter2" not in before
    reporter.tool_result(action, ToolResult(ok=False, output="1 failed, 14 passed\nhunter2\nAPI_KEY=supersecret"), 3820)
    after = output.getvalue()
    assert "FAIL:" in after and "1 failed, 14 passed" in after
    assert "supersecret" not in after and "hunter2" not in after
    assert reporter.command_history()[-1].command != command
    reporter.done()


def test_tty_collapse_expand_status_and_cancellation(monkeypatch):
    reporter, output = _reporter(monkeypatch, width=80)
    reporter._interactive = True
    reporter.thinking(18)
    reporter.context_status(42, 100)
    action = RunShellAction(type="run_shell", command="uv run pytest --token secretvalue")
    reporter.action(action)
    assert "[REDACTED]" in reporter.command_history()[-1].command
    assert "secretvalue" not in reporter.command_history()[-1].command
    reporter.tool_result(action, ToolResult(ok=False, output="API_KEY=supersecret\nreasoning_content=private-marker\n2 failed, 1262 passed"), 1230)
    collapsed = "\\n".join(line.plain for line in reporter._command_lines())
    assert "supersecret" not in collapsed
    assert reporter.toggle_command_output() is True
    expanded = "\\n".join(line.plain for line in reporter._command_lines())
    assert "[REDACTED]" in expanded and "supersecret" not in expanded
    assert "private-marker" not in expanded
    assert reporter.toggle_command_output() is False
    reporter.phase("Replanning")
    assert "REPLANNING" in reporter._bottom_status().plain
    assert "ctx 42%" in reporter._bottom_status().plain
    reporter.cancelled()
    assert "CANCELLED" in reporter._bottom_status().plain
    reporter.done()
    assert reporter._live.stopped
    assert "secretvalue" not in output.getvalue()


def test_narrow_status_prioritizes_phase_and_elapsed(monkeypatch):
    reporter, _ = _reporter(monkeypatch, width=22)
    reporter._interactive = True
    reporter.thinking(18)
    reporter.context_status(82, 100)
    bar = reporter._bottom_status().plain
    assert bar.startswith("THINKING")
    assert len(bar) <= 20
    assert "ctx" not in bar
    reporter.done()


def test_toggle_key_uses_existing_shortcut_monitor(monkeypatch):
    reporter, _ = _reporter(monkeypatch)
    reporter._interactive = True
    agent = type("Agent", (), {"cancel": lambda self, reason: 1})()
    monitor = ActiveShortcutMonitor(agent, reporter)
    reporter.action(ReadFileAction(type="read_file", path="README.md"))
    assert monitor._handle_key("o") is False
    assert reporter.toggle_command_output() is False
    reporter.done()

def test_display_redacts_unlabelled_opaque_argument():
    command = "uv run pytest private-provider-prompt-response-marker"
    assert "private-provider-prompt-response-marker" not in redact_command_for_display(command)
    assert "uv run pytest" in redact_command_for_display(command)

def test_failed_strategy_is_pinned_through_context_compaction():
    from code_agent.context_budget import bound_messages

    state = ExecutionState(task="fix project", max_steps=None)
    action = RunShellAction(type="run_shell", command="echo private-short-secret")
    failure = ToolResult(ok=False, output="private-short-secret")
    state.record_action(action, failure, [])
    state.record_action(action, failure, [])
    checkpoint = {"failed_approaches": state.snapshot()["failed_approaches"]}
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "fix project"},
    ] + [{"role": "assistant", "content": f"old output {index} " + "x" * 1200} for index in range(30)]
    compacted, omitted = bound_messages(messages, window_tokens=8000, output_tokens=500, checkpoint=checkpoint)
    assert omitted > 0
    assert state.failed_approaches[0]["strategy_fingerprint"] in compacted[2]["content"]
    assert "private-short-secret" not in compacted[2]["content"]
    state.failure_recovery_instruction(action, failure)
    assert "private-short-secret" not in state.replan_reason
    assert "private-short-secret" not in str(state.snapshot())


def test_quoted_and_short_flag_command_values_are_redacted():
    command = 'curl -H "X-Private: hunter2" -u user:shortpass --data "password=hunter2"'
    display = redact_command_for_display(command)
    assert "hunter2" not in display
    assert "shortpass" not in display
    assert "curl" in display


def test_echo_output_is_hidden_even_when_secret_is_unlabelled(monkeypatch):
    reporter, output = _reporter(monkeypatch)
    action = RunShellAction(type="run_shell", command="echo private-short-secret")
    reporter.action(action)
    reporter.tool_result(action, ToolResult(ok=True, output="private-short-secret"), 10)
    assert "private-short-secret" not in output.getvalue()
    assert "private-short-secret" not in reporter.command_history()[-1].output
    reporter.done()