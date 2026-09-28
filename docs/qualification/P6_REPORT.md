# P6 Hindsight qualification report

## Scope

P6 is isolated on branch `p6-production-qualification` at P4 revision
`98a2993`, in `.code-agent/p6-worktree`. No commit was created.
Shared P5 Reflect edits were preserved in the original workspace.
Core planning, recovery, and Reflect execution were not changed in P6.

## 1. Evaluation harness design

`memory evaluate` accepts a reviewed scenario manifest and an instrumented runner.
A uses disabled memory; B uses selective recall. Both explicitly disable Reflect.
Each arm receives a fresh fixture copy and its own run database, with alternating
arm order and optional bounded repeated trials. The runner writes observed metrics
to a dedicated JSON file. Only allowlisted booleans/numbers enter the results;
missing metrics remain null. Stdout/stderr, tasks, secrets, and provider payloads
are omitted. The harness saves JSON and a concise sibling Markdown report.
A future C arm is reserved for P5.

The offline smoke uses the real CodingAgent, durable runtime, file tools, and
pytest verification with explicitly synthetic seeded historical lessons and
identical scripted responses in both arms. It qualifies wiring and counters.
It is not a live Hindsight or real-model outcome comparison.

## 2. Scenarios and measured smoke observations

One paired trial was run for each of the eight scenarios (16 agent runs).
All repairs reached the verified episode outcome and matched the expected fixture
file. First-pass verification was true in every run. Token counts were unavailable.

| Scenario | A/B verified | A/B model calls | A/B tool dispatches | A/B recalls | B irrelevant recalls |
| --- | --- | --- | --- | --- | --- |
| repeated_bug_class | True/True | 3/3 | 8/8 | 0/1 | 0 |
| recurring_ci_failure | True/True | 3/3 | 8/8 | 0/1 | 0 |
| rejected_approach_later | True/True | 3/3 | 8/8 | 0/1 | 0 |
| multi_session_migration | True/True | 3/3 | 6/6 | 0/1 | 0 |
| release_rollback_lesson | True/True | 3/3 | 8/8 | 0/1 | 0 |
| architecture_decision_recall | True/True | 3/3 | 8/8 | 0/1 | 0 |
| simple_rename | True/True | 3/3 | 6/6 | 0/0 | 0 |
| isolated_simple_bug | True/True | 3/3 | 8/8 | 0/1 | 1 |

Rename skipped recall. The isolated simple bug recalled an unrelated seeded lesson,
which exposes the broad P4 repair rule. P6 records this finding without changing
recall policy. The migration fixture is a continuation against seeded historical
context; a real multi-session migration remains to be qualified.

Median agent-run latency across these heterogeneous fixtures was
2.630s
for A and
3.574s
for B. These are observations from one scripted smoke trial, not comparative
performance evidence. Other tests were running on the same machine.

Artifacts: [JSON results](P6_SMOKE_RESULTS.json), [human summary](P6_SMOKE_RESULTS.md).
No memory superiority claim is justified by this smoke run.

## 3. Metrics

Implemented fields: verified completion; first-pass verification; model decisions
through verified finalization; actual model calls; actual tool dispatches;
repository read/search/list/map dispatches; recorded verification commands;
repeated identical failed action signatures; recall latency; UTF-8 memory-context
bytes; stale recall count; fixture-labeled irrelevant recall count; total agent
latency; recall requests; input/output tokens when supplied.

Internal filesystem reads are not claimed as measured repository tool reads.
Semantic strategy repetition and relevance for real tasks need a reviewed rubric.
The external runner supplies real-model observations; absent values are not inferred.

## 4. Operator commands

- `agent47 memory status`: complete counts, queued/scheduled/due work, review count,
  and oldest pending age.
- `agent47 memory health`: disabled, dependency missing, configuration error,
  provider unavailable, or healthy.
- `agent47 memory outbox --limit 50`: bounded metadata list with timestamps and leases.
- `agent47 memory inspect ID`: metadata only, never episode content.
- `agent47 memory retry ID`: one bounded reconciliation pass using the existing ID.
- `agent47 memory evaluate`, `evaluate-smoke`, and `benchmark`: qualification utilities.

