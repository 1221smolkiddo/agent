# Agent47 0.1.0 clean-install release gate — PASS

The built stable wheel and sdist both install into separate fresh virtual environments,
pass `pip check`, import from installed site-packages, render `agent47 --help`, report
`Agent47 0.1.0`, and reach the normal unauthenticated setup flow on bare `agent47`.
The old beta missing-`Abort` failure was reproduced and fixed. The release candidate
is ready for human merge/release with the documented existing qualification limits.
No main merge, push, tag, or PyPI publication was performed.

## Candidate and changes

Branch: `qual/hindsight-production-20260928`.
Validated candidate base: `7b04c8f20da7dbc1496300486d95386557c3513e`.
The containing release commit records the final HEAD; obtain it with `git rev-parse HEAD`.
Source SHA-256 fingerprints in [the JSON evidence](RELEASE_0_1_0_GATE.json) tie the
built/installed artifacts and test results to the exact five changed release files.

- Distribution `agent47`, import package `code_agent`, stable version `0.1.0`.
- `pyproject.toml`, existing `__version__`, and the lockfile project version agree.
  The release classifier is Production/Stable. No new version constant was introduced.
- Console scripts remain `agent47 = code_agent.interactive:main` and
  `code-agent = code_agent.cli:app`; Python requirement remains `>=3.11`.
- Hatchling remains the build backend and `src/code_agent` remains the wheel package.
  Runtime dependencies and optional extras are unchanged.
- Interactive startup imports `Abort` from Typer's public API. Its existing terminal
  regression test uses the same public exception; rendering behavior is unchanged.
- Explicit Hatch exclusions prevent worktree scratch state, private environment files,
  databases, caches, Git metadata, virtualenvs and build output from entering artifacts.

## Reproduced beta failure

