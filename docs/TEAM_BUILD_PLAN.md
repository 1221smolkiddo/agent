# Agent47 Team Build Plan

This is the working plan for building Agent47 from a CLI-first coding agent into an industry-grade Codex-like developer agent.

The immediate product is a **CLI agent**. VS Code and other editor integrations come later, after the CLI core is trustworthy.

## Current Position

Agent47 is currently a working early CLI agent.

Approximate maturity:

| Area | Progress |
| --- | --- |
| Foundation CLI agent | 70% |
| Safe local coding agent | 25% |
| Public alpha product | 15-20% |
| Codex-like agent | 10-15% |
| Scalable public tool | 5-10% |

## Team Model

Suggested team of 4:

| Role | Main Ownership |
| --- | --- |
| Agent Core Owner | agent loop, planning, recovery, verification |
| Safety/Tools Owner | permissions, sandbox, shell policy, patch application |
| CLI/UX Owner | `agent47`, status output, command UX, history, docs |
| Infrastructure/Release Owner | tests, CI, packaging, GitHub issues, release process |

Everyone can work across areas, but each issue should have one clear owner.

## Golden Rules

- CLI comes first.
- Never claim a file was created, edited, tested, or committed unless verified.
- Prefer patch previews over direct writes.
- Ask before risky actions.
- Keep `.env`, secrets, local databases, and sandboxes out of git.
- Every meaningful feature needs tests.
- Every issue should have acceptance criteria.
- The agent should recover from failures without the user repeatedly typing "continue."

## How To Track Work

Use the checkboxes in this file as a lightweight project board.

- `[ ]` means not started.
- `[~]` means in progress.
- `[x]` means done.

When someone starts work:

- Put their name next to the issue.
- Change `[ ]` to `[~]`.
- Link the GitHub issue or branch if one exists.

When the work is finished:

- Change `[~]` to `[x]`.
- Add the merged PR or commit link.
- Make sure the acceptance criteria are met.
- Update `docs/PROGRESS.md` if the feature changes what Agent47 can do.

## Completed Work

These are already built and should be maintained while new work continues.

- [x] Python CLI project scaffold.
- [x] `agent47` interactive terminal mode.
- [x] `code-agent run` one-shot CLI mode.
- [x] OpenRouter-compatible model client.
- [x] Default Qwen model configuration.
- [x] `.env.example` and git-ignored local `.env`.
- [x] SQLite run history.
- [x] File listing, file reading, file writing, and exact-text editing tools.
- [x] Project search with ripgrep and Python fallback.
- [x] Web search action with user approval.
- [x] Optional tree-sitter code summaries.
- [x] Permission prompts for read/list/search/code-summary/write/edit/shell/web actions.
- [x] Dry-run mode.
- [x] Local workspace sandbox copy mode.
- [x] Operation status labels such as `THINKING`, `READING`, `EDITING`, `TESTING`, and `DONE`.
- [x] Stop controls with `Ctrl+C`, `/stop`, and `/exit`.
- [x] Automatic tool failure recovery loop.
- [x] Guard against false completion after blocked writes/edits.
- [x] GitHub CI, issue templates, PR template, and contribution docs.
- [x] Progress tracker in `docs/PROGRESS.md`.
- [x] Feature summary in `features_and_workdone.md`.
- [x] Automatic focused verification after successful file mutations.
- [x] Mutation tracking verifies write content, edit/patch content changes, and delete removal before final claims are trusted.
- [x] Final-answer gate rejects unverified file creation/edit claims.
- [x] Baseline interactive session state for follow-up turns.
- [x] Approved git status/diff inspection before edits.
- [x] Run detail views and resumable runs from saved step history.
- [x] Baseline shell command risk policy and secret redaction.
- [x] Baseline public-web network policy for web search.
- [x] Durable `update_plan` action with persisted step status checkpoints.
- [x] Visible plan snapshots in CLI and `agent47`.
- [x] Planner metadata for target files, owned files, checks, blockers, and risk notes.
- [x] Structured work reports with changed-line diff review.
- [x] Lightweight repo map and task-aware relevance ranking.
- [x] Local deterministic eval harness with baseline safety regressions.
- [x] Baseline newline-delimited JSON protocol for future frontends.
- [x] JSON protocol approval request/response handling for editor frontends with correlated request IDs and fail-closed responses.
- [x] Model fallback and usage/cost tracking baseline.
- [x] Install doctor and cross-platform install guide.
- [x] Fixture-based coding evals for create/edit/fix/recover tasks.

