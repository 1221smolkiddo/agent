from __future__ import annotations

import json
import os

import pytest

from code_agent.completion_evidence import substantive_content
from code_agent.failure_types import RunDisposition
from code_agent.prompts import system_prompt
from code_agent.schema import MakeDirectoryAction, WriteFileAction
from code_agent.terminal_ui import normal_result_message
from code_agent.tools import ToolRegistry
from test_agent_recovery import FakeModel, make_real_tool_agent


def action(kind, **kwargs):
    return json.dumps({"type": kind, **kwargs})


def configured_agent(tmp_path, responses, monkeypatch):
    model = FakeModel(responses)
    agent = make_real_tool_agent(tmp_path, model)
    agent.max_steps = len(responses)
    # Test explicit verification ordering; unrelated auto-detected commands are covered separately.
    monkeypatch.setattr(agent, "_run_automatic_verification", lambda **kwargs: [])
    return agent, model


@pytest.mark.parametrize("message", ["Would you like me to proceed?", "I'll start by initializing.",
                                     "I can create the app.", "We can begin."])
def test_future_promise_cannot_finalize_mutation(tmp_path, monkeypatch, message):
    agent, _ = configured_agent(tmp_path, [action("final", message=message)], monkeypatch)
    result = agent.run_detailed("Build a small web app")
    assert result.blocked
    assert result.disposition == RunDisposition.WAITING
    assert any(r.get("type") == "false_completion" for r in result.failed_actions)


@pytest.mark.parametrize("content,path", [("", ".gitkeep"), ("// TODO\n", "app.js"),
                                          ("# placeholder\n", "app.py"), ("{}", "package.json")])
def test_placeholder_is_not_implementation(tmp_path, monkeypatch, content, path):
    agent, _ = configured_agent(tmp_path, [action("write_file", path=path, content=content),
                                         action("final", message="Done")], monkeypatch)
    result = agent.run_detailed("Build a small web app")
    assert result.blocked
    assert not substantive_content(path, content)


def test_changed_source_without_verification_cannot_finish(tmp_path, monkeypatch):
    agent, _ = configured_agent(tmp_path, [action("write_file", path="app.py", content="print('hello')\n"),
                                         action("final", message="Done")], monkeypatch)
    result = agent.run_detailed("Implement a small app")
    assert result.blocked
    assert "verification" in result.failed_actions[-1]["output"]


@pytest.mark.parametrize("kind,path,content", [("write_file", "empty.txt", ""),
                                             ("make_directory", "empty-folder", None)])
def test_explicit_empty_artifact_request_is_allowed(tmp_path, monkeypatch, kind, path, content):
    args = {"path": path}
    if content is not None:
        args["content"] = content
    task = "Create an empty file empty.txt" if kind == "write_file" else "Create an empty directory empty-folder"
    agent, _ = configured_agent(tmp_path, [action(kind, **args), action("final", message="Created it")], monkeypatch)
    result = agent.run_detailed(task)
    assert not result.blocked
    assert (tmp_path / path).exists()


def test_genuine_required_input_waits_resumably(tmp_path, monkeypatch):
    message = "Required private schema is missing. Please provide the private schema to implement the integration."
    agent, _ = configured_agent(tmp_path, [action("final", message=message)], monkeypatch)
    result = agent.run_detailed("Implement integration using my private schema")
    assert result.disposition == RunDisposition.WAITING
    assert result.execution_state["phase"] == "waiting"
    assert any(b["kind"] == "waiting_user" for b in result.execution_state["blockers"])


def test_observed_permission_blocker_waits_resumably(tmp_path, monkeypatch):
    agent, _ = configured_agent(tmp_path, [action("write_file", path="app.py", content="print(1)"),
                                         action("final", message="Cannot continue: permission denied; approval is required.")], monkeypatch)
    agent.tools.approval_callback = lambda *_: False
    result = agent.run_detailed("Implement a small app")
    assert result.disposition == RunDisposition.WAITING
    assert result.denied_actions
    assert any(b["kind"] == "waiting_permission" for b in result.execution_state["blockers"])


