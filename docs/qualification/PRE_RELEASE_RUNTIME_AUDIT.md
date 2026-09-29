# Agent47 long-lived runtime and privacy qualification

Date: 2026-09-29. Branch: `audit/pre-release-runtime`.
Baseline: `8a9bbe588fc4c6799f9938328cff8d7ee103f4e9`.
This report supersedes the preceding audit's runtime and privacy findings. Main and
`v0.1.0` are unchanged. No push or package publication is authorized by this work.

## A. Global runtime budget: REMOVED

Normal Agent47 tasks have no total wall-clock cutoff. `CodingAgent._run_detailed`
no longer creates, checks, or passes a remaining overall deadline. The model receives
its own full per-turn budget; Reflect retains its independent bounded-call budget.
There is no replacement 600-second, hourly, or sliding session timer.

`Settings.agent_run_timeout_seconds` defaults to `None` and is deprecated and ignored.
The constructor argument remains accepted for callers, also ignored. Existing `.env`
values such as `AGENT_RUN_TIMEOUT_SECONDS=300` cannot reinstate the cutoff. The sample
configuration and doctor output explicitly describe this policy.

## B. Remaining operation and resource limits

| Protection | Owner | Behavior |
|---|---|---|
| Model turn | `models.OpenAICompatibleChatClient`, `CodingAgent._complete_model` | Default 60s; configured retries/fallback share the call deadline; late returned actions rejected |
| Provider retries | `models._call_with_retry`, fallback client | Bounded retry counts and backoff; exhausted failure blocks current turn; interactive session remains available |
| Commands/processes | `ProcessSupervisor`, sandbox policy, managed workers | Existing command-specific timeouts, cancellation, process-tree cleanup and resource limits |
| Network, MCP, LSP | HTTP clients and protocol clients | Existing request/queue deadlines; errors identify the component; remote error bodies omitted |
| Tool failure loops | `ExecutionState`, agent recovery | Identical outcomes/failure fingerprints, bounded recovery/replanning, max failures and max steps |
| Context | `context_budget`, agent and SDK adapter | Configured prompt/context limits plus output reservation; deterministic compaction under pressure |
| Memory | selective recall, Reflect and outbox | Independent timeout/call/lease limits; advisory failure does not grant authority or claim success |
| User cancellation | `CodingAgent.cancel`, interactive shortcuts, process supervisor | Cancel transport/processes, stop subsequent actions and finalization; late model results cannot mutate |

`max_steps` is still a per-task action/resource bound (interactive default 12), not a
session clock. Users can adjust it and continue/resume using existing commands.
Repeated failures remain bounded. Elapsed session duration is never a stall signal.
No duplicate stall subsystem or background watchdog was introduced.

## C. Long active work and live-provider timing

A fake-clock integration advances through planning (40s), healthy model calls
(50s/45s/45s), two tool operations (180s each) and verification work (60s each):
**660 seconds**, successfully completed. It passes with no legacy setting and with
legacy values of both 300 and 600. Each model call remains below its 60s limit.
Existing timeout/retry/process tests cover failure and cleanup independently.

The already-configured Gemini provider was exercised with two bounded smokes:

- Short response: timeout category, 16,476.95ms including setup, 15s model limit.
- Multi-step fixture: blocked after two model calls, 29,990.10ms total and
  29,448.54ms recorded model time. Final category `MODEL_TIMEOUT`.
- Safe phases included Planning, Inspecting project, Executing, Waiting for model,
  and Recovering. No raw provider body, prompt, credential, or reasoning was printed.

This demonstrates operation-level timeout handling; it does not qualify a successful
live long workflow. Provider instability is not treated as a total-runtime defect.

## D. Context management and durable continuity

`context_budget.bound_messages` is a deterministic pipeline, with no summarization
LLM call. It preserves system instructions and the current task, pins a structured
checkpoint, then fills the remaining budget with recent conversational evidence.
Older redundant history is removed under pressure. Hidden SDK fields are not part
of the checkpoint. Full run events/evidence remain available independently.

The agent pins current plan, unresolved blockers, recent evidence, verification
records, changed paths, recovery state, run ID and execution ID. Compaction no longer
deletes the execution state's evidence records to save prompt space. It persists an
execution-state checkpoint when compaction occurs. The provider boundary preserves
that pinned checkpoint rather than dropping it during its own size check.

`AGENT_CONTEXT_MAX_CHARS` retains the existing character budget (default 60,000).
`AGENT_CONTEXT_WINDOW_TOKENS` adds an explicit window budget (default 65,536), with
reserved configured output tokens and message framing. In the absence of a provider
input tokenizer, accounting conservatively uses UTF-8 bytes as token upper estimates.
Set the window to a value supported by the selected model and fallback models; registry
labels such as “large” are not precise tokenizer/window guarantees.

If the current task plus mandatory instructions/evidence alone cannot fit, the request
is not sent. Agent47 reports an explicit context-budget failure and preserves durable
state so the user can narrow that task or configure a suitable model/window. It does
not silently truncate the current task or reset the project.

`SessionState` retains a stable session ID, current plan, verified evidence and durable
execution/run references. Structured `session_checkpoint` records live in the existing
AgentStorage run ledger; startup restores the latest checkpoint for the workspace.
Each interactive turn updates the same logical session. Raw old conversation is not
required for resume. The recent transcript stays bounded and is sanitized before reuse.
Project memory, repository state, execution snapshots, plans, verification and optional
Hindsight experiences continue through their existing stores and authorities.

A 35-turn integration exercises real agent request construction, context pressure,
checkpoint writes and reopening storage. Current/recent instructions, plan and verified
evidence remain available, requests remain within configured limits, and identity is
stable after restoration. Large old conversation does not displace the pinned state.

## E. Runtime path and responsiveness

```text
interactive.main -> Settings / authentication -> SessionState.restore
  -> factory.create_agent -> model adapter + ToolRegistry + ExecutionRuntimeHost
  -> run_interactive_turn -> bounded task_with_context
  -> CodingAgent.run_detailed -> _run_detailed
       -> Planning / selective recall / persisted plan creation or reuse
       -> Inspecting project / context preflight
       -> bounded messages + execution checkpoint -> Waiting for model
       -> typed action / authorization / adapter / tool / transactional mutation
       -> Verifying / diagnosis / bounded recovery / optional Reflect
       -> Reviewing / finalization / verified report / retention outbox
  -> SessionState.update / structured checkpoint -> next interactive turn
```

Existing durable state machines and promotion gates are unchanged. Shadow mode still
records/computes durable state while legacy decisions are authoritative; this work
does not claim a migration to engine-only authority. Resume reuses persisted plans.

The existing Rich spinner is reused for safe phase updates. Model waits reset stale
labels; tools display phase descriptions instead of raw commands or search queries.
There are no fake percentages or periodic printed heartbeat lines. Timing remains
metadata-only at model/tool boundaries; full phase-duration coverage is not claimed.

## F. Durable-event privacy: PASS for tested boundaries

`SQLiteEventStore.append` recursively sanitizes before constructing/persisting events.
Returned events are the sanitized events applied to projections, keeping live/replayed
state consistent. `save_snapshot` sanitizes before computing its checksum and writing.
Nested lists/dicts, keys, strings and metadata reuse the central secret redactor.
Credential-valued fields are removed/redacted; dedicated provider-private fields are
excluded. Non-sensitive booleans, numbers, nulls and structured data remain unchanged.

The same recursive sanitizer protects AgentStorage checkpoints/reports and JSON
protocol output. Work-report construction also sanitizes before returning a payload.
All six requested fake credential/header/JWT forms are tested at persistence and UI
boundaries. Fresh SQLite event/snapshot rows contain no raw markers. Ordinary execution
snapshot replay and idempotency remain covered by integration tests.

Effect replay receives an additional guard: `CallableTransactionalAdapter.prepare`
refuses requests whose sensitive content would change during sanitization, as well as
redacted placeholders. A secret-bearing operation is not silently executed with altered
arguments. Use configured credentials/environment references rather than literal secrets
in durable tool requests. This is a fail-closed restriction, not transparent secret replay.
Existing databases are not retroactively scrubbed by this change.

## G. Status, automation and hidden reasoning

**Status/error privacy: PASS for tested paths.** Status labels/details/stages, tool
metadata/diagnostics, workspace summaries and mutation paths are sanitized. Raw command
arguments and search text are omitted from status descriptions. JSON protocol events
are recursively sanitized at serialization.

CLI/interactive/background worker/adapter/extension errors use safe categories instead
of raw exception bodies. Remote MCP/LSP error bodies are omitted. Debug-mode outer CLI
handling reports frame function/line metadata without exception bodies, source lines or
locals. Trusted local missing-key and sandbox-isolation diagnostics remain actionable.
Expected provider failures continue to use classified model-error summaries.

**Hidden reasoning: PASS for dedicated provider fields.** SDK reasoning fields remain
ignored; typed final/actions and reviewer results exclude unused raw payloads. Structured
checkpoints store conclusions/plans/verification, not provider analysis. Sanitization
also drops `reasoning_content`, `reasoning_details`, `chain_of_thought`, `private_analysis`
and `provider_debug` fields. Text disguised as ordinary answer content cannot be
semantically guaranteed absent; no such universal claim is made.

## H. Integration and verification

Final measured test counts and artifact checks are recorded below and in
[PRE_RELEASE_RUNTIME_EVIDENCE.json](PRE_RELEASE_RUNTIME_EVIDENCE.json). Coverage includes:
interactive continuity, durable execution, planning/preflight, model budgets, tools,
authorization, process cleanup, transactions, verification, recovery/resume, selective
recall, Reflect, retention/outbox, CLI startup/status and privacy.

## I. Known limitations

- Synchronous provider cancellation remains cooperative. SDK I/O timeouts are not a
  server-side computation kill guarantee; returned late actions are rejected.
- Detached descendants and inherited pipes can outlive best-effort process cleanup.
- Local Docker daemon is unavailable; live Docker security/cleanup was not rerun.
- The configured provider did not complete both smoke tasks successfully.
- Fixed mandatory context can exceed a configured window; this produces an explicit
  recoverable context-budget result, never a silent project reset.
- Redaction is heuristic for unknown unlabelled secret formats. Existing sensitive local
  databases/journals are not retroactively rewritten; normal source-file contents can
  still be intentionally shown by file/diff tools.
- Full phase-duration telemetry and model-specific exact input tokenizers are not added.
- The known Windows narrow-terminal rendering test is unchanged.

## J. Release gate

**READY FOR 0.1.0 WITH DOCUMENTED LIMITATIONS**

The requested deterministic release gates pass: no overall timer, healthy 660-second
workflow, bounded operations/retries, stable 35-turn context and durable session restore,
sanitary event/status/error boundaries and no dedicated reasoning-field persistence.
The full suite has no new failures. The provider/Docker/cancellation/context limits above
remain explicit qualifications; this does not claim green CI for these unpushed changes.

- Focused: **737 passed, 5 skipped, 4 deselected**, 158.40s.
- Full non-Docker suite: **1,257 passed, 1 failed, 5 skipped, 4 deselected**, 184.58s.
- Sole failure: unchanged `tests/test_interactive_diff.py::test_terminal_width_fallback_when_narrow`,
  assertion `"fallback" in text` fails.
- New long-lived runtime regressions: **16 passed**, included in both final suites.
- Ruff and diff whitespace checks: **PASS**.
- Fresh installed wheel: **PASS** installation, pip check, isolated import, both CLI
  help commands, and unauthenticated startup. Packaged runtime files match tested source.
- Packaging metadata, version and the narrow-terminal test are unchanged.
- An intermediate test-file write failed under Windows' default encoding. The test
  file was restored with explicit UTF-8; the final results above include every case.

The implementation is ready for merge review with these limitations documented.
No push, merge, tag change or publication was performed.

## K. LONG-HORIZON ARCHITECTURE

Agent47 uses long-running coding-agent patterns without claiming undocumented Codex
internals. The global task deadline remains removed. Healthy progress has no fixed
default step ceiling; `--max-steps N` or `AGENT_MAX_STEPS=N` can bound a run,
while omitted/zero means unlimited. The same-action/same-result detector,
failure fingerprints, five recovery attempts, default three consecutive failures,
replan requirements, cancellation and verification gates continue to stop non-progress.

A logical model turn defaults to 180 seconds, including owned retries and fallback.
The timeout is configurable with `AGENT_MODEL_TIMEOUT_SECONDS`; late results are
rejected and stream handles are closed. Shell policy defaults are 60 seconds for
read-only inspection, 120 for git, and 600 for tests/build/lint/typecheck and
install/network operations. Workspace resource policy may reduce those ceilings.
Development servers and watchers use the existing managed-process path. There is
no global task clock.

Model profile and registered-model numeric context metadata take priority over
`AGENT_CONTEXT_WINDOW_TOKENS`; absent metadata/override, the fallback is 65,536
tokens. This fallback is a conservative policy value, not a claim about a provider.
The existing UTF-8 byte estimator is conservative where an exact provider tokenizer
is unavailable. The request budget reserves the selected model's output capacity,
ten percent of the context window and a fixed framing allowance. Agent47 begins
compaction at 75% of usable input capacity and enforces an 85% hard bound. The old
character setting is optional compatibility protection, not the primary default.
Provider fallback uses the smallest configured client capacity. Stored context
metrics contain numeric estimates/counts only, never prompt text.

The current user request and structured execution checkpoint are pinned. The
checkpoint contains active plan, verification, blockers, evidence and changed paths;
recent conversation is kept only while it fits. Duplicate or oversized older
messages are omitted. Existing checkpoints are replaced by the newest structured
one, avoiding summary-of-summary decay. A truly oversized pinned request returns
`CONTEXT_CAPACITY` with durable state preserved. The status phase reports
compaction without exposing hidden reasoning.

Interactive sessions keep a stable persistent project goal separately from the
changing task. Explicit `SessionState.set_goal` changes it; ordinary follow-up
tasks do not. `/status` displays the goal. Recent explicit corrections are retained.
Bounded structured session checkpoints
persist the goal, plan, verification, relevant paths, decisions, failed approaches,
blockers and next actions in the existing project-local store. On restart, Agent47
restores that checkpoint without replaying a raw transcript. Repository files and
current tests remain authoritative; a checkpoint is an aid to continuation, not
proof that stale facts are still true. `project.md` remains explicit project
knowledge. Hindsight supplies selectively recalled historical experience and does
not become the active transcript or source of repo truth.

All checkpoint and event payloads continue through recursive sanitization; provider
reasoning fields remain excluded. The long-horizon tests use fake clocks and process
objects: they do not wait hours or run a real five-minute build. They verify more
than 100 productive actions, two simulated hours, a 150-second model turn,
a late model response, a five-minute build, process cleanup beyond 600 seconds,
100 conversational turns, repeated compaction and restart recovery.

The earlier sections' test counts describe the prior audit commit; the verification
for this long-horizon revision is recorded below after the final suite.
## L. Long-horizon qualification result

**READY FOR 0.1.0 WITH DOCUMENTED LIMITATIONS.** The final focused runtime,
context, session, process and privacy set reports **179 passed**. The final
non-Docker suite reports **1,264 passed, 1 failed, 5 skipped, 4 deselected**.
The sole failure is the pre-existing, unchanged Windows narrow-terminal case
`tests/test_interactive_diff.py::test_terminal_width_fallback_when_narrow`
(`"fallback" in text` fails). Ruff and `git diff --check` pass.

