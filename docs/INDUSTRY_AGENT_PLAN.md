# Agent47 Industry Agent Plan

This is the implementation to-do list for growing Agent47 into a production-grade coding
agent comparable in product shape to tools like Claude Code or Codex.

## Guiding Priorities

1. Keep the CLI core trustworthy before building editor features.
2. Measure capability with evals before claiming capability in docs.
3. Prefer safe, reviewable patches over blind full-file rewrites.
4. Treat model/provider support as routing infrastructure, not a pile of model names.
5. Make every risky operation auditable, reversible, and easy to explain.

## Priority 0: Baseline Stabilization

Goal: keep the current green baseline reliable while new features land.

To-do:

- Keep `uv run pytest` green.
- Keep `uv run code-agent evals` green.
- Add a release gate command that runs tests, lint, evals, and package build. Status: done with `code-agent release-smoke`.
- Add a short "known limitations" section to public docs.

Implementation plan:

- Add a `release-smoke` or documented release checklist command path.
- Expand CI to run unit tests and local evals.
- Update `docs/RELEASE_CHECKLIST.md` with exact commands and expected outputs.
- Require every new capability to include at least one unit test or fixture eval.

Acceptance criteria:

- A contributor can run one documented sequence and know whether the repo is releasable.
- CI fails if tests or local evals fail.

## Priority 1: Storage Migrations

Goal: make saved run history safe across public releases.

To-do:

- Add a schema version table. Status: done.
- Move ad hoc schema updates into explicit migrations. Status: done.
- Add migration tests from old database shapes. Status: done.
- Add doctor output for current DB version. Status: done.
- Add backup-before-migrate behavior for non-empty databases. Status: done.

Implementation plan:

- Create a `schema_migrations` table in `AgentStorage`.
- Define migration functions such as `001_initial`, `002_model_usage_cost`, and future steps.
- On startup, read the current DB version, run pending migrations in order, and record success.
- Keep migrations idempotent where practical, but test real old snapshots too.
- Add tests that create an older SQLite schema, initialize `AgentStorage`, and verify upgraded tables.

Acceptance criteria:

- Existing `.code-agent/agent.db` files continue to open after upgrades.
- Failed migration does not silently corrupt history.
- `code-agent doctor` reports DB path and schema version.

## Priority 2: Benchmark-Grade Evals

Goal: measure real coding-agent ability, not just deterministic tool behavior.

To-do:

- Add fixture repos for larger tasks. Status: partial, with dirty-worktree and patch-conflict fixtures.
- Track solve rate, verification rate, recovery rate, and false-success prevention. Status: partial, with JSON metrics and category pass rates.
- Add explicit live-model evals for release-candidate measurement. Status: started with `code-agent evals --live`.
- Save eval reports as JSON for trend tracking. Status: started with `--save-report` and `code-agent eval-reports`.
- Add benchmark categories for Python, Node, docs, CLI, and mixed multi-file work. Status: started with a broader CLI live benchmark catalog.
- Add prompt-injection and dirty-worktree scenarios. Status: started in live evals.
- Add model/provider comparison summaries. Status: started with `code-agent eval-reports --summary`.
- Add a capability dashboard and release-readiness gate over saved reports. Status: first pass done with
  `code-agent eval-reports --dashboard`.
- Add failure analytics with per-case traces and latest-vs-previous regression lists. Status: done with
  `code-agent eval-reports --analytics`.

Implementation plan:

- Extend `EvalSuiteResult` with machine-readable metrics.
- Add fixture directories or generated repos for each benchmark scenario.
- Keep deterministic scripted-model evals for safety regressions.
- Add optional live-model evals behind an explicit flag such as `--live`.
- Record per-case changed files, commands run, verification status, and final message.

Acceptance criteria:

- `code-agent evals` remains offline and deterministic by default.
- `code-agent evals --live` can evaluate a configured model on real tasks.
- Eval output shows pass/fail plus capability metrics.
- Saved eval reports can be rolled up into a dashboard with latest-run deltas, category pass rates,
  verification/change rates, failure hotspots, and release-gate recommendations.
- Failed eval reports can be diagnosed without reading raw output by using compact case traces,
  failure classes, verification evidence, timing metadata, and regression lists.

## Priority 3: Observability And Debug Bundles

Goal: make every failed run explainable.

To-do:

- Track tool timing.
- Track tool timing. Status: baseline done in saved tool-result payloads.
- Track model latency. Status: baseline done in model usage payloads when usage records are drained.
- Track retry counts and failure categories.
- Export a debug bundle for one run. Status: done with `code-agent history export`.
- Include redacted prompts, actions, tool results, diffs, verification output, and model usage. Status: done for saved run payloads, work reports, and model usage.

