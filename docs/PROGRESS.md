# Progress Tracker

This document tracks how far the coding agent has come and what collaborators can use today.

## Current Stage

**Stage:** Basic single-model CLI agent with Agent47 engineering protocol

**Approximate progress toward an industry-standard local AI coding agent:** 37%

The project currently has a working Python CLI foundation with a reusable agent core, model client, local tools, durable plan checkpoints with structured work reports, non-workspace intent routing, general web search with provider fallback, structured patch application, approved git-diff awareness, verification command detection/suggestion/automatic execution/outcome summaries, mutation-attempt tracking for truthful final answers, clearer interactive terminal panels with short transcript context and structured session state, run history detail views, resumable runs, tests, GitHub collaboration setup, and a stronger Agent47 system prompt for planning, focused edits, verification, recovery, and honest reporting.

This file should be updated whenever a meaningful Agent47 capability is added. Keep the enabled commands/actions current, move completed items out of "What Is Left" when they land, and update the percentage only when the agent gains real product capability rather than documentation alone.

## Enabled CLI Commands

There are currently **5 user-facing CLI command entries**:

| Command | Purpose |
| --- | --- |
| `code-agent run "task"` | Run the agent on a coding task. |
| `code-agent history` | Show recent saved agent runs from SQLite. |
| `code-agent history show <run-id>` | Show saved steps for one agent run. |
| `code-agent resume <run-id>` | Resume a saved run with compact prior context. |
| `agent47` | Open an interactive terminal session for free-form prompts. |

Common examples:

```powershell
uv run code-agent run "Inspect this project and suggest next steps"
uv run code-agent run --dry-run "Find risky areas in the codebase"
uv run code-agent run --sandbox "Try a risky change in an isolated copy"
uv run code-agent run --max-failures 5 "Recover from failed tool attempts"
uv run code-agent history
uv run code-agent history show 12
uv run code-agent resume 12 "Continue after the failed check"
agent47
```

Inside `agent47`, there are currently **14 slash commands**:

| Slash command | Purpose |
| --- | --- |
| `/help` | Show interactive help. |
| `/status` | Show workspace, model, mode, and max steps. |
| `/dry-run` | Inspect only; skip writes and shell commands. |
| `/write` | Allow writes and shell commands. |
| `/cwd <path>` | Change workspace. |
| `/sandbox [off]` | Create and use a sandbox copy, or return to the base workspace. |
| `/model <name>` | Change model for this session. |
| `/max-steps <n>` | Change max agent loop steps. |
| `/max-failures <n>` | Change consecutive failure recovery budget. |
| `/history` | Show recent saved agent runs. |
| `/history-show <run-id>` | Show saved steps for one agent run. |
| `/resume <run-id> [instruction]` | Resume a saved run with optional extra instruction. |
| `/stop` | Quit interactive mode. |
| `/exit` | Quit interactive mode. |

## Enabled Agent Actions

There are currently **15 model-requestable actions**:

| Action | Purpose |
| --- | --- |
| `final` | Finish and summarize the result for the user. |
| `update_plan` | Checkpoint durable task steps with `pending`, `in_progress`, `completed`, or `blocked` status. |
| `list_files` | List files inside the workspace. |
| `read_file` | Read a file inside the workspace. |
| `write_file` | Write a full file inside the workspace. |
| `edit_file` | Replace exact text in a file. |
| `apply_patch` | Apply a unified diff patch inside the workspace after approval. |
| `delete_file` | Delete a file inside the workspace after approval. |
| `run_shell` | Run a shell command in the workspace. |
| `search` | Search the project with ripgrep, falling back to a built-in Python search when ripgrep is unavailable. |
| `web_search` | Search the web after user approval, using provider fallback when available. |
| `summarize_code` | Summarize a source file with tree-sitter when parsing deps are installed. |
| `detect_verification` | Detect likely test, lint, typecheck, and build commands from project files. |
| `suggest_verification` | Suggest focused verification commands from changed file paths. |
| `inspect_git_diff` | Inspect git status, changed paths, and optional bounded diff hunks before editing. |

## Installed / Supported Stack

| Area | Status |
| --- | --- |
| Core language: Python | Enabled |
| CLI framework: Typer | Enabled |
| Model API: OpenRouter via OpenAI-compatible chat completions | Enabled |
| Model output cap: configurable `AGENT_MAX_TOKENS` | Enabled |
| Config: python-dotenv + pydantic-settings | Enabled |
| File ops: pathlib | Enabled |
| Diffs: difflib | Enabled |
| Structured patch application: git apply | Enabled |
| Durable plan checkpoints | Enabled |
| Structured work reports in CLI and `agent47` | Enabled |
| Shell execution: subprocess | Enabled |
| Shell command risk policy | Enabled |
| Web/network policy | Enabled |
| Secret output redaction | Enabled |
| Bounded approval previews and mutation diff outputs | Enabled |
| Project search: ripgrep plus Python fallback | Enabled |
| Web search | Enabled with user approval |
| Local workspace sandbox | Enabled |
| Tool failure recovery loop | Enabled |
| Agent47 engineering protocol | Enabled |
| Non-workspace question routing | Enabled |
| General web search provider fallback | Enabled |
| Git diff awareness | Enabled |
| Verification command detection | Enabled |
| Verification command suggestion | Enabled |
| Automatic focused verification execution | Enabled |
| Verification outcome summaries | Enabled |
| Mutation-attempt tracking for final-answer honesty | Enabled |
| Operation status labels | Enabled |
| Bordered interactive terminal panels | Enabled |
| Centered interactive banner and terminal color theme | Enabled |
| Short interactive transcript context | Enabled |
| Structured interactive session state | Enabled |
| Run detail views | Enabled |
| Resumable runs | Enabled |
| Stop shortcut: `Ctrl+C` and `/stop` | Enabled |
| Code parsing: tree-sitter | Enabled as optional parsing extra |
| Storage: SQLite | Enabled |
| Testing: pytest | Enabled |
| Default model: Qwen via OpenRouter | Enabled |
| Packaging: uv | Enabled |
| Collaboration: GitHub docs/templates/CI | Enabled |

