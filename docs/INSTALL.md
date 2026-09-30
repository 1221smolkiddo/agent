# Install

Agent47 supports Python 3.11 and newer on Windows, macOS, and Linux. CI currently exercises Python
3.11-3.13 on Linux and Python 3.12 on Windows and macOS. The first published closed-beta package is
`agent47==0.1.0b1`.

## Install The Closed Beta

Install the pre-release explicitly:

```bash
pip install --pre agent47
```

To pin the reviewed beta build instead of accepting later beta updates:

```bash
pip install agent47==0.1.0b1
```

The distribution installs two commands: `code-agent` for the automation CLI and `agent47` for the
interactive terminal. Confirm the installed build before configuring a provider:

```bash
code-agent --help
agent47 --help
```

Starting `agent47` with no arguments launches interactive onboarding. It requires Google sign-in first,
then guides the user to choose a provider and save its API key in the operating system credential manager.
No model session starts until both are complete. `code-agent` remains available for automation and CI.

Use `pip install --upgrade --pre agent47` only after reading that beta update's release notes and backing up
the project's `.code-agent/` directory.

## Recommended Installation

Install `uv`, clone the repository, and create the managed environment:

```bash
uv sync --extra dev --extra parsing
```

Create local configuration:

```bash
cp .env.example .env
```

PowerShell:

```powershell
Copy-Item .env.example .env
```

Set at least one provider key, then run diagnostics:

```text
AGENT_PROVIDER=openrouter
OPENROUTER_API_KEY=...
AGENT_MODEL=qwen/qwen3-coder
```

```bash
uv run code-agent doctor --strict
uv run code-agent models
uv run code-agent run --dry-run "Inspect this repository"
```

## Closed Beta Setup

The closed beta is for supervised testing on real projects. It does not authorize unattended or
unrestricted execution. Before the first non-dry-run task:

1. Back up the project's `.code-agent/` directory, including its SQLite database.
2. Run `uv run code-agent doctor --strict` and resolve failures.
3. Begin with a read-only `--dry-run` task.
4. Use `--sandbox` for work that executes unfamiliar project code when a healthy Docker or Podman backend
   is available.
5. Keep approval gates enabled for external effects and set conservative execution budgets.
6. Retain the execution ID and trace for every beta task so a failure can be investigated or recovered.

For operating limits, incident reports, and beta update policy, see [Closed Beta](BETA.md).

## Editable pip Installation

Create and activate a virtual environment, then install development and parsing extras:

```bash
python -m venv .venv
```

Windows:

```powershell
.\.venv\Scripts\python -m pip install -e ".[dev,parsing]"
.\.venv\Scripts\code-agent doctor
```

macOS and Linux:

```bash
.venv/bin/python -m pip install -e ".[dev,parsing]"
.venv/bin/code-agent doctor
```

## pipx

For the published beta:

```bash
pipx install --pip-args="--pre" agent47
code-agent doctor
```

From a local checkout:

```bash
pipx install .
code-agent doctor
```

Install development extras only in contributor environments; normal CLI use does not require them.

## Required Tools

- Python 3.11 or newer.
- Git for git-awareness, collaboration commands, patches, and reverts.
- `ripgrep` is recommended for fast repository search; a slower built-in fallback is available.
- Docker or Podman is optional and only required for container sandbox isolation.
- `uv` is recommended for reproducible development, tests, and builds.

Tree-sitter parsing is optional. Install the `parsing` extra for richer symbol extraction across languages.

## Optional Language Servers

Agent47 discovers language servers from `PATH` and starts only the server needed for a semantic action.
Install the servers for the languages you use:

| Language | Discovered executable |
| --- | --- |
| Python | `basedpyright-langserver`, `pyright-langserver`, or `pylsp` |
| TypeScript / JavaScript | `typescript-language-server` |
| Rust | `rust-analyzer` |
| Go | `gopls` |
| Java | `jdtls` |
| C / C++ | `clangd` |

The server executable must be available in the active execution environment: on the host for local mode or in
the selected container image for Docker/Podman mode. Missing servers degrade
cleanly: `lsp_status` reports availability and semantic actions return an installation-oriented error.
Strict `--sandbox` runs start servers through the reusable container and transparently map workspace URIs.

## Managed Development Processes

No additional service is required. `code-agent processes start` launches a detached worker from the active
Agent47 environment and writes job state beneath `.code-agent/processes/jobs/`.

