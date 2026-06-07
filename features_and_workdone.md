# Agent47 Features and Work Done

This document summarizes the current state of Agent47 for collaborators.

## Current Capabilities

- Python CLI-first coding agent.
- Interactive terminal launcher with `agent47`.
- One-shot command mode with `code-agent run`.
- OpenRouter-compatible model access.
- Default model set to `qwen/qwen3-coder`.
- Non-workspace question routing so general questions can be answered without inspecting project files.
- Local city-time answers through a `local_time` action for common city/time-zone aliases.
- Local `.env` configuration with secrets excluded from git.
- User approval prompts for file listing, reading, project search, code summary, verification detection/suggestion, file writes, file edits, patch application, shell commands, and web search.
- Dry-run mode for inspect-only sessions.
- Write mode through `/write` or non-dry-run CLI usage.
- Local workspace sandbox mode through `--sandbox` or `/sandbox`.
- Workspace path guardrails to block access outside the selected workspace.
- File operations with `pathlib`.
- Diff previews for write, edit, and structured patch operations.
- Structured `apply_patch` action backed by `git apply`.
- Verification command detection for Python, Node, Rust, and Go projects.
- Focused verification command suggestions based on changed file paths.
- Verification-like shell command outcomes are appended to final summaries.
- Shell execution with operation labels for install, build, test, check, and generic shell commands.
- Project search with `ripgrep` plus a built-in Python fallback when `ripgrep` is unavailable.
- Web search action with user permission.
- Optional tree-sitter code summaries.
- SQLite run history.
- Automatic recovery loop when a tool fails.
- Workspace tools are blocked for prompts that do not appear to be about the local project, files, code changes, tests, or commands.
- Mutation tool results include changed paths and a verification hint for the next model step.
- Bordered terminal panels distinguish user prompts, Agent47 responses, help, status, and history.
- Interactive mode keeps a short in-memory transcript for follow-up summaries and recaps.
- Guard against false completion after blocked writes, edits, or patch applications.
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
- The agent asks before writing, editing, or applying patches to files.
- The agent asks before detecting or suggesting verification commands.
- The agent asks before running shell commands.
- The agent asks before using web search.
- Sandbox mode copies the workspace and excludes `.env`, `.git`, `.venv`, caches, and local agent state.
- Failed writes in dry-run mode do not count as completed work.
- Project search skips local state and secret files such as `.env`, `.git`, `.code-agent`, caches, and virtual environments.
- The agent rejects final answers that claim a blocked write/edit/patch succeeded.

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
- Structured patch application and path validation.
- Verification command detection.
- Focused verification command suggestion.
- Verification outcome summaries.
- Clean terminal panel formatting.
- Project search fallback when `ripgrep` is unavailable.
- Non-workspace routing guard.
- Local city-time action.
- Transcript-aware interactive summaries.
- Operation status label formatting.
- Casual greeting handling.

## What Still Needs Work

- Multi-file change set metadata and richer patch conflict recovery.
- Stronger sandboxing for shell process isolation.
- Network policy controls.
- Streaming model output.
- Better repo context selection.
- Planner state with visible task steps.
- Automatic verification execution without relying on the model to choose every command.
- Session resume.
- Multi-provider and multi-model support.
- VS Code extension frontend.
