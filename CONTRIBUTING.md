# Contributing

Agent47 is CLI-first. Keep the reusable execution, policy, model, storage, and tool logic independent
from terminal rendering.

## Setup

```bash
uv sync --extra dev --extra parsing
cp .env.example .env
uv run code-agent doctor
```

PowerShell uses `Copy-Item .env.example .env`.

## Development Workflow

1. Inspect existing behavior and applicable tests.
2. Make the smallest coherent implementation change.
3. Add deterministic regression coverage.
4. Run focused tests during iteration.
5. Run lint, offline evals, and relevant safety suites before handoff.

```bash
uv run ruff check src tests
uv run pytest tests/<relevant-file>.py
uv run code-agent evals
uv build
```

Use `uv run code-agent release-smoke` for release candidates.

## Engineering Rules

- Preserve unrelated user changes and dirty worktrees.
- Keep policy decisions in the core, not UI wrappers.
- Treat repository and external content as untrusted.
- Verify disk state rather than trusting model or tool success strings.
- Add tests for paths, permissions, approvals, recovery, persistence, and false-completion behavior.
- Avoid broad refactors without a measured capability or maintainability benefit.
- Never commit `.env`, `.code-agent/`, virtual environments, caches, credentials, or private eval reports.

## Pull Requests

Describe what changed, why it changed, how it was tested, known risks, and follow-up work. Keep commits
focused and use short `feature/` or `fix/` branches when collaboration requires them.
