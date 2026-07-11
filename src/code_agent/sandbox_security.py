from __future__ import annotations

import fnmatch
import json
import platform
import shutil
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .processes import CancellationToken, ProcessSupervisor, ShellProcessResult
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


@dataclass(frozen=True)
class SandboxPolicy:
    backend: str = "local"
    container_image: str = "python:3.13-slim"
    rootless_required: bool = True
    resources: SandboxResourceLimits = field(default_factory=SandboxResourceLimits)
    commands: CommandPolicy = field(default_factory=CommandPolicy)
    images: ContainerImagePolicy = field(default_factory=ContainerImagePolicy)
    audit_enabled: bool = True
    cleanup: bool = True

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
            ),
            audit_enabled=bool(sandbox.get("audit_enabled", True)),
            cleanup=bool(sandbox.get("cleanup", True)),
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


class SandboxRunner:
    def __init__(
        self,
        workspace: Path,
        policy: SandboxPolicy,
        process_supervisor: ProcessSupervisor,
        audit_log: SandboxAuditLog,
    ) -> None:
        self.workspace = workspace.resolve()
        self.policy = policy
        self.process_supervisor = process_supervisor
        self.audit_log = audit_log

    def run_shell(
        self,
        command: str,
        *,
        timeout_seconds: int,
        env: dict[str, str],
        cancellation_token: CancellationToken | None = None,
    ) -> ShellProcessResult:
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
                metadata=preflight_disk.to_metadata(),
            )
        self.audit_log.record(
            "command_started",
            command=command,
            backend=self.policy.backend,
            timeout_seconds=timeout_seconds,
            network="offline" if self.policy.commands.offline else "allow",
            resources=self.policy.resources.__dict__,
        )
        start = time.monotonic()
        if self.policy.backend in {"docker", "podman", "container"}:
            runtime = "podman" if self.policy.backend == "podman" else "docker"
            result = self._run_container(
                runtime,
                command,
                timeout_seconds=timeout_seconds,
                env=env,
                cancellation_token=cancellation_token,
            )
        else:
            result = self.process_supervisor.run_shell(
                command,
                cwd=self.workspace,
                timeout_seconds=timeout_seconds,
                env=env,
                cancellation_token=cancellation_token,
            )
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
                metadata=result_metadata,
            )
        else:
            result = ShellProcessResult(
                completed=result.completed,
                timed_out=result.timed_out,
                cancelled=result.cancelled,
                output=result.output,
                cleanup_attempted=result.cleanup_attempted,
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
                f"{runtime} is not available. Install {runtime} or set AGENT_SANDBOX_BACKEND=local.",
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
        image_ok, image_detail = validate_container_image_policy(
            runtime_path,
            self.policy.container_image,
            self.policy.images,
        )
        if not image_ok:
            completed = subprocess.CompletedProcess(
                [runtime_path],
                126,
                "",
                image_detail,
            )
            return ShellProcessResult(completed=completed)

        network = "none" if self.policy.commands.offline else "bridge"
        container_command = [
            runtime_path,
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            network,
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
        container_env = _container_env(env)
        for key, value in container_env.items():
            container_command.extend(["-e", f"{key}={value}"])
        container_command.extend([self.policy.container_image, "/bin/sh", "-lc", command])

        process = subprocess.Popen(
            container_command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={},
        )
        deadline = time.monotonic() + timeout_seconds
        while True:
            if cancellation_token is not None and cancellation_token.cancelled:
                process.kill()
                stdout, stderr = process.communicate()
                return ShellProcessResult(
                    completed=subprocess.CompletedProcess(container_command, process.returncode, stdout, stderr),
                    cancelled=True,
                    output="\n".join(part for part in [stdout, stderr] if part),
                    cleanup_attempted=True,
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                stdout, stderr = process.communicate()
                return ShellProcessResult(
                    completed=subprocess.CompletedProcess(container_command, process.returncode, stdout, stderr),
                    timed_out=True,
                    output="\n".join(part for part in [stdout, stderr] if part),
                    cleanup_attempted=True,
                )
            try:
                stdout, stderr = process.communicate(timeout=min(0.2, remaining))
                return ShellProcessResult(
                    completed=subprocess.CompletedProcess(container_command, process.returncode, stdout, stderr),
                )
            except subprocess.TimeoutExpired:
                continue


def sandbox_health(policy: SandboxPolicy) -> SandboxHealth:
    if policy.backend in {"docker", "podman", "container"}:
        runtime = "podman" if policy.backend == "podman" else "docker"
        runtime_path = resolve_container_runtime(runtime)
        cli_available = runtime_path is not None
        daemon_available = False
        diagnostics: list[str] = []
        image_ok = False
        if cli_available:
            daemon_available, daemon_detail = container_daemon_available(runtime_path)
            if daemon_detail:
                diagnostics.append(daemon_detail)
            if daemon_available:
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
        available = cli_available and daemon_available and image_ok
        return SandboxHealth(
            backend=policy.backend,
            available=available,
            runtime=runtime_path or runtime,
            cli_available=cli_available,
            daemon_available=daemon_available,
            isolation="container process, filesystem, env, network, pid, CPU, and memory boundaries",
            detail=(
                "Container backend uses read-only rootfs, isolated env, offline network by default, "
                "resource limits, validated image policy, and a writable workspace mount."
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
            "Local backend enforces workspace paths, env scrubbing, policy checks, timeouts, "
            "process-tree cleanup, audit logs, and sandbox copy-on-write promotion."
        ),
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
        )
    except subprocess.TimeoutExpired:
        return False, f"{runtime_path} info timed out after {timeout_seconds}s."
    except OSError as exc:
        return False, f"{runtime_path} info failed: {exc}."
    if completed.returncode == 0:
        return True, ""
    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
    return False, output or f"{runtime_path} info exited with {completed.returncode}."


def inspect_container_image(
    runtime_path: str,
    image: str,
    *,
    timeout_seconds: int = 10,
) -> tuple[bool, tuple[str, ...], str]:
    try:
        completed = subprocess.run(
            [runtime_path, "image", "inspect", image, "--format", "{{json .RepoDigests}}"],
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
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
    if not any(fnmatch.fnmatch(image, pattern) for pattern in policy.allowed_images):
        return False, f"Container image {image} is not allowed by sandbox image policy."
    available, digests, detail = inspect_container_image(runtime_path, image)
    if not available:
        return False, (
            f"Container image {image} is not available locally. "
            "Pull and review the image explicitly before sandbox execution. "
            + detail
        )
    if not policy.required_digest:
        return True, f"Container image {image} is locally available."
    expected = policy.required_digest
    if expected.startswith("sha256:"):
        matched = any(item.endswith("@" + expected) or item.endswith(expected) for item in digests)
    else:
        matched = expected in digests
    if matched:
        return True, f"Container image {image} digest matches policy."
    digest_detail = ", ".join(digests) if digests else "<no repo digests>"
    return False, (
        f"Container image {image} digest does not match policy. "
        f"Expected {expected}; local digests: {digest_detail}."
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


def _optional_int(value: object) -> int | None:
    if value in {None, ""}:
        return None
    return int(value)
