# Install

Agent47 supports Python 3.11 and newer on Windows, macOS, and Linux. CI currently exercises Python
3.11-3.13 on Linux and Python 3.12 on Windows and macOS.

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

The server executable must be available in the environment that launches Agent47. Missing servers degrade
cleanly: `lsp_status` reports availability and semantic actions return an installation-oriented error.
Language servers run as local subprocesses in ordinary mode. Strict `--sandbox` runs disable host LSP
processes rather than weakening the container isolation boundary.

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
AGENT_MAX_TOKENS=4096
AGENT_MODEL_TIMEOUT_SECONDS=60
AGENT_MODEL_RETRY_COUNT=2
AGENT_MODEL_RETRY_BASE_SECONDS=0.5
AGENT_MODEL_RETRY_MAX_SECONDS=4
AGENT_MAX_FAILURES=3
AGENT_CONTEXT_MAX_CHARS=60000
AGENT_DB_PATH=.code-agent/agent.db
AGENT_STREAM=true
AGENT_REVIEWER_PASS=true
AGENT_SHELL_NETWORK=allow
AGENT_SANDBOX_BACKEND=auto
AGENT_SANDBOX_IMAGE=python:3.13-slim
```

The model retry count is bounded from 0-10. The context budget must be at least 8,000 characters.

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
uv run code-agent evals --live --limit 3 --save-report
```

## Troubleshooting

- **Provider key required:** configure the key matching `AGENT_PROVIDER` or the selected preset.
- **Command not found:** use `uv run code-agent`, activate the virtual environment, or run `pipx ensurepath`.
- **Slow repository search:** install `ripgrep` and confirm `rg` is visible to `code-agent doctor`.
- **SQLite errors:** set `AGENT_DB_PATH` to a writable local path; network filesystems are not recommended.
- **Docker CLI found but daemon unavailable:** start the runtime and rerun `sandbox health`.
- **Strict doctor warns about memory:** project memory is optional until the repository needs stable facts.
- **Tests are slow:** run focused files during iteration and use `release-smoke` or CI for the complete gate.
