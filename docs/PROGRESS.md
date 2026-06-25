# Progress Tracker

This document tracks how far the coding agent has come and what collaborators can use today.

## Current Stage

**Stage:** Basic single-model CLI agent with Agent47 engineering protocol

**Approximate progress toward an industry-standard local AI coding agent:** 64%

The project currently has a working Python CLI foundation with a reusable agent core, model client, local tools, durable plan checkpoints with target-file, ownership, check, blocker, and risk metadata, persisted structured work reports, non-workspace intent routing, general web search with provider fallback, structured patch application with multi-file change-set metadata and inverse-patch revert support, approved git-diff awareness, lightweight repo mapping, task-aware relevance ranking, compact symbol indexing, dependency graph indexing, deterministic local evals for baseline safety regressions and fixture-based coding tasks, a versioned newline-delimited JSON protocol with correlated stdin approval responses for future frontends, model fallback with usage/cost tracking, install diagnostics and cross-platform install guidance, verification command detection/suggestion/automatic execution/outcome summaries, disk-verified mutation tracking for truthful final answers, prompt-injection defenses for untrusted tool output, clearer interactive terminal panels with short transcript context and structured session state, run history detail views, resumable runs, tests, GitHub collaboration setup, and release metadata/checklists for public alpha preparation.

This file should be updated whenever a meaningful Agent47 capability is added. Keep the enabled commands/actions current, move completed items out of "What Is Left" when they land, and update the percentage only when the agent gains real product capability rather than documentation alone.

## Enabled CLI Commands

There are currently **12 user-facing CLI command entries**:

| Command | Purpose |
| --- | --- |
| `code-agent run "task"` | Run the agent on a coding task. |
| `code-agent run-json "task"` | Run the agent and emit versioned NDJSON protocol events for frontends. |
| `code-agent doctor` | Check local install, platform, tools, storage, and configuration. |
| `code-agent history` | Show recent saved agent runs from SQLite. |
| `code-agent history show <run-id>` | Show saved steps for one agent run. |
| `code-agent history export <run-id>` | Export a redacted debug bundle for one agent run. |
| `code-agent resume <run-id>` | Resume a saved run with compact prior context. |
| `code-agent revert <run-id>` | Revert verified file changes from a prior run. |
| `code-agent sandbox diff <sandbox-path>` | Show changed files and unified diffs between a sandbox and base workspace. |
| `code-agent sandbox apply <sandbox-path>` | Promote approved sandbox changes back to the base workspace. |
| `code-agent evals` | Run offline deterministic safety and regression evals. |
| `agent47` | Open an interactive terminal session for free-form prompts. |

Common examples:

```powershell
uv run code-agent run "Inspect this project and suggest next steps"
uv run code-agent doctor
uv run code-agent run-json --dry-run "Inspect this project and emit JSON events"
uv run code-agent run --dry-run "Find risky areas in the codebase"
uv run code-agent run --sandbox "Try a risky change in an isolated copy"
uv run code-agent run --max-failures 5 "Recover from failed tool attempts"
uv run code-agent history
uv run code-agent history show 12
uv run code-agent history export 12
uv run code-agent resume 12 "Continue after the failed check"
uv run code-agent revert 12
uv run code-agent sandbox diff .code-agent/sandboxes/sandbox-20260619-120000
uv run code-agent sandbox apply .code-agent/sandboxes/sandbox-20260619-120000
uv run code-agent evals
uv run code-agent evals --json
agent47
```

Inside `agent47`, there are currently **19 slash commands**:

| Slash command | Purpose |
| --- | --- |
| `/help` | Show interactive help. |
| `/status` | Show workspace, model, mode, and max steps. |
| `/dry-run` | Inspect only; skip writes and shell commands. |
| `/write` | Allow writes and shell commands. |
| `/stream [off]` | Turn compact model streaming progress on or off. |
| `/cwd <path>` | Change workspace. |
| `/sandbox [off]` | Create and use a sandbox copy, or return to the base workspace. |
| `/sandbox diff` | Show sandbox changes before promotion. |
| `/sandbox apply` | Promote approved sandbox changes back to the base workspace. |
| `/model <name>` | Change model for this session. |
| `/profile <name>` | Change model profile: `default`, `planner`, `coder`, `reviewer`, or `fast`. |
| `/max-steps <n>` | Change max agent loop steps. |
| `/max-failures <n>` | Change consecutive failure recovery budget. |
| `/history` | Show recent saved agent runs. |
| `/history-show <run-id>` | Show saved steps for one agent run. |
| `/resume <run-id> [instruction]` | Resume a saved run with optional extra instruction. |
| `/revert <run-id>` | Revert verified file changes from a prior run. |
| `/stop` | Quit interactive mode. |
| `/exit` | Quit interactive mode. |

## Enabled Agent Actions

There are currently **19 model-requestable actions**:

| Action | Purpose |
| --- | --- |
| `final` | Finish and summarize the result for the user. |
| `update_plan` | Checkpoint durable task steps with status plus target files, owned files, intended checks, blockers, and risk notes. |
| `list_files` | List files inside the workspace. |
| `read_file` | Read a file inside the workspace. |
| `write_file` | Write a full file inside the workspace. |
| `edit_file` | Replace exact text in a file. |
| `apply_patch` | Apply a unified diff patch inside the workspace after approval, with multi-file change-set metadata. |
| `delete_file` | Delete a file inside the workspace after approval. |
| `run_shell` | Run a shell command in the workspace. |
| `search` | Search the project with ripgrep, falling back to a built-in Python search when ripgrep is unavailable. |
| `web_search` | Search the web after user approval, using provider fallback when available. |
| `summarize_code` | Summarize a source file with tree-sitter when parsing deps are installed. |
| `detect_verification` | Detect likely test, lint, typecheck, and build commands from project files. |
| `suggest_verification` | Suggest focused verification commands from changed file paths. |
| `inspect_git_diff` | Inspect git status, changed paths, and optional bounded diff hunks before editing. |
| `repo_map` | Build a compact repository map with important files and layout signals. |
| `rank_context` | Rank likely relevant files for the current task before broader reads. |
| `symbol_index` | Build a compact symbol index for source and test declarations before implementation reads. |
| `dependency_graph` | Build a compact import dependency graph for source and test files before impact analysis. |

## Installed / Supported Stack

| Area | Status |
| --- | --- |
| Core language: Python | Enabled |
| CLI framework: Typer | Enabled |
| Model API: OpenRouter via OpenAI-compatible chat completions | Enabled |
| Model profiles: default/planner/coder/reviewer/fast | Enabled |
| Provider config boundary for OpenAI-compatible clients | Enabled |
| Model fallback: `AGENT_FALLBACK_MODELS` | Enabled |
| Model token usage tracking | Enabled |
| Optional model cost estimation | Enabled |
| Model output cap: configurable `AGENT_MAX_TOKENS` | Enabled |
| Config: python-dotenv + pydantic-settings | Enabled |
| File ops: pathlib | Enabled |
| Diffs: difflib | Enabled |
| Structured patch application: git apply | Enabled with multi-file metadata |
| Durable plan checkpoints with file/check/risk metadata | Enabled |
| Structured work reports in CLI and `agent47` | Enabled |
| Persisted work reports in SQLite history and resume context | Enabled |
| Shell execution: subprocess | Enabled |
| Shell command risk policy | Enabled |
| Web/network policy | Enabled |
| Secret output redaction | Enabled |
| Bounded approval previews and mutation diff outputs | Enabled |
| Project search: ripgrep plus Python fallback | Enabled |
| Lightweight repo map | Enabled |
| Task-aware context ranking | Enabled |
| Compact symbol index | Enabled |
| Lightweight dependency graph | Enabled |
| Web search | Enabled with user approval |
| Local workspace sandbox | Enabled with diff/apply promotion |
| Tool failure recovery loop | Enabled |
| Agent47 engineering protocol | Enabled |
| Non-workspace question routing | Enabled |
| General web search provider fallback | Enabled |
| Git diff awareness | Enabled |
| Verification command detection | Enabled |
| Verification command suggestion | Enabled |
| Automatic focused verification execution | Enabled |
| Verification outcome summaries | Enabled |
| Disk-verified mutation tracking for final-answer honesty | Enabled |
| Patch revert from stored inverse changes | Enabled |
| Operation status labels | Enabled |
| Compact model streaming progress | Enabled |
| Bordered interactive terminal panels | Enabled |
| Centered interactive banner and terminal color theme | Enabled |
| Short interactive transcript context | Enabled |
| Structured interactive session state | Enabled |
| Run detail views | Enabled |
| Resumable runs | Enabled |
| Stop shortcut: `Ctrl+C` and `/stop` | Enabled |
| Code parsing: tree-sitter | Enabled as optional parsing extra |
| Storage: SQLite | Enabled |
| Storage migrations: schema versioning and legacy backup | Enabled |
| Testing: pytest | Enabled |
| Local deterministic evals | Enabled |
| Eval metrics: JSON output and category pass rates | Enabled |
| Frontend JSON protocol: NDJSON subprocess transport with correlated stdin approvals | Enabled |
| Prompt-injection defenses for untrusted tool output | Enabled |
| Public alpha release metadata and checklist | Enabled |
| Install diagnostics: `code-agent doctor` | Enabled |
| Debug bundles: redacted `history export` run artifacts | Enabled |
| Default model: Qwen via OpenRouter | Enabled |
| Packaging: uv | Enabled |
| Collaboration: GitHub docs/templates/CI | Enabled |

## What Is Left

These are the remaining capability areas needed for Agent47 to feel like a fully fledged, industry-standard AI coding agent.

For the complete team issue breakdown, see [TEAM_BUILD_PLAN.md](TEAM_BUILD_PLAN.md).
For the next implementation to-do list, see [INDUSTRY_AGENT_PLAN.md](INDUSTRY_AGENT_PLAN.md).

| Priority | Capability | Why it matters | Status |
| --- | --- | --- | --- |
| 1 | Structured patch editing | Gives safe, reviewable multi-file code changes instead of brittle full-file rewrites | Baseline done for preview, apply, verify, and metadata |
| 2 | Verification loop | Lets Agent47 detect, suggest, run, and summarize the right tests, lint, typecheck, and builds after edits | Baseline done |
| 3 | Repo intelligence | Helps the agent choose relevant context using symbols, dependencies, git diff, and architecture summaries | Baseline done for repo map, ranking, symbol indexing, dependency graphing, and git awareness |
| 4 | Safer shell policy | Separates read-only, test/build, install/network, and destructive commands with stronger approvals | Baseline done |
| 5 | Durable sessions | Enables pause/resume, plan state, checkpoints, and long task recovery | Partial, with resumable runs, visible durable plan checkpoints, planner metadata, and persisted structured work reports |
| 6 | Streaming UX | Makes CLI and interactive mode feel alive during model reasoning and tool execution | Baseline done |
| 7 | Multi-model/provider layer | Supports planner/coder/reviewer profiles, fallbacks, and cost-aware routing | Baseline done for profiles, fallback, and usage/cost tracking |
| 8 | Observability | Captures traces, timings, token use, failures, and debug bundles for reliability work | Partial, with model usage and work reports |
| 9 | Collaboration workflow | Adds review mode, branch/commit/PR helpers, issue context, changelogs, and release notes | Partial |
| 10 | Editor integration | Brings Agent47 into VS Code with file context, diffs, approvals, and terminal output | Protocol baseline done |
| 11 | Evaluation harness | Measures solve rate, edit correctness, verification rate, and regressions on fixture repos | Baseline safety and fixture coding evals done |
| 12 | Packaging hardening | Adds release profiles, install docs, upgrade notes, and platform-specific validation | Baseline done for metadata, license, changelog, install guide, doctor, and release checklist |

## Next Recommended Build Order

1. Add storage migrations for durable public releases.
2. Add richer terminal/editor diff approval UI.
3. Add stronger process isolation for sandboxed commands.
4. Add formal security policy and threat model.
5. Add per-repo architecture and ownership memory under `.code-agent/`.

## Current Safety Notes

