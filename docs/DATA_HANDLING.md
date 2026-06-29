# Data Handling

Agent47 is local-first, but model-backed runs can send selected context to the configured model
provider.

## Local Data

Agent47 may create local state under `.code-agent/`:

- `agent.db`: run history, steps, work reports, and model usage metadata.
- `debug-bundles/`: redacted run exports created by `code-agent history export`.
- `eval-reports/`: saved eval metrics created by `code-agent evals --save-report`.
- `memory/project.md`: human-readable per-repo memory for stable project conventions, commands,
  architecture notes, pitfalls, glossary terms, and successful patterns.
- `sandboxes/`: copied workspaces created by `--sandbox` or `/sandbox`.

`.code-agent/` is git-ignored and should stay local.

## Provider Data

For model-backed runs, Agent47 sends:

- The user task.
- System/developer prompt instructions.
- Selected file contents or summaries that the user approved.
- Bounded project memory from `.code-agent/memory/project.md` for workspace coding tasks.
- Tool outputs needed for the agent to continue.
- Verification output when recovery is needed.

Agent47 does not intentionally send refused sensitive files such as `.env`, `.npmrc`, `.pypirc`,
or `.netrc`. Redaction is applied to tool outputs before model/storage use, but users should still
avoid approving commands that print secrets.

## Web Search

Web search is approval-gated. Search queries and public result snippets may be sent to the search
provider. Localhost, private-network, link-local, reserved, and multicast targets are blocked or
filtered by policy.

## Retention

Agent47 does not upload local history to its own service. Retention depends on:

- Files left under `.code-agent/`.
- Your configured model provider's data policy.
- Any external tools or shell commands you approve.

Delete `.code-agent/` to remove local Agent47 history for a workspace.
Delete `.code-agent/memory/project.md` to reset only project memory.

## Safe Sharing

Before sharing logs or reports:

- Prefer `code-agent history export <run-id>` over raw terminal logs.
- Inspect exported bundles for project-sensitive context.
- Do not share `.env`, provider keys, raw shell logs with secrets, or private repository content.