## What Is Left

These are the remaining capability areas needed for Agent47 to feel like a fully fledged, industry-standard AI coding agent.

For the complete team issue breakdown, see [TEAM_BUILD_PLAN.md](TEAM_BUILD_PLAN.md).

| Priority | Capability | Why it matters | Status |
| --- | --- | --- | --- |
| 1 | Structured patch editing | Gives safe, reviewable multi-file code changes instead of brittle full-file rewrites | Partial |
| 2 | Verification loop | Lets Agent47 detect, suggest, run, and summarize the right tests, lint, typecheck, and builds after edits | Baseline done |
| 3 | Repo intelligence | Helps the agent choose relevant context using symbols, dependencies, git diff, and architecture summaries | Partial, with git awareness baseline |
| 4 | Safer shell policy | Separates read-only, test/build, install/network, and destructive commands with stronger approvals | Baseline done |
| 5 | Durable sessions | Enables pause/resume, plan state, checkpoints, and long task recovery | Partial, with resumable runs, visible durable plan checkpoints, and structured work reports |
| 6 | Streaming UX | Makes CLI and interactive mode feel alive during model reasoning and tool execution | Partial |
| 7 | Multi-model/provider layer | Supports planner/coder/reviewer profiles, fallbacks, and cost-aware routing | Not started |
| 8 | Observability | Captures traces, timings, token use, failures, and debug bundles for reliability work | Partial |
| 9 | Collaboration workflow | Adds review mode, branch/commit/PR helpers, issue context, changelogs, and release notes | Partial |
| 10 | Editor integration | Brings Agent47 into VS Code with file context, diffs, approvals, and terminal output | Not started |
| 11 | Evaluation harness | Measures solve rate, edit correctness, verification rate, and regressions on fixture repos | Not started |
| 12 | Packaging hardening | Adds release profiles, install docs, upgrade notes, and platform-specific validation | Partial |

## Next Recommended Build Order

1. Add streaming output in CLI and `agent47`.
2. Add model profiles and provider abstraction.
3. Add a JSON protocol for future VS Code integration.
4. Add a lightweight repo index with relevance ranking.
5. Add richer planner fields for blockers, checks, and file ownership.

## Current Safety Notes

- Paths are guarded so tools cannot access files outside the workspace.
- Workspace tools are blocked for prompts that do not appear to be about the local project, local files, code changes, tests, or commands.
- Sandbox mode copies the workspace into `.code-agent/sandboxes/` and excludes secrets/local state.
- `.env`, `.venv`, caches, and local agent databases are ignored by git.
- Read/list/project-search/code-summary actions now ask for user approval.
- Project search skips local state and secret files such as `.env`, `.git`, `.code-agent`, caches, and virtual environments.
- Direct file reads and mutations against sensitive credential files such as `.env`, `.npmrc`, `.pypirc`, and `.netrc` are refused by default.
- Shell commands are classified by risk before approval, and destructive commands such as `git reset --hard`, recursive force deletes, and aggressive `git clean` forms are blocked by policy.
- Web search approval prompts include provider domains and query text, and localhost/private-network web targets are blocked or filtered.
- Tool outputs are redacted for common secret key/value pairs, bearer tokens, and OpenAI-style secret keys before model/storage use.
- Large approval previews and mutation diff outputs are truncated before terminal/model use to avoid flooding long generated files.
- `--dry-run` skips writes and shell commands.
- `agent47` interactive mode starts write-enabled, while `/dry-run` remains available for inspect-only sessions.
- Human approval prompts are implemented for write/edit/apply-patch/shell/web-search actions.
- Mutation attempts are tracked so final answers cannot claim file creation or edits without a verified successful mutation.
- Interactive session state tracks the current task, pending user info, target files, changed files, blockers, and recent tool results for follow-up turns.
- Interactive mode starts with a centered `A G E N T 4 7` banner and uses optional terminal colors for panels, prompts, and status labels.
- `code-agent history show <run-id>` and `/history-show <run-id>` expose saved step details for auditability.
- `code-agent resume <run-id>` and `/resume <run-id>` continue from compact saved run context while preserving a new run record.
- `update_plan` stores durable plan steps in run history and resume context, with validation that only one step is `in_progress`.
- CLI and `agent47` render a structured work report before the final response for non-trivial runs, including current task, current step, files, progress, commands, validation, change summary, changed-line diff review, and final outcome.
- `apply_patch` validates target paths, previews the full patch for approval, checks patch applicability, and applies it with `git apply`.
- `delete_file` removes files through a first-class approved mutation action instead of shell commands.
- `detect_verification` scans known project files for likely test, lint, typecheck, and build commands.
- `suggest_verification` ranks focused checks from changed paths, and successful file mutations now trigger automatic focused verification when commands are detected.
- Verification-like shell commands are recorded and appended to final summaries as pass/fail outcomes.
- `inspect_git_diff` shows dirty files and optional bounded diff hunks so Agent47 can avoid overwriting existing user changes.
- Failed automatic verification is fed back to the model for recovery instead of allowing a premature final answer.
- Failed tool calls and invalid model action responses are automatically fed back to the model for recovery until the failure budget is exhausted.
- Multi-file change set metadata and richer patch conflict recovery are not implemented yet.

## Last Updated

June 18, 2026
