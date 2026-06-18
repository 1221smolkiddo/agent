# Agent47 Install Guide

This guide is the public-alpha install path for Agent47 on Windows, macOS, and Linux.

Agent47 requires Python 3.11 or newer.

## Recommended: uv

From the repository root:

```bash
uv sync --extra dev --extra parsing
cp .env.example .env
uv run code-agent doctor
uv run code-agent evals
uv run code-agent run --dry-run "Inspect this project"
```

On Windows PowerShell, use:

```powershell
Copy-Item .env.example .env
uv run code-agent doctor
```

Set your API key in `.env`:

```text
OPENROUTER_API_KEY=...
```

## pip Editable Install

Use this when `uv` is not available:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,parsing]"
code-agent doctor
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,parsing]"
code-agent doctor
```

## pipx Install From A Local Checkout

Use this when you want the console commands installed in an isolated app environment:

```bash
pipx install ".[parsing]"
code-agent doctor
agent47
```

If the command is not found, run:

```bash
pipx ensurepath
```

Then restart the shell.

## macOS Notes

- Install Python 3.11+ with `uv`, Homebrew, or python.org.
- Install Git if Xcode command line tools are missing: `xcode-select --install`.
- Install ripgrep for faster search: `brew install ripgrep`.
- If shell commands cannot find `code-agent`, restart the terminal after installing with `pipx`.

## Linux Notes

- Install Python 3.11+ from your distribution or `uv`.
- Install Git and ripgrep with your package manager.
- Debian/Ubuntu example:

```bash
sudo apt-get update
sudo apt-get install -y git ripgrep
```

- If editable installs fail because build tools are missing, install your distribution's Python development package.

## Windows Notes

- PowerShell is the tested shell for local development.
- If script activation is blocked, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

- Install Git for Windows and ensure it is on PATH.
- Install ripgrep with winget, Chocolatey, Scoop, or another package manager.

## Smoke Test

Run these before opening a public-alpha issue or release:

```bash
code-agent doctor --strict
code-agent evals
code-agent run-json --dry-run "Inspect this project"
```

`doctor --strict` exits nonzero on warnings. That is useful for CI or release checks, but local development may intentionally have warnings such as missing API keys.

## Troubleshooting

- `OPENROUTER_API_KEY is required`: set `OPENROUTER_API_KEY` in `.env` or your shell environment.
- `code-agent` command not found: use `uv run code-agent ...`, activate the virtual environment, or verify `pipx ensurepath`.
- Slow project search: install `ripgrep`; Agent47 falls back to a slower Python search if `rg` is missing.
- SQLite path errors: set `AGENT_DB_PATH` to a writable location.
