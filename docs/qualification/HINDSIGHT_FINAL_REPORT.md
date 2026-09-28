# Agent47 + Hindsight final qualification — STOPPED AT SECURITY GATE

**NOT PRODUCTION READY.** Both mandatory redaction regressions still fail on the
supplied final core. Final qualification stopped as instructed; the tests were not
weakened and no core code was changed. The branch is preserved for the core owner's
fix and another handoff, and is not ready for merge approval.

## 1. Final core SHA

`3a6be181d99b6ef813b119b2076c90eb73783b47` from
`feat/hindsight-production-final`. Rebase completed without conflicts. Protected core
files and `docs/EXPERIENCE_MEMORY.md` match this commit. The loaded sanitizer was
confirmed to come from this qualification checkout and to match the core source,
allowing only Windows checkout line-ending conversion.

## 2. Qualification branch and SHA

Branch: `qual/hindsight-production-20260928`.
Worktree: `.code-agent/hindsight-qualification`.
Qualification source/test commit evaluated: `7a186e4d95a055f6cfdc7de9a72cff0a6ea281f7`.
Its parent is the final core. The uncommitted preparation was first preserved as
checkpoint `d9445e9`, then rebased. A subsequent documentation/evidence-only commit
records this stopped handoff; use `git rev-parse HEAD` for that commit's identity.
P6 `5b3622a` remains in the final core's ancestry. No older core files were restored.

## 3. Architecture summary

Agent47's SQLite run/evidence storage remains authoritative. Optional Hindsight
provides sanitized engineering experiences, selective provenance-aware recall, and
bounded Reflect recovery advice. Recalled/Reflect content is untrusted historical
advice; current repository evidence, runtime authorization, tests, and reviewer
outcomes retain their authority. Retention uses deterministic operation identity and
durable outbox reconciliation. Qualification changes cover operator display/privacy,
provider smoke, metrics, benchmark guards, and held A/B/C definitions.
This summary describes the supplied core; it does not claim all final gates passed.

## 4. Recall negative control

**NOT RUN after handoff — mandatory security stop.** The historical P6 isolated-bug
result was one automatic recall and remains explicitly PRE-CORE-FIX. Session A reports
the fix landed; this branch has not yet independently verified zero recall on final
core. The recurring-bug positive control and authority gates are also not newly run.

## 5. Security/redaction gate

**FAILED.** Exact failing cases:

- `tests/test_hindsight_final_qualification.py::test_core_sanitizer_removes_fake_credentials[short_bearer]`
- `tests/test_hindsight_final_qualification.py::test_core_sanitizer_removes_fake_credentials[jwt]`

A short Authorization bearer tail and a standalone JWT remain in the sanitized
result. API-key, environment-assignment, and password redaction cases passed. All five
selected operator/provider-error secrecy cases and configured-key inspection passed.
The selected disabled factory/benchmark test also passed.

The command was run with the shared existing Python environment and `PYTHONPATH=src`:

```text
python -m pytest tests/test_hindsight_final_qualification.py -k "core_sanitizer or provider_error or inspection or benchmark" -q --tb=short
```

Result: **10 passed, 2 failed, 23 deselected in 4.69 seconds**. Fake values are omitted
from failure messages and these reports. A category-only diagnostic confirms the same
failures in source identical to the final core:
[FINAL_CORE_SECURITY_GATE.json](FINAL_CORE_SECURITY_GATE.json).
The required stop applies even though the supplied core's other security tests passed.
[Core-owner handoff notes](CORE_HANDOFF_NOTES.md).

## 6. Operator tooling

The critical subset passed provider-error secrecy and inspection privacy. The complete
operator suite was not rerun after the security stop. Historical pre-core evidence:
19 original P6 tests passed; CLI status/health, outbox states, stable retry identity,
acknowledged-but-missing retry refusal, and absence of purge/delete were covered.
Those historical results are not final-core operator approval.

## 7. Fixture evaluation

