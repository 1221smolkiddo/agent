from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .processes import windows_creation_flags


CONTAINER_STATE_ROOT = Path(".code-agent/containers")
CONTAINER_LABEL = "io.agent47.workspace"


class ContainerError(RuntimeError):
    pass


@dataclass(frozen=True)
class DetectedEnvironment:
    source: str
    image: str
    languages: tuple[str, ...]
    dockerfile: str | None = None
    compose_file: str | None = None
    compose_service: str | None = None


@dataclass(frozen=True)
class ContainerRecord:
    workspace: str
    workspace_id: str
    runtime: str
    runtime_path: str
    name: str
    image: str
    image_source: str
    container_workspace: str
    cache_volume: str
    network: str
    config_sha256: str
    container_id: str
    status: str
    created_at: str
    updated_at: str


class ContainerManager:
    """Owns one hardened, reusable execution container for a workspace."""

    def __init__(self, workspace: Path, policy: Any, audit_log: Any | None = None) -> None:
        self.workspace = workspace.resolve()
        self.policy = policy
        self.audit_log = audit_log
        self.root = self.workspace / CONTAINER_STATE_ROOT
        self.state_path = self.root / "state.json"
        self._lock = threading.RLock()
        self._record: ContainerRecord | None = None

    @property
    def enabled(self) -> bool:
        return str(self.policy.backend) in {"docker", "podman", "container"}

    @property
    def container_workspace(self) -> str:
        value = os.environ.get("AGENT_CONTAINER_WORKSPACE") or str(
            getattr(self.policy, "container_workspace", "/workspace")
        ).strip()
        if not value.startswith("/") or ".." in Path(value).parts:
            raise ContainerError("Container workspace must be an absolute normalized POSIX path.")
        return value.rstrip("/") or "/workspace"

    def detect_environment(self) -> DetectedEnvironment:
        configured = str(self.policy.container_image).strip()
        devcontainer = self.workspace / ".devcontainer" / "devcontainer.json"
        if devcontainer.is_file():
            try:
                payload = json.loads(_strip_json_comments(devcontainer.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                payload = {}
            image = str(payload.get("image") or configured).strip()
            dockerfile = payload.get("dockerFile") or _dict(payload.get("build")).get("dockerfile")
            return DetectedEnvironment(
                source="devcontainer",
                image=image,
                languages=tuple(_detect_languages(self.workspace)),
                dockerfile=str(dockerfile) if dockerfile else None,
            )
        for name in ("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"):
            if (self.workspace / name).is_file():
                return DetectedEnvironment(
                    source="compose",
                    image=configured,
                    languages=tuple(_detect_languages(self.workspace)),
                    compose_file=name,
                )
        for name in ("Dockerfile", "dockerfile"):
            path = self.workspace / name
            if path.is_file():
                return DetectedEnvironment(
                    source="dockerfile",
                    image=configured,
                    languages=tuple(_detect_languages(self.workspace)),
                    dockerfile=name,
                )
        languages = tuple(_detect_languages(self.workspace))
        default_image = _language_default_image(languages) or configured
        return DetectedEnvironment(
            source="language-default" if languages else "configured",
            image=configured if configured != "auto" else default_image,
            languages=languages,
        )

    def ensure_running(self, *, env: dict[str, str] | None = None) -> ContainerRecord:
        if not self.enabled:
            raise ContainerError("Container execution is not enabled for this workspace.")
        with self._lock:
            runtime = "podman" if self.policy.backend == "podman" else "docker"
            security = _sandbox_security()
            runtime_path = security.resolve_container_runtime(runtime)
            if runtime_path is None:
                raise ContainerError(f"{runtime} is not installed or is not on PATH.")
            daemon_ok, daemon_detail = security.container_daemon_available(runtime_path)
            if not daemon_ok:
                raise ContainerError(f"{runtime} daemon is unavailable: {daemon_detail}")
            runtime_security, detail = security.inspect_container_runtime_security(runtime_path, runtime)
            if runtime_security is None:
                raise ContainerError(detail)
            rejection = security.runtime_security_rejection(self.policy, runtime_security)
            if rejection:
                raise ContainerError(rejection)
            for rejection in (
                self.policy.network_rejection(),
                self.policy.container_user_rejection(),
            ):
                if rejection:
                    raise ContainerError(rejection)
            environment = self.detect_environment()
            image, image_detail = security.resolve_container_image_reference(
                runtime_path,
                environment.image,
                self.policy.images,
            )
            if image is None:
                raise ContainerError(image_detail)
            workspace_id = sha256(str(self.workspace).casefold().encode("utf-8")).hexdigest()[:16]
            name = f"agent47-ws-{workspace_id}"
            cache_volume = f"agent47-cache-{workspace_id}"
            network = "none" if self.policy.commands.offline else "bridge"
            fingerprint = _config_fingerprint(
                image=image,
                workspace=str(self.workspace),
                container_workspace=self.container_workspace,
                network=network,
                user=self.policy.container_user,
                resources=asdict(self.policy.resources),
            )
            existing = self._inspect(runtime_path, name)
            reuse = _env_bool(
                "AGENT_CONTAINER_REUSE",
                bool(getattr(self.policy, "container_reuse", True)),
            )
            if (
                reuse
                and existing
                and existing.get("running")
                and existing.get("config_sha256") == fingerprint
            ):
                self._record = self._record_from_inspect(
                    existing,
                    runtime=runtime,
                    runtime_path=runtime_path,
                    name=name,
                    image=image,
                    image_source=environment.source,
                    cache_volume=cache_volume,
                    network=network,
                    config_sha256=fingerprint,
                )
                self._persist(self._record)
                self._audit("container_reused", container_name=name, container_id=self._record.container_id)
                return self._record
            if existing:
                self._remove(runtime_path, name)
            command = self._create_command(
                runtime=runtime,
                runtime_path=runtime_path,
                name=name,
                image=image,
                cache_volume=cache_volume,
                network=network,
                config_sha256=fingerprint,
                env=env or {},
                apparmor=bool(runtime_security.apparmor),
            )
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                env=security._container_runtime_env(),
                creationflags=windows_creation_flags(new_process_group=False),
            )
            if completed.returncode != 0:
                raise ContainerError(
                    "Container creation failed: " + (completed.stderr.strip() or completed.stdout.strip())
                )
            container_id = completed.stdout.strip()
            now = datetime.now(UTC).isoformat()
            self._record = ContainerRecord(
                workspace=str(self.workspace),
                workspace_id=workspace_id,
                runtime=runtime,
                runtime_path=runtime_path,
                name=name,
                image=image,
                image_source=environment.source,
                container_workspace=self.container_workspace,
                cache_volume=cache_volume,
                network=network,
                config_sha256=fingerprint,
                container_id=container_id,
                status="running",
                created_at=now,
                updated_at=now,
            )
            self._persist(self._record)
            self._audit("container_created", **self.status())
            return self._record

    def exec_argv(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        interactive: bool = True,
        tty: bool = False,
        execution_id: str | None = None,
    ) -> list[str]:
        record = self.ensure_running(env=env)
        command = [record.runtime_path, "exec"]
        if interactive:
            command.append("-i")
        if tty:
            command.append("-t")
        command.extend(["-w", self.map_cwd(cwd)])
        for key, value in _container_exec_env(env or {}).items():
            command.extend(["-e", f"{key}={value}"])
        command.append(record.name)
        if execution_id is None:
            command.extend(list(argv))
        else:
            pid_path = self._exec_pid_path(execution_id)
            command.extend(
                [
                    "sh",
                    "-c",
                    'echo $$ > "$1"; shift; exec "$@"',
                    "agent47",
                    pid_path,
                    *list(argv),
                ]
            )
        return command

    def terminate_exec(self, execution_id: str, *, force: bool = True) -> bool:
        record = self._record or self._load()
        if record is None:
            return False
        signal_name = "KILL" if force else "TERM"
        completed = subprocess.run(
            [
                record.runtime_path,
                "exec",
                record.name,
                "sh",
                "-c",
                'test -f "$1" && kill -"$2" "$(cat "$1")"',
                "agent47",
                self._exec_pid_path(execution_id),
                signal_name,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=_sandbox_security()._container_runtime_env(),
            creationflags=windows_creation_flags(new_process_group=False),
        )
        self.cleanup_exec(execution_id)
        return completed.returncode == 0

    def cleanup_exec(self, execution_id: str) -> None:
        path = self.root / "exec" / f"{execution_id}.pid"
        path.unlink(missing_ok=True)

    def _exec_pid_path(self, execution_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", execution_id):
            raise ContainerError("Invalid container execution id.")
        return (
            f"{self.container_workspace.rstrip('/')}"
            f"/.code-agent/containers/exec/{execution_id}.pid"
        )

    def popen(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        stdin: Any = subprocess.PIPE,
        stdout: Any = subprocess.PIPE,
        stderr: Any = subprocess.PIPE,
        text: bool = False,
        bufsize: int = -1,
        **_ignored: Any,
    ) -> subprocess.Popen[Any]:
        command = self.exec_argv(argv, cwd=cwd, env=env, interactive=stdin is not subprocess.DEVNULL)
        return subprocess.Popen(
            command,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            text=text,
            bufsize=bufsize,
            shell=False,
            env=_sandbox_security()._container_runtime_env(),
            creationflags=windows_creation_flags(),
            start_new_session=os.name != "nt",
        )

    def resolve_command(self, candidates: tuple[tuple[str, ...], ...]) -> tuple[str, ...] | None:
        for command in candidates:
            probe = subprocess.run(
                self.exec_argv(["sh", "-c", 'command -v "$1" >/dev/null 2>&1', "agent47", command[0]], interactive=False),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                env=_sandbox_security()._container_runtime_env(),
                creationflags=windows_creation_flags(new_process_group=False),
            )
            if probe.returncode == 0:
                return command
        return None

    def map_path(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(self.workspace)
        except ValueError as exc:
            raise ContainerError("Path must remain inside the workspace mount.") from exc
        suffix = relative.as_posix()
        return self.container_workspace + (f"/{suffix}" if suffix else "")

    def map_cwd(self, cwd: Path | str | None) -> str:
        if cwd is None:
            return self.container_workspace
        if isinstance(cwd, str) and cwd.startswith("/"):
            return cwd
        return self.map_path(Path(cwd))

    def host_uri_to_container(self, uri: str) -> str:
        from urllib.parse import quote, unquote, urlparse

        parsed = urlparse(uri)
        if parsed.scheme != "file":
            return uri
        raw_path = unquote(parsed.path)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:/", raw_path):
            raw_path = raw_path[1:]
        path = Path(raw_path)
        try:
            return "file://" + quote(self.map_path(path), safe="/")
        except (ContainerError, ValueError):
            return uri

    def container_uri_to_host(self, uri: str) -> str:
        from urllib.parse import unquote, urlparse

        parsed = urlparse(uri)
        if parsed.scheme != "file":
            return uri
        runtime_path = unquote(parsed.path)
        prefix = self.container_workspace.rstrip("/")
        if runtime_path != prefix and not runtime_path.startswith(prefix + "/"):
            return uri
        relative = runtime_path[len(prefix) :].lstrip("/")
        return (self.workspace / Path(relative)).resolve().as_uri()

    def status(self) -> dict[str, Any]:
        record = self._record or self._load()
        if record is None:
            environment = self.detect_environment()
            return {
                "enabled": self.enabled,
                "status": "not_started",
                "workspace": str(self.workspace),
                "image": environment.image,
                "image_source": environment.source,
                "languages": list(environment.languages),
            }
        inspected = self._inspect(record.runtime_path, record.name)
        return {
            **asdict(record),
            "enabled": self.enabled,
            "status": "running" if inspected and inspected.get("running") else "stopped",
        }

    def health(self) -> dict[str, Any]:
        status = self.status()
        status["healthy"] = status.get("status") == "running"
        return status

    def stop(self, *, remove: bool = False) -> bool:
        with self._lock:
            record = self._record or self._load()
            if record is None:
                return False
            action = "rm" if remove else "stop"
            command = [record.runtime_path, action]
            if remove:
                command.append("--force")
            else:
                command.extend(["--time", "5"])
            command.append(record.name)
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                env=_sandbox_security()._container_runtime_env(),
                creationflags=windows_creation_flags(new_process_group=False),
            )
            if completed.returncode == 0:
                self._audit("container_removed" if remove else "container_stopped", container_name=record.name)
                if remove:
                    self._record = None
                    self.state_path.unlink(missing_ok=True)
                return True
            return False

    def reconcile(self) -> dict[str, Any]:
        record = self._load()
        if record is None:
            return self.status()
        inspected = self._inspect(record.runtime_path, record.name)
        if inspected is None:
            self.state_path.unlink(missing_ok=True)
            self._record = None
            return self.status()
        self._record = record
        return self.status()

    def _create_command(
        self,
        *,
        runtime: str,
        runtime_path: str,
        name: str,
        image: str,
        cache_volume: str,
        network: str,
        config_sha256: str,
        env: dict[str, str],
        apparmor: bool,
    ) -> list[str]:
        command = [
            runtime_path,
            "run",
            "--detach",
            "--pull",
            "never",
            "--name",
            name,
            "--label",
            "io.agent47.container=true",
            "--label",
            f"{CONTAINER_LABEL}={sha256(str(self.workspace).casefold().encode()).hexdigest()[:16]}",
            "--label",
            f"io.agent47.config={config_sha256}",
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
            "--mount",
            f"type=bind,source={self.workspace},target={self.container_workspace}",
            "--mount",
            f"type=volume,source={cache_volume},target=/agent47-cache",
            "-w",
            self.container_workspace,
        ]
        command.extend(self._configured_mount_args())
        if runtime == "podman":
            command.extend(["--userns", "keep-id"])
        if runtime == "docker" and apparmor:
            command.extend(["--security-opt", "apparmor=docker-default"])
        base_env = {
            "HOME": "/tmp/agent47-home",
            "XDG_CACHE_HOME": "/agent47-cache/xdg",
            "PIP_CACHE_DIR": "/agent47-cache/pip",
            "npm_config_cache": "/agent47-cache/npm",
            "CARGO_HOME": "/agent47-cache/cargo",
            "GOMODCACHE": "/agent47-cache/go/pkg/mod",
            "GOCACHE": "/agent47-cache/go/build",
            "MAVEN_OPTS": "-Dmaven.repo.local=/agent47-cache/m2",
            "NUGET_PACKAGES": "/agent47-cache/nuget",
            **_container_exec_env(env),
        }
        for key, value in base_env.items():
            command.extend(["-e", f"{key}={value}"])
        command.extend([image, "sh", "-c", "trap 'exit 0' TERM INT; while :; do sleep 3600; done"])
        return command

    def _configured_mount_args(self) -> list[str]:
        arguments: list[str] = []
        for raw in getattr(self.policy, "container_bind_mounts", ()):
            parts = str(raw).rsplit(":", 2)
            if len(parts) < 2:
                raise ContainerError(f"Invalid bind mount: {raw}")
            source = Path(parts[0]).expanduser().resolve()
            target = parts[1]
            mode = parts[2] if len(parts) == 3 else "ro"
            if not source.exists() or not target.startswith("/") or mode not in {"ro", "rw"}:
                raise ContainerError(f"Invalid bind mount: {raw}")
            arguments.extend(
                ["--mount", f"type=bind,source={source},target={target},readonly={str(mode == 'ro').lower()}"]
            )
        for raw in getattr(self.policy, "container_cache_volumes", ()):
            parts = str(raw).split(":", 1)
            if len(parts) != 2 or not re.fullmatch(r"[A-Za-z0-9_.-]+", parts[0]) or not parts[1].startswith("/"):
                raise ContainerError(f"Invalid cache volume: {raw}")
            arguments.extend(["--mount", f"type=volume,source={parts[0]},target={parts[1]}"])
        return arguments

    def _inspect(self, runtime_path: str, name: str) -> dict[str, Any] | None:
        completed = subprocess.run(
            [runtime_path, "inspect", name],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=_sandbox_security()._container_runtime_env(),
            creationflags=windows_creation_flags(new_process_group=False),
        )
        if completed.returncode != 0:
            return None
        try:
            payload = json.loads(completed.stdout)[0]
        except (IndexError, KeyError, json.JSONDecodeError, TypeError):
            return None
        labels = _dict(_dict(payload.get("Config")).get("Labels"))
        state = _dict(payload.get("State"))
        return {
            "id": str(payload.get("Id") or ""),
            "running": bool(state.get("Running")),
            "started_at": str(state.get("StartedAt") or ""),
            "config_sha256": str(labels.get("io.agent47.config") or ""),
        }

    def _remove(self, runtime_path: str, name: str) -> None:
        subprocess.run(
            [runtime_path, "rm", "--force", name],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=_sandbox_security()._container_runtime_env(),
            creationflags=windows_creation_flags(new_process_group=False),
        )

    def _record_from_inspect(self, inspected: dict[str, Any], **values: Any) -> ContainerRecord:
        existing = self._load()
        now = datetime.now(UTC).isoformat()
        workspace_id = sha256(str(self.workspace).casefold().encode("utf-8")).hexdigest()[:16]
        return ContainerRecord(
            workspace=str(self.workspace),
            workspace_id=workspace_id,
            container_workspace=self.container_workspace,
            container_id=str(inspected["id"]),
            status="running",
            created_at=existing.created_at if existing else str(inspected.get("started_at") or now),
            updated_at=now,
            **values,
        )

    def _persist(self, record: ContainerRecord) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(record), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, self.state_path)

    def _load(self) -> ContainerRecord | None:
        if not self.state_path.is_file():
            return None
        try:
            return ContainerRecord(**json.loads(self.state_path.read_text(encoding="utf-8")))
        except (OSError, TypeError, json.JSONDecodeError):
            return None

    def _audit(self, event: str, **payload: Any) -> None:
        if self.audit_log is not None:
            self.audit_log.record(event, **payload)


def _detect_languages(workspace: Path) -> list[str]:
    markers = (
        ("python", ("pyproject.toml", "requirements.txt", "setup.py")),
        ("node", ("package.json", "pnpm-lock.yaml", "yarn.lock")),
        ("rust", ("Cargo.toml",)),
        ("go", ("go.mod", "go.work")),
        ("java", ("pom.xml", "build.gradle", "build.gradle.kts")),
        ("dotnet", ("global.json",)),
    )
    found = [language for language, names in markers if any((workspace / name).exists() for name in names)]
    if any(workspace.glob("*.sln")) or any(workspace.glob("*.csproj")):
        found.append("dotnet")
    return list(dict.fromkeys(found))


def _language_default_image(languages: tuple[str, ...]) -> str | None:
    images = {
        "python": "python:3.13-slim",
        "node": "node:22-bookworm-slim",
        "rust": "rust:1-bookworm",
        "go": "golang:1.24-bookworm",
        "java": "eclipse-temurin:21-jdk",
        "dotnet": "mcr.microsoft.com/dotnet/sdk:9.0",
    }
    return images.get(languages[0]) if len(languages) == 1 else None


def _config_fingerprint(**payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _container_exec_env(env: dict[str, str]) -> dict[str, str]:
    allowed = {
        "CI",
        "LANG",
        "LC_ALL",
        "NO_COLOR",
        "PYTHONUNBUFFERED",
        "TERM",
        "TZ",
    }
    return {key: value for key, value in env.items() if key in allowed and "\x00" not in value}


def _strip_json_comments(value: str) -> str:
    return re.sub(r"(^|\s)//.*$", r"\1", value, flags=re.MULTILINE)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _sandbox_security() -> Any:
    from . import sandbox_security

    return sandbox_security
