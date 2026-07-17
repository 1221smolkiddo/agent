from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from code_agent import cli as cli_module
from code_agent import container_manager as container_manager_module
from code_agent import managed_processes as managed_processes_module
from code_agent.container_manager import ContainerManager
from code_agent.cli import app
from code_agent.managed_processes import ManagedProcessStore
from code_agent.lsp import LspManager, LspServerSpec
from code_agent.processes import ProcessSupervisor
from code_agent.sandbox_security import (
    ContainerImagePolicy,
    ContainerRuntimeSecurity,
    SandboxAuditLog,
    SandboxPolicy,
    SandboxRunner,
)
from code_agent.processes import ShellProcessResult


def _policy(*, require_digest: bool = False) -> SandboxPolicy:
    return SandboxPolicy(
        backend="docker",
        container_image="python:3.13-slim",
        container_user="65532:65532",
        rootless_required=False,
        images=ContainerImagePolicy(
            allowed_images=("python:3.13-slim",),
            require_digest=require_digest,
        ),
        process_isolation_required=True,
    )


def _mock_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    security = container_manager_module._sandbox_security()
    monkeypatch.setattr(security, "resolve_container_runtime", lambda _runtime: "docker")
    monkeypatch.setattr(security, "container_daemon_available", lambda _runtime: (True, ""))
    monkeypatch.setattr(
        security,
        "inspect_container_runtime_security",
        lambda _runtime, _kind: (
            ContainerRuntimeSecurity(rootless=True, seccomp=True, apparmor=True),
            "",
        ),
    )
    monkeypatch.setattr(
        security,
        "resolve_container_image_reference",
        lambda _runtime, _image, _policy: ("python@sha256:reviewed", ""),
    )


def test_environment_detection_prefers_devcontainer(tmp_path: Path) -> None:
    devcontainer = tmp_path / ".devcontainer"
    devcontainer.mkdir()
    (devcontainer / "devcontainer.json").write_text(
        '{\n  // reviewed image\n  "image": "python:3.13-slim"\n}\n',
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    detected = ContainerManager(tmp_path, _policy()).detect_environment()

    assert detected.source == "devcontainer"
    assert detected.image == "python:3.13-slim"
    assert detected.languages == ("python",)


def test_container_creation_is_hardened_and_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_runtime(monkeypatch)
    manager = ContainerManager(tmp_path, _policy())
    monkeypatch.setattr(manager, "_inspect", lambda _runtime, _name: None)
    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "a" * 64 + "\n", "")

    monkeypatch.setattr(container_manager_module.subprocess, "run", fake_run)

    record = manager.ensure_running(env={"OPENAI_API_KEY": "must-not-pass", "CI": "true"})

    args = captured["args"]
    assert isinstance(args, list)
    assert "--detach" in args
    assert "--read-only" in args
    assert "--cap-drop" in args
    assert "ALL" in args
    assert "no-new-privileges" in args
    assert "--pids-limit" in args
    assert "type=bind" in " ".join(args)
    assert "type=volume" in " ".join(args)
    assert "OPENAI_API_KEY" not in " ".join(args)
    assert record.container_id == "a" * 64
    persisted = json.loads(manager.state_path.read_text(encoding="utf-8"))
    assert persisted["config_sha256"] == record.config_sha256


def test_running_container_is_reused_without_recreation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_runtime(monkeypatch)
    manager = ContainerManager(tmp_path, _policy())
    create_calls = 0

    def fake_inspect(_runtime: str, _name: str):
        if manager._record is None:
            return None
        return {
            "id": manager._record.container_id,
            "running": True,
            "started_at": manager._record.created_at,
            "config_sha256": manager._record.config_sha256,
        }

    def fake_run(args, **_kwargs):
        nonlocal create_calls
        create_calls += 1
        return subprocess.CompletedProcess(args, 0, "b" * 64 + "\n", "")

    monkeypatch.setattr(manager, "_inspect", fake_inspect)
    monkeypatch.setattr(container_manager_module.subprocess, "run", fake_run)

    first = manager.ensure_running()
    second = manager.ensure_running()

    assert first.container_id == second.container_id
    assert create_calls == 1


def test_container_path_mapping_stays_inside_workspace(tmp_path: Path) -> None:
    manager = ContainerManager(tmp_path, _policy())
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir()
    source.write_text("pass\n", encoding="utf-8")

    assert manager.map_path(source) == "/workspace/src/main.py"
    assert manager.container_uri_to_host("file:///workspace/src/main.py") == source.as_uri()
    assert manager.host_uri_to_container(source.as_uri()) == "file:///workspace/src/main.py"

    with pytest.raises(Exception, match="workspace"):
        manager.map_path(tmp_path.parent / "outside.py")


