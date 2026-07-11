# Agent47 Threat Model

This document describes the security assumptions for Agent47 as a public alpha CLI coding agent.

## Assets

- Source code and uncommitted local changes.
- Secrets in local files, environment variables, shell output, and provider credentials.
- Local machine integrity.
- Run history, debug bundles, eval reports, and work reports under `.code-agent/`.
- User trust in final answers and verification claims.

## Trust Boundaries

- User instructions are trusted only as the user's intent for the current run.
- Model output is untrusted until parsed, validated, approved where needed, and verified on disk.
- Repository files are untrusted because they can contain prompt-injection text.
- Command output is untrusted because tests, build scripts, and tools can print adversarial text.
- Web results are untrusted external content.
- The selected workspace is the normal file-operation boundary.
- `.code-agent/` is local state and should remain git-ignored.

## Main Threats And Controls

| Threat | Control |
| --- | --- |
| Prompt injection from repository files | Tool outputs are marked as untrusted context; model instructions say repo content cannot redefine tool policy; offline and live evals include malicious secret-exfiltration and verification-suppression scenarios. |
| Secret exfiltration through reads/search/output | Sensitive files are refused by direct tools, search skips secret/local-state paths, and tool outputs are redacted. |
| Destructive shell commands | Shell commands are classified; destructive forms such as `git reset --hard`, recursive force deletes, and aggressive `git clean` are blocked. |
| False success claims | Mutations are verified against disk state; final answers that claim blocked or failed mutations succeeded are rejected. |
| Unsafe patch paths | Patch paths are validated as workspace-relative before preview/apply. |
| Accidental base-workspace edits | `--dry-run` disables writes and shell commands; `--sandbox` copies the workspace and requires explicit promotion. |
| Unwanted install/network shell commands | `AGENT_SHELL_NETWORK=deny` and `--deny-network-shell` block commands classified as install/network before approval. |
| Unsafe web targets | Web access blocks local/private/reserved/link-local/multicast targets, checks DNS resolution before fetch, and enforces configured domain allowlists. |
| Unpinned or unexpected container images | Container sandbox execution refuses images outside policy and validates configured or reference-level `sha256` digest pins against local image metadata before launch. |
| Unreviewable multi-file edits | Patch previews include changed paths and metadata before approval. |
| Leaky debug artifacts | Debug bundles and stored payloads use redaction before export. |
| Model/provider failure | Fallback models can be configured; failures are recorded and reported instead of silently succeeding. |

## Residual Risks

- Shell commands still run as local processes after approval.
- Test/build/install commands can execute project code.
- Redaction is pattern-based and may miss unusual secret formats.
- Local `.code-agent/` state can contain sensitive project metadata even after redaction.
- Live evals and model calls send selected prompt/tool context to configured providers.
- The sandbox is a workspace copy, not an OS jail.
- Shell network-deny mode is command-policy enforcement, not an OS firewall.
- DNS checks happen before fetch but cannot prevent all time-of-check/time-of-use changes by remote infrastructure.

## Recommended Operating Modes

- Use `--dry-run` for inspection, planning, and unfamiliar repositories.
- Use `--sandbox` for broad edits or risky refactors.
- Review patch previews before approval.
- Run `code-agent doctor --strict` and `code-agent release-smoke` before public releases.
- Keep `.env`, `.code-agent/`, virtual environments, caches, and eval reports out of git.
