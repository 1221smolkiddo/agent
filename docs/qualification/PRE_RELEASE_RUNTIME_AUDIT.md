# Agent47 pre-release runtime and privacy audit

Date: 2026-09-29. Branch: `audit/pre-release-runtime`.
Baseline main: `361027aadd8130c31cc9bd8756f24119a3c4e08f` (clean, CI green).
`main` and `v0.1.0` were preserved. No push, tag change or PyPI upload was performed.
This audit evaluates the source on the audit branch; it does not inherit the earlier
release verdict. Source tests use a separate Python 3.11.15 environment at
`.code-agent/ci-version-check-venv`; the shared development environment was not changed.

## A. 300-second timeout root cause

The literal `300 sec timeout` is not emitted by the checked source. No incident
transcript, runtime configuration or provider trace was supplied, so the provenance
of that exact observed wording cannot be established. The concrete built-in default
capable of stopping a healthy multi-turn task at 300 seconds is:

- `config.Settings.agent_run_timeout_seconds`: default `300.0`, configurable through
  `AGENT_RUN_TIMEOUT_SECONDS`, constrained to 1–86,400 seconds.
- `factory.create_agent`: passes this setting to `CodingAgent`.
- `agent.CodingAgent._run_detailed`: starts `run_deadline` **before recall and planning**,
  checks the remaining budget before each model turn, and rejects a model action
  returned after the deadline. It emits `300s absolute run deadline` wording.
- `interactive.run_interactive_turn` renders the returned result/work report;
  `print_model_failure_card` renders failed model calls separately.

This is an Agent47-owned **overall wall-clock run budget**, not a provider's response
limit. Recall, repository work, planning, model calls, retry delays, tools and
verification all consume elapsed run time. User approval waiting is excluded.
The post-model comparison previously failed to include that exclusion; it now does.
Approval callbacks are restored after the run, and the pause accumulator is reset
before the next run so wrappers cannot accumulate across reused agents.

A distinct logical model-turn budget defaults to 60 seconds. It is bounded by the
remaining run budget and includes retries and fallback attempts. SDK retries are
explicitly disabled. Other independent 300-second settings exist: browser key setup,
optional image scanning, and the balanced execution profile's tool-policy metadata.
Those cannot be conflated with this task budget without an actual error trace.

### Runtime timeout table

The exhaustive source-reference appendix is
[PRE_RELEASE_TIMEOUT_INVENTORY.md](PRE_RELEASE_TIMEOUT_INVENTORY.md). It searches
tracked production code, tests, configuration and documentation, including unrelated
numeric matches. The following table groups the active timers and declared limits.

