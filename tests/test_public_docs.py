from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8").lower()


def test_public_security_docs_exist() -> None:
    for path in [
        "SECURITY.md",
        "docs/THREAT_MODEL.md",
        "docs/DATA_HANDLING.md",
        "docs/KNOWN_LIMITATIONS.md",
    ]:
        assert (ROOT / path).exists(), path


def test_security_policy_covers_core_cli_agent_risks() -> None:
    content = read("SECURITY.md")

    for phrase in [
        "alpha",
        "workspace",
        "shell commands",
        "sensitive credential files",
        "destructive commands",
        "dry-run",
        "sandbox",
        "not a malware sandbox",
    ]:
        assert phrase in content


def test_threat_model_covers_public_alpha_boundaries() -> None:
    content = read("docs/THREAT_MODEL.md")

    for phrase in [
        "prompt injection",
        "secret exfiltration",
        "repository files are untrusted",
        "command output is untrusted",
        "false success claims",
        "residual risks",
        "not an os jail",
    ]:
        assert phrase in content


def test_data_handling_discloses_provider_and_local_state() -> None:
    content = read("docs/DATA_HANDLING.md")

    for phrase in [
        ".code-agent",
        "agent.db",
        "debug-bundles",
        "eval-reports",
        "model provider",
        "web search",
        "delete `.code-agent/`",
    ]:
        assert phrase in content


def test_known_limitations_are_honest_about_alpha_status() -> None:
    content = read("docs/KNOWN_LIMITATIONS.md")

    for phrase in [
        "credible alpha",
        "not a finished industry product",
        "provider credits",
        "not os-level isolation",
        "network-deny mode",
        "per-repo memory",
        "editor integration",
    ]:
        assert phrase in content
