# Agent47 Security Control Review

This review maps Agent47's implemented controls to the security checklist used for the
industry-readiness gate. It describes the local CLI at the current source revision; it is not a
certification, penetration test, or claim of formal compliance.

Status meanings:

- **Implemented**: enforced in runtime code and covered by automated tests.
- **Partial**: useful enforcement exists, with a documented residual limitation.
- **Absent**: not implemented.
- **Not applicable**: belongs to a hosted multi-user or enterprise service, while Agent47 is a
  single-user local CLI.

## Local Security Controls

| Area | Status | Evidence and residual risk |
| --- | --- | --- |
| Secrets detection and redaction | Partial | Sensitive filenames and common key/token formats are refused or redacted before model output, logs, and storage. Pattern matching cannot detect every secret or encoded credential. |
| Credential isolation | Implemented | Project commands receive an allowlisted environment rather than provider credentials. Provider keys remain in the Agent47 process. OS credential-store integration and rotation are absent. |
| Filesystem scope | Implemented | File tools resolve paths inside the workspace and reject traversal, sensitive paths, and unsafe symlink mutations. Text transactions refuse unsupported binary encodings. |
| Transactional edits and recovery | Implemented | Mutations use journaled previews, checkpoints, atomic replacement, rollback, undo/redo, and crash recovery. Multi-file commits are not one OS-level atomic primitive. |
| Command policy | Implemented | Commands are classified, schema validated, previewed, approved, run without `shell=True`, bounded by timeouts, and cleaned up on cancellation. Destructive, compound-shell, inline-code, and workspace-escape forms are blocked. |
| Local process isolation | Partial | Environment scrubbing, process-tree cleanup, monitoring, and thresholds exist. The ordinary local backend is not an OS security boundary. |
| Container isolation | Implemented | Docker/Podman mode uses a copied workspace, non-root user, read-only root filesystem, dropped capabilities, PID/CPU/memory limits, immutable image policy, seccomp, offline networking by default, and explicit promotion. Host runtime trust remains. |
| Network controls | Partial | Shell network is denied by default; networked commands require explicit enablement and approval. Web access requires HTTPS, rejects private/loopback/reserved DNS results, and supports domain allowlists. Domain-limited container egress fails closed because an enforced proxy is not bundled. |
| Prompt-injection boundaries | Partial | Repository files, diffs, search results, web pages, command output, and model output are labeled untrusted; tool permissions remain authoritative. Detection is heuristic and cannot prove intent. |
| Tool security | Implemented | Tools have typed schemas, per-tool permissions, bounded outputs, timeouts where applicable, path policy, redaction, and validated result handling. External MCP tools are not yet supported. |
| Git safety | Partial | Agent47 previews commits, requires approval, scans added lines for likely credentials, checkpoints mutations, and blocks destructive/history-rewriting shell forms. Remote branch protection and force-push policy remain external Git-host controls. |
| Runtime protection | Partial | Timeout, cancellation, process-tree cleanup, restart limits, log rotation, and CPU/memory/disk/PID controls exist. Some local limits are monitor-and-terminate rather than kernel quotas. |
| Verification | Implemented | Changed files are checked, project verification is detected, tests/build/lint/type checks can run automatically, acceptance gates are enforced, and unsupported success claims are rejected. Verification cannot prove security. |
| Auditability | Partial | Model usage, tool results, commands, approvals, transactions, sandbox events, and timestamps are stored and exportable. Local logs can be edited by the same OS user and are not tamper-proof. |
| Privacy | Partial | Execution is local-first, provider context is bounded, secrets are redacted, and history has deletion/pruning controls. SQLite is not application-encrypted; provider retention applies after transmission. Telemetry is not enabled by default. |
| Model safety | Partial | Structured output validation, context filtering, loop/failure budgets, reviewer checks, confidence state, and refusal-capable policies exist. Jailbreak and hallucination detection are heuristic, not guarantees. |
| Supply chain | Partial | Dependencies are locked, container images can require immutable digests and Trivy scanning, and release smoke builds packages. Automated SBOM, artifact signing, provenance, registry allowlists, typosquat detection, and license gates are absent. |
| Code security scanning | Partial | Ruff, compiler/test diagnostics, secret checks, dependency/image scanning hooks, and model review exist. A general multi-language SAST/malware suite is not bundled. |
| Signed and reproducible releases | Absent | Signed updates, binary integrity verification, deterministic build attestation, SBOM, and automated provenance remain release-roadmap work. |

## Hosted and Enterprise Controls

The following are **not applicable to the current local single-user CLI** and must not be advertised
as implemented: user authentication, MFA, secure web sessions, RBAC, organization roles, OAuth/SAML
SSO, SCIM, admin workflows, centralized configuration, team permissions, immutable organization audit,
and multi-tenant conversation isolation.

If Agent47 gains a remote service or shared control plane, all of these become required security
boundaries rather than optional product features.

## Compliance Position

Agent47 has technical controls that can contribute evidence to a broader compliance program, but it is
not SOC 2, ISO 27001, GDPR, or HIPAA certified. Those outcomes require organizational policies, asset
and vendor management, incident response, access reviews, retention enforcement, legal agreements,
and independent audit evidence beyond this repository.

## Highest-Priority Residual Work

1. Automate SBOM generation, dependency/license scanning, artifact signing, and build provenance.
2. Add OS credential-store support and explicit provider data-retention disclosures.
3. Add tamper-evident audit export with hash chaining and optional external retention.
4. Add enforced domain-restricted egress through a reviewed proxy rather than command classification.
5. Add multi-language SAST and dependency-scanning release gates.
6. Add native OS sandboxing or require containers for all untrusted code execution.
7. Commission an independent penetration test before making production-security claims.
