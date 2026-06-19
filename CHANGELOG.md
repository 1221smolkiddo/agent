# Changelog

All notable Agent47 changes are tracked here.

This project follows semantic versioning once public alpha releases begin.

## 0.1.0 - Unreleased

### Added

- CLI-first Agent47 runner with one-shot, interactive, JSON protocol, history, resume, doctor, and eval commands.
- Disk-verified mutation tracking for writes, exact edits, patches, and deletes.
- Structured patch application with approval preview, workspace path validation, check-before-apply, and multi-file change-set metadata.
- Versioned newline-delimited JSON protocol for frontend integrations with correlated approval requests and responses.
- Prompt-injection defenses that mark tool output as untrusted model context.
- Shell, network, path, and secret-redaction safety policies.
- Release checklist, install guide, and package metadata for public alpha preparation.

### Security

- Local credential files are refused by default.
- Tool outputs are redacted before model/storage use.
- Destructive shell commands are blocked by policy.
