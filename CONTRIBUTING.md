# Contributing

Thanks for helping build this agent. The goal is to make a Python CLI coding agent that is useful first, then grow it into a collaborative editor experience.

## Setup

```powershell
uv sync --extra dev --extra parsing
Copy-Item .env.example .env
```

Add your API key to `.env`:

```text
OPENAI_API_KEY=...
```

Run checks:

```powershell
uv run pytest
uv run ruff check src tests
```

Run the CLI:

```powershell
uv run code-agent run "Inspect this project and suggest next steps"
```

## Development Principles

- Keep the CLI usable while the system evolves.
- Inspect before editing.
- Prefer small, reviewable changes.
- Keep the reusable agent core separate from UI surfaces.
- Add tests for safety boundaries, file operations, parsing, and storage.
- Do not commit secrets, `.env`, `.venv`, caches, or local agent databases.

## Branching

Use short feature branches:

```text
feature/tool-approvals
feature/streaming-output
fix/path-safety-windows
```

Open a pull request with:

- What changed
- How it was tested
- Any follow-up work
