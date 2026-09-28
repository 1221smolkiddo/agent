"""Qualification-only regression evidence; core fixes belong to Session A."""
from __future__ import annotations

import json
import logging
import sys
import uuid
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from code_agent.cli import app
from code_agent.experience_memory.config import ExperienceMemoryConfig
from code_agent.experience_memory.contracts import (
    ExperienceMemoryRecall, MemoryProvenance, MemoryResult, MemoryStatus,
    OperationLookup, OperationState, RecalledMemory,
)
from code_agent.experience_memory.episode_sanitizer import MemorySanitizer
from code_agent.experience_memory.providers.null import NullExperienceMemoryProvider
from code_agent.experience_memory.service import ExperienceMemoryService
from code_agent.memory_eval import _metric_view, human_report, run_evaluation
from code_agent.memory_operator import _entry_view, run_live_smoke

CATEGORIES = ("api_key", "short_bearer", "environment", "password", "jwt")


def fake_credential(category):
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl"
    return {
        "api_key": ("sk-test-secret-value", "sk-test-secret-value"),
        "short_bearer": ("Authorization: Bearer abc123", "abc123"),
        "environment": ("API_KEY=supersecret", "supersecret"),
        "password": ("password=hunter2", "hunter2"),
        "jwt": (jwt, jwt),
    }[category]


@pytest.mark.parametrize("category", CATEGORIES)
def test_core_sanitizer_removes_fake_credentials(category):
    # Keep the assertion/exception text free of credential values, even on failure.
    payload, marker = fake_credential(category)
    sanitized = MemorySanitizer(ExperienceMemoryConfig(api_key="sk-test-secret-value")).sanitize_text(payload)
    safe = marker not in sanitized
    assert safe, "Core sanitizer gap; see CORE_HANDOFF_NOTES.md (values omitted)."


@pytest.mark.parametrize("category", CATEGORIES)
def test_operator_provider_error_logs_and_exception_surfaces_are_safe(tmp_path, monkeypatch, caplog, category):
    import code_agent.memory_operator as operator

    payload, marker = fake_credential(category)
    class Provider:
        def health(self):
            raise RuntimeError(payload)
    service = ExperienceMemoryService(
        ExperienceMemoryConfig(enabled=True, api_key="sk-test-secret-value"), tmp_path, provider=Provider(),
    )
    monkeypatch.setattr(operator, "_service", lambda: (None, service))
    with caplog.at_level(logging.DEBUG):
        result = CliRunner().invoke(app, ["memory", "health"])
    safe = marker not in result.output + str(result.exception) + caplog.text
    assert safe, "Operator leaked a provider error (values omitted)."
    assert result.exit_code == 1 and json.loads(result.output)["health"] == "provider_unavailable"


@pytest.mark.parametrize("category", CATEGORIES)
def test_qualification_json_and_human_report_discard_secret_values(tmp_path, category):
    payload, marker = fake_credential(category)
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps({"scenarios": [{
        "name": "repeated_bug_class", "task": "Fix a recurring synthetic bug", "fixture": "fixture",
    }]}), encoding="utf-8")
    runner = tmp_path / "runner.py"
    runner.write_text("import json, os, pathlib\n"
                      "print(" + repr(payload) + ")\n"
                      "pathlib.Path(os.environ['AGENT47_EVAL_METRICS']).write_text(" + repr(json.dumps({
                          "model_calls": payload, "raw_provider_error": payload,
                          "verified_completion": True,
                      })) + ")\n", encoding="utf-8")
    output = tmp_path / "report.json"
    report = run_evaluation(manifest, [sys.executable, str(runner)], output)
    safe = marker not in output.read_text() + output.with_suffix(".md").read_text() + human_report(report)
    assert safe, "Qualification artifacts retained sensitive runner data (values omitted)."
    assert report["results"][0]["arms"]["A"]["metrics"]["model_calls"] is None


def test_operator_inspection_redacts_valid_identifier_containing_configured_key():
    key = "qualified-private-provider-value"
    entry = SimpleNamespace(
        prepared=SimpleNamespace(operation_id=str(uuid.uuid4()),
            document_id=f"agent47:{key}:probe:episode:v1", content=key, branch=key, head=key),
        state="prepared", next_attempt_at=0, error_code="", attempts=0, submitted=False,
        created_at=0, updated_at=0, lease_until=None, network_requests=0, wall_seconds=0,
    )
    view = _entry_view(entry, config=ExperienceMemoryConfig(api_key=key))
    safe = key not in json.dumps(view)
    assert safe, "Inspection exposed configured credential material (values omitted)."
    assert view["document_id"] == "<invalid>" and view["queue_state"] == "queued"
    assert "content" not in view and "branch" not in view and "head" not in view