| Timeout/limit | Value | Owner/function | Scope and exceeded behavior |
|---|---:|---|---|
| Run budget | 300s default; 1–86,400 | `agent._run_detailed`, `config.Settings` | Whole run; stops at checked boundaries and returns blocked; does not asynchronously interrupt tools |
| Model turn | 60s default | `models.OpenAICompatibleChatClient._call_with_retry`, `agent._complete_model` | Remaining turn budget shared across attempts; timeout failure; late result rejected |
| HTTP SDK I/O | Remaining turn seconds | `models.complete_with_timeout`, `stream_complete_with_timeout` | HTTP connection/read/write/pool timeouts; not a hard total wall-clock timer |
| Retry backoff | 0.5s base, 4s cap; 2 transient retries default | `models._call_with_retry` | Sleeps only if remaining turn budget permits; 3 credit retries are separate but share deadline |
| Fallback | Remaining same turn | `models.FallbackModelClient._try_clients_with_deadline` | Each fallback gets remaining budget, not a fresh full budget |
| Planner | 60s default | `execution_host.ModelPlanProvider.plan_with_context` | One independent plan call; exception falls back to deterministic planner; arbitrary synchronous clients remain cooperative |
| Reviewer | Client's configured model timeout | `reviewer.run_reviewer_pass` | Separate final review call; unavailable reviewer fails open; not bounded by remaining run deadline |
| Hosted effect timeout | 30s default; agent passes model-timeout setting | `execution_host.execute_action`, `execution_adapters.TransactionalAdapterRunner.execute` | Stored in adapter context; not itself an enforcement timer |
| Execution profile tool limits | 120/300/600s; base 300s | `execution_profiles.ToolPolicy` | Declared policy; do not infer enforcement from metadata alone |
| Agent orchestration profile | 120s default | `orchestration.AgentProfile`, request construction | Request limit passed to worker; not a separate watchdog |
| Shell command policy | Per command: e.g. 30/60/120/180s | `safety.classify_shell_command`, `sandbox_security.SandboxPolicy.effective_timeout` | Per process; restricted by configured sandbox resource timeout; timeout metadata and cleanup |
| Managed process | 0 means disabled; schema allows up to 604,800s | `process_worker.ProcessWorker` | Stops child on elapsed limit; readiness/resource monitoring independent |
| Managed startup | 5s | `managed_processes.start` | Bounded wait for worker state; worker can continue independently |
| Shell polling | 0.2s default | `processes.ProcessSupervisor.run_shell` | Cancellation/deadline polling interval |
| Process cleanup | 5s grace; Windows 2–5s waits, 10s forced taskkill | `processes.terminate_process_tree` | Signal, then process-tree kill fallback; cleanup can exceed operation deadline |
| Process readers | 2s each | `processes.capture_process_streams` | Bounded joins; surviving descendants holding pipes can leave daemon readers |
| Worker shutdown/readers | 1–3s joins/waits; helper commands 3/5s | `process_worker`, `managed_processes` | Bounded local worker/container control; not a proof of descendant termination |
| Container operation helpers | 5/10/15/30s by operation | `container_manager` | Daemon/inspect/create/stop/reconciliation commands return errors on timeout |
| Container forced cleanup | 10s | `sandbox_security.force_remove_container` | Forced cleanup attempt reported; daemon unavailable means no live proof |
| Daemon/security inspection | 5s | `sandbox_security.container_daemon_available`, `inspect_container_runtime_security` | Returns unavailable/security finding |
| Image inspection/scanning | 10s / 300s | `sandbox_security.inspect_container_image`, `validate_container_image_scan` | Image policy rejected when inspection/scanning fails |
| Windows platform inspection | 5s | `sandbox_security.windows_virtualization_diagnostic` | Diagnostic unavailable on timeout |
| MCP request | 30s default | `mcp.McpStdioClient._read_response` | Queue deadline; `request` disconnects/reconnects and may retry; no proof a remote effect stopped |
| MCP teardown | 3s | `mcp.McpStdioClient.disconnect` | Terminate then kill process; remote effects may remain ambiguous |
| LSP requests | 10s default | `lsp.LspClient.request` | Waits on response queue; returns timeout; does not establish remote request cancellation |
| LSP diagnostics/teardown | 1s diagnostics; 2s shutdown/waits; 1s reader joins | `lsp` | Bounded local waits |
| Hindsight provider | 10s default, maximum 120s | `experience_memory.providers.hindsight` | `asyncio.timeout_at`/remaining-time bounds; cancellation and bounded `aclose` |
| Automatic recall | 3s default, maximum 10s | `experience_memory.recall`, config | Advisory timeout; coding continues without history |
| Automatic Reflect | 5s default, maximum 15s | `experience_memory.reflection`, config | One escalation-call budget; no authority; timeout yields no advice |
| Startup/outbox recovery | Provider limit capped to 2s; 3 items startup | `factory.create_agent`, `retention.after_run` | Bounded reconciliation attempts; background recovery remains independent |
| Outbox claims | max(10s, provider timeout + 5s) | `experience_memory.outbox` | Lease expiration enables safe operation reconciliation, not blind duplicate retain |
| Git scope | 2s | `experience_memory.scope` | Missing Git provenance on failure |
| Execution database | 30s SQLite connect/busy timeout | `durable_execution.SQLiteEventStore` | Operational error; separate from model deadline |
| Outbox/index database | 5s / 10s | `experience_memory.outbox`, `repo_index` | SQLite busy/connect limit |
| Execution leases | 30s default TTL | `durable_execution.SQLiteEventStore.acquire_lease/heartbeat` | Fenced ownership; expiration does not forcibly kill a worker |
| Execution-plane wait | Caller-supplied or unbounded | `runtime_migration.ExecutionPlane.wait` | Join timeout raises while worker remains alive; it is a wait limit, not cancellation |
| Background index/hooks | 1–5s bounded joins, hook-specific limits | `repo_index`, `extensions` | Join timeout does not kill Python thread; extension code can continue |
| Plugin command/skill | 30s / 120s defaults | `plugins` | Subprocess timeout; arbitrary child trees require separate cleanup guarantees |
| Git/tool inspection | 15/30/60s | `tools._git`, search and formatter helpers | Subprocess failure; result is sanitized |
| Web search / OAuth HTTP | 20s | `tools.ToolRegistry._fetch_url`, `auth.oauth` | HTTP timeout/failure |
| OAuth browser flow | 180s default, 1–900 | `auth.config`, `auth.google` | Callback wait expires; distinct from a coding run |
| Local setup browser | 300s wait; 30s response; 2s shutdown | `cli`, `local_server` | Setup timeout page/exit; no model run started |
| Connectivity/readiness | 5s provider socket; .25s local port | `doctor`, `managed_processes` | Diagnostic/readiness result only |
| Offline evaluation/release subprocess | Usually 60s; explicit caller limits | `evals`, `collaboration`, `release_smoke`, `memory_eval` | Qualification/tool wrapper limits, not normal interactive task deadline |

