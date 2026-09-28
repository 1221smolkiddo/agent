"""Credential regressions across tool-output and experience-memory boundaries."""
from __future__ import annotations

import base64
import json

import pytest

from code_agent.experience_memory.config import ExperienceMemoryConfig
from code_agent.experience_memory.episode_sanitizer import MemorySanitizer
from code_agent.safety import redact_secrets


def encoded(value):
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


def fake_jwt(algorithm="HS256", subject="synthetic", signature="signature"):
    return ".".join((encoded({"alg": algorithm, "typ": "JWT"}),
                     encoded({"sub": subject}),
                     base64.urlsafe_b64encode(signature.encode()).decode().rstrip("=")))


@pytest.fixture(params=["shared", "memory"])
def redactor(request):
    if request.param == "shared":
        return redact_secrets
    return MemorySanitizer(ExperienceMemoryConfig()).sanitize_text


@pytest.mark.parametrize(("header", "token"), [
    ("Authorization: Bearer ", "x"),
    ("Authorization: Bearer ", "abc123"),
    ("Authorization: Bearer ", "synthetic-token-value-" * 20),
    ("authorization: bearer ", "short-token"),
    ("Authorization:Bearer ", "short-token"),
    ("AUTHORIZATION = BEARER\t", "short-token"),
    ("Authorization\t:\tbEaReR\t", "short-token"),
    ("Proxy-Authorization: Bearer ", "short-token"),
])
def test_bearer_header_redacts_all_lengths_and_spacing(redactor, header, token):
    output = redactor("Request failed; " + header + token + " ; retry later")
    safe = token not in output
    assert safe, "Bearer credential survived (value omitted)."
    assert output.startswith("Request failed; ") and output.endswith(" ; retry later")


@pytest.mark.parametrize(("algorithm", "subject", "signature"), [
    ("HS256", "synthetic", "signature"),
    ("RS512", "different synthetic account", "different-signature"),
    ("none", "unverified", "x"),
])
def test_standalone_jwt_is_redacted(redactor, algorithm, subject, signature):
    token = fake_jwt(algorithm, subject, signature)
    output = redactor(token)
    safe = token not in output
    assert safe, "Standalone JWT survived (value omitted)."
    assert output == "[REDACTED]"


def test_multiple_jwts_in_provider_diagnostic_preserve_safe_text(redactor):
    first, second = fake_jwt(), fake_jwt("ES256", "other", "other-signature")
    output = redactor(f"Provider rejected ({first}); earlier [{second}]; retry later.")
    safe = first not in output and second not in output
    assert safe, "Provider diagnostic retained JWT (values omitted)."
    assert output == "Provider rejected ([REDACTED]); earlier [[REDACTED]]; retry later."


@pytest.mark.parametrize(("payload", "marker"), [
    ("API_KEY=synthetic-key", "synthetic-key"),
    ("password=synthetic-password", "synthetic-password"),
    ("OPENAI_API_KEY=sk-synthetic-secret-token", "sk-synthetic-secret-token"),
])
def test_existing_key_password_redaction_is_preserved(redactor, payload, marker):
    safe = marker not in redactor(payload)
    assert safe, "Existing credential rule regressed (value omitted)."


@pytest.mark.parametrize("text", [
    "The bearer of this message can respond later.",
    "The bearer negotiabilityrequirements are described here.",
    "Bearer instruments are transferable.",
    "Version 1.2.3 and release v10.20.30 are available.",
    "Read archive.tar.gz and agent.config.json.",
    "Visit service.example.com or authorization.example.org.",
    "abcdefgh.ijklmnop.qrstuvwx is an ordinary dotted identifier.",
])
def test_safe_prose_versions_files_and_domains_are_preserved(redactor, text):
    assert redactor(text) == text


@pytest.mark.parametrize("candidate", [
    "a" * 1025 + "." + "b" * 8192 + "." + "c" * 2048,
    "a" * 1024 + "." + "b" * 8193 + "." + "c" * 2048,
    "a" * 1024 + "." + "b" * 8192 + "." + "c" * 2049,
    ("a" * 1024 + ".") * 100,
], ids=["oversized_header", "oversized_payload", "oversized_signature", "many_segments"])
def test_oversized_malformed_candidate_is_not_partially_matched(candidate):
    assert redact_secrets(candidate) == candidate