- Paths are guarded so tools cannot access files outside the workspace.
- Workspace tools are blocked for prompts that do not appear to be about the local project, local files, code changes, tests, or commands.
- Sandbox mode copies the workspace into `.code-agent/sandboxes/`, excludes secrets/local state, shows changed files as unified diffs, and promotes selected changes through patch approval and disk verification.
- `.env`, `.venv`, caches, and local agent databases are ignored by git.
- Read/list/project-search/code-summary actions now ask for user approval.
- Project search skips local state and secret files such as `.env`, `.git`, `.code-agent`, caches, and virtual environments.
- Direct file reads and mutations against sensitive credential files such as `.env`, `.npmrc`, `.pypirc`, and `.netrc` are refused by default.
- Shell commands are classified by risk before approval, and destructive commands such as `git reset --hard`, recursive force deletes, and aggressive `git clean` forms are blocked by policy.
- Web search approval prompts include provider domains and query text, and localhost/private-network web targets are blocked or filtered.
- Tool outputs are redacted for common secret key/value pairs, bearer tokens, and OpenAI-style secret keys before model/storage use.
- File contents, search results, git diffs, web results, command output, repo maps, and ranked context are marked as untrusted model context so prompt-injection text cannot redefine instructions.
- Large approval previews and mutation diff outputs are truncated before terminal/model use to avoid flooding long generated files.
- `--dry-run` skips writes and shell commands.
- `agent47` interactive mode starts write-enabled, while `/dry-run` remains available for inspect-only sessions.
- Human approval prompts are implemented for write/edit/apply-patch/shell/web-search actions.
- Mutation attempts are tracked and verified against disk state so final answers cannot claim file creation, edits, patches, or deletions without a verified successful mutation. Successful mutations also store inverse patches plus before/after hashes for audited revert.
- Interactive session state tracks the current task, pending user info, target files, changed files, blockers, and recent tool results for follow-up turns.
- Interactive mode starts with a centered `A G E N T 4 7` banner and uses optional terminal colors for panels, prompts, and status labels.
- Streaming-capable model clients now feed compact `STREAMING` progress markers in CLI and `agent47`, with `/stream [off]`, `--stream/--no-stream`, and `AGENT_STREAM` controls.
- Model profiles route runs through named `default`, `planner`, `coder`, `reviewer`, or `fast` settings, with per-profile model overrides and explicit `--model` precedence.
- `AGENT_FALLBACK_MODELS` tries fallback models in order after provider failures, and all-model failures become blocked run results instead of process crashes.
- Model attempts, token usage, fallback transitions, and optional cost estimates are stored in SQLite and summarized in work reports and JSON results.
- `code-agent history show <run-id>` and `/history-show <run-id>` expose saved step details for auditability.
- `code-agent resume <run-id>` and `/resume <run-id>` continue from compact saved run context while preserving a new run record.
- `update_plan` stores durable plan steps plus target files, owned files, intended checks, blockers, and risk notes in run history and resume context, with validation that only one step is `in_progress` and planned file paths stay workspace-relative.
- CLI and `agent47` render a structured work report before the final response for non-trivial runs, including current task, current step, files, planned targets, file ownership, planned checks, blockers, risk notes, progress, commands, validation, change summary, changed-line diff review, and final outcome.
- Structured work reports are persisted in SQLite, shown in `history show`, and included in resume context.
- `apply_patch` validates target paths, previews a multi-file change-set summary plus the full patch for approval, checks patch applicability, applies it with `git apply`, and records file-level operation/addition/deletion metadata.
- `code-agent revert <run-id>` and `/revert <run-id>` preview and apply stored inverse patches, fail on conflicts, and verify reverted files against recorded pre-change hashes/existence.
- `delete_file` removes files through a first-class approved mutation action instead of shell commands.
- `detect_verification` scans known project files for likely test, lint, typecheck, and build commands.
- `suggest_verification` ranks focused checks from changed paths, and successful file mutations now trigger automatic focused verification when commands are detected.
- Verification-like shell commands are recorded and appended to final summaries as pass/fail outcomes.
- `inspect_git_diff` shows dirty files and optional bounded diff hunks so Agent47 can avoid overwriting existing user changes.
- `repo_map`, `rank_context`, `symbol_index`, and `dependency_graph` give Agent47 a lightweight repository index, task-aware file ranking, compact declaration map, and import impact map before broad reads.
- Repo-map, ranking, symbol-index, and dependency-graph actions are stored in run history and summarized in structured work reports as context analysis.
- `code-agent evals` runs offline deterministic checks for greeting routing, blocked-write honesty, denied-read non-leakage, sandbox isolation, file creation, file editing, test fixing, failed-read recovery, dirty-worktree awareness, and patch-conflict recovery. `--json` emits metrics and per-case results for trend tracking.
- `code-agent run-json` emits versioned NDJSON events, fails closed on approvals by default, supports `--approval-stdin` request/response approvals with matching request IDs for parent editor processes, includes approval metadata for frontend UIs, and keeps `--approve-all` only for trusted automation.
- `code-agent doctor` checks Python version, platform, workspace writability, SQLite storage, console scripts, Git, ripgrep, API-key presence, and `.env` setup without exposing secrets.
- `docs/INSTALL.md` documents Windows, macOS, Linux, uv, editable pip, and pipx installation paths.
- Failed automatic verification is fed back to the model for recovery instead of allowing a premature final answer.
- Failed tool calls and invalid model action responses are automatically fed back to the model for recovery until the failure budget is exhausted.
- Public alpha package metadata, `LICENSE`, `CHANGELOG.md`, and `docs/RELEASE_CHECKLIST.md` are present.

## Last Updated

June 20, 2026