def test_managed_process_spec_routes_workload_into_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = SimpleNamespace(
        ensure_running=lambda **_kwargs: SimpleNamespace(
            runtime_path="docker",
            name="agent47-ws-test",
            container_workspace="/workspace",
        ),
        map_path=lambda path: (
            "/workspace"
            if Path(path).relative_to(tmp_path).as_posix() == "."
            else "/workspace/" + Path(path).relative_to(tmp_path).as_posix()
        ),
    )
    store = ManagedProcessStore(tmp_path, container_manager=manager)

    class Worker:
        def poll(self):
            return None

    monkeypatch.setattr(
        managed_processes_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: Worker(),
    )
    monkeypatch.setattr(
        store,
        "inspect",
        lambda process_id: {
            "process_id": process_id,
            "status": "running",
            "execution_backend": "container",
        },
    )

    state = store.start("python server.py", cwd=tmp_path, env={})
    spec_path = next(store.jobs.glob("*/spec.json"))
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    assert state["execution_backend"] == "container"
    assert spec["execution_backend"] == "container"
    assert spec["container_name"] == "agent47-ws-test"
    assert spec["container_cwd"] == "/workspace"
    assert spec["argv"] == ["python", "server.py"]


def test_container_policy_loads_mount_and_reuse_configuration(tmp_path: Path) -> None:
    policy_dir = tmp_path / ".code-agent"
    policy_dir.mkdir()
    (policy_dir / "policy.toml").write_text(
        "\n".join(
            [
                "[sandbox]",
                'backend = "docker"',
                'container_workspace = "/repo"',
                "container_reuse = false",
                'bind_mounts = ["/sdk:/opt/sdk:ro"]',
                'cache_volumes = ["demo-cache:/cache"]',
            ]
        ),
        encoding="utf-8",
    )

    policy = SandboxPolicy.from_workspace(tmp_path, backend="docker")

    assert policy.container_workspace == "/repo"
    assert policy.container_reuse is False
    assert policy.container_bind_mounts == ("/sdk:/opt/sdk:ro",)
    assert policy.container_cache_volumes == ("demo-cache:/cache",)


