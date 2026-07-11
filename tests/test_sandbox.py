import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import code_agent.sandbox_security as sandbox_security_module
from code_agent.cli import app
from code_agent.processes import ShellProcessResult
from code_agent.sandbox import (
    create_sandbox_workspace,
    diff_sandbox_workspace,
    format_sandbox_diff,
    format_sandbox_limits,
    promote_sandbox_changes,
)
from code_agent.sandbox_security import SandboxPolicy, SandboxResourceLimits, sandbox_health
from code_agent.sandbox_security import SandboxAuditLog, SandboxRunner
from code_agent.processes import ProcessSupervisor
from code_agent.schema import RunShellAction
from code_agent.tools import ToolRegistry


def test_create_sandbox_workspace_copies_project_files(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hello')", encoding="utf-8")

    sandbox = create_sandbox_workspace(tmp_path)

    assert sandbox.source == tmp_path.resolve()
    assert (sandbox.path / "src" / "app.py").read_text(encoding="utf-8") == "print('hello')"


def test_create_sandbox_workspace_excludes_local_state(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=value", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("git", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("venv", encoding="utf-8")

    sandbox = create_sandbox_workspace(tmp_path)

    assert not (sandbox.path / ".env").exists()
    assert not (sandbox.path / ".git").exists()
    assert not (sandbox.path / ".venv").exists()


def test_create_sandbox_workspace_skips_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    link = tmp_path / "linked.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        return

    sandbox = create_sandbox_workspace(tmp_path)

    assert not (sandbox.path / "linked.txt").exists()


def test_sandbox_diff_reports_update_create_and_delete(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('base')\n", encoding="utf-8")
    (tmp_path / "old.txt").write_text("remove me\n", encoding="utf-8")
    sandbox = create_sandbox_workspace(tmp_path)
    (sandbox.path / "src" / "app.py").write_text("print('sandbox')\n", encoding="utf-8")
    (sandbox.path / "new.txt").write_text("add me\n", encoding="utf-8")
    (sandbox.path / "old.txt").unlink()

    diff = diff_sandbox_workspace(tmp_path, sandbox.path)
    rendered = format_sandbox_diff(diff)

    assert diff.changed_paths == ["new.txt", "old.txt", "src/app.py"]
    assert "--- /dev/null" in diff.patch
    assert "+++ b/new.txt" in diff.patch
    assert "--- a/old.txt" in diff.patch
    assert "+++ /dev/null" in diff.patch
    assert "-print('base')" in diff.patch
    assert "+print('sandbox')" in diff.patch
    assert "changed files: 3" in rendered


def test_sandbox_apply_promotes_changes_after_approval(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('base')\n", encoding="utf-8")
    sandbox = create_sandbox_workspace(tmp_path)
    (sandbox.path / "src" / "app.py").write_text("print('sandbox')\n", encoding="utf-8")
    (sandbox.path / "new.txt").write_text("add me\n", encoding="utf-8")

    approvals: list[str] = []
    result = promote_sandbox_changes(
        tmp_path,
        sandbox.path,
        approval_callback=lambda _action, detail, _metadata: approvals.append(detail) or True,
    )

    assert result.ok
    assert result.changed_paths == ["new.txt", "src/app.py"]
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "print('sandbox')\n"
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "add me\n"
    assert "Patch preview:" in approvals[0]
    assert result.metadata["paths"] == ["new.txt", "src/app.py"]


def test_sandbox_apply_denial_leaves_base_unchanged(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("base\n", encoding="utf-8")
    sandbox = create_sandbox_workspace(tmp_path)
    (sandbox.path / "app.py").write_text("sandbox\n", encoding="utf-8")

    result = promote_sandbox_changes(
        tmp_path,
        sandbox.path,
        approval_callback=lambda _action, _detail, _metadata: False,
    )

    assert not result.ok
    assert "Permission denied for apply_patch" in result.output
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "base\n"


def test_sandbox_diff_cli_outputs_patch(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("base\n", encoding="utf-8")
    sandbox = create_sandbox_workspace(tmp_path)
    (sandbox.path / "app.py").write_text("sandbox\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["sandbox", "diff", str(sandbox.path), "--base", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "Sandbox diff:" in result.output
    assert "-base" in result.output
    assert "+sandbox" in result.output


def test_format_sandbox_limits_describes_process_and_network_boundaries() -> None:
    output = format_sandbox_limits()

    assert "backend: local" in output
    assert "hardened local subprocess policy" in output
    assert "offline by default" in output
    assert "explicit sandbox apply promotion" in output


def test_sandbox_policy_file_blocks_denied_command(tmp_path: Path) -> None:
    policy_dir = tmp_path / ".code-agent"
    policy_dir.mkdir()
    (policy_dir / "policy.toml").write_text(
        "[commands]\nallow = [\"uv run pytest\"]\ndeny = [\"uv run ruff*\"]\n",
        encoding="utf-8",
    )
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=False,
        approval_callback=lambda _action, _detail: True,
    )

    result = tools.run(RunShellAction(type="run_shell", command="uv run ruff check src"))

    assert not result.ok
    assert "Sandbox policy denied command" in result.output


def test_sandbox_policy_allowlist_rejects_unlisted_command(tmp_path: Path) -> None:
    policy_dir = tmp_path / ".code-agent"
    policy_dir.mkdir()
    (policy_dir / "policy.toml").write_text(
        "[commands]\nallow = [\"uv run pytest\"]\n",
        encoding="utf-8",
    )
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=False,
        approval_callback=lambda _action, _detail: True,
    )

    result = tools.run(RunShellAction(type="run_shell", command="npm run test"))

    assert not result.ok
    assert "allowlist did not include" in result.output or "install/network" in result.output


def test_sandbox_policy_allows_install_when_trusted_project_opts_in(tmp_path: Path, monkeypatch) -> None:
    policy_dir = tmp_path / ".code-agent"
    policy_dir.mkdir()
    (policy_dir / "policy.toml").write_text(
        "[commands]\nallow_install = true\n",
        encoding="utf-8",
    )
    captured: list[str] = []
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=False,
        approval_callback=lambda _action, detail: captured.append(detail) or False,
    )

    result = tools.run(RunShellAction(type="run_shell", command="npm install"))

    assert not result.ok
    assert result.output == "Permission denied for run_shell."
    assert "Sandbox backend: local" in captured[0]
    assert "Resource limits:" in captured[0]


def test_sandbox_audit_log_records_shell_command(tmp_path: Path, monkeypatch) -> None:
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=False,
        approval_callback=lambda _action, _detail: True,
    )

    result = tools.run(RunShellAction(type="run_shell", command="git --version"))

    assert result.ok
    audit_path = tmp_path / ".code-agent" / "audit" / "sandbox.jsonl"
    assert audit_path.exists()
    audit_text = audit_path.read_text(encoding="utf-8")
    assert "command_started" in audit_text
    assert "command_finished" in audit_text


def test_sandbox_disk_guard_blocks_shell_when_workspace_starts_over_budget(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_bytes(b"x")
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=False,
        approval_callback=lambda _action, _detail: True,
        sandbox_policy=SandboxPolicy(resources=SandboxResourceLimits(disk_mb=0)),
    )

    result = tools.run(RunShellAction(type="run_shell", command="git --version"))

    assert not result.ok
    assert "exceeded before command execution" in result.output
    assert result.metadata["disk_budget_stage"] == "preflight"
    assert result.metadata["disk_before_bytes"] == 1
    assert result.metadata["disk_limit_bytes"] == 0
    audit_events = [
        json.loads(line)["event"]
        for line in (tmp_path / ".code-agent" / "audit" / "sandbox.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert "disk_budget_exceeded" in audit_events
    assert "command_started" not in audit_events


def test_sandbox_disk_guard_fails_command_that_grows_workspace_past_budget(
    tmp_path: Path,
) -> None:
    class GrowingSupervisor:
        def run_shell(self, command: str, **_kwargs) -> ShellProcessResult:
            (tmp_path / "large.txt").write_bytes(b"x")
            return ShellProcessResult(
                completed=subprocess.CompletedProcess(command, 0, "ok", ""),
            )

    runner = SandboxRunner(
        tmp_path,
        SandboxPolicy(resources=SandboxResourceLimits(disk_mb=0)),
        GrowingSupervisor(),  # type: ignore[arg-type]
        SandboxAuditLog(tmp_path),
    )

    result = runner.run_shell("git --version", timeout_seconds=30, env={})

    assert result.completed.returncode == 125
    assert result.completed.stdout == "ok"
    assert "exceeded after command execution" in result.completed.stderr
    assert result.metadata is not None
    assert result.metadata["disk_budget_stage"] == "post_run"
    assert result.metadata["disk_before_bytes"] == 0
    assert result.metadata["disk_after_bytes"] == 1
    assert result.metadata["disk_growth_bytes"] == 1
    audit_records = [
        json.loads(line)
        for line in (tmp_path / ".code-agent" / "audit" / "sandbox.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert any(
        record["event"] == "disk_budget_exceeded"
        and record["disk_budget_stage"] == "post_run"
        for record in audit_records
    )
    finished = [record for record in audit_records if record["event"] == "command_finished"]
    assert finished[0]["returncode"] == 125
    assert finished[0]["disk"]["original_returncode"] == 0


def test_sandbox_audit_log_redacts_secret_looking_command_text(tmp_path: Path) -> None:
    audit = SandboxAuditLog(tmp_path)

    audit.record("command_started", command="echo API_KEY=super-secret-token")

    audit_text = (tmp_path / ".code-agent" / "audit" / "sandbox.jsonl").read_text(
        encoding="utf-8"
    )
    assert "super-secret-token" not in audit_text
    assert "API_KEY=[REDACTED]" in audit_text


def test_sandbox_health_reports_container_availability() -> None:
    health = sandbox_health(SandboxPolicy(backend="docker"))

    assert health.backend == "docker"
    assert "container" in health.isolation


def test_sandbox_health_distinguishes_cli_from_daemon(monkeypatch) -> None:
    monkeypatch.setattr(
        sandbox_security_module,
        "resolve_container_runtime",
        lambda runtime: "C:/Program Files/Docker/Docker/resources/bin/docker.exe",
    )
    monkeypatch.setattr(
        sandbox_security_module,
        "container_daemon_available",
        lambda _runtime: (False, "virtualization support not detected"),
    )
    monkeypatch.setattr(sandbox_security_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        sandbox_security_module,
        "windows_virtualization_diagnostic",
        lambda: "Windows virtualization: Virtualization Enabled In Firmware: No",
    )

    health = sandbox_health(SandboxPolicy(backend="docker"))
    rendered = health.format_text()

    assert not health.available
    assert health.cli_available is True
    assert health.daemon_available is False
    assert "virtualization support not detected" in rendered
    assert "Virtualization Enabled In Firmware: No" in rendered


def test_sandbox_health_reports_missing_container_cli(monkeypatch) -> None:
    monkeypatch.setattr(sandbox_security_module, "resolve_container_runtime", lambda _runtime: None)
    monkeypatch.setattr(sandbox_security_module, "windows_virtualization_diagnostic", lambda: "")

    health = sandbox_health(SandboxPolicy(backend="docker"))

    assert not health.available
    assert health.cli_available is False
    assert health.daemon_available is False
    assert "executable was not found" in health.format_text()


def test_container_runner_fails_fast_when_daemon_unavailable(tmp_path: Path, monkeypatch) -> None:
    popen_called: list[bool] = []
    monkeypatch.setattr(
        sandbox_security_module,
        "resolve_container_runtime",
        lambda _runtime: "C:/Program Files/Docker/Docker/resources/bin/docker.exe",
    )
    monkeypatch.setattr(
        sandbox_security_module,
        "container_daemon_available",
        lambda _runtime: (False, "virtualization support not detected"),
    )
    monkeypatch.setattr(
        sandbox_security_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: popen_called.append(True),
    )
    runner = SandboxRunner(
        tmp_path,
        SandboxPolicy(backend="docker"),
        ProcessSupervisor(),
        SandboxAuditLog(tmp_path, enabled=False),
    )

    result = runner.run_shell(
        "python -m pytest",
        timeout_seconds=120,
        env={},
    )

    assert result.completed.returncode == 125
    assert "daemon is not available" in result.completed.stderr
    assert "virtualization support not detected" in result.completed.stderr
    assert popen_called == []


def test_container_runner_fails_before_popen_when_image_policy_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    popen_called: list[bool] = []
    monkeypatch.setattr(
        sandbox_security_module,
        "resolve_container_runtime",
        lambda _runtime: "docker",
    )
    monkeypatch.setattr(
        sandbox_security_module,
        "container_daemon_available",
        lambda _runtime: (True, ""),
    )
    monkeypatch.setattr(
        sandbox_security_module,
        "validate_container_image_policy",
        lambda _runtime, _image, _policy: (False, "image policy failed"),
    )
    monkeypatch.setattr(
        sandbox_security_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: popen_called.append(True),
    )
    runner = SandboxRunner(
        tmp_path,
        SandboxPolicy(backend="docker"),
        ProcessSupervisor(),
        SandboxAuditLog(tmp_path, enabled=False),
    )

    result = runner.run_shell("ls", timeout_seconds=30, env={})

    assert result.completed.returncode == 126
    assert result.completed.stderr == "image policy failed"
    assert popen_called == []


def test_container_image_policy_rejects_unallowed_image() -> None:
    policy = sandbox_security_module.ContainerImagePolicy(allowed_images=("python:*",))

    ok, detail = sandbox_security_module.validate_container_image_policy(
        "docker",
        "node:latest",
        policy,
    )

    assert not ok
    assert "not allowed" in detail


def test_container_image_policy_requires_local_image(monkeypatch) -> None:
    monkeypatch.setattr(
        sandbox_security_module,
        "inspect_container_image",
        lambda _runtime, _image: (False, (), "No such image"),
    )

    ok, detail = sandbox_security_module.validate_container_image_policy(
        "docker",
        "python:3.13-slim",
        sandbox_security_module.ContainerImagePolicy(),
    )

    assert not ok
    assert "not available locally" in detail


def test_container_image_policy_validates_required_digest(monkeypatch) -> None:
    monkeypatch.setattr(
        sandbox_security_module,
        "inspect_container_image",
        lambda _runtime, _image: (
            True,
            ("python@sha256:expected",),
            "",
        ),
    )

    ok, detail = sandbox_security_module.validate_container_image_policy(
        "docker",
        "python:3.13-slim",
        sandbox_security_module.ContainerImagePolicy(required_digest="sha256:expected"),
    )

    assert ok
    assert "matches policy" in detail


def test_container_image_policy_rejects_digest_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(
        sandbox_security_module,
        "inspect_container_image",
        lambda _runtime, _image: (
            True,
            ("python@sha256:actual",),
            "",
        ),
    )

    ok, detail = sandbox_security_module.validate_container_image_policy(
        "docker",
        "python:3.13-slim",
        sandbox_security_module.ContainerImagePolicy(required_digest="sha256:expected"),
    )

    assert not ok
    assert "digest does not match" in detail


def test_sandbox_policy_loads_container_image_rules(tmp_path: Path) -> None:
    policy_dir = tmp_path / ".code-agent"
    policy_dir.mkdir()
    (policy_dir / "policy.toml").write_text(
        "\n".join(
            [
                "[sandbox]",
                'container_image = "python:3.13-slim"',
                "",
                "[images]",
                'allowed = ["python:*"]',
                'required_digest = "sha256:expected"',
            ]
        ),
        encoding="utf-8",
    )

    policy = SandboxPolicy.from_workspace(tmp_path, backend="docker")

    assert policy.container_image == "python:3.13-slim"
    assert policy.images.allowed_images == ("python:*",)
    assert policy.images.required_digest == "sha256:expected"


def test_container_image_policy_accepts_pinned_image_reference(monkeypatch) -> None:
    monkeypatch.setattr(
        sandbox_security_module,
        "inspect_container_image",
        lambda _runtime, _image: (
            True,
            ("python@sha256:expected",),
            "",
        ),
    )

    ok, detail = sandbox_security_module.validate_container_image_policy(
        "docker",
        "python:3.13-slim@sha256:expected",
        sandbox_security_module.ContainerImagePolicy(allowed_images=("python:*",)),
    )

    assert ok
    assert "image reference pin" in detail


def test_container_image_policy_rejects_image_reference_policy_digest_conflict() -> None:
    ok, detail = sandbox_security_module.validate_container_image_policy(
        "docker",
        "python:3.13-slim@sha256:actual",
        sandbox_security_module.ContainerImagePolicy(
            allowed_images=("python:*",),
            required_digest="sha256:expected",
        ),
    )

    assert not ok
    assert "conflicts with policy" in detail


@pytest.mark.docker_security
def test_docker_sandbox_uses_isolated_env_and_read_only_rootfs(tmp_path: Path) -> None:
    runtime = sandbox_security_module.resolve_container_runtime("docker")
    if runtime is None:
        pytest.skip("docker executable is not available")
    daemon_ok, daemon_detail = sandbox_security_module.container_daemon_available(runtime)
    if not daemon_ok:
        pytest.skip(f"docker daemon is not available: {daemon_detail}")
    image_ok, image_detail = sandbox_security_module.validate_container_image_policy(
        runtime,
        "python:3.13-slim",
        sandbox_security_module.ContainerImagePolicy(),
    )
    if not image_ok:
        pytest.skip(image_detail)
    runner = SandboxRunner(
        tmp_path,
        SandboxPolicy(backend="docker"),
        ProcessSupervisor(),
        SandboxAuditLog(tmp_path),
    )

    result = runner.run_shell(
        (
            "python -c \"import os, pathlib; "
            "print(os.environ.get('AGENT47_SANDBOXED_SHELL')); "
            "pathlib.Path('/agent47-rootfs-write').write_text('blocked')\""
        ),
        timeout_seconds=30,
        env={},
    )

    assert result.completed.returncode != 0
    assert "1" in result.completed.stdout
    assert "Read-only file system" in result.completed.stderr or "Permission denied" in result.completed.stderr


def test_container_env_uses_linux_path_and_drops_windows_shell_keys() -> None:
    env = sandbox_security_module._container_env(
        {
            "PATH": "C:\\Windows\\System32",
            "COMSPEC": "C:\\Windows\\System32\\cmd.exe",
            "HOME": "C:\\Users\\Sirius",
            "TEMP": "C:\\Users\\Sirius\\AppData\\Local\\Temp",
            "USERNAME": "Sirius",
            "USERPROFILE": "C:\\Users\\Sirius",
            "AGENT47_SANDBOXED_SHELL": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )

    assert env["PATH"] == "/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"
    assert "COMSPEC" not in env
    assert "HOME" not in env
    assert "TEMP" not in env
    assert "USERNAME" not in env
    assert "USERPROFILE" not in env
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
