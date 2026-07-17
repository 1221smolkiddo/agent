from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path
import socket
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from code_agent.cli import app
from code_agent.managed_processes import (
    ManagedProcessError,
    ManagedProcessSpec,
    ManagedProcessStore,
)
from code_agent.managed_processes import _pid_alive
from code_agent.process_worker import ProcessWorker
from code_agent.processes import ProcessSupervisor
from code_agent.schema import (
    InspectProcessAction,
    ListProcessesAction,
    ProcessEventsAction,
    ReadProcessLogsAction,
    SendProcessInputAction,
    StartProcessAction,
    StopProcessAction,
)
from code_agent.tools import ToolRegistry


def _environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def _wait_for(
    supervisor: ProcessSupervisor,
    process_id: str,
    statuses: set[str],
    *,
    timeout: float = 15.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    state = supervisor.inspect_managed(process_id)
    while state.get("status") not in statuses and time.monotonic() < deadline:
        time.sleep(0.1)
        state = supervisor.inspect_managed(process_id)
    assert state.get("status") in statuses, state
    return state


def _write_script(workspace: Path, name: str, content: str) -> None:
    (workspace / name).write_text(content, encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="POSIX zombie semantics")
def test_pid_liveness_reaps_exited_direct_child() -> None:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    time.sleep(0.1)

    assert _pid_alive(process.pid) is False


def test_port_state_is_persisted_before_output_becomes_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "controls").mkdir()
    spec = ManagedProcessSpec(
        process_id="proc-test",
        command="python server.py",
        argv=[sys.executable, "server.py"],
        name="server",
        cwd=str(tmp_path),
        environment_keys=[],
        environment_sha256="",
    )
    (job_dir / "spec.json").write_text(json.dumps(asdict(spec)), encoding="utf-8")
    (job_dir / "state.json").write_text(
        json.dumps({**spec.public_payload(), "status": "running"}),
        encoding="utf-8",
    )
    worker = ProcessWorker(job_dir)
    observed_ports: list[int | None] = []
    append_log = worker._append_rotating_log

    def observe_then_append(stream: str, text: str) -> None:
        state = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))
        observed_ports.append(state.get("detected_port"))
        append_log(stream, text)

    monkeypatch.setattr(worker, "_append_rotating_log", observe_then_append)
    worker.output_queue.put(("stdout", "ready at http://127.0.0.1:54321\n"))

    worker._drain_output()

    assert observed_ports == [54321]


def test_managed_process_persists_redacted_logs_events_and_state(tmp_path: Path) -> None:
    _write_script(
        tmp_path,
        "server.py",
        "import time\n"
        "print('OPENAI_API_KEY=sk-test-secret', flush=True)\n"
        "print('ready at http://127.0.0.1:54321', flush=True)\n"
        "time.sleep(60)\n",
    )
    supervisor = ProcessSupervisor(tmp_path)
    state = supervisor.start_managed(
        "python server.py",
        cwd=tmp_path,
        env=_environment(),
        name="server",
    )
    process_id = str(state["process_id"])
    try:
        deadline = time.monotonic() + 10
        logs = supervisor.managed_logs(process_id)
        while "ready at" not in json.dumps(logs) and time.monotonic() < deadline:
            time.sleep(0.1)
            logs = supervisor.managed_logs(process_id)
        events = supervisor.managed_events(process_id)
        inspected = supervisor.inspect_managed(process_id)

        payload = json.dumps({"logs": logs, "events": events})
        assert "sk-test-secret" not in payload
        assert "ready at" in payload
        assert inspected["detected_port"] == 54321
        assert inspected["environment_keys"]
        assert "environment" not in inspected
        assert any(item["type"] == "output" for item in events["events"])
        spec_text = (
            tmp_path / ".code-agent/processes/jobs" / process_id / "spec.json"
        ).read_text(encoding="utf-8")
        assert "sk-test-secret" not in spec_text
        assert '"environment"' not in spec_text
    finally:
        reconnected = ProcessSupervisor(tmp_path)
        assert reconnected.inspect_managed(process_id)["process_id"] == process_id
        stopped = reconnected.stop_managed(process_id, grace_seconds=0.5)
        assert stopped["status"] in {"stopped", "exited", "failed", "orphaned"}
        assert stopped["worker_running"] is False