**NOT RUN on final core — security stop.** Historical scripted A/B results were 16/16
verified with zero runner errors, with the isolated-bug recall marked PRE-CORE-FIX.
Reflect counters were unavailable on that older base. Final counts and negative-control
fix verification remain pending. Fixture-only evidence makes no performance claim.
[Historical fixture results](FINAL_PRE_CORE_SMOKE_RESULTS.json).

## 8. Live Hindsight

**LIVE HINDSIGHT: NOT RUN — configuration unavailable**

The supported configuration probe before handoff found no Hindsight credentials and
memory disabled; this was not re-probed after the mandatory stop. The user also supplied
configuration unavailability in the final handoff. No credentials were requested,
printed, or persisted, and no live request or synthetic provider write occurred.
Missing live credentials alone is not the reason for the failed verdict.

## 9. Real-model A/B/C

**REAL MODEL A/B/C: NOT RUN — provider/configuration unavailable**

The six synthetic scenarios and A=disabled, B=recall, C=recall+Reflect definition remain
prepared. The definition now records the supplied final core SHA, but the redaction
gate blocks execution. Reviewed runner/model selection, frozen equivalent B/C banks,
seed review, and externally enforced spend-limit evidence also remain unavailable.
No paid trials or model-quality conclusions are claimed. Missing A/B/C alone is not
the reason for the failed verdict.

## 10. Disabled-path benchmark

The selected guarded real-factory benchmark regression passed on final core: SDK,
network, recall, and Reflect counters stayed zero. The full seven-sample measurement
was not rerun after the stop. Historical construction median was 83.74 ms, scripted
read-only run 352.47 ms, and observed CLI startup delta 9.02 ms, with zero forbidden
attempts. These are pre-core observations, not final-core timing or superiority claims.
[Historical benchmark](FINAL_DISABLED_BENCHMARK.json).

## 11. Focused qualification

Critical subset: **10 passed, 2 failed, 23 deselected (4.69 seconds)**. Full final focused
suite: **NOT RUN — mandatory redaction stop**. Session A's supplied 429 focused passes
are handoff evidence, not a new result from this qualification branch. Historical
pre-core gates were 304 passed/2 failed and dedicated 52 passed/2 failed. Their failures
were the same two redaction cases now confirmed on final core.

## 12. Full suite

**NOT RUN — critical gates failed.** The one-time full-suite run must wait for green
critical/focused gates. Session A supplied 1,134 passed, 1 known baseline failure,
5 skipped, and 4 Docker deselected on its core; this is not a new exact-state full-suite
result including this branch's additional qualification changes.

## 13. Ruff/diff-check

**Passed:** `uv run --no-sync --active ruff check src tests` using the existing shared
environment, and `git diff --check`. No source or test was modified after the failed
gate. The 44-file qualification artifact scan found zero fake-credential marker hits;
protected core/document diff remains empty. See `FINAL_QUALIFICATION_STATUS.json`
and `FINAL_ARTIFACT_SECURITY.json`.

## 14. Docker

Docker qualification remains unavailable/unrun. No live sandbox qualification is
claimed. The full suite was not launched in this stopped handoff.

## 15. Known baseline failure

Session A reports `test_terminal_width_fallback_when_narrow` as the known
pre-existing environment/baseline failure. It was not rerun here. Terminal code was
not modified, and this issue does not explain the two Hindsight redaction failures.

## 16. Remaining limitations and next handoff

The core owner must fix both exact redaction cases without waiving tests and supply a
new final core SHA. Rebase this branch onto that SHA, verify these gates, then run the
isolated-bug zero-recall and recurring-bug positive controls, authority/disabled gates,
operator suite, fixtures, focused tests, and one full non-Docker suite. Live/real-model
qualification remains conditional on already available supported configuration and
bounded cost. Historical performance and fixture results are limited to their scope.
No core fix, main merge, or push was made by qualification work.

## 17. Final production verdict

**NOT PRODUCTION READY**

The observed final-core redaction failures block approval. This is not a verdict based
solely on missing live credentials, real-model evaluation, or the unrelated terminal
failure. Final Git status and evidence commit identity are reported after committing
these qualification records; clean Git state does not imply readiness to merge.
