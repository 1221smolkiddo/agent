from __future__ import annotations

import os
import subprocess
from pathlib import Path

from code_agent import processes as processes_module
from code_agent.processes import (
    CancellationToken,
    ProcessSupervisor,
    background_python_executable,
    split_command_argv,
    use_background_python,
    windows_creation_flags,
)


def test_process_supervisor_cancels_registered_shell_process(tmp_path: Path, monkeypatch) -> None:
    class FakeProcess:
        pid = 1234
        returncode = None

        def poll(self):
            return self.returncode

        def communicate(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd="uv run pytest", timeout=timeout)
            self.returncode = -9
            return "partial output", ""

    fake_process = FakeProcess()
    terminated: list[int] = []

    def fake_terminate(process):
        terminated.append(process.pid)
        process.returncode = -9

    monkeypatch.setattr(processes_module.subprocess, "Popen", lambda *_args, **_kwargs: fake_process)
    monkeypatch.setattr(processes_module, "terminate_process_tree", fake_terminate)

    token = CancellationToken()
    token.cancel("test stop")
    supervisor = ProcessSupervisor()

    result = supervisor.run_shell(
        "uv run pytest",
        cwd=tmp_path,
        timeout_seconds=120,
        env={},
        cancellation_token=token,
    )

    assert result.cancelled is True
    assert result.cleanup_attempted is True
    assert result.completed.returncode == -9
    assert "partial output" in result.output
    assert terminated == [1234]
    assert supervisor.active_count == 0


def test_process_supervisor_executes_without_shell(tmp_path: Path, monkeypatch) -> None:
    class FakeProcess:
        pid = 1234
        returncode = 0

        def communicate(self, timeout=None):
            return "ok", ""

    captured: dict[str, object] = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(processes_module.subprocess, "Popen", fake_popen)

    result = ProcessSupervisor().run_shell(
        "uv run pytest tests/test_processes.py",
        cwd=tmp_path,
        timeout_seconds=30,
        env={},
    )

    assert result.completed.returncode == 0
    assert captured["shell"] is False
    assert captured["args"] == ["uv", "run", "pytest", "tests/test_processes.py"]
    if os.name == "nt":
        assert int(captured["creationflags"]) & subprocess.CREATE_NO_WINDOW


def test_windows_creation_flags_hide_normal_and_detached_processes() -> None:
    flags = windows_creation_flags()
    detached = windows_creation_flags(detached=True)

    if os.name != "nt":
        assert flags == 0
        assert detached == 0
        return
    assert flags & subprocess.CREATE_NO_WINDOW
    assert flags & subprocess.CREATE_NEW_PROCESS_GROUP
    assert detached & subprocess.CREATE_NO_WINDOW
    assert detached & subprocess.DETACHED_PROCESS


def test_split_command_argv_pins_python_to_current_runtime() -> None:
    argv = split_command_argv("python -m pytest")

    assert argv == [processes_module.sys.executable, "-m", "pytest"]


def test_background_python_avoids_console_interpreter_on_windows() -> None:
    executable = background_python_executable()
    argv = use_background_python([processes_module.sys.executable, "server.py"])

    assert argv == [executable, "server.py"]
    if os.name == "nt" and Path(processes_module.sys.executable).with_name("pythonw.exe").exists():
        assert Path(executable).name.lower() == "pythonw.exe"