The deterministic long-horizon tests verify 121 productive actions across 7,503
simulated seconds, a 150-second model response accepted within the 180-second
turn limit, an over-deadline response rejected, a five-minute build accepted,
a 600-second hung build cleaned up, and 100 task turns with repeated compaction
and checkpoint restoration. The restart test restores the original goal,
active plan, latest correction, blocker, decision and failed approach from a
fresh storage instance, then checks repository content directly. Privacy tests
cover event/snapshot storage, status, automation errors, credentials and private
reasoning fields. These results are simulated where noted, not live provider or
hours-long process measurements.

The local Docker daemon is unavailable (the Docker Desktop Linux engine pipe
does not exist), so Docker security tests were not rerun. Provider smoke and
best-effort descendant cleanup limitations from the earlier audit remain. Model
capacity metadata is optional; current registered models without numeric
capacity use the configured override or conservative 65,536-token fallback.
The known terminal assertion still prevents a completely green local suite.
No packaging metadata, tag, main branch, PyPI publication or remote branch
was changed by this qualification.

## M. ADAPTIVE REPLANNING

Failed approaches are bounded to 16 structured records in the durable execution
checkpoint. Each record has a deterministic SHA-256 fingerprint of the executable
action (including its tool, target and parameters), a sanitized display target,
failure category, output digest and exit code, attempt count, timestamps, workspace
generation and supersession fields. Neither raw failed output nor the raw shell
command is stored in these records. A matching action becomes failed after two
unsuccessful attempts in the same workspace generation. A further identical action
is rejected before tool execution, including after context compaction or process
restart. Rewording a plan does not change the fingerprint; changing an executable
action does. A workspace mutation can invalidate the old failure context, allowing
verification to be retried against changed code. A successful alternative of the
same action type marks the prior failed approach superseded. Existing recovery
thresholds remain unchanged.

## N. INTERACTIVE TERMINAL UX

Shell and managed-process actions now create a sanitized display copy of the
command before the tool runs. Approval prompts and terminal command history use
that copy; the executor receives the original command. The display masks known
credential forms, credential-bearing flags, quoted arguments, opaque tokens and
literal echo payloads. A bounded eight-command history holds sanitized command,
state, elapsed duration, short result and at most 4,000 sanitized output characters.
The interactive Rich display starts collapsed and `o` toggles up to 40 recent
sanitized output lines. The existing Live spinner handles the active state; a
compact bottom line shows phase, elapsed time, step and context utilization,
dropping lower-priority fields as terminal width shrinks. Replanning and
cancellation update that phase. Non-interactive output uses plain `RUN` and
`PASS`/`FAIL` lines and starts no key-reader thread. Provider-private reasoning
fields are omitted from expanded output.

The tool API currently returns output only when a command completes, so the active
panel shows the command and running indicator immediately, then exposes captured
output after completion. Arbitrary command output can contain novel secret formats
that pattern-based sanitization cannot recognize; operators should avoid printing
credentials in tool output. The separate existing tool/result storage policy still
applies. The pre-existing Windows narrow-terminal assertion and unavailable Docker
engine remain release qualification limitations pending final suite results below.

Final qualification for this revision: focused adaptive, terminal, recovery,
privacy, runtime and session tests **173 passed**; the new adaptive/terminal file
**12 passed**. Ruff and `git diff --check` pass. The final non-Docker suite reports
**1,276 passed, 1 failed, 5 skipped, 4 deselected** in 214.81 seconds. The sole
failure is the unchanged Windows
`tests/test_interactive_diff.py::test_terminal_width_fallback_when_narrow`
assertion (`"fallback" in text`), already present before this revision. No new
full-suite failure appeared. Docker Desktop's Linux engine pipe is absent, so
Docker security could not be rerun. The terminal privacy tests cover redacted
approval and display commands while the executor receives the original, plus
redacted collapsed and expanded output, echoed credentials and the durable
execution snapshot. Pattern-based sanitization cannot prove that an arbitrary,
previously unknown secret format in tool output will be recognized. This release
remains **READY FOR 0.1.0 WITH DOCUMENTED LIMITATIONS** for merge review; the
local full suite is not completely green.
