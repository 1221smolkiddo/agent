from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable


CATEGORY_PRIORITY = {
    "syntax": 0,
    "type": 1,
    "import": 2,
    "dependency": 3,
    "build": 4,
    "linker": 5,
    "runtime": 6,
    "filesystem": 7,
    "permission": 8,
    "network": 9,
    "database": 10,
    "test": 11,
    "infrastructure": 12,
    "environment": 13,
    "style": 14,
    "unknown": 15,
}
SEVERITY_PRIORITY = {"error": 0, "warning": 1, "information": 2, "hint": 3}
MAX_NORMALIZED_DIAGNOSTICS = 200
MAX_FAILED_TESTS = 100
MAX_DIAGNOSTIC_TEXT = 2000


@dataclass(frozen=True)
class SuggestedFix:
    title: str
    detail: str
    confidence: str
    command: str | None = None
    safe_autofix: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "detail": self.detail,
            "confidence": self.confidence,
            "command": self.command,
            "safe_autofix": self.safe_autofix,
        }


@dataclass(frozen=True)
class FailedTest:
    name: str
    path: str | None = None
    line: int | None = None
    message: str = ""
    expected: str | None = None
    actual: str | None = None
    stack: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "line": self.line,
            "message": self.message,
            "expected": self.expected,
            "actual": self.actual,
            "stack": list(self.stack),
        }


@dataclass(frozen=True)
class NormalizedDiagnostic:
    tool: str
    source: str
    category: str
    severity: str
    message: str
    path: str | None = None
    line: int | None = None
    column: int | None = None
    end_line: int | None = None
    end_column: int | None = None
    rule: str | None = None
    raw: str = ""
    snippet: str = ""
    fix_available: bool = False
    is_primary: bool = False
    occurrence_count: int = 1
    related_sources: tuple[str, ...] = ()

    @property
    def location(self) -> str:
        if not self.path:
            return ""
        suffix = f":{self.line}" if self.line is not None else ""
        if self.column is not None:
            suffix += f":{self.column}"
        return self.path + suffix

    def as_payload(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "source": self.source,
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "rule": self.rule,
            "raw": self.raw,
            "snippet": self.snippet,
            "fix_available": self.fix_available,
            "is_primary": self.is_primary,
            "occurrence_count": self.occurrence_count,
            "related_sources": list(self.related_sources),
            "location": self.location,
        }


@dataclass(frozen=True)
class CommandDiagnosticReport:
    tool: str
    command: str
    category: str
    summary: str
    root_cause: str
    diagnostics: tuple[NormalizedDiagnostic, ...] = ()
    failed_tests: tuple[FailedTest, ...] = ()
    suggested_fixes: tuple[SuggestedFix, ...] = ()
    cascade_count: int = 0
    signature: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    affected_paths: tuple[str, ...] = ()
    dependency_chain: tuple[str, ...] = ()
    total_diagnostics: int = 0
    diagnostics_truncated: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "command": self.command,
            "category": self.category,
            "summary": self.summary,
            "root_cause": self.root_cause,
            "diagnostics": [item.as_payload() for item in self.diagnostics],
            "failed_tests": [item.as_payload() for item in self.failed_tests],
            "suggested_fixes": [item.as_payload() for item in self.suggested_fixes],
            "cascade_count": self.cascade_count,
            "signature": self.signature,
            "counts": self.counts,
            "affected_paths": list(self.affected_paths),
            "dependency_chain": list(self.dependency_chain),
            "total_diagnostics": self.total_diagnostics,
            "diagnostics_truncated": self.diagnostics_truncated,
        }