def test_structured_directory_and_parent_file_creation_respect_permissions(tmp_path):
    approvals = []
    tools = ToolRegistry(workspace=tmp_path, dry_run=False,
                         approval_callback=lambda kind, detail: approvals.append((kind, detail)) or True)
    assert tools.run(MakeDirectoryAction(type="make_directory", path="client/src")).ok
    assert tools.run(WriteFileAction(type="write_file", path="server/src/app.py", content="print(1)")).ok
    assert (tmp_path / "client/src").is_dir()
    assert (tmp_path / "server/src/app.py").read_text() == "print(1)"
    assert approvals[0][0] == "make_directory"
    assert "client/src" in approvals[0][1]
    tools.approval_callback = lambda *_: False
    assert not tools.run(MakeDirectoryAction(type="make_directory", path="denied")).ok
    assert not (tmp_path / "denied").exists()
    tools.dry_run = True
    assert not tools.run(MakeDirectoryAction(type="make_directory", path="dry")).ok
    assert not (tmp_path / "dry").exists()


def test_directory_escape_and_sensitive_paths_are_blocked(tmp_path):
    tools = ToolRegistry(workspace=tmp_path, dry_run=False, approval_callback=lambda *_: True)
    assert not tools.run(MakeDirectoryAction(type="make_directory", path="../outside")).ok
    assert not tools.run(MakeDirectoryAction(type="make_directory", path=".ssh/private")).ok
    assert not (tmp_path / ".ssh/private").exists()


@pytest.mark.parametrize("detail", ["Adapter failed (FileNotFoundError): secret api_key=private-key",
                                    "Traceback (most recent call last):", "Exception in thread index:", "read_memory"])
def test_normal_terminal_hides_internal_failures(detail):
    visible = normal_result_message(detail, blocked=True)
    assert "FileNotFoundError" not in visible
    assert "Traceback" not in visible
    assert "read_memory" not in visible
    assert "private-key" not in visible


def test_linkedin_frontend_empty_workspace_continues_past_scaffolding(tmp_path, monkeypatch):
    html = "<!doctype html><html><body><main><h1>Network</h1><section id='feed'>Posts</section></main></body></html>"
    fixed = html.replace("<main>", "<main><aside id='profile'>Your profile</aside>")
    checks = """from pathlib import Path
import unittest
class Frontend(unittest.TestCase):
    def test_feed_and_profile(self):
        page = Path('client/index.html').read_text(encoding='utf-8')
        self.assertIn("id='feed'", page)
        self.assertIn("id='profile'", page)
"""
    def plan(status):
        return action("update_plan", steps=[{"step": "Implement and verify the frontend", "status": status}])
    responses = [action("list_files", path="."), plan("in_progress"),
                 action("make_directory", path="client"), action("write_file", path="client/.gitkeep", content=""),
                 action("final", message="Would you like me to proceed?"),
                 action("write_file", path="client/index.html", content=html),
                 action("write_file", path="client/package.json", content='{"name":"network-frontend","private":true}'),
                 action("write_file", path="test_frontend.py", content=checks),
                 action("run_shell", command="python -m pytest test_frontend.py"),
                 plan("in_progress"), action("write_file", path="client/index.html", content=fixed),
                 action("run_shell", command="python -m pytest test_frontend.py"),
                 plan("completed"), action("final", message="Implemented the frontend; verification passed.")]
    agent, model = configured_agent(tmp_path, responses, monkeypatch)
    result = agent.run_detailed("Build a simple LinkedIn-style frontend in an empty workspace.")
    assert not result.blocked
    assert result.disposition == RunDisposition.SUCCESS
    assert (tmp_path / "client/index.html").read_text(encoding='utf-8') == fixed
    assert (tmp_path / "client/package.json").is_file()
    assert [v["ok"] for v in result.verification_results] == [False, True]
    assert any(r.get("type") == "false_completion" for r in result.failed_actions)
    assert result.execution_state["plan_revision"] >= 2
    requests = [json.loads(m["content"]) for turn in model.messages_seen for m in turn
                if m["role"] == "assistant" and m["content"].startswith("{")]
    assert all("ls -la" not in str(r) and "mkdir -p" not in str(r) for r in requests)
    assert "structured" in model.messages_seen[0][0]["content"].lower()
    if os.name == "nt":
        assert "native Windows" in system_prompt(tmp_path, False)


def test_non_workspace_creation_request_remains_direct_chat(tmp_path, monkeypatch):
    agent, _ = configured_agent(tmp_path, [action("final", message="Clouds drift above the hills.")], monkeypatch)
    result = agent.run_detailed("Create a haiku about clouds")
    assert not result.blocked
    assert result.changed_paths == []


