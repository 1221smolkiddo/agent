# Real-model A/B/C definition — HELD / NOT RUN

Six synthetic, non-sensitive engineering fixtures are prepared in
`REAL_MODEL_ABC_MANIFEST.json`. They cover recurring bug, CI failure, migration
continuation, historical architecture decision, isolated bug, and mechanical rename.
A disables memory; B enables one selective recall and disables Reflect; C enables
recall plus at most one Reflect. Each arm gets a fresh workspace and run database.
Order rotates A/B/C, B/C/A, C/A/B. One trial yields 18 runs. This is a bounded small
qualification definition, not evidence that memory improves reasoning.

The generic `memory evaluate --include-reflect` harness is implemented and tested
with a no-model external probe. Paid evaluation remains held. Before use, supply the
final core SHA, review the synthetic lesson/golden fixture inventory in
`REAL_MODEL_SEED_REVIEW.json`, prepare independent equivalent frozen B/C provider-bank
snapshots, select and freeze a model, and verify an account/provider-enforced USD 5
spending ceiling. Do not set the enforcement or seed-review flags without evidence.
A missing core SHA, unmerged/different core, missing seed review, absent cost ceiling,
more than six scenarios, multiple trials, excessive limits, or timeout over 120 seconds
is rejected by the qualification harness before it launches the external runner.

Runner contract:

- Execute a real `CodingAgent` through `create_agent` using the supported Settings,
  honor `AGENT47_EVAL_MAX_STEPS` and all supplied model/runtime environment limits,
  and count actual model/tool calls. There is no model call to classify eligibility.
- Use 6 steps, 1024 output tokens, 8000 context characters, no model retries/fallbacks,
  no reviewer, deterministic planning, and 90 seconds per run. These are request/run
  limits; the provider-enforced spend ceiling is needed to bound actual financial cost.
- Initialize the copied synthetic fixture with a reviewed Git identity matching its
  seeded bank and provenance. Use separate equivalent bank snapshots for B and C so
  retention from one arm cannot contaminate another. Do not suppress core retention.
  Seed/snapshot setup is not performed while provider configuration is unavailable.
- Write only observed allowlisted boolean/numeric metrics to `AGENT47_EVAL_METRICS`:
  verification, model/tool calls, reads, verification commands, repeated failures,
  latency, stale/irrelevant recall, recall/Reflect requests and latency, token usage
  when available. Missing measurements are null. Do not include transcript, source,
  payloads, credentials, exception text, or guessed token/cost observations.
- Independently review strategy/relevance judgments; existing offline irrelevance
  labels and repeated action hashes are not a semantic real-model scoring rubric.

After handoff and verified prerequisites, the command shape is:

```text
python -m code_agent memory evaluate --include-reflect --manifest docs/qualification/REAL_MODEL_ABC_MANIFEST.json --runner "PATH_TO_REVIEWED_INSTRUMENTED_REAL_RUNNER" --timeout-seconds 90 --trials 1 --output docs/qualification/FINAL_REAL_MODEL_ABC_RESULTS.json
```

The instrumented real runner/model/provider, frozen bank snapshots, verified provider cost control remain to be supplied/reviewed.
The manifest records final core base `3a6be181d99b6ef813b119b2076c90eb73783b47`
and the qualified shared-redaction source blob. Both redaction gates now pass.
Before paid use, record the containing final fix commit as the approved SHA and
verify the runner, bank snapshots, seed review, provider configuration, and actual
spending ceiling. Execution remains held because those prerequisites are unavailable. Nothing here invokes
or authorizes expensive model trials before those prerequisites. Fixture test results
can establish correctness on these narrow tasks; they do not establish general model
quality or a historical-memory performance advantage.