FILE_COLON_PATTERN = re.compile(
    r"^(?P<path>(?:[A-Za-z]:)?[^:\n]+?):(?P<line>\d+)"
    r"(?::(?P<column>\d+))?:\s*(?P<severity>fatal error|error|warning|note|info(?:rmation)?)"
    r"(?:\s+(?P<rule>[A-Za-z][A-Za-z0-9_.-]*\d+|[A-Z]\d{3,5}))?:?\s*(?P<message>.+)$",
    re.IGNORECASE,
)
FILE_PAREN_PATTERN = re.compile(
    r"^(?P<path>.+?)\((?P<line>\d+)(?:,(?P<column>\d+))?\):\s*"
    r"(?P<severity>fatal error|error|warning|info(?:rmation)?)\s*"
    r"(?P<rule>[A-Za-z]+\d+|[A-Za-z][A-Za-z0-9_.-]+)?:?\s*(?P<message>.+)$",
    re.IGNORECASE,
)
PYRIGHT_PATTERN = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?P<column>\d+)\s+-\s+"
    r"(?P<severity>error|warning|information):\s*(?P<message>.+?)"
    r"(?:\s+\((?P<rule>report[A-Za-z0-9]+)\))?$",
    re.IGNORECASE,
)
RUST_LOCATION_PATTERN = re.compile(
    r"^\s*-->\s+(?P<path>.+?):(?P<line>\d+):(?P<column>\d+)\s*$"
)
RUST_HEADER_PATTERN = re.compile(
    r"^(?P<severity>error|warning)(?:\[(?P<rule>[A-Z]\d+)\])?:\s*(?P<message>.+)$",
    re.IGNORECASE,
)
PYTHON_FRAME_PATTERN = re.compile(r'^\s*File "(?P<path>.+?)", line (?P<line>\d+), in .+$')
PYTHON_EXCEPTION_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Warning))(?::\s*(?P<message>.*))?$"
)
ESLINT_DETAIL_PATTERN = re.compile(
    r"^\s*(?P<line>\d+):(?P<column>\d+)\s+"
    r"(?P<severity>error|warning)\s+(?P<message>.+?)\s{2,}(?P<rule>[@A-Za-z0-9_./-]+)\s*$"
)
GO_DIAGNOSTIC_PATTERN = re.compile(
    r"^(?P<path>.+?\.go):(?P<line>\d+):(?P<column>\d+):\s*(?P<message>.+)$"
)
RULE_LOCATION_PATTERN = re.compile(
    r"^(?P<path>(?:[A-Za-z]:)?[^:\n]+?):(?P<line>\d+)"
    r"(?::(?P<column>\d+))?:\s*(?P<rule>[@A-Za-z][@A-Za-z0-9_.-]*\d*)"
    r"[:\s]+(?P<message>.+)$"
)
MAVEN_LOCATION_PATTERN = re.compile(
    r"^\[(?P<severity>ERROR|WARNING)\]\s+(?P<path>.+?):"
    r"\[(?P<line>\d+),(?P<column>\d+)\]\s+(?P<message>.+)$"
)


def diagnose_command(
    command: str,
    *,
    stdout: str,
    stderr: str,
    ordered_output: str,
    execution: dict[str, Any],
    workspace: Path | None = None,
    lsp_diagnostics: Iterable[dict[str, Any]] = (),
) -> CommandDiagnosticReport:
    tool = detect_tool(command, ordered_output)
    diagnostics = _parse_output(tool, command, stdout, stderr)
    diagnostics.extend(_execution_diagnostics(tool, command, ordered_output, execution))
    diagnostics.extend(_normalize_lsp_diagnostics(lsp_diagnostics))
    diagnostics = [_bound_diagnostic(item) for item in diagnostics]
    diagnostics = _deduplicate(diagnostics)
    diagnostics = _prioritize(diagnostics)
    total_diagnostics = len(diagnostics)
    diagnostics = diagnostics[:MAX_NORMALIZED_DIAGNOSTICS]
    diagnostics = _enrich_snippets(diagnostics, workspace)
    failed_tests = _parse_failed_tests(tool, ordered_output)[:MAX_FAILED_TESTS]
    if diagnostics:
        diagnostics[0] = replace(diagnostics[0], is_primary=True)
    root = diagnostics[0] if diagnostics else None
    category = root.category if root else _category_from_command(command)
    root_cause = _root_cause(root, execution, ordered_output)
    summary = _summary(tool, root, failed_tests, execution)
    suggestions = _suggest_fixes(tool, command, root, failed_tests, ordered_output)
    cascade_count = max(0, len(diagnostics) - 1)
    counts = _counts(diagnostics)
    signature = _signature(command, diagnostics, failed_tests)
    affected_paths = tuple(dict.fromkeys(item.path for item in diagnostics if item.path))
    return CommandDiagnosticReport(
        tool=tool,
        command=command,
        category=category,
        summary=summary,
        root_cause=root_cause,
        diagnostics=tuple(diagnostics),
        failed_tests=tuple(failed_tests),
        suggested_fixes=tuple(suggestions),
        cascade_count=cascade_count,
        signature=signature,
        counts=counts,
        affected_paths=affected_paths,
        dependency_chain=affected_paths[:8],
        total_diagnostics=total_diagnostics,
        diagnostics_truncated=total_diagnostics > len(diagnostics),
    )


