from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8").lower()


def test_only_canonical_public_docs_exist() -> None:
    for path in [
        "README.md",
        "docs/ARCHITECTURE.md",
        "docs/BETA.md",
        "docs/INSTALL.md",
        "docs/KNOWN_LIMITATIONS.md",
    ]:
        assert (ROOT / path).exists(), path


def test_readme_covers_security_data_and_release_workflow() -> None:
    content = read("README.md")

    for phrase in [
        "closed beta",
        "workspace",
        "permissions and safety",
        "security reports",
        "local and provider data",
        "history prune",
        "release-smoke",
    ]:
        assert phrase in content


def test_architecture_covers_current_autonomy_and_trust_boundaries() -> None:
    content = read("docs/ARCHITECTURE.md")

    for phrase in [
        "execution state",
        "workspace generation",
        "context engine",
        "shell-free argv",
        "trust boundaries",
        "verification confidence",
        "sqlite",
    ]:
        assert phrase in content


def test_install_covers_supported_setup_and_runtime_controls() -> None:
    content = read("docs/INSTALL.md")

    for phrase in [
        "python 3.11",
        "uv sync",
        "editable pip",
        "pipx",
        "agent_context_max_chars",
        "sandbox health",
    ]:
        assert phrase in content


def test_known_limitations_are_honest_about_beta_status() -> None:
    content = read("docs/KNOWN_LIMITATIONS.md")

    for phrase in [
        "closed-beta coding agent",
        "model reliability",
        "not os-level isolation",
        "context budgeting",
        "pattern matching cannot identify every secret",
        "release gate",
    ]:
        assert phrase in content
