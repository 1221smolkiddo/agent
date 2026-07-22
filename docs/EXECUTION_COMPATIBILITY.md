# Execution compatibility and kernel freeze

The Agent47 execution kernel is frozen at compatibility version `1`. The freeze applies to event
meaning, command validation, state transitions, graph lineage, effect states, evidence relationships,
approval semantics, lease fencing, and budget accounting.

Every execution permanently records:

```text
execution_id
engine_version
compatibility_version
schema_version
```

`engine_version` identifies the implementation that created the execution. `compatibility_version`
selects its immutable behavioral contract. `schema_version` identifies the serialized event format.
New event schemas may be upcast into the execution's original compatibility contract, but replay must
never silently apply newer behavioral semantics. Compatibility-version fixtures are permanent.

## Stable kernel boundary

The kernel contains only event persistence and replay, command validation, state transitions and
invariants, leases, versioned graphs, effect journaling, evidence, approvals, budgets, and extension
capability negotiation.

The following remain outside the kernel and are replaceable:

- planners and graph-mutation producers;
- critics, diagnosers, and memory retrieval;
- scheduling and verification policies;
- models and model routers;
- filesystem, shell, Git, MCP, network, and model adapters;
- skills, workers, subagents, and orchestration strategies;
- CLI, service, editor, and observability interfaces.

Kernel additions require proof that the behavior cannot be implemented through a versioned extension
point. Security and correctness fixes may change kernel code, but must preserve deterministic replay
or explicitly quarantine an unsafe execution with a documented recovery path.

## Adapter capability contract

Adapters publish a versioned `AdapterCapabilities` descriptor covering effect kinds, permissions,
isolation, idempotency, reconciliation, compensation, verification, cancellation, timeouts, streaming,
concurrency safety, retry safety, durability, resource accounting, and supported execution
compatibility versions.

Guarantees are levels—not booleans: `unsupported`, `best_effort`, `adapter`, `provider`, or `engine`.
The engine negotiates requirements before `prepare()` and rejects an effect when no adapter satisfies
the required contract.

```text
prepare -> execute -> reconcile -> compensate -> verify
```

An unknown effect is reconciled before retry. A committed idempotency key replays its durable outcome
without calling the external system again.

## Migration policy

The default runtime mode is `shadow`. The legacy loop remains authoritative while the engine
independently projects state and compares decisions. Shadow mode never executes a mutating action a
second time; it consumes the authoritative outcome as mirrored evidence. Independent shadow execution
is limited to read-only actions or isolated disposable workspaces.

Authority promotion is sequential:

1. tracing and projections;
2. planning and graph mutation;
3. scheduling and budgets;
4. verification, diagnosis, and replanning;
5. side effects and approvals;
6. recovery and completion;
7. engine-only operation.

Each stage requires enough shadow samples, no critical unexpected divergence, and a divergence rate
below the configured threshold. A structured divergence records execution/task, decision type, both
decisions, relevant state and evidence, severity, expected status, and probable cause.

Promotion samples are scoped to the authority being transferred. Planning promotion counts only
`planning` comparisons; unrelated tool or completion samples cannot satisfy that gate. Later gates use
their corresponding scheduling/budget, verification/diagnosis/replanning, side-effect/approval, and
recovery/completion decision families.

Promotion state and the metrics used to authorize it are durable. Promotion can advance only one stage
at a time through `code-agent execution promote STAGE`; it cannot skip an authority boundary. The host
currently supports primary operation through `scheduling_budgets`: it emits an immutable task-assignment
record, transitions only a dependency-ready task to running, and accounts action and model usage against
the assigned task's hierarchical budget scope. It fails closed for later stages.

Normal CLI runs are composed by `ExecutionRuntimeHost`. Shadow planning is independent of the legacy
plan. Authoritative legacy tool selections are wrapped once by transactional adapters so the event log
can enforce idempotency and ambiguity handling without executing the action twice. Planning-primary
mode is enabled only after its stage gate passes; unsupported authority transfers fail closed.

## Control and execution planes

The control plane creates and inspects executions and manages pause, resume, cancellation, approvals,
and checkpoints. It cannot transition tasks or execute effects. The execution plane owns schedulers,
workers, leases, event advancement, recovery, and checkpoint processing. Duplicate workers are fenced
by the execution lease.

## Mutation-only planners

Initial planning and replanning share the same protocol. A planner emits only versioned mutations:
insert, delete, split, merge, update, or change dependencies. It never replaces a complete graph after
startup. Every mutation names its base version, rationale, and affected subtree and must preserve DAG
validity.

## Deprecation policy

- Public contracts use semantic or explicit compatibility versions.
- Breaking extension API changes require a new API version and migration guide.
- Existing event streams are never rewritten in place.
- Deprecated commands remain replayable for every supported compatibility version.
- New executions stop emitting a deprecated command before its handler can be removed.
- Removal requires permanent replay fixtures proving historical executions remain readable.