Prepared maps to queued in diagnostics. Submitted, processing, completed, failed,
cancelled, and unknown remain distinguishable. Retry refuses terminal, exhausted,
expired-ambiguity, and acknowledged-but-missing review-only entries. It never
blindly resends an acknowledged operation and introduces no delete/purge command.

## 5. Live qualification mechanism

`agent47 memory live-smoke --bank-id EXISTING_BANK --timeout-seconds 60`
performs health, one synthetic asynchronous retain, bounded operation polling,
and recall. A pass requires the unique marker plus matching document and
repository-bank provenance. Each SDK deadline is capped by the remaining overall
timeout. Ambiguous retains are not automatically resubmitted. Failure output
contains fixed statuses and generated IDs, not raw errors.

Cloud and self-hosted configuration use the existing optional SDK adapter.
The command is opt-in and is never invoked by normal CI. Unit coverage uses fakes
without real keys. No deletion is performed; synthetic memory remains in the bank.
A real deployment was not tested. The observed local health configuration was
`disabled`, so provider indexing and operation-retention-window behavior remain
unqualified.

## 6. Security audit findings

Two gaps were fixed: provider provenance/source identifiers could leak through
representations, and an injected provider could return a raw operation error.
Provenance and prepared-episode representations now redact payload/identifier
data; source identifiers/metadata are excluded from repr. Operation errors
normalize to fixed codes. Operator output validates identifiers and state/error
labels and hides payloads. Invalid configuration diagnostics hide input values.

Tests exercise a configured API key, bearer token, authorization header, embedded
diagnostic credential, and embedded provider error across logs, exceptions,
SQLite outbox rejection, telemetry, saved work reports, CLI diagnostics, and
representations. Existing retention-sanitizer checks also passed.
Heuristic filtering cannot prove absence of unknown or encoded secrets; SDK debug
logging remains outside the facade boundary.

## 7. Disabled-path performance findings

[Raw benchmark](P6_BENCHMARK.json): Windows, Python 3.14.3,
10000 hot-path iterations, seven fresh-process samples per startup arm.

- Disabled service construction mean: 0.824 microseconds.
- Disabled recall decision mean: 3.367 microseconds.
- Guarded socket/DNS attempts: 0.
- Guarded optional SDK import attempts: 0.
- Disabled recall/context: false / 0 bytes.
- Trivial rename recall: false; isolated bug recall: true.
- Eligibility classification has no model interface or model call. Measured scripted
  model calls remained three per arm.
- CLI startup median baseline: 1.1614s;
  P6: 1.1653s; delta:
  3.97ms.

The startup baseline removes only P6's CLI import/registration from the same source.
No acceptance threshold was specified in advance; none was invented after measurement.
The small observed median delta is not a formal startup regression qualification.

## 8. Documentation changes

`EXPERIENCE_MEMORY.md` now describes the architecture, supported cloud/self-hosted
configuration, existing retention/recall/provenance semantics, outbox recovery and
operator commands, privacy boundary, live smoke, evaluation runner contract,
offline fixtures, measurement definitions, and performance limitations.
IMPLEMENTED is separated from FUTURE / ROADMAP. No benefit or exactly-once claim
was added.

## 9. Files changed

Modified:

- `docs/EXPERIENCE_MEMORY.md`
- `src/code_agent/cli.py`
- `src/code_agent/experience_memory/contracts.py`
- `src/code_agent/experience_memory/episode_sanitizer.py`
- `src/code_agent/experience_memory/outbox.py`
- `src/code_agent/experience_memory/service.py`

Added:

- `src/code_agent/memory_operator.py`
- `src/code_agent/memory_eval.py`
- `src/code_agent/memory_eval_runner.py`
- `src/code_agent/memory_benchmark.py`
- `tests/test_memory_qualification.py`
- `docs/qualification/P6_SMOKE_RESULTS.json`
- `docs/qualification/P6_SMOKE_RESULTS.md`
- `docs/qualification/P6_BENCHMARK.json`
- `docs/qualification/P6_REPORT.md`

