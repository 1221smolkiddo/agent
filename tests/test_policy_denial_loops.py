from __future__ import annotations

import json
import subprocess
from unittest.mock import Mock

import pytest
from rich.console import Console

from code_agent.agent import CodingAgent
from code_agent.execution_state import ExecutionState
from code_agent.failure_types import RunDisposition
from code_agent.permissions import ApprovalMode, PermissionPolicy
from code_agent.safety import classify_shell_command
from code_agent.sandbox_security import SandboxPolicy, CommandPolicy
from code_agent.schema import RunShellAction, StartProcessAction
from code_agent.status import StatusReporter
from code_agent.storage import AgentStorage
from code_agent.tools import ToolRegistry
from test_agent_recovery import FakeModel


def shell(command):
    return RunShellAction(type="run_shell", command=command)


def agent_for(tmp_path, model, tools, snapshot=None):
    return CodingAgent(cwd=tmp_path, dry_run=False, max_steps=None, max_failures=1,
                       model_client=model, tools=tools, stream_model=False,
                       storage=AgentStorage(tmp_path / "agent.db"),
                       execution_state_snapshot=snapshot,
                       resumed_from_run_id=1 if snapshot else None)


@pytest.mark.parametrize("executable", ["python", "py", "python3"])
@pytest.mark.parametrize("port", ["", " 8000"])
def test_http_server_is_managed(tmp_path, monkeypatch, executable, port):
    command = f"{executable} -m http.server{port}"
    assert classify_shell_command(command).category == "development"
    approvals = Mock(return_value=True)
    tools = ToolRegistry(tmp_path, False, approval_callback=approvals)
    start = Mock(return_value={"process_id": "test-process", "pid": 12, "status": "running"})
    monkeypatch.setattr(tools.process_supervisor, "start_managed", start)
    monkeypatch.setattr(tools, "_run_shell_process", Mock(side_effect=AssertionError("unmanaged execution")))
    result = tools.run(shell(command))
    assert result.ok and result.metadata["process"]["process_id"] == "test-process"
    assert start.call_args.args == (command,)
    assert start.call_args.kwargs["cwd"] == tmp_path
    assert approvals.call_args.args[0] == "start_process"
    assert command in approvals.call_args.args[1]
    tools.close()


@pytest.mark.parametrize("mode", list(ApprovalMode))
def test_unknown_interactive_requires_explicit_approval(tmp_path, monkeypatch, mode):
    prompt = Mock(return_value="y")
    policy = PermissionPolicy(prompt, mode)
    tools = ToolRegistry(tmp_path, False, approval_callback=policy.approve, interactive_shell=True)
    runner = Mock(return_value=(subprocess.CompletedProcess([], 0, "ok", ""), False, "", False, False, {}))
    monkeypatch.setattr(tools, "_run_shell_process", runner)
    result = tools.run(shell("project-check --quick"))
    assert result.ok
    assert prompt.call_count == runner.call_count == 1
    assert "Command: project-check --quick" in prompt.call_args.args[1]
    tools.close()


def test_unknown_denied_prompt_once(tmp_path, monkeypatch):
    prompt = Mock(return_value=False)
    tools = ToolRegistry(tmp_path, False, approval_callback=prompt, interactive_shell=True)
    runner = Mock(side_effect=AssertionError("denied command executed"))
    monkeypatch.setattr(tools, "_run_shell_process", runner)
    first = tools.run(shell("project-check --quick"))
    second = tools.run(shell("project-check   --quick"))
    assert not first.ok and second.metadata["policy_denied"]
    assert prompt.call_count == 1 and runner.call_count == 0
    tools.close()


@pytest.mark.parametrize("command", [
    "project-check --quick", "py -c print(1)", "python -c print(1)",
    "rm -rf .", "project-check ../outside", "project-check --path=../outside",
    "project-check .env", "project-check --token abc", "project-check $HOME",
    "bash script.sh", "project-check; echo done", "project-check --path=/etc/passwd",
    'python3.14 "-c" "print(1)"', './python "-c" "print(1)"',
    'uv run "python" "-c" "print(1)"', "project-check .ssh/id_rsa",
])
def test_hard_or_unattended_unknown_blocks_without_prompt(tmp_path, command):
    prompt = Mock(return_value=True)
    tools = ToolRegistry(tmp_path, False, approval_callback=prompt,
                         interactive_shell=command != "project-check --quick")
    result = tools.run(shell(command))
    assert not result.ok and result.metadata["policy_denied"]
    prompt.assert_not_called()
    tools.close()


