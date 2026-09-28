from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_agent import __version__
from code_agent.agent import AgentRunResult, CodingAgent
from code_agent.durable_execution import CriterionProjection, ExecutionProjection, ExecutionStatus
from code_agent.experience_memory import ExperienceMemoryConfig, ExperienceMemoryService, MemoryResult, MemoryStatus
from code_agent.experience_memory.contracts import OperationLookup, OperationState
from code_agent.experience_memory.episode_sanitizer import MAX_EPISODE_BYTES, MemorySanitizer
from code_agent.experience_memory.episodes import EngineeringEpisodeBuilder, EpisodeOutcome
from code_agent.experience_memory.providers.null import NullExperienceMemoryProvider
from code_agent.experience_memory.retention import EpisodeRetentionCoordinator
from code_agent.experience_memory.scope import RepositoryScope
from code_agent.storage import AgentStorage

KEY = "hindsight-test-key"
SCOPE = RepositoryScope("agent47-repo-" + "a" * 64, "feature/memory", "b" * 40)


def configuration(**kwargs):
    return ExperienceMemoryConfig(enabled=True, api_key=KEY, **kwargs)


class CaptureProvider(NullExperienceMemoryProvider):
    def __init__(self, status=MemoryStatus.OK):
        super().__init__(status)
        self.retained = []

    def retain(self, bank_id, experience):
        self.retained.append((bank_id, experience))
        return MemoryResult(self.status)

    def get_operation(self, bank_id, operation_id):
        return OperationLookup(MemoryStatus.OK, OperationState.COMPLETED)

    def recall(self, *args):
        raise AssertionError("P2 cannot recall")

    def reflect(self, *args):
        raise AssertionError("P2 cannot reflect")


def make_result(*, checks=True, blocked=False, changed=True, failures=False):
    result = AgentRunResult(
        message="Fixed the parser after finding a bad boundary check.",
        run_id=1, task="raw transcript must not be sent",
        clean_task="Fix parser boundary regression",
        durable_execution_id="exec-123", blocked=blocked,
        changed_paths=["src/parser.py"] if changed else [],
        mutation_records=[{"path": "src/parser.py", "ok": True, "verified": True}]
        if changed else [],
        plan_updates=[{"steps": [
            {"step": "Reproduce the parser boundary failure", "status": "completed"},
            {"step": "Patch the boundary check", "status": "completed"},
        ], "blockers": []}],
        verification_results=[{
            "purpose": "test", "command": "uv run pytest tests/test_parser.py",
            "ok": checks, "diagnostics": {"summary": "Parser boundary assertion failed"}
            if not checks else {},
        }] if checks is not None else [],
        review_records=[{"ok": True, "summary": "No issues"}],
    )
    if failures:
        result.failed_actions = [{"action": "edit_file", "output": "First strategy failed: wrong boundary"}]
        result.command_records = [{"diagnostics": {"diagnostics": [
            {"category": "test", "rule": "assert", "message": "expected end marker"},
        ]}}]
    return result


def make_state(status=ExecutionStatus.COMPLETE):
    return ExecutionProjection(
        id="exec-123", status=status,
        created_at="2026-09-28T10:00:00Z", updated_at="2026-09-28T10:01:00Z",
    )


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr("code_agent.experience_memory.service.repository_scope", lambda _: SCOPE)
    provider = CaptureProvider()
    service = ExperienceMemoryService(configuration(), tmp_path, provider=provider)
    storage = AgentStorage(tmp_path / "runs.db")
    storage.create_run("Fix parser boundary regression", "model", tmp_path)
    return EpisodeRetentionCoordinator(service, storage), provider, storage


def test_meaningful_verified_run_retained_with_causal_structure(rig):
    coordinator, provider, storage = rig
    result = make_result(failures=True)
    coordinator.after_run(result, make_state(), "engine-execute", dry_run=False)
    assert len(provider.retained) == 1
    bank, experience = provider.retained[0]
    assert bank == SCOPE.bank_id
    assert experience.kind == "engineering_episode"
    assert experience.outcome == "verified"
    assert experience.branch == SCOPE.branch and experience.head == SCOPE.head
    assert experience.document_id == "agent47:exec-123:engine-execute:episode:v1"
    assert experience.operation_id
    payload = json.loads(experience.summary)
    assert payload["goal"] == "Fix parser boundary regression"
    assert payload["failed_approaches"] and payload["diagnostics"]
    assert payload["verification"][0]["passed"] is True
    assert payload["started_at"] and payload["ended_at"]
    assert "raw transcript" not in experience.summary
    telemetry = storage.run_steps_payloads(1)[0]["payload"]
    assert telemetry["status"] == "submitted" and telemetry["reason"] == "verified_change"
    assert "content" not in telemetry and KEY not in str(telemetry)


