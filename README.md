# Agent47

Agent47 is a CLI-first Python coding agent built around an event-sourced execution runtime. It combines
repository intelligence, structured tool use, transactional editing, criterion-backed verification,
crash recovery, sandboxing, model fallback, dynamic extensions, skills, MCP, and multi-agent
orchestration. The execution kernel, reasoning services, tool adapters, and terminal interfaces are
separate so future clients and workers can reuse the same deterministic runtime contracts.

Agent47 is currently alpha software. It has serious safety and reliability controls, but generated
changes and approved commands still require human review. Read
[Known Limitations](docs/KNOWN_LIMITATIONS.md) before using it on sensitive repositories.

## Current Capabilities

- CLI, interactive terminal, and versioned NDJSON interfaces.
- An append-only SQLite execution log as the source of truth, with commands separated from events,
  deterministic replay, checksummed snapshots, command idempotency, execution leases, fencing tokens,
  persistent execution IDs, compatibility version `1`, and immutable execution traces.
- Versioned execution DAGs with hierarchical tasks, dependencies, lifecycle invariants, scheduling policies,
  risk and cost estimates, retry budgets, graph lineage, mutation-only planning, cancellation, pause, and resume.
- Immutable acceptance criteria, versioned evidence, criterion-level verification, diagnosis records,
  targeted replanning, first-class approvals, hierarchical resource budgets, model routing records,
  execution memory, context compression, self-critique, and background worker APIs.
- A runtime-adoption host that defaults to safe shadow operation: independent deterministic or model-backed
  planning, semantic divergence reports, stage-specific promotion gates, transactional wrapping of the one
  authoritative tool call, startup recovery, and durable run-to-execution resume links.
- Versioned adapter capability contracts and negotiation for effect kinds, permissions, isolation,
  idempotency, reconciliation, compensation, verification, cancellation, timeout support, concurrency,
  retry safety, durability, resource accounting, and execution compatibility.
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
- A dynamic platform with namespaced/versioned tools, aliases, JSON schemas, dependencies, health checks,
  reload/removal, prioritized lifecycle hooks, scoped instructions, task-selected skills, plugin manifests,
  and trusted workspace extension discovery.
- MCP stdio clients with initialization and capability negotiation, tool/resource/prompt discovery, resource
  reads and subscriptions, prompt retrieval, caching, allowlisted tools, bounded requests, reconnect, and
  isolated authentication environment variables.
- Built-in security audit, test generation, review, documentation, dependency, refactoring, performance, and
  DevOps skills, plus planner, researcher, coder, reviewer, tester, security, documentation, performance,
  refactoring, and dependency-analysis agent profiles.
- Isolated subagent contexts with capability intersection, token/execution/time budgets, cancellation,
  failure containment, dependency-aware DAG validation, and parallel ready-task orchestration APIs.
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
uv run code-agent platform inspect
uv run code-agent execution runtime-status
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
uv run code-agent run "Continue the interrupted task" --execution-id <execution-id>
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
code-agent platform
code-agent execution
code-agent history
code-agent resume
code-agent revert
code-agent transactions
code-agent sandbox
code-agent containers
code-agent processes
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
uv run code-agent platform inspect
uv run code-agent execution runtime-status
uv run code-agent execution list
uv run code-agent execution trace <execution-id>
uv run code-agent execution explain <execution-id>
uv run code-agent execution recover-active
uv run code-agent collab review --run-id 12 --strict
```

## Architecture At A Glance

```text
CLI / interactive / NDJSON / future clients
                    |
                    v
          ExecutionRuntimeHost
          |         |          |
          |         |          +--> migration gates and shadow comparison
          |         +-------------> independent planner and graph mutations
          v
 commands -> append-only events -> projected execution state
                    |
                    +--> versioned DAG, criteria, evidence, budgets, approvals
                    +--> trace, snapshots, replay, leases, recovery
                    |
                    v
        hosted CodingAgent / workers / subagents
                    |
                    v
      transactional capability adapters
                    |
                    v
 files / shell / Git / MCP / models / dynamic tools
```

The kernel owns durable facts and invariants. Planners, schedulers, critics, verifiers, models,
adapters, skills, agents, and interfaces remain replaceable extensions. The model proposes reasoning
outputs but cannot directly mutate execution state.

## Durable Execution Runtime

Every hosted run has a stable execution ID and permanently records its engine, compatibility, and event
schema versions. Commands express intent; validated events record facts; projections reconstruct current
state. Snapshots accelerate replay but never replace the event log as the source of truth.

The task lifecycle is validated by the engine:

```text
queued -> ready -> running -> verifying -> verified -> complete
                   |             |
                   |             +-> diagnosing -> replanning -> ready
                   +-> waiting | blocked | failed | cancelled
