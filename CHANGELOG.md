# Changelog

Notable user-facing and engineering changes are recorded here.

## 0.1.0 - Unreleased

### Added

- CLI, interactive terminal, and versioned NDJSON interfaces.
- Repository context maps, ranking, symbols, dependencies, memory, and git awareness.
- Durable execution phases, enforceable plans, bounded context compaction, and repeated-outcome blocking.
- Verification detection, automatic checks, diagnostics, reviewer passes, resume, and safe revert.
- OpenAI-compatible providers, profiles, presets, bounded retries, fallback, usage, and cost records.
- Run history, redacted debug bundles, eval reports, collaboration helpers, deletion, and retention pruning.
- Local, copied-workspace, Docker, and Podman execution modes.

### Security

- Workspace path validation and sensitive-file refusal.
- Shell-free local argv execution with command classification and process-tree cleanup.
- Secret redaction before model context and persistence.
- Mutation evidence and rejection of unsupported completion claims.
- Container image, digest, resource, pid, root-filesystem, environment, and network controls.
- Fail-closed Docker/Podman sandbox resolution with preserved workspace policy and isolation evidence.
- Direct container argv execution without an inner shell or silent local downgrade.
- Rootless and seccomp health enforcement, fixed non-root workload identity, and immutable image resolution.
- Named-container CID tracking with forced cleanup across success, failure, cancellation, and timeout paths.
- Optional Trivy vulnerability gates and fail-closed rejection of unenforced domain egress allowlists.

### Quality

- Cross-platform CI for supported Python versions.
- Deterministic coding and safety evals, opt-in live evals, and bounded release smoke checks.
- Consolidated implementation-backed documentation.
