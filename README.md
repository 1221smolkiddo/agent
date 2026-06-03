# Code Agent

A Python CLI-first coding agent scaffold. The core logic lives outside the CLI so it can later be reused by a VS Code extension shell or background service.

The long-term goal is a Codex-like developer agent: repo-aware, tool-using, careful with edits, test-aware, and eventually available inside VS Code.

## Quick Start

```bash
uv sync --extra dev
cp .env.example .env
uv run code-agent run "Inspect this project and suggest the next feature"
```

If `uv` is not installed yet:

```bash
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev,parsing]"
.\.venv\Scripts\code-agent run "Inspect this project"
```

On macOS/Linux:

```bash
export OPENAI_API_KEY="your-api-key"
uv run code-agent run "Create a README section describing this project"
```

## CLI Usage

```bash
code-agent run "Fix the failing pytest"
code-agent run --cwd ../some-project --model gpt-4o-mini "Add tests for the parser"
code-agent run --dry-run "Refactor the CLI argument parser"
code-agent history
```

With `uv`:

```bash
uv run code-agent run "Fix the failing pytest"
```

## Environment

- `OPENAI_API_KEY` is required.
- `AGENT_MODEL` is optional. The CLI also accepts `--model`.
- `OPENAI_BASE_URL` is optional and defaults to `https://api.openai.com/v1`.
- `AGENT_DB_PATH` is optional and defaults to `.code-agent/agent.db`.

## Project Shape

```text
src/
  code_agent/
    agent.py            Agent loop
    cli.py              Typer CLI entrypoint
    config.py           dotenv + pydantic-settings
    models.py           OpenAI-compatible model client
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
