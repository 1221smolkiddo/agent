from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class VerificationDiagnostics:
    runner: str
    summary: str
    failed_tests: list[str] = field(default_factory=list)
    failed_files: list[str] = field(default_factory=list)
    assertions: list[str] = field(default_factory=list)
    suggested_focus: list[str] = field(default_factory=list)

    def as_payload(self) -> dict[str, object]:
        return {
            "runner": self.runner,
            "summary": self.summary,
            "failed_tests": self.failed_tests,
            "failed_files": self.failed_files,
            "assertions": self.assertions,
            "suggested_focus": self.suggested_focus,
        }


def diagnose_verification_failure(command: str, output: str) -> dict[str, object] | None:
    normalized = command.lower()
    if "pytest" in normalized:
        return diagnose_pytest_failure(output).as_payload()
    return None


def diagnose_pytest_failure(output: str) -> VerificationDiagnostics:
    failed_tests = _pytest_failed_tests(output)
    failed_files = _failed_files_from_tests(failed_tests)
    assertions = _pytest_assertions(output)
    suggested_focus = _suggested_focus(failed_files)
    summary = _pytest_summary(failed_tests, assertions)
    return VerificationDiagnostics(
        runner="pytest",
        summary=summary,
        failed_tests=failed_tests,
        failed_files=failed_files,
        assertions=assertions,
        suggested_focus=suggested_focus,
    )


def _pytest_failed_tests(output: str) -> list[str]:
    failed: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        match = re.match(r"FAILED\s+(.+?)(?:\s+-\s+.*)?$", stripped)
        if match:
            failed.append(match.group(1))
    return _dedupe(failed)


def _failed_files_from_tests(failed_tests: list[str]) -> list[str]:
    files: list[str] = []
    for test in failed_tests:
        path = test.split("::", 1)[0]
        if path:
            files.append(path)
    return _dedupe(files)


def _pytest_assertions(output: str) -> list[str]:
    assertions: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("E       AssertionError"):
            assertions.append(stripped[8:].strip() or "AssertionError")
        elif stripped.startswith("E       assert "):
            assertions.append(stripped[8:].strip())
    return _dedupe(assertions)[:5]


def _suggested_focus(failed_files: list[str]) -> list[str]:
    focus: list[str] = []
    for path in failed_files:
        if path.startswith("tests/") or path.startswith("test/"):
            focus.append(path)
            continue
        focus.append(path)
    return _dedupe(focus)[:8]


def _pytest_summary(failed_tests: list[str], assertions: list[str]) -> str:
    if failed_tests and assertions:
        return f"{failed_tests[0]} failed: {assertions[0]}"
    if failed_tests:
        count = len(failed_tests)
        noun = "test" if count == 1 else "tests"
        return f"{count} pytest {noun} failed; first failure: {failed_tests[0]}"
    if assertions:
        return f"pytest failed: {assertions[0]}"
    return "pytest failed; inspect the output for details"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped
