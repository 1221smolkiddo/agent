# Known Limitations

Agent47 is a capable alpha coding agent, not a complete replacement for an experienced engineer or a
hard security sandbox. These limitations are part of the operating contract.

## Model Reliability

- Coding quality depends on the selected provider, model, prompt compatibility, context window, and credits.
- Deterministic offline evals validate agent logic with scripted responses; they do not measure real-model quality.
- Live evals are opt-in, consume tokens, and need multiple runs before making reliability claims.
- Model fallback preserves messages and durable execution state and records provider handoffs, but can still change
  behavior, latency, cost, output limits, and interpretation between attempts.
- The reviewer is another model call and cannot prove correctness or security.

## Autonomy

- Plans are enforced only after the model creates one. Simple tasks may proceed without an explicit plan.
- Repeated-action detection matches validated action payloads and identical outcomes. Semantically equivalent but
  differently worded actions may not be recognized as the same loop.
- Workspace generations reset repeat detection after a recorded mutation; external edits during a run are not a
  complete transactional snapshot.
- Context budgeting is character-based rather than provider-tokenizer-based.
- Deterministic compaction preserves task and recent evidence but may omit older details that later become relevant.
- Acceptance gates support explicit command, evidence, and changed-file criteria. Command equivalence and evidence
  substring matching remain heuristics and cannot infer every project-specific success condition.
- Checkpoints preserve Agent47 control state, not arbitrary in-memory state inside external tools or providers.
- Command diagnostics support broad common compiler, test, linter, build, and runtime output shapes, but
  vendor wording and custom reporters can still fall back to generic diagnostics.
- Output ordering is timestamped at the client pipe readers; operating-system and tool buffering can delay
  when an individual partial line becomes observable.
- Logs and normalized findings are deliberately bounded. Truncation is reported with original character
  counts and hashes, but extremely verbose commands may require inspecting their native artifact files.
- Recurring diagnostic signatures identify repeated evidence across stored runs. A recurrence is marked as a
  regression candidate rather than proof that the same underlying defect returned.
- Multi-file transactions provide journaled all-or-rollback behavior, not a single operating-system primitive
  spanning multiple files. A process crash can expose an intermediate state until startup recovery completes.
- Full workspace checkpoints hash files before every approved mutation. Content-addressed deduplication limits
  repeated storage, but very large repositories can experience checkpoint latency and disk growth.
- Normal text tools refuse to replace symlink paths. Snapshot recovery preserves symlink identity, while
  editing the symlink target requires addressing the target file directly.
- POSIX mode bits and timestamps are preserved where the operating system exposes them. Windows permission
  preservation is limited by Python and filesystem ACL semantics.
- Three-way merge is conservative. Non-overlapping text changes can merge automatically; overlapping,
  binary, move, delete, and symlink conflicts require explicit user resolution.

## Sandboxing And Commands

- Ordinary non-sandbox runs use hardened local subprocess policy, not OS-level isolation.
- `--sandbox` fails closed unless Docker or Podman runtime, daemon, local image, and policy checks are healthy.
- Approved verification, build, and install commands can execute arbitrary project code.
- Workspace path checks do not constrain every operating-system API available to an approved local process.
- Local command network-deny mode is command classification, not an OS firewall.
- Managed local shutdown signals the process group/tree and escalates after a grace period, but local mode does
  not use Windows Job Objects, Linux namespaces, seccomp/AppArmor, or macOS seatbelt profiles. A hostile process
  can still escape best-effort local lifecycle control; use container isolation for untrusted project code.
- Durable workers survive Agent47 CLI exit, not host reboot. After reboot, stale active records reconcile as
  orphaned and require an explicit new start; Agent47 does not silently relaunch project code at login.
- POSIX systems support native PTYs. Windows interactive pipe control is supported, but PTY requests fail closed
  until a native ConPTY transport is implemented.
- Readiness monitoring currently proves localhost TCP acceptance, not application-level HTTP correctness.
- Linux and Windows expose process-tree CPU and memory sampling. Other POSIX platforms may report monitoring as
  unavailable. Local resource limits are monitor-and-terminate thresholds, not kernel-enforced quotas.
- Reusable containers survive individual commands and Agent47 CLI runs, but not explicit removal, runtime data
  resets, or host reboot. Managed jobs fail or follow their configured restart policy if their container vanishes.
- Rotating logs and events deliberately discard the oldest backup after configured limits are reached.
- Docker and Podman isolation depend on a correctly configured daemon, host virtualization, runtime security, and
  trusted image supply chain.
- Container networking is offline by default, but the container runtime itself remains a privileged host component.
- Strict health requires rootless runtime evidence; disabling that policy restores the rootful daemon risk.
- Runtime seccomp is mandatory, while AppArmor or SELinux availability remains platform-dependent.
- Domain-limited shell egress is refused rather than simulated. Networked containers currently receive either
  offline isolation or explicitly approved unrestricted bridge access.
- Vulnerability scanning is enforced only when `images.scan_required` is enabled and depends on a trusted,
  current local Trivy installation and vulnerability database.
- Disk limits are measured around commands and are not a filesystem quota.

