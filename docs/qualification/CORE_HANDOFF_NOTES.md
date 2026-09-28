# Core handoff notes — DRAFT

Qualification branch: `qual/hindsight-production-20260928`, based on P6 `5b3622a`.
No core integration, policy, planner, runtime, or outbox lifecycle file was edited.

Session A must review two confirmed redaction gaps before final qualification:

- A short authorization bearer value survives `MemorySanitizer.sanitize_text`.
  Shared bearer filtering uses a minimum length; the later credential matcher
  replaces the bearer word while retaining the short credential tail.
- A standalone JWT survives the same boundary. Its individual segments are shorter
  than the opaque-value filter and there is no standalone JWT pattern.

Reproducer: `tests/test_hindsight_final_qualification.py::test_core_sanitizer_removes_fake_credentials`.
The `short_bearer` and `jwt` cases fail on the committed P6 base. Values are deliberately
omitted from this report. `FINAL_SECURITY_PROBE.json` contains labels and pass flags only.
Do not waive these tests or fix core redaction in the qualification branch.

The isolated/simple-bug recall false positive remains EXPECTED PRE-CORE-FIX.
After handoff, it must make zero automatic recalls. If it still recalls, stop final
qualification and return the finding to Session A without changing recall policy.

Pending: final core commit SHA, the false-positive fix, redaction fixes, merge/rebase,
and final exact-state qualification. No final production approval is implied.
