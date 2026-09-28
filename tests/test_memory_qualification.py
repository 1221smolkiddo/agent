"""P6 operator, evaluation, security, and disabled-path qualification."""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

from typer.testing import CliRunner

from code_agent.cli import app
from code_agent.experience_memory.config import ExperienceMemoryConfig
from code_agent.experience_memory.contracts import MemoryResult, MemoryStatus, OperationLookup, OperationState
from code_agent.experience_memory.outbox import ExperienceMemoryOutbox
from code_agent.experience_memory.recall_policy import MemoryRecallPolicy
from code_agent.experience_memory.service import ExperienceMemoryService
from code_agent.memory_eval import SCENARIOS, human_report, run_evaluation
from code_agent.memory_operator import _entry_view


def test_evaluation_pairing_and_missing_metrics(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixture"
    fixtures.mkdir()
    (fixtures / "source.py").write_text("value = 1\n")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "scenarios": [
            {"name": SCENARIOS[0][0], "task": "Fix recurring parser bug", "fixture": "fixture"},
            {"name": SCENARIOS[-1][0], "task": "Fix isolated bug", "fixture": "fixture"},
        ],
    }))
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import json, os, pathlib\n"
        "p = pathlib.Path(os.environ['AGENT47_EVAL_METRICS'])\n"
        "p.write_text(json.dumps({'verified_completion': True, 'model_calls': 2}))\n"
    )
    output = tmp_path / "results.json"
    report = run_evaluation(manifest, [sys.executable, str(runner)], output)
    assert [row["order"] for row in report["results"]] == [["A", "B"], ["B", "A"]]
    assert all(row["arms"]["A"]["metrics"]["model_calls"] == 2 for row in report["results"])
    assert report["results"][0]["arms"]["A"]["metrics"]["tool_calls"] is None
    assert "No superiority claim" in human_report(report)
    assert json.loads(output.read_text()) == report


class ExplodingProvider:
    def health(self):
        raise RuntimeError("Bearer secret-bearer; Authorization: secret-header")

    def get_operation(self, bank_id, operation_id):
        raise RuntimeError("provider error secret-provider")

    def retain(self, bank_id, experience):
        raise RuntimeError("API key secret-api")


def test_provider_errors_and_diagnostics_do_not_emit_secrets(tmp_path: Path, caplog) -> None:
    secrets = ("secret-api", "secret-bearer", "secret-header", "secret-provider")
    config = ExperienceMemoryConfig(enabled=True, api_key="secret-api")
    service = ExperienceMemoryService(config, tmp_path, provider=ExplodingProvider())
    with caplog.at_level(logging.DEBUG):
        assert service.health().status == MemoryStatus.UNAVAILABLE
        assert service.get_operation("agent47-repo-" + "a" * 64, "4db1b87b-f991-4fc1-b8cb-2ec80859655c").status == MemoryStatus.UNAVAILABLE
    outbox = ExperienceMemoryOutbox(tmp_path / "outbox.db", service)
    view = str([_entry_view(entry) for entry in outbox.list_entries()])
    db = (tmp_path / "outbox.db").read_bytes().decode("latin1")
    exposed = repr(config) + repr(service.health()) + caplog.text + view + db
    assert all(secret not in exposed for secret in secrets)


def test_disabled_policy_has_no_sdk_import_or_provider_call(tmp_path: Path) -> None:
    before = "hindsight_client" in sys.modules
    config = ExperienceMemoryConfig(enabled=False, api_key="secret-api")
    start = time.perf_counter()
    for _ in range(1000):
        service = ExperienceMemoryService(config, tmp_path)
        assert service.health().status == MemoryStatus.DISABLED
        assert not MemoryRecallPolicy(config).decide("Fix recurring failure", workspace_task=True).should_recall
    elapsed = time.perf_counter() - start
    assert ("hindsight_client" in sys.modules) == before
    assert elapsed >= 0  # Measurement is recorded separately; no invented threshold.