def test_paused_report_has_no_finished_heading_or_raw_failure():
    from code_agent.agent import AgentRunResult
    from code_agent.terminal_ui import console, print_work_report_panel

    result = AgentRunResult(message="Adapter failed (FileNotFoundError): api_key=private-key",
                            run_id=1, blocked=True, disposition=RunDisposition.WAITING,
                            plan_updates=[{"steps": [{"step": "Inspect project", "status": "in_progress"}]}])
    with console.capture() as capture:
        print_work_report_panel(result)
    visible = capture.get()
    assert "Paused" in visible
    assert "Finished Work" not in visible
    assert "FileNotFoundError" not in visible and "private-key" not in visible


def test_verification_before_latest_change_cannot_finish(tmp_path, monkeypatch):
    from code_agent.completion_evidence import completion_blocker
    import hashlib

    content = "print('new behavior')\n"
    (tmp_path / "app.py").write_text(content, encoding="utf-8")
    records = [{"path": "app.py", "action": "write_file", "ok": True, "verified": True,
                "content_changed": True, "after_sha256": hashlib.sha256(content.encode()).hexdigest(), "step": 4}]
    verification = [{"purpose": "test", "command": "python -m pytest", "ok": True, "step": 3}]
    assert completion_blocker("Implement a small app", "Done", records, verification, tmp_path)
    verification[0]["step"] = 5
    assert completion_blocker("Implement a small app", "Done", records, verification, tmp_path) is None
    (tmp_path / "app.py").write_text("# external replacement\n", encoding="utf-8")
    assert completion_blocker("Implement a small app", "Done", records, verification, tmp_path)


def test_tests_only_do_not_implement_frontend(tmp_path):
    import hashlib
    from code_agent.completion_evidence import completion_blocker

    content = "def test_placeholder(): assert True\n"
    (tmp_path / "test_app.py").write_text(content, encoding="utf-8")
    records = [{"path": "test_app.py", "ok": True, "verified": True, "content_changed": True,
                "after_sha256": hashlib.sha256(content.encode()).hexdigest(), "step": 1}]
    verification = [{"purpose": "test", "command": "python -m pytest", "ok": True, "step": 2}]
    assert completion_blocker("Build a frontend", "Done", records, verification, tmp_path)


def test_fingerprint_secret_target_is_opaque_and_legacy_snapshot_remains_recoverable():
    from code_agent.execution_state import ExecutionState
    from code_agent.schema import RunShellAction, ToolResult

    state = ExecutionState(task="inspect project", max_steps=None)
    proposed = RunShellAction(type="run_shell", command="echo private-short-secret")
    failed = ToolResult(ok=False, output="private-short-secret")
    state.record_action(proposed, failed, [])
    state.record_action(proposed, failed, [])
    snapshot = state.snapshot()
    assert "private-short-secret" not in str(snapshot)
    assert "private-short-secret" not in repr(next(iter(state._failure_fingerprints)))
    # Previous versions persisted a raw target. Restoration upgrades it to the same digest.
    snapshot["failure_fingerprints"][0]["target"] = proposed.command
    restored = ExecutionState.from_snapshot(snapshot, task="inspect project", max_steps=None)
    restored.record_action(proposed, failed, [])
    assert max(restored._failure_fingerprints.values()) == 3
    assert "private-short-secret" not in str(restored.snapshot())


def test_windows_transaction_hash_uses_actual_crlf_bytes(tmp_path):
    import hashlib
    from code_agent.completion_evidence import completion_blocker

    raw = b"value = 2\r\n"
    (tmp_path / "app.py").write_bytes(raw)
    records = [{"path": "app.py", "action": "write_file", "ok": True, "verified": True,
                "content_changed": True, "transaction_id": "synthetic", "step": 1,
                "after_sha256": hashlib.sha256(raw).hexdigest()}]
    checks = [{"purpose": "test", "command": "python -m pytest", "ok": True, "step": 2}]
    assert completion_blocker("Fix app.py", "Done", records, checks, tmp_path) is None
    (tmp_path / "app.py").write_bytes(b"value = 3\r\n")
    assert completion_blocker("Fix app.py", "Done", records, checks, tmp_path)
