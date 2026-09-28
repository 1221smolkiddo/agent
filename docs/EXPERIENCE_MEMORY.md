# Historical experience memory

P1 established an optional provider foundation. P2 adds selective retention of
meaningful engineering episodes after a run has finalized. P3 makes delivery durable.
P4 adds one selective, provenance-aware recall before planning. P5 adds a selective
historical Reflect escalation after normal failure diagnosis and recovery. It never
creates banks at startup. Service construction does not probe a server;
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
| `AGENT_EXPERIENCE_MEMORY_AUTOMATIC_REFLECT_ENABLED` | `true` | Enabled only within the overarching memory opt-in; independently disable with `false` |
| `AGENT_EXPERIENCE_MEMORY_AUTOMATIC_REFLECT_MAX_REQUESTS` | `1` | Separate per-run ceiling, `0` or `1`; failures consume the attempt |
| `AGENT_EXPERIENCE_MEMORY_AUTOMATIC_REFLECT_TIMEOUT_SECONDS` | `5` | Deadline including cleanup, capped by provider timeout; positive, at most 15 seconds |
| `AGENT_EXPERIENCE_MEMORY_AUTOMATIC_REFLECT_MAX_TOKENS` | `768` | SDK answer budget and conservative local UTF-8 hypothesis byte ceiling; 128–2048 |
| `AGENT_EXPERIENCE_MEMORY_AUTOMATIC_REFLECT_SOURCE_FACTS_MAX_TOKENS` | `256` | SDK observation-search default budget; 32–1024, with the SDK limitation described below |
| `AGENT_EXPERIENCE_MEMORY_AUTOMATIC_REFLECT_CONTEXT_MAX_CHARS` | `3000` | Entire formatted recovery context ceiling, 500–6000 characters |

Malformed settings fail validation with input values hidden. Missing cloud keys or self-hosted URLs
are an operational `missing_config` status rather than startup errors. Keys are `SecretStr` values,
excluded from repr and settings serialization. URLs reject userinfo, queries, and fragments.
Plain HTTP is allowed only for explicit self-hosted loopback without an API key; use HTTPS for
authenticated or remote self-hosted endpoints. Configure endpoints through trusted operator settings.

## Public contracts

`ExperienceMemoryProvider` is a synchronous protocol with `health()`, `recall(bank_id, query)`,
`recall_detailed(bank_id, RecallRequest)`, `retain(bank_id, Experience)`, and
`reflect(bank_id, query)`, and `reflect_detailed(bank_id, ReflectRequest)`. Both the null provider and Hindsight
provider implement it. `MemoryResult` contains a `MemoryStatus`, normalized `RecalledExperience`
values, optional reflection text, and an immutable `untrusted=True` marker. No SDK classes cross
this boundary. Failure results contain only a fixed status, never SDK exception text or raw responses.

`ExperienceMemoryService(config, workspace, provider=...)` offers explicit `health()`, `scope()`,
`recall(query)`, `retain(summary)`, `reflect(query)`, and typed `reflect_detailed(ReflectRequest)` methods. Provider injection supports other
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

## Security and privacy boundaries

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
Historical memory never becomes recovery state or completion evidence. Live cloud and self-hosted server qualification is available through the opt-in P6 smoke command below; an actual deployment result is not included here.

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
  -> MemorySanitizer -> durable SQLite outbox -> async Hindsight retain
  -> operation reconciliation/polling
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
extraction and indexing finished. The outbox polls and reconciles those operations.

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
startup and poller limits. Operator inspection and bounded manual reconciliation are described below. Live cloud/self-hosted deployment results and provider operation-retention-window behavior remain to be verified before claiming end-to-end exactly-once delivery.

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

## P5 selective Reflect during recovery

The normal diagnoser, repository evidence gathering, recovery instructions, and retry
checks run first. `ReflectPolicy` then decides deterministically whether historical
reasoning may help. No model decides whether to call Reflect. Eligible code failures
escalate for a repeated normalized diagnostic, verification failure after a recorded
repair or replan, or matching diagnostic history from earlier local runs. After a
previous unresolved diagnosis, conflicting outcomes for the same recalled document or
bounded recalled summaries can also trigger escalation. Passing verification clears
recovery tracking but never replenishes the Reflect request budget.

The first ordinary failure, a resolved failure, missing diagnosis, obvious syntax/style
fix, available safe autofix, provider/network/environment/infrastructure failure,
permission denial, cancellation, and disabled configuration skip Reflect. These skipped
failures do not count as ordinary code recovery attempts. Exhausted failure, step, or
run-time budgets cannot gain another recovery step through reflection.

`ReflectRequest`, `ReflectDecision`, `ReflectResult`, `ReflectionHypothesis`, and
`ReflectionSupport` are immutable Agent47 types. One per-run coordinator owns an
independent zero-or-one Reflect budget; recall is neither repeated nor charged for
Reflect. The clean task, normalized diagnostic, last three attempted strategies,
verification outcome, and at most two short recalled summaries become a sanitized JSON
question capped at 1,800 characters. Source declarations/fences, credentials,
environment assignments, oversized dumps, and the interactive transcript wrapper are
removed or rejected before dispatch. No transcript, source file, unrestricted tool
output, environment export, or hidden reasoning is used as the question.