def detect_tool(command: str, output: str = "") -> str:
    normalized = command.lower()
    candidates = [
        ("gcc", ("gcc", "g++")),
        ("clang", ("clang", "clang++")),
        ("msvc", ("cl.exe", "msbuild")),
        ("pytest", ("pytest",)),
        ("unittest", ("unittest",)),
        ("jest", ("jest",)),
        ("vitest", ("vitest",)),
        ("mocha", ("mocha",)),
        ("cargo", ("cargo", "rustc", "clippy")),
        ("go", ("go test", "go build", "golangci-lint")),
        ("ruff", ("ruff",)),
        ("mypy", ("mypy",)),
        ("pyright", ("pyright",)),
        ("pylint", ("pylint",)),
        ("flake8", ("flake8",)),
        ("eslint", ("eslint",)),
        ("biome", ("biome",)),
        ("typescript", ("tsc", "typescript")),
        ("vite", ("vite",)),
        ("next", ("next build", "next dev")),
        ("javac", ("javac",)),
        ("maven", ("mvn", "maven")),
        ("gradle", ("gradle", "gradlew")),
        ("dotnet", ("dotnet", "msbuild", "xunit", "nunit")),
        ("cmake", ("cmake",)),
        ("make", ("make", "ninja")),
        ("bazel", ("bazel",)),
        ("swift", ("swift", "swiftc")),
        ("kotlin", ("kotlinc", "kotlin")),
        ("docker", ("docker", "podman")),
    ]
    for tool, tokens in candidates:
        if any(token in normalized for token in tokens):
            return tool
    lowered_output = output.lower()
    if "traceback (most recent call last)" in lowered_output:
        return "python"
    if "error cs" in lowered_output:
        return "dotnet"
    return "command"


def format_diagnostic_summary(report: CommandDiagnosticReport, max_items: int = 8) -> str:
    lines = [
        f"Diagnostics: {report.summary}",
        f"Root cause: {report.root_cause}",
    ]
    for item in report.diagnostics[:max_items]:
        location = f"{item.location}: " if item.location else ""
        rule = f" [{item.rule}]" if item.rule else ""
        lines.append(
            f"- {location}{item.severity.upper()} {item.category}{rule}: {item.message}"
        )
    if len(report.diagnostics) > max_items:
        lines.append(f"- <{len(report.diagnostics) - max_items} additional diagnostics>")
    for fix in report.suggested_fixes[:3]:
        command = f" Run `{fix.command}`." if fix.command else ""
        lines.append(f"- Suggested fix ({fix.confidence}): {fix.detail}{command}")
    return "\n".join(lines)


def verification_payload_from_report(payload: dict[str, Any]) -> dict[str, Any]:
    failed_tests = [
        str(item.get("name"))
        for item in payload.get("failed_tests", [])
        if isinstance(item, dict) and item.get("name")
    ]
    diagnostics = [
        item for item in payload.get("diagnostics", []) if isinstance(item, dict)
    ]
    failed_files = _dedupe_strings(
        [
            str(item.get("path"))
            for item in diagnostics
            if item.get("path") and item.get("severity") == "error"
        ]
        + [
            str(item.get("path"))
            for item in payload.get("failed_tests", [])
            if isinstance(item, dict) and item.get("path")
        ]
    )
    assertions = _dedupe_strings(
        [
            str(item.get("message"))
            for item in payload.get("failed_tests", [])
            if isinstance(item, dict) and item.get("message")
        ]
        + [
            str(item.get("message"))
            for item in diagnostics
            if item.get("category") == "test" and item.get("message")
        ]
    )[:5]
    rerun_commands = [
        str(item.get("command"))
        for item in payload.get("suggested_fixes", [])
        if isinstance(item, dict) and item.get("command")
    ]
    likely_source_files = _likely_source_files(failed_files)
    return {
        "runner": str(payload.get("tool") or "command"),
        "summary": str(payload.get("summary") or "verification failed"),
        "failed_tests": failed_tests,
        "failed_files": failed_files,
        "likely_source_files": likely_source_files,
        "assertions": assertions,
        "suggested_focus": _dedupe_strings([*failed_files, *likely_source_files])[:8],
        "focused_rerun_commands": rerun_commands[:3],
        "root_cause": payload.get("root_cause"),
        "category": payload.get("category"),
        "normalized_diagnostics": diagnostics,
        "suggested_fixes": payload.get("suggested_fixes", []),
        "signature": payload.get("signature"),
        "counts": payload.get("counts", {}),
        "affected_paths": payload.get("affected_paths", []),
        "dependency_chain": payload.get("dependency_chain", []),
    }


def annotate_incremental_scope(
    payload: dict[str, Any],
    changed_paths: Iterable[str],
    dependent_paths: Iterable[str] = (),
) -> dict[str, Any]:
    changed = {_normalize_path(path) for path in changed_paths}
    dependents = {_normalize_path(path) for path in dependent_paths}
    for item in payload.get("diagnostics", []):
        if not isinstance(item, dict):
            continue
        path = _normalize_path(str(item.get("path") or ""))
        item["incremental_scope"] = (
            "changed"
            if path in changed
            else "dependent"
            if path in dependents
            else "related"
        )
    payload["incremental"] = {
        "changed_paths": sorted(changed),
        "dependent_paths": sorted(dependents),
        "affected_paths": payload.get("affected_paths", []),
    }
    return payload


