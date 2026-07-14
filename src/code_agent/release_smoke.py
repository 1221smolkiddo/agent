from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SmokeCommand:
    name: str
    command: tuple[str, ...]
    required: bool = True
    timeout_seconds: int = 300


@dataclass(frozen=True)
class SmokeCheck:
    name: str
    command: list[str]
    ok: bool
    returncode: int
    output: str
    required: bool = True


@dataclass(frozen=True)
class SmokeReport:
    checks: list[SmokeCheck]

    @property
    def ok(self) -> bool:
        return all(check.ok or not check.required for check in self.checks)

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "checks": [
                {
                    "name": check.name,
                    "command": check.command,
                    "ok": check.ok,
                    "returncode": check.returncode,
                    "required": check.required,
                    "output": check.output,
                }
                for check in self.checks
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True)

    def format_text(self) -> str:
        lines = ["Agent47 release smoke gate:"]
        for check in self.checks:
            marker = "PASS" if check.ok else ("WARN" if not check.required else "FAIL")
            command = " ".join(check.command)
            lines.append(f"- {marker} {check.name}: {command}")
            if check.output:
                lines.append(_indent(_last_lines(check.output, max_lines=8)))
        lines.append(f"Result: {'ready' if self.ok else 'blocked'}")
        return "\n".join(lines)


def default_smoke_commands(*, include_build: bool = True) -> list[SmokeCommand]:
    commands = [
        SmokeCommand("unit-tests", ("uv", "run", "pytest")),
        SmokeCommand("lint", ("uv", "run", "ruff", "check", "src", "tests")),
        SmokeCommand("doctor-strict", ("uv", "run", "code-agent", "doctor", "--strict")),
        SmokeCommand("offline-evals", ("uv", "run", "code-agent", "evals")),
    ]
    if include_build:
        commands.append(SmokeCommand("package-build", ("uv", "build")))
    return commands


def run_release_smoke(
    cwd: Path,
    *,
    include_build: bool = True,
    commands: list[SmokeCommand] | None = None,
) -> SmokeReport:
    checks: list[SmokeCheck] = []
    for item in commands or default_smoke_commands(include_build=include_build):
        try:
            completed = subprocess.run(
                list(item.command),
                cwd=cwd,
                text=True,
                capture_output=True,
                timeout=item.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout
            stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
            completed = subprocess.CompletedProcess(
                list(item.command),
                124,
                stdout or "",
                "\n".join(
                    part
                    for part in [stderr or "", f"Timed out after {item.timeout_seconds}s."]
                    if part
                ),
            )
        output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
        checks.append(
            SmokeCheck(
                name=item.name,
                command=list(item.command),
                ok=completed.returncode == 0,
                returncode=completed.returncode,
                output=output,
                required=item.required,
            )
        )
        if item.required and completed.returncode != 0:
            break
    return SmokeReport(checks=checks)


def _last_lines(output: str, *, max_lines: int) -> str:
    lines = output.splitlines()
    if len(lines) <= max_lines:
        return output
    return "\n".join(["...", *lines[-max_lines:]])


def _indent(output: str) -> str:
    return "\n".join(f"    {line}" for line in output.splitlines())