def test_sandbox_allowlist_still_blocks_unknown(tmp_path):
    policy = SandboxPolicy(commands=CommandPolicy(allow=("pytest",)))
    prompt = Mock(return_value=True)
    tools = ToolRegistry(tmp_path, False, approval_callback=prompt,
                         interactive_shell=True, sandbox_policy=policy)
    assert tools.run(shell("project-check --quick")).metadata["policy_denied"]
    prompt.assert_not_called()
    tools.close()


@pytest.mark.parametrize("commands", [
    [shell("project-check --quick"), shell("project-check   --quick")],
    [shell("python -m http.server 8000"), StartProcessAction(type="start_process", command="py -m http.server 9000")],
    [shell("rm -rf ."), shell("rm -rf .")],
])
def test_policy_denial_pauses_after_two_model_calls(tmp_path, commands):
    prompt = Mock(return_value=False)
    tools = ToolRegistry(tmp_path, False, approval_callback=lambda action, detail: prompt(action, detail) if action in {"run_shell", "start_process"} else True, interactive_shell=True)
    model = FakeModel([a.model_dump_json() for a in commands] * 100)
    result = agent_for(tmp_path, model, tools).run_detailed("Start the development server in this repository")
    assert result.disposition == RunDisposition.WAITING and result.blocked
    assert len(model.messages_seen) == 2
    assert sum(d["count"] for d in result.execution_state["policy_denials"].values()) == 2
    assert result.execution_state["recovery_counts"].get("executable", 0) == 0
    assert result.execution_state["recovery_attempt_count"] == 0
    assert result.execution_state["failure_fingerprints"] == []
    assert "materially different strategy" in json.dumps(model.messages_seen[1])
    assert "Blocked command:" in result.message and "Progress is saved" in result.message
    assert prompt.call_count <= 1
    restored = ExecutionState.from_snapshot(result.execution_state, task="preview", max_steps=None)
    assert restored.denied_command(commands[1])["count"] == 2


def test_interactive_resume_can_reapprove(tmp_path, monkeypatch):
    tools = ToolRegistry(tmp_path, False, approval_callback=lambda *_: False, interactive_shell=True)
    action = shell("project-check --quick")
    result = agent_for(tmp_path, FakeModel([action.model_dump_json()] * 3), tools).run_detailed("Run the project check")
    prompt = Mock(return_value=True)
    resumed_tools = ToolRegistry(tmp_path, False, approval_callback=lambda action, detail: prompt(action, detail) if action == "run_shell" else True, interactive_shell=True)
    runner = Mock(return_value=(subprocess.CompletedProcess([], 0, "ok", ""), False, "", False, False, {}))
    monkeypatch.setattr(resumed_tools, "_run_shell_process", runner)
    resumed = agent_for(tmp_path, FakeModel([action.model_dump_json(), '{"type":"final","message":"Check completed."}']),
                        resumed_tools, result.execution_state).run_detailed("Run the project check")
    assert resumed.disposition == RunDisposition.SUCCESS
    assert prompt.call_count == runner.call_count == 1


def test_ctrl_c_during_model_saves_resumable_state(tmp_path):
    tools = ToolRegistry(tmp_path, False, approval_callback=lambda *_: True)
    model = FakeModel([])
    model.cancel = Mock(return_value=1)
    model.complete = Mock(side_effect=KeyboardInterrupt)
    cleanup = Mock(return_value=0)
    tools.cancel_running_processes = cleanup
    result = agent_for(tmp_path, model, tools).run_detailed("Inspect the project")
    assert result.disposition == RunDisposition.WAITING
    assert result.execution_state["phase"] == "waiting"
    assert model.complete.call_count == model.cancel.call_count == cleanup.call_count == 1
    assert "Progress is saved" in result.message


def test_cancellation_during_approval_not_reset(tmp_path, monkeypatch):
    tools = ToolRegistry(tmp_path, False, interactive_shell=True)
    def approve(*_):
        tools.cancellation_token.cancel("interactive ctrl+c")
        return True
    tools.approval_callback = approve
    def runner(*_, **__):
        assert tools.cancellation_token.cancelled
        return subprocess.CompletedProcess([], 1, "", ""), False, "", True, True, {}
    monkeypatch.setattr(tools, "_run_shell_process", runner)
    result = tools.run(shell("project-check --quick"))
    assert result.metadata["cancelled"]
    tools.close()