def _parse_output(
    tool: str,
    command: str,
    stdout: str,
    stderr: str,
) -> list[NormalizedDiagnostic]:
    diagnostics: list[NormalizedDiagnostic] = []
    rust_header: tuple[str, str | None, str, str] | None = None
    python_frames: list[tuple[str, int, str]] = []
    eslint_path: str | None = None
    for source, output in (("stdout", stdout), ("stderr", stderr)):
        for line in output.splitlines():
            stripped = _strip_ansi(line).rstrip()
            if not stripped:
                continue
            pyright = PYRIGHT_PATTERN.match(stripped)
            if pyright:
                diagnostics.append(_match_diagnostic(tool, source, pyright, stripped, command))
                continue
            maven = MAVEN_LOCATION_PATTERN.match(stripped)
            if maven:
                diagnostics.append(_match_diagnostic(tool, source, maven, stripped, command))
                continue
            rust = RUST_HEADER_PATTERN.match(stripped)
            if rust and tool == "cargo":
                rust_header = (
                    _severity(rust.group("severity")),
                    rust.group("rule"),
                    rust.group("message"),
                    stripped,
                )
                continue
            rust_location = RUST_LOCATION_PATTERN.match(stripped)
            if rust_location and rust_header:
                severity, rule, message, raw = rust_header
                diagnostics.append(
                    NormalizedDiagnostic(
                        tool=tool,
                        source=source,
                        category=_categorize(message, rule, command),
                        severity=severity,
                        message=message,
                        path=_normalize_path(rust_location.group("path")),
                        line=int(rust_location.group("line")),
                        column=int(rust_location.group("column")),
                        rule=rule,
                        raw=f"{raw}\n{stripped}",
                    )
                )
                rust_header = None
                continue
            match = FILE_PAREN_PATTERN.match(stripped) or FILE_COLON_PATTERN.match(stripped)
            if match:
                diagnostics.append(_match_diagnostic(tool, source, match, stripped, command))
                continue
            rule_match = RULE_LOCATION_PATTERN.match(stripped)
            if rule_match and tool in {
                "ruff",
                "mypy",
                "pylint",
                "flake8",
                "eslint",
                "biome",
                "javac",
                "maven",
                "gradle",
            }:
                category = (
                    "style"
                    if tool in {"ruff", "pylint", "flake8", "eslint", "biome"}
                    else _categorize(
                        rule_match.group("message"),
                        rule_match.group("rule"),
                        command,
                    )
                )
                diagnostics.append(
                    NormalizedDiagnostic(
                        tool=tool,
                        source=source,
                        category=category,
                        severity=_rule_severity(rule_match.group("rule")),
                        message=rule_match.group("message"),
                        path=_normalize_path(rule_match.group("path")),
                        line=int(rule_match.group("line")),
                        column=_int(rule_match.group("column")),
                        rule=rule_match.group("rule"),
                        raw=stripped,
                        fix_available=_fix_available(
                            tool,
                            rule_match.group("message"),
                            rule_match.group("rule"),
                        ),
                    )
                )
                continue
            go_match = GO_DIAGNOSTIC_PATTERN.match(stripped)
            if go_match and tool == "go":
                message = go_match.group("message")
                trailing_rule = re.search(r"\s+\((?P<rule>[-A-Za-z0-9_.]+)\)\s*$", message)
                rule = trailing_rule.group("rule") if trailing_rule else None
                if trailing_rule:
                    message = message[: trailing_rule.start()].rstrip()
                diagnostics.append(
                    NormalizedDiagnostic(
                        tool=tool,
                        source=source,
                        category=_categorize(message, rule, command),
                        severity="error",
                        message=message,
                        path=_normalize_path(go_match.group("path")),
                        line=int(go_match.group("line")),
                        column=int(go_match.group("column")),
                        rule=rule,
                        raw=stripped,
                    )
                )
                continue
            frame = PYTHON_FRAME_PATTERN.match(stripped)
            if frame:
                python_frames.append(
                    (_normalize_path(frame.group("path")), int(frame.group("line")), stripped)
                )
                continue
            exception = PYTHON_EXCEPTION_PATTERN.match(stripped)
            if exception and python_frames:
                path, line_number, frame_raw = python_frames[-1]
                message = exception.group("message") or exception.group("name")
                diagnostics.append(
                    NormalizedDiagnostic(
                        tool=tool,
                        source=source,
                        category=_categorize(
                            f"{exception.group('name')}: {message}", None, command
                        ),
                        severity="error",
                        message=f"{exception.group('name')}: {message}",
                        path=path,
                        line=line_number,
                        raw=f"{frame_raw}\n{stripped}",
                    )
                )
                continue
            if tool == "eslint":
                if _looks_like_source_path(stripped):
                    eslint_path = _normalize_path(stripped)
                    continue
                eslint = ESLINT_DETAIL_PATTERN.match(stripped)
                if eslint and eslint_path:
                    diagnostics.append(
                        NormalizedDiagnostic(
                            tool=tool,
                            source=source,
                            category="style",
                            severity=_severity(eslint.group("severity")),
                            message=eslint.group("message"),
                            path=eslint_path,
                            line=int(eslint.group("line")),
                            column=int(eslint.group("column")),
                            rule=eslint.group("rule"),
                            raw=stripped,
                            fix_available=_fix_available(
                                tool,
                                eslint.group("message"),
                                eslint.group("rule"),
                            ),
                        )
                    )
                    continue
            runtime = _runtime_line(tool, source, stripped, command)
            if runtime:
                diagnostics.append(runtime)
    return diagnostics


