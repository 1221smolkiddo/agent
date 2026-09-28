> Archived pre-core preparation snapshot on P6 `5b3622a`; see
> `HINDSIGHT_FINAL_REPORT.md` for the final-core failed handoff.

# Agent47 + Hindsight final qualification — DRAFT

**Draft verdict: NOT PRODUCTION READY.** Final core handoff has not happened.
Two confirmed sanitizer regressions on the P6 base are correctness/security blockers.
Missing real-model A/B/C alone is not treated as a correctness failure. No live or
model-quality success is claimed, and no final-core results are invented.

## Pending final evidence

| Evidence | Status |
| --- | --- |
| Final core SHA | PENDING — Session A handoff |
| Isolated/simple-bug false-positive fix | PENDING — must measure zero automatic recall |
| Short bearer / standalone JWT fixes | PENDING — core owner |
| Final focused qualification tests | PENDING — after core handoff |
| Final exact-state non-Docker suite | PENDING — once, if required by changed code state |
| Final real-model A/B/C results | NOT RUN — pending core, reviewed runner/seeds, enforced cost limit |

## 1. Qualification isolation

Branch: `qual/hindsight-production-20260928`.
Worktree: `.code-agent/hindsight-qualification`.
Preserved committed P6 base: `5b3622a2a60613a84a1a72893a032c0151e2910a`.
Session A's `.code-agent/hindsight-final` worktree was not operated in.
No core planning/recall/Reflect/runtime/outbox lifecycle file was edited, and
`docs/EXPERIENCE_MEMORY.md` was left untouched. No merge into main, push, staging,
or qualification commit was performed. Original P6 artifacts remain historical evidence.

## 2. Operator tooling verification

The CLI exposes memory status, health, bounded outbox list, inspect, and bounded retry.
Health tests distinguish disabled, missing configuration, missing optional SDK,
provider unavailable/timeout, and healthy. Status reports configuration availability
and local queue information; use the explicit health command to check the provider.
Queued/prepared, submitted, processing, completed, failed, cancelled, and
unknown/operator-review states remain distinguishable. Retry reconciles first and
preserves the same operation identity; acknowledged-but-missing, review-only, terminal,
and exhausted operations are refused. No purge/delete command exists.

Qualification-only fixes: inspection now sanitizes even structurally valid identifiers
against the configured key; malformed health statuses become fixed unavailable output;
malformed or huge numeric runner metrics fail closed. CLI/provider errors discard raw
payload/error text. These changes do not modify outbox lifecycle semantics.

## 3. Security qualification

Five fake-credential categories were tested against CLI error/exception/log output,
qualification JSON/human reports, agent telemetry and saved work reports. These
operator/artifact surfaces passed. Retained episode content, branch, and HEAD remain
absent from inspection. Normal coding tests observed two scripted model calls with no
extra eligibility-classification call. Guarded disabled runs import no Hindsight SDK
and make no network or automatic memory call.

Core sanitizer probes fail for a short authorization bearer tail and a standalone JWT.
API-key, environment-assignment, and password cases pass. The two failures are kept as
strict regression tests rather than skipped/waived. Their values are omitted from
reports, exception messages, and qualification JSON. Session A owns the fixes:
[CORE_HANDOFF_NOTES.md](CORE_HANDOFF_NOTES.md) and
[category-only probe results](FINAL_SECURITY_PROBE.json).

## 4. Disabled-path benchmark

Seven real factory/runtime construction samples and seven scripted read-only agent
runs used the real storage/tools/preflight with a stub model. Seven fresh CLI processes
per startup arm guarded socket/DNS and SDK imports. 10,000 service/decision hot-path
iterations were also measured. Database cleanup and collection are outside timed
intervals. No real model or provider request was made.

| Measurement | Median | Min | Max |
| --- | ---: | ---: | ---: |
| Agent construction | 83.74 ms | 82.11 ms | 92.45 ms |
| Scripted read-only run | 352.47 ms | 338.70 ms | 359.60 ms |
| Baseline CLI import | 1166.02 ms | 1135.16 ms | 1353.25 ms |
| Qualification CLI import | 1175.03 ms | 1125.60 ms | 1207.54 ms |

