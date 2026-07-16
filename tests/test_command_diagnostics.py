from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from code_agent import processes as processes_module
from code_agent.command_diagnostics import (
    annotate_incremental_scope,
    diagnose_command,
    format_diagnostic_summary,
    verification_payload_from_report,
)
from code_agent.processes import ProcessSupervisor
from code_agent.schema import RunShellAction
from code_agent.tools import ToolRegistry


@pytest.mark.parametrize(
    ("command", "output", "tool", "category", "path", "rule"),
    [
        (
            "gcc -c src/main.c",
            "src/main.c:12:7: error: expected ';' before '}' token",
            "gcc",
            "syntax",
            "src/main.c",
            None,
        ),
        (
            "cl.exe src\\main.cpp",
            "src\\main.cpp(8,4): error C2143: syntax error: missing ';'",
            "msvc",
            "syntax",
            "src/main.cpp",
            "C2143",
        ),
        (
            "cargo check",
            "error[E0308]: mismatched types\n  --> src/lib.rs:5:9",
            "cargo",
            "type",
            "src/lib.rs",
            "E0308",
        ),
        (
            "go test ./...",
            "pkg/math/math.go:14:9: undefined: total",
            "go",
            "type",
            "pkg/math/math.go",
            None,
        ),
        (
            "mypy src",
            'src/app.py:7:5: error: Incompatible types in assignment [assignment]',
            "mypy",
            "type",
            "src/app.py",
            "assignment",
        ),
        (
            "pyright src",
            'src/app.py:9:12 - error: Type "str" is not assignable to "int" (reportAssignmentType)',
            "pyright",
            "type",
            "src/app.py",
            "reportAssignmentType",
        ),
        (
            "javac App.java",
            "src/App.java:17: error: cannot find symbol",
            "javac",
            "type",
            "src/App.java",
            None,
        ),
        (
            "npx tsc --noEmit",
            "src/app.ts(4,10): error TS2322: Type 'string' is not assignable to type 'number'.",
            "typescript",
            "type",
            "src/app.ts",
            "TS2322",
        ),
        (
            "dotnet build",
            "Program.cs(21,13): error CS0103: The name 'value' does not exist in the current context",
            "dotnet",
            "type",
            "Program.cs",
            "CS0103",
        ),
        (
            "swift build",
            "Sources/App/main.swift:3:5: error: cannot find 'missing' in scope",
            "swift",
            "type",
            "Sources/App/main.swift",
            None,
        ),
        (
            "kotlinc Main.kt",
            "src/Main.kt:6:9: error: unresolved reference: missing",
            "kotlin",
            "type",
            "src/Main.kt",
            None,
        ),
    ],
)
def test_normalizes_cross_language_compiler_diagnostics(
    command: str,
    output: str,
    tool: str,
    category: str,
    path: str,
    rule: str | None,
) -> None:
    report = diagnose_command(
        command,
        stdout="",
        stderr=output,
        ordered_output=output,
        execution={"exit_code": 1},
    )

    assert report.tool == tool
    assert report.diagnostics[0].category == category
    assert report.diagnostics[0].path == path
    assert report.diagnostics[0].rule == rule
    assert report.diagnostics[0].is_primary


def test_parses_eslint_rule_and_fix_availability() -> None:
    output = "src/app.ts\n  4:7  error  'value' is never reassigned  prefer-const"

    report = diagnose_command(
        "npx eslint src/app.ts",
        stdout=output,
        stderr="",
        ordered_output=output,
        execution={"exit_code": 1},
    )

    diagnostic = report.diagnostics[0]
    assert diagnostic.path == "src/app.ts"
    assert diagnostic.rule == "prefer-const"
    assert diagnostic.category == "style"
    assert diagnostic.fix_available
    assert report.suggested_fixes[0].safe_autofix


@pytest.mark.parametrize(
    ("command", "output", "tool", "rule", "severity"),
    [
        ("ruff check src", "src/app.py:2:1: F401 imported but unused", "ruff", "F401", "error"),
        (
            "flake8 src",
            "src/app.py:4:101: E501 line too long",
            "flake8",
            "E501",
            "error",
        ),
        (
            "pylint src",
            "src/app.py:1:0: C0114 Missing module docstring",
            "pylint",
            "C0114",
            "warning",
        ),
        (
            "golangci-lint run",
            "pkg/app.go:8:2: error return value is not checked (errcheck)",
            "go",
            "errcheck",
            "error",
        ),
    ],
)
def test_normalizes_rule_based_linter_output(
    command: str,
    output: str,
    tool: str,
    rule: str,
    severity: str,
) -> None:
    report = diagnose_command(
        command,
        stdout=output,
        stderr="",
        ordered_output=output,
        execution={"exit_code": 1},
    )

    diagnostic = report.diagnostics[0]
    assert report.tool == tool
    assert diagnostic.category == "style"
    assert diagnostic.rule == rule
    assert diagnostic.severity == severity