```

Completed tasks and terminal executions are immutable. Dependencies must be complete before execution,
and a task cannot become verified or complete until each criterion has linked immutable evidence and a
passing verification record. External effects use a durable journal:

```text
pending -> running -> committed | failed | unknown
unknown -> committed | failed | rolled_back
```

An ambiguous effect is reconciled before retry. A committed idempotency key replays its recorded result
without calling the external system again. Normal Agent47 runs now store their execution link in run
history, so `code-agent resume RUN_ID` restores the same event stream and injects a compressed recovery
context. `code-agent run ... --execution-id ID` provides an explicit recovery path.

### Runtime Modes And Promotion

| Mode | Current behavior |
| --- | --- |
| `legacy` | Runs the prior loop without an execution host. Intended only as a migration fallback. |
| `shadow` | Default. The legacy loop remains authoritative while the engine independently plans, projects, journals the single authoritative call, and records divergences. |
| `primary` | Available after the relevant promotion gate. The engine currently owns planning, graph, scheduling, budgets, verification, diagnosis, replanning, and task acceptance; the legacy loop remains the worker and side-effect selector. |
| `engine_only` | Fails closed until every later authority stage is implemented and qualified. |

Authority promotion is deliberately sequential: trace/projection, planning/graph, scheduling/budgets,
verification/diagnosis/replanning, side effects/approvals, recovery/completion, then engine-only operation.
Planning, scheduling/budget, and verification/diagnosis/replanning authority can currently be promoted. Each gate requires enough relevant shadow samples,
zero allowed critical divergences, and a configured maximum divergence rate; unrelated sample types cannot
satisfy a gate.

```bash
uv run code-agent execution runtime-status
uv run code-agent execution shadow-report
uv run code-agent execution promote planning --minimum-samples 100
```

Use `AGENT_SHADOW_PLANNER=deterministic` for offline reproducibility or `model` for an independent
planner-model call. Model planning is budgeted and traced; malformed output, provider failures, and timeouts
fall back to the deterministic planner and remain visible as execution diagnostics.

Execution operations include create, list, show, trace, replay, recover, recover-active, explain,
shadow-report, promotion status, runtime status, pause, resume, cancel, checkpoint, approve, and promote.
`execution explain` reports blockers, unsatisfied criteria, evidence links, graph changes, model decisions,
budget hotspots, and the critical path. Scheduling policy is replaceable independently of mechanics; FIFO,
priority, critical-path, cost-optimized, and verification-first policies are implemented. The reusable
autonomous executor adds bounded parallel workers, risk-triggered human checkpoints, lease heartbeats,
criterion verification, diagnosis, retry-limited subtree repair, adaptive model routing, execution-memory
retrieval, self-critique, and context compression. These components are available to the execution plane,
while normal CLI authority remains limited by the migration stage described above.

See [Durable Execution Engine](docs/EXECUTION_ENGINE.md) and
[Execution Compatibility](docs/EXECUTION_COMPATIBILITY.md).

## Execution Model

The event-sourced runtime and the hosted coding loop currently operate together. The runtime owns durable
identity, plans, event projection, effects, evidence, replay, and migration metrics; the hosted loop still
selects actions and performs its established repository workflow unless planning has been promoted.
Workspace runs follow this explicit lifecycle:

1. Classify the request and build a token-bounded context pack of exact files, symbols, relationships, and tests.
2. Create or update a durable hierarchical plan with dependencies, hypotheses, and acceptance criteria.
3. Execute one validated action at a time.
4. Verify mutations against disk and run selected project checks.
5. Diagnose failures, record evidence, invalidate stale assumptions, and require a revised plan.
6. Reject repeated identical outcomes and unsupported completion claims.
7. Checkpoint execution before later model turns and persist evidence, provider handoffs, confidence, reports,
   effect outcomes, and the durable run-to-execution link.

Plans are execution contracts. Dependencies gate active work; completed steps with unmet `command:`, `evidence:`,
or `file:` criteria become blocked. Failed planned verification requires a new plan revision before mutation,
execution, or finalization. Resumed runs hydrate both the latest loop checkpoint and the canonical durable
execution projection; ambiguous external effects are never silently repeated.

See [Architecture](docs/ARCHITECTURE.md) for module boundaries and detailed data flow.

## Dynamic Platform, Skills, MCP, And Agents

Agent47 composes its extension surface through `PlatformRuntime`; it does not add workspace tools through
another hard-coded switch. Runtime tools are namespaced, versioned, schema-described, permission-scoped,
health-checked, and discoverable. Lifecycle hooks are priority ordered and isolate failures. Repository and
workspace instructions are resolved hierarchically, while skills are selected from task wording and add
workflow, verification, and acceptance guidance without overriding core security policy.

```bash
uv run code-agent platform inspect
uv run code-agent platform inspect --trust-workspace-extensions
```

Workspace extension locations are:

```text
.agents/skills/<name>/SKILL.md
.agents/plugins/<name>/plugin.json
.agents/mcp.json
```

Executable workspace extensions are disabled by default. Set `AGENT_TRUST_WORKSPACE_EXTENSIONS=true` or
pass `--trust-workspace-extensions` only after reviewing plugin manifests and MCP commands. Plugin commands
must resolve inside their plugin directory, execute without a shell, receive a scrubbed environment, declare
permissions and dependencies, and obey a timeout. Unloading a plugin removes its contributed tools, skills,
profiles, and hooks.

MCP servers use stdio JSON-RPC and receive only named authentication environment variables plus a minimal
process environment. `allowed_tools` can restrict discovered tools; registered names are scoped under
`mcp-<server>.*` and still pass dynamic-tool permission checks. Resources, prompts, subscriptions, caching,
bounded reads, and reconnect are supported.

The built-in agent catalog includes planner, researcher, coder, reviewer, tester, security-auditor,
documentation-writer, performance-optimizer, refactoring-expert, and dependency-analyzer profiles.
`SubagentManager` deep-copies JSON context, intersects capabilities, caps token/execution/time budgets,
contains failures, and supports cancellation. `MultiAgentOrchestrator` validates dependency DAGs and can run
independent ready nodes concurrently through an injected worker. These APIs exist in the reusable core; the
normal CLI does not yet delegate every task to subagents automatically.

See [Dynamic Platform](docs/PLATFORM.md) for manifests and configuration.

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
- `AGENT_DB_PATH`, `AGENT_EXECUTION_DB_PATH`, `AGENT_STREAM`, and `AGENT_REVIEWER_PASS`.
- `AGENT_EXECUTION_MODE=legacy|shadow|primary|engine_only` and
  `AGENT_SHADOW_PLANNER=deterministic|model`.
- `AGENT_SHELL_NETWORK`, `AGENT_SANDBOX_BACKEND`, and `AGENT_SANDBOX_IMAGE`.
- `AGENT_TRUST_WORKSPACE_EXTENSIONS` and reusable-container workspace/reuse settings.

## Permissions And Safety

File access, repository inspection, mutations, shell commands, and web search are approval-gated.
JSON mode denies approvals unless an explicit approval mechanism is configured.

Core controls include:

- Event-sourced state transitions with deterministic replay, optimistic sequence checks, command
  idempotency, checksummed snapshots, lease fencing, immutable terminal state, and DAG validation.
- Capability negotiation before transactional effects, durable idempotency keys, unknown-effect quarantine,
  adapter reconciliation before retry, and compensation only where the selected adapter declares support.
- Hierarchical token, dollar, wall-time, CPU-time, tool, shell, network, and retry budgets in the execution
  kernel; child scopes inherit ancestor enforcement and parallel work can reserve capacity.
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

Agent47 is a local single-user CLI. It does not implement authentication, MFA, web sessions, RBAC, SSO,
SCIM, organization policy distribution, multi-tenant isolation, or tamper-proof centralized audit. It is not
SOC 2, ISO 27001, GDPR, or HIPAA certified. The controls here can contribute technical evidence to a broader
program, but they are not a compliance claim.

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
- `executions.db`: append-only execution events, checksummed projection snapshots, leases, compatibility
  metadata, versioned graphs, evidence, effects, approvals, budgets, traces, migration state, shadow samples,
  and structured divergences.
- `memory/project.md`: approval-gated, secret-scanned stable project facts.
- `transactions/`: mutation journals and content-addressed workspace checkpoints used for recovery,
  rollback, undo/redo, and selective restore.
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
- [Durable Execution Engine](docs/EXECUTION_ENGINE.md)
- [Execution Compatibility And Kernel Freeze](docs/EXECUTION_COMPATIBILITY.md)
- [Dynamic Platform, Skills, MCP, Plugins, And Agents](docs/PLATFORM.md)
- [Security Control Review](docs/SECURITY_REVIEW.md)
- [Install](docs/INSTALL.md)
- [Known Limitations](docs/KNOWN_LIMITATIONS.md)