Observed startup median delta: 9.02 ms.
Network/DNS attempts, Hindsight SDK import attempts, automatic recalls, and automatic
Reflect attempts were all **zero**. The startup baseline removes only P6 CLI
import/registration from current source; it is not an earlier-version production
baseline. Other machine work can affect these observations. No acceptance threshold
was predefined and no precision/superiority guarantee is claimed.
[Raw benchmark](FINAL_DISABLED_BENCHMARK.json).

## 5. Deterministic fixture evaluation

**16/16 verified, zero runner errors.** A/B use identical scripted responses on fresh
fixture copies and local databases. This measures wiring/counters, not real-model
reasoning or live provider behavior. Token counts and automatic Reflect counts are
unavailable on this P6-only base. Both arms explicitly disable Reflect.

| Scenario | A/B verified | A/B recalls | A/B model calls | A/B tools | A/B repo reads | B irrelevant recalls |
| --- | --- | --- | --- | --- | --- | --- |
| repeated_bug_class | True/True | 0/1 | 3/3 | 8/8 | 2/2 | 0 |
| recurring_ci_failure | True/True | 0/1 | 3/3 | 8/8 | 2/2 | 0 |
| rejected_approach_later | True/True | 0/1 | 3/3 | 8/8 | 2/2 | 0 |
| multi_session_migration | True/True | 0/1 | 3/3 | 6/6 | 2/2 | 0 |
| release_rollback_lesson | True/True | 0/1 | 3/3 | 8/8 | 2/2 | 0 |
| architecture_decision_recall | True/True | 0/1 | 3/3 | 8/8 | 2/2 | 0 |
| simple_rename | True/True | 0/0 | 3/3 | 6/6 | 2/2 | 0 |
| isolated_simple_bug | True/True | 0/1 | 3/3 | 8/8 | 2/2 | 1 |

The isolated arithmetic bug still makes one B recall: **EXPECTED PRE-CORE-FIX**.
Rename makes zero recalls. No recall policy was changed to alter these results.
Per-run latency and other numeric observations are retained in the
[raw fixture results](FINAL_PRE_CORE_SMOKE_RESULTS.json) and
[human summary](FINAL_PRE_CORE_SMOKE_RESULTS.md).

## 6. Live Hindsight qualification

**LIVE HINDSIGHT: NOT RUN — configuration unavailable.**
Supported Settings/environment/dotenv checks found memory disabled and no Hindsight
key. No secret values were printed, requested, or persisted. No live write occurred.
[Configuration flags](FINAL_CONFIGURATION_STATUS.json) and
[held live status](LIVE_HINDSIGHT_STATUS.json).

The prepared CLI live smoke defaults to Reflect and performs health -> one synthetic
retain -> bounded operation polling -> recall with matching document/bank provenance
-> explicit low-budget provider Reflect. It reports only fixed statuses, generated IDs,
latencies, and returned counts. SDK deadlines share the whole-run deadline. It never
prints returned memory/reflection text and does not resubmit ambiguous retain.
Fake-provider tests verify ordering, identity, Reflect timeout, and output privacy.
`--no-reflect` preserves the earlier provider-only subset. A pass would qualify the
provider flow, not Agent47's integrated recovery. Synthetic memory is not deleted.

## 7. Real-model A/B/C preparation

**NOT RUN.** Six synthetic scenarios and fixtures are prepared in
[REAL_MODEL_ABC_MANIFEST.json](REAL_MODEL_ABC_MANIFEST.json), with
[seed/golden review inventory](REAL_MODEL_SEED_REVIEW.json).
A=disabled, B=recall enabled/Reflect disabled, C=recall+Reflect; ordering rotates by scenario.
The external harness supports C and numeric Reflect request/latency measurements.
Legacy A/B behavior is preserved. The offline scripted runner is deliberately refused
for C so it cannot be mistaken for a real-model Reflect result.

One trial has 18 runs, six steps per run, 1024 output tokens, 8000 context characters,
90 seconds per run, no reviewer/model retries/fallbacks, and deterministic planning.
Paid execution is rejected before launch unless the approved full core SHA is present
and core files match it, seeds are reviewed, and an externally enforced provider
spending ceiling of at most USD 5 is attested. Step/token/wall limits do not substitute
for the provider spend control. The real runner/model, independently frozen equivalent
B/C banks, seed review, and actual cost-control evidence remain unconfigured.
The definition is a concrete held preparation, not trial results.
[Runner contract and limitations](REAL_MODEL_EVALUATION.md).

