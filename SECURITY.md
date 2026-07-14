# Security Policy

Agent47 is a local coding agent that can inspect repositories, modify files, and execute approved
commands. Treat it as a development tool with local execution privileges, not as a malware sandbox.

## Supported Versions

Security fixes currently target the `main` branch while Agent47 remains alpha software. Versioned
support guarantees will be defined when stable releases begin.

## Reporting A Vulnerability

Use the repository's private security-reporting channel when available. Do not publish credentials,
private source, exploit secrets, or unredacted logs in a public issue.

Include:

- Agent47 commit SHA or release version.
- Operating system and Python version.
- Command and selected model/provider.
- Whether dry-run, write, copied sandbox, or container sandbox mode was active.
- A reviewed, redacted `code-agent history export <run-id>` bundle when useful.

## Security Boundaries

- File and patch tools enforce workspace-relative paths and refuse common credential files.
- Repository files, diffs, command output, search results, web pages, and model output are untrusted.
- Mutations are checked against disk before completion claims are accepted.
- Repeated identical action outcomes are blocked without a workspace change.
- Local commands are classified, approved, executed without a shell, and receive a scrubbed environment.
- Destructive, compound-shell, workspace-escape, and arbitrary inline-code commands are blocked.
- Tool output and stored payloads are redacted for common secret patterns.
- Dry-run mode disables mutations and command execution.
- Copied sandboxes preserve reviewed policy and require explicit promotion to the base workspace.
- `--sandbox` requires a healthy Docker or Podman process boundary and refuses local fallback.
- Container commands execute as direct argv without an inner shell and record isolation evidence.
- Strict container health requires rootless runtime evidence, seccomp, a non-root workload UID, and an
  immutable local image digest.
- Docker explicitly selects `docker-default` when AppArmor is available.
- Named containers and CID files support forced cleanup on completion, cancellation, timeout, and launch failure.
- Optional Trivy policy can fail health and execution on denied image vulnerability severities.
- Domain allowlists never silently become unrestricted bridge access; unsupported egress policy fails closed.

## Residual Risk

- Approved project commands can execute arbitrary repository code.
- The local backend used by ordinary runs is policy enforcement, not OS-level isolation.
- Pattern-based redaction cannot identify every secret.
- A passing model review or test suite does not prove correctness or security.
- Provider calls send approved context under the provider's retention policy.
- Container safety depends on the host runtime, daemon, image, and policy configuration.
- Disabling rootless or image-scan requirements weakens the strict sandbox contract.

See [Known Limitations](docs/KNOWN_LIMITATIONS.md) for the full operating contract.