```bash
uv run code-agent processes start "npm run dev" --name frontend --port 5173 --auto-restart
uv run code-agent processes list --active
uv run code-agent processes logs <process-id>
uv run code-agent processes events <process-id> --after 0
uv run code-agent processes stop <process-id>
```

Use `--interactive` for commands that accept later input. On POSIX systems, add `--pty` when terminal
semantics are required. Windows currently provides durable interactive pipe mode and fails closed for
`--pty` until a native ConPTY backend is installed in Agent47.
With Docker/Podman execution enabled, managed project workloads run inside the workspace container and support
interactive input and container TTY allocation across Docker Desktop, WSL2, macOS, and Linux hosts.

## Provider Configuration

Agent47 supports these direct OpenAI-compatible providers:

| Provider | Key | Default base URL |
| --- | --- | --- |
| OpenRouter | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` |
| OpenAI | `OPENAI_API_KEY` | `https://api.openai.com/v1` |
| Gemini | `GEMINI_API_KEY` | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| DeepSeek | `DEEPSEEK_API_KEY` | `https://api.deepseek.com` |
| NVIDIA NIM | `NVIDIA_API_KEY` | `https://integrate.api.nvidia.com/v1` |

Use `.env.example` as the canonical configuration reference. Common settings:

```text
AGENT_MODEL_PRESET=
AGENT_MODEL=qwen/qwen3-coder
AGENT_PROFILE=default
AGENT_FALLBACK_MODELS=
AGENT_REQUEST_MAX_OUTPUT_TOKENS=
AGENT_RESPONSE_RESERVE_TOKENS=
AGENT_MODEL_TIMEOUT_SECONDS=180
AGENT_MODEL_RETRY_COUNT=2
AGENT_MODEL_RETRY_BASE_SECONDS=0.5
AGENT_MODEL_RETRY_MAX_SECONDS=4
AGENT_MAX_FAILURES=3
AGENT_CONTEXT_MAX_CHARS=
AGENT_DB_PATH=.code-agent/agent.db
AGENT_STREAM=true
AGENT_REVIEWER_PASS=true
AGENT_SHELL_NETWORK=deny
AGENT_SANDBOX_BACKEND=auto
AGENT_SANDBOX_IMAGE=python:3.13-slim
AGENT_CONTAINER_WORKSPACE=/workspace
AGENT_CONTAINER_REUSE=true
```

The model retry count is bounded from 0-10. The timeout is per provider attempt;
fallbacks receive fresh windows. Leave the output limit unset to use the provider/model
default. The response reserve protects context independently and defaults to a
conservative 4,096 tokens. Model token capacity governs normal context compaction;
the optional character limit is a secondary operator override. Agent47 reads only the
current workspace .env; process variables take precedence. An alternate file requires
an explicit Settings.for_workspace(workspace, env_file=...) selection.

## Persistent session context

Interactive requests pin the latest instruction, then add a structured session checkpoint
before recent conversation. The checkpoint keeps the project goal, active plan, blockers,
latest corrections, verified evidence, decisions, relevant failed approaches and next actions.
Repository context and system instructions are added by the agent; files remain authoritative.

Session context uses a share of the active model token capacity rather than a fixed character
or turn count. Soft/hard compaction remains token based (defaults 0.75/0.85). Numeric registered
capacity takes precedence over AGENT_CONTEXT_WINDOW_TOKENS; unknown models use a conservative
65,536-token fallback. The current estimator counts UTF-8 bytes conservatively rather than
using a provider tokenizer, so usable context may be smaller than the advertised model window.

Checkpoints use a shared 128 KB storage budget and contain sanitized structured facts.
They exclude raw transcripts, diffs and private model reasoning. Recent conversation is
kept within a model-relative budget in memory; checkpoint restoration carries continuity
across restarts. Lower priority facts may be omitted when the budget is exhausted.

Normal terminal output describes work stages and verification summaries. Use /history,
output expansion, /debug for recent errors, or AGENT47_DEBUG=1 for sanitized command and
diagnostic details. Approval prompts continue to show the concrete command or affected path.

Unattended network access remains deny by default. Interactive ask mode is not implemented;
allow still requires an explicit trusted configuration.

## Transaction Recovery

Workspace mutations create journals and deduplicated checkpoints under `.code-agent/transactions/`.
Keep this directory private and do not commit it. Useful commands:

```bash
uv run code-agent transactions list
uv run code-agent transactions list --run-id 12
uv run code-agent transactions undo <transaction-id>
uv run code-agent transactions redo <transaction-id>
uv run code-agent transactions restore <transaction-id>
uv run code-agent transactions restore <transaction-id> --path src/app.py
uv run code-agent transactions recover
```

