# Release-blocker handoff — redaction regressions RESOLVED

Original final core: `3a6be181d99b6ef813b119b2076c90eb73783b47`.
Previously stopped qualification HEAD: `2fdcd90f35414049fe13aff6c1ff948d84905114`.
Branch: `qual/hindsight-production-20260928`.

The user authorized a narrow sanitizer/security fix after the failed handoff.
Both unchanged regression cases now pass:

- `tests/test_hindsight_final_qualification.py::test_core_sanitizer_removes_fake_credentials[short_bearer]`
- `tests/test_hindsight_final_qualification.py::test_core_sanitizer_removes_fake_credentials[jwt]`

The bearer matcher previously required 16 characters; the later memory credential
matcher consumed the scheme word while retaining the short token tail. Explicit
Authorization bearer headers now redact every nonempty credential length with casing
and horizontal-spacing variations. Canonical long Bearer scheme shorthand is retained;
lowercase prose without a header is preserved.

Standalone JWTs previously had no matcher, and short dot-separated fields avoided the
opaque-value rule. The shared redactor now recognizes bounded three-field base64url
candidates with a decoded JSON JOSE header containing an algorithm string. Token
boundaries reject partial oversized-field matches. This is heuristic secret detection,
not cryptographic verification; versions, filenames, domains, and ordinary bearer prose
have false-positive controls. No literal fixture value is special-cased.

The exact two gates passed (1.43 seconds). The broader security/operator suite passed
134 tests (10.64 seconds). The complete 18-file focused suite passed 520 tests
(83.14 seconds), including memory authority, outbox, and disabled-memory coverage.
The isolated-bug B control makes zero recalls; the recurring-bug B control makes one.
All 16 scripted fixture runs verify with zero irrelevant recalls and zero Reflect
requests. The offline runner builds configuration directly; successful repairs do
not trigger Reflect. Independent Reflect disablement/recovery is covered by focused
tests, rather than claimed from these successful fixture runs.

See `FINAL_CORE_SECURITY_GATE.json`, `FINAL_RECALL_CONTROLS.json`,
`FINAL_FIXTURE_RESULTS.json`, and `HINDSIGHT_FINAL_REPORT.md` for final evidence.
The sole behavior change is shared secret redaction in `src/code_agent/safety.py`;
recall/Reflect policy, planner, runtime, outbox, and terminal code are unchanged.
No main merge or push occurred.