def test_policy_terminal_shows_sanitized_command(tmp_path, monkeypatch):
    import io
    import code_agent.status as status
    buffer = io.StringIO()
    monkeypatch.setattr(status, "console", Console(file=buffer, width=120))
    tools = ToolRegistry(tmp_path, False)
    action = shell("project-check --token sk-abcdefghijklmnopqrst")
    result = tools.run(action)
    StatusReporter().tool_result(action, result, 0)
    output = buffer.getvalue()
    assert "Blocked command:" in output and "Reason:" in output
    assert "sk-abcdefghijklmnopqrst" not in output
    assert "run_shell" not in output and "fingerprint" not in output
    tools.close()


def test_plan_rewording_does_not_reset_policy_denial(tmp_path):
    tools = ToolRegistry(tmp_path, False, approval_callback=lambda *_: True)
    action = shell("project-check --quick")
    plan = json.dumps({"type": "update_plan", "steps": [{"title": "Try command again", "status": "in_progress"}]})
    model = FakeModel([action.model_dump_json(), plan, action.model_dump_json()] * 100)
    result = agent_for(tmp_path, model, tools).run_detailed("Inspect this repository")
    assert result.disposition == RunDisposition.WAITING
    assert len(model.messages_seen) == 3
    assert list(result.execution_state["policy_denials"].values())[0]["count"] == 2
    assert not result.execution_state["failed_approaches"]


def test_unattended_resume_cannot_reset_denial(tmp_path):
    action = shell("project-check --quick")
    first = agent_for(tmp_path, FakeModel([action.model_dump_json()] * 3),
                      ToolRegistry(tmp_path, False)).run_detailed("Inspect this repository")
    model = FakeModel([action.model_dump_json()] * 100)
    prompt = Mock(return_value=True)
    resumed = agent_for(tmp_path, model, ToolRegistry(tmp_path, False, approval_callback=prompt),
                        first.execution_state).run_detailed("Inspect this repository")
    assert resumed.disposition == RunDisposition.WAITING and len(model.messages_seen) == 1
    assert not any(call.args[0] == "run_shell" for call in prompt.call_args_list)


def test_changed_effective_policy_requires_fresh_approval(tmp_path, monkeypatch):
    action = shell("project-check --quick")
    denied_policy = SandboxPolicy(commands=CommandPolicy(deny=("project-check",)))
    first = agent_for(tmp_path, FakeModel([action.model_dump_json()] * 3),
                      ToolRegistry(tmp_path, False, sandbox_policy=denied_policy,
                                   interactive_shell=True)).run_detailed("Inspect this repository")
    tools = ToolRegistry(tmp_path, False, approval_callback=lambda *_: True, interactive_shell=True)
    runner = Mock(return_value=(subprocess.CompletedProcess([], 0, "ok", ""), False, "", False, False, {}))
    monkeypatch.setattr(tools, "_run_shell_process", runner)
    resumed = agent_for(tmp_path, FakeModel([action.model_dump_json(), '{"type":"final","message":"Checked."}']),
                        tools, first.execution_state).run_detailed("Inspect this repository")
    assert resumed.disposition == RunDisposition.SUCCESS and runner.call_count == 1
    assert not resumed.execution_state["policy_denials"]


def test_initial_planning_interrupt_saves_task(tmp_path):
    runner = agent_for(tmp_path, FakeModel([]), ToolRegistry(tmp_path, False))
    runner._phase = Mock(side_effect=KeyboardInterrupt)
    result = runner.run_detailed("Inspect this repository")
    assert result.disposition == RunDisposition.WAITING
    assert runner.storage.get_run(result.run_id) is not None
    assert result.execution_state["step"] == 0


def test_managed_start_cancellation_cleans_up(tmp_path, monkeypatch):
    tools = ToolRegistry(tmp_path, False, approval_callback=lambda *_: True)
    def start(*_, **__):
        tools.cancellation_token.cancel("interactive ctrl+c")
        return {"process_id": "child", "pid": 1, "status": "running"}
    monkeypatch.setattr(tools.process_supervisor, "start_managed", start)
    cleanup = Mock(return_value=1)
    monkeypatch.setattr(tools.process_supervisor, "cancel_all", cleanup)
    with pytest.raises(KeyboardInterrupt):
        tools.run(shell("python -m http.server 8000"))
    cleanup.assert_called_once_with("interactive ctrl+c")
    tools.close()


def test_unknown_managed_command_requires_approval(tmp_path, monkeypatch):
    prompt = Mock(return_value=True)
    tools = ToolRegistry(tmp_path, False, approval_callback=prompt, interactive_shell=True)
    start = Mock(return_value={"process_id": "child", "pid": 1, "status": "running"})
    monkeypatch.setattr(tools.process_supervisor, "start_managed", start)
    assert tools.run(StartProcessAction(type="start_process", command="project-watch --local")).ok
    assert prompt.call_count == start.call_count == 1
    tools.close()