Omitting `--path` from `transactions restore` restores the complete recorded workspace checkpoint and can
remove files created after that checkpoint. Agent47 always shows the affected paths and diff before approval.

## Durable Execution Recovery

The execution journal is stored locally in SQLite under `.code-agent/` by default. Do not place it on a
network filesystem. If the Agent47 process or host restarts while an execution is active, resume it with its
execution ID (or the linked run ID). The runtime replays the journal, returns interrupted task lifecycle
states to the scheduler, and marks ambiguous external effects `unknown` for reconciliation; it never blindly
repeats an ambiguous effect.

```bash
uv run code-agent run "Continue the interrupted task" --execution-id <execution-id>
uv run code-agent execution recover <execution-id>
uv run code-agent execution trace <execution-id>
```

Keep database backups until a beta task is accepted. Recovery restores Agent47 control state, not arbitrary
in-memory state inside external tools, services, or providers.

## Platform Notes

### Windows

- Install Git for Windows and ensure `git` is on `PATH`.
- Install ripgrep with `winget`, Chocolatey, Scoop, or another package manager.
- If PowerShell blocks virtual-environment activation, invoke `.venv\Scripts\python` directly or set an
  appropriate user-scoped execution policy.
- Docker Desktop requires hardware virtualization and a running daemon. `code-agent sandbox health
  --backend docker` distinguishes a missing CLI from an unavailable daemon.
- Agent47 pins `python` verification commands to the active interpreter to avoid Windows executable
  lookup escaping the managed environment.

### macOS

- Install command-line developer tools so Git and native package builds are available.
- Docker Desktop or Podman is optional for container isolation.
- If `code-agent` is not found after `pipx`, run `pipx ensurepath` and restart the shell.

### Linux

- Install Python development support when native dependencies require compilation.
- Docker users may need daemon access through the configured socket or group. Do not weaken daemon
  permissions solely for Agent47.
- Rootless Podman is a suitable container backend when available.

## Sandbox Setup

Ordinary runs can use the local backend without an external runtime, but it is not OS-level isolation.
`--sandbox` requires a healthy Docker or Podman backend and fails closed rather than using local execution.

Docker example:

```bash
docker pull python:3.13-slim
uv run code-agent sandbox health --backend docker
uv run code-agent containers start --backend docker
uv run code-agent containers status --backend docker
uv run code-agent run --sandbox --sandbox-backend docker "Inspect and test this project"
```

The default strict policy requires a rootless runtime. Rootful Docker Desktop or Docker Engine therefore
fails health unless the repository explicitly sets `rootless_required = false`; that opt-out weakens the
daemon boundary and should be limited to reviewed development environments. Rootless Podman is preferred
when available.

Example strict `.code-agent/policy.toml`:

```toml
[sandbox]
rootless_required = true
seccomp_required = true
container_user = "65532:65532"
container_workspace = "/workspace"
container_reuse = true
# Extra bind mounts default to read-only unless `:rw` is explicit.
bind_mounts = ["/reviewed/sdk:/opt/sdk:ro"]
# Named cache volumes use `name:/absolute/container/path`.
cache_volumes = ["agent47-project-cache:/opt/project-cache"]

[network]
offline = true

[images]
allowed = ["python:3.13-slim"]
require_digest = true
scan_required = true
scanner = "trivy"
denied_severities = ["HIGH", "CRITICAL"]
```

Images must be pulled and reviewed before use. Agent47 resolves allowed tags to an immutable local repository
digest. When `scan_required` is enabled, install a verified Trivy release; health and execution fail if the
scanner is missing or reports a denied severity. Networking is disabled by default. Setting `offline = false`
with a domain allowlist is refused because an unrestricted bridge is not a domain firewall. With `auto`,
Agent47 tries Docker and then Podman and reports all failed checks.

## Verification

Fast installation checks:

```bash
uv run code-agent doctor --strict
uv run code-agent evals
uv run code-agent run-json --dry-run "Inspect this repository"
```

Contributor and release checks:

```bash
uv run ruff check src tests
uv run pytest
uv run code-agent release-smoke
uv build
```

Live evals consume provider credits and are intentionally opt-in:

```bash
uv run code-agent evals --live --limit 3 --trials 3 --save-report
```

## Troubleshooting

