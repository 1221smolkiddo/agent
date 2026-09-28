"""Durable memory delivery tests use fake remote truth, never live Hindsight."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest

from code_agent.experience_memory.config import ExperienceMemoryConfig
from code_agent.experience_memory.contracts import (
    MemoryResult, MemoryStatus, OperationLookup, OperationState,
)
from code_agent.experience_memory.episode_sanitizer import PreparedEpisode
from code_agent.experience_memory.outbox import ExperienceMemoryOutbox
from code_agent.experience_memory.providers.hindsight import HindsightExperienceMemoryProvider
from code_agent.experience_memory.providers.null import NullExperienceMemoryProvider
from code_agent.experience_memory.service import ExperienceMemoryService

BANK = "agent47-repo-" + "a" * 64
KEY = "super-private-hindsight-key"


def prepared(goal: str = "Fix the parser") -> PreparedEpisode:
    content = json.dumps({
        "kind": "engineering_episode", "goal": goal,
        "outcome": "verified", "agent_version": "0.1.0b4",
    })
    document_id = "agent47:exec-123:task:episode:v1"
    digest = hashlib.sha256(content.encode()).hexdigest()
    operation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, document_id + ":" + digest))
    return PreparedEpisode(content, document_id, operation_id, "feature/test", "b" * 40,
                           "verified", "0.1.0b4")


class FakeProvider(NullExperienceMemoryProvider):
    def __init__(self):
        super().__init__(MemoryStatus.OK)
        self.remote: dict[str, OperationState] = {}
        self.submissions: list[str] = []
        self.lookups: list[str] = []
        self.fail_once: str | None = None
        self.error_code = ""

    def retain(self, bank_id, experience):
        self.submissions.append(experience.operation_id)
        if self.status != MemoryStatus.OK:
            return MemoryResult(self.status)
        if self.fail_once == "before_accept":
            self.fail_once = None
            return MemoryResult(MemoryStatus.TIMEOUT)
        self.remote.setdefault(experience.operation_id, OperationState.PENDING)
        if self.fail_once == "after_accept":
            self.fail_once = None
            return MemoryResult(MemoryStatus.TIMEOUT)
        return MemoryResult(MemoryStatus.OK)

    def get_operation(self, bank_id, operation_id):
        self.lookups.append(operation_id)
        return OperationLookup(
            MemoryStatus.OK, self.remote.get(operation_id, OperationState.NOT_FOUND),
            self.error_code,
        )


@pytest.fixture
def rig(tmp_path):
    provider = FakeProvider()
    config = ExperienceMemoryConfig(enabled=True, api_key=KEY)
    service = ExperienceMemoryService(config, tmp_path, provider=provider)
    outbox = ExperienceMemoryOutbox(tmp_path / "runs.db", service)
    return outbox, provider, service


def due(outbox: ExperienceMemoryOutbox, operation_id: str) -> None:
    with outbox._connect() as conn:
        conn.execute(
            "update experience_memory_outbox set next_attempt_at = 0 where operation_id = ?",
            (operation_id,),
        )


def test_submission_is_persisted_before_provider_call(rig):
    outbox, provider, _ = rig
    item = prepared()
    assert outbox.enqueue(BANK, item, 7)
    entry = outbox.get(item.operation_id)
    assert entry and entry.state == "prepared" and entry.prepared == item
    assert provider.submissions == []
    assert outbox.process(item.operation_id, first_submission=True).state == "submitted"
    assert outbox.get(item.operation_id).network_requests == 1
    assert outbox.get(item.operation_id).wall_seconds >= 0


@pytest.mark.parametrize(("remote", "local"), [
    (OperationState.PENDING, "submitted"),
    (OperationState.PROCESSING, "processing"),
    (OperationState.COMPLETED, "completed"),
    (OperationState.FAILED, "failed"),
    (OperationState.CANCELLED, "cancelled"),
])
def test_reconciliation_maps_provider_truth(rig, remote, local):
    outbox, provider, _ = rig
    item = prepared()
    outbox.enqueue(BANK, item, 7)
    outbox.process(item.operation_id, first_submission=True)
    provider.remote[item.operation_id] = remote
    due(outbox, item.operation_id)
    entry = outbox.process(item.operation_id)
    assert entry.state == local
    assert provider.submissions == [item.operation_id]
    assert provider.lookups == [item.operation_id]
    assert entry.submitted
    if local in {"completed", "failed", "cancelled"}:
        assert entry.next_attempt_at is None


def test_accepted_then_response_lost_recovery_never_resubmits(rig):
    outbox, provider, service = rig
    item = prepared()
    provider.fail_once = "after_accept"
    outbox.enqueue(BANK, item, 7)
    assert outbox.process(item.operation_id, first_submission=True).state == "unknown"
    assert provider.remote[item.operation_id] == OperationState.PENDING
    restarted = ExperienceMemoryOutbox(outbox.db_path, service)
    due(restarted, item.operation_id)
    assert restarted.recover()[0].state == "submitted"
    assert provider.submissions == [item.operation_id]
    provider.remote[item.operation_id] = OperationState.COMPLETED
    due(restarted, item.operation_id)
    assert restarted.recover()[0].state == "completed"
    assert provider.submissions == [item.operation_id]


def test_timeout_before_accept_retries_same_id_after_not_found(rig):
    outbox, provider, service = rig
    item = prepared()
    provider.fail_once = "before_accept"
    outbox.enqueue(BANK, item, 7)
    assert outbox.process(item.operation_id, first_submission=True).state == "unknown"
    assert item.operation_id not in provider.remote
    restarted = ExperienceMemoryOutbox(outbox.db_path, service)
    due(restarted, item.operation_id)
    assert restarted.recover()[0].state == "submitted"
    assert provider.submissions == [item.operation_id, item.operation_id]
    assert list(provider.remote) == [item.operation_id]


def test_crash_after_remote_accept_before_local_record_reconciles(rig):
    outbox, provider, service = rig
    item = prepared()
    outbox.enqueue(BANK, item, 7)
    provider.remote[item.operation_id] = OperationState.PROCESSING
    restarted = ExperienceMemoryOutbox(outbox.db_path, service)
    assert restarted.recover()[0].state == "processing"
    assert provider.submissions == []


def test_expired_lease_is_recovered_without_duplicate_work(rig):
    outbox, provider, _ = rig
    item = prepared()
    outbox.enqueue(BANK, item, 7)
    with outbox._connect() as conn:
        conn.execute("""
            update experience_memory_outbox set lease_owner = 'dead-process',
                lease_until = ? where operation_id = ?
        """, (time.time() + 20, item.operation_id))
    assert outbox.recover() == []
    with outbox._connect() as conn:
        conn.execute("""
            update experience_memory_outbox set lease_until = 0 where operation_id = ?
        """, (item.operation_id,))
    assert outbox.recover()[0].state == "submitted"
    assert provider.submissions == [item.operation_id]


def test_old_document_operation_finishes_before_new_version_dispatch(rig):
    outbox, provider, _ = rig
    old, new = prepared(), prepared("Fix parser with an extra boundary check")
    assert old.document_id == new.document_id and old.operation_id != new.operation_id
    outbox.enqueue(BANK, old, 7)
    outbox.process(old.operation_id, first_submission=True)
    outbox.enqueue(BANK, new, 7)
    assert outbox.process(new.operation_id, first_submission=True).state == "prepared"
    assert provider.submissions == [old.operation_id]
    provider.remote[old.operation_id] = OperationState.COMPLETED
    due(outbox, old.operation_id)
    outbox.process(old.operation_id)
    assert outbox.process(new.operation_id, first_submission=True).state == "submitted"
    assert provider.submissions == [old.operation_id, new.operation_id]


def test_missing_dependency_or_config_keeps_durable_prepared_entry(rig, tmp_path):
    outbox, provider, _ = rig
    item = prepared()
    provider.status = MemoryStatus.MISSING_DEPENDENCY
    outbox.enqueue(BANK, item, 7)
    entry = outbox.process(item.operation_id, first_submission=True)
    assert entry.state == "prepared" and entry.error_code == "missing_dependency"
    assert entry.network_requests == 0

    missing_config = ExperienceMemoryService(ExperienceMemoryConfig(enabled=True), tmp_path)
    second = prepared("Fix another parser edge case")
    outbox = ExperienceMemoryOutbox(tmp_path / "missing-config.db", missing_config)
    outbox.enqueue(BANK, second, 8)
    entry = outbox.process(second.operation_id, first_submission=True)
    assert entry.state == "prepared" and entry.error_code == "missing_config"


def test_secret_is_rejected_before_journal_and_error_is_allowlisted(rig):
    outbox, provider, _ = rig
    item = prepared()
    dangerous = replace(item, content=item.content + " API_KEY=private-value")
    with pytest.raises(ValueError):
        outbox.enqueue(BANK, dangerous, 7)
    outbox.enqueue(BANK, item, 7)
    outbox.process(item.operation_id, first_submission=True)
    provider.remote[item.operation_id] = OperationState.FAILED
    provider.error_code = "Authorization: Bearer private-value"
    due(outbox, item.operation_id)
    entry = outbox.process(item.operation_id)
    assert entry.error_code == "provider_failed"
    with outbox._connect() as conn:
        row = conn.execute("select * from experience_memory_outbox").fetchone()
    assert KEY not in str(tuple(row)) and "private-value" not in str(tuple(row))


def test_repeated_enqueue_has_one_logical_operation_and_conflicts_fail(rig):
    outbox, provider, _ = rig
    item = prepared()
    assert outbox.enqueue(BANK, item, 7)
    assert not outbox.enqueue(BANK, item, 7)
    outbox.process(item.operation_id, first_submission=True)
    assert provider.submissions == [item.operation_id]
    with pytest.raises(ValueError):
        outbox.enqueue(BANK, replace(item, content=item.content + " changed"), 7)


def test_submitted_operation_missing_is_not_blindly_resubmitted(rig):
    outbox, provider, _ = rig
    item = prepared()
    outbox.enqueue(BANK, item, 7)
    outbox.process(item.operation_id, first_submission=True)
    del provider.remote[item.operation_id]
    due(outbox, item.operation_id)
    entry = outbox.process(item.operation_id)
    assert entry.state == "unknown"
    assert entry.error_code == "operation_missing_after_ack"
    assert entry.next_attempt_at is None
    assert provider.submissions == [item.operation_id]


def test_hindsight_lookup_maps_sdk_status_without_raw_error(monkeypatch):
    operation_id = prepared().operation_id

    class Operations:
        async def get_operation_status(self, bank_id, requested, **kwargs):
            assert bank_id == BANK and requested == operation_id
            assert kwargs["include_payload"] is False
            return SimpleNamespace(operation_id=requested, status="failed",
                                   error_message="Authorization: Bearer private-value")

    class Client:
        def __init__(self, **kwargs):
            self.operations = Operations()

        async def aclose(self):
            pass

    monkeypatch.setattr(
        "code_agent.experience_memory.providers.hindsight.importlib.import_module",
        lambda _: SimpleNamespace(Hindsight=Client),
    )
    result = HindsightExperienceMemoryProvider(
        ExperienceMemoryConfig(enabled=True, api_key=KEY),
    ).get_operation(BANK, operation_id)
    assert result == OperationLookup(MemoryStatus.OK, OperationState.FAILED,
                                     "provider_failed")


def test_disabled_provider_uses_no_network_or_outbox(tmp_path):
    service = ExperienceMemoryService(ExperienceMemoryConfig(), tmp_path)
    assert service.get_operation(BANK, prepared().operation_id).status == MemoryStatus.DISABLED
    assert not (tmp_path / "runs.db").exists()


def test_retry_limit_and_backoff_stop_uncertain_operations(rig):
    outbox, provider, _ = rig
    item = prepared()
    provider.status = MemoryStatus.TIMEOUT
    outbox.enqueue(BANK, item, 7)
    entry = outbox.process(item.operation_id, first_submission=True)
    for _ in range(5):
        due(outbox, item.operation_id)
        entry = outbox.process(item.operation_id)
    assert entry.state == "unknown" and entry.error_code == "retry_limit"
    assert entry.next_attempt_at is None and entry.attempts == 6
    assert outbox.recover() == []


def test_old_ambiguous_operation_is_not_resubmitted(rig):
    outbox, provider, _ = rig
    item = prepared()
    provider.fail_once = "before_accept"
    outbox.enqueue(BANK, item, 7)
    outbox.process(item.operation_id, first_submission=True)
    with outbox._connect() as conn:
        conn.execute(
            "update experience_memory_outbox set created_at = ?, next_attempt_at = 0 where operation_id = ?",
            (time.time() - 90_000, item.operation_id),
        )
    entry = outbox.process(item.operation_id)
    assert entry.error_code == "ambiguity_window_expired"
    assert entry.next_attempt_at is None
    assert provider.submissions == [item.operation_id]


def test_hindsight_lookup_404_means_not_found(monkeypatch):
    operation_id = prepared().operation_id

    class Missing(Exception):
        status = 404

    class Operations:
        async def get_operation_status(self, *args, **kwargs):
            raise Missing("Authorization: Bearer private-value")

    class Client:
        def __init__(self, **kwargs):
            self.operations = Operations()

        async def aclose(self):
            pass

    monkeypatch.setattr(
        "code_agent.experience_memory.providers.hindsight.importlib.import_module",
        lambda _: SimpleNamespace(Hindsight=Client),
    )
    result = HindsightExperienceMemoryProvider(
        ExperienceMemoryConfig(enabled=True, api_key=KEY),
    ).get_operation(BANK, operation_id)
    assert result == OperationLookup(MemoryStatus.OK, OperationState.NOT_FOUND)


def test_factory_runs_bounded_startup_recovery_without_broad_approval(monkeypatch, tmp_path):
    from code_agent.config import Settings
    from code_agent.factory import create_agent

    class Model:
        model = "test-model"

        def complete(self, messages):
            return '{"type":"final","message":"done"}'

    calls = []
    monkeypatch.setattr("code_agent.factory.create_fallback_client", lambda *args: Model())
    monkeypatch.setattr("code_agent.credentials.keyring.CredentialStore.get_provider_key", lambda *args: None)
    monkeypatch.setattr(
        "code_agent.factory.ExperienceMemoryOutbox.recover",
        lambda self, *, max_items: calls.append(("recover", max_items)) or [],
    )
    monkeypatch.setattr(
        "code_agent.factory.start_recovery_worker",
        lambda db_path, service: calls.append(("worker", service.config.timeout_seconds)),
    )
    settings = Settings(
        _env_file=None, openrouter_api_key="model-key", agent_model="test-model",
        agent_db_path=tmp_path / "agent.db", agent_execution_db_path=tmp_path / "execution.db",
        agent_reviewer_pass=False, agent_stream=False,
        agent_experience_memory_enabled=True, hindsight_api_key=KEY,
    )
    agent = create_agent(settings, tmp_path, None, False, 2)
    assert calls == [("recover", 3), ("worker", 2.0)]
    assert agent.experience_memory.enabled