The locked Python SDK 0.10.1 receives one `areflect` call with `budget="low"`,
`max_tokens=768` by default, a bounded response schema, observation/experience fact
types, mental models excluded, entities omitted, and observation-search budget 256.
Fact bodies, tool calls, and tool-call outputs are omitted; SDK retries and applying
all directives are disabled. The schema permits one hypothesis and at most five
120-character fact identifiers with their type. Only sanitized, conservatively bounded
hypothesis text and references cross the adapter. Older servers returning plain text
receive unknown provenance; malformed structured output fails safely.

SDK limits: `reflect_search_observations_max_tokens` is a default for observation search
when the Reflect model does not name its own budget, **not a strict cap on every
internal fact or source chunk**. SDK 0.10.1 has no per-request total supporting-facts/
chunks cap or skepticism/literalism disposition settings. Agent47 requests skeptical,
literal historical reasoning in the question and context and does not mutate bank
dispositions. Strict internal evidence/context limits require trusted server
configuration; Agent47 does not enforce those server internals. Returned references,
hypothesis, and inserted context have strict local limits. See the
[Reflect API](https://hindsight.vectorize.io/developer/api/reflect) and
[server configuration](https://hindsight.vectorize.io/developer/configuration).

A successful hypothesis is appended only to the next worker recovery message as
**UNTRUSTED ADVISORY CONTEXT**, with every line quoted. It cannot execute tools, modify
files, grant permissions, weaken security, skip tests, establish VERIFIED, or complete
a task. Current files, tests, configuration, execution evidence, and local `project.md`
override historical claims. Repairs and replans still use normal tools, approval
callbacks, confidence checks, retry budgets, and lifecycle rules.

References matching the already recalled fact and type reuse `StalenessGuard` and
its provenance checks. Stale support is explicitly labeled stale; unmatched or absent
support is unknown. Even checked support yields only a historical inference requiring
current validation. Model-supplied identifiers are advisory references, not new
verification evidence. No extra retrieval upgrades their authority.

Timeout, missing dependency/configuration, unavailable provider, malformed result, and
sanitizer/staleness failures produce no reflection context and leave normal recovery
running. The consumed request is never retried automatically. The run database records
only `automatic_experience_reflect` attempted flag, fixed reason/status, latency,
supporting-reference count, and formatted context size. Query, hypothesis, raw provider
responses, error text, and credentials are excluded from this telemetry.

Live cloud/self-hosted Reflect and its effect on reasoning remain unqualified. Local
fake-provider and SDK-contract tests establish wiring and authority boundaries, not a
model-quality or deployment-success claim.

## P6 production qualification

### IMPLEMENTED

Architecture and authority:

```text
task -> deterministic recall policy -> bounded Hindsight recall -> provenance/staleness
                                                |                    |
                                                v                    v
                                       advisory planning context   numeric telemetry
verified run -> sanitizer -> SQLite outbox -> async retain -> operation polling
                                    |
                           operator inspection/retry
```

The opt-in Hindsight configuration, cloud and self-hosted URL rules, retention identity,
recall budget, provenance rules, and privacy boundary are described above. The outbox
stores sanitized episode content locally, so protect the SQLite database as repository
operational data. CLI diagnostics deliberately omit content, branch, HEAD, keys, URLs,
provider payloads, and exception messages. Error codes come from a fixed allowlist.
The SDK is imported only when enabled operations are called.

Operators can use:

```sh
agent47 memory status
agent47 memory health
agent47 memory outbox --limit 50
agent47 memory inspect OPERATION_ID
agent47 memory retry OPERATION_ID
```

`status` reports availability, complete state totals, scheduled/due counts, review count, and oldest pending age. The bounded list includes lease expiry, update time, and age for identifying stuck rows.
`health` distinguishes disabled, missing dependency, configuration error, provider
unavailable, and healthy. The outbox view shows prepared (queued), submitted,
processing, completed, failed, cancelled, and unknown rows. An unknown row with
`operation_missing_after_ack`, `ambiguity_window_expired`, or `retry_limit`
requires operator review. `retry` performs one provider lookup with the stored
operation ID before any permissible resend. It refuses terminal, review-only, and
exhausted rows. It never creates a fresh operation ID. A currently leased row cannot
be claimed. There is no delete or purge command.

A live deployment can be checked explicitly, outside normal CI:

```sh
agent47 memory live-smoke --bank-id agent47-repo-<64 hex characters> --timeout-seconds 60
```

Configure `AGENT_EXPERIENCE_MEMORY_ENABLED=true` and either cloud
(`HINDSIGHT_API_KEY`, optional HTTPS `HINDSIGHT_BASE_URL`) or self-hosted
(`AGENT_EXPERIENCE_MEMORY_DEPLOYMENT=self_hosted` and `HINDSIGHT_BASE_URL`).
Use an **existing** bank. The command checks health, retains a unique synthetic
episode, polls the same operation ID to completion within the deadline, recalls its
marker, and requires matching document and repository-bank provenance. The timeout bounds the whole sequence, including individual SDK deadlines. It does not remove the synthetic
memory because this integration has no qualified safe deletion path. JSON output
contains IDs and stage/status only. Normal unit tests use fakes and require no key.

The A/B harness is `agent47 memory evaluate`. Give it a JSON manifest with a
`scenarios` array. Each item has `name`, `task`, and a relative `fixture`
directory inside the manifest tree. Supported names are `repeated_bug_class`,
`recurring_ci_failure`, `rejected_approach_later`, `multi_session_migration`,
`release_rollback_lesson`, `architecture_decision_recall`, `simple_rename`,
and `isolated_simple_bug`. The last two are negative controls. Example:

```json
{"scenarios":[{"name":"repeated_bug_class","task":"Fix the recurring parser bug","fixture":"fixtures/parser"}]}
```

```sh
agent47 memory evaluate --manifest cases.json --runner "python path/to/instrumented_runner.py" --output results.json
```

The harness copies each fixture into a fresh temporary workspace for A
(`AGENT_EXPERIENCE_MEMORY_ENABLED=false`) and B (selective recall enabled),
alternates arm order by scenario, and sets `AGENT47_EVAL_TASK`,
`AGENT47_EVAL_SCENARIO`, `AGENT47_EVAL_ARM`, and
`AGENT47_EVAL_METRICS`. The runner must execute the task and write a JSON object
to the metrics path. It may use actual Agent47 run telemetry and verification
evidence. The harness records verified completion, first-pass verification, steps
to verified solution, model/tool calls, repository reads, verification commands,
repeated failed strategies, recall latency, memory context bytes, stale/irrelevant
recalls, total latency, and token counts when available. Missing observations stay
`null`; runner stdout/stderr and task content are not copied into the report.
The human report gives paired completion and errors without a superiority claim.
P5 supplies Reflect, but this harness currently supports A/B only. A C evaluation arm remains a qualification extension.

### FUTURE / ROADMAP

Run the manifest against independently reviewed, representative repository fixtures
with real model calls, multiple trials, fixed model/settings, and seeded historical
memories. Publish the resulting JSON and review quality as well as costs before
claiming memory improves outcomes. Qualify actual cloud and self-hosted deployments,
including operation-retention-window behavior. The smoke utility and harness do not
by themselves establish exactly-once delivery or a performance benefit.

### Offline smoke and performance commands

```sh
agent47 memory evaluate-smoke --output memory-smoke.json
agent47 memory benchmark --output memory-benchmark.json --iterations 10000 --samples 7
```

The offline smoke builds eight small Git repositories and runs the real CodingAgent,
durable runtime, file tools, and pytest verification in both arms. Historical lessons
are explicitly synthetic seeded fixtures. Both arms use identical scripted model
responses, so this measures wiring rather than memory's effect on reasoning. The
migration fixture represents continuation against a seeded earlier-session lesson;
it is not a measured long-running real migration. The runner counts actual model
calls and tool dispatches; repository reads count read/search/list/map dispatches,
not every internal filesystem read. Steps count model decisions through verified
finalization. Verification commands count recorded verification runs. Repeated failed
strategies count repeated failing action signatures; a real semantic strategy rubric
must be supplied by a reviewed external runner. Irrelevance has known fixture labels;
a live evaluation requires an independent relevance review. Missing token counts stay
null. The harness also records recall request count.

Use `--trials N` with the external evaluator to repeat pairs. Both arms explicitly
disable automatic Reflect, and each gets a separate local run database. Fixture
`.env` files are excluded. The external runner must provide reviewed, frozen memory
seeds and fixed model settings; shared provider-bank writes can otherwise contaminate
later trials. Credentials can be inherited for opt-in live runners but are never
persisted in evaluation reports. JSON and a sibling Markdown report are saved.

The disabled benchmark trips on socket connections/DNS and optional SDK imports. It
times disabled service construction and deterministic recall decisions and records
fresh-process CLI import samples, alternating order. Its baseline removes only P6's
CLI import/registration from the current source. It declares no acceptance threshold.
Timing variation is reported directly rather than converted into a production pass.
The negative controls record the existing P4 policy: local rename skips recall, while
an isolated bug matches the broad repair rule and can trigger an irrelevant recall.
No new classification model call is introduced.

The [P5 report](qualification/P5_REPORT.md) and [P6 report](qualification/P6_REPORT.md)
are historical checkpoint observations, including the policy behavior measured then. See them for
release checks. Live cloud/self-hosted results remain unqualified until the opt-in
smoke command succeeds against a real deployment.
