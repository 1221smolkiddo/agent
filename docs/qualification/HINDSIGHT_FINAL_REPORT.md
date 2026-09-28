# Agent47 + Hindsight final qualification

**PRODUCTION READY WITH DOCUMENTED LIMITATIONS**

Both release-blocking redaction regressions are fixed, all 520 focused tests pass,
and the isolated first-time bug makes zero automatic recalls. All 16 scripted fixture
runs remain verified. The full non-Docker suite was run once: 1,217 passed, one known
unrelated Windows terminal-width failure, five skipped, four Docker deselected.
Live Hindsight, real-model A/B/C, and live Docker qualification remain unavailable.
The branch is ready for human review and merge with these recorded limitations.
No main merge or push was performed.

## 1. Source and branch identity

Branch: `qual/hindsight-production-20260928`.
Worktree: `.code-agent/hindsight-qualification`.
Final integration core base: `3a6be181d99b6ef813b119b2076c90eb73783b47`.
Previously stopped qualification base: `2fdcd90f35414049fe13aff6c1ff948d84905114`.
This final commit adds only the shared secret-redaction fix, its regressions, and
qualification evidence to that base. The SHA of the containing commit is the final
qualification branch SHA (`git rev-parse HEAD` after commit).
Exact tested redaction source blob: `b33920c9d7c3832f5d5bc9b1fc866481c8f90b9e`.
The new regression-test SHA-256 is in [FINAL_CORE_SECURITY_GATE.json](FINAL_CORE_SECURITY_GATE.json).
P6 `5b3622a` is preserved in ancestry. Original P6/pre-core artifacts remain historical.

## 2. Architecture and change scope

Agent47 SQLite runtime/evidence storage remains authoritative. Optional Hindsight
stores sanitized engineering experiences, selective provenance-aware recall, and
bounded Reflect recovery advice. Recall/Reflect output is untrusted advice; current
repository evidence, authorization, verification, reviewer outcomes, and lifecycle
rules retain authority. Deterministic operation identity and durable reconciliation
remain unchanged.
The only final behavior change is `src/code_agent/safety.py`, shared by tool-output
redaction and `MemorySanitizer`. Recall/Reflect policies, planner ordering, runtime,
outbox semantics, memory contracts, terminal rendering, and
`docs/EXPERIENCE_MEMORY.md` were not modified by the final blocker fix.

## 3. Root causes and sanitizer fix

The old explicit bearer matcher required 16 token characters. For shorter values,
the later memory credential rule consumed the scheme word while leaving the credential
tail. Explicit Authorization/Bearer credentials now redact any nonempty allowed-token
length, case-insensitively, with horizontal-spacing and colon/equal variations.
Canonical long `Bearer` scheme shorthand remains supported. Lowercase ordinary prose
without an Authorization header is preserved.

Standalone JWTs had no matcher and short dot-separated fields escaped the opaque-value
rule. The shared redactor now matches three bounded base64url fields (header 8–1,024,
payload 2–8,192, signature 1–2,048 characters), with boundaries preventing partial
matches inside oversized fields. It decodes only the bounded header and requires a
JSON JOSE object with an algorithm string. This distinguishes normal versions,
filenames, domains, and dotted identifiers. It detects secret material and does not
verify JWT authenticity. Decode errors and malformed candidates remain ordinary text.
No literal fixture value is special-cased; safe surrounding diagnostic text remains.

## 4. Security/redaction results

The two unchanged previously failing cases were reproduced individually, then passed
first after the fix: **2 passed in 1.43 seconds**.
The complete redaction/operator security gate passed **134 tests in 10.64 seconds**:
`test_secret_redaction.py`, `test_safety.py`, `test_hindsight_final_qualification.py`,
and `test_memory_qualification.py`.
There are 48 new parametrized security cases covering short/long bearer credentials,
header casing/spacing, several generated JWTs, safe diagnostic surroundings, existing
API keys/passwords, ordinary bearer prose, versions/files/domains, and bounded malformed
inputs. Existing provider-error, inspection, log, telemetry, report and qualification
privacy checks remain strict and pass. Category-only probes pass for all five fake
credential classes; no raw fake credential values or returned episode text is in these
release records. [Security evidence](FINAL_CORE_SECURITY_GATE.json).

## 5. Recall negative and positive controls

The isolated first-time arithmetic-bug B arm made **zero** automatic recalls; the
recurring parser-bug B arm made **one**. Both A arms made zero, and all four control
runs were verified. Full fixtures reconfirm these counts. Structured recurring history
and mechanical exclusions also pass the focused recall-policy/coordinator tests.
[Control results](FINAL_RECALL_CONTROLS.json).

## 6. Authority boundaries

The 520-pass focused suite includes recalled injection before planning, untrusted
memory visibility, current repository planning evidence, Reflect injection quoting,
real shell-approval refusal, verification/reviewer authority, incomplete-runtime
outcomes, and reflection success claims against current failed tests. Memory cannot
grant permissions, establish VERIFIED, or replace current tests/reviewer evidence.
No extra model call classifies recall eligibility. The memory/Reflect authority tests
pass without changing runtime or policy behavior.

## 7. Operator and outbox qualification

All operator/outbox focused tests pass. Status plus explicit health distinguishes
memory disabled, missing dependency/configuration, provider unavailable/timeout, and
healthy. Queued/prepared, submitted, processing, completed, failed, cancelled, and
unknown/operator-review states remain distinguishable. Inspection omits payload,
branch, HEAD, and raw exceptions. Safe retry preserves deterministic operation identity
and refuses blind resubmission of acknowledged-but-missing or operator-review entries.
No broad purge/delete functionality was added. Reconciliation, leasing, bounded retry,
crash/restart, terminal operation, and privacy regressions pass.

## 8. Deterministic fixture evaluation

**16/16 verified, zero runner errors, zero irrelevant recalls.**
Both arms use identical scripted responses in fresh fixture copies and databases.
A disables memory and B enables selective recall. The generic harness supplies
Reflect-disabled environment flags, but this offline scripted runner constructs its
configuration directly and uses the current enabled-memory Reflect default. None
of these successful repairs escalates to Reflect; the observed count is zero in both
arms. These fixtures therefore measure successful-run recall/verification wiring,
not independent Reflect disablement, failure recovery, real-model superiority, or
live provider reliability. Reflect disablement/recovery is covered by the focused tests.

| Scenario | A/B verified | A/B recalls | A/B Reflect | B irrelevant recalls |
| --- | --- | --- | --- | --- |
| repeated_bug_class | True/True | 0/1 | 0/0 | 0 |
| recurring_ci_failure | True/True | 0/1 | 0/0 | 0 |
| rejected_approach_later | True/True | 0/1 | 0/0 | 0 |
| multi_session_migration | True/True | 0/1 | 0/0 | 0 |
| release_rollback_lesson | True/True | 0/1 | 0/0 | 0 |
| architecture_decision_recall | True/True | 0/1 | 0/0 | 0 |
| simple_rename | True/True | 0/0 | 0/0 | 0 |
| isolated_simple_bug | True/True | 0/0 | 0/0 | 0 |

Model/tool/read counts and per-run latencies are in
[FINAL_FIXTURE_RESULTS.json](FINAL_FIXTURE_RESULTS.json). Token counts are unavailable.
The older isolated-bug recall artifact remains explicitly PRE-CORE-FIX historical
rather than being rewritten to invent a passing earlier result.

## 9. Live Hindsight

**LIVE HINDSIGHT: NOT RUN — configuration unavailable**

No keys were requested, printed, or persisted. No live provider write was made.
The prepared synthetic health/retain/poll/recall-identity/Reflect smoke is tested with
fake providers and emits only safe IDs, statuses, latencies and counts. A future live
pass would qualify the provider flow within that scope. Missing credentials alone is
a limitation rather than a correctness failure.

## 10. Real-model A/B/C

**REAL MODEL A/B/C: NOT RUN — provider/configuration unavailable**

Six synthetic scenarios remain prepared: repeated bug, recurring CI failure,
migration continuation, architecture-decision recall, isolated bug, and mechanical
rename. A disables memory, B enables recall only, C enables recall+Reflect; ordering
rotates by scenario. Reviewed real runner/model, independent equivalent frozen B/C
banks, seed review and externally enforced provider spend ceiling remain unavailable.
No credits were spent and no model-quality conclusions are claimed. Before paid use,
record this containing fix commit as the approved core SHA and verify all prerequisites.
The existing bounded single-trial guards remain active.
[Held evaluation definition](REAL_MODEL_EVALUATION.md).

## 11. Disabled-path measurement

