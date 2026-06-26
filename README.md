# Agent47

A Python CLI-first AI coding agent scaffold. The core logic lives outside the CLI so it can later be reused by a VS Code extension shell or background service.

The long-term goal is Agent47: a real AI engineering assistant that is repo-aware, tool-using, careful with edits, test-aware, and eventually available inside VS Code.

Agent47 now uses an engineering protocol in its system prompt: classify the request, create durable plans with intended files/checks/risks for non-trivial workspace tasks, inspect relevant files, make focused edits, verify changes when practical, recover from failures, and report honestly about what changed and what was checked.

Agent47 is not limited to the current folder. It can answer general questions directly, use web search for current external information when needed, and only inspect project files when the request is actually about the local workspace.

## Industry-Standard Target

Agent47 is being built toward the baseline expected from a serious AI coding agent:

- Repo-aware context gathering with symbol, dependency, and git-diff awareness.
- Structured patch editing with diff preview, approval, conflict handling, and rollback-friendly history.
- Automatic test, lint, typecheck, and build detection with focused verification loops.
- Safer autonomy through command policies, sandbox limits, secret redaction, and explicit approvals.
- Durable sessions with resumable plans, target-file/check/risk metadata, run history, per-repo memory, and compacted context.
- Multi-model support with provider abstraction, model profiles, fallback routing, streaming, and cost tracking.
- Collaboration workflows for review, commits, branches, pull requests, issues, changelogs, and release notes.
- Editor integration, starting with a JSON protocol and eventually a VS Code extension.
- Evaluation harnesses that measure task success, edit correctness, verification rate, and regressions.

## Quick Start

```bash
uv sync --extra dev
cp .env.example .env
uv run code-agent run "Inspect this project and suggest the next feature"
uv run code-agent doctor
```

Add your OpenRouter key to `.env` before running the agent:

```text
OPENROUTER_API_KEY=...
AGENT_MODEL=qwen/qwen3-coder
```

If `uv` is not installed yet:

```bash
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev,parsing]"
.\.venv\Scripts\code-agent run "Inspect this project"
```

On macOS/Linux:

```bash
export OPENROUTER_API_KEY="your-api-key"
uv run code-agent run "Create a README section describing this project"
```

## CLI Usage

```bash
code-agent doctor
code-agent run "Fix the failing pytest"
code-agent run --preset gemini-flash "Fix the failing pytest"
code-agent run --provider deepseek --model deepseek-v4-pro "Fix the failing pytest"
code-agent run --cwd ../some-project --model qwen/qwen3-coder "Add tests for the parser"
code-agent run --profile coder "Implement the next roadmap item"
code-agent run --profile reviewer "Review the latest changes for regressions"
code-agent run --dry-run "Refactor the CLI argument parser"
code-agent run --sandbox "Try a risky refactor in an isolated copy"
code-agent run --no-stream "Run without compact model streaming progress"
code-agent run --max-failures 5 "Fix the issue and recover from failed attempts"
code-agent run-json --dry-run "Inspect this project and emit JSON protocol events"
code-agent models
code-agent evals
code-agent evals --live --limit 3
code-agent eval-reports
code-agent release-smoke
code-agent history
code-agent history show 12
code-agent history export 12
code-agent resume 12 "Continue from the failed verification"
code-agent revert 12
code-agent sandbox diff .code-agent/sandboxes/sandbox-20260619-120000
code-agent sandbox apply .code-agent/sandboxes/sandbox-20260619-120000
```

With `uv`:

```bash
uv run code-agent run "Fix the failing pytest"
```

Frontend protocol:

```bash
uv run code-agent run-json --dry-run "Inspect this project"
```

`run-json` emits versioned newline-delimited JSON events for frontend integrations. It includes run lifecycle events, status updates, action starts, approval requests, approval resolutions, recovery events, failures, work reports, and final results. Approval requests are denied by default in JSON mode so a frontend can safely observe required permissions. Use `--approval-stdin` to let a parent process reply with one JSON approval response per request, or `--approve-all` only in trusted automation. Stdin approval responses must be JSON objects with `type: "approval_response"`, the matching `request_id`, an `approved` boolean, and an optional `reason`; mismatched or malformed responses fail closed.

Local evals:

```bash
uv run code-agent evals
uv run code-agent evals --json
uv run code-agent evals --live --limit 3
uv run code-agent evals --live --limit 3 --save-report
uv run code-agent eval-reports
```

The eval suite runs deterministic safety regressions plus fixture coding tasks for file creation,
file editing, test fixing, failed-read recovery, dirty-worktree awareness, and patch-conflict
recovery. `--json` emits benchmark-style metrics and per-case results for trend tracking.
Live evals are opt-in because they call the configured model provider and spend tokens; use them
for release-candidate measurement against small generated repos that exercise bug fixing,
multi-file feature work, and prompt-injection resilience.
Use `--save-report` to persist JSON reports under `.code-agent/eval-reports/`; reports include
failure categories, per-case metadata, model/provider settings, changed files, commands,
verification outcomes, and model usage when available.

Release smoke gate:

```bash
uv run code-agent release-smoke
uv run code-agent release-smoke --json
```

The release smoke gate runs unit tests, lint, strict doctor checks, offline evals, and package
build in one repeatable command.

Debug bundles:

```bash
uv run code-agent history export 12
```

The export command writes a redacted JSON bundle with the saved run, steps, work report, model
usage, and summary metrics under `.code-agent/debug-bundles/` by default.

Interactive mode:

```bash
agent47
```

Interactive mode separates prompts, responses, help, status, history, and structured work reports into bordered terminal panels so user input and Agent47 output do not visually merge.
It starts with a centered `A G E N T 4 7` banner and uses a restrained terminal color theme when color output is supported. Set `NO_COLOR=1` to disable color, or `AGENT47_COLOR=always` to force it.
It carries both a short in-memory transcript and structured session state into follow-up turns so Agent47 can continue recent work without relying on magic phrases.
Interactive mode starts write-enabled so file creation and edits can actually happen after approval. Use `/dry-run` when you want inspect-only behavior.

Inside interactive mode, type freely:

```text
agent47: Inspect this project and suggest the next feature
agent47: /status
agent47: /dry-run
agent47: /write
agent47: /stream
agent47: /stream off
agent47: /sandbox
agent47: /sandbox diff
agent47: /sandbox apply
agent47: /sandbox off
agent47: /profile coder
agent47: /max-failures 5
agent47: /history-show 12
agent47: /resume 12 continue from the failed verification
agent47: /revert 12
agent47: /stop
agent47: /exit
```

`agent47` is the only interactive launcher. The old `copilot` alias was removed to avoid colliding with GitHub Copilot.

## Environment

- `OPENROUTER_API_KEY` is required.
- `AGENT_PROVIDER` is optional and defaults to `openrouter`. Supported values are `openrouter`, `openai`, `gemini`, and `deepseek`.
- `AGENT_MODEL_PRESET` is optional. Supported presets are `qwen-coder`, `gemini-flash`, `gemini-pro`, `deepseek-flash`, and `deepseek-pro`.
- `AGENT_MODEL` is optional. The CLI also accepts `--model`.
- `AGENT_PROFILE` is optional and defaults to `default`. Supported profiles are `default`, `planner`, `coder`, `reviewer`, and `fast`.
- `AGENT_PLANNER_MODEL`, `AGENT_CODER_MODEL`, `AGENT_REVIEWER_MODEL`, and `AGENT_FAST_MODEL` are optional per-profile model overrides.
- `AGENT_FALLBACK_MODELS` is optional. Use a comma-separated list of models to try if the primary model/provider call fails.
- `OPENROUTER_BASE_URL` is optional and defaults to `https://openrouter.ai/api/v1`.
- `OPENROUTER_SITE_URL` and `OPENROUTER_APP_NAME` are optional OpenRouter metadata headers.
- `GEMINI_API_KEY` is required when `AGENT_PROVIDER=gemini`.
- `GEMINI_BASE_URL` is optional and defaults to `https://generativelanguage.googleapis.com/v1beta/openai/`.
- `DEEPSEEK_API_KEY` is required when `AGENT_PROVIDER=deepseek`.
- `DEEPSEEK_BASE_URL` is optional and defaults to `https://api.deepseek.com`.
- `AGENT_MAX_TOKENS` is optional and defaults to `4096`.
- `AGENT_MAX_FAILURES` is optional and defaults to `3`.
- `AGENT_INPUT_COST_PER_MILLION` and `AGENT_OUTPUT_COST_PER_MILLION` are optional. When both are set, Agent47 estimates per-run model cost from provider token usage.
- `AGENT_DB_PATH` is optional and defaults to `.code-agent/agent.db`.
- `AGENT_STREAM` is optional and defaults to `true`. It enables compact model streaming progress without printing raw JSON action tokens.
- `OPENAI_API_KEY` and `OPENAI_BASE_URL` are still accepted as a temporary fallback.

