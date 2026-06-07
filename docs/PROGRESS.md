# Progress Tracker

This document tracks how far the coding agent has come and what collaborators can use today.

## Current Stage

**Stage:** Basic single-model CLI agent with Agent47 engineering protocol

**Approximate progress toward an industry-standard local AI coding agent:** 15%

The project currently has a working Python CLI foundation with a reusable agent core, model client, local tools, run history, tests, GitHub collaboration setup, and a stronger Agent47 system prompt for planning, focused edits, verification, recovery, and honest reporting.

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

There are currently **9 model-requestable actions**:

| Action | Purpose |
| --- | --- |
| `final` | Finish and summarize the result for the user. |
| `list_files` | List files inside the workspace. |
| `read_file` | Read a file inside the workspace. |
| `write_file` | Write a full file inside the workspace. |
| `edit_file` | Replace exact text in a file. |
| `run_shell` | Run a shell command in the workspace. |
| `search` | Search with ripgrep. |
| `web_search` | Search the web after user approval. |
| `summarize_code` | Summarize a source file with tree-sitter when parsing deps are installed. |

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
| Shell execution: subprocess | Enabled |
| Project search: ripgrep | Enabled |
| Web search | Enabled with user approval |
| Local workspace sandbox | Enabled |
| Tool failure recovery loop | Enabled |
| Agent47 engineering protocol | Enabled |
| Operation status labels | Enabled |
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
| 1 | Structured patch editing | Gives safe, reviewable multi-file code changes instead of brittle full-file rewrites | Not started |
| 2 | Verification loop | Lets Agent47 detect and run the right tests, lint, typecheck, and builds after edits | Not started |
| 3 | Repo intelligence | Helps the agent choose relevant context using symbols, dependencies, git diff, and architecture summaries | Partial |
| 4 | Safer shell policy | Separates read-only, test/build, install/network, and destructive commands with stronger approvals | Partial |
| 5 | Durable sessions | Enables pause/resume, plan state, checkpoints, and long task recovery | Partial |
| 6 | Streaming UX | Makes CLI and interactive mode feel alive during model reasoning and tool execution | Not started |
| 7 | Multi-model/provider layer | Supports planner/coder/reviewer profiles, fallbacks, and cost-aware routing | Not started |
| 8 | Observability | Captures traces, timings, token use, failures, and debug bundles for reliability work | Partial |
| 9 | Collaboration workflow | Adds review mode, branch/commit/PR helpers, issue context, changelogs, and release notes | Partial |
| 10 | Editor integration | Brings Agent47 into VS Code with file context, diffs, approvals, and terminal output | Not started |
| 11 | Evaluation harness | Measures solve rate, edit correctness, verification rate, and regressions on fixture repos | Not started |
| 12 | Packaging hardening | Adds release profiles, install docs, upgrade notes, and platform-specific validation | Partial |

## Next Recommended Build Order

1. Add structured patch preview and patch apply.
2. Add automatic verification command detection.
3. Add git diff awareness so Agent47 respects existing user changes.
4. Add resumable sessions with run IDs and plan state.
5. Add streaming output in CLI and `agent47`.
6. Add model profiles and provider abstraction.
7. Add a JSON protocol for future VS Code integration.

## Current Safety Notes

- Paths are guarded so tools cannot access files outside the workspace.
- Sandbox mode copies the workspace into `.code-agent/sandboxes/` and excludes secrets/local state.
- `.env`, `.venv`, caches, and local agent databases are ignored by git.
- Read/list/project-search/code-summary actions now ask for user approval.
- `--dry-run` skips writes and shell commands.
- Human approval prompts are implemented for write/edit/shell/web-search actions.
- Failed tool calls are automatically fed back to the model for recovery until the failure budget is exhausted.
- Patch approval is not implemented yet.

## Last Updated

June 7, 2026
