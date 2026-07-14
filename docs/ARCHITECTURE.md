# Architecture

Agent47 is a reusable synchronous agent core with three interfaces: one-shot CLI, interactive terminal,
and versioned NDJSON. Interfaces construct the same model, policy, tool, storage, and execution components.

## Runtime Flow

```text
user task
  -> CLI / interactive / NDJSON
  -> Settings and model selection
  -> CodingAgent
  -> repository context preflight
  -> model action
  -> schema validation
  -> execution-state and loop checks
  -> permission and security policy
  -> tool execution
  -> mutation and verification evidence
  -> recovery, reviewer, or finalization
  -> SQLite state and work report
```

The model does not receive unrestricted shell or filesystem access. It returns one JSON action matching
the discriminated Pydantic action schema. The agent validates the action, applies runtime policy, executes
the corresponding tool, and returns a bounded result as untrusted context.

## Execution State

`execution_state.py` implements the durable autonomy contract:

- Phases: discover, plan, execute, verify, recover, and finalize.
- Current step and maximum-step budget.
- Plan steps and acceptance checks.
- Changed paths and workspace generation.
- Failed hypotheses and verification confidence.
- Action count and context-compaction statistics.

Actions are fingerprinted from their validated payload and workspace generation. If the same action
produces the same outcome twice without a workspace change, the third execution is blocked and the model
must gather different evidence, change strategy, update the plan, or report a blocker.

For non-trivial work, plan steps are enforceable. A success claim is rejected while plan steps remain
unfinished or planned verification commands lack passing evidence.

## Context Engine

Workspace tasks begin with bounded automatic context:

- Per-repository memory.
- Repository map and ranked files.
- Optional symbol and dependency indexes for relevant tasks.
- Git status and diff awareness when requested.

The repository index is cached in SQLite using path, kind, size, modification time, SHA-256, module,
symbols, and imports. Unchanged files reuse cached metadata.

Conversation context uses a configurable character budget. When history exceeds the budget, Agent47
preserves the system prompt, original task, recent evidence, and a deterministic summary of omitted
actions, failures, and changed paths. It does not ask a model to summarize its own control history.

## Agent Loop

`agent.py` owns orchestration and evidence collection:

1. Create the run and execution state.
2. Classify workspace versus general conversation.
3. Gather automatic context for workspace work.
4. Request one model action.
5. Parse and validate the action.
6. Enforce non-workspace, plan, loop, permission, and security constraints.
7. Execute the tool and record elapsed time.
8. Verify mutations against current disk state.
9. Select automatic verification commands for changed paths.
10. Diagnose failures and provide action-specific recovery guidance.
11. Reject premature or unsupported final claims.
12. Optionally run a separate reviewer model before finalization.

Failure budgets are consecutive, not global: successful evidence resets the tool failure counter. Repeated
unchanged outcomes remain tracked separately by execution state.

## Tool Boundary

`tools.py` implements file, patch, repository, memory, verification, shell, and web actions. File tools
resolve paths inside the workspace and reject sensitive files. Large outputs and diffs are bounded.

Local commands pass through:

1. Command classification.
2. Sandbox policy and workspace-path checks.
3. Explicit approval.
4. Secret-scrubbed environment construction.
5. Shell-free argv execution under `ProcessSupervisor`.
6. Timeout, cancellation, process-tree cleanup, output redaction, and disk-budget checks.

Python verification commands are pinned to the active interpreter so Windows executable lookup cannot
escape the managed environment.

Container execution supports Docker and Podman with offline networking by default, read-only root filesystems,
resource and pid limits, dropped capabilities, isolated environment variables, and image policy validation.

## Permissions And Trust

The permission layer supports manual approval and controlled auto-approval modes. High-risk subprocess
actions remain manual. NDJSON approvals are request-ID matched and fail closed.

Trust boundaries:

- User task: trusted as requested intent, not as permission to bypass policy.
- Model output: untrusted until parsed and validated.
- Repository files, diffs, command output, search results, web pages, and memory: untrusted context.
- Workspace: normal file-operation boundary.
- Local subprocess backend: policy boundary, not OS isolation.
- Container backend: stronger process and network boundary when runtime and image policy are healthy.

## Models

`models.py`, `model_registry.py`, `model_profiles.py`, and `model_presets.py` provide an OpenAI-compatible
provider interface with streaming, usage records, cost estimation, bounded transient retries, credit-aware
token reduction, error classification, and cross-provider fallback.

Fallbackable failures include rate limits, capacity, credits, timeout, connection, server, and empty
responses. Authentication, model-not-found, and malformed-request failures stop early.

Planner, coder, reviewer, and fast profiles can select different model IDs while retaining a shared tool
and policy core. The reviewer receives mutation, command, and verification evidence rather than unrestricted
conversation state.

## Verification

`verification.py` detects checks from Python, Node, Rust, and Go project metadata. Selection depends on
changed paths and can include lint, typecheck, tests, and build. Automatic checks stop at the first failure
and attach compact diagnostics with likely files and focused rerun commands.

Mutation trust is independent of model claims:

- Writes must leave expected content on disk.
- Edits and patches must produce a content change.
- Deletes must remove a previously existing file.
- Final test/build/lint claims must agree with the latest evidence for that purpose.

## Persistence

`storage.py` uses SQLite with migrations, WAL mode, foreign keys, busy timeouts, redaction-before-write,
and restrictive database permissions on Unix. Stored records include runs, steps, model usage, work reports,
repository index data, execution-state checkpoints, and verification evidence.

History can be inspected, exported, deleted, or pruned. Resume reconstructs compact prior evidence and
durable execution state. Revert uses recorded before/after hashes and inverse patches to avoid overwriting
newer user changes.

## Interfaces

- `cli.py`: Typer commands for runs, diagnostics, evals, history, resume, revert, sandboxes, and collaboration.
- `interactive.py`: prompt-toolkit session, streaming status, steering, model selection, and interruption.
- `protocol.py`: versioned NDJSON events and fail-closed approval exchange for frontend integrations.
- `terminal_ui.py`: Rich rendering only; it does not own agent decisions.

## Evaluation And Release

`evals.py` provides deterministic safety and coding fixtures plus opt-in live-model tasks. Reports retain
case traces, commands, mutations, verification, model usage, failure categories, and regressions.

`release_smoke.py` runs bounded tests, lint, strict doctor checks, offline evals, and package build. CI uses
a cross-platform Python matrix and a live Docker security job.

## Module Map

| Area | Primary modules |
| --- | --- |
| Orchestration | `agent.py`, `execution_state.py`, `factory.py` |
| Actions and prompts | `schema.py`, `prompts.py`, `protocol.py` |
| Tools and safety | `tools.py`, `permissions.py`, `safety.py`, `processes.py` |
| Sandboxes | `sandbox.py`, `sandbox_security.py` |
| Repository context | `repo_index.py`, `parsing.py`, `memory.py` |
| Verification and review | `verification.py`, `verification_diagnostics.py`, `reviewer.py` |
| Models | `models.py`, `model_registry.py`, `model_profiles.py`, `model_presets.py` |
| Persistence and recovery | `storage.py`, `session.py`, `resume.py`, `revert.py`, `work_report.py` |
| Product interfaces | `cli.py`, `interactive.py`, `terminal_ui.py`, `status.py` |
| Quality gates | `evals.py`, `eval_reports.py`, `release_smoke.py`, `doctor.py` |

## Design Constraints

- Keep policy decisions in the reusable core, not terminal rendering.
- Preserve user changes and verify disk state rather than trusting tool return strings.
- Keep all external or repository-derived content marked as untrusted.
- Prefer deterministic state summaries and evidence over model-authored bookkeeping.
- Fail closed on malformed actions, approvals, unsafe paths, and unsupported success claims.
- Keep the CLI first-class even if additional clients are added later.