- **Provider key required:** configure the key matching `AGENT_PROVIDER` or the selected preset.
- **Command not found:** use `uv run code-agent`, activate the virtual environment, or run `pipx ensurepath`.
- **Slow repository search:** install `ripgrep` and confirm `rg` is visible to `code-agent doctor`.
- **SQLite errors:** set `AGENT_DB_PATH` to a writable local path; network filesystems are not recommended.
- **Docker CLI found but daemon unavailable:** start the runtime and rerun `sandbox health`.
- **Strict doctor warns about memory:** project memory is optional until the repository needs stable facts.
- **Tests are slow:** run focused files during iteration and use `release-smoke` or CI for the complete gate.


## Workspace execution hotfix behavior

Repository indexing uses `.code-agent/repo-index.db`, a derived, rebuildable cache.
Execution/history stays in the configured agent database (normally `.code-agent/agent.db`).
Old index rows in an existing agent database are not migrated. The separate cache uses
WAL, a bounded busy timeout, and explicit write transactions. Background refresh errors
are contained and retried with a bounded delay. A failed cache read/write falls back to
repository scanning; completed file mutations do not depend on cache invalidation.

The execution prompt identifies the native host platform. Use structured listing and
file mutation tools for setup; file writes create parent directories. `make_directory`
creates a requested empty directory, requires mutation approval, respects dry-run and
workspace boundaries, and blocks credential/VCS directories. It is scaffolding and is
not a transactional file edit with an inverse patch. Shell commands remain for project
verification, package tooling, git and supported backend operations.

For autonomous filesystem implementation requests, a final promise or proceed question
is rejected. Empty placeholder files and directories cannot satisfy implementation.
Completion checks current artifact hashes, meaningful content, plan/acceptance gates,
and relevant successful verification recorded after the latest changes. Explicit empty
file/directory requests remain supported. Missing required private input and observed
permission/policy denials use the existing saved waiting state; resume after supplying
input or enabling the appropriate permission. Interactive Ctrl+C saves a resumable pause;
explicit stop/exit remains terminal. Neither accepts late responses or triggers retention.

Normal terminal reports label waiting runs **Paused** and explicit terminal stops **Stopped**.
Adapter exception classes, traceback text, action identifiers and raw action JSON stay
out of normal results. Sanitized detail remains available in debug/history or explicit
expanded output. Approval prompts continue to show the concrete command and paths.

These completion checks are conservative deterministic guards. They cannot establish
that a test/build command covers every requested behavior or replace review. The
qualification workflow uses a scripted planner with real structured file tools and
native pytest verification; it is not evidence of live model quality or browser layout.


## Interactive command policy and cancellation

`python -m http.server [port]`, `py -m http.server [port]`, and
`python3 -m http.server [port]` are development processes. Shell requests for development
commands route through managed process startup with explicit approval, configured
sandbox/network restrictions, workspace checks, persisted logs and process lifecycle.
The server's bind address is determined by the approved command; its default is not
restricted to loopback by this classification. The default `AGENT_SHELL_NETWORK=deny`
blocks development servers and potentially network-capable unknown commands before
approval. Set it to `allow` only when that network access is intentional; the selected
sandbox network policy still applies.

Interactive runs may request approval for a merely unclassified command. The prompt
shows the complete sanitized command. Interpreter wrappers, inline code, destructive
operations, credential access, workspace escapes, and configured sandbox denials do not
use this escalation. Unattended runs continue to block unclassified commands. Approval
is for one action; unknown commands are never automatically approved or task-approved.

A denied command strategy is recorded separately from execution failures. The planner
receives an unavailable-strategy instruction. A second equivalent proposal pauses before
another execution or approval prompt. Whitespace, Python aliases, foreground/background
action changes and HTTP-server port changes do not evade this check. Plan changes and
ordinary repository activity do not reset policy denials. Eight policy-denied command
strategies in one run also pause, even if the planner continually changes commands/plans. Effective sandbox/network policy
changes can invalidate old denials; approval still applies. Strategy matching is
conservative and is not a general proof that two arbitrary commands behave equivalently.

Progress is saved with the sanitized blocked command and reason. Use `/resume <run-id>`
with guidance to choose another strategy. An interactive resume allows a fresh explicit
approval attempt; another denial pauses immediately. Resume does not override hard blocks.
Ctrl+C cancels supported provider clients, prevents further retries/replans, cancels managed
child work through the existing supervisor, checkpoints progress, and returns to the prompt.
SDK cancellation is best effort; late responses are discarded. The existing supervisor's
cancellation scope includes managed workspace processes, so Ctrl+C can also stop a server
started earlier in that workspace.