def test_malformed_health_status_is_normalized_without_exception_text(tmp_path, monkeypatch):
    import code_agent.memory_operator as operator
    class Provider:
        def health(self):
            return MemoryResult("sk-test-secret-value")
    service = ExperienceMemoryService(ExperienceMemoryConfig(enabled=True, api_key="configured"),
                                      tmp_path, provider=Provider())
    monkeypatch.setattr(operator, "_service", lambda: (None, service))
    result = CliRunner().invoke(app, ["memory", "health"])
    safe = "sk-test-secret-value" not in result.output + str(result.exception)
    assert safe, "Malformed status exposed input (values omitted)."
    assert result.exit_code == 1 and json.loads(result.output)["status"] == "unavailable"


def test_oversized_numeric_metrics_fail_closed():
    assert _metric_view({"model_calls": 10 ** 1000})["model_calls"] is None


def test_abc_controls_are_independent_and_order_rotates(tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / ".env").write_text("API_KEY=supersecret", encoding="utf-8")
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps({"scenarios": [{
        "name": name, "task": "Synthetic engineering probe", "fixture": "fixture",
    } for name in ("repeated_bug_class", "recurring_ci_failure", "multi_session_migration")]}), encoding="utf-8")
    runner = tmp_path / "runner.py"
    runner.write_text("""import json, os, pathlib
arm = os.environ['AGENT47_EVAL_ARM']
assert os.environ['AGENT_EXPERIENCE_MEMORY_ENABLED'] == ('false' if arm == 'A' else 'true')
assert os.environ['AGENT_EXPERIENCE_MEMORY_AUTOMATIC_RECALL_MAX_REQUESTS'] == ('0' if arm == 'A' else '1')
assert os.environ['AGENT_EXPERIENCE_MEMORY_AUTOMATIC_REFLECT_ENABLED'] == ('true' if arm == 'C' else 'false')
assert os.environ['AGENT_EXPERIENCE_MEMORY_AUTOMATIC_REFLECT_MAX_REQUESTS'] == ('1' if arm == 'C' else '0')
assert not pathlib.Path('.env').exists()
pathlib.Path(os.environ['AGENT47_EVAL_METRICS']).write_text(json.dumps({
    'verified_completion': True, 'recall_requests': int(arm != 'A'), 'reflect_requests': int(arm == 'C'),
}))
""", encoding="utf-8")
    result = run_evaluation(manifest, [sys.executable, str(runner)], tmp_path / "results.json",
                            include_reflect=True, timeout_seconds=5)
    assert [row["order"] for row in result["results"]] == [["A", "B", "C"], ["B", "C", "A"], ["C", "A", "B"]]
    assert all(not arm["error"] for row in result["results"] for arm in row["arms"].values())
    assert all(row["arms"]["C"]["metrics"]["reflect_requests"] == 1 for row in result["results"])
    assert "C=recall plus Reflect" in human_report(result)


class SyntheticProvider(NullExperienceMemoryProvider):
    def __init__(self):
        super().__init__(MemoryStatus.OK)
        self.calls = []
        self.retained = None
    def health(self):
        self.calls.append("health")
        return MemoryResult(MemoryStatus.OK)
    def retain(self, bank_id, experience):
        self.calls.append("retain")
        self.retained = experience
        return MemoryResult(MemoryStatus.OK)
    def get_operation(self, bank_id, operation_id):
        self.calls.append("poll")
        assert operation_id == self.retained.operation_id
        return OperationLookup(MemoryStatus.OK, OperationState.COMPLETED)
    def recall_detailed(self, bank_id, request):
        self.calls.append("recall")
        return ExperienceMemoryRecall(MemoryStatus.OK, (RecalledMemory(request.query,
            provenance=MemoryProvenance(document_id=self.retained.document_id, repository_bank_id=bank_id)),))
    def reflect(self, bank_id, query):
        self.calls.append("reflect")
        return MemoryResult(MemoryStatus.OK, text=query + " Authorization: Bearer abc123")


def test_live_provider_smoke_covers_reflect_and_never_returns_payload(tmp_path):
    provider = SyntheticProvider()
    service = ExperienceMemoryService(ExperienceMemoryConfig(enabled=True, api_key="configured"),
                                      tmp_path, provider=provider)
    result = run_live_smoke(service, "agent47-repo-" + "a" * 64, timeout_seconds=5, include_reflect=True)
    assert result["passed"] and provider.calls == ["health", "retain", "poll", "recall", "reflect"]
    assert result["reflect_returned_count"] == 1 and result["health_status"] == "ok"
    safe = "abc123" not in json.dumps(result)
    assert safe, "Provider smoke retained reflection payload (values omitted)."
    assert all(result[name] >= 0 for name in (
        "retain_submission_latency_seconds", "indexing_completion_latency_seconds",
        "recall_latency_seconds", "reflect_latency_seconds",
    ))


def test_live_reflect_timeout_stays_safe(tmp_path):
    class Provider(SyntheticProvider):
        def reflect(self, bank_id, query):
            raise TimeoutError("password=hunter2")
    service = ExperienceMemoryService(ExperienceMemoryConfig(enabled=True, api_key="configured"),
                                      tmp_path, provider=Provider())
    result = run_live_smoke(service, "agent47-repo-" + "a" * 64, include_reflect=True)
    safe = "hunter2" not in json.dumps(result)
    assert safe and result["stage"] == "reflect" and result["status"] == "timeout"