## B. Long-task behavior

The default run intentionally has a 300-second budget. It is not a sliding idle
timeout. A healthy multi-cycle task can therefore be stopped even when every call
is healthy. Mocked work consuming 320 seconds is blocked with a 300-second budget
and succeeds with a 600-second configured budget. Each modeled call consumes 80s
under an explicitly configured 100s model-turn budget; no real 5-minute wait occurs.

The setting permits legitimate tasks longer than 300 seconds. However, the runtime
is **cooperative**, not a hard deadline: in-progress tools, planner work, reviewer
work and retention are not all clipped to remaining run time. An operation started
before the deadline can finish after it. Automatic verification/finalization can
also add elapsed time. Do not describe this as guaranteed termination at 300 seconds.

## C. Provider versus internal latency

Fast and below-budget stub calls complete. Fake-clock late completions are rejected;
late streamed tokens are discarded and the stream closes. Network timeout triggers
configured retry/fallback; fallback receives only the remaining logical turn budget.
Planner timeout returns a deterministic plan and records its failure type.
A real short process timeout is distinct from a model timeout and cleans its registered
process. These tests isolate provider latency from run-budget and process behavior.

One securely configured provider smoke ran: Gemini, model `gemini-3.5-flash`, one
short request, zero retries, 2,169.18ms elapsed, **server error category**. No prompt,
key, response body or hidden reasoning was printed in the smoke report. Medium and
workflow provider smokes were not run after this failure. This proves only that this
request failed at the provider boundary; it does not explain a historical 300-second
incident or establish successful live long-workflow latency.

## D. Timeout and cancellation correctness

**PARTIAL, with remaining limits.**

- OpenAI-compatible SDK retries are disabled; Agent47 owns retry accounting.
- Post-response deadlines now reject late completions. Stream loops enforce elapsed
  turn time between events and close on success/error. Stream creation time counts.
- HTTP SDK timeouts are per I/O operation. A slowly arriving non-stream response,
  or a blocked read before the next stream event, can still exceed total turn time.
  There is no independent synchronous-request watchdog. Returned late content cannot
  execute an action, but prompt server-side computation is not provably cancelled.
- `CodingAgent.cancel` closes supported model transports and requests tool cancellation.
  Custom synchronous clients without timeout/cancel support remain cooperative.