The published [agent47 0.1.0b4 wheel](https://pypi.org/project/agent47/0.1.0b4/) was downloaded
and installed in a separate diagnostic environment with normally resolved dependencies.
`agent47 --help` failed before startup with:

```text
ImportError: cannot import name 'Abort' from 'typer._click.exceptions'
```

Its source imported from a private Typer implementation module. With the newly resolved
Typer `0.27.2`, that internal module no longer supplies that symbol. The same private
module is absent under the declared minimum Typer `0.15.0`, also reproduced separately.
Using the supported public `from typer import Abort` removes the dependency on that
internal layout. The new candidate passes with Typer `0.27.2`; maintainer regression
tests also pass with the existing development environment. No guessed dependency pin
or symptom workaround was added. The failed beta virtualenv was removed; its full
install/failure logs remain in `C:/Users/Sirius/AppData/Local/Temp/agent47-0.1.0-release-sqwb5o6q/logs`.

## Build and distribution audit

Final build command: `uv build --out-dir dist/0.1.0-r2`.
Both artifacts were rebuilt from scratch after the first sdist audit found ignored
`.code-agent` qualification scratch files included by the default worktree build.
The explicit exclusion fix removes them. The first build is rejected evidence,
not an artifact for publication. The final wheel was built from the final sdist.

| Artifact | Files | SHA-256 |
| --- | ---: | --- |
| `agent47-0.1.0-py3-none-any.whl` | 138 | `675ad94bb3c2115f96799d34fe50c653b6f65a9532eb8270d71b07f4f9b52a81` |
| `agent47-0.1.0.tar.gz` | 286 | `62bd7b248cab91b2152a2e167b1369d35a32261151fc57eaeb4e78b957498e6b` |

All 134 tracked package runtime files, including modules, browser assets and builtin
skills, are present. Packaged file contents match the candidate tracked source,
allowing only line-ending normalization. Metadata identifies `agent47` version `0.1.0`,
with both correct entrypoints. There are no private `.env` files, credentials, runtime
SQLite databases, virtualenvs, Git metadata, caches, qualification scratch files or
unrelated worktrees. The sdist retains the public tracked `.env.example` template;
all its credential fields were verified empty. The wheel contains package files and
normal distribution metadata only. No untracked runtime file is needed for the build.

## True clean-room installation

Two separate never-used environments were created outside the repository:

- Wheel: `C:\Users\Sirius\AppData\Local\Temp\agent47-0.1.0-release-sqwb5o6q\candidate-wheel\.venv`.
- Sdist: `C:\Users\Sirius\AppData\Local\Temp\agent47-0.1.0-release-sqwb5o6q\candidate-sdist\.venv`.

They use Python `3.14.3`, the same version used for current release validation, with
system site-packages disabled. Every child process clears repository `PYTHONPATH`,
virtualenv overrides and real provider credentials; isolated HOME/profile directories
and a null keyring backend prevent using the user's saved account/API keys. The
working directories are the respective temporary folders, never the checkout.
Neither install is editable or uses the project's existing `.venv`.

Only the built wheel, or the built sdist in the second environment, was given to pip;
pip resolved their declared runtime dependencies. Full installation stdout/stderr
are retained in `C:/Users/Sirius/AppData/Local/Temp/agent47-0.1.0-release-sqwb5o6q/logs/wheel-install.log` and `sdist-install.log`.
Their SHA-256 values are in the JSON evidence. Both installations succeeded, both
`pip check` runs reported no broken requirements, and both `pip show agent47` runs
reported Version `0.1.0`.

Installed wheel import:

```text
C:\Users\Sirius\AppData\Local\Temp\agent47-0.1.0-release-sqwb5o6q\candidate-wheel\.venv\Lib\site-packages\code_agent\__init__.py
```

Installed sdist import:

```text
C:\Users\Sirius\AppData\Local\Temp\agent47-0.1.0-release-sqwb5o6q\candidate-sdist\.venv\Lib\site-packages\code_agent\__init__.py
```

An isolated `python -I` import also checked the CLI/interactive module paths, version
metadata and `sys.path`: they resolve into the fresh venv's site-packages, without the
repository source. Both environments resolved Typer `0.27.2`.

## Actual installed executables

For both fresh environments:

| Check | Result |
| --- | --- |
| `agent47.exe --help` | PASS; help renders, exit 0 |
| `agent47.exe --version` | PASS; `Agent47 0.1.0`, exit 0 |
| `code-agent.exe --help` | PASS; automation CLI renders, exit 0 |
| Bare `agent47.exe` | PASS; normal Interactive Setup Required flow, exit 0 |

Bare startup uses controlled closed stdin and a 30-second bound. It reaches the
expected guidance to sign in/configure from an interactive terminal and exits
naturally. No login was attempted, no real key was supplied/read, no browser or paid
model call was made, and no import/module/packaging traceback occurred. An expected
unauthenticated setup message is correctly treated as successful startup.

## Source/static regression validation

- Relevant CLI/startup/release tests: **95 passed in 5.33 seconds** after clean installs.
- Ruff over `src tests`: **passed**.
- `git diff --check`: **passed**.
- `uv lock --check --offline`: **passed**; stable version change preserves resolution.
- Because startup runtime code changed, the full non-Docker suite was run once:
  **1,217 passed, 1 known failure, 5 skipped, 4 deselected in 197.76 seconds**.

The only failure is
`tests/test_interactive_diff.py::test_terminal_width_fallback_when_narrow`, matching
the known pre-existing Windows terminal-rendering baseline issue. Terminal rendering
code was not changed. There are no new Agent47/Hindsight failures. Local JUnit scratch
files and test environments are not staged or included in the distribution.
Commands use `uv run --no-sync --active` for the existing maintainer test environment;
clean installation and executable checks always use each fresh environment directly.

## Release readiness and limits

**RELEASE GATE: PASS.** Ready for human merge and the planned `0.1.0` publication step;
this task performs neither. The earlier Hindsight verdict remains
PRODUCTION READY WITH DOCUMENTED LIMITATIONS. Live Hindsight, real-model A/B/C,
Docker-daemon availability, heuristic limitations and the known terminal baseline
remain documented in [HINDSIGHT_FINAL_REPORT.md](HINDSIGHT_FINAL_REPORT.md).
This gate establishes clean Windows/Python 3.14.3 installation/startup with the actual
resolved dependencies; it does not claim paid-model/live-provider or every-platform
qualification. Final Git status is checked clean after the release commit.