`--model` always wins over profile-specific model environment variables for that run. Profiles still control temperature and token defaults.

Model switching:

```bash
uv run code-agent models
uv run code-agent run --preset gemini-flash "Fix the failing test"
uv run code-agent run --preset deepseek-pro "Refactor the parser"
uv run code-agent run --provider gemini --model gemini-3.5-flash "Inspect this repo"
```

Preset selection chooses both provider and model. `--model` can still override the model string
while keeping the preset provider, and `--provider` can override the provider for advanced cases.
If a preset needs a missing key, Agent47 fails with a targeted message such as
`GEMINI_API_KEY is required for AGENT_PROVIDER=gemini`.

Gemini and DeepSeek can be used either through OpenRouter model slugs or through their direct
OpenAI-compatible endpoints:

```bash
AGENT_PROVIDER=gemini
GEMINI_API_KEY=...
AGENT_MODEL=gemini-3.5-flash
```

```bash
AGENT_PROVIDER=deepseek
DEEPSEEK_API_KEY=...
AGENT_MODEL=deepseek-v4-pro
```

Check the provider docs for current model IDs before pinning production profiles.

For platform-specific setup, see [docs/INSTALL.md](docs/INSTALL.md). To check a local installation without making network calls, run:

```bash
code-agent doctor
```

## Permissions

The agent asks for confirmation before:

- listing files
- reading files
- searching the project
- summarizing code structure
- detecting and suggesting verification commands
- inspecting git status and diffs
- building a compact symbol index
- writing files
- editing files
- applying structured patches
- running shell commands
- searching the web

All file access remains workspace-guarded. Sensitive local credential files such as `.env`, `.npmrc`, `.pypirc`, and `.netrc` are refused by default so their contents do not enter model context.

Project search uses `ripgrep` when available and falls back to a built-in Python search when `ripgrep` is missing from the agent process PATH. Search skips local state and secret files such as `.env`, `.git`, `.code-agent`, caches, and virtual environments.

Shell commands are classified before approval. Destructive commands such as `git reset --hard`, recursive force deletes, and aggressive `git clean` forms are blocked by policy; install/network commands are labeled high risk; verification and read-only commands get lower-risk labels. Tool outputs are redacted for common secret patterns before they are returned to the model or stored. Large approval previews and mutation diffs are bounded so long generated files do not flood the terminal or model context.

File mutations are verified against disk state before Agent47 trusts them in final answers. Writes must leave the requested content on disk, edits and patches must change content, and deletes must remove a file that existed before the action.

Patch approvals include a change-set summary before the unified diff, and applied patches record structured metadata for every changed file, including operation, additions, deletions, and total file count. JSON frontends receive the same metadata on approval events and final payloads.

Web search approval prompts include the provider domains and query. Agent47 blocks localhost, private-network, link-local, reserved, and multicast web targets, and filters unsafe result URLs before returning search results.

Repository content, command output, search results, diffs, web results, repo maps, ranked context, symbol indexes, and dependency graphs are treated as untrusted data in the model loop. Tool payloads that can contain external or repo-supplied text are marked with `untrusted_content` and a security instruction so prompt-injection text in files or tool output is not promoted into model instructions.

