# Known Limitations

Agent47 is a credible alpha CLI coding agent, not a finished industry product.

## Current Strengths

- CLI and interactive terminal workflow.
- Workspace-aware file tools with approvals.
- Structured patch previews and revert metadata.
- Verification detection and automatic focused checks.
- Local sandbox copies with diff/apply promotion.
- Run history, resume, work reports, debug bundles, and eval reports.
- Local per-repo memory under `.code-agent/memory/project.md` for stable project facts.
- Offline deterministic evals and opt-in live-model benchmarks.

## Current Limits

- Live benchmark quality depends on configured provider credits and model capability.
- The default `local` sandbox backend is a copied workspace plus hardened subprocess policy,
  including private per-command HOME/TMP/cache directories and obvious absolute-path escape
  blocking. It is still not OS-level isolation in the security-boundary sense. Use
  `--sandbox-backend docker` or `--sandbox-backend podman` when a container
  runtime is available and OS-level process, network, pid, CPU, memory, and root-filesystem
  isolation is required.
- Docker Desktop still requires host virtualization support. Agent47 can detect an installed Docker
  CLI separately from a running daemon and report diagnostics, but it cannot enable BIOS/firmware
  virtualization from inside the terminal.
- Shell commands can still execute local project code after approval, and a local process may still
  use operating-system APIs that are outside Agent47's command parser model.
- Shell network-deny mode and the default sandbox policy block commands classified as
  install/network before approval. Container sandbox backends also run with network disabled by
  default.
- Shell timeout and Ctrl+C cancellation paths attempt process-tree cleanup, but Agent47 still does
  not provide a container, VM, seccomp/AppArmor profile, Windows Job Object policy, or macOS seatbelt.
- Project memory is approval-gated and secret-scanned on writes, but users should still review it like any
  other local project note and delete stale or incorrect entries.
- Editor integration is intentionally deferred while Agent47 remains CLI-first.
- Redaction handles common secret patterns but cannot guarantee every secret format.
- The agent can still make incorrect code changes; tests and review remain necessary.

## Public Alpha Bar

Before calling a release public-alpha ready:

- `uv run code-agent release-smoke` should pass.
- A live eval report should be saved for the intended release model when credits are available.
- Release notes should include safety limits and upgrade notes.
- Security docs and the threat model should be reviewed for the release.

## Recommended Use

- Start with `--dry-run` for unfamiliar repositories.
- Use `--sandbox` for risky edits.
- Keep changes small and review patch previews.
- Run project tests after edits.
- Treat approved shell commands as local processes with cleanup safeguards, not as jailed execution.
- Treat generated code like a junior contributor's patch: useful, but always reviewed.
