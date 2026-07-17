from __future__ import annotations

import contextlib
import fnmatch
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import tomllib
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .processes import (
    CancellationToken,
    ProcessSupervisor,
    ShellProcessResult,
    capture_process_streams,
    windows_creation_flags,
)
from .safety import ShellPolicy, redact_secrets


POLICY_PATH = ".code-agent/policy.toml"
DISK_USAGE_IGNORES = {
    ".code-agent",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
LOCAL_RUNTIME_ROOT = Path(".code-agent") / "runtime" / "local"
LOCAL_ENV_OVERRIDES = {
    "AGENT47_LOCAL_SANDBOX": "1",
    "AGENT47_SANDBOXED_SHELL": "1",
}
CONTAINER_BACKENDS = {"docker", "podman", "container"}
CONTAINER_RUNTIME_ENV_KEYS = {
    "CONTAINER_CONNECTION",
    "CONTAINER_HOST",
    "DOCKER_CERT_PATH",
    "DOCKER_CONFIG",
    "DOCKER_CONTEXT",
    "DOCKER_HOST",
    "DOCKER_TLS_VERIFY",
    "XDG_RUNTIME_DIR",
}


class SandboxIsolationError(RuntimeError):
    """Raised when required process isolation cannot be established."""


@dataclass(frozen=True)
class SandboxResourceLimits:
    timeout_seconds: int | None = None
    cpus: float = 1.0
    memory_mb: int = 1024
    disk_mb: int = 2048
    pids: int = 128


@dataclass(frozen=True)
class CommandPolicy:
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    allow_install: bool = False
    allow_git_mutation: bool = False
    offline: bool = True
    domain_allowlist: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContainerImagePolicy:
    allowed_images: tuple[str, ...] = ("python:3.13-slim",)
    required_digest: str | None = None
    require_digest: bool = True
    scan_required: bool = False
    scanner: str = "trivy"
    denied_severities: tuple[str, ...] = ("HIGH", "CRITICAL")


@dataclass(frozen=True)
class ContainerRuntimeSecurity:
    rootless: bool
    seccomp: bool
    apparmor: bool = False
    selinux: bool = False
    detail: str = ""


@dataclass(frozen=True)
class SandboxPolicy:
    backend: str = "local"
    container_image: str = "python:3.13-slim"
    rootless_required: bool = True
    seccomp_required: bool = True
    container_user: str = "65532:65532"
    resources: SandboxResourceLimits = field(default_factory=SandboxResourceLimits)
    commands: CommandPolicy = field(default_factory=CommandPolicy)
    images: ContainerImagePolicy = field(default_factory=ContainerImagePolicy)
    audit_enabled: bool = True
    cleanup: bool = True
    process_isolation_required: bool = False
    requested_backend: str = "local"
    container_workspace: str = "/workspace"
    container_reuse: bool = True
    container_bind_mounts: tuple[str, ...] = ()
    container_cache_volumes: tuple[str, ...] = ()

    @classmethod
    def from_workspace(
        cls,
        workspace: Path,
        *,
        backend: str = "local",
        container_image: str = "python:3.13-slim",
    ) -> "SandboxPolicy":
        path = workspace.resolve() / POLICY_PATH
        if not path.exists():
            return cls(backend=backend, container_image=container_image)
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
        sandbox = _dict(payload.get("sandbox"))
        resources = _dict(payload.get("resources"))
        commands = _dict(payload.get("commands"))
        network = _dict(payload.get("network"))
        images = _dict(payload.get("images"))
        return cls(
            backend=str(sandbox.get("backend", backend)).strip().lower() or backend,
            container_image=str(
                sandbox.get("container_image", container_image)
            ).strip()
            or container_image,
            rootless_required=bool(sandbox.get("rootless_required", True)),
            seccomp_required=bool(sandbox.get("seccomp_required", True)),
            container_user=str(sandbox.get("container_user", "65532:65532")).strip(),
            resources=SandboxResourceLimits(
                timeout_seconds=_optional_int(resources.get("timeout_seconds")),
                cpus=float(resources.get("cpus", 1.0)),
                memory_mb=int(resources.get("memory_mb", 1024)),
                disk_mb=int(resources.get("disk_mb", 2048)),
                pids=int(resources.get("pids", 128)),
            ),
            commands=CommandPolicy(
                allow=tuple(str(item) for item in commands.get("allow", []) or []),
                deny=tuple(str(item) for item in commands.get("deny", []) or []),
                allow_install=bool(commands.get("allow_install", False)),
                allow_git_mutation=bool(commands.get("allow_git_mutation", False)),
                offline=bool(network.get("offline", True)),
                domain_allowlist=tuple(str(item) for item in network.get("domain_allowlist", []) or []),
            ),
            images=ContainerImagePolicy(
                allowed_images=tuple(str(item) for item in images.get("allowed", ["python:3.13-slim"]) or []),
                required_digest=(
                    str(images["required_digest"]).strip()
                    if images.get("required_digest")
                    else None
                ),
                require_digest=bool(images.get("require_digest", True)),
                scan_required=bool(images.get("scan_required", False)),
                scanner=str(images.get("scanner", "trivy")).strip().lower() or "trivy",
                denied_severities=tuple(
                    str(item).strip().upper()
                    for item in images.get("denied_severities", ["HIGH", "CRITICAL"]) or []
                ),
            ),
            audit_enabled=bool(sandbox.get("audit_enabled", True)),
            cleanup=bool(sandbox.get("cleanup", True)),
            container_workspace=str(sandbox.get("container_workspace", "/workspace")).strip()
            or "/workspace",
            container_reuse=bool(sandbox.get("container_reuse", True)),
            container_bind_mounts=tuple(
                str(item) for item in sandbox.get("bind_mounts", []) or []
            ),
            container_cache_volumes=tuple(
                str(item) for item in sandbox.get("cache_volumes", []) or []
            ),
        )

    def command_rejection(self, command: str, shell_policy: ShellPolicy) -> str | None:
        for pattern in self.commands.deny:
            if _matches_command(pattern, command):
                return f"Sandbox policy denied command by pattern `{pattern}`."
        if shell_policy.category == "install/network" and not self.commands.allow_install:
            return (
                "Sandbox policy blocked install/network command. Add an explicit policy allow "
                "or enable commands.allow_install only for trusted projects."
            )
        if shell_policy.category == "git" and _looks_like_git_mutation(command) and not self.commands.allow_git_mutation:
            return "Sandbox policy blocked git mutation command inside the sandbox."
        if self.commands.allow:
            if not any(_matches_command(pattern, command) for pattern in self.commands.allow):
                return "Sandbox policy allowlist did not include this command."
        return None

    def effective_timeout(self, shell_policy: ShellPolicy) -> int:
        if self.resources.timeout_seconds is not None:
            return min(shell_policy.timeout_seconds, self.resources.timeout_seconds)
        return shell_policy.timeout_seconds

    @property
    def process_isolated(self) -> bool:
        return self.backend in CONTAINER_BACKENDS

    def isolation_rejection(self) -> str | None:
        if self.process_isolation_required and not self.process_isolated:
            return (
                "Sandbox process isolation was required, but the resolved backend is local. "
                "Agent47 refuses to execute rather than silently downgrade isolation."
            )
        return None

    def network_rejection(self) -> str | None:
        if not self.commands.offline and self.commands.domain_allowlist:
            return (
                "Domain allowlists require an enforced egress proxy. Agent47 refuses unrestricted "
                "bridge networking rather than pretending command classification is a firewall."
            )
        return None

    def container_user_rejection(self) -> str | None:
        user = self.container_user.strip().lower()
        if not re.fullmatch(r"[1-9][0-9]{0,9}:[1-9][0-9]{0,9}", user):
            return (
                "Sandbox containers must run as an explicit numeric non-root UID:GID pair."
            )
        return None


@dataclass(frozen=True)
class SandboxHealth:
    backend: str
    available: bool
    isolation: str
    runtime: str | None = None
    cli_available: bool = False
    daemon_available: bool | None = None
    detail: str = ""
    diagnostics: tuple[str, ...] = ()

    def format_text(self) -> str:
        lines = [
            "Sandbox health:",
            f"- backend: {self.backend}",
            f"- available: {'yes' if self.available else 'no'}",
            f"- isolation: {self.isolation}",
        ]
        if self.runtime:
            lines.append(f"- runtime: {self.runtime}")
        lines.append(f"- cli available: {'yes' if self.cli_available else 'no'}")
        if self.daemon_available is not None:
            lines.append(f"- daemon available: {'yes' if self.daemon_available else 'no'}")
        if self.detail:
            lines.append(f"- detail: {self.detail}")
        if self.diagnostics:
            lines.append("- diagnostics:")
            lines.extend(f"  - {item}" for item in self.diagnostics)
        return "\n".join(lines)


@dataclass(frozen=True)
class SandboxDiskSnapshot:
    before_bytes: int
    limit_bytes: int
    after_bytes: int | None = None
    stage: str = "preflight"

    @property
    def growth_bytes(self) -> int | None:
        if self.after_bytes is None:
            return None
        return self.after_bytes - self.before_bytes

    @property
    def measured_bytes(self) -> int:
        return self.before_bytes if self.after_bytes is None else self.after_bytes

    @property
    def exceeded(self) -> bool:
        return self.measured_bytes > self.limit_bytes

    def failure_message(self) -> str:
        if self.stage == "preflight":
            return (
                "Sandbox disk usage limit exceeded before command execution: "
                f"{self.before_bytes} bytes used, limit {self.limit_bytes} bytes."
            )
        growth = self.growth_bytes
        growth_detail = "" if growth is None else f", growth {growth} bytes"
        return (
            "Sandbox disk usage limit exceeded after command execution: "
            f"{self.measured_bytes} bytes used, limit {self.limit_bytes} bytes"
            f"{growth_detail}."
        )

    def to_metadata(self) -> dict[str, Any]:
        return {
            "disk_before_bytes": self.before_bytes,
            "disk_after_bytes": self.after_bytes,
            "disk_growth_bytes": self.growth_bytes,
            "disk_limit_bytes": self.limit_bytes,
            "disk_measured_bytes": self.measured_bytes,
            "disk_budget_stage": self.stage,
            "disk_limit_exceeded": self.exceeded,
        }


class SandboxAuditLog:
    def __init__(self, workspace: Path, *, enabled: bool = True) -> None:
        self.workspace = workspace.resolve()
        self.enabled = enabled
        self.path = self.workspace / ".code-agent" / "audit" / "sandbox.jsonl"

    def record(self, event: str, **payload: Any) -> None:
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            **_redact_payload(payload),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


class SandboxDiskGuard:
    def __init__(
        self,
        workspace: Path,
        *,
        limit_bytes: int,
        audit_log: SandboxAuditLog,
    ) -> None:
        self.workspace = workspace.resolve()
        self.limit_bytes = limit_bytes
        self.audit_log = audit_log

    def preflight(self, *, command: str, backend: str) -> SandboxDiskSnapshot:
        snapshot = SandboxDiskSnapshot(
            before_bytes=workspace_disk_usage_bytes(self.workspace),
            limit_bytes=self.limit_bytes,
            stage="preflight",
        )
        self._record_snapshot("disk_usage_measured", command, backend, snapshot)
        if snapshot.exceeded:
            self._record_snapshot("disk_budget_exceeded", command, backend, snapshot)
        return snapshot

    def post_run(
        self,
        *,
        command: str,
        backend: str,
        before_bytes: int,
    ) -> SandboxDiskSnapshot:
        snapshot = SandboxDiskSnapshot(
            before_bytes=before_bytes,
            after_bytes=workspace_disk_usage_bytes(self.workspace),
            limit_bytes=self.limit_bytes,
            stage="post_run",
        )
        self._record_snapshot("disk_usage_measured", command, backend, snapshot)
        if snapshot.exceeded:
            self._record_snapshot("disk_budget_exceeded", command, backend, snapshot)
        return snapshot

    def _record_snapshot(
        self,
        event: str,
        command: str,
        backend: str,
        snapshot: SandboxDiskSnapshot,
    ) -> None:
        self.audit_log.record(
            event,
            command=command,
            backend=backend,
            **snapshot.to_metadata(),
        )


@dataclass(frozen=True)
class LocalRuntimeIsolation:
    run_id: str
    root: Path
    home: Path
    temp: Path
    cache: Path
    cleanup_enabled: bool

    @classmethod
    def create(cls, workspace: Path, *, cleanup_enabled: bool) -> "LocalRuntimeIsolation":
        run_id = uuid.uuid4().hex
        root = workspace.resolve() / LOCAL_RUNTIME_ROOT / run_id
        home = root / "home"
        temp = root / "tmp"
        cache = root / "cache"
        for path in (home, temp, cache):
            path.mkdir(parents=True, exist_ok=True)
        return cls(
            run_id=run_id,
            root=root,
            home=home,
            temp=temp,
            cache=cache,
            cleanup_enabled=cleanup_enabled,
        )

    def env(self, base_env: dict[str, str]) -> dict[str, str]:
        clean = dict(base_env)
        home = str(self.home)
        temp = str(self.temp)
        cache = str(self.cache)
        clean.update(LOCAL_ENV_OVERRIDES)
        clean.update(
            {
                "HOME": home,
                "USERPROFILE": home,
                "TMP": temp,
                "TEMP": temp,
                "TMPDIR": temp,
                "XDG_CACHE_HOME": cache,
                "PIP_CACHE_DIR": str(self.cache / "pip"),
                "NPM_CONFIG_CACHE": str(self.cache / "npm"),
                "npm_config_cache": str(self.cache / "npm"),
                "PYTHONPYCACHEPREFIX": str(self.cache / "pycache"),
            }
        )
        if os.name == "nt":
            drive = self.home.drive or str(self.home.anchor).rstrip("\\/")
            clean["HOMEDRIVE"] = drive
            try:
                clean["HOMEPATH"] = "\\" + str(self.home.relative_to(Path(drive + "\\")))
            except (ValueError, OSError):
                clean["HOMEPATH"] = str(self.home)
            clean["APPDATA"] = str(self.home / "AppData" / "Roaming")
            clean["LOCALAPPDATA"] = str(self.home / "AppData" / "Local")
        return clean

    def metadata(self) -> dict[str, Any]:
        return {
            "local_isolation": True,
            "local_runtime_id": self.run_id,
            "local_runtime_root": str(self.root),
            "local_home": str(self.home),
            "local_temp": str(self.temp),
            "local_cache": str(self.cache),
            "local_runtime_cleanup": self.cleanup_enabled,
        }

    def cleanup(self) -> None:
        if self.cleanup_enabled:
            shutil.rmtree(self.root, ignore_errors=True)


class SandboxRunner:
    def __init__(
        self,
        workspace: Path,
        policy: SandboxPolicy,
        process_supervisor: ProcessSupervisor,
        audit_log: SandboxAuditLog,
        container_manager: Any | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.policy = policy
        self.process_supervisor = process_supervisor
        self.audit_log = audit_log
        self.container_manager = container_manager

    def run_shell(
        self,
        command: str,
        *,
        timeout_seconds: int,
        env: dict[str, str],
        cancellation_token: CancellationToken | None = None,
    ) -> ShellProcessResult:
        isolation_metadata = {
            "isolation_required": self.policy.process_isolation_required,
            "process_isolated": self.policy.process_isolated,
            "sandbox_backend": self.policy.backend,
            "requested_backend": self.policy.requested_backend,
        }
        isolation_rejection = self.policy.isolation_rejection()
        if isolation_rejection:
            self.audit_log.record("isolation_rejected", reason=isolation_rejection, **isolation_metadata)
            return ShellProcessResult(
                completed=subprocess.CompletedProcess(command, 126, "", isolation_rejection),
                metadata=isolation_metadata,
            )
        disk_guard = SandboxDiskGuard(
            self.workspace,
            limit_bytes=self.policy.resources.disk_mb * 1024 * 1024,
            audit_log=self.audit_log,
        )
        preflight_disk = disk_guard.preflight(command=command, backend=self.policy.backend)
        if preflight_disk.exceeded:
            completed = subprocess.CompletedProcess(
                command,
                125,
                "",
                preflight_disk.failure_message(),
            )
            return ShellProcessResult(
                completed=completed,
                metadata={**preflight_disk.to_metadata(), **isolation_metadata},
            )
        self.audit_log.record(
            "command_started",
            command=command,
            backend=self.policy.backend,
            timeout_seconds=timeout_seconds,
            network="offline" if self.policy.commands.offline else "allow",
            resources=self.policy.resources.__dict__,
            **isolation_metadata,
        )
        start = time.monotonic()
        if self.policy.backend in {"docker", "podman", "container"}:
            if self.container_manager is not None:
                result = self._run_reusable_container(
                    command,
                    timeout_seconds=timeout_seconds,
                    env=env,
                    cancellation_token=cancellation_token,
                )
            else:
                runtime = "podman" if self.policy.backend == "podman" else "docker"
                result = self._run_container(
                    runtime,
                    command,
                    timeout_seconds=timeout_seconds,
                    env=env,
                    cancellation_token=cancellation_token,
                )
        else:
            path_rejection = local_command_path_rejection(command, self.workspace)
            if path_rejection:
                result = ShellProcessResult(
                    completed=subprocess.CompletedProcess(command, 126, "", path_rejection),
                    metadata={
                        "local_isolation": True,
                        "local_command_path_rejected": True,
                    },
                )
            else:
                runtime = LocalRuntimeIsolation.create(
                    self.workspace,
                    cleanup_enabled=self.policy.cleanup,
                )
                self.audit_log.record(
                    "local_runtime_created",
                    command=command,
                    backend=self.policy.backend,
                    **runtime.metadata(),
                )
                try:
                    local_env = runtime.env(env)
                    result = self.process_supervisor.run_shell(
                        command,
                        cwd=self.workspace,
                        timeout_seconds=timeout_seconds,
                        env=local_env,
                        cancellation_token=cancellation_token,
                    )
                    result = ShellProcessResult(
                        completed=result.completed,
                        timed_out=result.timed_out,
                        cancelled=result.cancelled,
                        output=result.output,
                        cleanup_attempted=result.cleanup_attempted,
                        events=result.events,
                        duration_ms=result.duration_ms,
                        metadata={
                            **(result.metadata or {}),
                            **runtime.metadata(),
                        },
                    )
                finally:
                    runtime.cleanup()
        elapsed_ms = round((time.monotonic() - start) * 1000, 2)
        post_run_disk = disk_guard.post_run(
            command=command,
            backend=self.policy.backend,
            before_bytes=preflight_disk.before_bytes,
        )
        result_metadata = {
            **(result.metadata or {}),
            **post_run_disk.to_metadata(),
            "original_returncode": result.completed.returncode,
            "duration_ms": result.duration_ms or elapsed_ms,
            **isolation_metadata,
        }
        if post_run_disk.exceeded:
            stderr = "\n".join(
                part for part in [result.completed.stderr, post_run_disk.failure_message()] if part
            )
            result = ShellProcessResult(
                completed=subprocess.CompletedProcess(
                    result.completed.args,
                    125,
                    result.completed.stdout,
                    stderr,
                ),
                timed_out=result.timed_out,
                cancelled=result.cancelled,
                output="\n".join(part for part in [result.output, stderr] if part),
                cleanup_attempted=result.cleanup_attempted,
                events=result.events,
                duration_ms=result.duration_ms,
                metadata=result_metadata,
            )
        else:
            result = ShellProcessResult(
                completed=result.completed,
                timed_out=result.timed_out,
                cancelled=result.cancelled,
                output=result.output,
                cleanup_attempted=result.cleanup_attempted,
                events=result.events,
                duration_ms=result.duration_ms,
                metadata=result_metadata,
            )
        self.audit_log.record(
            "command_finished",
            command=command,
            backend=self.policy.backend,
            returncode=result.completed.returncode,
            timed_out=result.timed_out,
            cancelled=result.cancelled,
            cleanup_attempted=result.cleanup_attempted,
            elapsed_ms=elapsed_ms,
            disk=result_metadata,
        )
        return result

    def _run_reusable_container(
        self,
        command: str,
        *,
        timeout_seconds: int,
        env: dict[str, str],
        cancellation_token: CancellationToken | None,
    ) -> ShellProcessResult:
        from .container_manager import ContainerError

        try:
            execution_id = uuid.uuid4().hex
            exec_dir = self.workspace / ".code-agent" / "containers" / "exec"
            exec_dir.mkdir(parents=True, exist_ok=True)
            pid_file = exec_dir / f"{execution_id}.pid"
            pid_file.touch()
            try:
                os.chmod(pid_file, 0o666)
            except OSError:
                pass
            command_argv = shlex.split(command, posix=True)
            if not command_argv:
                raise ContainerError("Sandbox command cannot be empty.")
            exec_argv = self.container_manager.exec_argv(
                command_argv,
                cwd=self.workspace,
                env=env,
                interactive=False,
                execution_id=execution_id,
            )
            result = self.process_supervisor.run_shell(
                command,
                cwd=self.workspace,
                timeout_seconds=timeout_seconds,
                env=_container_runtime_env(),
                cancellation_token=cancellation_token,
                argv=exec_argv,
            )
            container_cleanup = False
            if result.timed_out or result.cancelled:
                container_cleanup = self.container_manager.terminate_exec(execution_id)
            else:
                self.container_manager.cleanup_exec(execution_id)
            return ShellProcessResult(
                completed=result.completed,
                timed_out=result.timed_out,
                cancelled=result.cancelled,
                output=result.output,
                cleanup_attempted=result.cleanup_attempted or container_cleanup,
                metadata={
                    **(result.metadata or {}),
                    "container_reused": True,
                    "container": self.container_manager.status(),
                    "container_exec_cleanup": container_cleanup,
                },
                events=result.events,
                duration_ms=result.duration_ms,
            )
        except (ContainerError, OSError, ValueError) as exc:
            return ShellProcessResult(
                completed=subprocess.CompletedProcess(command, 126, "", str(exc)),
                metadata={"container_reused": False},
            )

    def _run_container(
        self,
        runtime: str,
        command: str,
        *,
        timeout_seconds: int,
        env: dict[str, str],
        cancellation_token: CancellationToken | None,
    ) -> ShellProcessResult:
        runtime_path = resolve_container_runtime(runtime)
        if runtime_path is None:
            completed = subprocess.CompletedProcess(
                [runtime],
                127,
                "",
                f"{runtime} is not available. Install {runtime} and run `code-agent sandbox health`.",
            )
            return ShellProcessResult(completed=completed)
        daemon_ok, daemon_detail = container_daemon_available(runtime_path)
        if not daemon_ok:
            completed = subprocess.CompletedProcess(
                [runtime_path],
                125,
                "",
                "Container runtime daemon is not available. "
                + (daemon_detail or "Run `code-agent sandbox health` for diagnostics."),
            )
            return ShellProcessResult(completed=completed)
        security, security_detail = inspect_container_runtime_security(runtime_path, runtime)
        if security is None:
            completed = subprocess.CompletedProcess(
                [runtime_path],
                126,
                "",
                security_detail,
            )
            return ShellProcessResult(completed=completed)
        security_rejection = runtime_security_rejection(self.policy, security)
        if security_rejection:
            completed = subprocess.CompletedProcess(
                [runtime_path],
                126,
                "",
                security_rejection,
            )
            return ShellProcessResult(completed=completed)
        network_rejection = self.policy.network_rejection()
        if network_rejection:
            completed = subprocess.CompletedProcess(
                [runtime_path],
                126,
                "",
                network_rejection,
            )
            return ShellProcessResult(completed=completed)
        user_rejection = self.policy.container_user_rejection()
        if user_rejection:
            completed = subprocess.CompletedProcess(
                [runtime_path],
                126,
                "",
                user_rejection,
            )
            return ShellProcessResult(completed=completed)
        resolved_image, image_detail = resolve_container_image_reference(
            runtime_path,
            self.policy.container_image,
            self.policy.images,
        )
        if resolved_image is None:
            completed = subprocess.CompletedProcess(
                [runtime_path],
                126,
                "",
                image_detail,
            )
            return ShellProcessResult(completed=completed)

        network = "none" if self.policy.commands.offline else "bridge"
        container_id = uuid.uuid4().hex
        container_name = f"agent47-{container_id}"
        lifecycle_dir = Path(tempfile.mkdtemp(prefix="agent47-container-"))
        cidfile = lifecycle_dir / f"{container_id}.cid"
        container_command = [
            runtime_path,
            "run",
            "--rm",
            "--pull",
            "never",
            "--name",
            container_name,
            "--cidfile",
            str(cidfile),
            "--label",
            "io.agent47.sandbox=true",
            "--label",
            f"io.agent47.run={container_id}",
            "--network",
            network,
            "--init",
            "--ipc",
            "none",
            "--user",
            self.policy.container_user,
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            "--cpus",
            str(self.policy.resources.cpus),
            "--memory",
            f"{self.policy.resources.memory_mb}m",
            "--pids-limit",
            str(self.policy.resources.pids),
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=256m",
            "-v",
            f"{self.workspace}:/workspace:rw",
            "-w",
            "/workspace",
        ]
        if runtime == "podman":
            container_command.extend(["--userns", "keep-id"])
        if runtime == "docker" and security.apparmor:
            container_command.extend(["--security-opt", "apparmor=docker-default"])
        container_env = _container_env(env)
        for key, value in container_env.items():
            container_command.extend(["-e", f"{key}={value}"])
        try:
            command_argv = shlex.split(command, posix=True)
        except ValueError as exc:
            completed = subprocess.CompletedProcess(
                [runtime_path],
                126,
                "",
                f"Sandbox command could not be parsed safely: {exc}",
            )
            return ShellProcessResult(completed=completed)
        if not command_argv:
            completed = subprocess.CompletedProcess(
                [runtime_path],
                126,
                "",
                "Sandbox command cannot be empty.",
            )
            return ShellProcessResult(completed=completed)
        container_command.extend([resolved_image, *command_argv])

        self.audit_log.record(
            "container_launching",
            backend=self.policy.backend,
            container_name=container_name,
            image=resolved_image,
            network=network,
            rootless=security.rootless,
            seccomp=security.seccomp,
            container_user=self.policy.container_user,
        )
        result: ShellProcessResult
        try:
            process = subprocess.Popen(
                container_command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_container_runtime_env(),
                creationflags=windows_creation_flags(),
                start_new_session=os.name != "nt",
            )
            result = _wait_for_container_process(
                process,
                container_command,
                timeout_seconds=timeout_seconds,
                cancellation_token=cancellation_token,
                cwd=self.workspace,
                env=_container_runtime_env(),
            )
        except OSError as exc:
            result = ShellProcessResult(
                completed=subprocess.CompletedProcess(
                    container_command,
                    125,
                    "",
                    f"Container launch failed: {exc}",
                )
            )
        finally:
            cleanup_ok, cleanup_detail = cleanup_container(
                runtime_path,
                container_name,
                cidfile,
            )
            self.audit_log.record(
                "container_cleanup",
                backend=self.policy.backend,
                container_name=container_name,
                cleanup_ok=cleanup_ok,
                detail=cleanup_detail,
            )
        return ShellProcessResult(
            completed=result.completed,
            timed_out=result.timed_out,
            cancelled=result.cancelled,
            output=result.output,
            cleanup_attempted=True,
            events=result.events,
            duration_ms=result.duration_ms,
            metadata={
                **(result.metadata or {}),
                "container_name": container_name,
                "container_image": resolved_image,
                "container_rootless": security.rootless,
                "container_seccomp": security.seccomp,
                "container_apparmor": security.apparmor,
                "container_user": self.policy.container_user,
                "container_cleanup_ok": cleanup_ok,
                "container_cleanup_detail": cleanup_detail,
            },
        )


def _wait_for_container_process(
    process,
    container_command: list[str],
    *,
    timeout_seconds: int,
    cancellation_token: CancellationToken | None,
    cwd: Path,
    env: dict[str, str],
) -> ShellProcessResult:
    if all(
        stream is not None and callable(getattr(stream, "readline", None))
        for stream in (getattr(process, "stdout", None), getattr(process, "stderr", None))
    ):
        started = time.monotonic()
        return capture_process_streams(
            process,
            " ".join(container_command),
            deadline=started + timeout_seconds,
            started=started,
            cwd=cwd,
            env=env,
            cancellation_token=cancellation_token,
            poll_seconds=0.2,
        )
    deadline = time.monotonic() + timeout_seconds
    while True:
        if cancellation_token is not None and cancellation_token.cancelled:
            process.kill()
            stdout, stderr = process.communicate()
            return ShellProcessResult(
                completed=subprocess.CompletedProcess(
                    container_command,
                    process.returncode,
                    stdout,
                    stderr,
                ),
                cancelled=True,
                output="\n".join(part for part in [stdout, stderr] if part),
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.kill()
            stdout, stderr = process.communicate()
            return ShellProcessResult(
                completed=subprocess.CompletedProcess(
                    container_command,
                    process.returncode,
                    stdout,
                    stderr,
                ),
                timed_out=True,
                output="\n".join(part for part in [stdout, stderr] if part),
            )
        try:
            stdout, stderr = process.communicate(timeout=min(0.2, remaining))
            return ShellProcessResult(
                completed=subprocess.CompletedProcess(
                    container_command,
                    process.returncode,
                    stdout,
                    stderr,
                ),
            )
        except subprocess.TimeoutExpired:
            continue


def cleanup_container(
    runtime_path: str,
    container_name: str,
    cidfile: Path,
    *,
    timeout_seconds: int = 10,
) -> tuple[bool, str]:
    target = container_name
    try:
        if cidfile.is_file():
            recorded = cidfile.read_text(encoding="utf-8").strip()
            if re.fullmatch(r"[a-fA-F0-9]{12,64}", recorded):
                target = recorded
        completed = subprocess.run(
            [runtime_path, "rm", "--force", target],
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=_container_runtime_env(),
        )
        output = "\n".join(
            part for part in [completed.stdout, completed.stderr] if part
        ).strip()
        missing = "no such" in output.lower() or "does not exist" in output.lower()
        return completed.returncode == 0 or missing, output
    except subprocess.TimeoutExpired:
        return False, f"Forced cleanup timed out after {timeout_seconds}s."
    except OSError as exc:
        return False, f"Forced cleanup failed: {exc}."
    finally:
        cidfile.unlink(missing_ok=True)
        if cidfile.parent.name.startswith("agent47-container-"):
            with contextlib.suppress(OSError):
                cidfile.parent.rmdir()


def sandbox_health(policy: SandboxPolicy) -> SandboxHealth:
    if policy.backend in CONTAINER_BACKENDS:
        runtime = "podman" if policy.backend == "podman" else "docker"
        runtime_path = resolve_container_runtime(runtime)
        cli_available = runtime_path is not None
        daemon_available = False
        diagnostics: list[str] = []
        image_ok = False
        runtime_security_ok = False
        network_ok = policy.network_rejection() is None
        if not network_ok:
            diagnostics.append(policy.network_rejection() or "Invalid sandbox network policy.")
        user_ok = policy.container_user_rejection() is None
        if not user_ok:
            diagnostics.append(
                policy.container_user_rejection() or "Invalid sandbox container user policy."
            )
        if cli_available:
            daemon_available, daemon_detail = container_daemon_available(runtime_path)
            if daemon_detail:
                diagnostics.append(daemon_detail)
            if daemon_available:
                security, security_detail = inspect_container_runtime_security(
                    runtime_path,
                    runtime,
                )
                if security_detail:
                    diagnostics.append(security_detail)
                if security is not None:
                    security_rejection = runtime_security_rejection(policy, security)
                    if security_rejection:
                        diagnostics.append(security_rejection)
                    else:
                        runtime_security_ok = True
                        diagnostics.append(
                            "Runtime security verified: "
                            f"rootless={'yes' if security.rootless else 'no'}, "
                            f"seccomp={'yes' if security.seccomp else 'no'}, "
                            f"apparmor={'yes' if security.apparmor else 'no'}, "
                            f"selinux={'yes' if security.selinux else 'no'}."
                        )
                image_ok, image_detail = validate_container_image_policy(
                    runtime_path,
                    policy.container_image,
                    policy.images,
                )
                if image_detail:
                    diagnostics.append(image_detail)
        else:
            diagnostics.append(f"{runtime} executable was not found on PATH or common install paths.")
        if runtime == "docker" and platform.system().lower() == "windows":
            virtualization_detail = windows_virtualization_diagnostic()
            if virtualization_detail:
                diagnostics.append(virtualization_detail)
        available = (
            cli_available
            and daemon_available
            and runtime_security_ok
            and image_ok
            and network_ok
            and user_ok
        )
        return SandboxHealth(
            backend=policy.backend,
            available=available,
            runtime=runtime_path or runtime,
            cli_available=cli_available,
            daemon_available=daemon_available,
            isolation="container process, filesystem, env, network, pid, CPU, and memory boundaries",
            detail=(
                "Container backend uses a verified rootless runtime and seccomp profile, read-only "
                "rootfs, isolated env, offline network by default, resource limits, validated "
                "image policy, and a writable copied-workspace mount."
                if available
                else "Container backend is configured but not ready. See diagnostics."
            ),
            diagnostics=tuple(diagnostics),
        )
    return SandboxHealth(
        backend=policy.backend,
        available=True,
        cli_available=True,
        daemon_available=None,
        isolation="hardened local subprocess policy, not an OS security boundary",
        detail=(
            "Local backend enforces workspace paths, per-command private HOME/TMP/cache "
            "directories, env scrubbing, policy checks, timeouts, process-tree cleanup, "
            "audit logs, and sandbox copy-on-write promotion."
        ),
    )


def resolve_sandbox_policy(
    workspace: Path,
    *,
    backend: str,
    container_image: str,
    require_process_isolation: bool,
) -> SandboxPolicy:
    requested = backend.strip().lower() or "auto"
    if requested not in {"auto", "local", *CONTAINER_BACKENDS}:
        raise SandboxIsolationError(
            "Sandbox backend must be one of: auto, local, docker, podman, container."
        )
    candidates = (
        ["docker", "podman"]
        if requested == "auto" and require_process_isolation
        else ["local" if requested == "auto" else requested]
    )
    failures: list[str] = []
    attempted: set[tuple[str, str]] = set()
    for candidate in candidates:
        policy = SandboxPolicy.from_workspace(
            workspace,
            backend=candidate,
            container_image=container_image,
        )
        resolved_backend = "docker" if policy.backend == "container" else policy.backend
        policy = replace(
            policy,
            backend=resolved_backend,
            process_isolation_required=require_process_isolation,
            requested_backend=requested,
        )
        attempt = (policy.backend, policy.container_image)
        if attempt in attempted:
            continue
        attempted.add(attempt)
        rejection = policy.isolation_rejection()
        if rejection:
            failures.append(rejection)
            continue
        if require_process_isolation:
            health = sandbox_health(policy)
            if not health.available:
                detail = "; ".join(health.diagnostics) or health.detail
                failures.append(f"{policy.backend}: {detail}")
                continue
        return policy
    detail = " | ".join(failures) or "No supported container backend was available."
    raise SandboxIsolationError(
        "Sandbox mode requires real process isolation and will not fall back to local execution. "
        f"Requested backend: {requested}. {detail}"
    )


def resolve_container_runtime(runtime: str) -> str | None:
    found = shutil.which(runtime)
    if found:
        return found
    candidates = []
    if runtime == "docker":
        candidates.extend(
            [
                Path("C:/Program Files/Docker/Docker/resources/bin/docker.exe"),
            ]
        )
    elif runtime == "podman":
        candidates.extend(
            [
                Path("C:/Program Files/RedHat/Podman/podman.exe"),
                Path("C:/Program Files/Podman/podman.exe"),
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def container_daemon_available(runtime_path: str, *, timeout_seconds: int = 5) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            [runtime_path, "info"],
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=_container_runtime_env(),
        )
    except subprocess.TimeoutExpired:
        return False, f"{runtime_path} info timed out after {timeout_seconds}s."
    except OSError as exc:
        return False, f"{runtime_path} info failed: {exc}."
    if completed.returncode == 0:
        return True, ""
    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
    return False, output or f"{runtime_path} info exited with {completed.returncode}."


def inspect_container_runtime_security(
    runtime_path: str,
    runtime: str,
    *,
    timeout_seconds: int = 5,
) -> tuple[ContainerRuntimeSecurity | None, str]:
    command = (
        [runtime_path, "info", "--format", "{{json .SecurityOptions}}"]
        if runtime == "docker"
        else [runtime_path, "info", "--format", "json"]
    )
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=_container_runtime_env(),
        )
    except subprocess.TimeoutExpired:
        return None, f"{runtime} security inspection timed out after {timeout_seconds}s."
    except OSError as exc:
        return None, f"{runtime} security inspection failed: {exc}."
    if completed.returncode != 0:
        output = "\n".join(
            part for part in [completed.stdout, completed.stderr] if part
        ).strip()
        return None, output or f"{runtime} security inspection exited with {completed.returncode}."
    try:
        payload = json.loads(completed.stdout.strip() or "null")
    except json.JSONDecodeError:
        return None, f"{runtime} security inspection returned invalid JSON."

    tokens = tuple(_security_tokens(payload))
    lowered = " ".join(tokens).lower()
    if runtime == "docker":
        security = ContainerRuntimeSecurity(
            rootless="rootless" in lowered,
            seccomp="seccomp" in lowered,
            apparmor="apparmor" in lowered,
            selinux="selinux" in lowered,
            detail=", ".join(tokens),
        )
    else:
        host = payload.get("host", {}) if isinstance(payload, dict) else {}
        security_payload = host.get("security", {}) if isinstance(host, dict) else {}
        security = ContainerRuntimeSecurity(
            rootless=bool(
                host.get("rootless", security_payload.get("rootless", False))
                if isinstance(security_payload, dict)
                else host.get("rootless", False)
            ),
            seccomp=_security_feature_enabled(tokens, "seccomp"),
            apparmor=_security_feature_enabled(tokens, "apparmor"),
            selinux=_security_feature_enabled(tokens, "selinux"),
            detail=", ".join(tokens[:24]),
        )
    return security, ""


def runtime_security_rejection(
    policy: SandboxPolicy,
    security: ContainerRuntimeSecurity,
) -> str | None:
    failures = []
    if policy.rootless_required and not security.rootless:
        failures.append("rootless runtime is required but was not detected")
    if policy.seccomp_required and not security.seccomp:
        failures.append("seccomp enforcement is required but was not detected")
    if failures:
        return "; ".join(failures) + "."
    return None


def inspect_container_image(
    runtime_path: str,
    image: str,
    *,
    timeout_seconds: int = 10,
) -> tuple[bool, tuple[str, ...], str]:
    try:
        completed = subprocess.run(
            [
                runtime_path,
                "image",
                "inspect",
                image,
                "--format",
                "{{json .RepoDigests}}",
            ],
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=_container_runtime_env(),
        )
    except subprocess.TimeoutExpired:
        return False, (), f"Image inspect for {image} timed out after {timeout_seconds}s."
    except OSError as exc:
        return False, (), f"Image inspect for {image} failed: {exc}."
    if completed.returncode != 0:
        output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
        return False, (), output or f"Image not available locally: {image}."
    try:
        raw = json.loads(completed.stdout.strip() or "[]")
    except json.JSONDecodeError:
        return False, (), f"Image inspect for {image} returned invalid digest metadata."
    digests = tuple(str(item) for item in raw if isinstance(item, str))
    return True, digests, ""


def validate_container_image_policy(
    runtime_path: str,
    image: str,
    policy: ContainerImagePolicy,
) -> tuple[bool, str]:
    image_name, image_digest = split_container_image_digest(image)
    if not any(
        fnmatch.fnmatch(candidate, pattern)
        for candidate in {image, image_name}
        for pattern in policy.allowed_images
    ):
        return False, f"Container image {image} is not allowed by sandbox image policy."
    expected_digest = normalized_container_digest(policy.required_digest) or image_digest
    if policy.required_digest and image_digest and image_digest != expected_digest:
        return False, (
            f"Container image {image} digest conflicts with policy. "
            f"Expected {expected_digest}; image reference pins {image_digest}."
        )
    available, digests, detail = inspect_container_image(runtime_path, image)
    if not available:
        return False, (
            f"Container image {image} is not available locally. "
            "Pull and review the image explicitly before sandbox execution. "
            + detail
        )
    if not expected_digest:
        if policy.require_digest and not digests:
            return False, (
                f"Container image {image} has no immutable repository digest. "
                "Sandbox image policy requires digest-addressable images."
            )
        if policy.require_digest:
            return validate_container_image_scan(
                digests[0],
                policy,
                f"Container image {image} has an immutable local repository digest.",
            )
        return validate_container_image_scan(
            image,
            policy,
            f"Container image {image} is locally available.",
        )
    matched = any(container_digest_matches(item, expected_digest) for item in digests)
    if matched:
        source = "policy" if policy.required_digest else "image reference"
        return validate_container_image_scan(
            f"{image_name}@{expected_digest}",
            policy,
            f"Container image {image} digest matches {source} pin.",
        )
    digest_detail = ", ".join(digests) if digests else "<no repo digests>"
    return False, (
        f"Container image {image} digest does not match policy. "
        f"Expected {expected_digest}; local digests: {digest_detail}."
    )


def validate_container_image_scan(
    image: str,
    policy: ContainerImagePolicy,
    success_detail: str,
    *,
    timeout_seconds: int = 300,
) -> tuple[bool, str]:
    if not policy.scan_required:
        return True, success_detail
    if policy.scanner != "trivy":
        return False, f"Unsupported container image scanner: {policy.scanner}."
    scanner_path = shutil.which("trivy")
    if scanner_path is None:
        return False, "Container image policy requires Trivy, but `trivy` was not found on PATH."
    severities = ",".join(policy.denied_severities)
    command = [
        scanner_path,
        "image",
        "--scanners",
        "vuln",
        "--severity",
        severities,
        "--exit-code",
        "1",
        "--no-progress",
        image,
    ]
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=_scanner_env(),
        )
    except subprocess.TimeoutExpired:
        return False, f"Trivy image scan timed out after {timeout_seconds}s."
    except OSError as exc:
        return False, f"Trivy image scan failed: {exc}."
    if completed.returncode == 0:
        return True, success_detail + " Trivy vulnerability policy passed."
    output = "\n".join(
        part for part in [completed.stdout, completed.stderr] if part
    ).strip()
    return False, (
        f"Container image failed Trivy vulnerability policy for severities {severities}. "
        + (output[-2000:] if output else f"Trivy exited with {completed.returncode}.")
    )


def resolve_container_image_reference(
    runtime_path: str,
    image: str,
    policy: ContainerImagePolicy,
) -> tuple[str | None, str]:
    valid, detail = validate_container_image_policy(runtime_path, image, policy)
    if not valid:
        return None, detail
    image_name, image_digest = split_container_image_digest(image)
    if image_digest:
        return image, detail
    available, digests, inspect_detail = inspect_container_image(runtime_path, image)
    if not available:
        return None, inspect_detail
    expected = normalized_container_digest(policy.required_digest)
    if expected:
        return f"{image_name}@{expected}", detail
    if policy.require_digest:
        matching = next(
            (
                digest
                for digest in digests
                if digest.split("@", 1)[0].split(":", 1)[0]
                == image_name.split(":", 1)[0]
            ),
            digests[0] if digests else None,
        )
        if matching:
            return matching, detail
        return None, f"Container image {image} could not be resolved to an immutable digest."
    return image, detail


def split_container_image_digest(image: str) -> tuple[str, str | None]:
    if "@sha256:" not in image:
        return image, None
    name, digest = image.rsplit("@", 1)
    return name, normalized_container_digest(digest)


def normalized_container_digest(value: str | None) -> str | None:
    if not value:
        return None
    digest = value.strip()
    if not digest:
        return None
    if digest.startswith("@"):
        digest = digest[1:]
    if digest.startswith("sha256:"):
        return digest
    if len(digest) == 64 and all(char in "0123456789abcdefABCDEF" for char in digest):
        return "sha256:" + digest.lower()
    return digest


def container_digest_matches(actual: str, expected: str) -> bool:
    normalized_expected = normalized_container_digest(expected) or expected
    normalized_actual = actual.strip()
    if normalized_actual.startswith("sha256:"):
        return normalized_actual == normalized_expected
    return normalized_actual.endswith("@" + normalized_expected) or normalized_actual.endswith(
        normalized_expected
    )


def windows_virtualization_diagnostic(*, timeout_seconds: int = 5) -> str:
    try:
        completed = subprocess.run(
            ["systeminfo"],
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    lines = []
    for line in completed.stdout.splitlines():
        normalized = " ".join(line.split())
        lowered = normalized.lower()
        if (
            "virtualization enabled in firmware" in lowered
            or "vm monitor mode extensions" in lowered
            or "hyper-v requirements" in lowered
            or "a hypervisor has been detected" in lowered
        ):
            lines.append(normalized)
    if not lines:
        return ""
    return "Windows virtualization: " + "; ".join(lines[:6])


def _container_env(env: dict[str, str]) -> dict[str, str]:
    clean: dict[str, str] = {
        "PATH": "/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin",
        "AGENT47_SANDBOXED_SHELL": "1",
        "HOME": "/tmp",
        "TMPDIR": "/tmp",
        "XDG_CACHE_HOME": "/tmp/.cache",
    }
    allowed = {
        "NO_COLOR",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUNBUFFERED",
        "TERM",
    }
    for key, value in env.items():
        upper = key.upper()
        if upper in allowed:
            clean[key] = value
    return clean


def _container_runtime_env() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() in CONTAINER_RUNTIME_ENV_KEYS
    }


def _scanner_env() -> dict[str, str]:
    blocked_fragments = ("API_KEY", "PASSWORD", "SECRET", "TOKEN")
    return {
        key: value
        for key, value in os.environ.items()
        if not any(fragment in key.upper() for fragment in blocked_fragments)
    }


def _redact_payload(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {str(key): _redact_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_payload(item) for item in value)
    return value


def validate_workspace_boundary(path: Path, workspace: Path) -> None:
    resolved = path.resolve()
    root = workspace.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Path escapes workspace: {path}")
    for parent in [resolved, *resolved.parents]:
        if parent == root.parent:
            break
        if parent.is_symlink():
            raise ValueError(f"Refusing symlinked workspace path: {path}")


def workspace_disk_usage_bytes(workspace: Path) -> int:
    total = 0
    for path in workspace.rglob("*"):
        if any(part in DISK_USAGE_IGNORES for part in path.relative_to(workspace).parts):
            continue
        if path.is_file() and not path.is_symlink():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def local_command_path_rejection(command: str, workspace: Path) -> str | None:
    for raw_token in _command_path_tokens(command):
        token = _clean_command_path_token(raw_token)
        if not token or _looks_like_option(token):
            continue
        if not _looks_like_absolute_path(token):
            continue
        path = Path(token)
        try:
            validate_workspace_boundary(path, workspace)
        except (OSError, ValueError):
            return (
                "Sandbox local backend blocked an absolute path outside the workspace: "
                f"{raw_token}. Use a workspace-relative path or the container backend."
            )
    return None


def _command_path_tokens(command: str) -> tuple[str, ...]:
    try:
        return tuple(shlex.split(command, posix=False))
    except ValueError:
        return tuple(command.split())


def _clean_command_path_token(token: str) -> str:
    cleaned = token.strip().strip("'\"")
    cleaned = cleaned.rstrip(".,;)")
    if cleaned.startswith(("'", '"')):
        cleaned = cleaned[1:]
    return cleaned


def _looks_like_option(token: str) -> bool:
    return token.startswith("-") and not _looks_like_absolute_path(token)


def _looks_like_absolute_path(token: str) -> bool:
    if re.match(r"^[A-Za-z]:[\\/]", token):
        return True
    if token.startswith("\\\\"):
        return True
    return token.startswith("/")


def _matches_command(pattern: str, command: str) -> bool:
    normalized = " ".join(command.strip().split())
    return fnmatch.fnmatch(normalized, pattern) or normalized.startswith(pattern.rstrip("*"))


def _looks_like_git_mutation(command: str) -> bool:
    lowered = command.lower()
    return any(
        token in lowered
        for token in [
            "git add",
            "git commit",
            "git merge",
            "git rebase",
            "git push",
            "git reset",
            "git clean",
            "git checkout",
            "git switch",
        ]
    )


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _security_tokens(value: object, prefix: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from _security_tokens(item, path)
    elif isinstance(value, list):
        for item in value:
            yield from _security_tokens(item, prefix)
    elif prefix:
        yield f"{prefix}={value}"
    else:
        yield str(value)


def _security_feature_enabled(tokens: tuple[str, ...], feature: str) -> bool:
    matches = [token.lower() for token in tokens if feature in token.lower()]
    if not matches:
        return False
    return not all(token.endswith("=false") or token.endswith("=disabled") for token in matches)


def _optional_int(value: object) -> int | None:
    if value in {None, ""}:
        return None
    return int(value)