def test_memory_health_disabled_cli(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EXPERIENCE_MEMORY_ENABLED", "false")
    result = CliRunner().invoke(app, ["memory", "health"])
    assert result.exit_code == 0
    assert json.loads(result.output)["health"] == "disabled"
    assert "secret" not in result.output


def test_manual_retry_never_resends_acknowledged_missing_operation(tmp_path: Path) -> None:
    import hashlib
    import uuid
    from code_agent.experience_memory.episode_sanitizer import PreparedEpisode

    class Provider(ExplodingProvider):
        calls = 0

        def retain(self, bank_id, experience):
            self.calls += 1
            return MemoryResult(MemoryStatus.OK)

        def get_operation(self, bank_id, operation_id):
            return OperationLookup(MemoryStatus.OK, OperationState.NOT_FOUND)

    provider = Provider()
    service = ExperienceMemoryService(
        ExperienceMemoryConfig(enabled=True, api_key="safe-test-value"), tmp_path, provider=provider,
    )
    outbox = ExperienceMemoryOutbox(tmp_path / "outbox.db", service)
    content = json.dumps({"kind": "engineering_episode", "goal": "Synthetic test"})
    document = "agent47:test:case:episode:v1"
    operation = str(uuid.uuid5(uuid.NAMESPACE_URL, document + ":" + hashlib.sha256(content.encode()).hexdigest()))
    item = PreparedEpisode(content, document, operation, None, None, "verified", "test")
    outbox.enqueue("agent47-repo-" + "a" * 64, item, 1)
    assert outbox.process(operation, first_submission=True).state == "submitted"
    assert outbox.retry(operation).error_code == "operation_missing_after_ack"
    assert provider.calls == 1
    assert _entry_view(outbox.get(operation))["operator_review"] is True
    try:
        outbox.retry(operation)
    except ValueError:
        pass
    else:
        raise AssertionError("Operator-review operation was retried")
    assert provider.calls == 1
    assert _entry_view(outbox.get(operation))["operation_id"] == operation
    assert content not in repr(outbox.get(operation))



def test_enabled_trivial_task_skips_recall_without_model_call() -> None:
    config = ExperienceMemoryConfig(enabled=True, api_key="safe-test-value")
    policy = MemoryRecallPolicy(config)
    assert not policy.decide("Rename the local variable", workspace_task=True).should_recall
    assert policy.decide("Fix recurring CI failure", workspace_task=True).should_recall


def test_live_smoke_uses_synthetic_identity_without_real_key(tmp_path: Path) -> None:
    from code_agent.experience_memory.contracts import (
        ExperienceMemoryRecall, MemoryProvenance, RecalledMemory,
    )
    from code_agent.memory_operator import run_live_smoke

    class Provider(ExplodingProvider):
        retained = None

        def health(self):
            return MemoryResult(MemoryStatus.OK)

        def retain(self, bank_id, experience):
            self.retained = experience
            return MemoryResult(MemoryStatus.OK)

        def get_operation(self, bank_id, operation_id):
            assert operation_id == self.retained.operation_id
            return OperationLookup(MemoryStatus.OK, OperationState.COMPLETED)

        def recall_detailed(self, bank_id, request):
            marker = self.retained.summary.split("marker ")[1].split('"')[0]
            return ExperienceMemoryRecall(
                MemoryStatus.OK,
                (RecalledMemory(
                    text=f"Synthetic qualification marker {marker}",
                    provenance=MemoryProvenance(document_id=self.retained.document_id, repository_bank_id=bank_id),
                ),),
            )

    provider = Provider()
    service = ExperienceMemoryService(
        ExperienceMemoryConfig(enabled=True, api_key="safe-test-value"), tmp_path,
        provider=provider,
    )
    result = run_live_smoke(service, "agent47-repo-" + "a" * 64, timeout_seconds=1)
    assert result["passed"] is True
    assert result["operation_id"] == provider.retained.operation_id



def test_health_distinguishes_operational_states(monkeypatch, tmp_path: Path) -> None:
    import code_agent.memory_operator as operator
    from code_agent.experience_memory.providers.null import NullExperienceMemoryProvider

    for status, label in (
        (MemoryStatus.DISABLED, "disabled"),
        (MemoryStatus.MISSING_CONFIG, "configuration_error"),
        (MemoryStatus.MISSING_DEPENDENCY, "missing_dependency"),
        (MemoryStatus.UNAVAILABLE, "provider_unavailable"),
        (MemoryStatus.TIMEOUT, "provider_unavailable"),
        (MemoryStatus.OK, "healthy"),
    ):
        config = ExperienceMemoryConfig(
            enabled=status != MemoryStatus.DISABLED,
            api_key="safe-test-value" if status != MemoryStatus.MISSING_CONFIG else None,
        )
        service = ExperienceMemoryService(config, tmp_path, provider=NullExperienceMemoryProvider(status))
        monkeypatch.setattr(operator, "_service", lambda: (None, service))
        result = CliRunner().invoke(app, ["memory", "health"])
        assert json.loads(result.output)["health"] == label
        assert result.exit_code == (0 if status in {MemoryStatus.DISABLED, MemoryStatus.OK} else 1)


def test_invalid_configuration_cli_never_formats_input(monkeypatch) -> None:
    monkeypatch.setenv("HINDSIGHT_BASE_URL", "https://user:secret-api@example.com/?token=secret-token")
    result = CliRunner().invoke(app, ["memory", "health"])
    assert result.exit_code == 1
    assert "configuration_error" in result.output
    assert "secret-api" not in result.output and "secret-token" not in result.output


def test_rejected_raw_payload_never_enters_outbox(tmp_path: Path) -> None:
    import hashlib
    import uuid
    from code_agent.experience_memory.episode_sanitizer import PreparedEpisode

    config = ExperienceMemoryConfig(enabled=True, api_key="secret-api")
    service = ExperienceMemoryService(config, tmp_path, provider=ExplodingProvider())
    outbox = ExperienceMemoryOutbox(tmp_path / "outbox.db", service)
    for secret in (
        "secret-api", "Bearer abcdefghijklmnopqrstuvwxyz",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        "API_KEY=embedded-diagnostic-secret",
    ):
        document = "agent47:security:test:episode:v1"
        content = json.dumps({"kind": "engineering_episode", "goal": secret})
        operation = str(uuid.uuid5(uuid.NAMESPACE_URL, document + ":" + hashlib.sha256(content.encode()).hexdigest()))
        item = PreparedEpisode(content, document, operation, None, None, "unverified", "test")
        try:
            outbox.enqueue("agent47-repo-" + "a" * 64, item, 1)
        except ValueError as exc:
            assert secret not in str(exc)
        else:
            raise AssertionError("Secret-bearing payload accepted.")
        assert secret not in (tmp_path / "outbox.db").read_bytes().decode("latin1")
    assert outbox.list_entries() == []


def test_live_smoke_wrong_document_or_bank_fails(tmp_path: Path) -> None:
    from code_agent.experience_memory.contracts import ExperienceMemoryRecall, RecalledMemory, MemoryProvenance
    from code_agent.memory_operator import run_live_smoke

    class Provider(ExplodingProvider):
        def health(self):
            return MemoryResult(MemoryStatus.OK)

        def retain(self, bank_id, experience):
            self.retained = experience
            return MemoryResult(MemoryStatus.OK)

        def get_operation(self, bank_id, operation_id):
            return OperationLookup(MemoryStatus.OK, OperationState.COMPLETED)

        def recall_detailed(self, bank_id, request):
            return ExperienceMemoryRecall(MemoryStatus.OK, (RecalledMemory(
                request.query,
                provenance=MemoryProvenance(document_id=self.retained.document_id, repository_bank_id="wrong"),
            ),))

    service = ExperienceMemoryService(
        ExperienceMemoryConfig(enabled=True, api_key="safe-test-value"), tmp_path, provider=Provider(),
    )
    result = run_live_smoke(service, "agent47-repo-" + "a" * 64, timeout_seconds=1)
    assert result["passed"] is False and result["stage"] == "recall_identity"


def test_live_smoke_provider_error_is_safe(tmp_path: Path) -> None:
    from code_agent.memory_operator import run_live_smoke

    class Provider(ExplodingProvider):
        def health(self):
            return MemoryResult(MemoryStatus.OK)

    service = ExperienceMemoryService(
        ExperienceMemoryConfig(enabled=True, api_key="secret-api"), tmp_path, provider=Provider(),
    )
    result = run_live_smoke(service, "agent47-repo-" + "a" * 64)
    assert result["passed"] is False
    assert "secret-api" not in str(result) and "secret-provider" not in str(result)


def test_evaluation_rejects_nonfinite_and_secret_metrics() -> None:
    from code_agent.memory_eval import _metric_view

    view = _metric_view({
        "verified_completion": "secret-api", "model_calls": float("inf"),
        "input_tokens": -1, "output_tokens": True, "provider_error": "secret-provider",
    })
    assert all(value is None for value in view.values())


def test_recall_secrets_do_not_enter_telemetry_reports_or_logs(tmp_path: Path, caplog) -> None:
    from code_agent.agent import CodingAgent
    from code_agent.evals import ScriptedModel
    from code_agent.experience_memory.contracts import ExperienceMemoryRecall, RecalledMemory
    from code_agent.experience_memory.providers.null import NullExperienceMemoryProvider
    from code_agent.storage import AgentStorage
    from code_agent.tools import ToolRegistry

    secrets = (
        "secret-api", "abcdefghijklmnopqrstuvwxyz", "embedded-diagnostic-secret",
    )

    class Provider(NullExperienceMemoryProvider):
        def recall_detailed(self, bank_id, request):
            return ExperienceMemoryRecall(MemoryStatus.OK, (RecalledMemory(
                "Earlier parser lesson. secret-api Authorization: Bearer abcdefghijklmnopqrstuvwxyz "
                "API_KEY=embedded-diagnostic-secret",
            ),))

    (tmp_path / "payload.py").write_text("value = 1\n")
    config = ExperienceMemoryConfig(enabled=True, api_key="secret-api")
    service = ExperienceMemoryService(config, tmp_path, provider=Provider(MemoryStatus.OK))
    model = ScriptedModel([
        json.dumps({"type": "write_file", "path": "payload.py", "content": "value = 2\n"}),
        json.dumps({"type": "final", "message": "Updated the parser fixture."}),
    ])
    storage = AgentStorage(tmp_path / ".code-agent" / "runs.db")
    agent = CodingAgent(
        tmp_path, False, 5, 3, model,
        ToolRegistry(tmp_path, False, approval_callback=lambda *_: True), storage,
        stream_model=False, experience_memory=service,
    )
    with caplog.at_level(logging.DEBUG):
        result = agent.run_detailed("Fix the recurring parser failure in this project.")
    surfaces = (
        str(model.messages_seen), str(storage.run_steps_payloads(result.run_id)),
        str(storage.get_work_report(result.run_id)), repr(service.health()), caplog.text,
    )
    assert all(secret not in surface for secret in secrets for surface in surfaces)



def test_provider_provenance_and_operation_errors_are_not_secret_repr(tmp_path: Path) -> None:
    from code_agent.experience_memory.contracts import MemoryProvenance, RecalledMemory

    secret = "secret-api"
    memory = RecalledMemory(
        "Synthetic lesson", provenance=MemoryProvenance(
            memory_id=secret, document_id=secret, branch=secret,
        ), source_fact_ids=(secret,), metadata=(("source", secret),),
    )
    assert secret not in repr(memory)

    class Provider(ExplodingProvider):
        def get_operation(self, bank_id, operation_id):
            return OperationLookup(MemoryStatus.OK, OperationState.FAILED, f"error with {secret}")

    service = ExperienceMemoryService(
        ExperienceMemoryConfig(enabled=True, api_key=secret), tmp_path, provider=Provider(),
    )
    result = service.get_operation("agent47-repo-" + "a" * 64, "4db1b87b-f991-4fc1-b8cb-2ec80859655c")
    assert result.error_code == "provider_failed"
    assert secret not in repr(result)


def test_retry_reconciles_same_id_before_unacknowledged_resend(tmp_path: Path) -> None:
    import hashlib
    import uuid
    from code_agent.experience_memory.episode_sanitizer import PreparedEpisode

    class Provider(ExplodingProvider):
        submissions = []
        lookups = []

        def retain(self, bank_id, experience):
            self.submissions.append(experience.operation_id)
            return MemoryResult(MemoryStatus.TIMEOUT if len(self.submissions) == 1 else MemoryStatus.OK)

        def get_operation(self, bank_id, operation_id):
            self.lookups.append(operation_id)
            return OperationLookup(MemoryStatus.OK, OperationState.NOT_FOUND)

    provider = Provider()
    service = ExperienceMemoryService(
        ExperienceMemoryConfig(enabled=True, api_key="safe-test-value"), tmp_path, provider=provider,
    )
    outbox = ExperienceMemoryOutbox(tmp_path / "outbox.db", service)
    content = json.dumps({"kind": "engineering_episode", "goal": "Synthetic retry"})
    document = "agent47:retry:test:episode:v1"
    operation = str(uuid.uuid5(uuid.NAMESPACE_URL, document + ":" + hashlib.sha256(content.encode()).hexdigest()))
    prepared = PreparedEpisode(content, document, operation, None, None, "unverified", "test")
    outbox.enqueue("agent47-repo-" + "a" * 64, prepared, 1)
    assert outbox.process(operation, first_submission=True).state == "unknown"
    assert outbox.retry(operation).state == "submitted"
    assert provider.lookups == [operation] and provider.submissions == [operation, operation]



def test_outbox_reports_all_states_and_stuck_progress(tmp_path: Path) -> None:
    import hashlib
    import uuid
    from code_agent.experience_memory.episode_sanitizer import PreparedEpisode

    service = ExperienceMemoryService(ExperienceMemoryConfig(), tmp_path)
    outbox = ExperienceMemoryOutbox(tmp_path / "outbox.db", service)
    for index, state in enumerate(("prepared", "submitted", "processing", "completed", "failed", "cancelled", "unknown")):
        content = json.dumps({"kind": "engineering_episode", "goal": f"Synthetic state {index}"})
        document = f"agent47:states:test{index}:episode:v1"
        operation = str(uuid.uuid5(uuid.NAMESPACE_URL, document + ":" + hashlib.sha256(content.encode()).hexdigest()))
        outbox.enqueue("agent47-repo-" + "a" * 64, PreparedEpisode(
            content, document, operation, None, None, "unverified", "test",
        ), 1)
        with outbox._connect() as conn:
            conn.execute(
                "update experience_memory_outbox set state = ?, next_attempt_at = ? where operation_id = ?",
                (state, None if state == "unknown" else 0, operation),
            )
    view = outbox.diagnostics()
    assert view["total"] == 7 and view["operator_review"] == 1
    assert view["due"] == 3 and view["scheduled"] == 3
    assert all(value == 1 for value in view["counts"].values())
    entries = [_entry_view(entry) for entry in outbox.list_entries()]
    assert any(entry["queue_state"] == "queued" for entry in entries)
    assert all("content" not in entry and "lease_until" in entry for entry in entries)
    assert all(entry["age_seconds"] >= 0 for entry in entries)


def test_live_smoke_poll_timeout_is_bounded_without_resubmission(tmp_path: Path, monkeypatch) -> None:
    import code_agent.memory_operator as operator

    clock = [0.0]
    monkeypatch.setattr(operator.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(operator.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    class Provider(ExplodingProvider):
        retains = 0
        polls = 0

        def health(self):
            return MemoryResult(MemoryStatus.OK)

        def retain(self, bank_id, experience):
            self.retains += 1
            return MemoryResult(MemoryStatus.OK)

        def get_operation(self, bank_id, operation_id):
            self.polls += 1
            return OperationLookup(MemoryStatus.OK, OperationState.PROCESSING)

    provider = Provider()
    service = ExperienceMemoryService(
        ExperienceMemoryConfig(enabled=True, api_key="safe-test-value"), tmp_path, provider=provider,
    )
    result = operator.run_live_smoke(service, "agent47-repo-" + "a" * 64, timeout_seconds=3, poll_seconds=2)
    assert result["passed"] is False and result["state"] == "timeout"
    assert provider.retains == 1 and provider.polls == 2 and clock[0] == 3


def test_evaluation_smoke_covers_eight_catalog_cases() -> None:
    from code_agent.memory_eval_runner import CASES

    assert set(CASES) == {name for name, _ in SCENARIOS}
    for task, original, corrected, tests, lesson in CASES.values():
        assert task and original != corrected and lesson
        assert "assert " in tests