def test_unverified_apparent_success_never_claims_verified(rig):
    coordinator, provider, _ = rig
    result = make_result(checks=None)
    episode = coordinator.builder.build(result, make_state(), "engine-execute", SCOPE)
    assert episode.outcome == EpisodeOutcome.UNVERIFIED
    assert not coordinator.policy.decide(episode).retain
    coordinator.after_run(result, make_state(), "engine-execute", dry_run=False)
    assert provider.retained == []


def test_incomplete_runtime_or_rejected_reviewer_prevents_verified():
    builder = EngineeringEpisodeBuilder()
    result = make_result()
    assert builder.build(result, make_state(ExecutionStatus.ACTIVE), "task", SCOPE).outcome == EpisodeOutcome.PARTIALLY_VERIFIED
    result.review_records = [{"ok": False, "summary": "Missing coverage"}]
    assert builder.build(result, make_state(), "task", SCOPE).outcome == EpisodeOutcome.PARTIALLY_VERIFIED
    result.review_records = [{"ok": True, "error": "reviewer unavailable"}]
    episode = builder.build(result, make_state(), "task", SCOPE)
    assert episode.reviewer == "unavailable"


def test_diagnosed_failed_strategy_is_retained_without_false_success(rig):
    coordinator, provider, _ = rig
    result = make_result(checks=False, changed=False, failures=True)
    result.message = "Parser boundary remains broken."
    coordinator.after_run(result, make_state(ExecutionStatus.FAILED), "task", dry_run=False)
    assert len(provider.retained) == 1
    payload = json.loads(provider.retained[0][1].summary)
    assert payload["outcome"] == "failed"
    assert payload["failed_approaches"] and payload["diagnostics"]
    assert payload["verification"][0]["passed"] is False


def test_trivial_read_only_and_generic_success_skipped(rig):
    coordinator, provider, storage = rig
    result = make_result(checks=None, changed=False)
    result.message = "The file exists."
    coordinator.after_run(result, make_state(), "task", dry_run=False)
    assert provider.retained == []
    assert storage.run_steps_payloads(1) == []


def test_meaningful_blocker_retained(rig):
    coordinator, provider, _ = rig
    result = make_result(checks=None, blocked=True, changed=False)
    result.failed_actions = [{"action": "run_shell", "output": "Required migration database is unavailable; rerun only after provisioning it."}]
    coordinator.after_run(result, make_state(ExecutionStatus.FAILED), "task", dry_run=False)
    assert len(provider.retained) == 1
    assert provider.retained[0][1].outcome == "blocked"


def test_disabled_dry_run_and_missing_provider_do_not_change_run(rig, tmp_path, monkeypatch):
    coordinator, provider, storage = rig
    result = make_result()
    coordinator.after_run(result, make_state(), "task", dry_run=True)
    assert provider.retained == []
    disabled = ExperienceMemoryService(ExperienceMemoryConfig(), tmp_path)
    EpisodeRetentionCoordinator(disabled, storage).after_run(result, make_state(), "task", dry_run=False)
    assert provider.retained == [] and storage.run_steps_payloads(1) == []

    def missing(name):
        raise ModuleNotFoundError(KEY)

    monkeypatch.setattr("code_agent.experience_memory.providers.hindsight.importlib.import_module", missing)
    missing_service = ExperienceMemoryService(configuration(), tmp_path)
    EpisodeRetentionCoordinator(missing_service, storage).after_run(result, make_state(), "task", dry_run=False)
    assert storage.run_steps_payloads(1)[0]["payload"]["status"] == "prepared"
    assert storage.run_steps_payloads(1)[0]["payload"]["reason"] == "missing_dependency"
    assert KEY not in str(storage.run_steps_payloads(1))


@pytest.mark.parametrize("status", [MemoryStatus.TIMEOUT, MemoryStatus.UNAVAILABLE])
def test_provider_failure_is_best_effort(rig, status):
    coordinator, provider, storage = rig
    provider.status = status
    result = make_result()
    coordinator.after_run(result, make_state(), "task", dry_run=False)
    assert result.blocked is False
    assert storage.run_steps_payloads(1)[0]["payload"]["status"] == "unknown"
    assert storage.run_steps_payloads(1)[0]["payload"]["reason"] == status.value


