# Agent47

A Python CLI-first AI coding agent scaffold. The core logic lives outside the CLI so it can later be reused by a VS Code extension shell or background service.

The long-term goal is Agent47: a real AI engineering assistant that is repo-aware, tool-using, careful with edits, test-aware, and eventually available inside VS Code.

Agent47 now uses an engineering protocol in its system prompt: classify the request, plan internally, inspect relevant files, make focused edits, verify changes when practical, recover from failures, and report honestly about what changed and what was checked.

Agent47 is not limited to the current folder. It can answer general questions directly, use web search for current external information when needed, and only inspect project files when the request is actually about the local workspace.

## Industry-Standard Target

Agent47 is being built toward the baseline expected from a serious AI coding agent:

- Repo-aware context gathering with symbol, dependency, and git-diff awareness.
- Structured patch editing with diff preview, approval, conflict handling, and rollback-friendly history.
- Automatic test, lint, typecheck, and build detection with focused verification loops.
- Safer autonomy through command policies, sandbox limits, secret redaction, and explicit approvals.
- Durable sessions with resumable plans, run history, per-repo memory, and compacted context.
- Multi-model support with provider abstraction, model profiles, fallback routing, streaming, and cost tracking.
- Collaboration workflows for review, commits, branches, pull requests, issues, changelogs, and release notes.
- Editor integration, starting with a JSON protocol and eventually a VS Code extension.
- Evaluation harnesses that measure task success, edit correctness, verification rate, and regressions.

## Quick Start

```bash
uv sync --extra dev
cp .env.example .env
uv run code-agent run "Inspect this project and suggest the next feature"
```

Add your OpenRouter key to `.env` before running the agent:

```text
OPENROUTER_API_KEY=...
AGENT_MODEL=qwen/qwen3-coder
```

If `uv` is not installed yet:

```bash
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev,parsing]"
.\.venv\Scripts\code-agent run "Inspect this project"
```

On macOS/Linux:

```bash
export OPENROUTER_API_KEY="your-api-key"
uv run code-agent run "Create a README section describing this project"
```

## CLI Usage

```bash
code-agent run "Fix the failing pytest"
code-agent run --cwd ../some-project --model qwen/qwen3-coder "Add tests for the parser"
code-agent run --dry-run "Refactor the CLI argument parser"
code-agent run --sandbox "Try a risky refactor in an isolated copy"
code-agent run --max-failures 5 "Fix the issue and recover from failed attempts"
code-agent history
```

With `uv`:

```bash
uv run code-agent run "Fix the failing pytest"
```

Interactive mode:

```bash
agent47
```

Interactive mode separates prompts, responses, help, status, and history into bordered terminal panels so user input and Agent47 output do not visually merge.
It also carries a short in-memory transcript into follow-up turns so Agent47 can continue recent work without relying on magic phrases.
Interactive mode starts write-enabled so file creation and edits can actually happen after approval. Use `/dry-run` when you want inspect-only behavior.

Inside interactive mode, type freely:

```text
agent47: Inspect this project and suggest the next feature
agent47: /status
agent47: /dry-run
agent47: /write
agent47: /sandbox
agent47: /sandbox off
agent47: /max-failures 5
agent47: /stop
agent47: /exit
```

`agent47` is the only interactive launcher. The old `copilot` alias was removed to avoid colliding with GitHub Copilot.

## Environment

- `OPENROUTER_API_KEY` is required.
- `AGENT_MODEL` is optional. The CLI also accepts `--model`.
- `OPENROUTER_BASE_URL` is optional and defaults to `https://openrouter.ai/api/v1`.
- `OPENROUTER_SITE_URL` and `OPENROUTER_APP_NAME` are optional OpenRouter metadata headers.
- `AGENT_MAX_TOKENS` is optional and defaults to `4096`.
- `AGENT_MAX_FAILURES` is optional and defaults to `3`.
- `AGENT_DB_PATH` is optional and defaults to `.code-agent/agent.db`.
- `OPENAI_API_KEY` and `OPENAI_BASE_URL` are still accepted as a temporary fallback.

## Permissions

The agent asks for confirmation before:

- listing files
- reading files
- searching the project
- summarizing code structure
- detecting and suggesting verification commands
- writing files
- editing files
- applying structured patches
- running shell commands
- searching the web

All file access remains workspace-guarded.

Project search uses `ripgrep` when available and falls back to a built-in Python search when `ripgrep` is missing from the agent process PATH. Search skips local state and secret files such as `.env`, `.git`, `.code-agent`, caches, and virtual environments.

## Failure Recovery

The agent automatically loops after failed tool calls. It feeds the failure back to the model with recovery instructions so the model can inspect, retry, or choose another action.

```bash
code-agent run --max-failures 5 "Fix the failing test"
```

It stops after the configured consecutive failure budget is exhausted.

## Operation Status

The CLI prints status lines while the agent works:

- `THINKING` before model reasoning.
- `READING` when listing or reading files.
- `SEARCHING` for project search.
- `SEARCHING WEB` for general web search.
- `CHECKING project verification commands` when detecting test/lint/build commands.
- `CHECKING suggested verification` when choosing focused checks for changed files.
- `EDITING` for file writes and edits.
- `EDITING applying patch` for structured patch edits.
- `INSTALLING`, `BUILDING`, `TESTING`, or `CHECKING` for recognized shell commands.
- `RECOVERING` when a tool fails and the agent is trying another path.
- `DONE` when the agent reaches a final answer.

## Stopping The Agent

Use `Ctrl+C` to stop a running operation.

Inside `agent47`, you can also quit between prompts with:

```text
/stop
/exit
```

Esc is not used as the default stop key because most terminals treat it as line-editing input rather than a process interrupt.

## Sandbox

Use sandbox mode when you want the agent to experiment without touching the real project:

```bash
code-agent run --sandbox "Try changing the CLI flow"
```

In `agent47`:

```text
agent47: /sandbox
agent47: /write
agent47: Try the change in the sandbox
agent47: /sandbox off
```

Sandboxes are copied into `.code-agent/sandboxes/` and exclude `.env`, `.git`, `.venv`, caches, and other local state.

## Project Shape

```text
src/
  code_agent/
    agent.py            Agent loop
    cli.py              Typer CLI entrypoint
    interactive.py      Copilot-style terminal session
    factory.py          Shared agent construction
    config.py           dotenv + pydantic-settings
    models.py           OpenAI-compatible model client for OpenRouter
    prompts.py          System prompt
    sandbox.py          Local workspace sandbox copies
    schema.py           Shared action/result models
    storage.py          SQLite run history
    parsing.py          Optional tree-sitter code summaries
    tools.py            pathlib, difflib, subprocess, ripgrep tools
tests/
  test_path_safety.py
```

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md).

For the complete team issue breakdown and checkbox task board from the current CLI to an industry-grade Agent47, see [docs/TEAM_BUILD_PLAN.md](docs/TEAM_BUILD_PLAN.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
