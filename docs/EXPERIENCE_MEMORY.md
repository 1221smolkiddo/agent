# Historical experience memory

P1 established an optional provider foundation. P2 adds write-only, best-effort retention of
meaningful engineering episodes after a run has finalized. It never recalls or reflects
automatically, creates banks at startup, or probes a server. `factory.create_agent` supplies
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

Malformed settings fail validation with input values hidden. Missing cloud keys or self-hosted URLs
are an operational `missing_config` status rather than startup errors. Keys are `SecretStr` values,
excluded from repr and settings serialization. URLs reject userinfo, queries, and fragments.
Plain HTTP is allowed only for explicit self-hosted loopback without an API key; use HTTPS for
authenticated or remote self-hosted endpoints. Configure endpoints through trusted operator settings.

## Public contracts

`ExperienceMemoryProvider` is a synchronous protocol with `health()`, `recall(bank_id, query)`,
`retain(bank_id, Experience)`, and `reflect(bank_id, query)`. Both the null provider and Hindsight
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
document ID) without episode text, credentials, or SDK exception messages. They cannot
change a run's final result. P2 does not yet journal the outbound operation as a durable
runtime effect, enforce a dedicated network budget or approval gate, reconcile ambiguous
outcomes, handle deletion/retention policy centrally, or qualify live hosted deployments.
Those are required before claiming end-to-end delivery guarantees.