## Team Task Board

Use this as the quick issue picker. Detailed descriptions and acceptance criteria are in the phase sections below.

### Phase 1: Trust And Verification

- [x] Issue 1: Mutation Tracking. Owner: Agent47
- [x] Issue 2: Final Answer Verification Gate. Owner: Agent47
- [x] Issue 3: Final Work Report. Owner: Agent47

### Phase 2: Patch-Based Editing

- [x] Issue 4: Patch Model. Owner: Agent47
- [x] Issue 5: Patch Preview And Approval. Owner: Agent47
- [x] Issue 6: Patch Apply And Verify. Owner: Agent47
- [x] Issue 7: Patch Revert. Owner: Agent47

### Phase 3: Permissions And Policy

- [ ] Issue 8: Permission Scopes. Owner:
- [x] Issue 9: Shell Command Policy. Owner: Agent47
- [x] Issue 10: Secret Redaction. Owner: Agent47
- [x] Issue 11: Network Permission Policy. Owner: Agent47

### Phase 4: Sandbox And Isolation

- [x] Issue 12: Sandbox Diff View. Owner: Agent47
- [x] Issue 13: Promote Sandbox Changes. Owner: Agent47
- [ ] Issue 14: Sandbox Cleanup. Owner:
- [ ] Issue 15: Process Isolation Research. Owner:

### Phase 5: Testing And Build Automation

- [x] Issue 16: Project Detector. Owner: Agent47
- [x] Issue 17: Test Command Runner. Owner: Agent47
- [x] Issue 18: Build/Lint Runner. Owner: Agent47

### Phase 6: Context Engine

- [x] Issue 19: Repo Map. Owner: Agent47
- [x] Issue 20: File Relevance Ranking. Owner: Agent47
- [x] Issue 21: Symbol Index Baseline. Owner: Agent47
- [x] Issue 22: Git Awareness. Owner: Agent47

### Phase 7: Planner And Task State

- [x] Issue 23: Task Plan Object. Owner: Agent47
- [x] Issue 24: Visible Step Progress. Owner: Agent47
- [x] Issue 25: Session Resume. Owner: Agent47

### Phase 8: Streaming And UX

- [x] Issue 26: Streaming Model Output. Owner: Agent47
- [ ] Issue 27: Better Terminal UI. Owner:
- [ ] Issue 28: Multiline Input. Owner:

### Phase 9: Model Layer

- [~] Issue 29: Provider Abstraction. Owner: Agent47
- [x] Issue 30: Model Profiles. Owner: Agent47
- [x] Issue 31: Cost And Token Tracking. Owner: Agent47
- [x] Issue 32: Model Fallback. Owner: Agent47

### Phase 10: Storage And History

- [x] Issue 33: Run Detail View. Owner: Agent47
- [ ] Issue 34: History Search. Owner:
- [ ] Issue 35: Storage Migrations. Owner:

### Phase 11: Public Packaging

- [x] Issue 36: Package Metadata. Owner: Agent47
- [x] Issue 37: Install Guide. Owner: Agent47
- [x] Issue 38: Versioning And Releases. Owner: Agent47
- [ ] Issue 39: PyPI Or GitHub Release. Owner:

### Phase 12: Evals And Reliability

- [x] Issue 40: Local Eval Harness. Owner: Agent47
- [x] Issue 41: Regression Tasks. Owner: Agent47
- [ ] Issue 42: Error Reporting. Owner:

### Phase 13: Documentation

- [ ] Issue 43: User Guide. Owner:
- [ ] Issue 44: Developer Guide. Owner:
- [ ] Issue 45: Examples. Owner:

### Phase 14: Security And Public Readiness

- [ ] Issue 46: Security Policy. Owner:
- [ ] Issue 47: Threat Model. Owner:
- [x] Issue 48: Prompt Injection Defenses. Owner: Agent47
- [x] Issue 49: Public Alpha Checklist. Owner: Agent47

### Phase 15: VS Code Later

- [x] Issue 50: VS Code Architecture Decision. Owner: Agent47
- [ ] Issue 51: Extension Scaffold. Owner:
- [ ] Issue 52: Diff Approval UI. Owner:

## Phase 1: Trust And Verification

Goal: make the agent truthful about what it did.

Status: **Next priority**

### Issues

#### 1. Mutation Tracking

Owner suggestion: Agent Core Owner

Build a mutation tracker that records every attempted file change.

Tasks:

- Track `write_file` attempts.
- Track `edit_file` attempts.
- Track `delete_file` attempts.
- Track target path.
- Track whether the tool succeeded.
- Track whether the file exists after write.
- Track whether content changed after edit.
- Store mutation results in SQLite.
- Include mutation summary in final answer context.

Acceptance criteria:

- If a write fails, final answer cannot say it succeeded.
- If a write succeeds, final answer can include verified path.
- Tests cover success, dry-run skip, permission denied, and missing file.

Status: **Done for baseline**. Agent47 records write, edit, patch, and delete mutation attempts in run steps, verifies successful tool reports against disk state, stores post-mutation facts such as file existence, content match, and content-changed status, and uses those records to reject unverified final claims.

#### 2. Final Answer Verification Gate

Owner suggestion: Agent Core Owner

Before accepting a `final` action, validate claims against tracked mutations.

Tasks:

- Detect final answers that claim created/edited/written/saved files.
- Compare claims with mutation tracker.
- Reject false success.
- Ask model to correct the final answer.
- Add maximum correction attempts.

Acceptance criteria:

- Model cannot say "I created X" unless X was verified.
- Model can honestly say "I could not create X because write mode is disabled."
- Regression tests for false completion.

Status: **Done**. Final answers that claim file creation or edits are rejected unless the run contains a successful write, edit, or patch mutation.

#### 3. Final Work Report

Owner suggestion: CLI/UX Owner

Add a structured work report at the end of each run.

Report sections:

- Created files
- Edited files
- Commands run
- Tests/builds run
- Denied actions
- Failed actions
- Sandbox path, if used

Acceptance criteria:

- Final report is shown for every non-trivial run.
- Report is stored in SQLite.
- User can understand exactly what happened.

Status: **Done for baseline**. CLI and `agent47` now show a structured work report for non-trivial runs with current task, current step, files being modified, progress, commands executed, validation status, modified files, change summary, changed-line diff review, and final outcome. Reports are persisted in SQLite, shown in history detail, and included in resume context.

## Phase 2: Patch-Based Editing

Goal: stop relying on full-file writes as the normal edit path.

Status: **Not started**

### Issues

#### 4. Patch Model

Owner suggestion: Safety/Tools Owner

Create a patch representation for file edits.

Tasks:

- Define patch schema.
- Support create file patch.
- Support update file patch.
- Support delete file patch later.
- Store patch metadata.
- Add tests for patch parsing.

Acceptance criteria:

- Patch objects can be serialized.
- Patch objects can be previewed.
- Patch objects can be validated before apply.

Status: **Baseline done**. Unified-diff patch actions now produce structured change-set metadata with changed paths, file count, per-file operation, additions, deletions, and totals. The same metadata is carried through approval events, tool results, mutation records, work reports, and JSON result payloads.

#### 5. Patch Preview And Approval

Owner suggestion: Safety/Tools Owner

Show patches before applying.

Tasks:

- Generate unified diff.
- Show target file.
- Ask permission.
- Support approve/deny.
- Support deny with reason.

Acceptance criteria:

- User sees diff before file changes.
- Denied patch does not modify files.
- Approved patch modifies expected files only.

Status: **Baseline done**. Patch approval prompts include a multi-file summary before the unified diff, validate workspace-relative target paths before prompting, and expose the same metadata to JSON frontends for editor diff approval UI.

#### 6. Patch Apply And Verify

Owner suggestion: Safety/Tools Owner

Apply patches safely and verify results.

Tasks:

- Apply create-file patches.
- Apply update patches.
- Detect context mismatch.
- Verify file content after patch.
- Store applied patch in history.

Acceptance criteria:

- Patch apply is deterministic.
- Failed patch gives useful recovery output.
- Agent can retry with updated context.

Status: **Baseline done**. Patch apply runs `git apply --check` before applying, records check/apply stage metadata, verifies changed files against disk state, and feeds failed patch checks back into the normal recovery loop.

#### 7. Patch Revert

Owner suggestion: Safety/Tools Owner

Allow reverting agent changes.

Tasks:

- Store inverse patch.
- Add `code-agent revert <run-id>`.
- Add `/revert <run-id>` in `agent47`.

Acceptance criteria:

- A run with file changes can be reverted.
- Revert is tested.

Status: **Baseline done**. Successful verified mutations now store inverse patches plus before/after file hashes and existence facts. Added `code-agent revert <run-id>` and `/revert <run-id>` to preview inverse patches, apply them through the normal patch approval path, fail on conflicts, and verify reverted files against recorded pre-change state. Tests cover approved revert, denied revert, conflict protection, created-file removal, and CLI revert.

## Phase 3: Permissions And Policy

Goal: make permissions usable and safe.

Status: **Started**

### Issues

#### 8. Permission Scopes

Owner suggestion: CLI/UX Owner

Add better permission choices.

Options:

- Allow once
- Allow this file for this run
- Allow this command once
- Deny
- Deny all for this run

Acceptance criteria:

- Repeated reads of approved files do not spam the user.
- Dangerous operations still ask.
- Permission choices are stored per run only.

#### 9. Shell Command Policy

Owner suggestion: Safety/Tools Owner

Classify shell commands by risk.

Categories:

- Safe read-only
- Test/build
- Install
- Git
- Network
- Destructive
- Unknown

Tasks:

- Parse command safely.
- Prompt with risk label.
- Block destructive commands by default.
- Add allowlist/denylist config.

Acceptance criteria:

- `pytest` and `ruff` are recognized as test/check.
- `pip install` and `uv add` are recognized as install.
- `rm`, `del`, `Remove-Item`, `git reset --hard` are blocked unless explicitly allowed.

Status: **Baseline done**. Shell commands are classified as read-only, verification, git, install/network, destructive, or unknown. Approval prompts include category, risk, and reason, and clearly destructive commands are blocked before approval. Configurable allow/deny profiles are still future work.

#### 10. Secret Redaction

Owner suggestion: Safety/Tools Owner

Prevent secrets from appearing in model context or logs.

Tasks:

- Redact known env key names.
- Redact API-key-like strings.
- Prevent reading `.env` by default.
- Scrub tool outputs before storage.

Acceptance criteria:

- `.env` is not read without explicit override.
- Stored history does not contain API keys.
- Tests cover redaction patterns.

Status: **Baseline done**. Sensitive credential files are refused by read/write/edit/delete/patch tools, and tool outputs redact common secret key/value pairs, bearer tokens, and OpenAI-style secret keys before model/storage use. A future explicit override flow can be added if needed.

#### 11. Network Permission Policy

Owner suggestion: Safety/Tools Owner

Control web access.

Tasks:

- Ask before web search.
- Add approved domains for a run.
- Add blocked domains.
- Log URLs searched.

Acceptance criteria:

- Web access is visible and auditable.
- User can deny all web access for a run.

Status: **Baseline done**. Web search approval details now include provider domains and query text, outbound fetch URLs are restricted to public HTTP/HTTPS targets, and localhost/private-network result URLs are filtered before model use. Per-run domain allow/deny choices are still future work.

## Phase 4: Sandbox And Isolation

Goal: make risky work happen away from the real repo.

Status: **Started**

### Issues

#### 12. Sandbox Diff View

Owner suggestion: CLI/UX Owner

Show differences between sandbox and base workspace.

Tasks:

- Add `code-agent sandbox diff`.
- Add `/sandbox diff`.
- Show changed files.
- Show unified diffs.

Acceptance criteria:

- User can inspect sandbox changes before applying.

Status: **Baseline done**. Added `code-agent sandbox diff <sandbox-path>` and `/sandbox diff` to show changed files plus unified diffs between the base workspace and active sandbox. Diffing skips ignored local state and reports unreadable files instead of silently applying them.

#### 13. Promote Sandbox Changes

Owner suggestion: Safety/Tools Owner

Apply selected sandbox changes to the real workspace.

Tasks:

- Add `code-agent sandbox apply`.
- Add `/sandbox apply`.
- Ask approval per file or all files.
- Verify applied files.

Acceptance criteria:

- Sandbox changes do not affect real workspace until promoted.
- Promotion is patch-based.

Status: **Baseline done**. Added `code-agent sandbox apply <sandbox-path>` and `/sandbox apply`. Promotion generates a patch from sandbox changes, routes it through the normal patch approval/apply path, and verifies promoted base files match the sandbox after apply.

#### 14. Sandbox Cleanup

Owner suggestion: Infrastructure/Release Owner

Manage old sandboxes.

Tasks:

- List sandboxes.
- Delete sandbox by ID.
- Delete old sandboxes.
- Show sandbox size.

Acceptance criteria:

- Users can clean local storage safely.

#### 15. Process Isolation Research

Owner suggestion: Safety/Tools Owner

Research stronger isolation options.

Options:

- Python subprocess restrictions
- Windows Job Objects
- Docker/Podman
- Firejail on Linux
- macOS sandbox-exec alternatives

Acceptance criteria:

- Document recommended approach per OS.
- Create prototype issue for selected approach.

