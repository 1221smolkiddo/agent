# Agent47 Industry-Level Capability Track

This file is the working surface for turning Agent47 from a credible alpha CLI agent into a
measured engineering system.

## First Serious Pass: Capability Dashboard

The first production-minded step is not another isolated tool. It is a measurement layer that
answers whether Agent47 is actually improving.

Implemented first pass:

- `code-agent evals --save-report` persists benchmark reports.
- `code-agent eval-reports --summary` compares pass rates by mode, provider, and model.
- `code-agent eval-reports --dashboard` now builds a capability dashboard over saved reports.
- `code-agent eval-reports --dashboard --json` emits machine-readable release-gate data.

Dashboard signals:

- latest saved report and previous-report delta
- live/offline report counts
- release-readiness gate status
- aggregate pass, verification, change, and blocked rates
- category pass rates
- failure hotspots by failure class
- next recommended actions

## Why This Comes First

Industry-level agents are not trusted because they have a long feature list. They are trusted
because their behavior is measured, regressions are visible, and release claims are backed by
repeatable evidence.

The next major capabilities should be prioritized by dashboard results:

1. Low solve rate means improve planning, context ranking, or model routing.
2. Low verification rate means harden command detection and eval validators.
3. High safety-block failures mean improve policies or task decomposition.
4. High verification failures mean improve failure-output parsing and retry strategy.
5. High model-error failures mean improve fallback routing and provider diagnostics.

## Next Serious Build

After the dashboard has several saved live reports, the next best build is failure analytics:

- persist tool/model timing
- classify retry paths
- expose per-case failure traces
- compare latest failures against previous runs
- make release-smoke optionally require a passing capability dashboard