## Failure Recovery

The agent automatically loops after failed tool calls. It feeds the failure back to the model with recovery instructions so the model can inspect, retry, or choose another action.

```bash
code-agent run --max-failures 5 "Fix the failing test"
```

It stops after the configured consecutive failure budget is exhausted.

## Operation Status

The CLI prints status lines while the agent works:

- `THINKING` before model reasoning.
- `STREAMING` while a streaming-capable model response is arriving. Agent47 shows compact progress markers instead of raw JSON action text.
- `READING` when listing or reading files.
- `SEARCHING` for project search.
- `SEARCHING WEB` for general web search.
- `CHECKING project verification commands` when detecting test/lint/build commands.
- `CHECKING suggested verification` when choosing focused checks for changed files.
- `PLANNING updating task plan` when checkpointing durable plan steps and planner metadata.
- `READING git changes` when inspecting dirty files before editing.
- `ANALYZING symbol index` when locating functions, classes, and exported declarations.
- `ANALYZING dependency graph` when mapping imports and internal source/test dependencies.
- `EDITING` for file writes and edits.
- `EDITING applying patch` for structured patch edits.
- `INSTALLING`, `BUILDING`, `TESTING`, or `CHECKING` for recognized shell commands.
- `RECOVERING` when a tool fails and the agent is trying another path.
- Invalid model action responses are retried automatically, including common cases where a valid JSON action is wrapped in prose or a code fence.
- `DONE` when the agent reaches a final answer.

After non-trivial runs, the CLI and interactive shell render a structured work report before the final response. It shows current task, current step, files being modified, planned targets, file ownership, planned checks, blockers, risk notes, progress, context analysis, model usage, commands executed, validation status, modified files, change summary, changed-line diff review, and final outcome.

## Stopping The Agent

Use `Ctrl+C` to stop a running operation.

Inside `agent47`, you can also quit between prompts with:

```text
/stop
/exit
```

Esc is not used as the default stop key because most terminals treat it as line-editing input rather than a process interrupt.

## Sandbox

Use sandbox mode when you want the agent to experiment without touching the real project:

```bash
code-agent run --sandbox "Try changing the CLI flow"
```

In `agent47`:

```text
agent47: /sandbox
agent47: /write
agent47: Try the change in the sandbox
agent47: /sandbox diff
agent47: /sandbox apply
agent47: /sandbox off
```

Sandboxes are copied into `.code-agent/sandboxes/` and exclude `.env`, `.git`, `.venv`, caches, and other local state.
Use `code-agent sandbox diff <sandbox-path>` to inspect changed files and unified diffs. Use `code-agent sandbox apply <sandbox-path>` to promote approved sandbox changes back to the base workspace through the same patch approval and verification pipeline as normal edits.

## Project Shape

```text
src/
  code_agent/
    agent.py            Agent loop
    cli.py              Typer CLI entrypoint
    interactive.py      Copilot-style terminal session
    factory.py          Shared agent construction
    config.py           dotenv + pydantic-settings
    doctor.py           Local install and platform diagnostics
    models.py           OpenAI-compatible model client for OpenRouter
    prompts.py          System prompt
    protocol.py         Versioned JSON event protocol for future frontends
    resume.py           Run detail formatting and resume context
    sandbox.py          Local workspace sandbox copies
    session.py          Interactive session state
    schema.py           Shared action/result models
    storage.py          SQLite run history
    parsing.py          Optional tree-sitter code summaries
    tools.py            pathlib, difflib, subprocess, ripgrep, and git-awareness tools
tests/
  test_path_safety.py
```

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md).

For the complete team issue breakdown and checkbox task board from the current CLI to an industry-grade Agent47, see [docs/TEAM_BUILD_PLAN.md](docs/TEAM_BUILD_PLAN.md).

For public alpha release gates, see [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md). Release notes are tracked in [CHANGELOG.md](CHANGELOG.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