## Phase 5: Testing And Build Automation

Goal: agent should verify its own work.

Status: **Done for the current CLI baseline**

### Issues

#### 16. Project Detector

Owner suggestion: Infrastructure/Release Owner

Detect project type and commands.

Signals:

- `pyproject.toml`
- `package.json`
- `Cargo.toml`
- `go.mod`
- `pytest.ini`
- GitHub workflows

Acceptance criteria:

- Agent can identify Python, Node, Rust, Go basics.
- Agent reports likely test/build/lint commands.

Status: **Done**. Verification command detection covers Python, Node, Rust, and Go project signals.

#### 17. Test Command Runner

Owner suggestion: Agent Core Owner

Run tests after edits.

Tasks:

- Choose relevant test command.
- Ask permission.
- Run command.
- Parse failure output.
- Feed failures back to model.

Acceptance criteria:

- After editing Python code, agent can run pytest.
- Failed tests trigger recovery loop.

Status: **Done**. Successful code mutations trigger focused test commands when detected, and failed automatic verification is fed back to the model for recovery.

#### 18. Build/Lint Runner

Owner suggestion: Agent Core Owner

Run build and lint checks.

Tasks:

- Detect build commands.
- Detect lint commands.
- Add status labels.
- Store results in final report.

Acceptance criteria:

- Final answer says which checks ran and whether they passed.

Status: **Done**. Focused lint, typecheck, test, and build commands can be selected from changed paths, run through the normal shell tool, stored in history, and appended to final summaries.

## Phase 6: Context Engine

Goal: read the right files, not random files.

Status: **Not started**

### Issues

#### 19. Repo Map

Owner suggestion: Agent Core Owner

Build a compact repository map.

Tasks:

- List important files.
- Summarize modules.
- Ignore caches/local state.
- Store map in memory/cache.

Acceptance criteria:

- Agent can answer "what is this project?" without broad random reads.

Status: **Done for baseline**. Agent47 now exposes an approved `repo_map` action that respects ignored local state, detects important project files, summarizes source/test/doc counts, and reports important files plus top-level layout.

#### 20. File Relevance Ranking

Owner suggestion: Agent Core Owner

Choose relevant files for a task.

Signals:

- user prompt terms
- filenames
- symbols
- recent git changes
- test failures

Acceptance criteria:

- Agent asks to read fewer, more relevant files.

Status: **Done for baseline**. Agent47 now exposes an approved `rank_context` action that scores indexed files against the user task using path terms, file kind, tests, docs, source modules, and important project-file signals. Repo-map and ranking usage is recorded in run history and structured work reports.

#### 21. Symbol Index Baseline

Owner suggestion: Agent Core Owner

Use tree-sitter to extract symbols.

Tasks:

- Index functions/classes.
- Support Python first.
- Add TypeScript/JavaScript next.
- Query symbols by name.

Acceptance criteria:

- Agent can locate functions/classes without scanning full files.

Status: **Baseline done**. Added an approved `symbol_index` action that indexes Python classes/functions with `ast` and conservative JavaScript/TypeScript declarations with deterministic parsing. Symbol-index output is recorded as context analysis and marked as untrusted model context before reuse. Tree-sitter-backed richer symbol graphs remain a possible future enhancement.

#### 22. Git Awareness

Owner suggestion: Infrastructure/Release Owner

Use git status and diffs safely.

Tasks:

- Show dirty files.
- Avoid overwriting user changes.
- Include git diff in context when relevant.
- Ask before git commands.

Acceptance criteria:

- Agent warns before editing dirty files.
- Agent final report includes git status summary.

Status: **Baseline done**. Agent47 now exposes an approved `inspect_git_diff` action that reports git status, staged and unstaged changed paths, and optional bounded diff hunks so the model can check dirty work before editing. Runtime work reports include changed-line diff review for mutations.

## Phase 7: Planner And Task State

Goal: make work visible and resumable.

Status: **Started lightly**

### Issues

#### 23. Task Plan Object

Owner suggestion: Agent Core Owner

Add structured plans.

Fields:

- task
- steps
- status
- blockers
- files touched
- checks

Acceptance criteria:

- Agent creates and updates a plan for non-trivial tasks.

Status: **Baseline done**. Agent47 now has a typed `update_plan` action with durable step status checkpoints plus target files, owned files, intended checks, blockers, and risk notes. These checkpoints are stored in run history, resume context, live plan rendering, JSON payloads, and structured work reports so the intended blast radius is visible before edits.

