from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from .managed_processes import (
    ManagedProcessSpec,
    check_local_port,
    detect_port,
    sample_process_resources,
)
from .processes import terminate_process_tree, windows_creation_flags
from .safety import redact_secrets


class ProcessWorker:
    def __init__(self, job_dir: Path) -> None:
        self.job_dir = job_dir.resolve()
        self.spec = ManagedProcessSpec(**json.loads((self.job_dir / "spec.json").read_text(encoding="utf-8")))
        self.state_path = self.job_dir / "state.json"
        self.events_path = self.job_dir / "events.ndjson"
        self.controls_dir = self.job_dir / "controls"
        self.process: subprocess.Popen[Any] | None = None
        self.pty_master: int | None = None
        self.sequence = 0
        self.restart_count = 0
        self.stop_requested = False
        self.restart_requested = False
        self.started_monotonic = 0.0
        self.detected_port: int | None = None
        self.last_health: bool | None = None
        self.last_resource_emit = 0.0
        self.failure_reason: str | None = None
        self.output_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.readers: list[threading.Thread] = []
        self._event_lock = threading.Lock()

    def run(self) -> int:
        self._emit("worker_started", worker_pid=os.getpid())
        self._write_state(status="starting", worker_pid=os.getpid(), pid=None)
        while True:
            self.stop_requested = False
            self.restart_requested = False
            self.failure_reason = None
            try:
                self._start_child()
            except Exception as exc:
                self._emit("start_failed", severity="error", message=redact_secrets(str(exc)))
                self._write_state(status="failed", pid=None, error=redact_secrets(str(exc)))
                return 1

            exit_code = self._monitor_child()
            if self.stop_requested:
                status = "failed" if self.failure_reason else "stopped"
                health = self.failure_reason or "stopped"
                self._write_state(status=status, exit_code=exit_code, ready=False, health=health)
                self._emit(status, exit_code=exit_code, reason=self.failure_reason)
                return 1 if self.failure_reason else 0

            should_restart = self.restart_requested or (
                self.spec.auto_restart
                and exit_code != 0
                and self.restart_count < self.spec.max_restarts
            )
            if not should_restart:
                status = "exited" if exit_code == 0 else "failed"
                self._write_state(status=status, exit_code=exit_code, ready=False, health=status)
                self._emit("exited", exit_code=exit_code, status=status)
                return 0 if exit_code == 0 else 1

            if not self.restart_requested:
                self.restart_count += 1
            delay = min(
                self.spec.max_restart_backoff_seconds,
                self.spec.restart_backoff_seconds * (2 ** max(0, self.restart_count - 1)),
            )
            self._write_state(status="restarting", restart_count=self.restart_count, ready=False)
            self._emit(
                "restarting",
                exit_code=exit_code,
                restart_count=self.restart_count,
                backoff_seconds=delay,
                requested=self.restart_requested,
            )
            if not self.restart_requested:
                deadline = time.monotonic() + delay
                while time.monotonic() < deadline:
                    self._consume_controls(process_running=False)
                    if self.stop_requested:
                        self._write_state(status="stopped", exit_code=exit_code, ready=False)
                        return 0
                    time.sleep(0.1)

    def _start_child(self) -> None:
        creationflags = windows_creation_flags()
        child_argv = self._child_argv()
        common: dict[str, Any] = {
            "cwd": self.spec.cwd,
            "env": dict(os.environ),
            "shell": False,
            "creationflags": creationflags,
            "start_new_session": os.name != "nt",
        }
        if self.spec.pty and self.spec.execution_backend != "container":
            import pty

            master, slave = pty.openpty()
            self.pty_master = master
            self.process = subprocess.Popen(
                child_argv,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
                **common,
            )
            os.close(slave)
            self.readers = [self._start_pty_reader(master)]
        else:
            self.process = subprocess.Popen(
                child_argv,
                stdin=subprocess.PIPE if self.spec.interactive else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT if self.spec.pty else subprocess.PIPE,
                text=True,
                bufsize=1,
                **common,
            )
            self.readers = [
                self._start_text_reader("terminal" if self.spec.pty else "stdout", self.process.stdout),
            ]
            if not self.spec.pty:
                self.readers.append(self._start_text_reader("stderr", self.process.stderr))
        self.started_monotonic = time.monotonic()
        self.last_health = None
        self.detected_port = self.spec.readiness_port
        self._write_state(
            status="running",
            pid=self.process.pid,
            started_at=datetime.now(UTC).isoformat(),
            exit_code=None,
            restart_count=self.restart_count,
            ready=False,
            health="starting" if self.detected_port else "running",
            detected_port=self.detected_port,
        )
        self._emit(
            "started",
            pid=self.process.pid,
            restart_count=self.restart_count,
            pty=self.spec.pty,
            interactive=self.spec.interactive,
            execution_backend=self.spec.execution_backend,
            container_name=self.spec.container_name,
        )

    def _child_argv(self) -> list[str]:
        if self.spec.execution_backend != "container":
            return self.spec.argv
        if not self.spec.container_runtime_path or not self.spec.container_name:
            raise RuntimeError("Container process spec is missing runtime metadata.")
        pid_path = (
            f"{(self.spec.container_workspace or '/workspace').rstrip('/')}"
            f"/.code-agent/processes/jobs/{self.spec.process_id}/container.pid"
        )
        command = [self.spec.container_runtime_path, "exec", "-i"]
        if self.spec.pty:
            command.append("-t")
        command.extend(["-w", self.spec.container_cwd or self.spec.container_workspace or "/workspace"])
        for key in self.spec.environment_keys:
            value = os.environ.get(key)
            if value is not None and key in {"CI", "LANG", "LC_ALL", "NO_COLOR", "PYTHONUNBUFFERED", "TERM", "TZ"}:
                command.extend(["-e", f"{key}={value}"])
        command.extend(
            [
                self.spec.container_name,
                "sh",
                "-c",
                'echo $$ > "$1"; shift; exec "$@"',
                "agent47",
                pid_path,
                *self.spec.argv,
            ]
        )
        return command

    def _monitor_child(self) -> int:
        assert self.process is not None
        process = self.process
        while process.poll() is None:
            self._drain_output()
            self._consume_controls(process_running=True)
            self._monitor_health()
            self._monitor_resources()
            if self.stop_requested or self.restart_requested:
                grace = 5.0
                self._write_state(status="stopping", ready=False, health="stopping")
                self._emit("stopping", requested_restart=self.restart_requested, grace_seconds=grace)
                self._terminate_child(process, grace_seconds=grace)
                break
            if self.spec.timeout_seconds and time.monotonic() - self.started_monotonic >= self.spec.timeout_seconds:
                self.stop_requested = True
                self.failure_reason = "timeout"
                self._emit("timeout", timeout_seconds=self.spec.timeout_seconds, severity="error")
                self._terminate_child(process, grace_seconds=3.0)
                break
            time.sleep(0.1)
        for reader in self.readers:
            reader.join(timeout=1.0)
        self._drain_output()
        if self.pty_master is not None:
            try:
                os.close(self.pty_master)
            except OSError:
                pass
            self.pty_master = None
        return_code = process.poll()
        if return_code is None:
            try:
                return_code = process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                return_code = process.wait(timeout=2.0)
        self.process = None
        return int(return_code)

    def _start_text_reader(self, stream_name: str, stream: TextIO | None) -> threading.Thread:
        def reader() -> None:
            if stream is None:
                return
            for line in iter(stream.readline, ""):
                self.output_queue.put((stream_name, line))
            stream.close()

        thread = threading.Thread(
            target=reader,
            name=f"agent47-{self.spec.process_id}-{stream_name}",
            daemon=True,
        )
        thread.start()
        return thread

    def _start_pty_reader(self, master: int) -> threading.Thread:
        def reader() -> None:
            while True:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    return
                if not chunk:
                    return
                self.output_queue.put(("terminal", chunk.decode("utf-8", errors="replace")))

        thread = threading.Thread(
            target=reader,
            name=f"agent47-{self.spec.process_id}-pty",
            daemon=True,
        )
        thread.start()
        return thread

    def _drain_output(self) -> None:
        while True:
            try:
                stream, text = self.output_queue.get_nowait()
            except queue.Empty:
                return
            safe_text = redact_secrets(text)
            port = detect_port(safe_text)
            if port and self.detected_port is None:
                self.detected_port = port
                self._write_state(detected_port=port, health="starting")
                self._emit("port_detected", port=port)
            self._append_rotating_log(stream, safe_text)
            self._emit("output", stream=stream, text=safe_text)

    def _consume_controls(self, *, process_running: bool) -> None:
        if not self.controls_dir.exists():
            return
        for path in sorted(self.controls_dir.glob("*.json")):
            try:
                command = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            finally:
                try:
                    path.unlink()
                except OSError:
                    pass
            action = command.get("action")
            if action == "stop":
                self.stop_requested = True
                self.restart_requested = False
            elif action == "restart":
                self.restart_requested = True
                self.stop_requested = False
            elif action == "input" and process_running:
                self._write_input(str(command.get("data", "")))

    def _write_input(self, data: str) -> None:
        if not self.spec.interactive or self.process is None:
            self._emit("input_rejected", severity="error", message="Process is not interactive.")
            return
        try:
            if self.spec.pty and self.pty_master is not None:
                os.write(self.pty_master, data.encode("utf-8"))
            elif self.process.stdin is not None:
                payload: Any = data if self.process.text_mode else data.encode("utf-8")
                self.process.stdin.write(payload)
                self.process.stdin.flush()
            else:
                raise OSError("stdin is unavailable")
        except (BrokenPipeError, OSError, ValueError) as exc:
            self._emit("input_rejected", severity="error", message=redact_secrets(str(exc)))
            return
        self._emit("input", bytes=len(data.encode("utf-8")))

    def _monitor_health(self) -> None:
        if self.detected_port is None:
            return
        healthy = (
            self._container_port_ready(self.detected_port)
            if self.spec.execution_backend == "container"
            else check_local_port(self.detected_port)
        )
        if healthy == self.last_health:
            return
        self.last_health = healthy
        status = "ready" if healthy else "running"
        self._write_state(status=status, ready=healthy, health="healthy" if healthy else "starting")
        self._emit("health", ready=healthy, port=self.detected_port)

    def _monitor_resources(self) -> None:
        if self.process is None or time.monotonic() - self.last_resource_emit < 1.0:
            return
        self.last_resource_emit = time.monotonic()
        resources = (
            self._container_resources()
            if self.spec.execution_backend == "container"
            else sample_process_resources(self.process.pid)
        )
        self._write_state(resources=resources)
        self._emit("resource", **resources)
        memory = resources.get("memory_mb")
        cpu = resources.get("cpu_seconds")
        if self.spec.memory_limit_mb and isinstance(memory, (int, float)) and memory > self.spec.memory_limit_mb:
            self.stop_requested = True
            self.failure_reason = "memory_limit"
            self._emit(
                "resource_limit",
                severity="error",
                resource="memory",
                actual=memory,
                limit=self.spec.memory_limit_mb,
            )
        if self.spec.cpu_time_limit_seconds and isinstance(cpu, (int, float)) and cpu > self.spec.cpu_time_limit_seconds:
            self.stop_requested = True
            self.failure_reason = "cpu_time_limit"
            self._emit(
                "resource_limit",
                severity="error",
                resource="cpu_time",
                actual=cpu,
                limit=self.spec.cpu_time_limit_seconds,
            )

    def _terminate_child(self, process: subprocess.Popen[Any], *, grace_seconds: float) -> None:
        if self.spec.execution_backend != "container":
            terminate_process_tree(process, grace_seconds=grace_seconds)
            return
        self._signal_container_process("TERM")
        try:
            process.wait(timeout=grace_seconds)
            return
        except subprocess.TimeoutExpired:
            self._signal_container_process("KILL")
        terminate_process_tree(process, grace_seconds=1.0)

    def _signal_container_process(self, signal_name: str) -> None:
        if not self.spec.container_runtime_path or not self.spec.container_name:
            return
        pid_path = (
            f"{(self.spec.container_workspace or '/workspace').rstrip('/')}"
            f"/.code-agent/processes/jobs/{self.spec.process_id}/container.pid"
        )
        subprocess.run(
            [
                self.spec.container_runtime_path,
                "exec",
                self.spec.container_name,
                "sh",
                "-c",
                'test -f "$1" && kill -"$2" "$(cat "$1")"',
                "agent47",
                pid_path,
                signal_name,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=windows_creation_flags(new_process_group=False),
        )

    def _container_port_ready(self, port: int) -> bool:
        if not self.spec.container_runtime_path or not self.spec.container_name:
            return False
        script = (
            "import socket,sys; s=socket.socket(); s.settimeout(.25); "
            "s.connect(('127.0.0.1',int(sys.argv[1]))); s.close()"
        )
        for executable in ("python3", "python"):
            completed = subprocess.run(
                [
                    self.spec.container_runtime_path,
                    "exec",
                    self.spec.container_name,
                    executable,
                    "-c",
                    script,
                    str(port),
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
                creationflags=windows_creation_flags(new_process_group=False),
            )
            if completed.returncode == 0:
                return True
        return False

    def _container_resources(self) -> dict[str, Any]:
        if not self.spec.container_runtime_path or not self.spec.container_name:
            return {"available": False}
        completed = subprocess.run(
            [
                self.spec.container_runtime_path,
                "stats",
                "--no-stream",
                "--format",
                "{{.CPUPerc}}|{{.MemUsage}}|{{.PIDs}}",
                self.spec.container_name,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=windows_creation_flags(new_process_group=False),
        )
        if completed.returncode != 0:
            return {"available": False}
        try:
            cpu_raw, memory_raw, pids_raw = completed.stdout.strip().split("|", 2)
            memory_match = re.fullmatch(
                r"([0-9.]+)\s*([KMGTP]i?B)",
                memory_raw.split("/", 1)[0].strip(),
                re.I,
            )
            if memory_match is None:
                return {"available": False}
            memory_mb = _memory_to_mb(
                float(memory_match.group(1)),
                memory_match.group(2),
            )
            return {
                "available": True,
                "scope": "container",
                "cpu_percent": float(cpu_raw.rstrip("%")),
                "memory_mb": round(memory_mb, 3),
                "pids": int(pids_raw.strip()),
            }
        except (TypeError, ValueError):
            return {"available": False}

    def _append_rotating_log(self, stream: str, text: str) -> None:
        path = self.job_dir / f"{stream}.log"
        encoded_size = len(text.encode("utf-8"))
        if path.exists() and path.stat().st_size + encoded_size > self.spec.log_max_bytes:
            for index in range(self.spec.log_backups, 0, -1):
                source = path if index == 1 else path.with_suffix(f".log.{index - 1}")
                destination = path.with_suffix(f".log.{index}")
                if source.exists():
                    if index == self.spec.log_backups and destination.exists():
                        destination.unlink()
                    os.replace(source, destination)
            self._emit("log_rotated", stream=stream, backups=self.spec.log_backups)
        with path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()

    def _emit(self, event_type: str, **payload: Any) -> None:
        with self._event_lock:
            self.sequence += 1
            event = {
                "sequence": self.sequence,
                "process_id": self.spec.process_id,
                "type": event_type,
                "timestamp": datetime.now(UTC).isoformat(),
                **payload,
            }
            encoded = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
            if self.events_path.exists() and self.events_path.stat().st_size + len(encoded.encode("utf-8")) > self.spec.log_max_bytes:
                self._rotate_path(self.events_path)
            with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
                handle.flush()
            if event_type != "output" or self.sequence % 100 == 0:
                self._write_state(event_sequence=self.sequence, last_event=event_type)

    def _write_state(self, **changes: Any) -> None:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            state = self.spec.public_payload()
        state.update(changes)
        state["worker_pid"] = os.getpid()
        if os.name == "nt":
            state["worker_launcher_pid"] = os.getppid()
        state["updated_at"] = datetime.now(UTC).isoformat()
        temp = self.state_path.with_suffix(".json.tmp")
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._replace_with_retry(temp, self.state_path)

    def _rotate_path(self, path: Path) -> None:
        for index in range(self.spec.log_backups, 0, -1):
            source = path if index == 1 else Path(f"{path}.{index - 1}")
            destination = Path(f"{path}.{index}")
            if source.exists():
                if index == self.spec.log_backups and destination.exists():
                    destination.unlink()
                os.replace(source, destination)

    @staticmethod
    def _replace_with_retry(source: Path, destination: Path) -> None:
        for attempt in range(20):
            try:
                os.replace(source, destination)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.02 * (attempt + 1))


def _memory_to_mb(value: float, unit: str) -> float:
    normalized = unit.upper()
    factors = {
        "KB": 1 / 1000,
        "KIB": 1 / 1024,
        "MB": 1.0,
        "MIB": 1.0,
        "GB": 1000.0,
        "GIB": 1024.0,
        "TB": 1_000_000.0,
        "TIB": 1024.0 * 1024.0,
        "PB": 1_000_000_000.0,
        "PIB": 1024.0 * 1024.0 * 1024.0,
    }
    return value * factors.get(normalized, 1.0)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m code_agent.process_worker JOB_DIR")
    worker = ProcessWorker(Path(sys.argv[1]))
    raise SystemExit(worker.run())


if __name__ == "__main__":
    main()