- Hindsight uses asyncio deadlines and bounded cleanup; tests exercise timeout/close
  and fail-open behavior. Running-loop incompatibility is handled as unavailable.
- Durable effect exceptions are recorded as UNKNOWN and require reconciliation, not
  blind replay. Timeout alone cannot establish that an external side effect failed.
- `ExecutionPlane.wait(timeout)` explicitly leaves a live worker running. It is not
  cancellation and must not be presented as such.

No architecture rewrite was made to claim stronger guarantees than these tests prove.

## E. User-visible responsiveness

The terminal already has a Rich spinner refreshed four times per second. Streaming
chunks deliberately are not printed: `StatusReporter.model_stream_chunk` is a no-op.
A defect kept the previous tool label while waiting for later non-stream model calls.
`thinking()` now resets it to **Thinking — Waiting for model**, with no task content.
No fake percentages or additional printing were introduced.

The existing model latency, tool elapsed time, recall/Reflect metrics and planner
route records are reused. The model boundary now persists metadata-only
`runtime_timing` start/end events containing phase, step, execution ID, elapsed ms
and completion/error category, even for clients without usage-record support.
Inputs and completions are never serialized into these events. Planning, preflight,
review and retention do not yet all have phase duration records, and startup before
planning can still look like a generic idle spinner. Full phase timing remains an
IMPORTANT diagnostic limitation.

Broad instrumentation was rejected by automatic approval review because it rewrote
many critical paths. None of those edits were applied. The scoped implementation
extends the existing model boundary and status display only.

## F. Privacy and data flow

| Data | Local storage | External boundary | Terminal | Sanitization/limits |
|---|---|---|---|---|
| User task | Redacted AgentStorage run; durable goal currently verbatim | Model/planner/reviewer receive task; SDK boundary now redacts recognized secrets | History/report may show task | Durable goal privacy gap reproduced |
| System prompt | Constructed in memory; not intentionally stored as raw prompt by agent | Sent to model as required | Not intentionally printed | SDK content redactor applies; configured third-party SDK logging is outside this guarantee |
| Dedicated hidden reasoning | SDK fields ignored; reviewer raw payload removed | Not sent to Hindsight/reports by these paths | Stream callback receives content only and does not print it | Regression tests PASS; cannot identify arbitrary reasoning disguised as ordinary content |
| Typed model actions / final answer | Typed action steps/results; malformed raw response replaced by length metadata | Subsequent context and optional review | Intended answer/report | Shared secret redactor at SDK and markdown/error-card boundaries; typed final/result objects are not a universal privacy filter |
| File snippets / edit content | Local tool history, snapshots and transactional journals may hold content | Selected snippets/diffs go to model; MCP arguments depend on invoked tool | Explicit diff/report views can show source | Sensitive paths are denied; journal content is not a blanket redacted data store |
| Tool stdout/stderr | Built-in shell and managed logs are redacted/bounded | May be included in model context | Diagnostics/report output | Registry now also redacts extension result output and nested metadata before runtime use |
| Environment | Managed specs retain names and hash, not raw environment values | Filtered tool/container environment; explicitly configured MCP environment | No intentional environment-value dump | Sanitization is heuristic; environment names themselves remain visible |
| API keys | OS credential manager; legacy env settings; accidental durable goal persistence is a defect | Provider authentication necessarily uses key/header | Must not be printed | Recognized patterns redacted in tested paths; arbitrary unlabelled/unknown-format credentials are not guaranteed |
| Authorization headers | Must not be persisted as diagnostics | Sent only for configured provider authentication | Error bodies no longer intentionally echoed | Shared bearer redactor and category-only provider error summaries |
| Git metadata | Local reports/index/events | Model context and opt-in memory provenance; repository memory bank hashed | Branch/path summaries | Git provenance is not treated as proof or permission; contextual secrets still require redaction |
| Recalled memory | Bounded quoted advisory context and safe metrics | Optional model/planner input | No automatic raw recall dump | Sanitizer, staleness/provenance checks, one-call selective budget |
| Reflect context | Safe diagnostic evidence/attempt summaries | Opt-in Hindsight Reflect then bounded advice to model | No raw Reflect payload dump | Escalation only, one-call budget, no permission/completion authority |
| Retained lessons/outbox | Sanitized engineering episode plus operation IDs/leases | Optional Hindsight retain of validated summary/metadata | Metadata status only | Transcript/raw prompts excluded; timeout becomes ambiguous/reconciled, not duplicate write |
| Execution events/snapshots | Durable SQLite stores event payloads verbatim | Recovery/planning can reuse selected context | Execution/debug views can expose stored data | **FAIL**: synthetic API-key goal persists in canonical events |
| Provider failures | Usage records and error summaries | No need to resend raw exception body | Concise category/error type | Retry/fallback/agent/reviewer errors now omit raw provider body; debug stack formatting excludes exception body |