def test_container_cli_exposes_start_status_and_remove(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeManager:
        def __init__(self, *_args, **_kwargs):
            pass

        def ensure_running(self):
            calls.append("start")
            return SimpleNamespace(name="agent47-ws-test", status="running")

        def reconcile(self):
            calls.append("status")
            return {"name": "agent47-ws-test", "status": "running"}

        def stop(self, *, remove: bool = False):
            calls.append("remove" if remove else "stop")
            return True

    monkeypatch.setattr(cli_module, "ContainerManager", FakeManager)
    monkeypatch.setattr(cli_module, "resolve_sandbox_policy", lambda *_args, **_kwargs: _policy())
    runner = CliRunner()

    started = runner.invoke(app, ["containers", "start", "--cwd", str(tmp_path)])
    status = runner.invoke(app, ["containers", "status", "--cwd", str(tmp_path)])
    removed = runner.invoke(
        app,
        ["containers", "stop", "--cwd", str(tmp_path), "--remove"],
    )

    assert started.exit_code == 0, started.output
    assert status.exit_code == 0, status.output
    assert removed.exit_code == 0, removed.output
    assert calls == ["start", "status", "remove"]


def test_reusable_runner_terminates_in_container_command_on_cancellation(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    class FakeContainerManager:
        def exec_argv(self, argv, **options):
            calls.append(f"exec:{options['execution_id']}")
            return ["docker", "exec", "container", *argv]

        def terminate_exec(self, execution_id):
            calls.append(f"terminate:{execution_id}")
            return True

        def cleanup_exec(self, execution_id):
            calls.append(f"cleanup:{execution_id}")

        def status(self):
            return {"name": "container", "status": "running"}

    class FakeSupervisor:
        def run_shell(self, *_args, **_kwargs):
            return ShellProcessResult(
                completed=subprocess.CompletedProcess("python server.py", -9, "", "cancelled"),
                cancelled=True,
                cleanup_attempted=True,
            )

    runner = SandboxRunner(
        tmp_path,
        _policy(),
        FakeSupervisor(),
        SandboxAuditLog(tmp_path, enabled=False),
        container_manager=FakeContainerManager(),
    )

    result = runner.run_shell("python server.py", timeout_seconds=30, env={})

    execution_id = calls[0].split(":", 1)[1]
    assert result.cancelled is True
    assert result.cleanup_attempted is True
    assert calls == [f"exec:{execution_id}", f"terminate:{execution_id}"]


@pytest.mark.docker_security
def test_live_container_reuse_exec_mount_and_cleanup(tmp_path: Path) -> None:
    security = container_manager_module._sandbox_security()
    runtime = security.resolve_container_runtime("docker")
    if runtime is None:
        pytest.skip("docker executable is not available")
    daemon_ok, detail = security.container_daemon_available(runtime)
    if not daemon_ok:
        pytest.skip(f"docker daemon is unavailable: {detail}")
    tmp_path.chmod(0o777)
    manager = ContainerManager(tmp_path, _policy())
    try:
        first = manager.ensure_running()
        second = manager.ensure_running()
        assert first.container_id == second.container_id
        runner = SandboxRunner(
            tmp_path,
            _policy(),
            ProcessSupervisor(tmp_path, container_manager=manager),
            SandboxAuditLog(tmp_path, enabled=False),
            container_manager=manager,
        )
        result = runner.run_shell(
            'python -c "from pathlib import Path; Path(\'mounted.txt\').write_text(\'ok\')"',
            timeout_seconds=20,
            env={},
        )
        assert result.completed.returncode == 0, result.completed.stderr
        assert result.metadata is not None
        assert result.metadata["container_reused"] is True
        assert (tmp_path / "mounted.txt").read_text(encoding="utf-8") == "ok"
        assert manager.health()["healthy"] is True
    finally:
        manager.stop(remove=True)


@pytest.mark.docker_security
def test_live_managed_process_runs_inside_reusable_container(tmp_path: Path) -> None:
    security = container_manager_module._sandbox_security()
    runtime = security.resolve_container_runtime("docker")
    if runtime is None or not security.container_daemon_available(runtime)[0]:
        pytest.skip("docker is unavailable")
    tmp_path.chmod(0o777)
    (tmp_path / "server.py").write_text(
        "import time\nprint('container-managed-ready', flush=True)\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    manager = ContainerManager(tmp_path, _policy())
    supervisor = ProcessSupervisor(tmp_path, container_manager=manager)
    state = supervisor.start_managed(
        "python server.py",
        cwd=tmp_path,
        env={"PYTHONUNBUFFERED": "1"},
    )
    process_id = str(state["process_id"])
    try:
        deadline = time.monotonic() + 15
        logs = supervisor.managed_logs(process_id)
        while "container-managed-ready" not in json.dumps(logs) and time.monotonic() < deadline:
            time.sleep(0.1)
            logs = supervisor.managed_logs(process_id)
        inspected = supervisor.inspect_managed(process_id)
        assert "container-managed-ready" in json.dumps(logs)
        assert inspected["execution_backend"] == "container"
        assert inspected["container_name"] == manager.ensure_running().name
    finally:
        stopped = supervisor.stop_managed(process_id, grace_seconds=1.0)
        assert stopped["worker_running"] is False
        manager.stop(remove=True)


@pytest.mark.docker_security
def test_live_lsp_json_rpc_is_forwarded_through_container(tmp_path: Path) -> None:
    security = container_manager_module._sandbox_security()
    runtime = security.resolve_container_runtime("docker")
    if runtime is None or not security.container_daemon_available(runtime)[0]:
        pytest.skip("docker is unavailable")
    tmp_path.chmod(0o777)
    (tmp_path / "demo.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "fake_lsp.py").write_text(
        "import json, sys\n"
        "def read():\n"
        "    headers = {}\n"
        "    while True:\n"
        "        line = sys.stdin.buffer.readline()\n"
        "        if not line: return None\n"
        "        if line in (b'\\r\\n', b'\\n'): break\n"
        "        key, value = line.decode().split(':', 1); headers[key.lower()] = value.strip()\n"
        "    return json.loads(sys.stdin.buffer.read(int(headers['content-length'])))\n"
        "def send(payload):\n"
        "    body = json.dumps(payload, separators=(',', ':')).encode()\n"
        "    sys.stdout.buffer.write(f'Content-Length: {len(body)}\\r\\n\\r\\n'.encode() + body)\n"
        "    sys.stdout.buffer.flush()\n"
        "while True:\n"
        "    message = read()\n"
        "    if message is None: break\n"
        "    method = message.get('method')\n"
        "    if method == 'exit': break\n"
        "    if 'id' not in message: continue\n"
        "    result = {'capabilities': {'hoverProvider': True}} if method == 'initialize' else "
        "{'contents': {'kind': 'plaintext', 'value': 'container-hover'}} if method == 'textDocument/hover' else None\n"
        "    send({'jsonrpc': '2.0', 'id': message['id'], 'result': result})\n",
        encoding="utf-8",
    )
    manager = ContainerManager(tmp_path, _policy())
    lsp = LspManager(
        tmp_path,
        specs=(
            LspServerSpec(
                name="python",
                language_id="python",
                extensions=(".py",),
                commands=(("python", "/workspace/fake_lsp.py"),),
            ),
        ),
        process_factory=manager.popen,
        command_resolver=manager.resolve_command,
        runtime_workspace=manager.container_workspace,
        host_uri_to_runtime=manager.host_uri_to_container,
        runtime_uri_to_host=manager.container_uri_to_host,
    )
    try:
        hover = lsp.hover(tmp_path / "demo.py", 1, 1)
        assert hover is not None
        assert hover["contents"] == "container-hover"
        assert lsp.status(tmp_path / "demo.py")["servers"][0]["running"] is True
    finally:
        lsp.close()
        manager.stop(remove=True)
