from __future__ import annotations

import subprocess
from pathlib import Path

from code_agent import processes as processes_module
from code_agent.processes import CancellationToken, ProcessSupervisor


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