The durable probe created a fresh synthetic execution database outside the repo:
four events, with the fake API-key marker present in a goal payload. The report stores
only this Boolean finding, not real credentials. Redacting canonical effect requests
in-place could change replay semantics and fingerprints; that requires a deliberate
persistence policy rather than an unreviewed rewrite.

Further source findings: status tool labels can still contain raw commands/queries;
the inspected Typer app has exception locals disabled, but unhandled automation
exceptions can still expose raw exception bodies; execution-plane status returns raw
exception text. These paths are not covered by the narrower
interactive error fix and remain privacy/error-UX defects. Replay/transaction journals
are sensitive local operational data, not a claim of secret-free storage.

## G. Hidden reasoning

**PASS for dedicated provider fields and typed response paths tested here.**
Non-stream `message.reasoning_content/reasoning_details` and stream delta equivalents
are ignored. Reviewer extra fields are not retained in `raw` or work-report records.
Malformed action responses no longer enter storage verbatim. A model can place any
text in ordinary `content`; this audit does not claim semantic detection of all such
text. Public plan rationale and deterministic verifier `reasoning_summary` are not
provider hidden-reasoning fields.

## H. Secret leakage

**FAIL overall**, despite the corrected tested boundaries.

All five requested synthetic secret shapes are covered in model request redaction,
retry/fallback/model-failure diagnostics, terminal markdown/error cards, tool result
metadata, run storage and saved work reports. Existing memory/Reflect/outbox suites
cover redaction and exclusion of unsafe history. Built-in process stderr and managed
logs are also exercised by integration tests. No weaker sanitizer was introduced.

The canonical durable-event goal probe still leaks the synthetic credential locally.
Raw status labels, arbitrary structured credential formats and some automation/debug
views prevent an end-to-end PASS. No real secret was used as a test marker.

## I. Runtime systems verified

The source call graph is:

```text
agent47 = code_agent.interactive:main
  -> require_interactive_onboarding
  -> config.Settings / CredentialStore / provider registry
  -> factory.create_agent
       -> model profiles/presets -> OpenAICompatibleChatClient / FallbackModelClient
       -> ToolRegistry -> ProcessSupervisor / SandboxRunner / ContainerManager
       -> ExecutionRuntimeHost -> DurableExecutionRuntime / SQLiteEventStore
       -> optional ModelPlanProvider, reviewer, ExperienceMemoryService/outbox recovery
  -> interactive.run_interactive_turn -> CodingAgent.run_detailed -> _run_detailed
       -> clean task / selective MemoryRecallCoordinator.before_planning
       -> optional repository snapshot for contextual planner
       -> ExecutionRuntimeHost.begin_legacy_run -> plan generation/persistence
       -> create AgentStorage run and durable execution link
       -> automatic repository/context preflight
       -> repeated model turns / typed JSON parsing / loop and confidence gates
       -> tool authorization / execute_action / transactional adapter journal
       -> ToolRegistry.run -> tool/process/sandbox or extension
       -> mutation evidence / automatic verification / diagnosis and repair
       -> optional bounded MemoryReflectCoordinator escalation
       -> reviewer / _finalize_run -> durable finish + run snapshot
       -> tools/platform close -> work report
       -> EpisodeRetentionCoordinator.after_run -> sanitizer -> durable outbox
       -> CLI result/rendering
```

