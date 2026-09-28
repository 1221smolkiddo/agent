# Historical experience memory (P1)

P1 establishes an optional provider foundation. It does not automatically recall, retain, reflect,
create banks, or probe a server. `factory.create_agent` supplies `CodingAgent.experience_memory`,
but no agent lifecycle or extension hook invokes it. Service construction does not inspect Git or
import the optional SDK. Missing configuration, dependency, network, and provider failures return
typed statuses and cannot stop an otherwise valid run.

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

Retention accepts only a reviewed single-paragraph prose summary of at most 1000 characters;
queries are at most 2000 characters. Shared Agent47 secret checks/redaction are reused, including
literal removal of the configured Hindsight key. Obvious source fences, Python declarations,
environment assignments, private keys, and multiline dumps are rejected before dispatch.
No arbitrary metadata, environment exports, source-file uploads, or unrestricted source dumps are
supported. Metadata contains only source, historical-memory kind, validated branch, and HEAD.
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

A retain timeout or transport failure may occur after the server stored data. There is no automatic
retry, and `ok` requires confirmed synchronous success. This facade provides no durable outbox,
idempotency, deletion, reconciliation, or approval workflow. Before any later phase adds automatic
or model-invoked memory operations, integrate authorization, network budgets, effect journaling,
retention policy, and ambiguous-write reconciliation through the existing runtime contracts.
Historical memory must never become recovery state or completion evidence. P1 intentionally has
no such dispatch path. Live cloud and self-hosted server qualification remains future work.

## P1 release-gate observations

On Windows, `test_terminal_width_fallback_when_narrow` fails on unchanged `main` as well
as this branch: the 50-column Rich capture truncates the expected `fallback` label.
`test_form_submission_handler_rejects_duplicate_post` produced one Windows socket
`ConnectionAbortedError` during a full-suite run, then passed six isolated runs.
No test was changed or suppressed. These are pre-existing environmental/flaky
verification issues, not evidence of a Hindsight startup or network dependency.