def _match_diagnostic(
    tool: str,
    source: str,
    match: re.Match[str],
    raw: str,
    command: str,
) -> NormalizedDiagnostic:
    values = match.groupdict()
    message = values.get("message") or raw
    rule = values.get("rule")
    if not rule:
        trailing_rule = re.search(r"\s+\[(?P<rule>[@A-Za-z0-9_.-]+)\]\s*$", message)
        if trailing_rule:
            rule = trailing_rule.group("rule")
            message = message[: trailing_rule.start()].rstrip()
    if not rule and tool == "go":
        trailing_rule = re.search(r"\s+\((?P<rule>[-A-Za-z0-9_.]+)\)\s*$", message)
        if trailing_rule:
            rule = trailing_rule.group("rule")
            message = message[: trailing_rule.start()].rstrip()
    return NormalizedDiagnostic(
        tool=tool,
        source=source,
        category=_categorize(message, rule, command),
        severity=_severity(values.get("severity")),
        message=message.strip(),
        path=_normalize_path(values.get("path")),
        line=_int(values.get("line")),
        column=_int(values.get("column")),
        rule=rule,
        raw=raw,
        fix_available=_fix_available(tool, message, rule),
    )


def _execution_diagnostics(
    tool: str,
    command: str,
    output: str,
    execution: dict[str, Any],
) -> list[NormalizedDiagnostic]:
    diagnostics: list[NormalizedDiagnostic] = []
    returncode = execution.get("exit_code")
    lowered = output.lower()
    if execution.get("timed_out"):
        diagnostics.append(
            NormalizedDiagnostic(
                tool=tool,
                source="process",
                category="infrastructure",
                severity="error",
                message=f"Command timed out after {execution.get('timeout_seconds', '?')} seconds.",
                raw="timeout",
            )
        )
    if execution.get("cancelled"):
        diagnostics.append(
            NormalizedDiagnostic(
                tool=tool,
                source="process",
                category="infrastructure",
                severity="error",
                message="Command was cancelled before completion.",
                raw="cancelled",
            )
        )
    if returncode in {137, -9} or any(
        token in lowered for token in ("out of memory", "oomkilled", "cannot allocate memory")
    ):
        diagnostics.append(
            NormalizedDiagnostic(
                tool=tool,
                source="process",
                category="environment",
                severity="error",
                message="Process was likely terminated because memory was exhausted.",
                raw=f"exit_code={returncode}",
            )
        )
    if execution.get("signal"):
        diagnostics.append(
            NormalizedDiagnostic(
                tool=tool,
                source="process",
                category="runtime",
                severity="error",
                message=f"Process terminated by signal {execution['signal']}.",
                raw=f"signal={execution['signal']}",
            )
        )
    return diagnostics


def _runtime_line(
    tool: str,
    source: str,
    line: str,
    command: str,
) -> NormalizedDiagnostic | None:
    lowered = line.lower()
    patterns = [
        ("permission", ("permission denied", "access is denied", "eacces", "eperm")),
        ("network", ("connection refused", "timed out connecting", "dns", "enotfound", "econnreset")),
        ("database", ("sqlstate", "database is locked", "connection pool", "deadlock detected")),
        ("dependency", ("module not found", "cannot find module", "no module named", "package not found")),
        ("linker", ("undefined reference", "unresolved external symbol", "ld returned")),
        ("runtime", ("segmentation fault", "panic:", "fatal exception", "unhandled exception")),
        ("filesystem", ("no such file or directory", "file not found", "read-only file system")),
        ("infrastructure", ("docker daemon", "cannot connect to the docker", "container launch failed")),
        (
            "environment",
            (
                "environment variable",
                "is not set",
                "command not found",
                "not recognized as an internal",
            ),
        ),
    ]
    for category, tokens in patterns:
        if any(token in lowered for token in tokens):
            return NormalizedDiagnostic(
                tool=tool,
                source=source,
                category=category,
                severity="error",
                message=line.strip(),
                raw=line,
            )
    if re.match(r"^(error|fatal):\s+", line, re.IGNORECASE):
        message = re.sub(r"^(error|fatal):\s+", "", line, flags=re.IGNORECASE)
        return NormalizedDiagnostic(
            tool=tool,
            source=source,
            category=_categorize(message, None, command),
            severity="error",
            message=message,
            raw=line,
        )
    return None