## 8. Final core SHA used

Final SHA: **PENDING**. All observations above are pre-handoff on committed P6
`5b3622a` plus uncommitted qualification-only changes. They are not final-core evidence.

## 9. Negative control after core handoff

**PENDING.** Rebase/merge the isolated qualification branch onto Session A's supplied
core SHA, resolving core policy/Reflect/planner/runtime/outbox conflicts in favor of
Session A and preserving non-conflicting qualification tooling. First run the
isolated/simple-bug case and require zero automatic recall. If it still recalls,
stop final qualification and return the finding to Session A. Do not fix policy here.
Then verify the two redaction regressions and rerun final qualification.

## 10. Focused qualification tests

The 14-file operator/security/disabled/memory/recovery/configuration/CLI focused gate:
**304 passed, 2 failed in 44.53 seconds**. Both failures are the strict core sanitizer
regressions described above; all 19 original P6 qualification cases pass. There are
35 added parametrized qualification cases, including five classes across actual agent
telemetry/work reports/logs and two deliberately failing core-gap checks.
The final dedicated operator/security gate: **52 passed, 2 failed in 10.47 seconds**;
these are the same two core sanitizer gaps. Six added paid-trial guard cases refuse
execution before any command launches. No existing test was weakened, suppressed, or edited.
After adding a synthetic engineering lesson to the provider smoke payload, its six
regression checks passed (1.71 seconds); the final Ruff and whitespace checks passed.

## 11. Full-suite evidence

Not rerun before final core handoff, as requested. Historical committed P6 evidence:
1008 passed, 1 known unrelated narrow-terminal failure, 5 skipped, 4 deselected.
This is not a new exact-state full-suite result. Docker was previously unavailable
and no Docker/live sandbox qualification is claimed. After handoff, run the full
non-Docker suite once if Session A has not supplied a clean result on identical code
or the qualification rebase/code changes require it. Final result: **PENDING**.

## 12. Static checks

Ruff over `src tests`: **passed**. `git diff --check`: **passed**.
A protected-path diff confirms no changes to core behavior or the memory documentation
owned by Session A. Additional artifact scans reject the five fake-secret markers.

## 13. Remaining limitations

Confirmed short bearer/JWT sanitizer failures; pending core handoff and negative-control
fix verification; no live provider credentials/results; no reviewed real-model runner,
seeded independent B/C banks or cost-enforcement evidence; no final exact-state full
suite; the historical unrelated terminal test; Docker unavailable. Startup numbers
are local observations without a predeclared performance threshold. Historical
provenance/privacy and cooperative cancellation retain their documented limits.

## 14. Production verdict

**NOT PRODUCTION READY — DRAFT, pending core security fixes and final qualification.**
This verdict reflects observed sanitizer failures and missing final core validation,
not merely absence of real-model trials. Do not treat preparatory wiring/fixture
success as final production or model-quality approval.

## 15. git status

No main merge or push. Changes remain uncommitted in the isolated qualification
worktree. The following is a snapshot taken at report generation:

```text
 M src/code_agent/memory_benchmark.py
 M src/code_agent/memory_eval.py
 M src/code_agent/memory_eval_runner.py
 M src/code_agent/memory_operator.py
?? docs/qualification/CORE_HANDOFF_NOTES.md
?? docs/qualification/FINAL_ARTIFACT_SECURITY.json
?? docs/qualification/FINAL_CONFIGURATION_STATUS.json
?? docs/qualification/FINAL_DISABLED_BENCHMARK.json
?? docs/qualification/FINAL_PRE_CORE_SMOKE_RESULTS.json
?? docs/qualification/FINAL_PRE_CORE_SMOKE_RESULTS.md
?? docs/qualification/FINAL_QUALIFICATION_STATUS.json
?? docs/qualification/FINAL_SECURITY_PROBE.json
?? docs/qualification/HINDSIGHT_FINAL_REPORT.md
?? docs/qualification/LIVE_HINDSIGHT_STATUS.json
?? docs/qualification/REAL_MODEL_ABC_MANIFEST.json
?? docs/qualification/REAL_MODEL_EVALUATION.md
?? docs/qualification/REAL_MODEL_SEED_REVIEW.json
?? docs/qualification/real_fixtures/
?? tests/test_hindsight_final_qualification.py
```
