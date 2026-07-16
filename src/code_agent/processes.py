from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any


MAX_PROCESS_STREAM_CHARS = 2_000_000
MAX_PROCESS_EVENTS = 20_000


@dataclass(frozen=True)
class ShellOutputEvent:
    sequence: int
    stream: str
    text: str
    timestamp: str
    offset_ms: float

    def as_payload(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "stream": self.stream,
            "text": self.text,
            "timestamp": self.timestamp,
            "offset_ms": self.offset_ms,
        }


@dataclass(frozen=True)
class ShellProcessResult:
    completed: subprocess.CompletedProcess[str]
    timed_out: bool = False
    cancelled: bool = False
    output: str = ""
    cleanup_attempted: bool = False
    metadata: dict[str, Any] | None = None
    events: tuple[ShellOutputEvent, ...] = ()
    duration_ms: float = 0.0


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
        process_args = split_command_argv(command)
        if not process_args:
            raise ValueError("Shell command cannot be empty.")
        process = subprocess.Popen(
            process_args,
            cwd=cwd,
            shell=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            creationflags=creationflags,
            start_new_session=start_new_session,
        )
        self._register(process)
        started = time.monotonic()
        deadline = time.monotonic() + timeout_seconds
        cleanup_attempted = False
        captured_parts: list[str] = []
        try:
            if _supports_stream_capture(process):
                return capture_process_streams(
                    process,
                    command,
                    deadline=deadline,
                    started=started,
                    cwd=cwd,
                    env=env,
                    cancellation_token=cancellation_token,
                    poll_seconds=poll_seconds,
                )
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
                        duration_ms=round((time.monotonic() - started) * 1000, 2),
                        metadata=_execution_metadata(process_args, cwd, env),
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
                        duration_ms=round((time.monotonic() - started) * 1000, 2),
                        metadata=_execution_metadata(process_args, cwd, env),
                    )

                try:
                    stdout, stderr = process.communicate(timeout=min(poll_seconds, remaining))
                    return ShellProcessResult(
                        completed=subprocess.CompletedProcess(command, process.returncode, stdout, stderr),
                        output="",
                        cleanup_attempted=cleanup_attempted,
                        duration_ms=round((time.monotonic() - started) * 1000, 2),
                        metadata=_execution_metadata(process_args, cwd, env),
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


def _supports_stream_capture(process: subprocess.Popen[str]) -> bool:
    return all(
        stream is not None and callable(getattr(stream, "readline", None))
        for stream in (getattr(process, "stdout", None), getattr(process, "stderr", None))
    )


def capture_process_streams(
    process: subprocess.Popen[str],
    command: str,
    *,
    deadline: float,
    started: float,
    cwd: Any,
    env: dict[str, str],
    cancellation_token: CancellationToken | None,
    poll_seconds: float,
) -> ShellProcessResult:
    events: list[ShellOutputEvent] = []
    streams: dict[str, list[str]] = {"stdout": [], "stderr": []}
    original_chars = {"stdout": 0, "stderr": 0}
    captured_chars = {"stdout": 0, "stderr": 0}
    dropped_chars = {"stdout": 0, "stderr": 0}
    dropped_events = 0
    event_lock = threading.Lock()
    sequence = 0

    def read_stream(name: str, stream: Any) -> None:
        nonlocal dropped_events, sequence
        while True:
            chunk = stream.readline()
            if not chunk:
                return
            text = _coerce_process_output(chunk)
            with event_lock:
                original_chars[name] += len(text)
                remaining = MAX_PROCESS_STREAM_CHARS - captured_chars[name]
                captured = text[: max(0, remaining)]
                dropped_chars[name] += len(text) - len(captured)
                if not captured:
                    continue
                captured_chars[name] += len(captured)
                streams[name].append(captured)
                if len(events) >= MAX_PROCESS_EVENTS:
                    dropped_events += 1
                    continue
                sequence += 1
                events.append(
                    ShellOutputEvent(
                        sequence=sequence,
                        stream=name,
                        text=captured,
                        timestamp=datetime.now(UTC).isoformat(),
                        offset_ms=round((time.monotonic() - started) * 1000, 2),
                    )
                )

    readers = [
        threading.Thread(
            target=read_stream,
            args=("stdout", process.stdout),
            name=f"agent47-process-{process.pid}-stdout",
            daemon=True,
        ),
        threading.Thread(
            target=read_stream,
            args=("stderr", process.stderr),
            name=f"agent47-process-{process.pid}-stderr",
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    timed_out = False
    cancelled = False
    cleanup_attempted = False
    while process.poll() is None:
        if cancellation_token is not None and cancellation_token.cancelled:
            cancelled = True
            cleanup_attempted = True
            terminate_process_tree(process)
            break
        if time.monotonic() >= deadline:
            timed_out = True
            cleanup_attempted = True
            terminate_process_tree(process)
            break
        time.sleep(min(poll_seconds, max(0.01, deadline - time.monotonic())))

    for reader in readers:
        reader.join(timeout=2.0)
    with event_lock:
        for name in ("stdout", "stderr"):
            if dropped_chars[name] <= 0:
                continue
            marker = f"\n<truncated {dropped_chars[name]} {name} chars>\n"
            streams[name].append(marker)
            sequence += 1
            events.append(
                ShellOutputEvent(
                    sequence=sequence,
                    stream=name,
                    text=marker,
                    timestamp=datetime.now(UTC).isoformat(),
                    offset_ms=round((time.monotonic() - started) * 1000, 2),
                )
            )
        if dropped_events:
            sequence += 1
            events.append(
                ShellOutputEvent(
                    sequence=sequence,
                    stream="process",
                    text=f"\n<truncated {dropped_events} ordered output events>\n",
                    timestamp=datetime.now(UTC).isoformat(),
                    offset_ms=round((time.monotonic() - started) * 1000, 2),
                )
            )
    returncode = process.poll()
    stdout = "".join(streams["stdout"])
    stderr = "".join(streams["stderr"])
    ordered = "".join(event.text for event in sorted(events, key=lambda item: item.sequence))
    duration_ms = round((time.monotonic() - started) * 1000, 2)
    metadata = _execution_metadata(split_command_argv(command), cwd, env)
    metadata.update(
        {
            "termination_reason": (
                "cancelled"
                if cancelled
                else "timeout"
                if timed_out
                else "signal"
                if isinstance(returncode, int) and returncode < 0
                else "exit"
            ),
            "signal": abs(returncode) if isinstance(returncode, int) and returncode < 0 else None,
            "capture": {
                "stdout_chars": original_chars["stdout"],
                "stderr_chars": original_chars["stderr"],
                "stdout_truncated": dropped_chars["stdout"] > 0,
                "stderr_truncated": dropped_chars["stderr"] > 0,
                "event_count": len(events) + dropped_events,
                "captured_event_count": len(events),
                "ordered_events_truncated": dropped_events > 0,
            },
        }
    )
    return ShellProcessResult(
        completed=subprocess.CompletedProcess(command, returncode, stdout, stderr),
        timed_out=timed_out,
        cancelled=cancelled,
        output=ordered,
        cleanup_attempted=cleanup_attempted,
        metadata=metadata,
        events=tuple(sorted(events, key=lambda item: item.sequence)),
        duration_ms=duration_ms,
    )


def _execution_metadata(
    argv: list[str],
    cwd: Any,
    env: dict[str, str],
) -> dict[str, Any]:
    normalized_env = "\n".join(f"{key}={env[key]}" for key in sorted(env))
    return {
        "argv": argv,
        "cwd": str(cwd),
        "environment_keys": sorted(env),
        "environment_sha256": sha256(normalized_env.encode("utf-8")).hexdigest(),
    }


def _joined_output(*parts: str | bytes | None) -> str:
    return "\n".join(_coerce_process_output(part) for part in parts if part)


def _coerce_process_output(value: str | bytes) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def split_command_argv(command: str) -> list[str]:
    argv = shlex.split(command, posix=os.name != "nt")
    if os.name == "nt":
        argv = [_strip_wrapping_quotes(item) for item in argv]
    if argv and argv[0].lower() in {"python", "python3", "python.exe", "python3.exe"}:
        argv[0] = sys.executable
    return argv


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
