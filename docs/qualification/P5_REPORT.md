# P5 selective Reflect release report

P5 implementation is finished and uncommitted. The focused gate passes. The full
non-Docker gate has one independently reproduced pre-existing rendering failure;
no existing tests were weakened. Live Reflect qualification remains outstanding.

## 1. ReflectPolicy

`experience_memory/reflect_policy.py` makes deterministic escalation decisions after
normal diagnosis. The model does not choose whether to call Reflect.

## 2. Exact triggers and skips

Priority order: repeated normalized diagnostic; verification failure after a recorded
repair/replan; matching diagnostic history from prior runs; conflicting outcomes for
the same recalled document after an unresolved diagnosis; bounded recalled summaries
after an unresolved diagnosis. The last two require a previous recovery attempt.

Skip: disabled memory/provider/Reflect, zero budget, cancellation/denial, resolved
failure, missing diagnosis, permission/network/provider/infrastructure/environment
failure, syntax/style error or safe autofix, unsafe query, exhausted request budget,
no remaining step, exhausted failure budget, or insufficient remaining run time.
Skipped non-code/deterministic failures do not count as ordinary code recovery attempts.

## 3. Hindsight Reflect parameters

The installed, locked `hindsight-client` 0.10.1 signature was inspected. Dispatch uses
`areflect(bank_id, query, budget="low", max_tokens=768, response_schema=...)` by default,
plus `fact_types=["observation", "experience"]`, `exclude_mental_models=True`,
`reflect_search_observations_max_tokens=256`,
`reflect_search_observations_include_entities=False`, `include_facts=False`,
`include_tool_calls=False`, `include_tool_call_output=False`,
`apply_all_directives=False`, and fixed skeptical/literal advisory `context`.
Client retries are disabled with `max_attempts=1`. Cleanup shares the call deadline.
The schema bounds hypothesis length and permits at most five 120-character references.

SDK 0.10.1 offers no per-request skepticism/literalism disposition controls or strict
aggregate internal fact/chunk cap. Observation-search tokens are a default when its
model does not name a budget. Agent47 does not change bank configuration. Returned
fact bodies/traces are omitted, and returned text/references have strict local limits.
See [Reflect API](https://hindsight.vectorize.io/developer/api/reflect) and
[server configuration](https://hindsight.vectorize.io/developer/configuration).

## 4. Recovery integration

`CodingAgent.run_detailed` collects the existing normalized diagnosis, ordinary recovery
instructions, action/verification evidence, and failure-budget checks before calling
`_reflection_recovery_context`. A successful hypothesis becomes a separate quoted user
message for the next recovery decision. Successful edits and accepted plan updates
record strategies after a diagnosed failure. Passing verification clears tracking.
No extra recall occurs, and no execution engine lifecycle code was changed.

## 5. Security boundary

Immutable Agent47 request/result/hypothesis/decision/support types contain no SDK
objects. Sanitized structured queries contain only bounded task/diagnostic/strategy/
verification/history fields. Source declarations/fences, oversized dumps, secrets,
environment assignments, and interactive transcript wrappers are rejected or removed.
No raw tool output, transcript, source upload, environment, or hidden reasoning is sent.

The context explicitly says UNTRUSTED ADVISORY CONTEXT, data rather than instructions,
no permission/security/test/verification/completion authority, and current repository
and project.md precedence. Every reflected line is quoted. Actual approval callbacks,
confidence gates, verification records, and lifecycle decisions remain authoritative.
StalenessGuard assesses references matching already recalled facts and types. Stale
support is labeled stale; absent/unmatched support is unknown. Even checked support
is a historical inference requiring current validation, never proof of current success.

## 6. Budgets

Defaults: one automatic Reflect attempt per run, low reasoning, 768 answer tokens
and a conservative 768-byte local hypothesis ceiling, observation-search default 256,
five supporting references, 1,800-character query, five-second provider deadline,
and 3,000-character formatted context. Query uses last three strategies and two short
historical summaries. Tracking is bounded to 64 diagnostic signatures. Recall and
Reflect budgets are independent. Failures consume the attempt; passing verification
does not replenish it. Independent disable and validated limits are documented in
[EXPERIENCE_MEMORY.md](../EXPERIENCE_MEMORY.md).

## 7. Failure behavior and telemetry

Timeout/provider/malformed-response/sanitizer/staleness failure yields no context and
normal recovery continues. No raw provider error text reaches the model or telemetry.
`automatic_experience_reflect` stores attempted flag, fixed reason/status, latency,
supporting-reference count, and context size only. Query/hypothesis/credentials are
excluded. Missing configuration/dependency and disabled paths return typed statuses.

## 8. Files changed

Modified: `src/code_agent/agent.py`; memory `__init__.py`, `config.py`, `contracts.py`,
`providers/hindsight.py`, `providers/null.py`, `recall.py`, and `service.py`;
`docs/EXPERIENCE_MEMORY.md`.
Added: memory `reflect_policy.py`, `reflection.py`; `tests/test_reflect_policy.py`,
`tests/test_memory_reflect.py`; this release report.
The separately modified `src/code_agent/cli.py` was left untouched; it has no semantic
`git diff` at the report snapshot.

## 9. Tests added

62 new parametrized cases cover first/repeated/recurring failures, failed repair and
automatic verification, resolved failures, deterministic fixes and non-code skips,
separate recall/Reflect budgets, independent disable and fully disabled runs, SDK
parameters/deadline/cleanup, malformed responses, bounded sanitized queries/context,
stale/unknown support, next recovery context, retry cutoff, telemetry privacy, and
real approval denial. Five adversarial instructions (ignore system, leak keys, skip
tests, delete repository, mark complete) stay quoted untrusted data. A false success
claim after Reflect is blocked against actual failed tests without another tool call,
verified criteria, or completed durable execution.

## 10. Focused results

353 passed in 52.95 seconds. The focused command covered P1–P4 memory, retention,
outbox, recall policy/staleness, project memory, P5 policy/Reflect, agent recovery,
durable execution, adapters, state, migration, profiles, execution CLI, and planning
context. The confidence gate was exercised with existing current-repository evidence;
it was not disabled or weakened.

## 11. Full suite

`uv run pytest -m "not docker_security" -q`:
**1,051 passed, 1 failed, 5 skipped, 4 deselected** in 164.72 seconds.
Failure: `tests/test_interactive_diff.py::test_terminal_width_fallback_when_narrow`
expects `fallback` in the narrow console output but that header is truncated on this
Windows environment. Rendering source and test have no P5 diff. Loading the renderer
from `git show HEAD:src/code_agent/interactive_diff.py` into an isolated Python process
and rerunning the single test reproduced the same failure. Neither file was changed.
The full release gate is therefore not entirely green.

## 12. Static checks

`uv run ruff check src tests`: passed.
`git diff --check`: passed after removing an extra documentation EOF blank line.

## 13. Remaining concerns

Live cloud/self-hosted Reflect was not run. No measured reasoning-quality improvement
is claimed. The locked SDK's internal evidence/chunk/disposition limitations above
require trusted server settings for stronger limits; no unsupported SDK arguments
were invented. Provenance and secret/source filtering remain heuristic. Async timeout
relies on cooperative SDK cancellation. The unchanged narrow-terminal test remains
an unrelated full-suite release-gate failure.

## 14. git diff --stat

Tracked files only; the new policy, coordinator, tests, and this report are untracked
and consequently absent from this command's totals.

```text
 docs/EXPERIENCE_MEMORY.md                          |  86 ++++++++++++++++-
 src/code_agent/agent.py                            |  84 ++++++++++++++++-
 src/code_agent/experience_memory/__init__.py       |   3 +
 src/code_agent/experience_memory/config.py         |  24 +++++
 src/code_agent/experience_memory/contracts.py      |  37 ++++++++
 .../experience_memory/providers/hindsight.py       | 102 +++++++++++++++++++++
 src/code_agent/experience_memory/providers/null.py |   4 +
 src/code_agent/experience_memory/recall.py         |   2 +
 src/code_agent/experience_memory/service.py        |  34 ++++++-
 9 files changed, 368 insertions(+), 8 deletions(-)
```

## 15. git status

No commit or staging was performed. Snapshot at report completion:

```text
 M docs/EXPERIENCE_MEMORY.md
 M src/code_agent/agent.py
 M src/code_agent/cli.py
 M src/code_agent/experience_memory/__init__.py
 M src/code_agent/experience_memory/config.py
 M src/code_agent/experience_memory/contracts.py
 M src/code_agent/experience_memory/providers/hindsight.py
 M src/code_agent/experience_memory/providers/null.py
 M src/code_agent/experience_memory/recall.py
 M src/code_agent/experience_memory/service.py
?? docs/qualification/
?? src/code_agent/experience_memory/reflect_policy.py
?? src/code_agent/experience_memory/reflection.py
?? tests/test_memory_reflect.py
?? tests/test_reflect_policy.py
```