def _parse_failed_tests(tool: str, output: str) -> list[FailedTest]:
    failed: list[FailedTest] = []
    lines = [_strip_ansi(line) for line in output.splitlines()]
    expected = _first_group(lines, r"^(?:Expected|expected):?\s*(.+)$")
    actual = _first_group(lines, r"^(?:Received|Actual|actual):?\s*(.+)$")
    for line in lines:
        stripped = line.strip()
        patterns = [
            r"^FAILED\s+(?P<name>\S+(?:::\S+)*)(?:\s+-\s+(?P<message>.+))?$",
            r"^FAIL:\s+(?P<name>.+?)\s+\((?P<path>.+?)\)$",
            r"^(?:--- FAIL:|FAIL)\s+(?P<name>[A-Za-z0-9_./:-]+)",
            r"^test\s+(?P<name>\S+)\s+\.\.\.\s+FAILED$",
            r"^[×✕]\s+(?P<name>.+)$",
            r"^not ok\s+\d+\s*-\s*(?P<name>.+)$",
            r"^[●]\s+(?P<name>.+)$",
            r"^\s*Failed\s+(?P<name>[A-Za-z0-9_.:/-]+)(?:\s+\[.+\])?$",
            r"^\d+\)\s+Failed\s*:\s*(?P<name>.+)$",
            r"^(?P<name>[A-Za-z0-9_.]+)(?:\([^)]+\))?\s+Time elapsed .+<<< FAILURE!$",
        ]
        for pattern in patterns:
            match = re.match(pattern, stripped)
            if not match:
                continue
            values = match.groupdict()
            name = values.get("name") or stripped
            path = values.get("path")
            if not path and "::" in name:
                path = name.split("::", 1)[0]
            failed.append(
                FailedTest(
                    name=name,
                    path=_normalize_path(path) if path else None,
                    message=values.get("message") or "",
                    expected=expected,
                    actual=actual,
                )
            )
            break
    return _dedupe_tests(failed)


def _normalize_lsp_diagnostics(values: Iterable[dict[str, Any]]) -> list[NormalizedDiagnostic]:
    diagnostics = []
    for item in values:
        message = str(item.get("message") or "").strip()
        if not message:
            continue
        diagnostics.append(
            NormalizedDiagnostic(
                tool="lsp",
                source=str(item.get("source") or "lsp"),
                category=_categorize(message, str(item.get("code") or "") or None, ""),
                severity=_severity(str(item.get("severity") or "error")),
                message=message,
                path=_normalize_path(str(item.get("path") or "")) or None,
                line=_int(item.get("line")),
                column=_int(item.get("column")),
                rule=str(item.get("code") or "") or None,
                raw=message,
            )
        )
    return diagnostics


def _deduplicate(values: list[NormalizedDiagnostic]) -> list[NormalizedDiagnostic]:
    grouped: dict[tuple[Any, ...], NormalizedDiagnostic] = {}
    for item in values:
        key = (
            (item.path or "").lower(),
            item.line,
            item.column,
            item.category,
            item.rule or "",
            re.sub(r"\s+", " ", item.message.lower()).strip(),
        )
        previous = grouped.get(key)
        if previous is None:
            grouped[key] = item
            continue
        sources = tuple(dict.fromkeys((*previous.related_sources, previous.source, item.source)))
        grouped[key] = replace(
            previous,
            occurrence_count=previous.occurrence_count + item.occurrence_count,
            related_sources=sources,
            fix_available=previous.fix_available or item.fix_available,
        )
    return list(grouped.values())


def _prioritize(values: list[NormalizedDiagnostic]) -> list[NormalizedDiagnostic]:
    return sorted(
        values,
        key=lambda item: (
            SEVERITY_PRIORITY.get(item.severity, 9),
            CATEGORY_PRIORITY.get(item.category, 99),
            item.path or "~",
            item.line or 10**9,
            item.column or 10**9,
        ),
    )


def _bound_diagnostic(item: NormalizedDiagnostic) -> NormalizedDiagnostic:
    return replace(
        item,
        message=_bounded_text(item.message),
        raw=_bounded_text(item.raw),
    )


def _enrich_snippets(
    values: list[NormalizedDiagnostic],
    workspace: Path | None,
) -> list[NormalizedDiagnostic]:
    if workspace is None:
        return values
    root = workspace.resolve()
    enriched = []
    for item in values:
        if not item.path or item.line is None:
            enriched.append(item)
            continue
        candidate = Path(item.path)
        target = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        try:
            target.relative_to(root)
            lines = target.read_text(encoding="utf-8").splitlines()
            start = max(0, item.line - 2)
            end = min(len(lines), item.line + 1)
            snippet = "\n".join(
                f"{index + 1:>5} | {lines[index]}" for index in range(start, end)
            )
        except (OSError, UnicodeError, ValueError):
            snippet = ""
        enriched.append(replace(item, snippet=snippet))
    return enriched


