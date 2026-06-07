from pathlib import Path

from code_agent.schema import DetectVerificationAction, SuggestVerificationAction
from code_agent.tools import ToolRegistry
from code_agent.verification import detect_verification_commands, suggest_verification_commands


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
