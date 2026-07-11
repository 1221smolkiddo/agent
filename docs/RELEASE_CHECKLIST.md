# Agent47 Public Alpha Release Checklist

Use this checklist before tagging any public alpha release.

## Package Metadata

- [ ] `pyproject.toml` has the release version, authors, classifiers, keywords, and project URLs.
- [ ] Distribution rights and licensing posture are reviewed before publishing.
- [ ] `CHANGELOG.md` has a dated entry for the release.
- [ ] `README.md` quick start works from a clean checkout.
- [ ] `docs/INSTALL.md` covers Windows, macOS, Linux, `uv`, editable pip, and `pipx`.

## Safety Gates

- [ ] `SECURITY.md`, `docs/THREAT_MODEL.md`, `docs/DATA_HANDLING.md`, and `docs/KNOWN_LIMITATIONS.md` are current.
- [ ] Prompt-injection tests pass.
- [ ] Secret redaction tests pass.
- [ ] Permission and path-safety tests pass.
- [ ] Domain allowlist, DNS safety, sandbox disk-budget, and container image pin tests pass.
- [ ] JSON protocol approvals fail closed unless a matching response is supplied.
- [ ] Patch previews include every changed file before approval.
- [ ] Patch apply metadata records every changed file after apply.
- [ ] Patch revert previews inverse changes and verifies reverted file hashes.
- [ ] Mutation verification prevents false final-answer success claims.

## Verification Gates

- [ ] `uv run code-agent release-smoke` passes.
- [ ] `uv run pytest` passes.
- [ ] `uv run ruff check src tests` passes.
- [ ] `uv run code-agent doctor --strict` passes or any warning is documented in release notes.
- [ ] `uv run code-agent evals` passes.
- [ ] Optional but recommended before public claims: `uv run code-agent evals --live --limit 3` passes with the release candidate model.
- [ ] Optional live Docker sandbox security CI has been run manually with `docker_security_tests=true` when making container isolation claims.
- [ ] A clean virtual environment can install the package.
- [ ] `code-agent run-json --dry-run "Inspect this project"` emits valid NDJSON.

`release-smoke` runs the local release gate in one command: unit tests, lint, strict doctor,
offline evals, and package build. Live evals are intentionally opt-in because they call the
configured model provider and can spend tokens.

## Release Steps

- [ ] Update version in `pyproject.toml`.
- [ ] Update `CHANGELOG.md` from `Unreleased` to the release date.
- [ ] Create a signed git tag named `vX.Y.Z`.
- [ ] Build source and wheel distributions.
- [ ] Inspect package contents before upload.
- [ ] Publish to the selected alpha channel.
- [ ] Create release notes that include safety limitations and upgrade instructions.
