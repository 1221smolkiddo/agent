# Progress Tracker

This document tracks how far the coding agent has come and what collaborators can use today.

## Current Stage

**Stage:** Basic single-model CLI agent

**Approximate progress toward a Codex-like local coding agent:** 10-15%

The project currently has a working Python CLI foundation with a reusable agent core, model client, local tools, run history, tests, and GitHub collaboration setup.

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

Inside `agent47`, there are currently **11 slash commands**:

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
| Code parsing: tree-sitter | Enabled as optional parsing extra |
| Storage: SQLite | Enabled |
| Testing: pytest | Enabled |
| Default model: Qwen via OpenRouter | Enabled |
| Packaging: uv | Enabled |
| Collaboration: GitHub docs/templates/CI | Enabled |

## What Is Left

The next major milestones are:

1. Replace direct file writes with patch preview and patch apply.
2. Add stronger sandbox isolation for processes and network policy.
3. Add streaming output in the terminal.
4. Add stronger repo context gathering.
5. Add planner state and visible step progress.
6. Add automatic test command detection and verification loops.
7. Add resumable sessions.
8. Add multi-model/provider support.
9. Add VS Code extension frontend.
10. Improve reliability, docs, and examples.

## Current Safety Notes

- Paths are guarded so tools cannot access files outside the workspace.
- Sandbox mode copies the workspace into `.code-agent/sandboxes/` and excludes secrets/local state.
- `.env`, `.venv`, caches, and local agent databases are ignored by git.
- `--dry-run` skips writes and shell commands.
- Human approval prompts are implemented for write/edit/shell/web-search actions.
- Failed tool calls are automatically fed back to the model for recovery until the failure budget is exhausted.
- Patch approval is not implemented yet.

## Last Updated

June 3, 2026
