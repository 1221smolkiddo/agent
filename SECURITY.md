# Security Policy

Agent47 is a local CLI coding agent. It can read project files, propose edits, apply approved
patches, and run approved shell commands inside a workspace. Treat it like any other tool that can
modify source code and execute commands on your machine.

## Supported Versions

Agent47 is currently alpha software. Security fixes are applied to the main branch until versioned
public releases begin.

## Reporting A Vulnerability

Please report security issues privately through the repository security channel if available, or by
opening a minimal issue that does not include secrets, exploit payloads, private repository content,
or API keys.

Include:

- Agent47 version or commit SHA.
- Operating system and Python version.
- Command used.
- Whether `--dry-run`, `--sandbox`, or write mode was active.
- Redacted logs or a redacted `code-agent history export <run-id>` bundle when possible.

## Security Boundaries

Agent47's current safety model is defense in depth, not a hard isolation boundary.

- Workspace path guards prevent normal file tools from reading or writing outside the selected
  workspace.
- Sensitive credential files such as `.env`, `.npmrc`, `.pypirc`, and `.netrc` are refused by
  default for direct file reads and mutations.
- Shell commands are classified before approval. Clearly destructive commands are blocked by policy.
- Tool outputs are redacted for common secret patterns before model/storage use.
- Repository files, command output, web results, search results, diffs, and generated repo maps are
  treated as untrusted model context.
- `--dry-run` skips file mutations and shell execution.
- `--sandbox` uses a copied workspace and requires explicit promotion before base files change.

## What Agent47 Does Not Guarantee

- It is not a malware sandbox.
- It does not provide OS-level process isolation.
- It cannot prove a model response is correct.
- It cannot guarantee generated code is secure.
- It cannot guarantee every secret format is detected.
- It cannot prevent risk if a user approves a dangerous command or patch.

Use `uv run code-agent release-smoke` before publishing releases, and use `--dry-run` or
`--sandbox` for uncertain tasks.
