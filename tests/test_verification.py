from pathlib import Path

from code_agent.schema import DetectVerificationAction, SuggestVerificationAction
from code_agent.tools import ToolRegistry
from code_agent.verification import (
    detect_verification_commands,
    select_verification_commands,
    suggest_verification_commands,
)
from code_agent.verification_diagnostics import (
    diagnose_node_test_failure,
    diagnose_pytest_failure,
    diagnose_verification_failure,
)


def test_detect_verification_commands_for_python_project(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
dependencies = ["pytest", "ruff"]

[build-system]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
""".strip(),
        encoding="utf-8",
    )

    output = detect_verification_commands(tmp_path)

    assert "- test: uv run pytest (pyproject.toml)" in output
    assert "- lint: uv run ruff check src tests (pyproject.toml)" in output
    assert "- build: uv build (pyproject.toml)" in output


def test_detect_verification_commands_for_node_project(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        """
{
  "scripts": {
    "test": "vitest",
    "lint": "eslint .",
    "typecheck": "tsc --noEmit",
    "build": "vite build"
  }
}
""".strip(),
        encoding="utf-8",
    )

    output = detect_verification_commands(tmp_path)

    assert "- test: npm run test (package.json)" in output
    assert "- lint: npm run lint (package.json)" in output
    assert "- typecheck: npm run typecheck (package.json)" in output
    assert "- build: npm run build (package.json)" in output


def test_detect_verification_commands_reports_when_none_found(tmp_path: Path) -> None:
    output = detect_verification_commands(tmp_path)

    assert output == "No verification commands detected from known project files."


def test_suggest_verification_commands_for_python_change(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
dependencies = ["pytest", "ruff"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
""".strip(),
        encoding="utf-8",
    )

    output = suggest_verification_commands(tmp_path, ["src/code_agent/agent.py"])

    assert "Reason: Python source or test files changed." in output
    assert output.index("- lint: uv run ruff check src tests") < output.index("- test: uv run pytest")


def test_select_verification_commands_returns_structured_commands(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
dependencies = ["pytest"]

[tool.pytest.ini_options]
testpaths = ["tests"]
""".strip(),
        encoding="utf-8",
    )

    commands, reason = select_verification_commands(tmp_path, ["tests/test_app.py"])

    assert reason == "Python source or test files changed."
    assert [command.command for command in commands] == ["uv run pytest"]


def test_suggest_verification_commands_skips_docs_only_change(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
dependencies = ["pytest"]
""".strip(),
        encoding="utf-8",
    )

    output = suggest_verification_commands(tmp_path, ["docs/README.md"])

    assert output == "No verification commands recommended. Reason: Only documentation files changed."


def test_detect_verification_tool_requires_permission(tmp_path: Path) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: False)

    result = tools.run(DetectVerificationAction(type="detect_verification"))

    assert not result.ok
    assert result.output == "Permission denied for detect_verification."


def test_suggest_verification_tool_requires_permission(tmp_path: Path) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: False)

    result = tools.run(
        SuggestVerificationAction(type="suggest_verification", changed_paths=["src/app.py"])
    )

    assert not result.ok
    assert result.output == "Permission denied for suggest_verification."


def test_detect_verification_tool_returns_detected_commands(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/app\n", encoding="utf-8")
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: True)

    result = tools.run(DetectVerificationAction(type="detect_verification"))

    assert result.ok
    assert "- test: go test ./... (go.mod)" in result.output
    assert "- build: go build ./... (go.mod)" in result.output


def test_suggest_verification_tool_returns_focused_commands(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        """
{
  "scripts": {
    "test": "vitest",
    "lint": "eslint .",
    "build": "vite build"
  }
}
""".strip(),
        encoding="utf-8",
    )
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: True)

    result = tools.run(
        SuggestVerificationAction(type="suggest_verification", changed_paths=["src/App.tsx"])
    )

    assert result.ok
    assert "- lint: npm run lint (package.json)" in result.output
    assert "- test: npm run test (package.json)" in result.output
    assert "- build: npm run build (package.json)" in result.output


def test_diagnose_pytest_failure_extracts_compact_failure_context() -> None:
    output = """
================================== FAILURES ===================================
________________________________ test_multiply ________________________________

    def test_multiply():
>       assert multiply(3, 4) == 12
E       assert -1 == 12
E        +  where -1 = multiply(3, 4)

tests/test_mathlib.py:4: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_mathlib.py::test_multiply - assert -1 == 12
""".strip()

    diagnostics = diagnose_pytest_failure(output)

    assert diagnostics.runner == "pytest"
    assert diagnostics.failed_tests == ["tests/test_mathlib.py::test_multiply"]
    assert diagnostics.failed_files == ["tests/test_mathlib.py"]
    assert diagnostics.likely_source_files == ["mathlib.py", "src/mathlib.py"]
    assert diagnostics.assertions == ["assert -1 == 12"]
    assert diagnostics.suggested_focus == ["tests/test_mathlib.py", "mathlib.py", "src/mathlib.py"]
    assert diagnostics.focused_rerun_commands == [
        "python -m pytest tests/test_mathlib.py::test_multiply"
    ]
    assert diagnostics.summary == "tests/test_mathlib.py::test_multiply failed: assert -1 == 12"


def test_diagnose_node_test_failure_extracts_compact_failure_context() -> None:
    output = """
not ok 1 - totals values
  ---
  Expected values to be strictly equal:
  3 !== 10
  at total.test.js:6:10
""".strip()

    diagnostics = diagnose_node_test_failure("npm test", output)

    assert diagnostics.runner == "node"
    assert diagnostics.failed_tests == ["totals values"]
    assert diagnostics.failed_files == ["total.test.js"]
    assert diagnostics.likely_source_files == ["total.js"]
    assert "Expected values" in diagnostics.assertions[0]
    assert diagnostics.suggested_focus == ["total.test.js", "total.js"]
    assert diagnostics.focused_rerun_commands == ["npm test"]
    assert diagnostics.summary.startswith("totals values failed:")


def test_diagnose_verification_failure_only_handles_known_runners() -> None:
    assert diagnose_verification_failure("python -m pytest", "FAILED tests/test_app.py::test_app") is not None
    assert diagnose_verification_failure("npm test", "not ok 1 - totals values") is not None
    assert diagnose_verification_failure("npm run build", "build failed") is None
