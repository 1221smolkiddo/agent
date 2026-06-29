from __future__ import annotations

from pathlib import Path

from code_agent.memory import (
    MemoryUpdate,
    build_memory_write_plan,
    memory_file_path,
    read_project_memory,
    write_memory_plan,
)
from code_agent.schema import ReadMemoryAction, UpdateMemoryAction
from code_agent.tools import ToolRegistry


def test_memory_write_plan_creates_sectioned_markdown_and_dedupes(tmp_path: Path) -> None:
    plan = build_memory_write_plan(
        tmp_path,
        [
            MemoryUpdate("project_conventions", "Use Ruff for linting."),
            MemoryUpdate("project_conventions", "Use Ruff for linting."),
            MemoryUpdate("user_preferences", "Prefer dependency injection over globals."),
            MemoryUpdate("architecture_notes", "CLI commands should stay thin and delegate to modules."),
            MemoryUpdate("common_commands", "uv run pytest"),
            MemoryUpdate("known_pitfalls", "Do not overwrite unrelated dirty worktree changes."),
            MemoryUpdate("project_glossary", "work report: structured run summary saved in SQLite."),
            MemoryUpdate("successful_patterns", "Add focused fixture evals for new capabilities."),
            MemoryUpdate("verification_strategy", "Run release-smoke before release commits."),
            MemoryUpdate("dependencies_integrations", "OpenAI-compatible providers use the shared model client."),
            MemoryUpdate("release_notes", "Document SQLite migrations in release notes."),
        ],
    )

    assert plan.changed
    assert plan.sections == [
        "project_conventions",
        "user_preferences",
        "architecture_notes",
        "common_commands",
        "known_pitfalls",
        "project_glossary",
        "successful_patterns",
        "verification_strategy",
        "dependencies_integrations",
        "release_notes",
    ]
    assert plan.after.count("Use Ruff for linting.") == 1
    assert "## Project Conventions" in plan.after
    assert "## Successful Implementation Patterns" in plan.after


def test_memory_refuses_secret_like_entries(tmp_path: Path) -> None:
    try:
        build_memory_write_plan(
            tmp_path,
            [MemoryUpdate("common_commands", "OPENAI_API_KEY=sk-secret-secret-secret")],
        )
    except ValueError as exc:
        assert "looks like a secret" in str(exc)
    else:
        raise AssertionError("expected secret-like memory entry to be refused")


def test_read_project_memory_redacts_existing_secret_text(tmp_path: Path) -> None:
    path = memory_file_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("# Agent47 Project Memory\n\nTOKEN=super-secret-token\n", encoding="utf-8")

    output = read_project_memory(tmp_path)

    assert "super-secret-token" not in output
    assert "TOKEN=[REDACTED]" in output


def test_update_memory_tool_requires_approval_and_writes_after_approval(tmp_path: Path) -> None:
    approvals: list[str] = []

    def approve(action: str, detail: str, metadata=None) -> bool:
        approvals.append(action)
        assert "Project memory update preview" in detail
        assert metadata["paths"] == [".code-agent/memory/project.md"]
        return True

    tools = ToolRegistry(workspace=tmp_path, dry_run=False, approval_callback=approve)
    result = tools.run(
        UpdateMemoryAction(
            type="update_memory",
            entries=[
                {
                    "section": "project_conventions",
                    "content": "Use pathlib for filesystem paths.",
                }
            ],
        )
    )

    assert result.ok
    assert approvals == ["update_memory"]
    assert "Use pathlib for filesystem paths." in memory_file_path(tmp_path).read_text(encoding="utf-8")


def test_update_memory_tool_denial_does_not_write(tmp_path: Path) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=False, approval_callback=lambda *_args: False)

    result = tools.run(
        UpdateMemoryAction(
            type="update_memory",
            entries=[
                {
                    "section": "user_preferences",
                    "content": "Do not use default exports.",
                }
            ],
        )
    )

    assert not result.ok
    assert "Permission denied" in result.output
    assert not memory_file_path(tmp_path).exists()


def test_update_memory_tool_respects_dry_run(tmp_path: Path) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda *_args: True)

    result = tools.run(
        UpdateMemoryAction(
            type="update_memory",
            entries=[
                {
                    "section": "project_conventions",
                    "content": "Use Black for formatting.",
                }
            ],
        )
    )

    assert not result.ok
    assert "Dry-run mode skipped update_memory" in result.output
    assert not memory_file_path(tmp_path).exists()


def test_read_memory_tool_returns_local_memory(tmp_path: Path) -> None:
    plan = build_memory_write_plan(
        tmp_path,
        [MemoryUpdate("known_pitfalls", "Generated files can be large; preview diffs carefully.")],
    )
    write_memory_plan(plan)
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda *_args: True)

    result = tools.run(ReadMemoryAction(type="read_memory", max_chars=2000))

    assert result.ok
    assert "Generated files can be large" in result.output
    assert result.metadata["path"] == ".code-agent/memory/project.md"
