# Architecture

This project is CLI-first, but the code is split so other frontends can reuse the same agent core later.

## Current Flow

```text
Typer CLI
  -> Settings
  -> Agent factory
  -> CodingAgent
  -> ModelClient
  -> ToolRegistry
  -> Storage
```

## Modules

- `code_agent.cli`: command line surface.
- `code_agent.interactive`: Copilot-style interactive terminal session.
- `code_agent.factory`: shared agent construction.
- `code_agent.agent`: agent loop and action parsing.
- `code_agent.models`: OpenAI-compatible model client pointed at OpenRouter by default.
- `code_agent.tools`: local filesystem, shell, search, git-awareness, and code summary tools.
- `code_agent.permissions`: terminal confirmation prompts for risky tools.
- `code_agent.sandbox`: local workspace copies for isolated agent runs.
- `code_agent.parsing`: optional tree-sitter based code structure summaries.
- `code_agent.resume`: run detail formatting and compact resume context.
- `code_agent.safety`: shell command risk classification, sensitive path checks, and secret redaction.
- `code_agent.storage`: SQLite run history.
- `code_agent.config`: environment and settings.
- `code_agent.schema`: typed action and tool result models.
- `code_agent.status`: operation labels for thinking, editing, searching, testing, building, and recovery.

## Codex-Like Direction

We cannot clone any proprietary internals, but we can build the same kind of product experience:

- repo-aware context gathering
- approved git status/diff inspection before edits
- tool-using agent loop
- automatic recovery from failed tool attempts
- careful file editing
- shell/test execution
- approvals for risky actions
- approved web search
- local sandbox runs
- run history
- resumable sessions
- eventually editor integration

## Future VS Code Shape

The VS Code extension should be a frontend that talks to this Python core. Early options:

- spawn the CLI as a subprocess
- expose a local JSON-RPC/stdin-stdout protocol
- later add a lightweight local service for long-running sessions
