from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .processes import (
    background_python_executable,
    split_command_argv,
    terminate_pid_tree,
    use_background_python,
    windows_creation_flags,
)


PROCESS_ROOT = Path(".code-agent/processes")
URL_PORT_PATTERN = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::(\d{2,5}))?", re.I)


class ManagedProcessError(RuntimeError):
    pass


@dataclass(frozen=True)
class ManagedProcessSpec:
    process_id: str
    command: str
    argv: list[str]
    name: str
    cwd: str
    environment_keys: list[str]
    environment_sha256: str
    interactive: bool = False
    pty: bool = False
    timeout_seconds: int = 0
    readiness_port: int | None = None
    auto_restart: bool = False
    max_restarts: int = 3
    restart_backoff_seconds: float = 1.0
    max_restart_backoff_seconds: float = 30.0
    memory_limit_mb: int | None = None
    cpu_time_limit_seconds: int | None = None
    log_max_bytes: int = 5_000_000
    log_backups: int = 3
    created_at: str = ""

    def public_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["pty_supported"] = os.name != "nt"
        return payload


class ManagedProcessStore:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.root = self.workspace / PROCESS_ROOT
        self.jobs = self.root / "jobs"
        self.jobs.mkdir(parents=True, exist_ok=True)

    def start(
        self,
        command: str,
        *,
        cwd: Path,
        env: dict[str, str],
        name: str | None = None,
        interactive: bool = False,
        pty: bool = False,
        timeout_seconds: int = 0,
        readiness_port: int | None = None,
        auto_restart: bool = False,
        max_restarts: int = 3,
        restart_backoff_seconds: float = 1.0,
        max_restart_backoff_seconds: float = 30.0,
        memory_limit_mb: int | None = None,
        cpu_time_limit_seconds: int | None = None,
        log_max_bytes: int = 5_000_000,
        log_backups: int = 3,
    ) -> dict[str, Any]:
        argv = use_background_python(split_command_argv(command))
        if not argv:
            raise ManagedProcessError("Process command cannot be empty.")
        resolved_cwd = cwd.resolve()
        try:
            resolved_cwd.relative_to(self.workspace)
        except ValueError as exc:
            raise ManagedProcessError("Process cwd must remain inside the workspace.") from exc
        if pty and not interactive:
            raise ManagedProcessError("PTY mode requires interactive=true.")
        if pty and os.name == "nt":
            raise ManagedProcessError(
                "Native PTY is unavailable on Windows without a ConPTY backend; use interactive pipe mode."
            )
        process_id = f"proc-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:8]}"
        created_at = datetime.now(UTC).isoformat()
        normalized_env = "\n".join(f"{key}={env[key]}" for key in sorted(env))
        spec = ManagedProcessSpec(
            process_id=process_id,
            command=command,
            argv=argv,
            name=(name or Path(argv[0]).name)[:80],
            cwd=str(resolved_cwd),
            environment_keys=sorted(env),
            environment_sha256=sha256(normalized_env.encode("utf-8")).hexdigest(),
            interactive=interactive,
            pty=pty,
            timeout_seconds=timeout_seconds,
            readiness_port=readiness_port,
            auto_restart=auto_restart,
            max_restarts=max_restarts,
            restart_backoff_seconds=restart_backoff_seconds,
            max_restart_backoff_seconds=max_restart_backoff_seconds,
            memory_limit_mb=memory_limit_mb,
            cpu_time_limit_seconds=cpu_time_limit_seconds,
            log_max_bytes=log_max_bytes,
            log_backups=log_backups,
            created_at=created_at,
        )
        job_dir = self.jobs / process_id
        job_dir.mkdir(parents=True)
        (job_dir / "controls").mkdir()
        _atomic_json(job_dir / "spec.json", asdict(spec))
        _atomic_json(
            job_dir / "state.json",
            {
                **spec.public_payload(),
                "status": "starting",
                "worker_pid": None,
                "pid": None,
                "restart_count": 0,
                "ready": False,
                "health": "starting",
                "event_sequence": 0,
                "updated_at": created_at,
            },
        )
        worker_log = (job_dir / "worker.log").open("a", encoding="utf-8", newline="\n")
        flags = windows_creation_flags(detached=True)
        try:
            worker = subprocess.Popen(
                [background_python_executable(), "-m", "code_agent.process_worker", str(job_dir)],
                cwd=self.workspace,
                stdin=subprocess.DEVNULL,
                stdout=worker_log,
                stderr=worker_log,
                env=env,
                close_fds=True,
                creationflags=flags,
                start_new_session=os.name != "nt",
            )
        finally:
            worker_log.close()
        deadline = time.monotonic() + 5.0
        state = self.inspect(process_id)
        while state.get("status") == "starting" and time.monotonic() < deadline:
            if worker.poll() is not None:
                break
            time.sleep(0.05)
            state = self.inspect(process_id)
        if worker.poll() is not None and state.get("status") == "starting":
            detail = (job_dir / "worker.log").read_text(encoding="utf-8", errors="replace")
            raise ManagedProcessError(f"Process worker failed to start: {detail.strip() or '<no output>'}")
        return state

    def list(self, *, include_finished: bool = True) -> list[dict[str, Any]]:
        records = []
        for path in sorted(self.jobs.glob("*/state.json"), reverse=True):
            try:
                record = _read_json_retry(path)
            except (OSError, json.JSONDecodeError):
                continue
            record = self._reconcile(record, path.parent)
            if include_finished or record.get("status") in {"starting", "running", "ready", "restarting"}:
                records.append(record)
        return records

    def inspect(self, process_id: str) -> dict[str, Any]:
        job_dir = self._job_dir(process_id)
        try:
            record = _read_json_retry(job_dir / "state.json")
        except FileNotFoundError as exc:
            raise ManagedProcessError(f"Unknown process: {process_id}") from exc
        except json.JSONDecodeError as exc:
            raise ManagedProcessError(f"Corrupt process state: {process_id}") from exc
        return self._reconcile(record, job_dir)

    def logs(self, process_id: str, *, stream: str = "all", tail_chars: int = 20_000) -> dict[str, Any]:
        job_dir = self._job_dir(process_id)
        if stream not in {"all", "stdout", "stderr", "terminal"}:
            raise ManagedProcessError("stream must be one of: all, stdout, stderr, terminal")
        names = [stream] if stream != "all" else ["stdout", "stderr", "terminal"]
        logs: dict[str, str] = {}
        for name in names:
            path = job_dir / f"{name}.log"
            if path.exists():
                logs[name] = _tail_text(path, tail_chars)
        return {"process_id": process_id, "logs": logs, "state": self.inspect(process_id)}

    def events(self, process_id: str, *, after: int = 0, limit: int = 500) -> dict[str, Any]:
        job_dir = self._job_dir(process_id)
        events: list[dict[str, Any]] = []
        paths = [
            *(job_dir / f"events.ndjson.{index}" for index in range(20, 0, -1)),
            job_dir / "events.ndjson",
        ]
        for path in paths:
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if int(event.get("sequence", 0)) <= after:
                    continue
                events.append(event)
                if len(events) >= limit:
                    break
            if len(events) >= limit:
                break
        cursor = int(events[-1]["sequence"]) if events else after
        return {"process_id": process_id, "after": after, "cursor": cursor, "events": events}

    def send_input(self, process_id: str, data: str) -> dict[str, Any]:
        state = self.inspect(process_id)
        if not state.get("interactive"):
            raise ManagedProcessError("Process was not started in interactive mode.")
        self._control(process_id, "input", data=data)
        return {"process_id": process_id, "accepted": True, "bytes": len(data.encode("utf-8"))}

    def stop(self, process_id: str, *, grace_seconds: float = 5.0) -> dict[str, Any]:
        state = self.inspect(process_id)
        if state.get("status") in {"exited", "failed", "stopped"}:
            return self._finish_worker_shutdown(process_id, state)
        if state.get("status") == "orphaned":
            resources = state.get("resources")
            process_ids = resources.get("process_ids", []) if isinstance(resources, dict) else []
            root_pid = state.get("pid")
            if isinstance(root_pid, int) and root_pid > 0 and root_pid not in process_ids:
                process_ids.append(root_pid)
            for pid in reversed(process_ids):
                if isinstance(pid, int) and _pid_alive(pid):
                    try:
                        terminate_pid_tree(pid, grace_seconds=min(grace_seconds, 1.0))
                    except OSError:
                        continue
            state.update(
                {
                    "status": "stopped",
                    "health": "orphan_cleaned",
                    "ready": False,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
            _atomic_json(self._job_dir(process_id) / "state.json", state)
            return self._finish_worker_shutdown(process_id, state)
        self._control(process_id, "stop", grace_seconds=grace_seconds)
        deadline = time.monotonic() + grace_seconds + 5.0
        while time.monotonic() < deadline:
            time.sleep(0.1)
            state = self.inspect(process_id)
            if state.get("status") in {"exited", "failed", "stopped", "orphaned"}:
                return self._finish_worker_shutdown(process_id, state)
        pid = state.get("pid")
        if isinstance(pid, int) and pid > 0:
            terminate_pid_tree(pid, grace_seconds=1.0)
        final_deadline = time.monotonic() + 3.0
        while time.monotonic() < final_deadline:
            state = self.inspect(process_id)
            if state.get("status") in {"exited", "failed", "stopped", "orphaned"}:
                return self._finish_worker_shutdown(process_id, state)
            time.sleep(0.1)
        return self._finish_worker_shutdown(process_id, state, force=True)

    def restart(self, process_id: str) -> dict[str, Any]:
        self._control(process_id, "restart")
        return {"process_id": process_id, "accepted": True}

    def stop_all(self, reason: str = "cancelled") -> int:
        active = self.list(include_finished=False)
        for record in active:
            self._control(str(record["process_id"]), "stop", reason=reason, grace_seconds=3.0)
        return len(active)

    def _control(self, process_id: str, action: str, **payload: Any) -> None:
        job_dir = self._job_dir(process_id)
        message = {
            "id": uuid.uuid4().hex,
            "action": action,
            "timestamp": datetime.now(UTC).isoformat(),
            **payload,
        }
        target = job_dir / "controls" / f"{time.time_ns()}-{message['id']}.json"
        temp = target.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(message, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, target)

    def _finish_worker_shutdown(
        self,
        process_id: str,
        state: dict[str, Any],
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        worker_ids = [
            pid
            for pid in (state.get("worker_pid"), state.get("worker_launcher_pid"))
            if isinstance(pid, int) and pid > 0
        ]
        deadline = time.monotonic() + (0.5 if force else 2.0)
        while any(_pid_alive(pid) for pid in worker_ids) and time.monotonic() < deadline:
            time.sleep(0.05)
        remaining = [pid for pid in worker_ids if _pid_alive(pid)]
        for pid in reversed(dict.fromkeys(remaining)):
            try:
                terminate_pid_tree(pid, grace_seconds=0.5)
            except OSError:
                continue
        final_deadline = time.monotonic() + 2.0
        while any(_pid_alive(pid) for pid in worker_ids) and time.monotonic() < final_deadline:
            time.sleep(0.05)
        state = self.inspect(process_id)
        state["worker_running"] = any(_pid_alive(pid) for pid in worker_ids)
        state["worker_stopped_at"] = datetime.now(UTC).isoformat()
        _atomic_json(self._job_dir(process_id) / "state.json", state)
        return state

    def _job_dir(self, process_id: str) -> Path:
        if not re.fullmatch(r"proc-[A-Za-z0-9TZ-]+", process_id):
            raise ManagedProcessError("Invalid process id.")
        path = (self.jobs / process_id).resolve()
        try:
            path.relative_to(self.jobs.resolve())
        except ValueError as exc:
            raise ManagedProcessError("Process path escaped the job store.") from exc
        return path

    def _reconcile(self, record: dict[str, Any], job_dir: Path) -> dict[str, Any]:
        status = str(record.get("status", "unknown"))
        worker_pid = record.get("worker_pid")
        if status in {"starting", "running", "ready", "restarting", "stopping"}:
            if status == "starting" and not isinstance(worker_pid, int):
                try:
                    created_at = datetime.fromisoformat(str(record["created_at"]))
                    startup_age = (datetime.now(UTC) - created_at).total_seconds()
                except (KeyError, TypeError, ValueError):
                    startup_age = 10.0
                if startup_age < 10.0:
                    return record
            if not isinstance(worker_pid, int) or not _pid_alive(worker_pid):
                record["status"] = "orphaned"
                record["health"] = "worker_missing"
                record["ready"] = False
                record["updated_at"] = datetime.now(UTC).isoformat()
                _atomic_json(job_dir / "state.json", record)
        return record


def detect_port(text: str) -> int | None:
    matches = URL_PORT_PATTERN.findall(text)
    for raw in reversed(matches):
        if raw:
            port = int(raw)
            if 1 <= port <= 65535:
                return port
    return None


def check_local_port(port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def sample_process_resources(pid: int) -> dict[str, Any]:
    process_ids = _process_tree_pids(pid)
    samples = [
        _sample_windows_resources(process_id)
        if os.name == "nt"
        else _sample_posix_resources(process_id)
        for process_id in process_ids
    ]
    available = [sample for sample in samples if sample.get("available")]
    if not available:
        return {"available": False, "process_count": len(process_ids)}
    payload: dict[str, Any] = {
        "available": True,
        "process_count": len(available),
        "process_ids": process_ids,
        "cpu_seconds": round(sum(float(item.get("cpu_seconds", 0)) for item in available), 3),
        "memory_mb": round(sum(float(item.get("memory_mb", 0)) for item in available), 3),
    }
    thread_values = [item.get("threads") for item in available if item.get("threads") is not None]
    if thread_values:
        payload["threads"] = sum(int(value) for value in thread_values)
    return payload


def _sample_posix_resources(pid: int) -> dict[str, Any]:
    stat_path = Path(f"/proc/{pid}/stat")
    status_path = Path(f"/proc/{pid}/status")
    if not stat_path.exists():
        return {"available": False}
    try:
        fields = stat_path.read_text(encoding="utf-8").split()
        status = status_path.read_text(encoding="utf-8") if status_path.exists() else ""
        rss_match = re.search(r"^VmRSS:\s+(\d+)\s+kB", status, re.M)
        ticks = os.sysconf("SC_CLK_TCK")
        cpu_seconds = (int(fields[13]) + int(fields[14])) / ticks
        return {
            "available": True,
            "cpu_seconds": round(cpu_seconds, 3),
            "memory_mb": round((int(rss_match.group(1)) if rss_match else 0) / 1024, 3),
            "threads": int(fields[19]),
        }
    except (OSError, ValueError, IndexError):
        return {"available": False}


def _process_tree_pids(root_pid: int) -> list[int]:
    if os.name == "nt":
        parent_by_pid = _windows_parent_map()
    else:
        parent_by_pid: dict[int, int] = {}
        for path in Path("/proc").glob("[0-9]*/stat"):
            try:
                fields = path.read_text(encoding="utf-8").split()
                parent_by_pid[int(fields[0])] = int(fields[3])
            except (OSError, ValueError, IndexError):
                continue
    descendants = [root_pid]
    seen = {root_pid}
    changed = True
    while changed:
        changed = False
        for process_id, parent_id in parent_by_pid.items():
            if parent_id in seen and process_id not in seen:
                descendants.append(process_id)
                seen.add(process_id)
                changed = True
    return descendants


def _windows_parent_map() -> dict[int, int]:
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessEntry32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        invalid_handle = ctypes.c_void_p(-1).value
        if not snapshot or snapshot == invalid_handle:
            return {}
        entry = ProcessEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        parent_by_pid: dict[int, int] = {}
        first = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while first:
            parent_by_pid[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            first = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        kernel32.CloseHandle(snapshot)
        return parent_by_pid
    except (AttributeError, OSError, ValueError):
        return {}


def _sample_windows_resources(pid: int) -> dict[str, Any]:
    try:
        import ctypes
        from ctypes import wintypes

        process_query = 0x0400 | 0x0010
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(process_query, False, pid)
        if not handle:
            return {"available": False}

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        ok = psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        times_ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        kernel32.CloseHandle(handle)
        if not ok:
            return {"available": False}

        def filetime_seconds(value: Any) -> float:
            return ((value.dwHighDateTime << 32) | value.dwLowDateTime) / 10_000_000

        return {
            "available": True,
            "cpu_seconds": round(
                filetime_seconds(kernel) + filetime_seconds(user) if times_ok else 0.0,
                3,
            ),
            "memory_mb": round(counters.WorkingSetSize / (1024 * 1024), 3),
        }
    except (AttributeError, OSError, ValueError):
        return {"available": False}


def _tail_text(path: Path, max_chars: int) -> str:
    with path.open("rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        handle.seek(max(0, size - max_chars * 4))
        return handle.read().decode("utf-8", errors="replace")[-max_chars:]


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            exit_code = wintypes.DWORD()
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            return bool(ok) and exit_code.value == 259
        except (AttributeError, OSError, ValueError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    _replace_with_retry(temp, path)


def _replace_with_retry(source: Path, destination: Path) -> None:
    for attempt in range(20):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.02 * (attempt + 1))


def _read_json_retry(path: Path) -> dict[str, Any]:
    last_error: OSError | json.JSONDecodeError | None = None
    for attempt in range(20):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise json.JSONDecodeError("Expected object", "", 0)
            return payload
        except (PermissionError, FileNotFoundError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < 19:
                time.sleep(0.01 * (attempt + 1))
    assert last_error is not None
    raise last_error