#### 24. Visible Step Progress

Owner suggestion: CLI/UX Owner

Improve output beyond simple status labels.

Tasks:

- Show current step.
- Show completed steps.
- Show next action.
- Show failed action recovery.

Acceptance criteria:

- User can tell what the agent is doing without reading raw tool output.

Status: **Baseline done**. CLI and `agent47` now render the latest durable plan snapshot when a run updates its plan, and plan updates also continue to appear in saved history details and resume context.

#### 25. Session Resume

Owner suggestion: Infrastructure/Release Owner

Resume interrupted runs.

Tasks:

- Store session state.
- Add `code-agent resume <run-id>`.
- Add `/resume <run-id>`.

Acceptance criteria:

- Agent can continue from previous messages and tool results.

Status: **Baseline done**. `code-agent resume <run-id>` and `/resume <run-id>` now build compact continuation context from saved run steps and continue in a new auditable run. Full durable plan-state checkpoints are still tracked under the task-plan work.

## Phase 8: Streaming And UX

Goal: make the CLI feel responsive.

Status: **Not started**

### Issues

#### 26. Streaming Model Output

Owner suggestion: CLI/UX Owner

Stream tokens/status where useful.

Acceptance criteria:

- User sees progress during long model responses.

Status: **Baseline done**. OpenAI-compatible clients support streamed chat completions, the agent consumes streamed chunks when enabled, and CLI/`agent47` show compact `STREAMING` progress markers without dumping raw JSON action tokens. Controls are available through `AGENT_STREAM`, `--stream/--no-stream`, and `/stream [off]`.

#### 27. Better Terminal UI

Owner suggestion: CLI/UX Owner

Improve formatting.

Tasks:

- Use `rich`.
- Color status labels.
- Format diffs.
- Format approvals.
- Format final reports.

Acceptance criteria:

- CLI is readable in real use.

#### 28. Multiline Input

Owner suggestion: CLI/UX Owner

Support longer prompts.

Tasks:

- Add multiline mode.
- Add editor mode later.

Acceptance criteria:

- User can enter multi-paragraph tasks.

## Phase 9: Model Layer

Goal: support reliable model usage.

Status: **Basic**

### Issues

#### 29. Provider Abstraction

Owner suggestion: Agent Core Owner

Support OpenRouter first, more providers later.

Tasks:

- Define provider interface.
- Keep OpenAI-compatible client.
- Add provider config.

Acceptance criteria:

- Model provider can be swapped without changing agent loop.

Status: **Baseline in progress**. Model construction now uses a provider config boundary for OpenAI-compatible clients. Additional concrete providers beyond OpenRouter/OpenAI-compatible APIs are still future work.

#### 30. Model Profiles

Owner suggestion: Agent Core Owner

Add profiles.

Examples:

- fast
- balanced
- coder
- reviewer
- cheap

Acceptance criteria:

- User can run `agent47 --profile coder` or configure profile.

Status: **Done for baseline**. CLI runs support `--profile`, interactive mode supports `/profile`, and settings support `AGENT_PROFILE` plus per-profile model overrides for `planner`, `coder`, `reviewer`, and `fast`.

#### 31. Cost And Token Tracking

Owner suggestion: Infrastructure/Release Owner

Track usage.

Tasks:

- Store prompt tokens.
- Store completion tokens.
- Estimate cost.
- Show run cost.

Acceptance criteria:

- User can see model usage per run.

Status: **Done for baseline**. Agent47 records per-run model attempts in SQLite, includes model usage in structured work reports and JSON results, tracks prompt/completion/total tokens when the provider returns usage, and estimates cost when `AGENT_INPUT_COST_PER_MILLION` and `AGENT_OUTPUT_COST_PER_MILLION` are configured.

#### 32. Model Fallback

Owner suggestion: Agent Core Owner

Fallback when one model fails.

Tasks:

- Retry transient failures.
- Switch model on provider errors.
- Respect budget.

Acceptance criteria:

- Recover from rate limits or model unavailable errors.

Status: **Done for baseline**. Agent47 supports comma-separated `AGENT_FALLBACK_MODELS`, tries configured models in order after provider failures, records failed and successful attempts, and converts all-model failures into auditable blocked run results instead of crashing the process.

## Phase 10: Storage And History

Goal: make runs auditable.

Status: **Started**

### Issues

#### 33. Run Detail View

Owner suggestion: CLI/UX Owner

Add:

```powershell
code-agent history show <run-id>
```

Acceptance criteria:

- Shows messages, tools, mutations, final report.

