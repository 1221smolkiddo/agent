# Agent47

Agent47 is a CLI-first Python coding agent built as a durable execution platform. It combines
repository-aware coding tools, transactional workspace editing, model routing, verification, sandboxed
execution, skills, MCP, and multi-agent workers with an event-sourced runtime that records what happened
and why.

It is alpha software. Agent47 has meaningful safety and recovery controls, but a human must review
generated changes and every approval before using it on important repositories. Read
[Known Limitations](docs/KNOWN_LIMITATIONS.md) before use.

## Why Agent47

Most coding agents are a model loop with tools. Agent47 separates reasoning from durable control:

```text
CLI / interactive terminal / NDJSON
                |
                v
        ExecutionRuntimeHost
                |
                v
commands -> append-only events -> projected execution state
                |
      planning / scheduling / budgets / verification
                |
                v
        workers and transactional adapters
                |
                v
 workspace / shell / Git / MCP / models / extensions
```

Models and workers can propose plans, request effects, and report observations. The execution runtime
owns durable state, evidence, verification decisions, approvals, effect journaling, recovery, and final
acceptance.

## Highlights

- Durable event-sourced executions with SQLite WAL storage, deterministic replay, snapshots, command
  idempotency, execution leases, fencing tokens, compatibility versions, and trace export.
- Versioned DAG planning with dependencies, task lifecycles, policy-based scheduling, parallel workers,
  hierarchical budgets, pause/resume/cancel, and mutation-only replanning.
- Immutable acceptance criteria, evidence, verification decisions, diagnoses, repair decisions, and graph
  lineage. A worker cannot declare its own work complete.
- Runtime-owned effect lifecycle: request, authorization, preparation, dispatch, commit, reconciliation,
  compensation, cancellation, and effect evidence.
- Transactional filesystem, shell, Git, MCP, model, and generic-tool adapters with capability negotiation,
  idempotency, reconciliation, compensation, verification, and timeout contracts.
- Workspace-aware coding: exact edits, patches, search, project graph, Git awareness, LSP integration,
  transaction checkpoints, undo/redo, recovery, and safe revert.
- Automatic test, lint, type-check, build, reviewer, and mutation verification workflows with structured
  diagnostics and evidence-backed completion gates.
- OpenAI-compatible model providers, streaming, profiles, bounded retries, fallback routing, usage records,
  and cost tracking.
- Dynamic tools, lifecycle hooks, scoped instructions, skills, plugins, MCP clients, isolated subagents,
  and dependency-aware multi-agent orchestration APIs.

## Quick Start

Agent47 requires Python 3.11 or newer. `uv` is the recommended environment manager.

```bash
uv sync --extra dev --extra parsing
cp .env.example .env
```

On PowerShell:

```powershell
Copy-Item .env.example .env
```

Configure one provider in `.env`:

```text
AGENT_PROVIDER=openrouter
OPENROUTER_API_KEY=...
AGENT_MODEL=qwen/qwen3-coder
```

Then run diagnostics and an inspection-only task:

```bash
uv run code-agent doctor --strict
uv run code-agent run --dry-run "Inspect this repository and identify the highest-risk defect"
```

For full setup, provider configuration, containers, language servers, and Windows/macOS/Linux notes, see
[Install](docs/INSTALL.md).

## Everyday Use

```bash
# Work on a task
uv run code-agent run "Fix the failing parser tests"

# Plan or inspect without mutation
uv run code-agent run --dry-run "Plan a parser refactor"

# Use a container-backed workspace boundary
uv run code-agent run --sandbox --sandbox-backend docker "Refactor the parser"

# Resume a durable execution
uv run code-agent run "Continue the interrupted task" --execution-id <execution-id>

# Open the interactive terminal
uv run agent47
```

Useful operational commands:

```bash
uv run code-agent models
uv run code-agent history
uv run code-agent resume <run-id> "Continue from failed verification"
uv run code-agent transactions list
uv run code-agent sandbox health --backend docker
uv run code-agent platform inspect
uv run code-agent execution runtime-status
uv run code-agent execution trace <execution-id>
uv run code-agent execution explain <execution-id>
```