def test_deterministic_document_and_operation_identity(rig):
    coordinator, provider, _ = rig
    result = make_result()
    coordinator.after_run(result, make_state(), "task", dry_run=False)
    coordinator.after_run(result, make_state(), "task", dry_run=False)
    assert len(provider.retained) == 1  # repeated finalization reconciles, never resubmits
    first = provider.retained[0][1]
    from code_agent.experience_memory.outbox import ExperienceMemoryOutbox
    outbox = ExperienceMemoryOutbox(coordinator.storage.db_path, coordinator.service)
    with outbox._connect() as conn:
        conn.execute("update experience_memory_outbox set next_attempt_at = 0 where operation_id = ?", (first.operation_id,))
    outbox.process(first.operation_id)
    entry = outbox.get(first.operation_id)
    assert entry is not None and entry.state == "completed"
    assert entry.prepared.document_id == first.document_id
    assert entry.prepared.operation_id == first.operation_id
    assert entry.prepared.content == first.summary
    changed = replace(result, message="Fixed the parser with an additional check.")
    coordinator.after_run(changed, make_state(), "task", dry_run=False)
    third = provider.retained[1][1]
    assert third.document_id == first.document_id
    assert third.operation_id != first.operation_id


@pytest.mark.parametrize("secret", [
    "sk-123456789012345678901234", "Bearer abcdefghijklmnopqrstuvwxyz",
    "API_KEY=value", "password=hunter2", "Authorization: Bearer abcdefghijklmnop",
    "TOKEN=from-diagnostic", "https://example.com/?token=hidden",
])
def test_secrets_in_evidence_never_leave_sanitizer(rig, secret):
    coordinator, provider, storage = rig
    result = make_result(failures=True)
    result.clean_task += " " + secret
    result.failed_actions[0]["output"] += " " + secret
    result.command_records[0]["diagnostics"]["diagnostics"][0]["message"] += " " + secret
    result.message += " " + secret
    coordinator.after_run(result, make_state(), "task", dry_run=False)
    assert len(provider.retained) == 1
    outbound = provider.retained[0][1].summary
    assert secret not in outbound
    assert KEY not in outbound
    assert "raw transcript" not in outbound
    assert secret not in str(storage.run_steps_payloads(1))


def test_secret_bearing_git_metadata_rejected(rig, monkeypatch):
    coordinator, provider, storage = rig
    monkeypatch.setattr(
        "code_agent.experience_memory.service.repository_scope",
        lambda _: RepositoryScope(SCOPE.bank_id, "feature/API_KEY=value", SCOPE.head),
    )
    coordinator.after_run(make_result(), make_state(), "task", dry_run=False)
    assert provider.retained == []
    assert storage.run_steps_payloads(1)[0]["payload"]["status"] == "invalid_request"


def test_bounded_payload_and_explicit_truncation(rig):
    coordinator, provider, _ = rig
    result = make_result(failures=True)
    result.changed_paths = [f"src/{i}-" + "x" * 300 for i in range(100)]
    result.failed_actions = [{"action": "run_shell", "output": "x" * 1000}] * 100
    result.command_records = [{"diagnostics": {"diagnostics": [
        {"message": "x" * 1000, "category": "test"} for _ in range(100)
    ]}}] * 100
    coordinator.after_run(result, make_state(), "task", dry_run=False)
    content = provider.retained[0][1].summary
    assert len(content.encode("utf-8")) <= MAX_EPISODE_BYTES
    assert "truncated" in content
    assert "x" * 1000 not in content