Implementation plan:

- Add timing fields to stored step payloads.
- Add a `code-agent history export <run-id>` command.
- Write an export file under `.code-agent/debug-bundles/`.
- Redact secrets before writing bundle contents.
- Add tests for export shape and redaction.

Acceptance criteria:

- A failed run can be shared as a redacted artifact.
- Debug bundles include enough context to reproduce the failure path.

## Priority 4: Stronger Sandbox And Process Isolation

Goal: make shell execution safer for real projects.

To-do:

- Add timeout tiers by command risk. Status: done through shell policy enforcement.
- Add cancellation for running shell commands. Status: baseline done with a shared process supervisor
  and agent-level cancellation hook.
- Add optional network-deny mode.
- Add process tree cleanup on timeout or stop. Status: baseline done for shell timeout, Ctrl+C, and
  explicit cancellation paths; OS-level isolation remains future work.
- Research platform-specific isolation options for Windows, macOS, and Linux.

Implementation plan:

- Extend `ShellPolicy` with timeout, network, and write flags already present in the model.
- Enforce timeout tiers consistently in `ToolRegistry._run_shell`.
- Add process cleanup tests for timeouts, cancellation, and keyboard interrupts.
- Add a documented isolation strategy per platform.
- Keep stronger isolation optional until the cross-platform behavior is proven.

Acceptance criteria:

- Long-running commands terminate cleanly.
- Riskier commands get stricter timeouts and clearer approval detail.
- A stopped run does not leave unmanaged child processes behind.

## Priority 5: Provider Layer For Gemini And DeepSeek

Goal: support Gemini and DeepSeek cleanly, both through OpenRouter and direct provider APIs.

To-do:

- Keep OpenRouter model-slug support as the simplest path. Status: done.
- Add named provider selection: `openrouter`, `openai`, `gemini`, `deepseek`. Status: done.
- Add provider-specific API keys and base URLs. Status: done.
- Add provider/model profile routing.
- Add Cline-style model preset switching. Status: done with `AGENT_MODEL_PRESET`, `--preset`, and `code-agent models`.
- Add provider-aware usage records.
- Add config examples for Gemini and DeepSeek. Status: done.

Implementation plan:

- Extend `Settings` with:
  - `AGENT_PROVIDER`
  - `GEMINI_API_KEY`
  - `GEMINI_BASE_URL`
  - `DEEPSEEK_API_KEY`
  - `DEEPSEEK_BASE_URL`
- Refactor `Settings.model_api_key`, `model_base_url`, and `model_headers` into a provider resolver.
- Keep OpenRouter as the default provider so current users do not break.
- For DeepSeek direct API, use the existing OpenAI-compatible client.
- For Gemini direct API, use the Gemini OpenAI-compatible endpoint.
- Add provider-level tests with fake clients and no network calls.
- Update `.env.example`, README, and doctor checks.

Recommended model routing:

- Default/coder: strong coding model such as Qwen Coder, DeepSeek coder/chat, or Gemini Pro-class model.
- Planner: strongest reasoning model available in the selected provider.
- Reviewer: low-temperature model with good instruction following.
- Fast: cheaper flash/chat model for small tasks.
- Fallbacks: mix providers, for example primary OpenRouter plus direct DeepSeek or Gemini fallback.

Acceptance criteria:

- Users can run via OpenRouter with `AGENT_MODEL=google/...` or `AGENT_MODEL=deepseek/...`.
- Users can run direct DeepSeek with `AGENT_PROVIDER=deepseek`.
- Users can run direct Gemini with `AGENT_PROVIDER=gemini`.
- Users can run NVIDIA NIM models with `AGENT_PROVIDER=nvidia`, including the `glm-5.2` and `deepseek-v4-flash` presets.
- Usage records identify both provider and model.
- Existing OpenRouter setup continues to work unchanged.

## Priority 6: Per-Repo Memory

Goal: let Agent47 remember stable project facts without rereading everything every run.

To-do:

- Add `.code-agent/memory/` for local, git-ignored repo memory. Status: baseline done with
  `.code-agent/memory/project.md`.
- Store preferred commands, architecture notes, package manager, test strategy, and conventions.
  Status: baseline done with sectioned Markdown for conventions, user preferences, architecture notes,
  commands, pitfalls, glossary, successful patterns, verification strategy, dependencies/integrations,
  and release/migration notes.
