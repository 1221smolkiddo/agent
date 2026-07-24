# Durable execution engine

Agent47's autonomous runtime is an event-sourced execution engine. The append-only event stream is
canonical; task tables, graphs, evidence ledgers, budgets, and snapshots are projections that can be
discarded and rebuilt.

Kernel compatibility, adapter contracts, migration stages, and the architecture freeze are specified
in [EXECUTION_COMPATIBILITY.md](EXECUTION_COMPATIBILITY.md).

```text
Command -> validation -> lease fencing -> append events -> project state -> schedule work
```

The LLM is a replaceable reasoning service. It may propose plans, diagnoses, graph mutations, model
routes, and repairs, but it cannot directly mutate execution state.

## State machine

Tasks use validated transitions:

```text
queued -> ready -> running -> verifying -> verified -> complete
                   |             |
                   |             -> diagnosing -> replanning -> ready
                   -> waiting | blocked | failed | cancelled
```

Terminal tasks and terminal executions are immutable. Dependencies must be complete before a task
becomes ready or starts. A task cannot become verified or complete until every immutable acceptance
criterion has a passing verification backed by linked evidence.

## Event store and replay

`SQLiteEventStore` provides ordered per-execution streams, optimistic sequence checks, command
idempotency, correlation/causation identifiers, WAL durability, checksummed snapshots, and execution
leases with monotonically increasing fencing tokens. A snapshot is only a replay accelerator. Full
replay and snapshot-plus-tail replay must produce identical canonical projections.

The trace contains goals, graph versions, transitions, tool effects, evidence, verification,
diagnoses, approvals, budgets, model routing, memory, checkpoints, timings, and costs. Use:

```text
code-agent execution create "goal"
code-agent execution list
code-agent execution show EXECUTION_ID
code-agent execution trace EXECUTION_ID
code-agent execution checkpoint EXECUTION_ID
code-agent execution pause EXECUTION_ID
code-agent execution resume EXECUTION_ID
code-agent execution approve EXECUTION_ID APPROVAL_ID
code-agent execution replay EXECUTION_ID
code-agent execution recover EXECUTION_ID
code-agent execution explain EXECUTION_ID
code-agent execution shadow-report [EXECUTION_ID]
code-agent execution promotion-status
code-agent execution runtime-status
code-agent execution promote PLANNING
code-agent execution recover-active
code-agent run "goal" --execution-id EXECUTION_ID
```

## Runtime adoption host

`ExecutionRuntimeHost` is the composition root used by normal coding-agent runs. In `shadow` mode it
creates an independent versioned DAG before the legacy loop starts, journals the single authoritative
tool invocation through negotiated transactional adapters, projects immutable evidence, and compares
legacy planning with the engine plan. It never performs a second mutating action.

The independent planner is selected with `AGENT_SHADOW_PLANNER=deterministic|model`. The deterministic
provider gives offline, reproducible qualification. The model provider uses a separate planner client,
accepts only a structured DAG, accounts its tokens and cost in the execution budget, and falls back to
the deterministic provider on malformed output, provider failure, or timeout. The failure and fallback
remain in the execution trace.

After planning qualification, `AGENT_EXECUTION_MODE=primary` makes the engine graph authoritative while
the legacy loop remains the worker and side-effect selector. After scheduling-and-budget qualification,
the engine also selects exactly one dependency-ready task, records the assignment, and supplies the
worker with that task's bounded context. The worker must stop after its assignment; a later resume gets
the next assignment. Tool, shell, MCP, and model usage are charged to a task scope and its execution
ancestors, so parallel task scopes cannot collectively exceed an execution limit. Primary mode fails
closed before planning promotion and for later authority stages that this host has not adopted.
`engine_only` remains disabled until all authority stages are qualified.

On recovery, the host replays active executions and changes orphaned `running` effects to `unknown`.
When the host resumes an execution, interrupted task lifecycle states are journaled back to a scheduler-owned
ready state without consuming a retry. Resuming with `--execution-id` requires the same goal and never repeats
an ambiguous effect; its adapter must reconcile it or an operator must resolve it. Every legacy run stores a
durable execution link in the run history, so `code-agent resume RUN_ID` restores the same execution
automatically and injects a compressed graph, budget, diagnosis, model-route, and ambiguity summary into the
recovered worker.

## Versioned graphs and replanning

Every graph mutation creates a new version linked to its parent and records its rationale and affected
subtree. Supported mutations include insertion, deletion, task updates, dependency changes, splitting,
and merging. Mutations are rejected when based on stale versions, when they modify terminal tasks, or
when the result is not a valid DAG.

The planner normalizes hierarchical tasks, criteria, priorities, dependencies, risks, retry limits,
cost estimates, and agent assignments. The scheduler is policy-based: FIFO, priority, critical path,
cost optimized, and verification-first policies are available independently of execution mechanics.

## Evidence and verification

Evidence records are immutable and versioned. Supported `kind` values are open so adapters can record
file diffs, terminal output, tests, lint, type checks, benchmarks, screenshots, logs, API responses,
approvals, commits, or future media. Evidence links explicitly to criterion IDs. Verification records
preserve the exact criterion version, evidence IDs, verifier, result, and rationale.

Failed verification enters diagnosis. The diagnoser records classification, root-cause hypothesis,
confidence, source evidence, and repair strategy. Retry transitions consume the task retry budget and
re-enter only the affected task/subtree.

## Authority contracts

The execution engine treats models, workers, verifiers, diagnosers, and replanners as replaceable
components with explicit authority boundaries:

- A worker executes only its assigned contract through authorized runtime tools and emits a strictly
  observational `TaskExecutionResult`: evidence references, artifacts, observed effects, metrics,
  warnings, and an advisory `worker_assessment` (`completed`, `partial`, `blocked`, `failed`, or
  `unknown`). Workers cannot transition tasks, verify criteria, allocate budgets, mutate graphs,
  schedule work, approve effects, or emit lifecycle events.
- Only the runtime transitions lifecycle, accepts evidence, allocates budgets, schedules work, mutates
  graphs, persists authoritative events, and completes an execution.
- A verifier evaluates criteria and emits one immutable `VerificationDecision` per attempt. Decisions
  contain criterion/evidence references, policy, `verified|failed|inconclusive|blocked`, policy-owned
  confidence, concise reasoning summary, latency, cost, and (when blocked) a typed reason.
- A diagnoser consumes verification decisions and recommends repair; it cannot verify, schedule, or
  mutate state. A replanner produces versioned graph mutations and repair tasks but cannot complete work.

No task may enter `complete` unless its active immutable criteria are satisfied by persisted immutable
verification decisions with decision `verified`. A worker's own assessment is never acceptance evidence.
`blocked` carries one of `approval`, `external_service`, `missing_resource`, `dependency`, `lease`,
`budget`, `policy`, `user_input`, or `other`.

When work is not accepted, the runtime creates a diagnosis and an immutable `RepairDecision` linked to
the diagnosis, repair policy, expected criteria, approver, and resulting graph mutation. Original task
criteria are not rewritten: the original task is superseded and a new repair task preserves the lineage.

## Transactional side effects

External actions have a durable effect journal:

```text
pending -> running -> committed | failed | unknown
unknown -> committed | failed | rolled_back
```

Stable idempotency keys prevent duplicate requests. Exactly-once event application is guaranteed;
external exactly-once behavior requires adapter or provider idempotency. If Agent47 crashes after an
external operation but before recording its outcome, recovery marks the effect `unknown`. It cannot be
retried until reconciliation or explicit human authorization resolves the ambiguity.

The existing workspace transaction manager remains the compensation/checkpoint adapter for file
mutations. Shell, MCP, HTTP, Git, and database adapters are journaled through the same effect model.
During runtime adoption, legacy-selected filesystem, shell/process, Git, MCP/network, and general tool
actions already use this journal, but the legacy policy remains authoritative until the side-effects
promotion stage.

## Engine-owned effect lifecycle

Effects are first-class durable execution resources, not unstructured tool calls. A worker may submit
an effect request, but only the runtime may authorize, prepare, dispatch, reconcile, compensate, or
cancel it. The normal lifecycle is:

```text
requested -> authorized -> prepared -> dispatched -> committed
                                           |             |
                                           -> failed     -> compensated
                                           -> unknown
requested|authorized|prepared -> cancelled
```

Before adapter dispatch, the runtime evaluates capability requirements, policy, budgets, isolation,
idempotency, retry safety, and approval resources. Sensitive filesystem, shell, Git, MCP, and network
effects remain `requested` until a durable approval matching the task and effect scope is granted.
Ambiguous dispatched effects are reconciled during runtime recovery; they are never blindly repeated.

`ExecutionCompleted` is derived, not asserted by a worker: the runtime emits it only when the graph is
complete or validly superseded, criteria are verified, no effect is unresolved, no approval is pending,
and budget invariants hold. In `engine_only` mode, legacy worker implementations may remain as replaceable
workers, but no legacy component owns lifecycle, effects, approvals, recovery, or completion.

## Approvals, leases, and budgets

Approvals are durable resources with ID, scope, risk, granting identity, affected tasks, timestamps,
expiration, and revocation. High-risk autonomous tasks stop at a human checkpoint and resume only when
a current approval is present.

An execution lease contains owner, expiration, heartbeat, and fencing token. A stale worker cannot
append advancement events after another worker acquires the execution.

Budgets are hierarchical and support tokens, dollars, wall time, CPU time, tool calls, shell commands,
network requests, and retries. Child scopes inherit ancestor enforcement and support reservation,
consumption, and release so parallel workers cannot collectively overspend.

## Long-horizon intelligence

The autonomous executor combines dependency scheduling, parallel background workers, criterion
verification, diagnosis, bounded retries, human checkpoints, heartbeat renewal, and completion.
Adaptive routing selects model tier, reasoning depth, parallelism, retry budget, and verification level
from risk, confidence, remaining budget, and prior outcomes.

Execution memory stores successful fixes, failed approaches, conventions, preferences, decisions,
verification results, and benchmarks as trace events. Retrieval can search the current execution or
prior executions. Context compression retains the goal, active graph, decisions, evidence-derived
verification, diagnoses, budgets, and completion summaries while discarding conversational chatter.

## Core invariants

- Every event is durable, ordered, immutable, and applied once.
- Every command has a stable idempotency identity.
- Every task belongs to one execution and follows a legal lifecycle transition.
- Completed tasks and terminal executions are immutable.
- Every completed task has verified criteria backed by evidence.
- Every graph version is a valid DAG with preserved lineage.
- The scheduler never starts a dependency-blocked task.
- Budget consumption never exceeds an ancestor limit or reservation.
- A stale lease holder cannot advance an execution.
- Committed effects cannot be committed twice.
- Ambiguous effects require reconciliation.
- Full replay is deterministic and equals snapshot-plus-tail recovery.

The invariant suite includes randomized interruption, cache loss, snapshot restoration, effect
ambiguity, duplicate commands, stale leases, graph mutation failures, budget exhaustion, retries,
parallel scheduling, approval checkpoints, and deterministic replay.
