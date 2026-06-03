# Code Agent

A Python CLI-first coding agent scaffold. The core logic lives outside the CLI so it can later be reused by a VS Code extension shell or background service.

The long-term goal is a Codex-like developer agent: repo-aware, tool-using, careful with edits, test-aware, and eventually available inside VS Code.

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

Inside interactive mode, type freely:

```text
agent47: Inspect this project and suggest the next feature
agent47: /status
agent47: /dry-run
agent47: /write
agent47: /exit
```

`agent47` is the only interactive launcher. The old `copilot` alias was removed to avoid colliding with GitHub Copilot.

## Environment

- `OPENROUTER_API_KEY` is required.
- `AGENT_MODEL` is optional. The CLI also accepts `--model`.
- `OPENROUTER_BASE_URL` is optional and defaults to `https://openrouter.ai/api/v1`.
- `OPENROUTER_SITE_URL` and `OPENROUTER_APP_NAME` are optional OpenRouter metadata headers.
- `AGENT_MAX_TOKENS` is optional and defaults to `4096`.
- `AGENT_DB_PATH` is optional and defaults to `.code-agent/agent.db`.
- `OPENAI_API_KEY` and `OPENAI_BASE_URL` are still accepted as a temporary fallback.

## Permissions

The agent asks for confirmation before:

- writing files
- editing files
- running shell commands
- searching the web

Read/list/local search actions are workspace-guarded and do not prompt by default.

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
    schema.py           Shared action/result models
    storage.py          SQLite run history
    parsing.py          Optional tree-sitter code summaries
    tools.py            pathlib, difflib, subprocess, ripgrep tools
tests/
  test_path_safety.py
```

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