- Add explicit user approval before writing memory. Status: done.
- Add memory summaries to resume/context. Status: done through automatic workspace context preflight.

Implementation plan:

- Add a memory read action that returns bounded project metadata generated by Agent47 or edited by users.
  Status: done with `read_memory`, marked as untrusted context for prompt-injection safety.
- Add a memory write/update action with approval. Status: done with `update_memory`.
- Keep memory files small and human-readable Markdown or JSON. Status: done with bounded Markdown.
- Add doctor checks for memory directory health. Status: done.

Acceptance criteria:

- Agent47 can reuse stable project conventions across runs.
- Memory never stores secrets or raw large file contents.
- Users can inspect and delete memory easily.

## Priority 7: Collaboration Workflows

Goal: support real engineering workflows around code changes.

To-do:

- Add branch helper. Status: first pass done with `code-agent collab branch`.
- Add commit helper. Status: first pass done with `code-agent collab commit` and
  `code-agent collab commit-message`.
- Add PR summary helper. Status: first pass done with `code-agent collab pr-summary`.
- Add review mode command. Status: first pass done with `code-agent collab review`.
- Add changelog/release-note generation. Status: first pass done with `code-agent collab changelog`.

Implementation plan:

- Add git workflow actions with explicit approval.
- Reuse mutation and verification records to build commit/PR summaries.
- Add a reviewer profile command path that defaults to code-review findings.
- Add tests for approval behavior and generated summary shape.

Acceptance criteria:

- Agent47 can prepare a commit message from verified changes.
- Agent47 can generate a PR summary with tests run and risks.
- Branch and commit mutations are previewed by default and approval-gated when applied.
- Review mode leads with bugs, regressions, missing tests, and security issues.

## Priority 8: Terminal UX Upgrade

Goal: make the CLI pleasant during longer coding sessions.

To-do:

- Add multiline input.
- Add better diff approval views.
- Add command output panes.
- Add transcript export.
- Add clearer model/profile/status display.

Implementation plan:

- Keep `run-json` as the frontend protocol source of truth.
- Improve `agent47` rendering without changing core agent behavior.
- Add terminal snapshot tests where practical.

Acceptance criteria:

- Long prompts are comfortable to enter.
- Patch approval is readable for multi-file changes.
- Users can export a transcript for debugging or sharing.

## Priority 9: VS Code Integration

Goal: bring the existing core into an editor after the CLI is reliable.

To-do:

- Scaffold extension.
- Spawn `code-agent run-json` as a subprocess.
- Implement approval UI.
- Implement diff viewer.
- Pass open files and selections as first-class context.
- Show run history and work reports.

Implementation plan:

- Keep the Python core unchanged where possible.
- Treat VS Code as a client of the NDJSON protocol.
- Add protocol events only when the extension needs missing state.
- Build a minimal extension first: chat, approvals, diffs, run output.

Acceptance criteria:

- A user can approve reads/writes from VS Code.
- File diffs use native editor UI.
- The extension can resume or inspect a previous run.

## Priority 10: Public Readiness And Security

Goal: make the project safe to share and maintain.

To-do:

- Add `SECURITY.md`.
- Add a threat model.
- Document data handling.
- Document command safety limits.
- Add release notes and upgrade notes per version.
- Publish a first alpha release only after migrations and eval reporting are ready.

Implementation plan:

- Write a concrete threat model for prompt injection, secrets, shell commands, network access, and repo writes.
- Add public docs for what the agent will and will not do.
- Add issue templates for security and model/provider bugs.
- Keep public alpha labeled honestly as alpha until live evals and editor UX mature.

Acceptance criteria:

- Users understand the risk model before running write-enabled agent tasks.
- Security reports have a documented path.
- Releases are repeatable and include migration notes.

## Gemini And DeepSeek Recommendation

Adding Gemini and DeepSeek is a good idea, but it should be done in two layers:

1. Short term: use OpenRouter slugs through the current OpenAI-compatible client.
2. Long term: add direct providers so users can choose cheaper, faster, or more reliable routing.

Suggested `.env` shape:

```text
AGENT_PROVIDER=openrouter
AGENT_MODEL=qwen/qwen3-coder
AGENT_CODER_MODEL=
AGENT_PLANNER_MODEL=
AGENT_REVIEWER_MODEL=
AGENT_FAST_MODEL=
AGENT_FALLBACK_MODELS=

OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

GEMINI_API_KEY=
GEMINI_BASE_URL=

DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

Use current provider docs to choose exact model IDs because model names change over time. The
code should validate that a configured model is non-empty, but should not hardcode a fragile
global list of provider model names.