def test_interactive_process_accepts_input_through_control_channel(tmp_path: Path) -> None:
    _write_script(
        tmp_path,
        "interactive.py",
        "value = input()\nprint('received:' + value, flush=True)\n",
    )
    supervisor = ProcessSupervisor(tmp_path)
    state = supervisor.start_managed(
        "python interactive.py",
        cwd=tmp_path,
        env=_environment(),
        interactive=True,
    )
    process_id = str(state["process_id"])

    supervisor.send_managed_input(process_id, "hello\n")
    _wait_for(supervisor, process_id, {"exited"})
    logs = supervisor.managed_logs(process_id, stream="stdout")

    assert "received:hello" in logs["logs"]["stdout"]
    assert not list((tmp_path / ".code-agent/processes/jobs" / process_id / "controls").glob("*.json"))


def test_crashed_process_restarts_with_backoff_and_then_exits_cleanly(tmp_path: Path) -> None:
    _write_script(
        tmp_path,
        "restart.py",
        "from pathlib import Path\n"
        "counter = Path('counter.txt')\n"
        "value = int(counter.read_text() if counter.exists() else '0') + 1\n"
        "counter.write_text(str(value))\n"
        "print(f'attempt:{value}', flush=True)\n"
        "raise SystemExit(1 if value < 2 else 0)\n",
    )
    supervisor = ProcessSupervisor(tmp_path)
    state = supervisor.start_managed(
        "python restart.py",
        cwd=tmp_path,
        env=_environment(),
        auto_restart=True,
        max_restarts=3,
        restart_backoff_seconds=0.1,
        max_restart_backoff_seconds=0.2,
    )
    process_id = str(state["process_id"])

    final = _wait_for(supervisor, process_id, {"exited", "failed"})
    events = supervisor.managed_events(process_id)["events"]

    assert final["status"] == "exited"
    assert final["restart_count"] == 1
    assert (tmp_path / "counter.txt").read_text(encoding="utf-8") == "2"
    assert any(item["type"] == "restarting" for item in events)


def test_readiness_monitor_marks_listening_process_ready(tmp_path: Path) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    _write_script(
        tmp_path,
        "health.py",
        "import socket, sys, time\n"
        "server = socket.socket()\n"
        "server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "server.bind(('127.0.0.1', int(sys.argv[1])))\n"
        "server.listen()\n"
        "print(f'listening:{sys.argv[1]}', flush=True)\n"
        "time.sleep(60)\n",
    )
    supervisor = ProcessSupervisor(tmp_path)
    state = supervisor.start_managed(
        f"python health.py {port}",
        cwd=tmp_path,
        env=_environment(),
        readiness_port=port,
    )
    process_id = str(state["process_id"])
    try:
        ready = _wait_for(supervisor, process_id, {"ready"})
        assert ready["ready"] is True
        assert ready["health"] == "healthy"
    finally:
        supervisor.stop_managed(process_id, grace_seconds=0.5)


def test_memory_limit_terminates_resource_exhausting_process(tmp_path: Path) -> None:
    _write_script(
        tmp_path,
        "memory.py",
        "import time\npayload = bytearray(96 * 1024 * 1024)\nprint(len(payload), flush=True)\ntime.sleep(60)\n",
    )
    supervisor = ProcessSupervisor(tmp_path)
    state = supervisor.start_managed(
        "python memory.py",
        cwd=tmp_path,
        env=_environment(),
        memory_limit_mb=32,
    )
    process_id = str(state["process_id"])
    deadline = time.monotonic() + 8
    inspected = supervisor.inspect_managed(process_id)
    while not inspected.get("resources") and time.monotonic() < deadline:
        time.sleep(0.2)
        inspected = supervisor.inspect_managed(process_id)
    resources = inspected.get("resources")
    if not isinstance(resources, dict) or not resources.get("available"):
        supervisor.stop_managed(process_id, grace_seconds=0.5)
        pytest.skip("Per-process resource sampling is unavailable on this platform")

    final = _wait_for(supervisor, process_id, {"failed"})
    events = supervisor.managed_events(process_id)["events"]

    assert final["health"] == "memory_limit"
    assert any(item["type"] == "resource_limit" for item in events)


def test_process_logs_rotate_without_losing_latest_output(tmp_path: Path) -> None:
    _write_script(
        tmp_path,
        "noisy.py",
        "for index in range(5000):\n    print(f'{index}:' + 'x' * 80)\n",
    )
    supervisor = ProcessSupervisor(tmp_path)
    state = supervisor.start_managed(
        "python noisy.py",
        cwd=tmp_path,
        env=_environment(),
        log_max_bytes=65536,
        log_backups=2,
    )
    process_id = str(state["process_id"])
    _wait_for(supervisor, process_id, {"exited"})
    job_dir = tmp_path / ".code-agent/processes/jobs" / process_id

    assert (job_dir / "stdout.log.1").exists()
    assert (job_dir / "stdout.log").stat().st_size <= 70000
    assert supervisor.managed_events(process_id)["cursor"] > 0