Actual ordering matters: durable planning precedes the general repository preflight.
Planning gets historical/repository context only through the explicit contextual
planner path. Default execution mode is **shadow**: legacy agent decisions remain
authoritative while durable comparison/state is recorded. Primary authority requires
sequential promotion gates; passing a default run does not prove engine-only authority.

State transitions: `ExecutionEngine.dispatch` writes canonical events and updates
projections; `PlanningService.apply` persists graph versions; task scheduling and
worker results transition queued/ready/running/verifying/diagnosing/replanning/complete.
Transactional effects use REQUESTED -> AUTHORIZED -> PREPARED -> DISPATCHED/RUNNING ->
COMMITTED/FAILED/UNKNOWN. Verification evidence, not a worker success claim alone,
establishes primary completion. UNKNOWN effects are reconciled before retry.

| System | Result | Concrete integration evidence |
|---|---|---|
| Durable lifecycle, restart/recovery, resume | PASS, privacy separately FAIL | `test_durable_execution`: real process restart, effect UNKNOWN recovery, leases; `test_execution_migration`: host/agent path and execution plane separation |
| Planning and persisted plan reuse | PASS | `test_planning_context`, host model-planner fail-safe test, resume planner reuse tests |
| Typed actions, authorization, evidence | PASS | `test_execution_migration` primary effect approval/verification tests; `test_tool_permissions`; `test_execution_adapters` |
| Sandbox policy / approvals | PASS offline; local Docker NOT RUN | `test_sandbox`, `test_container_manager`, unsafe/path/permission tests; daemon absent |
| Transactional editing / rollback / optimistic hashes | PASS | `test_transactions`, hosted transactional replay, crash journal recovery tests |
| Verification / no false success | PASS | `test_verification`, primary verification refusal, agent failed-test/final-claim tests |
| Diagnosis / bounded repair | PASS | `test_agent_recovery`, host diagnosis/replan tests; retry/fallback budget tests |
| Selective Hindsight recall | PASS with fake SDK/provider | `test_memory_recall`, `test_hindsight_final_qualification`, recalled injection and first-time skip tests |
| Reflect wiring and no authority | PASS with fake SDK/provider | `test_memory_reflect`: advice reaches recovery, first failure skip, one-call budget, real-shell approval cannot change, failed tests cannot become success |
| Retention / sanitizer / outbox fail-open | PASS with fake SDK/provider | `test_episode_retention`, `test_memory_outbox`, `test_experience_memory`; provider failure does not alter accepted coding result |
| Local process timeout/cleanup | PASS for exercised processes | Real short subprocess timeout, cancellation tests, managed process lifecycle tests; Windows signal fallback regression |
| CLI / installed package | See final verification below | Fresh isolated wheel import, help, unauthenticated setup; no real keys supplied |
| Live successful provider workflow | NOT VERIFIED | One configured-provider request failed; medium/long smokes not attempted afterward |

## J. Remaining defects

### BLOCKER

1. **Durable execution privacy:** canonical goals/effect payloads can retain secrets
   verbatim. Reproduced with a synthetic goal. Execution export/recovery surfaces need
   a coherent sensitive-data policy preserving replay semantics.
2. **End-to-end output/error privacy incomplete:** command/query status labels and
   automation exception/status paths lack a universal safe boundary. A generic
   interactive error fix is not sufficient to certify every terminal/debug surface.

### IMPORTANT

3. **Hard timeout/cancellation limits:** synchronous HTTP I/O timeouts are not a total
   watchdog; planner/reviewer/tool/finalization work is not uniformly bounded by the
   remaining run budget; arbitrary extension/worker threads cannot be forcibly cancelled.