Use `code-agent run --help` for model, sandbox, approval, streaming, and execution options.

## Execution Runtime

Every hosted run has a stable execution ID. Its append-only event stream is the source of truth; snapshots
only accelerate recovery.

```text
worker observations
        |
        v
immutable evidence -> VerificationDecision
                         | verified      -> complete
                         | inconclusive  -> bounded repair
                         | failed        -> diagnosis -> RepairDecision -> replan
                         | blocked       -> typed waiting state
```

Completion is derived from runtime invariants: the graph must be complete or validly superseded, active
criteria must have verified decisions, effects must be resolved, approvals must not be pending, and budgets
must be consistent. A model's or worker's success claim is advisory only.

Effects are first-class resources:

```text
requested -> authorized -> prepared -> dispatched -> committed
                                           |             |
                                           -> failed     -> compensated
                                           -> unknown
```

The runtime, not a worker, decides whether an effect is allowed, whether approval is required, which
adapter can execute it, and how ambiguity is reconciled after recovery.

### Runtime Modes

| Mode | Purpose |
| --- | --- |
| `legacy` | Prior loop without an execution host; migration fallback only. |
| `shadow` | Default. The engine independently plans, journals, compares decisions, and records divergence without duplicating mutation. |
| `primary` | Enables only authority stages that have passed their scoped qualification gate. |
| `engine_only` | Available after every promotion gate. The runtime owns authority; workers are replaceable implementations underneath it. |

Promotion is sequential: tracing/projection, planning, scheduling/budgets,
verification/diagnosis/replanning, effects/approvals, recovery/completion, then engine-only.

```bash
uv run code-agent execution runtime-status
uv run code-agent execution shadow-report
uv run code-agent execution promote planning --minimum-samples 100
```

See [Durable Execution Engine](docs/EXECUTION_ENGINE.md) and
[Execution Compatibility](docs/EXECUTION_COMPATIBILITY.md) for the contracts and migration rules.

## Repository Intelligence And Editing

Agent47 builds bounded context from a persistent project graph: files, symbols, imports, calls,
references, tests, configuration, Git changes, and optional language-server data. It supports Python,
JavaScript/TypeScript, Rust, Go, Java, Kotlin, C/C++, C#, Swift, Ruby, and PHP static extraction, with LSP
features where a suitable server is installed.

Workspace edits are journaled. Agent47 supports exact writes and edits, unified patches, moves and deletes,
atomic multi-file transactions, checkpoints, undo/redo, recovery, conservative three-way merge, snapshots,
and revert. It verifies disk state instead of trusting a tool's return string.

## Permissions And Safety

### What Agent47 protects

- Scoped workspace access, path traversal checks, symlink safeguards, sensitive-file refusal, binary and
  large-file protections.
- Secret detection/redaction in logs, reports, stored data, and bounded tool output.
- Explicit approval workflows for risky actions and runtime-owned approvals for sensitive effects.
- Shell classification, safe argv execution, timeouts, cancellation, process-tree cleanup, output limits,
  and network-deny policy.
- Optional copied-workspace and Docker/Podman sandbox modes; container networking is off by default.
- Capability-scoped tools, adapters, skills, plugins, MCP servers, and subagents.
- Untrusted-context handling for repository files, model output, web content, tool output, and memory.

### Important boundaries

Local execution is hardened process control, **not OS-level isolation**. Use `--sandbox` with a healthy
Docker or Podman backend for untrusted project code. Sandboxing, permissions and safety, secret detection,
and approvals reduce risk; they do not eliminate the need for review.

See [Security Review](docs/SECURITY_REVIEW.md) and [Known Limitations](docs/KNOWN_LIMITATIONS.md).

## Models, Skills, MCP, And Multi-Agent Work

Agent47 supports OpenRouter, OpenAI, Gemini, DeepSeek, and NVIDIA NIM through OpenAI-compatible APIs.
Model profiles can separate planner, coder, reviewer, and fast models; fallback routing preserves durable
state and records provider handoffs, usage, and estimated cost.

```bash
uv run code-agent run --preset gemini-flash "Review this repository"
uv run code-agent run --provider nvidia --model z-ai/glm-5.2 "Fix the failing test"
```

