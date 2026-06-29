from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ShellProcessResult:
    completed: subprocess.CompletedProcess[str]
    timed_out: bool = False
    cancelled: bool = False
    output: str = ""
    cleanup_attempted: bool = False


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason = "cancelled"

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    def cancel(self, reason: str = "cancelled") -> None:
        self._reason = reason
        self._event.set()

    def reset(self) -> None:
        self._reason = "cancelled"
        self._event.clear()


class ProcessSupervisor:
    """Tracks local child processes so stops and timeouts can clean up trees."""

    def __init__(self) -> None:
        self._active: dict[int, subprocess.Popen[str]] = {}
        self._lock = threading.Lock()

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    def cancel_all(self, reason: str = "cancelled") -> int:
        with self._lock:
            processes = list(self._active.values())
        for process in processes:
            terminate_process_tree(process)
        return len(processes)

    def run_shell(
        self,
        command: str,
        *,
        cwd: Any,
        timeout_seconds: int,
        env: dict[str, str],
        cancellation_token: CancellationToken | None = None,
        poll_seconds: float = 0.2,
    ) -> ShellProcessResult:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        start_new_session = os.name != "nt"
        process = subprocess.Popen(
            command,
            cwd=cwd,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            creationflags=creationflags,
            start_new_session=start_new_session,
        )
        self._register(process)
        deadline = time.monotonic() + timeout_seconds
        cleanup_attempted = False
        captured_parts: list[str] = []
        try:
            while True:
                if cancellation_token is not None and cancellation_token.cancelled:
                    cleanup_attempted = True
                    terminate_process_tree(process)
                    stdout, stderr = process.communicate()
                    return ShellProcessResult(
                        completed=subprocess.CompletedProcess(command, process.returncode, stdout, stderr),
                        cancelled=True,
                        output=_joined_output(*captured_parts, stdout, stderr),
                        cleanup_attempted=cleanup_attempted,
                    )

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    cleanup_attempted = True
                    terminate_process_tree(process)
                    stdout, stderr = process.communicate()
                    return ShellProcessResult(
                        completed=subprocess.CompletedProcess(command, process.returncode, stdout, stderr),
                        timed_out=True,
                        output=_joined_output(*captured_parts, stdout, stderr),
                        cleanup_attempted=cleanup_attempted,
                    )

                try:
                    stdout, stderr = process.communicate(timeout=min(poll_seconds, remaining))
                    return ShellProcessResult(
                        completed=subprocess.CompletedProcess(command, process.returncode, stdout, stderr),
                        output="",
                        cleanup_attempted=cleanup_attempted,
                    )
                except subprocess.TimeoutExpired as exc:
                    captured_parts.extend(_coerce_process_output(part) for part in [exc.stdout, exc.stderr] if part)
        except KeyboardInterrupt:
            cleanup_attempted = True
            terminate_process_tree(process)
            raise
        finally:
            self._unregister(process)

    def _register(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            self._active[process.pid] = process

    def _unregister(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            self._active.pop(process.pid, None)


def terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()


def _joined_output(*parts: str | bytes | None) -> str:
    return "\n".join(_coerce_process_output(part) for part in parts if part)


def _coerce_process_output(value: str | bytes) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
