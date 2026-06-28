# Agent47 Features and Work Done

This document summarizes the current state of Agent47 for collaborators.

## Current Capabilities

- Python CLI-first coding agent.
- Interactive terminal launcher with `agent47`.
- One-shot command mode with `code-agent run`.
- OpenRouter-compatible model access.
- Default model set to `qwen/qwen3-coder`.
- Model profiles for `default`, `planner`, `coder`, `reviewer`, and `fast` runs.
- Optional per-profile model overrides with explicit `--model` precedence.
- Configurable model fallback list with `AGENT_FALLBACK_MODELS`.
- Per-run model attempt and token usage tracking in SQLite.
- Optional configurable model cost estimation from token usage.
- Provider configuration boundary for OpenAI-compatible clients.
- Non-workspace question routing so general questions can be answered without inspecting project files.
- General web search for current external information, with provider fallback.
- Local `.env` configuration with secrets excluded from git.
- User approval prompts for file listing, reading, project search, code summary, verification detection/suggestion, file writes, file edits, patch application, shell commands, and web search.
- Dry-run mode for inspect-only sessions.
- Interactive mode starts write-enabled, and write mode is also available through `/write` or non-dry-run CLI usage.
- Compact model streaming progress is available through `AGENT_STREAM`, `--stream/--no-stream`, and `/stream [off]`.
- Model profile selection is available through `AGENT_PROFILE`, `--profile`, and `/profile`.
- Local workspace sandbox mode through `--sandbox` or `/sandbox`.
- Sandbox diff/apply promotion is available through `code-agent sandbox diff`, `code-agent sandbox apply`, `/sandbox diff`, and `/sandbox apply`.
- Sandbox promotion uses patch approval and disk verification before changes reach the base workspace.
- Workspace path guardrails to block access outside the selected workspace.
- Git-diff awareness through an approved `inspect_git_diff` action that reports dirty paths and can include bounded diff hunks when needed.
- Lightweight repo mapping through an approved `repo_map` action that reports important project files, file counts, and top-level layout while skipping ignored local state.
- Task-aware context ranking through an approved `rank_context` action that scores likely relevant files before broad reads.
- Compact symbol indexing through an approved `symbol_index` action that locates Python classes/functions and JavaScript/TypeScript declarations before implementation reads.
- Lightweight dependency graph indexing through an approved `dependency_graph` action that maps Python and JavaScript/TypeScript imports into internal edges and external packages before impact analysis.
- Persistent SQLite-backed repo index cache with file size, modification time, SHA-256 hash, module name, symbols, and imports.
- Incremental repo index refresh that reuses unchanged cached files and removes deleted paths from the cache.
- Optional background repo index refresh worker for long-lived idle frontends.
- Repo-map, ranking, symbol-index, and dependency-graph actions are saved in run history and summarized in structured work reports as context analysis.
- Offline deterministic eval harness through `code-agent evals`.
- Built-in safety regressions for greeting routing, blocked-write honesty, denied-read non-leakage, and sandbox write isolation.
- Built-in fixture coding evals for create-file, edit-file, fix-test, and failed-read recovery tasks.
- Versioned newline-delimited JSON protocol through `code-agent run-json` for future VS Code and non-terminal frontends.
- JSON protocol events cover run start/finish, status, action starts, approval requests, approval resolutions, recovery, failures, work reports, and final results.
- JSON protocol approval handling fails closed by default, supports `--approval-stdin` request/response handling for parent editor processes, and keeps explicit `--approve-all` only for trusted automation.
- Install diagnostics through `code-agent doctor`.
- Cross-platform install guide for Windows, macOS, Linux, uv, pip editable installs, and pipx.
- File operations with `pathlib`.
- Bounded diff previews for write, edit, delete, and structured patch operations.
- Structured `apply_patch` action backed by `git apply`.
- Stored inverse patches and before/after hashes for verified mutations.
- Patch revert through `code-agent revert <run-id>` and `/revert <run-id>`, with preview, approval, conflict detection, and post-revert verification.
- First-class `delete_file` action for approved file removal.
- Verification command detection for Python, Node, Rust, and Go projects.
- Focused verification command suggestions based on changed file paths.
- Automatic focused verification after successful file mutations when commands are detected.
- Verification-like shell command outcomes are appended to final summaries.
- Shell execution with operation labels for install, build, test, check, and generic shell commands.
- Shell command risk classification for read-only, verification, git, install/network, destructive, and unknown commands.
- Destructive shell commands are blocked before approval.
- Sensitive credential files such as `.env`, `.npmrc`, `.pypirc`, and `.netrc` are refused by file mutation/read tools.
- Tool outputs are redacted for common secret patterns before model/storage use.
- Large mutation diffs are truncated before model/storage use so long files do not overwhelm recovery turns.
- Web search approval prompts include provider domains and query text.
- Localhost, private-network, link-local, reserved, and multicast web targets are blocked by network policy.
- Unsafe web result URLs are filtered before results are returned to the model.
- Project search with `ripgrep` plus a built-in Python fallback when `ripgrep` is unavailable.
- Web search action with user permission.
- Optional tree-sitter code summaries.
- SQLite run history.
- Run detail views through `code-agent history show <run-id>` and `/history-show <run-id>`.
- Resumable runs through `code-agent resume <run-id>` and `/resume <run-id>`.
- Revert runs through `code-agent revert <run-id>` and `/revert <run-id>`.
- Durable `update_plan` checkpoints with `pending`, `in_progress`, `completed`, and `blocked` step statuses plus target files, owned files, checks, blockers, and risk notes.
- Plan updates are saved in run history, rendered in live CLI panels, summarized in work reports, and included in resume context.
- CLI and `agent47` render structured work reports before the final response for non-trivial runs.
- Work reports include current task, current step, files being modified, planned targets, file ownership, planned checks, blockers, risk notes, progress, context analysis, model usage, commands executed, validation status, modified files, change summary, changed-line diff review, and final outcome.
- Work reports are stored in SQLite, surfaced in history details, and included in resume context.
- Automatic recovery loop when a tool fails.
- More tolerant action parsing for valid JSON actions wrapped in prose or code fences.
- Workspace tools are blocked for prompts that do not appear to be about the local project, files, code changes, tests, or commands.
- Mutation tool results include verified changed paths, post-mutation disk facts, and automatic verification results or a verification hint for the next model step.
- Mutation tool results include inverse patches and hashes so verified changes can be reverted without blind overwrites.
- Bordered terminal panels distinguish user prompts, Agent47 responses, help, status, and history.
- Interactive mode keeps a short in-memory transcript for follow-up summaries and recaps.
- Interactive mode keeps structured session state for the current task, pending user info, target files, last changed files, blockers, and recent tool results.
- Saved run steps can be compacted into continuation context for follow-up runs.
- Guard against false completion after blocked writes, edits, or patch applications.
- Guard against false completion after unverified file deletion claims.
- Operation status labels such as `THINKING`, `STREAMING`, `READING`, `EDITING`, `SEARCHING`, `TESTING`, `BUILDING`, `RECOVERING`, and `DONE`.
- Planner status labels such as `PLANNING updating task plan`.
- Optional reviewer pass that post-reviews successful workspace mutations before finalizing, with structured JSON decisions, fail-open semantics, and bounded model usage tracking.
- Reviewer pass is enabled by default in factory-created agents via `AGENT_REVIEWER_PASS=true`; scripted eval and test agents remain unaffected.
- Reviewer decisions are auditable in run history, surfaced in work reports, and included in structured payloads.
- Configurable `AGENT_REVIEWER_MODEL` lets the reviewer use a dedicated model separate from the primary agent.
- Centered interactive startup banner with optional terminal colors for panels, prompts, and status labels.
- Stop controls with `Ctrl+C`, `/stop`, and `/exit`.