def test_disabled_benchmark_instruments_agent_and_memory_boundaries(monkeypatch):
    import subprocess
    from code_agent.memory_benchmark import benchmark_disabled
    original = subprocess.run
    def measured_child(command, **kwargs):
        if len(command) > 1 and command[1] == "-c":
            return SimpleNamespace(stdout=json.dumps({"seconds": 0.01, "sdk_loaded": False,
                "network_attempts": 0, "sdk_import_attempts": 0}))
        return original(command, **kwargs)
    monkeypatch.setattr(subprocess, "run", measured_child)
    result = benchmark_disabled(iterations=3, samples=3)
    assert len(result["agent_construction_samples_seconds"]) == 3
    assert len(result["agent_run_samples_seconds"]) == 3
    assert all(result[name] == 0 for name in (
        "network_attempts", "sdk_import_attempts", "automatic_recall_calls", "automatic_reflect_calls",
    ))


def test_operator_cli_has_no_purge_or_delete_command():
    result = CliRunner().invoke(app, ["memory", "--help"])
    assert result.exit_code == 0
    for name in ("status", "health", "outbox", "inspect", "retry", "live-smoke"):
        assert name in result.output
    assert "purge" not in result.output and "delete" not in result.output



def test_real_model_definition_cannot_run_before_core_handoff(tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps({"runner_kind": "real_model", "final_core_sha": None,
        "scenarios": [{"name": "repeated_bug_class", "task": "Synthetic", "fixture": "fixture"}]}))
    with pytest.raises(ValueError, match="reviewed final core SHA"):
        run_evaluation(manifest, ["must-not-run"], tmp_path / "results.json", include_reflect=True)
    assert not (tmp_path / "results.json").exists()



@pytest.mark.parametrize("category", CATEGORIES)
def test_agent_telemetry_work_reports_and_logs_omit_credential_values(tmp_path, caplog, category):
    from code_agent.agent import CodingAgent
    from code_agent.evals import ScriptedModel
    from code_agent.storage import AgentStorage
    from code_agent.tools import ToolRegistry

    payload, marker = fake_credential(category)
    class Provider(NullExperienceMemoryProvider):
        def recall_detailed(self, bank_id, request):
            return ExperienceMemoryRecall(MemoryStatus.OK, (RecalledMemory(
                "Synthetic historical parser lesson. " + payload,
            ),))
    (tmp_path / "payload.py").write_text("value = 1\n", encoding="utf-8")
    service = ExperienceMemoryService(ExperienceMemoryConfig(enabled=True, api_key="sk-test-secret-value"),
                                      tmp_path, provider=Provider(MemoryStatus.OK))
    model = ScriptedModel([
        '{"type":"write_file","path":"payload.py","content":"value = 2\\n"}',
        '{"type":"final","message":"Updated the synthetic parser fixture."}',
    ])
    storage = AgentStorage(tmp_path / "runs.db")
    tools = ToolRegistry(tmp_path, False, approval_callback=lambda *_: True)
    agent = CodingAgent(tmp_path, False, 5, 3, model, tools, storage,
                        stream_model=False, experience_memory=service)
    try:
        with caplog.at_level(logging.DEBUG):
            result = agent.run_detailed("Fix the recurring parser failure in this project.")
        surfaces = json.dumps(storage.run_steps_payloads(result.run_id)) + str(
            storage.get_work_report(result.run_id)) + caplog.text
        safe = marker not in surfaces
        assert safe, "Telemetry/work report/log boundary retained credential material (values omitted)."
        assert len(model.messages_seen) == 2  # No eligibility classification model call.
    finally:
        tools.close()



@pytest.mark.parametrize("change", [
    {"max_steps": 9}, {"max_output_tokens": 2048}, {"max_context_chars": 1000},
    {"provider_budget_usd": 6}, {"provider_budget_enforced": False},
    {"historical_seed_snapshot_reviewed": False},
])
def test_paid_trial_limits_refuse_execution_before_any_command(monkeypatch, change):
    from code_agent.memory_eval import _real_model_environment
    source = {"final_core_sha": "f" * 40, "historical_seed_snapshot_reviewed": True,
              "limits": {"max_steps": 6, "max_output_tokens": 1024, "max_context_chars": 8000,
                         "provider_budget_usd": 5, "provider_budget_enforced": True}}
    if "historical_seed_snapshot_reviewed" in change:
        source.update(change)
    else:
        source["limits"].update(change)
    def forbidden(*args, **kwargs):
        raise AssertionError("Refused paid trial launched a command.")
    monkeypatch.setattr("code_agent.memory_eval.subprocess.run", forbidden)
    with pytest.raises(ValueError):
        _real_model_environment(source, [{}], include_reflect=True, timeout_seconds=90, trials=1)
