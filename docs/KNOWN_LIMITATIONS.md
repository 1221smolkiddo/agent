# Known Limitations

Agent47 is a credible alpha CLI coding agent, not a finished industry product.

## Current Strengths

- CLI and interactive terminal workflow.
- Workspace-aware file tools with approvals.
- Structured patch previews and revert metadata.
- Verification detection and automatic focused checks.
- Local sandbox copies with diff/apply promotion.
- Run history, resume, work reports, debug bundles, and eval reports.
- Offline deterministic evals and opt-in live-model benchmarks.

## Current Limits

- Live benchmark quality depends on configured provider credits and model capability.
- The sandbox is a copied workspace, not OS-level isolation.
- Shell commands can still execute local project code after approval.
- Shell network-deny mode blocks commands classified as install/network, but it is not an OS firewall.
- Cancellation and process isolation are basic.
- Per-repo memory for stable project conventions is not implemented yet.
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
- Treat generated code like a junior contributor's patch: useful, but always reviewed.