def test_approval_renders_entire_sanitized_command(monkeypatch):
    import io
    import code_agent.permissions as permissions
    command = "project-check " + " ".join(f"file-{i:03d}.py" for i in range(100)) + " --token sk-abcdefghijklmnopqrst"
    buffer = io.StringIO()
    monkeypatch.setattr(permissions, "console", Console(file=buffer, width=120))
    monkeypatch.setattr(permissions.Prompt, "ask", lambda *_a, **_k: "n")
    permissions.confirm_permission("run_shell", "Risk: high\nCategory: unknown\nCommand: " + command)
    output = buffer.getvalue()
    assert "Command:" in output
    assert "file-000.py" in output and "file-099.py" in output
    assert "sk-abcdefghijklmnopqrst" not in output


def test_interactive_ctrl_c_returns_saved_turn(monkeypatch, tmp_path):
    from code_agent.interactive import run_interactive_turn, ActiveShortcutMonitor
    from code_agent.session import SessionState
    tools = ToolRegistry(tmp_path, False)
    model = FakeModel([])
    runner = agent_for(tmp_path, model, tools)
    def complete(_messages):
        ActiveShortcutMonitor(runner)._handle_sigint(None, None)
    model.complete = complete
    transcript = run_interactive_turn("Inspect this repository", runner, [], SessionState())
    assert len(transcript) == 1
    assert runner._active_execution_state.disposition == RunDisposition.WAITING
    assert runner.storage.get_run(runner._active_run_id) is not None


def test_different_denials_and_plan_rewrites_are_bounded(tmp_path):
    from code_agent.agent import MAX_POLICY_DENIALS_PER_RUN
    responses = []
    for index in range(100):
        responses.append(shell(f"unclassified-command-{index} --quick").model_dump_json())
        responses.append(json.dumps({"type": "update_plan", "steps": [{"title": "Try another strategy", "status": "in_progress"}]}))
    model = FakeModel(responses)
    result = agent_for(tmp_path, model, ToolRegistry(tmp_path, False)).run_detailed("Inspect this repository")
    assert result.disposition == RunDisposition.WAITING
    assert len(model.messages_seen) == 2 * MAX_POLICY_DENIALS_PER_RUN - 1
    assert len(result.execution_state["policy_denials"]) == MAX_POLICY_DENIALS_PER_RUN
    assert result.execution_state["recovery_counts"].get("executable", 0) == 0


def test_windows_cleanup_tolerates_raced_invalid_pid(monkeypatch):
    import code_agent.processes as processes
    def inspect_handle(handle, **_):
        monkeypatch.setattr(processes.os, "kill", Mock(side_effect=SystemError("invalid pid")))
        assert handle.wait(timeout=0.1) is None
    monkeypatch.setattr(processes, "terminate_process_tree", inspect_handle)
    processes.terminate_pid_tree(424242, grace_seconds=0.1)


def test_managed_working_directory_escape_is_policy_denial(tmp_path):
    tools = ToolRegistry(tmp_path, False, approval_callback=lambda *_: True,
                         interactive_shell=True)
    action = StartProcessAction(type="start_process", command="python -m http.server 8000",
                                working_directory="../outside")
    result = tools.run(action)
    assert not result.ok and result.metadata["policy_class"] == "workspace-escape"
    assert "Blocked command:" in result.output
    tools.close()


def test_ctrl_c_just_before_run_saves_resumable_state(tmp_path):
    runner = agent_for(tmp_path, FakeModel([]), ToolRegistry(tmp_path, False))
    runner.cancel("interactive ctrl+c")
    result = runner.run_detailed("Inspect this repository")
    assert result.disposition == RunDisposition.WAITING
    assert result.execution_state["phase"] == "waiting"
    assert runner.storage.get_run(result.run_id) is not None


def test_unknown_does_not_override_explicit_network_deny(tmp_path):
    prompt = Mock(return_value=True)
    tools = ToolRegistry(tmp_path, False, approval_callback=prompt,
                         interactive_shell=True, shell_network_policy="deny")
    result = tools.run(shell("project-check --quick"))
    assert not result.ok and result.metadata["policy_denied"]
    assert result.metadata["policy_reason"] == "Shell network access is denied for this run."
    prompt.assert_not_called()
    tools.close()