Seven real factory/runtime construction samples, seven scripted read-only runs,
10,000 disabled hot-path iterations, and seven fresh CLI processes per startup arm
recorded **zero SDK imports, network/DNS attempts, automatic recalls, and Reflect calls**.
The real factory/storage/preflight use a stub model; no paid model/provider request
occurs. Cleanup/collection stays outside timed intervals.

| Measurement | Median | Min | Max |
| --- | ---: | ---: | ---: |
| Agent construction | 91.12 ms | 87.27 ms | 101.98 ms |
| Scripted read-only run | 369.58 ms | 336.06 ms | 381.02 ms |
| Baseline CLI import | 1152.10 ms | 1124.66 ms | 1169.44 ms |
| Qualification CLI import | 1146.52 ms | 1119.59 ms | 1169.09 ms |

Observed median startup delta: -5.58 ms. The startup baseline
removes only P6 CLI registration/imports from current code. Local timing noise and
other machine work affect observations; no acceptance threshold was predefined and
no precision or speed superiority is claimed.
[Post-fix benchmark](POST_FIX_DISABLED_BENCHMARK.json).

## 12. Focused qualification

**520 passed in 83.14 seconds**, zero failures/errors/skips, across 18 files covering
experience memory, retention, outbox/reconciliation, recall/policy/staleness,
Reflect/policy/authority, operator/evaluation/security, durable runtime/recovery,
configuration/path safety, and execution CLI. The 134-pass security gate is a separately
run overlapping subset, not an additional 134 unique focused tests.
[Machine-readable status](FINAL_QUALIFICATION_STATUS.json).

## 13. Full suite — run once

**1,217 passed, 1 failed, 5 skipped, 4 deselected in 224.16 seconds.**
The only failure is
`tests/test_interactive_diff.py::test_terminal_width_fallback_when_narrow`.
The narrow-terminal rendered output lacks the expected fallback text, matching the
pre-existing Windows baseline/environment failure documented by Session A. All
Hindsight tests pass; no new Agent47/Hindsight failure appeared. Terminal code is
unchanged. The full suite was run once after green security/recall/focused gates.

Commands used the existing shared environment and this checkout's `PYTHONPATH=src`;
`--no-sync --active` avoids dependency installation or switching environments:

```text
uv run --no-sync --active ruff check src tests
git diff --check
uv run --no-sync --active pytest -m "not docker_security" -q --tb=short --junitxml=.code-agent/qualification-full.xml
```

Raw local JUnit scratch files are ignored and are not committed; only safe aggregate
release evidence is tracked. The known failure is documented, not suppressed.

## 14. Static checks and artifact privacy

Ruff over `src tests` and `git diff --check` passed. Final staging is limited to the
shared redactor, its regression tests, and qualification documentation/results.
Fake-credential artifact scans and staged-path checks exclude credentials, DBs, caches,
virtualenvs and temporary smoke workspaces. Protected policy/runtime/outbox/terminal
paths remain unchanged. [Artifact scan](FINAL_ARTIFACT_SECURITY.json).

## 15. Docker and other qualification limitations

Docker CLI is installed, but a bounded daemon probe reports unavailable; live Docker
security tests were not run and four were deselected. Live Hindsight and real-model
A/B/C remain unavailable. The five skipped tests and known terminal failure limit
full-environment coverage. Scripted successful-repair fixtures do not exercise Reflect escalation or independently
validate the intended B-arm Reflect-disable flag; the focused Reflect tests cover those
boundaries. Scripted fixtures/stub-model benchmarks do not prove general model-quality
or deployment performance. Heuristic recall, provenance/staleness and
bounded secret detection retain documented limits; redaction is not a proof that
arbitrary obfuscated/oversized credential formats are absent.

## 16. Git and merge readiness

Changes are isolated on the qualification feature branch. No main merge or push was
performed. The final fix/evidence commit preserves P6 and the supplied core; Git status
is checked clean after commit. This is ready for human review and merge with the
recorded limitations; it does not authorize automatic merging or deployment.

## 17. Production verdict

**PRODUCTION READY WITH DOCUMENTED LIMITATIONS**

The exact redaction blockers, isolated-bug control, focused Hindsight/security/authority
and outbox gates, and disabled path pass. The full suite has only the allowed unrelated
Windows baseline failure. Missing live provider, real-model, and Docker evidence is
reported explicitly, with no fabricated success or performance claim.