def test_sdk_uses_stable_async_ids_and_safe_metadata(rig, monkeypatch):
    coordinator, _, _ = rig
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def aretain(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(success=True, var_async=True, operation_id=kwargs["operation_id"])

        async def aclose(self):
            pass

    monkeypatch.setattr(
        "code_agent.experience_memory.providers.hindsight.importlib.import_module",
        lambda _: SimpleNamespace(Hindsight=Client),
    )
    service = ExperienceMemoryService(configuration(), Path("."))
    coordinator.service = service
    coordinator.after_run(make_result(), make_state(), "task", dry_run=False)
    assert len(calls) == 1
    call = calls[0]
    assert call["retain_async"] is True and call["update_mode"] == "replace"
    assert call["document_id"] == "agent47:exec-123:task:episode:v1"
    assert call["operation_id"]
    assert call["metadata"] == {
        "source": "agent47", "memory_kind": "engineering_episode",
        "branch": SCOPE.branch, "head": SCOPE.head,
        "outcome": "verified", "agent_version": __version__,
        "repository_bank_id": SCOPE.bank_id,
        "changed_paths": '["src/parser.py"]',
    }


def test_finalization_integration_is_single_best_effort_call(monkeypatch, tmp_path):
    from code_agent.factory import create_agent

    class Model:
        model = "test-model"

        def complete(self, messages):
            return '{"type":"final","message":"Inspected the repository."}'

    calls = []

    def record(self, result, state, task_id, *, dry_run):
        calls.append((result.run_id, state.status.value if state else None, task_id, dry_run))

    monkeypatch.setattr("code_agent.factory.create_fallback_client", lambda *args: Model())
    monkeypatch.setattr("code_agent.credentials.keyring.CredentialStore.get_provider_key", lambda *args: None)
    monkeypatch.setattr(EpisodeRetentionCoordinator, "after_run", record)
    from code_agent.config import Settings

    settings = Settings(
        _env_file=None, openrouter_api_key="model-key", agent_model="test-model",
        agent_db_path=tmp_path / "agent.db", agent_execution_db_path=tmp_path / "execution.db",
        agent_reviewer_pass=False, agent_stream=False,
        agent_experience_memory_enabled=True, hindsight_api_key=KEY,
    )
    agent: CodingAgent = create_agent(settings, tmp_path, None, True, 2)
    result = agent.run_detailed("Inspect repository")
    assert result.message == "Inspected the repository."
    assert len(calls) == 1 and calls[0][0] == result.run_id
    assert calls[0][1] == "complete" and calls[0][3] is True
    assert not (tmp_path / ".code-agent/memory/project.md").exists()


def test_verified_requires_successful_mutation_and_latest_runtime_decisions():
    builder = EngineeringEpisodeBuilder()
    result = make_result()
    result.mutation_records = []
    assert builder.build(result, make_state(), "task", SCOPE).outcome == EpisodeOutcome.PARTIALLY_VERIFIED

    result = make_result()
    state = make_state()
    state.criteria["criterion"] = CriterionProjection("criterion", "task", "Parser works")
    assert builder.build(result, state, "task", SCOPE).outcome == EpisodeOutcome.PARTIALLY_VERIFIED
    state.criteria["criterion"].verification_ids = ["old", "latest"]
    state.verifications = {
        "old": {"decision": "failed"},
        "latest": {"decision": "verified"},
    }
    assert builder.build(result, state, "task", SCOPE).outcome == EpisodeOutcome.VERIFIED
    state.verifications["latest"] = {"decision": "failed"}
    assert builder.build(result, state, "task", SCOPE).outcome == EpisodeOutcome.FAILED


def test_failed_runtime_does_not_invent_successful_approach():
    result = make_result(checks=None, changed=True)
    episode = EngineeringEpisodeBuilder().build(
        result, make_state(ExecutionStatus.FAILED), "task", SCOPE,
    )
    assert episode.outcome == EpisodeOutcome.FAILED
    assert episode.successful_approach == ""


def test_unavailable_runtime_records_safe_telemetry(rig):
    coordinator, provider, storage = rig
    coordinator.after_run(make_result(), None, "task", dry_run=False)
    assert provider.retained == []
    assert storage.run_steps_payloads(1)[0]["payload"] == {
        "type": "experience_memory_retention", "status": "unavailable",
        "reason": "runtime_state_unavailable", "document_id": "",
        "operation_id": "",
    }


def test_environment_values_do_not_enter_episode(rig, monkeypatch):
    coordinator, provider, _ = rig
    monkeypatch.setenv("EPISODE_PRIVATE_SENTINEL", "environment-only-secret-987")
    coordinator.after_run(make_result(), make_state(), "task", dry_run=False)
    assert len(provider.retained) == 1
    assert "environment-only-secret-987" not in provider.retained[0][1].summary


def test_sha256_git_head_remains_valid_provenance():
    episode = EngineeringEpisodeBuilder().build(
        make_result(), make_state(), "task",
        RepositoryScope(SCOPE.bank_id, SCOPE.branch, "a" * 64),
    )
    prepared = MemorySanitizer(configuration()).prepare(episode)
    assert prepared.head == "a" * 64
    assert json.loads(prepared.content)["head"] == "a" * 64
