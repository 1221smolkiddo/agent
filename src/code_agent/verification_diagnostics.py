from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class VerificationDiagnostics:
    runner: str
    summary: str
    failed_tests: list[str] = field(default_factory=list)
    failed_files: list[str] = field(default_factory=list)
    likely_source_files: list[str] = field(default_factory=list)
    assertions: list[str] = field(default_factory=list)
    suggested_focus: list[str] = field(default_factory=list)
    focused_rerun_commands: list[str] = field(default_factory=list)

    def as_payload(self) -> dict[str, object]:
        return {
            "runner": self.runner,
            "summary": self.summary,
            "failed_tests": self.failed_tests,
            "failed_files": self.failed_files,
            "likely_source_files": self.likely_source_files,
            "assertions": self.assertions,
            "suggested_focus": self.suggested_focus,
            "focused_rerun_commands": self.focused_rerun_commands,
        }


def diagnose_verification_failure(command: str, output: str) -> dict[str, object] | None:
    normalized = command.lower()
    if "pytest" in normalized:
        return diagnose_pytest_failure(command, output).as_payload()
    if _is_node_test_command(normalized):
        return diagnose_node_test_failure(command, output).as_payload()
    return None


def diagnose_pytest_failure(command: str, output: str | None = None) -> VerificationDiagnostics:
    if output is None:
        output = command
        command = "python -m pytest"
    failed_tests = _pytest_failed_tests(output)
    failed_files = _failed_files_from_tests(failed_tests)
    likely_source_files = _likely_python_source_files(failed_files)
    assertions = _pytest_assertions(output)
    suggested_focus = _suggested_focus(failed_files, likely_source_files)
    focused_rerun_commands = _pytest_rerun_commands(command, failed_tests, failed_files)
    summary = _pytest_summary(failed_tests, assertions)
    return VerificationDiagnostics(
        runner="pytest",
        summary=summary,
        failed_tests=failed_tests,
        failed_files=failed_files,
        likely_source_files=likely_source_files,
        assertions=assertions,
        suggested_focus=suggested_focus,
        focused_rerun_commands=focused_rerun_commands,
    )


def diagnose_node_test_failure(command: str, output: str) -> VerificationDiagnostics:
    failed_tests = _node_failed_tests(output)
    failed_files = _node_failed_files(output)
    likely_source_files = _likely_node_source_files(failed_files)
    assertions = _node_assertions(output)
    suggested_focus = _suggested_focus(failed_files, likely_source_files)
    summary = _node_summary(failed_tests, failed_files, assertions)
    return VerificationDiagnostics(
        runner="node",
        summary=summary,
        failed_tests=failed_tests,
        failed_files=failed_files,
        likely_source_files=likely_source_files,
        assertions=assertions,
        suggested_focus=suggested_focus,
        focused_rerun_commands=[command],
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


def _likely_python_source_files(failed_files: list[str]) -> list[str]:
    candidates: list[str] = []
    for path in failed_files:
        normalized = path.replace("\\", "/")
        name = normalized.rsplit("/", 1)[-1]
        if not name.startswith("test_") or not name.endswith(".py"):
            continue
        source_name = name.removeprefix("test_")
        candidates.append(source_name)
        candidates.append(f"src/{source_name}")
        parent = normalized.rsplit("/", 1)[0] if "/" in normalized else ""
        if parent.startswith("tests/"):
            candidates.append(f"src/{parent.removeprefix('tests/')}/{source_name}")
    return _dedupe(candidates)[:8]


def _suggested_focus(failed_files: list[str], likely_source_files: list[str]) -> list[str]:
    focus: list[str] = []
    for path in [*failed_files, *likely_source_files]:
        focus.append(path)
    return _dedupe(focus)[:8]


def _pytest_rerun_commands(command: str, failed_tests: list[str], failed_files: list[str]) -> list[str]:
    base = command.strip() or "python -m pytest"
    targets = failed_tests or failed_files
    return [f"{base} {target}" for target in targets[:3]]


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


def _is_node_test_command(command: str) -> bool:
    return (
        "npm run test" in command
        or "npm test" in command
        or "node --test" in command
        or "vitest" in command
        or "jest" in command
    )


def _node_failed_tests(output: str) -> list[str]:
    failed: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        for pattern in [
            r"(?:not ok|FAIL)\s+\d*\s*[- ]\s*(.+)$",
            r"×\s+(.+)$",
            r"✕\s+(.+)$",
            r"test\(['\"](.+?)['\"]",
        ]:
            match = re.search(pattern, stripped)
            if match:
                failed.append(match.group(1).strip())
                break
    return _dedupe(failed)[:8]


def _node_failed_files(output: str) -> list[str]:
    files: list[str] = []
    for line in output.splitlines():
        for match in re.finditer(r"([A-Za-z0-9_./\\-]+(?:\.test|\.spec)\.[cm]?[jt]sx?)(?::\d+)?", line):
            files.append(match.group(1).replace("\\", "/"))
        for match in re.finditer(r"(?:FAIL|File)\s+([A-Za-z0-9_./\\-]+\.[cm]?[jt]sx?)", line.strip()):
            files.append(match.group(1).replace("\\", "/"))
    return _dedupe(files)[:8]


def _likely_node_source_files(failed_files: list[str]) -> list[str]:
    candidates: list[str] = []
    for path in failed_files:
        normalized = path.replace("\\", "/")
        candidates.append(
            re.sub(r"(?:(?:\.test)|(?:\.spec))(\.[cm]?[jt]sx?)$", r"\1", normalized)
        )
        name = normalized.rsplit("/", 1)[-1]
        source_name = re.sub(r"(?:(?:\.test)|(?:\.spec))(\.[cm]?[jt]sx?)$", r"\1", name)
        if normalized.startswith("test/") or normalized.startswith("tests/"):
            candidates.append(source_name)
            candidates.append(f"src/{source_name}")
    return _dedupe([item for item in candidates if item and item not in failed_files])[:8]


def _node_assertions(output: str) -> list[str]:
    assertions: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if re.search(r"\bExpected\b|\bReceived\b|AssertionError|ERR_ASSERTION|expected .* to ", stripped, re.I):
            assertions.append(stripped)
    return _dedupe(assertions)[:5]


def _node_summary(failed_tests: list[str], failed_files: list[str], assertions: list[str]) -> str:
    if failed_tests and assertions:
        return f"{failed_tests[0]} failed: {assertions[0]}"
    if failed_files and assertions:
        return f"{failed_files[0]} failed: {assertions[0]}"
    if failed_tests:
        return f"node test failed; first failure: {failed_tests[0]}"
    if failed_files:
        return f"node test failed in {failed_files[0]}"
    if assertions:
        return f"node test failed: {assertions[0]}"
    return "node test failed; inspect the output for details"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped
