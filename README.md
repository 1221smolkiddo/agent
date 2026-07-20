# Agent47

Agent47 is a CLI-first Python coding agent with repository intelligence, structured tool use,
durable execution state, verification, recovery, sandboxing, model fallback, and machine-readable
frontend events. The reusable core is separate from the terminal interfaces so other clients can
drive the same agent behavior.

Agent47 is currently alpha software. It has serious safety and reliability controls, but generated
changes and approved commands still require human review. Read
[Known Limitations](docs/KNOWN_LIMITATIONS.md) before using it on sensitive repositories.

## Current Capabilities

- CLI, interactive terminal, and versioned NDJSON interfaces.
- Workspace-aware file reads, exact edits, writes, deletes, unified patches, search, and code summaries.
- Journaled workspace transactions with automatic checkpoints, atomic multi-file commit/rollback, crash
  recovery, three-way merge, move support, undo/redo, and selective or whole-workspace snapshot restore.
- A persistent incremental project graph with symbols, imports, calls, references, test-to-source mappings,
  configuration relationships, token-aware context ranking, parallel changed-file indexing, background refresh,
  selective invalidation, project memory, and git-diff awareness.
- Native JSON-RPC language-server clients with definition, references, hover, completion, workspace symbols,
  diagnostics, rename previews, formatting previews, code actions, crash restart, and deterministic cleanup.
- Durable hierarchical plans with dependencies, typed acceptance gates, evidence-backed hypotheses,
  confidence scoring, explicit blockers, checkpointed resume, and mandatory replanning after invalidated assumptions.
- Outcome-aware loop prevention tied to workspace generations.
- Bounded context compaction that preserves the original task and recent evidence.
- Automatic test, lint, typecheck, and build detection with verification diagnostics and reviewer passes.
- Timestamped command execution with separate stdout/stderr, ordered event logs, structured cross-language
  compiler/test/linter/runtime diagnostics, root-cause ranking, fix suggestions, and JSON export.
- Mutation verification that rejects false completion claims.
- Permission modes, sensitive-file refusal, secret redaction, path guards, and command classification.
- Dry-run mode, copied-workspace sandboxes, and Docker or Podman container backends.
- OpenAI-compatible providers, model profiles, streaming, bounded retries, state-preserving cross-provider fallback,
  provider-handoff audit records, usage, and cost records.
- SQLite run history, work reports, redacted debug bundles, per-repository memory, deletion, pruning, resume, and revert.
- Deterministic offline evals, opt-in live evals, capability reports, release smoke checks, and collaboration helpers.

## Quick Start

Agent47 requires Python 3.11 or newer. The recommended environment manager is `uv`.

```bash
uv sync --extra dev --extra parsing
cp .env.example .env
```

On PowerShell:

```powershell
Copy-Item .env.example .env
```

Set a provider key in `.env`, then verify the installation:

```text
AGENT_PROVIDER=openrouter
OPENROUTER_API_KEY=...
AGENT_MODEL=qwen/qwen3-coder
```

```bash
uv run code-agent doctor --strict
uv run code-agent run "Inspect this repository and identify the highest-risk defect"
```

See [Install](docs/INSTALL.md) for `pip`, `pipx`, and platform-specific setup.

## Interfaces

### One-Shot CLI

```bash
uv run code-agent run "Fix the failing parser tests"
uv run code-agent run --dry-run "Plan the parser refactor"
uv run code-agent run --sandbox "Refactor the parser in an isolated workspace"
uv run code-agent run --sandbox --sandbox-backend docker "Run the refactor with container isolation"
uv run code-agent run --deny-network-shell "Fix tests without install or network commands"
```

Use `code-agent run --help` for model, profile, approval, sandbox, failure-budget, streaming,
and workspace options.

### Interactive CLI

```bash
uv run agent47
```

Interactive mode keeps a bounded transcript and structured session state. Important commands include:

```text
/status
/dry-run
/write
/sandbox
/sandbox diff
/sandbox apply
/model select
/profile coder
/steer focus on the failing test only
/history-show 12
/resume 12 continue from verification
/revert 12
/stop
```

`Ctrl+C` interrupts the current model or tool turn. `Ctrl+E`, `/stop`, and `/exit` leave the session.

### JSON Protocol

```bash
uv run code-agent run-json --dry-run "Inspect this repository"
```

`run-json` emits versioned newline-delimited JSON for run lifecycle, actions, approvals, recovery,
execution state, work reports, failures, and final results. Approvals fail closed by default. Use
`--approval-stdin` for request-matched approval responses or `--approve-all` only in trusted automation;
manual-only actions remain denied.

## Main Commands

```text
code-agent models
code-agent run
code-agent run-json
code-agent doctor
code-agent evals
code-agent eval-reports
code-agent release-smoke
code-agent history
code-agent resume
code-agent revert
code-agent transactions
code-agent sandbox
code-agent collab
```

Useful operational examples:

```bash
uv run code-agent models
uv run code-agent history
uv run code-agent history show 12
uv run code-agent history export 12
uv run code-agent history delete 12 --yes
uv run code-agent history prune --keep-last 20 --yes
uv run code-agent resume 12 "Continue from the failed verification"
uv run code-agent revert 12
uv run code-agent transactions list
uv run code-agent transactions undo <transaction-id>
uv run code-agent transactions redo <transaction-id>
uv run code-agent transactions restore <transaction-id>
uv run code-agent transactions recover
uv run code-agent sandbox health --backend docker
uv run code-agent collab review --run-id 12 --strict
```

## Execution Model

Workspace runs follow an explicit lifecycle:

1. Classify the request and build a token-bounded context pack of exact files, symbols, relationships, and tests.
2. Create or update a durable hierarchical plan with dependencies, hypotheses, and acceptance criteria.
3. Execute one validated action at a time.
4. Verify mutations against disk and run selected project checks.
5. Diagnose failures, record evidence, invalidate stale assumptions, and require a revised plan.
6. Reject repeated identical outcomes and unsupported completion claims.
7. Checkpoint execution before later model turns and persist evidence, provider handoffs, confidence, and reports.

Plans are execution contracts. Dependencies gate active work; completed steps with unmet `command:`, `evidence:`,
or `file:` criteria become blocked. Failed planned verification requires a new plan revision before mutation,
execution, or finalization. Resumed runs hydrate the latest persisted execution checkpoint.

See [Architecture](docs/ARCHITECTURE.md) for module boundaries and detailed data flow.

## Models And Providers

Supported direct providers are OpenRouter, OpenAI, Gemini, DeepSeek, and NVIDIA NIM through
OpenAI-compatible chat-completions APIs. Presets and registered models can supply provider-specific
runtime defaults.

```bash
uv run code-agent run --preset gemini-flash "Fix the failing test"
uv run code-agent run --preset glm-5.2 "Implement the requested feature"
uv run code-agent run --provider nvidia --model z-ai/glm-5.2 "Review this repository"
```

Fallback routing is error-aware. Capacity, rate-limit, credit, timeout, connection, server, and
empty-response failures may retry or fall through. Authentication, missing-model, and malformed-request
errors stop instead of hiding configuration defects.

The canonical configuration list is `.env.example`. Important controls include:

- `AGENT_PROVIDER`, `AGENT_MODEL_PRESET`, `AGENT_MODEL`, and `AGENT_PROFILE`.
- Provider API keys and base URLs.
- `AGENT_FALLBACK_MODELS`, `AGENT_MAX_TOKENS`, and model retry settings.
- `AGENT_MAX_FAILURES` and `AGENT_CONTEXT_MAX_CHARS`.
- `AGENT_DB_PATH`, `AGENT_STREAM`, and `AGENT_REVIEWER_PASS`.
- `AGENT_SHELL_NETWORK`, `AGENT_SANDBOX_BACKEND`, and `AGENT_SANDBOX_IMAGE`.

## Permissions And Safety

File access, repository inspection, mutations, shell commands, and web search are approval-gated.
JSON mode denies approvals unless an explicit approval mechanism is configured.

Core controls include:

- Workspace-relative path validation for file and patch operations.
- Refusal to read or modify credential files such as `.env`, `.npmrc`, `.pypirc`, and `.netrc`.
- Pattern-based secret redaction before tool output or state is stored.
- Shell-free local process execution with explicit argv and process-tree cleanup.
- Durable managed processes with interactive control, POSIX PTYs, readiness checks, restart backoff,
  rotating redacted logs, ordered events, and process-tree resource monitoring.
- Blocking of destructive, compound-shell, workspace-escape, and arbitrary inline-code commands.
- Risk labeling and explicit approval for allowed verification, git, install, network, and read-only commands.
- Untrusted-context markers for repository content, diffs, command output, search results, and web results.
- Private HOME, temporary, and cache directories for local commands.
- Disk, process, CPU, memory, timeout, image, digest, and network controls for configured sandboxes.
- Per-workspace reusable Docker/Podman containers shared by shell commands, LSP servers, and managed jobs.
- Language-server edits are converted to unified patches and never bypass normal patch approval and verification.

Ordinary runs use the hardened local subprocess policy. `--sandbox` is a strict security mode: it
selects a healthy Docker or Podman backend and refuses to start if process isolation cannot be proven.
Strict sandbox runs transport language-server JSON-RPC and managed workloads through the workspace container.

### Security Reports

See [Security Control Review](docs/SECURITY_REVIEW.md) for the implemented/partial/absent control
matrix, local-versus-enterprise scope, compliance position, and prioritized residual risks.

Report vulnerabilities through the repository's private security channel when available. Include the
commit SHA, OS, Python version, command, operating mode, and a redacted
`code-agent history export <run-id>` bundle. Never include credentials, exploit secrets, or private
repository content in a public report.

## Local And Provider Data

Agent47 stores local state under `.code-agent/`:

- `agent.db`: redacted run tasks, steps, execution state, reports, model usage, file facts, persistent symbols,
  project-graph edges, and incremental-index statistics.
- `memory/project.md`: approval-gated, secret-scanned stable project facts.
- `sandboxes/`: copied workspaces.
- `debug-bundles/`: explicitly exported redacted diagnostics.
- `eval-reports/`: saved benchmark results.
- `processes/jobs/`: managed-process specifications, state, rotating logs, events, and one-shot controls.
- `containers/state.json`: reusable workspace-container identity, image, runtime, mount, and health metadata.

Model-backed runs send the user task, system instructions, approved context, bounded project memory,
tool evidence, and verification output to the configured provider. Provider retention is governed by
that provider's policy. Web search sends approved queries to external search services.

Use `history delete`, `history prune`, or remove `.code-agent/` to delete local history. Redaction is
defense in depth and cannot identify every possible secret format.

## Managed Processes

Use managed processes for development servers, watchers, and interactive commands that must not block an
agent step. Each job is owned by a detached worker and can continue after the launching Agent47 CLI exits.

```bash
uv run code-agent processes start "npm run dev" --name web --port 3000 --auto-restart
uv run code-agent processes list --active
uv run code-agent processes inspect <process-id>
uv run code-agent processes logs <process-id> --stream stdout
uv run code-agent processes events <process-id> --after 0
uv run code-agent processes input <process-id> "yes\n"
uv run code-agent processes restart <process-id>
uv run code-agent processes stop <process-id> --grace 5
```

The worker stores only environment key names and a fingerprint; values are inherited from the scrubbed
launch environment and are never written to the job specification. Output is redacted before rotating log
and event persistence. Readiness can use an explicit localhost port or a port detected from common local URLs.
Unexpected non-zero exits can restart with bounded exponential backoff. Stop requests first signal the process
group, wait for the grace period, and then force the remaining tree.
When container execution is enabled, the detached control worker remains on the host while the actual project
workload, readiness probe, and resource scope run inside the reusable workspace container.

## Sandboxes

`--dry-run` disables mutations and shell execution. `--sandbox` combines a copied workspace with required
Docker or Podman process isolation and explicit promotion back to the base repository. It never silently
falls back to local execution. The effective `.code-agent/policy.toml` is preserved in the copy.

```bash
uv run code-agent run --sandbox "Try the refactor"
uv run code-agent sandbox diff .code-agent/sandboxes/<name>
uv run code-agent sandbox apply .code-agent/sandboxes/<name>
```

Container commands execute through one lazily created workspace container as direct `docker exec` or
`podman exec` argv. Backends add read-only root
filesystems, dropped capabilities, pid and resource limits, isolated environment variables, and offline
networking by default. Strict health requires a rootless runtime, seccomp, an explicit non-root container
UID, and a locally reviewed image that resolves to an immutable digest. Containers receive deterministic
workspace names, audit labels, persistent dependency caches, stale-state reconciliation, and explicit cleanup.

```bash
uv run code-agent containers start --backend docker
uv run code-agent containers status --backend docker
uv run code-agent containers stop --backend docker
uv run code-agent containers stop --backend docker --remove
```

Production policies can additionally require a Trivy vulnerability gate. Enabling shell networking with
a domain allowlist is refused because bridge networking cannot enforce domains without a managed egress
proxy; offline mode remains the secure default. Use `code-agent sandbox health` to verify the complete
boundary before a run.

## Verification And Evals

```bash
uv run code-agent evals
uv run code-agent evals --json
uv run code-agent evals --live --limit 3 --trials 3 --save-report
uv run code-agent eval-reports --dashboard --json
uv run code-agent eval-reports --analytics
```

Offline evals cover file operations, failed-read and failed-verification recovery, dirty worktrees,
patch conflicts, prompt injection, secret exfiltration, verification suppression, shell policy, and
false completion. Live evals call the configured provider and consume tokens.

Before a release candidate:

```bash
uv run code-agent release-smoke
uv run code-agent release-smoke --require-dashboard
```

The smoke gate runs tests, Ruff, strict diagnostics, offline evals, and package build with per-command
timeouts. CI covers Python 3.11-3.13 on Linux plus Python 3.12 on Windows and macOS, and runs live Docker
sandbox security checks on normal pull-request and main-branch events.

## Development

```bash
uv sync --extra dev --extra parsing
uv run ruff check src tests
uv run pytest
uv run code-agent evals
uv build
```

Keep changes focused, inspect before editing, preserve unrelated user work, add regression tests for
behavioral changes, and never commit `.env`, `.code-agent/`, virtual environments, caches, or credentials.

For pull requests, report what changed, how it was tested, known risks, and follow-up work. Use short
feature or fix branches and keep the CLI usable while core behavior evolves.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Install](docs/INSTALL.md)
- [Known Limitations](docs/KNOWN_LIMITATIONS.md)