4. **Orphan descendants/readers:** bounded reader joins and process-tree termination
   are best effort; an already-exited parent or detached descendant can retain pipes.
   Live Docker cleanup could not be requalified locally.
5. **Live provider success unqualified:** the configured provider failed the trivial
   request; no successful medium/long live task is claimed.
6. **Timing gaps:** planning/preflight/review/retention lack full phase timing and early
   startup status coverage. The model/tool diagnostics are useful but not comprehensive.
7. **Heuristic redaction:** unknown formats, quoted structured secrets and ordinary
   content containing hidden reasoning cannot be universally identified.

### NON-BLOCKING

8. Known Windows narrow-terminal baseline test remains untouched; full-suite result is
   recorded below. Python/platform coverage of this audit is local Windows 3.11.15;
   the baseline main's green CI does not qualify these unpushed changes.
9. PyPI credentials remain a separate release-operations requirement; no publication
   or tag realignment belongs to this audit.

Fixed defects: late SDK result acceptance, elapsed stream deadline and stream close,
raw retry/fallback/provider error diagnostics, reviewer raw reasoning fields,
malformed response persistence, approval pause comparison/reset/restore, recognized
secrets in SDK messages and extension tool results, terminal response/error redaction,
stale model-wait status, and Windows CTRL_BREAK SystemError bypassing taskkill fallback.

## K. Release verdict

**NOT READY FOR 0.1.0**

The durable privacy finding and remaining output/error privacy gaps prevent a release
PASS. Successful deterministic suites establish wiring and the tested fixes, not
complete live-provider behavior or absolute cancellation guarantees. Do not publish
this audit branch as a qualified replacement release without resolving those blockers.

## Final verification

Results and artifact/import paths are recorded in the accompanying evidence summary. Local logs/JUnit and temporary databases remain ignored
or outside the repository. Only reviewed source, tests and sanitized audit documents
are eligible for the audit commit. No runtime databases, credentials or build outputs
are committed.

### Completed checks

- Ruff and `git diff --check`: **PASS**.
- Final focused integration suites: **676 passed, 5 skipped, 4 deselected in 145.67s**.
- New deterministic audit regressions: **24 passed**, included above and in the full suite.
- Full non-Docker suite: **1,241 passed, 1 failed, 5 skipped, 4 deselected in 211.61s**.
- The sole failure remains `tests/test_interactive_diff.py::test_terminal_width_fallback_when_narrow`:
  assertion `"fallback" in text` fails. The test and diff-rendering implementation were not changed.
- The earlier four Windows managed-process failures were reproduced, fixed at the
  shared termination fallback and verified; they do not remain in the final suite.
- Local live Docker: **NOT RUN**, daemon pipe unavailable. Baseline CI Docker success
  is historical evidence, not a substitute for this audit's live execution.
- Fresh audit wheel: **PASS** installation, `pip check`, isolated package import,
  `agent47 --help`, `code-agent --help`, and bare unauthenticated setup/startup, all exit 0.
  All 134 packaged runtime files match current source, ignoring newline normalization.
- Installed module path:
  `C:/Users/Sirius/AppData/Local/Temp/agent47-runtime-audit-install-zchqmw2b/.venv/Lib/site-packages/code_agent/__init__.py`.
- Installed smoke children had fresh profiles, a null keyring, no inherited provider
  credentials or repository `PYTHONPATH`, controlled closed stdin and a 30s CLI bound.
- The first smoke harness used Windows' default text decoder; its capture failed.
  Explicit UTF-8 fixed the harness and a second brand-new environment passed all checks.
  This was not treated as a package defect or omitted from the audit history.
- No audit-environment `pythonw` managed workers remained at final inspection.

[Sanitized evidence](PRE_RELEASE_RUNTIME_EVIDENCE.json) records exact counts, source
fingerprints, package paths, provider timing and Boolean privacy probes. Temporary
logs and probe databases remain outside committed source. The report's privacy FAIL
and release refusal remain unchanged by successful tests.