def test_process_store_marks_missing_worker_as_orphaned(tmp_path: Path) -> None:
    store = ManagedProcessStore(tmp_path)
    job_dir = store.jobs / "proc-20260101T000000Z-deadbeef"
    job_dir.mkdir(parents=True)
    state = {
        "process_id": job_dir.name,
        "status": "running",
        "worker_pid": 99999999,
        "pid": 99999998,
    }
    (job_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    inspected = store.inspect(job_dir.name)

    assert inspected["status"] == "orphaned"
    assert inspected["health"] == "worker_missing"
    stopped = store.stop(job_dir.name, grace_seconds=0.1)
    assert stopped["status"] == "stopped"
    assert stopped["health"] == "orphan_cleaned"


def test_windows_pty_fails_closed_without_conpty_backend(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows-specific PTY boundary")
    store = ManagedProcessStore(tmp_path)

    with pytest.raises(ManagedProcessError, match="ConPTY"):
        store.start(
            "python interactive.py",
            cwd=tmp_path,
            env=_environment(),
            interactive=True,
            pty=True,
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX PTY test")
def test_posix_pty_process_supports_terminal_input(tmp_path: Path) -> None:
    _write_script(
        tmp_path,
        "terminal.py",
        "value = input('prompt:')\nprint('terminal:' + value, flush=True)\n",
    )
    supervisor = ProcessSupervisor(tmp_path)
    state = supervisor.start_managed(
        "python terminal.py",
        cwd=tmp_path,
        env=_environment(),
        interactive=True,
        pty=True,
    )
    process_id = str(state["process_id"])
    supervisor.send_managed_input(process_id, "hello\n")
    _wait_for(supervisor, process_id, {"exited"})

    logs = supervisor.managed_logs(process_id, stream="terminal")
    assert "terminal:hello" in logs["logs"]["terminal"]


def test_tool_registry_exposes_managed_process_lifecycle(tmp_path: Path) -> None:
    _write_script(tmp_path, "server.py", "import time\nprint('started', flush=True)\ntime.sleep(60)\n")
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=False,
        approval_callback=lambda *_args: True,
    )
    started = tools.run(
        StartProcessAction(type="start_process", command="python server.py", name="server")
    )
    assert started.ok, started.output
    process_id = started.metadata["process"]["process_id"]
    try:
        assert tools.run(ListProcessesAction(type="list_processes")).ok
        assert tools.run(
            InspectProcessAction(type="inspect_process", process_id=process_id)
        ).ok
        assert tools.run(
            ReadProcessLogsAction(type="read_process_logs", process_id=process_id)
        ).ok
        assert tools.run(
            ProcessEventsAction(type="process_events", process_id=process_id)
        ).ok
    finally:
        stopped = tools.run(
            StopProcessAction(type="stop_process", process_id=process_id, grace_seconds=0.5)
        )
        assert stopped.ok
        assert stopped.metadata["process"]["worker_running"] is False


def test_tool_registry_refuses_input_for_noninteractive_process(tmp_path: Path) -> None:
    _write_script(tmp_path, "server.py", "import time\ntime.sleep(60)\n")
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=False,
        approval_callback=lambda *_args: True,
    )
    started = tools.run(StartProcessAction(type="start_process", command="python server.py"))
    process_id = started.metadata["process"]["process_id"]
    try:
        result = tools.run(
            SendProcessInputAction(
                type="send_process_input",
                process_id=process_id,
                data="unsafe\n",
            )
        )
        assert not result.ok
        assert "interactive" in result.output
    finally:
        stopped = tools.run(
            StopProcessAction(type="stop_process", process_id=process_id, grace_seconds=0.5)
        )
        assert stopped.metadata["process"]["worker_running"] is False


def test_process_cli_lists_persisted_jobs(tmp_path: Path) -> None:
    store = ManagedProcessStore(tmp_path)
    job_dir = store.jobs / "proc-20260101T000000Z-deadbeef"
    job_dir.mkdir(parents=True)
    (job_dir / "state.json").write_text(
        json.dumps(
            {
                "process_id": job_dir.name,
                "name": "old-server",
                "status": "exited",
                "worker_pid": None,
                "pid": None,
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["processes", "list", "--cwd", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert job_dir.name in result.output