## 10. Tests added

19 qualification tests cover pairing/order and absent metrics; disabled import and
provider behavior; rename selection; health states; invalid configuration secrecy;
API/bearer/header rejection before SQLite persistence; provider-error secrecy;
recall secrecy in model context, telemetry, reports and logs; provenance reprs;
manual retry identity and acknowledged-missing refusal; all-state outbox summaries
and stuck progress; live smoke identity, wrong bank, provider error, and bounded
polling; finite numeric metrics; and complete eight-scenario catalog coverage.
No existing test was weakened, changed, or suppressed.

## 11. Focused test results

- New qualification file: **19 passed in 3.34s** in the independent checkout.
- Expanded memory/outbox/recall/retention/configuration/CLI/security/recovery slice:
  **346 passed in 40.74s**.
- Evaluation harness smoke: **16/16 verified fixture runs**, no runner errors.

## 12. Full suite result

`uv run pytest -q -m "not docker_security"`:
**1008 passed, 1 failed, 5 skipped, 4 deselected in 161.60s**.

Failure: `tests/test_interactive_diff.py:475`,
`test_terminal_width_fallback_when_narrow`. The 50-column Rich capture truncates
the expected fallback label. This failure was already documented in P1, and neither
that test nor the diff implementation changed in P6. The gate remains failed;
it was not waived or hidden.

Docker/security live checks: Docker client is installed, but the Docker Desktop
Linux engine pipe is absent. Four Docker security tests were deselected as requested;
all applicable non-Docker security tests were included. Docker qualification was
not practical in this environment.

## 13. Static checks

`uv run ruff check src tests`: passed.
`git diff --check`: passed.
The source snapshot was audited independently of concurrent P5 work.
The audit reused the existing root uv environment via `--project`; pytest's
configuration selected this worktree's source. CLI subprocess audits set
`PYTHONPATH` to this worktree's `src`.

## 14. Remaining production gaps

1. Real cloud/self-hosted smoke and provider operation-retention-window qualification.
2. Real-model paired trials with reviewed historical seeds, fixed settings, token
   accounting, and independent relevance/strategy scoring.
3. Isolated-bug negative control currently recalls irrelevant history under P4.
4. A predefined startup regression budget and representative deployment performance
   trials; one local timing sample is not a production gate.
5. The documented existing Windows narrow-terminal test failure.
6. Safe cleanup/deletion is not designed here; synthetic live data persists.
7. Provenance/staleness/privacy remain heuristic, and SDK cancellation depends on
   cooperative behavior. Shared live-bank retention can contaminate A/B trials
   unless the external runner freezes historical data.

## 15. git diff --stat

Tracked-file output; untracked additions are listed in sections 9 and 16.

```text
 docs/EXPERIENCE_MEMORY.md                          | 148 ++++++++++++++++++++-
 src/code_agent/cli.py                              |   2 +
 src/code_agent/experience_memory/contracts.py      |   9 +-
 .../experience_memory/episode_sanitizer.py         |   6 +-
 src/code_agent/experience_memory/outbox.py         |  72 +++++++++-
 src/code_agent/experience_memory/service.py        |  10 +-
 6 files changed, 228 insertions(+), 19 deletions(-)
```

## 16. git status

Branch: `p6-production-qualification`. No staged changes; no commit.

```text
 M docs/EXPERIENCE_MEMORY.md
 M src/code_agent/cli.py
 M src/code_agent/experience_memory/contracts.py
 M src/code_agent/experience_memory/episode_sanitizer.py
 M src/code_agent/experience_memory/outbox.py
 M src/code_agent/experience_memory/service.py
?? docs/qualification/
?? src/code_agent/memory_benchmark.py
?? src/code_agent/memory_eval.py
?? src/code_agent/memory_eval_runner.py
?? src/code_agent/memory_operator.py
?? tests/test_memory_qualification.py
```
