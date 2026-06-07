# Progress Tracker

This document tracks how far the coding agent has come and what collaborators can use today.

## Current Stage

**Stage:** Basic single-model CLI agent with Agent47 engineering protocol

**Approximate progress toward an industry-standard local AI coding agent:** 24%

The project currently has a working Python CLI foundation with a reusable agent core, model client, local tools, non-workspace intent routing, structured patch application, verification command detection/suggestion/outcome summaries, clearer interactive terminal panels, run history, tests, GitHub collaboration setup, and a stronger Agent47 system prompt for planning, focused edits, verification, recovery, and honest reporting.

This file should be updated whenever a meaningful Agent47 capability is added. Keep the enabled commands/actions current, move completed items out of "What Is Left" when they land, and update the percentage only when the agent gains real product capability rather than documentation alone.

## Enabled CLI Commands

There are currently **3 user-facing CLI commands**:

| Command | Purpose |
| --- | --- |
| `code-agent run "task"` | Run the agent on a coding task. |
| `code-agent history` | Show recent saved agent runs from SQLite. |
| `agent47` | Open an interactive terminal session for free-form prompts. |

Common examples:

```powershell
uv run code-agent run "Inspect this project and suggest next steps"
uv run code-agent run --dry-run "Find risky areas in the codebase"
uv run code-agent run --sandbox "Try a risky change in an isolated copy"
uv run code-agent run --max-failures 5 "Recover from failed tool attempts"
uv run code-agent history
agent47
```

Inside `agent47`, there are currently **12 slash commands**:

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
| `/stop` | Quit interactive mode. |
| `/exit` | Quit interactive mode. |

## Enabled Agent Actions

There are currently **12 model-requestable actions**:

| Action | Purpose |
| --- | --- |
| `final` | Finish and summarize the result for the user. |
| `list_files` | List files inside the workspace. |
| `read_file` | Read a file inside the workspace. |
| `write_file` | Write a full file inside the workspace. |
| `edit_file` | Replace exact text in a file. |
| `apply_patch` | Apply a unified diff patch inside the workspace after approval. |
| `run_shell` | Run a shell command in the workspace. |
| `search` | Search the project with ripgrep, falling back to a built-in Python search when ripgrep is unavailable. |
| `web_search` | Search the web after user approval. |
| `summarize_code` | Summarize a source file with tree-sitter when parsing deps are installed. |
| `detect_verification` | Detect likely test, lint, typecheck, and build commands from project files. |
| `suggest_verification` | Suggest focused verification commands from changed file paths. |

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
| Shell execution: subprocess | Enabled |
| Project search: ripgrep plus Python fallback | Enabled |
| Web search | Enabled with user approval |
| Local workspace sandbox | Enabled |
| Tool failure recovery loop | Enabled |
| Agent47 engineering protocol | Enabled |
| Non-workspace question routing | Enabled |
| Verification command detection | Enabled |
| Verification command suggestion | Enabled |
| Verification outcome summaries | Enabled |
| Operation status labels | Enabled |
| Bordered interactive terminal panels | Enabled |
| Stop shortcut: `Ctrl+C` and `/stop` | Enabled |
| Code parsing: tree-sitter | Enabled as optional parsing extra |
| Storage: SQLite | Enabled |
| Testing: pytest | Enabled |
| Default model: Qwen via OpenRouter | Enabled |
| Packaging: uv | Enabled |
| Collaboration: GitHub docs/templates/CI | Enabled |

## What Is Left

These are the remaining capability areas needed for Agent47 to feel like a fully fledged, industry-standard AI coding agent.

| Priority | Capability | Why it matters | Status |
| --- | --- | --- | --- |
| 1 | Structured patch editing | Gives safe, reviewable multi-file code changes instead of brittle full-file rewrites | Partial |
| 2 | Verification loop | Lets Agent47 detect, suggest, run, and summarize the right tests, lint, typecheck, and builds after edits | Partial |
| 3 | Repo intelligence | Helps the agent choose relevant context using symbols, dependencies, git diff, and architecture summaries | Partial |
| 4 | Safer shell policy | Separates read-only, test/build, install/network, and destructive commands with stronger approvals | Partial |
| 5 | Durable sessions | Enables pause/resume, plan state, checkpoints, and long task recovery | Partial |
| 6 | Streaming UX | Makes CLI and interactive mode feel alive during model reasoning and tool execution | Partial |
| 7 | Multi-model/provider layer | Supports planner/coder/reviewer profiles, fallbacks, and cost-aware routing | Not started |
| 8 | Observability | Captures traces, timings, token use, failures, and debug bundles for reliability work | Partial |
| 9 | Collaboration workflow | Adds review mode, branch/commit/PR helpers, issue context, changelogs, and release notes | Partial |
| 10 | Editor integration | Brings Agent47 into VS Code with file context, diffs, approvals, and terminal output | Not started |
| 11 | Evaluation harness | Measures solve rate, edit correctness, verification rate, and regressions on fixture repos | Not started |
| 12 | Packaging hardening | Adds release profiles, install docs, upgrade notes, and platform-specific validation | Partial |

## Next Recommended Build Order

1. Finish the verification loop so Agent47 automatically runs focused checks after edits without relying on the model to choose every command.
2. Add git diff awareness so Agent47 respects existing user changes.
3. Add resumable sessions with run IDs and plan state.
4. Add streaming output in CLI and `agent47`.
5. Add model profiles and provider abstraction.
6. Add a JSON protocol for future VS Code integration.

## Current Safety Notes

- Paths are guarded so tools cannot access files outside the workspace.
- Workspace tools are blocked for prompts that do not appear to be about the local project, local files, code changes, tests, or commands.
- Sandbox mode copies the workspace into `.code-agent/sandboxes/` and excludes secrets/local state.
- `.env`, `.venv`, caches, and local agent databases are ignored by git.
- Read/list/project-search/code-summary actions now ask for user approval.
- Project search skips local state and secret files such as `.env`, `.git`, `.code-agent`, caches, and virtual environments.
- `--dry-run` skips writes and shell commands.
- Human approval prompts are implemented for write/edit/apply-patch/shell/web-search actions.
- `apply_patch` validates target paths, previews the full patch for approval, checks patch applicability, and applies it with `git apply`.
- `detect_verification` scans known project files for likely test, lint, typecheck, and build commands.
- `suggest_verification` ranks focused checks from changed paths, and mutation tool results now include changed-path verification hints.
- Verification-like shell commands are recorded and appended to final summaries as pass/fail outcomes.
- Failed tool calls are automatically fed back to the model for recovery until the failure budget is exhausted.
- Multi-file change set metadata and richer patch conflict recovery are not implemented yet.

## Last Updated

June 7, 2026
