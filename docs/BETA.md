# Closed Beta

Agent47 is ready for supervised closed-beta use on real projects. This beta validates the durable execution
runtime under representative workloads; it is not an authorization for unattended, unrestricted, or
security-critical production use.

## Before enrolling a project

1. Pin the tested release commit and record it with every report.
2. Back up `.code-agent/` before the first run and before upgrading Agent47.
3. Run `code-agent doctor --strict` successfully in the intended environment.
4. Begin with `code-agent run --dry-run` on a low-risk repository.
5. Use a Docker or Podman sandbox for unfamiliar code, and keep networking disabled unless it is needed.
6. Set conservative token, cost, time, and tool-call budgets; retain approval gates for external effects.
7. Keep human review of diffs, commands, approvals, and verification reports mandatory.

## What to test

- Resume an interrupted execution with `code-agent run ... --execution-id <id>`.
- Recover a stopped execution with `code-agent execution recover <id>` and inspect its trace.
- Verify that an ambiguous external effect is not automatically repeated.
- Exercise plan, verification, diagnosis, and repair paths using disposable test changes.
- Check that task completion is supported by evidence and verification decisions.

Record the execution ID, command, project type, operating system, selected provider/model, result, and any
recovery action. Redact credentials and private source content before sharing reports.

## Incident handling

Stop the affected execution and preserve its `.code-agent/` database and trace when you observe duplicate
external effects, unexplained budget changes, incorrect completion, an unrecoverable execution, or a suspected
credential exposure. Do not delete the journal before it is investigated. Restore the project from its normal
source-control or backup process if needed; Agent47 recovery is not a replacement for project backups.

## Beta updates

Corrective updates are evidence-driven. A beta report must be reproduced against the pinned release, fixed with
a narrowly scoped change, and protected by a regression test. Each update should include release notes,
upgrade guidance, and a fresh backup recommendation. Do not mix broad new features with a corrective beta
update unless the beta scope is explicitly expanded.

## Exit criteria

Before widening access, review beta evidence for restart recovery, duplicate-effect prevention, budget
accounting, verification correctness, platform compatibility, and security reports. Passing local tests alone
is not sufficient evidence for unrestricted production deployment.
