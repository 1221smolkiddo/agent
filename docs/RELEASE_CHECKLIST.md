# Agent47 Public Alpha Release Checklist

Use this checklist before tagging any public alpha release.

## Package Metadata

- [ ] `pyproject.toml` has the release version, license, authors, classifiers, keywords, and project URLs.
- [ ] `LICENSE` is present and matches the package classifier.
- [ ] `CHANGELOG.md` has a dated entry for the release.
- [ ] `README.md` quick start works from a clean checkout.
- [ ] `docs/INSTALL.md` covers Windows, macOS, Linux, `uv`, editable pip, and `pipx`.

## Safety Gates

- [ ] Prompt-injection tests pass.
- [ ] Secret redaction tests pass.
- [ ] Permission and path-safety tests pass.
- [ ] JSON protocol approvals fail closed unless a matching response is supplied.
- [ ] Patch previews include every changed file before approval.
- [ ] Patch apply metadata records every changed file after apply.
- [ ] Mutation verification prevents false final-answer success claims.

## Verification Gates

- [ ] `uv run pytest` passes.
- [ ] `uv run ruff check src tests` passes.
- [ ] `uv run code-agent doctor --strict` passes or any warning is documented in release notes.
- [ ] `uv run code-agent evals` passes.
- [ ] A clean virtual environment can install the package.
- [ ] `code-agent run-json --dry-run "Inspect this project"` emits valid NDJSON.

## Release Steps

- [ ] Update version in `pyproject.toml`.
- [ ] Update `CHANGELOG.md` from `Unreleased` to the release date.
- [ ] Create a signed git tag named `vX.Y.Z`.
- [ ] Build source and wheel distributions.
- [ ] Inspect package contents before upload.
- [ ] Publish to the selected alpha channel.
- [ ] Create release notes that include safety limitations and upgrade instructions.