def _suggest_fixes(
    tool: str,
    command: str,
    root: NormalizedDiagnostic | None,
    failed_tests: list[FailedTest],
    output: str,
) -> list[SuggestedFix]:
    if failed_tests:
        target = failed_tests[0].name
        return [
            SuggestedFix(
                "Rerun affected test",
                "Fix the earliest assertion or setup failure, then rerun only the affected test.",
                "High",
                command=f"{command} {target}",
            )
        ]
    if root is None:
        return [
            SuggestedFix(
                title="Inspect command output",
                detail="Review stderr first, then rerun the smallest reproducible command.",
                confidence="Low",
            )
        ]
    category = root.category
    fixes: list[SuggestedFix] = []
    if category == "syntax":
        fixes.append(
            SuggestedFix(
                "Correct syntax",
                f"Open {root.location or 'the first reported file'} and fix the first syntax error before downstream diagnostics.",
                "High",
            )
        )
    elif category == "type":
        fixes.append(
            SuggestedFix(
                "Resolve type mismatch",
                "Inspect the declared and inferred types at the primary location; fix this before dependent errors.",
                "High",
            )
        )
    elif category in {"import", "dependency"}:
        fixes.append(
            SuggestedFix(
                "Restore dependency",
                "Verify the dependency is declared, installed in the active environment, and imported with the correct module name.",
                "High",
            )
        )
    elif category == "permission":
        fixes.append(
            SuggestedFix(
                "Correct permissions",
                "Check workspace ownership, file ACLs, and whether the command is writing outside approved paths.",
                "High",
            )
        )
    elif category == "network":
        fixes.append(
            SuggestedFix(
                "Check network dependency",
                "Confirm network policy, proxy settings, DNS, service availability, and retry only if the failure is transient.",
                "Medium",
            )
        )
    elif category == "style" and tool in {"ruff", "eslint", "biome"}:
        autofix = {
            "ruff": "ruff check --fix",
            "eslint": "eslint --fix",
            "biome": "biome check --write",
        }[tool]
        fixes.append(
            SuggestedFix(
                "Apply tool autofix",
                "Preview the formatter/linter autofix and apply it only to affected files.",
                "High",
                command=autofix,
                safe_autofix=True,
            )
        )
    elif category == "environment" and "memory" in root.message.lower():
        fixes.append(
            SuggestedFix(
                "Reduce memory pressure",
                "Run a smaller target, lower parallelism, or increase the configured memory limit.",
                "Medium",
            )
        )
    if "cache" in output.lower() and tool in {"gradle", "maven", "cargo", "cmake", "next"}:
        fixes.append(
            SuggestedFix(
                "Clean stale build state",
                "Clean only the relevant build cache, then rerun the focused build.",
                "Low",
            )
        )
    return fixes or [
        SuggestedFix(
            "Fix primary diagnostic",
            f"Address the first {category} error at {root.location or 'the root failure'} before rerunning.",
            "Medium",
        )
    ]


def _root_cause(
    root: NormalizedDiagnostic | None,
    execution: dict[str, Any],
    output: str,
) -> str:
    if execution.get("timed_out"):
        return "The process exceeded its configured timeout before completing."
    if execution.get("cancelled"):
        return "The process was cancelled before it could complete."
    if root:
        location = f" at {root.location}" if root.location else ""
        return f"Primary {root.category} failure{location}: {root.message}"
    if execution.get("exit_code") not in {None, 0}:
        return f"The command exited with code {execution['exit_code']} without a recognized diagnostic."
    if output.strip():
        return "The command reported failure output without a recognized structured diagnostic."
    return "The command failed without diagnostic output."


def _summary(
    tool: str,
    root: NormalizedDiagnostic | None,
    failed_tests: list[FailedTest],
    execution: dict[str, Any],
) -> str:
    if execution.get("timed_out"):
        return f"{tool} timed out"
    if execution.get("cancelled"):
        return f"{tool} was cancelled"
    if failed_tests:
        return f"{len(failed_tests)} test(s) failed; first: {failed_tests[0].name}"
    if root:
        location = f" at {root.location}" if root.location else ""
        return f"{tool} reported a {root.category} {root.severity}{location}"
    return f"{tool} exited with code {execution.get('exit_code', '<unknown>')}"