Status: **Baseline done**. `code-agent history show <run-id>` and `/history-show <run-id>` show saved run metadata and summarized step payloads. Live CLI and `agent47` runs now include structured work reports; richer history report rendering can be added later.

#### 34. History Search

Owner suggestion: Infrastructure/Release Owner

Search past runs.

Acceptance criteria:

- User can search by task, file, status.

#### 35. Storage Migrations

Owner suggestion: Infrastructure/Release Owner

Manage SQLite schema changes.

Acceptance criteria:

- Existing databases can migrate safely.

## Phase 11: Public Packaging

Goal: friends and early users can install cleanly.

Status: **Not ready**

### Issues

#### 36. Package Metadata

Owner suggestion: Infrastructure/Release Owner

Tasks:

- Add license.
- Add classifiers.
- Add author/project URLs.
- Add changelog.

Acceptance criteria:

- Package metadata is PyPI-ready.

Status: **Done for public-alpha baseline**. `pyproject.toml` now includes license, author, classifiers, keywords, and project URLs, with `LICENSE` and `CHANGELOG.md` present.

#### 37. Install Guide

Owner suggestion: CLI/UX Owner

Write install docs for:

- Windows
- macOS
- Linux
- uv
- pipx

Acceptance criteria:

- New user can install without help.

Status: **Done for baseline**. Added `docs/INSTALL.md` with Windows, macOS, Linux, uv, pip editable, and pipx install paths, plus `code-agent doctor` for local install diagnostics and release smoke checks.

#### 38. Versioning And Releases

Owner suggestion: Infrastructure/Release Owner

Tasks:

- Semantic versioning.
- Git tags.
- GitHub release notes.
- Release checklist.

Acceptance criteria:

- Team can cut a release repeatably.

Status: **Done for public-alpha baseline**. Added `docs/RELEASE_CHECKLIST.md` with package, safety, verification, and release gates, plus changelog guidance for dated release entries.

#### 39. PyPI Or GitHub Release

Owner suggestion: Infrastructure/Release Owner

Publish alpha package.

Acceptance criteria:

- Install command works on clean machine.

## Phase 12: Evals And Reliability

Goal: know whether the agent is improving.

Status: **Not started**

### Issues

#### 40. Local Eval Harness

Owner suggestion: Infrastructure/Release Owner

Create small benchmark tasks.

Examples:

- create file
- edit file
- fix test
- refuse unsafe command
- recover from failed read

Acceptance criteria:

- Evals run in CI or locally.

Status: **Done for baseline**. Agent47 now has `code-agent evals`, an offline deterministic eval harness that runs scripted safety and fixture-based coding checks through the real agent loop.

#### 41. Regression Tasks

Owner suggestion: Agent Core Owner

Add tasks for bugs we already saw.

Must include:

- greeting should not inspect repo
- blocked write should not claim success
- denied read should not reveal content
- sandbox write should not touch base repo

Acceptance criteria:

- These never regress silently.

Status: **Done for baseline**. Built-in local evals cover greeting-without-workspace-inspection, blocked-write honesty, denied-read non-leakage, sandbox write isolation, create-file, edit-file, fix-test, and recover-after-failed-read behaviors.

#### 42. Error Reporting

Owner suggestion: Infrastructure/Release Owner

Improve errors.

Tasks:

- Friendly model API errors.
- Friendly permission errors.
- Friendly network errors.
- Debug log option.

Acceptance criteria:

- Users know what to do when something fails.

## Phase 13: Documentation

Goal: make the project understandable.

Status: **Started**

### Issues

#### 43. User Guide

Owner suggestion: CLI/UX Owner

Write docs for:

- `agent47`
- one-shot commands
- permissions
- sandbox
- dry-run/write mode
- history

Acceptance criteria:

- A new user can use the CLI safely.

#### 44. Developer Guide

Owner suggestion: Infrastructure/Release Owner

Write docs for:

- architecture
- adding tools
- adding providers
- writing tests
- safety rules

Acceptance criteria:

- New contributor can pick an issue and start.

#### 45. Examples

Owner suggestion: CLI/UX Owner

Create example workflows:

- inspect project
- create docs file
- fix failing test
- use sandbox
- web search docs

Acceptance criteria:

- Examples are copy-paste friendly.

## Phase 14: Security And Public Readiness

Goal: make public release responsible.

Status: **Not started**

### Issues

#### 46. Security Policy

Owner suggestion: Safety/Tools Owner

Add `SECURITY.md`.

Acceptance criteria:

- Users know how to report security issues.

#### 47. Threat Model

Owner suggestion: Safety/Tools Owner

Document risks.

Risks:

- prompt injection
- malicious repos
- secret exposure
- destructive shell commands
- unsafe network access
- dependency installation

Acceptance criteria:

- Threat model exists and informs safety issues.

#### 48. Prompt Injection Defenses

Owner suggestion: Agent Core Owner

Tasks:

- Treat repo content as untrusted.
- Add prompt rules.
- Add tests with malicious file content.

Acceptance criteria:

- Agent does not follow instructions found in files as system instructions.

Status: **Baseline done**. The system prompt treats repo content, diffs, command output, web results, search results, repo maps, and ranked context as untrusted data. Tool payloads that can contain external or repository-supplied text are marked with `untrusted_content` and a security instruction before returning to the model, with regression coverage for malicious file content.

#### 49. Public Alpha Checklist

Owner suggestion: Infrastructure/Release Owner

Checklist:

- tests pass
- lint passes
- install docs work
- secret redaction works
- permissions work
- sandbox works
- mutation verification works
- patch editing works
- warning labels are clear

Acceptance criteria:

- Team agrees project is alpha-ready.

Status: **Checklist done**. `docs/RELEASE_CHECKLIST.md` defines the public-alpha gates the team must pass before a release. Actual alpha readiness still depends on running and signing off those gates for a specific release candidate.

## Phase 15: VS Code Later

Goal: editor integration after CLI core is trusted.

Status: **Future**

### Issues

#### 50. VS Code Architecture Decision

Owner suggestion: CLI/UX Owner

Decide how extension talks to Python core.

Options:

- subprocess
- JSON-RPC over stdin/stdout
- local service

Acceptance criteria:

- Team chooses one approach and documents tradeoffs.

Status: **Done for baseline**. Agent47 now exposes `code-agent run-json`, a subprocess-friendly newline-delimited JSON event protocol for status, action starts, approval requests, approval resolutions, recovery, failures, work reports, and final results. JSON mode fails closed by default, supports `--approval-stdin` for parent editor processes to answer approval requests, and keeps `--approve-all` limited to trusted automation. VS Code can build on this transport before a fuller JSON-RPC layer is needed.

#### 51. Extension Scaffold

Owner suggestion: CLI/UX Owner

Create minimal VS Code extension.

Acceptance criteria:

- Opens sidebar.
- Can call CLI core.

#### 52. Diff Approval UI

Owner suggestion: CLI/UX Owner

Show patches in VS Code.

Acceptance criteria:

- User can approve/deny edits from editor.

## Recommended Work Split For 4 People

### Sprint 1

Person 1: Mutation tracking

- Issue 1
- Issue 2
- Issue 3

Person 2: Patch editing foundation

- Issue 4
- Issue 5
- Issue 6

Person 3: Permission UX

- Issue 8
- Issue 24
- Issue 27

Person 4: Project detection and release hygiene

- Issue 16
- Issue 36
- Issue 37
- Issue 40

### Sprint 2

Person 1:

- Test runner
- build/lint runner
- recovery integration

Person 2:

- sandbox diff
- sandbox apply
- patch revert

Person 3:

- history detail view
- session resume
- better terminal UI

Person 4:

- storage migrations
- eval tasks
- security policy
- CI improvements

## Definition Of Public Alpha

Agent47 can be public alpha when:

- It installs cleanly on a fresh machine.
- It never claims unverified file changes.
- It edits through patches.
- It asks permission for risky actions.
- It has a usable sandbox workflow.
- It can run tests/checks and report results.
- It has a security warning and threat model.
- It has enough docs for strangers to use it.
- It has CI and a basic eval suite.

## Definition Of Industry-Level Agent

Agent47 becomes industry-level when:

- It is reliable across real repositories.
- It has strong sandboxing.
- It has strong prompt-injection defenses.
- It supports multi-model/provider workflows.
- It has robust context selection.
- It has resumable sessions.
- It has audited history.
- It has patch review and revert.
- It has install/release stability.
- It has measurable evals.
- It has a strong UX that users trust.

## Immediate Next Issues To Create

Create GitHub issues in this order:

1. Add mutation tracking for write/edit actions.
2. Verify final answers against mutation tracker.
3. Add structured final work reports.
4. Define patch schema.
5. Add patch preview and approval.
6. Add patch apply verification.
7. Add permission scopes.
8. Add shell command risk classification.
9. Add sandbox diff command.
10. Add project type/test command detector.
