# Roadmap

This is the build map for turning Agent47 from a promising CLI agent into an industry-standard AI coding agent.

## Current Position

Agent47 already has a reusable Python core, a CLI and interactive shell, OpenRouter/OpenAI-compatible model access, typed tool actions, durable plan checkpoints, non-workspace intent routing, local file/search/shell/web tools with general web-search fallback, structured patch application, git-diff awareness, verification command detection/suggestion/automatic execution/outcome summaries, permission prompts, dry-run mode, local sandbox copies, SQLite run history with detail views and resume support, operation status labels, cleaner bordered terminal panels with short transcript context and structured session state, failure recovery, optional tree-sitter summaries, and an Agent47 engineering protocol in the system prompt.

## Industry-Standard Capability Checklist

| Capability area | Current state | What is left |
| --- | --- | --- |
| Agent loop | Basic typed action loop with failure recovery, non-workspace routing, and durable plan checkpoints | Add richer task decomposition, cancellation, pause/resume, and bounded long-running work |
| Code editing | Full-file writes, exact text replacement, and approved `git apply` patches | Add multi-file change set metadata, conflict handling, rollback support, and formatting hooks |
| Repository intelligence | File listing, ripgrep search, optional code summaries, approved git status/diff inspection | Add repo index, symbol graph, dependency graph, ownership hints, and context ranking |
| Verification | Agent can detect likely verification commands, suggest focused checks from changed paths, automatically run focused checks after successful mutations, feed failed checks back to the model, and summarize verification outcomes | Add richer retry policy and failure-output parsing |
| Safety and sandboxing | Workspace path guard, dry-run, permission prompts, copy sandbox, shell command risk policy, destructive-command blocking, public-web network policy, and secret redaction | Add configurable command allow/deny policy, process limits, timeout tiers, and deeper secret scanning |
| Model layer | Single OpenAI-compatible chat client | Add provider abstraction, model profiles, planner/coder/reviewer routing, fallback models, token/cost tracking, and streaming |
| Memory and sessions | SQLite run history with detail views, resumable runs, durable plan checkpoints, and in-memory interactive session state for follow-ups | Add conversation checkpoints, per-repo memory, decision logs, and context compaction |
| Collaboration | Basic docs and GitHub setup | Add branch/commit/PR workflow, review mode, issue ingestion, changelog generation, and release notes |
| Developer UX | CLI, `agent47` interactive mode, status labels, bordered panels for prompts and responses, short transcript and session-state context for follow-ups | Add richer TUI streaming, approval diff views, command output panes, transcript export, and VS Code integration |
| Observability | Stored steps and simple status | Add structured traces, tool timing, model usage metrics, failure analytics, and debug bundles |
| Evaluation | Unit tests for core behavior | Add agent task benchmarks, golden transcript tests, sandboxed fixture repos, regression scenarios, and quality gates |
| Packaging | Python package with uv workflow | Add signed releases, config profiles, install docs for common platforms, and upgrade/migration notes |

## Build Phases

## Phase 1: Trustworthy Editing

- Done: add a first-class `apply_patch` action instead of relying on full-file writes for code edits.
- Done: show unified diffs before approval and validate patch paths before applying.
- Store approved patches with richer metadata in run history.
- Add multi-file change set support with one approval prompt per coherent change.
- Add formatting hooks for common stacks after patch application.
- Add tests for patch parsing, apply failures, and partial-application recovery.

## Phase 2: Real Verification Loop

- Done: detect project test, lint, typecheck, and build commands from files such as `pyproject.toml`, `package.json`, `Cargo.toml`, and `go.mod`.
- Done: suggest targeted verification commands based on changed files.
- Done: store verification-like shell command outcomes and append them to final summaries.
- Done: automatically run selected verification commands after successful file mutations when appropriate.
- Done: feed failed automatic verification back to the model for recovery.
- Improve failure-output parsing and bounded retry policy.

## Phase 3: Repository Intelligence

- Done: add git diff awareness so Agent47 can inspect dirty files before editing.
- Build a lightweight repo index with files, symbols, imports, and recently changed paths.
- Rank context by task relevance instead of reading broad files.
- Add code ownership and architectural summary files under `.code-agent/`.

## Phase 4: Safer Autonomy

- Done: add command policy classes for read-only, verification, git, install/network, destructive, and unknown operations.
- Done: block destructive shell commands before approval.
- Done: add public-web network policy for web search with local/private target blocking.
- Done: add redaction for common secret patterns in tool outputs.
- Add sandbox process limits, timeout tiers, and optional network-deny mode for shell commands.
- Add cancellation and clean shutdown for running tool calls.

## Phase 5: Better Model System

- Add provider abstraction beyond OpenRouter/OpenAI-compatible chat.
- Add model profiles for fast planning, deep coding, reviewing, and summarization.
- Add fallback routing when a model fails or returns invalid actions repeatedly.
- Add token, latency, and cost tracking per run.
- Add streaming responses in CLI and interactive mode.

## Phase 6: Durable Sessions and Memory

- Done: add an in-memory interactive session state for current task, pending user info, target files, changed files, blockers, and recent tool results.
- Done: add run detail views and resume support from saved tool history.
- Done: save durable plan updates with step statuses in run history and resume context.
- Save patch sets, richer verification results, and final summaries.
- Add per-repo memory for conventions, preferred commands, and recurring project facts.
- Add context compaction for long tasks.

## Phase 7: Collaboration Workflow

- Add git branch, commit, and PR helper actions with explicit approval.
- Add issue/PR template awareness.
- Add review mode that focuses on bugs, regressions, tests, and security risks.
- Add release-note and changelog generation.

## Phase 8: Editor Integration

- Add a local JSON protocol so frontends can drive the Python core.
- Build a VS Code extension with sidebar chat, file context, approval UI, diffs, terminals, and run history.
- Support editor selections and open files as first-class context.
- Add background task notifications.

## Phase 9: Evaluation and Product Hardening

- Create fixture repositories for common tasks and regression testing.
- Add golden transcript tests for agent behavior.
- Track solve rate, edit correctness, verification rate, tool failures, and user intervention rate.
- Add CI gates for unit tests, lint, typecheck, package build, and agent benchmark smoke tests.
- Document supported workflows, limits, safety model, and troubleshooting.