## Main Commands

```powershell
agent47
code-agent doctor
code-agent run --dry-run "Inspect this project"
code-agent run --sandbox "Try a risky change in a copied workspace"
code-agent run-json --dry-run "Inspect this project and emit JSON events"
code-agent history
code-agent evals
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
/revert 12
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
- Sandbox diff/apply tests cover changed-file previews, approved promotion, denied promotion, and CLI diff output.
- Failed writes in dry-run mode do not count as completed work.
- Project search skips local state and secret files such as `.env`, `.git`, `.code-agent`, caches, and virtual environments.
- Direct reads, writes, edits, deletes, and patches against sensitive credential files are refused by default.
- Shell approval prompts include command category, risk, and reason.
- Destructive shell commands are blocked by policy instead of being sent to a normal approval prompt.
- Web search is limited to public HTTP/HTTPS targets and filters local/private-network URLs.
- Tool outputs redact common key/value secrets, bearer tokens, and OpenAI-style secret keys.
- The agent tracks mutation attempts, verifies them against disk state, and rejects final answers that claim unverified, blocked, skipped, or failed file changes succeeded.
- Reviewer pass uses fail-open semantics: if the reviewer model fails or returns malformed output, the primary agent's result is not blocked.

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
- Sandbox diff/apply promotion behavior.
- Tool permission behavior.
- Permission preview truncation for large generated file diffs.
- Shell command policy and secret redaction.
- Network policy controls for web search.
- Agent failure recovery.
- False-completion prevention after blocked writes.
- Structured patch application and path validation.
- Patch revert from stored inverse changes.
- Verification command detection.
- Focused verification command suggestion.
- Automatic focused verification execution.
- Verification outcome summaries.
- Clean terminal panel formatting.
- Project search fallback when `ripgrep` is unavailable.
- Persistent incremental repo index, context ranking, symbol indexing, and dependency graphing.
- Sandbox diff/apply promotion.
- Local deterministic eval harness.
- Fixture-based coding evals.
- Versioned NDJSON protocol for frontend integrations.
- Install doctor and cross-platform install guide.
- Model fallback and usage/cost tracking.
- Non-workspace routing guard.
- General web-search provider fallback.
- Transcript-aware interactive summaries.
- Interactive session-state follow-up handling.
- Operation status label formatting.
- Durable planner action validation, metadata storage, resume summaries, work report sections, and status formatting.
- Casual greeting handling.
- Reviewer pass parsing, message construction, run execution, fail-open error handling, and CodingAgent integration.

## What Still Needs Work

- Multi-file change set metadata and richer patch conflict recovery.
- Stronger sandboxing for shell process isolation.
- Network policy controls.
- Rich token-level streaming views for future non-JSON frontends.
- Broader fixture evals with multi-file patches, larger repos, and prompt-injection scenarios.
- Additional concrete providers beyond OpenAI-compatible APIs.
- Package metadata, license, changelog, and release checklist.
- VS Code extension frontend.