def test_parses_maven_compiler_location() -> None:
    output = "[ERROR] src/main/java/App.java:[12,8] cannot find symbol"

    report = diagnose_command(
        "mvn test",
        stdout=output,
        stderr="",
        ordered_output=output,
        execution={"exit_code": 1},
    )

    diagnostic = report.diagnostics[0]
    assert diagnostic.path == "src/main/java/App.java"
    assert diagnostic.line == 12
    assert diagnostic.column == 8
    assert diagnostic.category == "type"


def test_parses_python_traceback_and_highlights_user_code(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("def run():\n    raise ValueError('bad')\n", encoding="utf-8")
    output = (
        "Traceback (most recent call last):\n"
        f'  File "{source}", line 2, in run\n'
        "ValueError: bad\n"
    )

    report = diagnose_command(
        "python app.py",
        stdout="",
        stderr=output,
        ordered_output=output,
        execution={"exit_code": 1},
        workspace=tmp_path,
    )

    diagnostic = report.diagnostics[0]
    assert diagnostic.category == "runtime"
    assert diagnostic.line == 2
    assert "raise ValueError" in diagnostic.snippet


def test_extracts_failed_test_expected_actual_and_focused_rerun() -> None:
    output = (
        "FAILED tests/test_math.py::test_average - assert 12 == 4\n"
        "Expected: 4\n"
        "Actual: 12\n"
    )

    report = diagnose_command(
        "uv run pytest",
        stdout=output,
        stderr="",
        ordered_output=output,
        execution={"exit_code": 1},
    )
    payload = verification_payload_from_report(report.as_payload())

    assert report.failed_tests[0].name == "tests/test_math.py::test_average"
    assert report.failed_tests[0].expected == "4"
    assert report.failed_tests[0].actual == "12"
    assert payload["failed_tests"] == ["tests/test_math.py::test_average"]
    assert payload["focused_rerun_commands"] == [
        "uv run pytest tests/test_math.py::test_average"
    ]


@pytest.mark.parametrize(
    ("command", "output", "expected"),
    [
        ("npm test -- --runInBand", "● math › averages values", "math › averages values"),
        ("dotnet test", "Failed Namespace.Tests.AddsValues [12 ms]", "Namespace.Tests.AddsValues"),
        (
            "mvn test",
            "testAverage(com.example.MathTest) Time elapsed 0.01 s <<< FAILURE!",
            "testAverage",
        ),
        ("go test ./...", "--- FAIL: TestAverage (0.00s)", "TestAverage"),
        ("cargo test", "test tests::average ... FAILED", "tests::average"),
        ("python -m unittest", "FAIL: test_average (tests.MathTests)", "test_average"),
        ("nunit3-console tests.dll", "1) Failed : Tests.MathTests.Average", "Tests.MathTests.Average"),
    ],
)
def test_extracts_failed_tests_across_runners(
    command: str,
    output: str,
    expected: str,
) -> None:
    report = diagnose_command(
        command,
        stdout=output,
        stderr="",
        ordered_output=output,
        execution={"exit_code": 1},
    )

    assert report.failed_tests[0].name == expected


def test_detects_timeout_oom_permission_network_and_database() -> None:
    output = (
        "Permission denied\n"
        "connection refused\n"
        "SQLSTATE 40001 deadlock detected\n"
        "cannot allocate memory\n"
    )
    report = diagnose_command(
        "docker build .",
        stdout="",
        stderr=output,
        ordered_output=output,
        execution={"exit_code": 137, "timed_out": True, "timeout_seconds": 30},
    )

    categories = {item.category for item in report.diagnostics}
    assert {"infrastructure", "environment", "permission", "network", "database"} <= categories
    assert report.category == "permission"


def test_deduplicates_lsp_and_compiler_diagnostics() -> None:
    output = "src/app.py:3:5: error: Incompatible type"
    report = diagnose_command(
        "mypy src",
        stdout="",
        stderr=output,
        ordered_output=output,
        execution={"exit_code": 1},
        lsp_diagnostics=[
            {
                "path": "src/app.py",
                "line": 3,
                "column": 5,
                "severity": "error",
                "message": "Incompatible type",
                "source": "pyright",
            }
        ],
    )

    assert len(report.diagnostics) == 1
    assert report.diagnostics[0].occurrence_count == 2
    assert set(report.diagnostics[0].related_sources) == {"stderr", "pyright"}


def test_incremental_scope_marks_changed_dependent_and_related_paths() -> None:
    payload = {
        "affected_paths": ["src/app.py", "src/service.py", "tests/test_app.py"],
        "diagnostics": [
            {"path": "src/app.py"},
            {"path": "src/service.py"},
            {"path": "tests/test_app.py"},
        ],
    }

    annotate_incremental_scope(
        payload,
        ["src/app.py"],
        ["src/service.py"],
    )

    assert [item["incremental_scope"] for item in payload["diagnostics"]] == [
        "changed",
        "dependent",
        "related",
    ]
    assert payload["incremental"]["changed_paths"] == ["src/app.py"]


def test_formats_clickable_style_file_line_summary() -> None:
    output = "src/app.ts(4,10): error TS2322: wrong type"
    report = diagnose_command(
        "npx tsc --noEmit",
        stdout="",
        stderr=output,
        ordered_output=output,
        execution={"exit_code": 1},
    )

    summary = format_diagnostic_summary(report)

    assert "src/app.ts:4:10" in summary
    assert "[TS2322]" in summary
    assert "Root cause:" in summary


def test_process_supervisor_preserves_streams_order_and_metadata(tmp_path: Path) -> None:
    script = tmp_path / "emit.py"
    script.write_text(
        """
import sys
import time
print("out-1", flush=True)
time.sleep(0.05)
print("err-1", file=sys.stderr, flush=True)
time.sleep(0.05)
print("out-2", flush=True)
""".strip(),
        encoding="utf-8",
    )

    result = ProcessSupervisor().run_shell(
        f'python "{script}"',
        cwd=tmp_path,
        timeout_seconds=10,
        env={"PATH": "", "SAFE": "1"},
    )

    assert result.completed.returncode == 0
    assert result.completed.stdout == "out-1\nout-2\n"
    assert result.completed.stderr == "err-1\n"
    assert [event.stream for event in result.events] == ["stdout", "stderr", "stdout"]
    assert [event.sequence for event in result.events] == [1, 2, 3]
    assert all(event.timestamp for event in result.events)
    assert result.duration_ms > 0
    assert result.metadata["cwd"] == str(tmp_path)
    assert result.metadata["environment_keys"] == ["PATH", "SAFE"]
    assert len(result.metadata["environment_sha256"]) == 64


def test_process_capture_drains_but_bounds_large_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(processes_module, "MAX_PROCESS_STREAM_CHARS", 100)
    script = tmp_path / "large.py"
    script.write_text("print('x' * 1000, flush=True)", encoding="utf-8")

    result = ProcessSupervisor().run_shell(
        f'python "{script}"',
        cwd=tmp_path,
        timeout_seconds=10,
        env={"PATH": ""},
    )

    assert result.completed.returncode == 0
    assert len(result.completed.stdout) < 200
    assert "<truncated" in result.completed.stdout
    assert result.metadata["capture"]["stdout_chars"] == 1001
    assert result.metadata["capture"]["stdout_truncated"] is True


def test_report_payload_is_machine_readable() -> None:
    report = diagnose_command(
        "go test ./...",
        stdout="",
        stderr="pkg/app.go:4:2: undefined: missing",
        ordered_output="pkg/app.go:4:2: undefined: missing",
        execution={"exit_code": 1},
    )

    rendered = json.dumps(report.as_payload())

    assert '"signature"' in rendered
    assert '"diagnostics"' in rendered


def test_shell_tool_exposes_separate_logs_execution_and_normalized_json(
    tmp_path: Path,
    monkeypatch,
) -> None:
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=False,
        approval_callback=lambda *_args: True,
    )
    stderr = "src/app.ts(4,10): error TS2322: wrong type TOKEN=super-secret-token\n"
    events = [
        {
            "sequence": 1,
            "stream": "stdout",
            "text": "building\n",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "offset_ms": 1.0,
        },
        {
            "sequence": 2,
            "stream": "stderr",
            "text": stderr,
            "timestamp": "2026-01-01T00:00:00.010000+00:00",
            "offset_ms": 10.0,
        },
    ]
    monkeypatch.setattr(
        tools,
        "_run_shell_process",
        lambda *_args, **_kwargs: (
            subprocess.CompletedProcess(["npm"], 1, "building\n", stderr),
            False,
            "building\n" + stderr,
            False,
            False,
            {
                "duration_ms": 12.0,
                "cwd": str(tmp_path),
                "argv": ["npm", "run", "build"],
                "environment_keys": ["PATH"],
                "environment_sha256": "a" * 64,
            },
            events,
            12.0,
        ),
    )

    result = tools.run(RunShellAction(type="run_shell", command="npm run build"))

    assert not result.ok
    assert result.metadata["execution"]["exit_code"] == 1
    assert result.metadata["execution"]["duration_ms"] == 12.0
    assert result.metadata["logs"]["stdout"]["text"] == "building\n"
    assert result.metadata["logs"]["stderr"]["text"] == (
        "src/app.ts(4,10): error TS2322: wrong type TOKEN=[REDACTED]\n"
    )
    assert [
        item["stream"] for item in result.metadata["logs"]["events"]["items"]
    ] == ["stdout", "stderr"]
    assert result.metadata["diagnostics"]["diagnostics"][0]["path"] == "src/app.ts"
    assert "super-secret-token" not in json.dumps(result.metadata)
    assert "src/app.ts:4:10" in result.output