def _categorize(message: str, rule: str | None, command: str) -> str:
    lowered = message.lower()
    normalized_rule = (rule or "").lower()
    checks = [
        (
            "syntax",
            (
                "syntax",
                "parse error",
                "expected expression",
                "expected ';'",
                'expected ";"',
                "unexpected token",
                "unterminated",
            ),
        ),
        (
            "type",
            (
                "type mismatch",
                "mismatched types",
                "incompatible type",
                "cannot assign",
                "not assignable",
                "undefined type",
                "undefined:",
                "cannot find symbol",
                "cannot find '",
                "unresolved reference",
                "does not exist in the current context",
                "typeerror",
            ),
        ),
        ("import", ("cannot import", "unresolved import", "no module named", "cannot find module", "unknown package")),
        ("dependency", ("dependency", "package not found", "could not resolve", "failed to download")),
        ("linker", ("undefined reference", "unresolved external", "linker", "ld returned")),
        ("permission", ("permission denied", "access denied", "eacces", "eperm")),
        ("network", ("connection refused", "dns", "network", "http 429", "http 503", "timed out connecting")),
        ("database", ("sqlstate", "database", "deadlock", "migration")),
        ("filesystem", ("file not found", "no such file", "read-only file system")),
        ("test", ("assertion", "expected", "received", "test failed")),
        ("environment", ("out of memory", "cannot allocate memory", "environment variable", "not set")),
        ("runtime", ("exception", "panic", "segmentation fault", "null pointer", "index out of range")),
        ("style", ("unused import", "line too long", "format", "lint")),
    ]
    if normalized_rule.startswith(("e", "w", "f", "pl", "clippy")) and any(
        token in command.lower() for token in ("ruff", "flake8", "pylint", "eslint", "clippy")
    ):
        return "style"
    for category, tokens in checks:
        if any(token in lowered for token in tokens):
            return category
    if re.search(r"\b[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)\b", message):
        return "runtime"
    return _category_from_command(command)


def _category_from_command(command: str) -> str:
    lowered = command.lower()
    if any(token in lowered for token in ("test", "pytest", "jest", "vitest", "xunit", "nunit")):
        return "test"
    if any(token in lowered for token in ("lint", "ruff", "eslint", "clippy", "biome")):
        return "style"
    if any(token in lowered for token in ("mypy", "pyright", "tsc", "typecheck")):
        return "type"
    if any(
        token in lowered
        for token in (
            "build",
            "cargo",
            "javac",
            "kotlinc",
            "swift",
            "mvn",
            "gradle",
            "cmake",
            "make",
            "bazel",
        )
    ):
        return "build"
    return "unknown"


def _severity(value: str | None) -> str:
    lowered = (value or "error").lower()
    if lowered in {"fatal error", "error"}:
        return "error"
    if lowered == "warning":
        return "warning"
    if lowered in {"info", "information", "note"}:
        return "information"
    if lowered == "hint":
        return "hint"
    return "error"


def _rule_severity(rule: str) -> str:
    normalized = rule.upper()
    if normalized.startswith(("E", "F", "CS", "TS")):
        return "error"
    return "warning"


def _fix_available(tool: str, message: str, rule: str | None) -> bool:
    lowered = message.lower()
    return (
        "fixable" in lowered
        or "can be fixed" in lowered
        or (tool in {"ruff", "eslint", "biome"} and rule is not None)
    )


def _counts(values: list[NormalizedDiagnostic]) -> dict[str, int]:
    counts = {"error": 0, "warning": 0, "information": 0, "hint": 0}
    for item in values:
        counts[item.severity] = counts.get(item.severity, 0) + 1
    return counts


def _signature(
    command: str,
    diagnostics: list[NormalizedDiagnostic],
    failed_tests: list[FailedTest],
) -> str:
    material = [command]
    material.extend(
        f"{item.category}|{item.path}|{item.line}|{item.rule}|{item.message}"
        for item in diagnostics
    )
    material.extend(f"test|{item.name}|{item.message}" for item in failed_tests)
    return sha256("\n".join(material).encode("utf-8")).hexdigest()


def _dedupe_tests(values: list[FailedTest]) -> list[FailedTest]:
    output = []
    seen = set()
    for item in values:
        key = (item.name, item.path, item.line)
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output


def _dedupe_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _likely_source_files(failed_files: list[str]) -> list[str]:
    output: list[str] = []
    for path in failed_files:
        normalized = _normalize_path(path)
        name = normalized.rsplit("/", 1)[-1]
        if name.startswith("test_") and name.endswith(".py"):
            source_name = name.removeprefix("test_")
            output.extend([source_name, f"src/{source_name}"])
        match = re.match(r"(.+?)(?:\.test|\.spec)(\.[cm]?[jt]sx?)$", normalized)
        if match:
            output.append(match.group(1) + match.group(2))
    return _dedupe_strings(output)[:8]


def _first_group(lines: list[str], pattern: str) -> str | None:
    for line in lines:
        match = re.match(pattern, line.strip())
        if match:
            return match.group(1)
    return None


def _normalize_path(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().strip('"').replace("\\", "/")


def _looks_like_source_path(value: str) -> bool:
    return bool(re.search(r"\.(?:py|pyi|js|jsx|ts|tsx|rs|go|java|kt|cs|c|cc|cpp|h|hpp)$", value))


def _strip_ansi(value: str) -> str:
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)


def _bounded_text(value: str, max_chars: int = MAX_DIAGNOSTIC_TEXT) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + f" <truncated {len(value) - max_chars} chars>"


def _int(value: Any) -> int | None:
    try:
        return int(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None