## Local Sign-In And BYOK

Agent47 is local-first: Google sign-in is optional and uses the OAuth authorization-code flow with PKCE.
The temporary callback listener is bound to `127.0.0.1` on an available port and is shut down after login.
OAuth tokens and API keys are stored in the operating system credential manager; only non-sensitive profile
metadata is saved locally. Configure a Google Desktop OAuth client with `GOOGLE_CLIENT_ID`, then run:

```bash
agent47 login
agent47 keys add gemini
agent47 whoami
```

Manage keys with `agent47 keys list`, `agent47 keys remove <provider>`, and `agent47 keys test [provider]`.
Supported key providers include OpenAI, Anthropic, Gemini, OpenRouter, Groq, DeepSeek, NVIDIA NIM, and a
generic OpenAI-compatible entry. Agent runtime providers automatically prefer a secure BYOK key over `.env`.

The platform layer adds namespaced/versioned tools, lifecycle hooks, scoped instructions, built-in skills,
workspace skills, plugin manifests, and MCP stdio servers. Built-in skills cover security auditing, test
generation, code review, documentation, dependency analysis, refactoring, performance, and DevOps.
Subagents run with intersected capabilities and bounded token, execution, and time budgets.

Workspace executable extensions are disabled by default. Review them before setting
`AGENT_TRUST_WORKSPACE_EXTENSIONS=true`.

See [Platform Extensions](docs/PLATFORM.md).

## Data, Privacy, And Retention

Agent47 stores run history, execution events, workspace transaction metadata, reports, and project memory
locally in SQLite beneath `.code-agent/` by default. Sensitive values are redacted before writes, but local
storage is not application-level encrypted. Keep `.code-agent/`, `.env`, caches, virtual environments, and
private evaluation reports out of Git.

Model-backed runs send approved context to the configured provider and follow that provider's retention
policy. Web and MCP requests can send data to external services. Review [local and provider data](docs/KNOWN_LIMITATIONS.md#secrets-and-data)
before enabling them on sensitive workspaces.

```bash
uv run code-agent history export <run-id>
uv run code-agent history delete <run-id> --yes
uv run code-agent history prune --keep-last 20 --yes
```

## Verification, Evals, And Releases

Agent47 detects common test, lint, type-check, and build commands for Python, Node, Rust, and Go projects.
It records structured command diagnostics, changed-file and graph-affected checks, reviewer findings, and
criterion-linked evidence.

```bash
# Fast contributor checks
uv run ruff check src tests
uv run pytest

# Offline evaluation and release gate
uv run code-agent evals
uv run code-agent release-smoke
uv build
```

Live evals are opt-in because they consume provider credits:

```bash
uv run code-agent evals --live --limit 3 --trials 3 --save-report
```

Security reports, release smoke output, and live-eval reports should be reviewed before any release.
The full release process and remaining caveats are documented in [Known Limitations](docs/KNOWN_LIMITATIONS.md#release-gate).

## Documentation Map

| Topic | Document |
| --- | --- |
| Installation, providers, sandboxes, language servers | [Install](docs/INSTALL.md) |
| Module boundaries, workspace flow, trust model | [Architecture](docs/ARCHITECTURE.md) |
| Durable execution, evidence, effects, recovery | [Execution Engine](docs/EXECUTION_ENGINE.md) |
| Kernel compatibility and staged authority promotion | [Execution Compatibility](docs/EXECUTION_COMPATIBILITY.md) |
| Dynamic tools, skills, plugins, MCP, subagents | [Platform](docs/PLATFORM.md) |
| Security posture and operating boundaries | [Security Review](docs/SECURITY_REVIEW.md) |
| Product limitations and release gate | [Known Limitations](docs/KNOWN_LIMITATIONS.md) |

## Contributing

Keep changes small, preserve user data, add focused tests, and run the relevant checks before opening a
review. The execution runtime has compatibility and replay guarantees; changes to events, task lifecycle,
effect lifecycle, evidence, or authority boundaries require durable fixtures and migration coverage.

Agent47 is a capable alpha engineering collaborator, not an infallible autonomous authority.
