# Roadmap

## Phase 1: Reliable CLI

- Add streaming model output.
- Add explicit approval prompts before shell execution and file writes.
- Add structured patch application instead of direct full-file writes.
- Improve run history with session IDs and resume support.
- Add richer tests for file tools, storage, and model mocking.

## Phase 2: Better Coding Agent

- Add repository summaries and context selection.
- Add tree-sitter powered symbol extraction.
- Add git diff awareness.
- Add test/lint command detection.
- Add task planning and step status.
- Add safer shell command policies.

## Phase 3: Collaboration

- Add issue templates and pull request templates.
- Add CI for tests and linting.
- Add documented contribution workflow.
- Add examples for common agent tasks.

## Phase 4: Editor Integration

- Add a VS Code extension package.
- Start with subprocess calls to the Python CLI.
- Move to a JSON protocol for interactive sessions.
- Add sidebar chat, file context, diffs, approvals, and terminal output.

## Phase 5: Multi-Model

- Add provider abstraction.
- Add model profiles.
- Add router policies for planning, coding, and reviewing.
- Add cost and latency tracking.
