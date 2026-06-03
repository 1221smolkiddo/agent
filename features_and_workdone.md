# Agent47 Features and Work Done

This document summarizes the current state of Agent47 for collaborators.

## Current Capabilities

- Python CLI-first coding agent.
- Interactive terminal launcher with `agent47`.
- One-shot command mode with `code-agent run`.
- OpenRouter-compatible model access.
- Default model set to `qwen/qwen3-coder`.
- Local `.env` configuration with secrets excluded from git.
- User approval prompts for file listing, reading, project search, code summary, file writes, file edits, shell commands, and web search.
- Dry-run mode for inspect-only sessions.
- Write mode through `/write` or non-dry-run CLI usage.
- Local workspace sandbox mode through `--sandbox` or `/sandbox`.
- Workspace path guardrails to block access outside the selected workspace.
- File operations with `pathlib`.
- Diff previews for write and edit operations.
- Shell execution with operation labels for install, build, test, check, and generic shell commands.
- Project search with `ripgrep`.~
- Web search action with user permission.
- Optional tree-sitter code summaries.
- SQLite run history.
- Automatic recovery loop when a tool fails.
- Guard against false completion after blocked writes or edits.
- Operation status labels such as `THINKING`, `READING`, `EDITING`, `SEARCHING`, `TESTING`, `BUILDING`, `RECOVERING`, and `DONE`.
- Stop controls with `Ctrl+C`, `/stop`, and `/exit`.

## Main Commands

```powershell
agent47
code-agent run --dry-run "Inspect this project"
code-agent run --sandbox "Try a risky change in a copied workspace"
code-agent history
```

Inside `agent47`:

```text
/help
/status
/dry-run
/write
/sandbox
/sandbox off
/max-steps 20
/max-failures 5
/history
/stop
```

## Safety Work Completed

- `.env` is git-ignored.
- `.code-agent/` local data and sandboxes are git-ignored.
- The agent asks before reading or listing files.
- The agent asks before writing or editing files.
- The agent asks before running shell commands.
- The agent asks before using web search.
- Sandbox mode copies the workspace and excludes `.env`, `.git`, `.venv`, caches, and local agent state.
- Failed writes in dry-run mode do not count as completed work.
- The agent rejects final answers that claim a blocked write/edit succeeded.

## Collaboration Work Completed

- Git repository initialized.
- GitHub remote configured.
- CI workflow added for tests and linting.
- Issue templates added.
- Pull request template added.
- Contribution guide added.
- Architecture document added.
- Roadmap document added.
- Progress tracker added at `docs/PROGRESS.md`.

## Current Test Coverage

- Path safety.
- Sandbox copying and exclusion behavior.
- Tool permission behavior.
- Agent failure recovery.
- False-completion prevention after blocked writes.
- Operation status label formatting.
- Casual greeting handling.

## What Still Needs Work

- Patch-based editing instead of direct full-file writes.
- Stronger sandboxing for shell process isolation.
- Network policy controls.
- Streaming model output.
- Better repo context selection.
- Planner state with visible task steps.
- Automatic test command detection.
- Session resume.
- Multi-provider and multi-model support.
- VS Code extension frontend.
