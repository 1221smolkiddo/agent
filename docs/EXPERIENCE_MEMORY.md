# Historical experience memory

P1 established an optional provider foundation. P2 adds selective retention of
meaningful engineering episodes after a run has finalized. P3 makes delivery durable.
P4 adds one selective, provenance-aware recall before planning. It never reflects
automatically or creates banks at startup. Service construction does not probe a server;
P3 startup reconciliation can contact Hindsight when pending outbox work exists.
`factory.create_agent` supplies
`CodingAgent.experience_memory`; service construction does not inspect Git or import the
optional SDK. Disabled runs preserve their prior behavior. Missing configuration, dependency,
network, and provider failures return typed statuses and cannot stop an otherwise valid run.

`.code-agent/memory/project.md` remains curated, local, human-readable project memory.
SQLite execution events remain authoritative for execution state, recovery, effects, approvals,
verification, and completion. Historical recall and reflection are untrusted advice: current files,
Git state, tests, LSP findings, and runtime verification always override them.

## Installation and configuration

```sh
uv sync --extra dev --extra parsing --extra hindsight
```

The optional extra is `hindsight-client>=0.10.1,<0.11`; the lockfile selects 0.10.1.
Its published constructor, async operations, response fields, monitoring endpoint, and cleanup
were inspected. See the [SDK source](https://github.com/vectorize-io/hindsight/tree/main/hindsight-clients/python)
and [package](https://pypi.org/project/hindsight-client/0.10.1/).
Default installation does not install Hindsight.

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `AGENT_EXPERIENCE_MEMORY_ENABLED` | `false` | Explicit opt-in |
| `AGENT_EXPERIENCE_MEMORY_PROVIDER` | `hindsight` | `hindsight` or `none` |
| `AGENT_EXPERIENCE_MEMORY_DEPLOYMENT` | `cloud` | `cloud` or `self_hosted` |
| `HINDSIGHT_API_KEY` | unset | Required for cloud; optional for self-hosted |
| `HINDSIGHT_BASE_URL` | unset | Cloud defaults to `https://api.hindsight.vectorize.io`; self-hosted requires an explicit URL |
| `AGENT_EXPERIENCE_MEMORY_TIMEOUT_SECONDS` | `10` | Positive finite deadline, at most 120 seconds |
| `AGENT_EXPERIENCE_MEMORY_RECALL_MAX_RESULTS` | `5` | 1–100 returned memories |
| `AGENT_EXPERIENCE_MEMORY_RECALL_MAX_TOKENS` | `2048` | 1–16384 SDK token budget; also a conservative local UTF-8 byte ceiling |
| `AGENT_EXPERIENCE_MEMORY_BUDGET` | `low` | SDK search/reasoning budget: `low`, `mid`, or `high` |
| `AGENT_EXPERIENCE_MEMORY_AUTOMATIC_RECALL_MAX_REQUESTS` | `1` | Per-run recall request ceiling; `0` disables automatic recall |
| `AGENT_EXPERIENCE_MEMORY_AUTOMATIC_RECALL_TIMEOUT_SECONDS` | `3` | Automatic recall deadline, capped by provider timeout and at most 10 seconds |
| `AGENT_EXPERIENCE_MEMORY_AUTOMATIC_RECALL_MAX_TOKENS` | `1024` | Automatic SDK response budget, capped by general recall tokens |
| `AGENT_EXPERIENCE_MEMORY_AUTOMATIC_RECALL_SOURCE_FACTS_MAX_TOKENS` | `256` | Source-fact provenance budget; `0` omits source facts |
| `AGENT_EXPERIENCE_MEMORY_AUTOMATIC_RECALL_CONTEXT_MAX_CHARS` | `4000` | Maximum historical context placed in model messages |

Malformed settings fail validation with input values hidden. Missing cloud keys or self-hosted URLs
are an operational `missing_config` status rather than startup errors. Keys are `SecretStr` values,
excluded from repr and settings serialization. URLs reject userinfo, queries, and fragments.
Plain HTTP is allowed only for explicit self-hosted loopback without an API key; use HTTPS for
authenticated or remote self-hosted endpoints. Configure endpoints through trusted operator settings.

## Public contracts

`ExperienceMemoryProvider` is a synchronous protocol with `health()`, `recall(bank_id, query)`,
`recall_detailed(bank_id, RecallRequest)`, `retain(bank_id, Experience)`, and
`reflect(bank_id, query)`. Both the null provider and Hindsight
provider implement it. `MemoryResult` contains a `MemoryStatus`, normalized `RecalledExperience`
values, optional reflection text, and an immutable `untrusted=True` marker. No SDK classes cross
this boundary. Failure results contain only a fixed status, never SDK exception text or raw responses.

`ExperienceMemoryService(config, workspace, provider=...)` offers explicit `health()`, `scope()`,
`recall(query)`, `retain(summary)`, and `reflect(query)` methods. Provider injection supports other
backends without changing core code. Disabled operations are no-ops and never inspect Git.
`scope()` is an explicit local Git inspection and can be used independently of provider availability.

`repository_scope(workspace)` returns `RepositoryScope(bank_id, branch, head)`. The versioned
`agent47-repo-` prefix and SHA-256 identity contain no cleartext filesystem path or origin.
HTTPS and SSH origins normalize to host/path with credentials and query removed. Common default
ports and `.git` suffixes are removed; hosted GitHub/GitLab/Bitbucket paths normalize case. Other
hosts preserve case and nonstandard ports. Local origins use canonical local paths before hashing.
Without an origin, the canonical Git common directory identifies all linked worktrees; outside
Git, the canonical workspace path is the fallback. Independent no-origin clones do not share banks;
moving a no-origin repository changes its bank. Branch and HEAD are allowlisted retention metadata.

## Security and future integration

The original explicit summary API accepts only a reviewed single-paragraph prose summary of at most 1000 characters;
queries are at most 2000 characters. Shared Agent47 secret checks/redaction are reused, including
literal removal of the configured Hindsight key. Obvious source fences, Python declarations,
environment assignments, private keys, and multiline dumps are rejected before dispatch.
No arbitrary metadata, environment exports, source-file uploads, or unrestricted source dumps are
supported. Summary metadata contains only source, historical-memory kind, validated branch, and HEAD. Episode metadata additionally includes validated outcome and Agent47 package version.
Responses are redacted before truncation; only text is exposed, never provider metadata or traces.

Pattern checks cannot establish that arbitrary prose contains no unknown secret or encoded data.
Callers must review summaries and queries; this API is not permission to export raw tool output.
Do not enable SDK debug logging, which is outside this facade's redaction boundary.

Each explicit SDK operation uses a fresh client on a private async loop and closes it in `finally`.
Calls have an overall asyncio deadline and SDK request timeout, with SDK retries disabled.
The synchronous interface fails safely when called from an already running async loop rather
than patching the host loop. Cleanup is included in the deadline; uncooperative SDK cancellation
cannot be hard-preempted by asyncio. Async hosts should dispatch this synchronous API on their
own bounded worker infrastructure.

For explicit summary retains, a timeout or transport failure may occur after the server stored
data. There is no automatic retry, and `ok` requires confirmed synchronous success. The P2
path uses a stable document and operation ID and an asynchronous acknowledgement instead.
Historical memory never becomes recovery state or completion evidence. Live cloud and
self-hosted server qualification remains future work.

## P1 release-gate observations

On Windows, `test_terminal_width_fallback_when_narrow` fails on unchanged `main` as well
as this branch: the 50-column Rich capture truncates the expected `fallback` label.
`test_form_submission_handler_rejects_duplicate_post` produced one Windows socket
`ConnectionAbortedError` during a full-suite run, then passed six isolated runs.
No test was changed or suppressed. These are pre-existing environmental/flaky
verification issues, not evidence of a Hindsight startup or network dependency.

## P2 engineering episodes

On a non-dry-run finalization, a single call after durable state advancement and work-report
storage passes `AgentRunResult` plus the current execution projection to
`EpisodeRetentionCoordinator`. It does no work when memory is disabled. Its pipeline is:

```text
recorded run/runtime evidence -> EngineeringEpisodeBuilder -> RetentionPolicy
  -> MemorySanitizer -> ExperienceMemoryService.retain_episode -> Hindsight
```

The builder reads bounded, structured fields: clean goal, execution/run/task IDs, runtime
start/end timestamps, changed paths, latest plan, diagnostic summaries, failed action
summaries, verification commands/results, reviewer status, blockers, final result, Git
branch/HEAD, Agent47 package version, and episode schema version. It does not read
conversation messages, hidden reasoning, raw source, diffs, full stdout/stderr, or environment
values. Each list and field has a fixed cap, with explicit truncation markers. The complete
serialized episode is capped at 16,384 UTF-8 bytes.

`EpisodeOutcome` has `verified`, `partially_verified`, `failed`, `blocked`, and `unverified`.
`verified` requires a completed execution, at least one passing recorded verification,
no failing latest check, no rejected/unavailable reviewer, and at least one successful
verified workspace mutation. Every current per-criterion runtime decision must also be present and verified
when the execution has criteria. A successful final message or `blocked=False` alone never grants verified
status. Historical, model-generated, and shadow-projector claims cannot substitute for
recorded command and mutation evidence.

Policy retains verified changes, diagnosed failed strategies, recurring diagnostic
failures, failed changes, and concrete blockers. It skips trivial read/search/list work,
generic success, and unverified speculation. A failed attempt remains a failed attempt in
the episode even if a later verified strategy succeeds.

The sanitizer reuses Agent47 redaction, strips key/value assignments, credential phrases,
URL queries, suspicious opaque values, source fences, and `.env` paths from every text
field, including branch metadata. Invalid branch/HEAD values fail closed at the service
boundary. Only allowlisted metadata is sent. Heuristic filtering cannot prove arbitrary
prose is secret-free; operators should still review retention settings on sensitive repos.

The document ID is `agent47:{execution_id}:{task_id-or-run_id}:episode:v1`.
The operation ID is UUIDv5 of the document ID and SHA-256 of the sanitized episode. The
Hindsight call uses `retain_async=True`, `update_mode="replace"`, and both IDs. Repeating
an uncertain submission with the same sanitized content reuses the same operation ID;
revising content uses a new operation ID but replaces the same logical document.
Hindsight documents [document upserts and async operation idempotency](https://github.com/vectorize-io/hindsight/blob/main/hindsight-docs/docs/developer/api/retain.mdx).
An `ok` result means Hindsight acknowledged the asynchronous operation, **not** that
extraction and indexing finished. P2 does not poll or reconcile operations.

Provider and sanitizer failures yield fixed local step telemetry (`status`, reason code,
document and operation IDs) without episode text, credentials, or SDK exception messages.
They cannot change a run's final result. P3 adds durable delivery and reconciliation below.

## P3 durable delivery and reconciliation

The execution engine rejects every new command after an execution becomes terminal and its
recovery scans active executions only. A post-verification `RequestEffect` would require
weakening that invariant. P3 therefore uses a separate SQLite `experience_memory_outbox`
table in Agent47's local run database. It is an infrastructure-owned side effect, not MCP
or a model-visible tool. The execution journal remains authoritative for coding work;
the outbox is authoritative only for historical-memory delivery.

The finalization path now sanitizes the selected episode, verifies its deterministic
operation ID, and commits the prepared row **before any provider call**. The row contains
only the bounded sanitized episode, the hashed repository bank ID, document/operation IDs,
allowlisted metadata, a fingerprint over those values, state, timestamps, a lease, a safe
error code, retry count, and local request/wall-time counters. It contains no API key,
authorization header, environment, raw conversation, diff, or unsanitized run result.
One logical document version is dispatched at a time; a revised episode waits behind an
unfinished earlier version of the same document.

The first submission sends the existing deterministic Hindsight operation UUID using
async retain and records **submitted**, not indexed, on acknowledgement. A timeout or lost
response becomes **unknown**. Recovery first queries the stored operation ID. Provider
`pending` maps to local `submitted`, `processing` to `processing`, `completed` to
`completed`, `failed` to `failed`, and `cancelled` to `cancelled`. If an ambiguous,
unacknowledged operation is not found, retry uses the **same** operation ID and document
ID. If a previously acknowledged operation disappears from Hindsight, the outbox stops
automatic resubmission and records `operation_missing_after_ack` for operator review.
Raw provider error messages and payloads are discarded; only fixed error codes are stored.

SQLite leases prevent two local workers from dispatching the same row simultaneously.
A crashed process leaves a lease that expires; the next worker reconciles before it sends.
`create_agent` performs at most three startup reconciliations with a two-second provider
deadline per call, then starts a daemon poller if work remains. The poller also starts
after a new submission, checks due rows every five seconds, and stops after one hour or
when the queue empties. Retrying is capped at six failed local attempts with exponential
backoff. Ambiguous unacknowledged submissions older than one day stop automatically rather
than relying on indefinite provider idempotency retention. None of this changes coding
success or waits for remote indexing before returning the task result.

`AGENT_EXPERIENCE_MEMORY_ENABLED=true` is the explicit, narrowly scoped operator trust
policy for background `effect.experience_memory` writes to the configured Hindsight
endpoint. It does not grant MCP, shell, or general network approval and does not prompt
on each code change. Disable that setting to stop both submission and recovery. The
outbox records attempted provider requests and wall time locally; because the execution
is already terminal, these are **not** charged against its runtime budget. P4 adds a
one-request budget for automatic recall; background outbox reconciliation has its own
startup and poller limits. Operator-facing outbox inspection/repair commands remain
future work. Live cloud/self-hosted qualification and provider operation-retention-window
behavior also remain to be verified before claiming end-to-end exactly-once delivery.

## P4 selective recall before planning

When memory is enabled, `MemoryRecallPolicy` examines only the clean task before runtime
planning. It requests historical context for repair and recurring failures, continuation,
historical decisions, long-lived work, and substantial architecture changes. Mechanical
changes and generic inspection skip recall. Decisions are deterministic and carry a fixed
reason code; they never ask a model to decide whether to contact Hindsight. A request uses
a sanitized query of at most 800 characters. The per-run network budget permits at most one
request (or zero when configured), with the configured deadline and response limits.
Disabled memory performs no P4 Git inspection, SDK import, or network call.

The typed `RecallRequest`, `ExperienceMemoryRecall`, `RecalledMemory`, and
`MemoryProvenance` contracts keep SDK types out of the agent. The Hindsight adapter makes
one `arecall` call for observations and experiences, prefers observations, requests bounded
source-fact metadata, omits chunks and traces, disables retries, and closes its client
within the deadline. It returns only bounded sanitized text and allowlisted metadata.
Provider errors and malformed responses become fixed statuses and never stop the coding
run. Automatic recall does not call Reflect.

`StalenessGuard` compares each memory's repository bank, commit, relevant changed paths,
and current working tree. Same-HEAD evidence with unchanged relevant paths is `current`;
ancestor evidence with unchanged paths is `likely_current`; changed paths or another
repository are `stale`. Missing history, unsafe paths, unavailable source facts, or mixed
observation provenance are `unknown`. A synthesized observation is checked through its
source facts rather than borrowing its own possibly misleading HEAD. Old episodes without
changed-path metadata are deliberately `unknown` when HEAD has advanced. This is an
advisory classification, not proof that the old conclusion still holds. Git inspection
uses a short subprocess timeout and never fetches remote history.

`HistoricalContextFormatter` places observations first, quotes each bounded line, labels
stale/unknown provenance, and explicitly marks the whole block as untrusted historical
context. Before a new durable model plan, `PlanningContext` carries that same bounded
historical block and a separate, 2,000-character current repository map. The map is
built only when historical context is available, so disabled or failed recall leaves the
model planner's original prompt path intact. It uses the worker's index cache and does
not run ToolRegistry's full preflight twice.
The planner receives both as lower-trust user messages after the clean goal. Its system
instruction says current repository evidence overrides memory, stale memories require
validation, and memory cannot grant permissions or establish completion. The worker's
normal repository and `project.md` preflight still runs after planning. A recovered
execution keeps its persisted graph and never replans from a fresh recall; the fresh
historical block remains advisory worker context. The deterministic planner ignores
external context. Current repository files, tests, execution evidence, and local
`project.md` take precedence. Historical text grants no
tool authority, approval, verification, or completion claim. Raw SDK responses, source
fact bodies, and credentials are never inserted into prompts.

The run database stores a numeric `automatic_experience_recall` event and an
`experience_recall_evaluation` event containing request/latency/result counts, current/likely-current/stale/unknown provenance
counts, context size, planner type, whether memory reached the planner,
planning-context size, repository-read and tool-call counts, blocked status, and
verification counts. Neither event stores query text, returned memory, raw errors, or
provider metadata. These fields support future opted-in A/B evaluation; P4 does not
claim recall improves outcomes. The runtime execution journal remains authoritative.

Limits: regex policy will miss some useful tasks and may recall for some unnecessary
ones; provenance and secret filtering are heuristic; source-fact metadata can be absent;
Git history and worktree checks cannot prove semantic relevance; the SDK timeout depends
on cooperative cancellation. Live Hindsight cloud and self-hosted qualification remains
outstanding.