## Secrets And Data

- Sensitive filenames are refused and common secret formats are redacted, but pattern matching cannot identify every secret.
- Approved commands can print credentials in unusual formats before redaction recognizes them.
- `.code-agent/` can contain sensitive repository metadata even when obvious credentials are redacted.
- Model-backed runs send approved context to the configured provider and inherit that provider's retention policy.
- Web search sends approved queries to third-party search services.
- SQLite storage is local and redacted before writes, but it is not application-level encrypted.
- Unix database permissions are restricted; Windows protection depends on filesystem ACLs and the user account.
- Deleting a run removes Agent47's SQLite records but cannot delete data already sent to a provider or external command.

## Repository Understanding

- The persistent project graph statically extracts common declarations, imports, calls, and references across
  Python, JavaScript/TypeScript, Rust, Go, Java, Kotlin, C/C++, C#, Swift, Ruby, and PHP. Regex-backed languages
  are less precise than Python AST parsing or an installed language server, especially for overloaded methods,
  aliases, generated declarations, conditional compilation, and complex macro syntax.
- LSP features require separately installed language-server executables and inherit each server's indexing,
  configuration, startup-time, and protocol limitations.
- Container-backed LSP requires the selected image to contain the relevant language-server binary. Environment
  detection does not install tools or implicitly build or pull unreviewed images.
- Workspace symbol search aggregates relevant installed servers, but very large polyglot monorepos can make
  server startup and indexing expensive.
- Code actions that contain edits are previewed as patches. Server commands and resource operations such as
  create, rename, or delete are reported but are not executed automatically.
- Diagnostics are consumed from LSP push notifications or pull requests when the diagnostics tool runs; the
  terminal does not yet maintain a continuously rendered diagnostics panel between agent actions.
- Dynamic imports, generated code, macros, reflection, build-time code generation, runtime dependency injection,
  and custom monorepo tooling can evade the static graph.
- Context packs and token estimates use source character counts rather than provider-specific tokenizers. Graph-aware ranking remains
  heuristic and can miss behavior connected only through runtime data, external services, or unsupported build metadata.
- Background refresh is polling plus transaction-triggered invalidation, not a native filesystem watcher. External
  edits become visible on the next interval or synchronous repository-context request.
- Git-aware workflows assume a valid local Git repository and do not replace remote branch protection or code review.
- Revert refuses conflicts but cannot reconstruct changes that were never recorded by Agent47.

## Verification

- Detected commands depend on recognizable Python, Node, Rust, or Go project metadata.
- Python pytest verification uses graph-affected test files when static relationships are available. Unsupported
  runners, dynamic dependencies, manifest changes, or incomplete graphs can still require broad checks.
- Passing tests do not prove absence of bugs, security issues, performance regressions, or platform-specific defects.
- Some project commands require services, credentials, network access, hardware, or interactive input unavailable to the agent.
- The complete local test suite can be slow on Windows; focused tests are recommended during iteration.
- Optional Docker security tests are skipped when a suitable runtime is unavailable.

## Providers And Web Search

- OpenAI-compatible APIs differ in streaming events, usage records, error shapes, and supported request fields.
- Rate-limit and transient retries are bounded; long provider outages still block runs.
- Cost estimates require configured per-million-token prices and provider usage metadata.
- Built-in web search relies on public HTML search endpoints and can break when providers change markup or block automation.
- DNS and private-address checks reduce SSRF risk but cannot eliminate every time-of-check/time-of-use condition.

## Product And Release

- Agent47 remains CLI-first; there is no maintained VS Code extension or remote multi-user service.
- There is no organization control plane, SSO, centralized policy distribution, or tamper-proof audit service.
- Package publishing, artifact signing, SBOM generation, and release provenance are not fully automated.
- The package metadata still identifies the project as alpha and version `0.1.0` until an explicit release is cut.
- Cross-platform CI cannot represent every shell, filesystem, locale, terminal, and container environment.

## Recommended Operating Modes

- Use `--dry-run` for unfamiliar repositories and inspection-only work.
- Use `--sandbox` for broad edits that execute project code; it requires Docker or Podman process isolation.
- Keep network shell access denied unless installation or external access is intentional.
- Review every approval preview, generated diff, command, and final verification report.
- Keep `.env`, `.code-agent/`, virtual environments, caches, and private eval reports out of Git.
- Use small changes, focused tests, and normal human code review.
- Treat Agent47 like a fast engineering collaborator with local execution privileges, not an infallible authority.

## Release Gate

Before publishing a release candidate:

1. Update the version and retained documentation.
2. Run `uv run code-agent release-smoke`.
3. Run live evals repeatedly with the intended release model and save the reports.
4. Review prompt-injection, secret, path, permission, sandbox, approval, mutation, and revert tests.
5. Run Docker sandbox security CI when making container-isolation claims.
6. Install the built wheel in a clean environment and smoke-test `doctor`, `run-json`, and interactive startup.
7. Inspect source and wheel contents, document upgrade risks, and create a signed release tag.

Do not market the agent as production-secure or industry-complete solely because offline tests pass.
