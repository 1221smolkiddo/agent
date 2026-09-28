"""Synthetic repository scenarios for offline P6 wiring qualification.

Both arms use the same scripted responses. This tests plumbing and measurement,
not model intelligence or a Hindsight deployment.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from .memory_eval import SCENARIOS

CASES = {
    "repeated_bug_class": (
        "Fix the recurring blank-token parser bug in this project.",
        "def parse(text):\n    return [int(token) for token in text.split(',')]\n",
        "def parse(text):\n    return [int(token) for token in text.split(',') if token.strip()]\n",
        "from payload import parse\n\ndef test_parse():\n    assert parse('1,,2') == [1, 2]\n",
        "Earlier parser failures required ignoring empty fields, with a regression test.",
    ),
    "recurring_ci_failure": (
        "Fix the recurring CI failure caused by changed working directory in this project.",
        "from pathlib import Path\n\ndef load():\n    return Path('config.txt').read_text().strip()\n",
        "from pathlib import Path\n\ndef load():\n    return Path(__file__).with_name('config.txt').read_text().strip()\n",
        "from payload import load\n\ndef test_load(tmp_path, monkeypatch):\n    monkeypatch.chdir(tmp_path)\n    assert load() == 'configured'\n",
        "CI changes the working directory. Resolve package data relative to the module.",
    ),
    "rejected_approach_later": (
        "Fix the timeout retry bug in this project using the previously rejected approach lesson.",
        "def attempts(limit):\n    return list(range(limit + 1))\n",
        "def attempts(limit):\n    return list(range(limit))\n",
        "from payload import attempts\n\ndef test_attempts():\n    assert attempts(3) == [0, 1, 2]\n",
        "A previous retry change was rejected because it exceeded the attempt limit.",
    ),
    "multi_session_migration": (
        "Continue the earlier config migration in this project while preserving the legacy key.",
        "def migrate(record):\n    return {'version': record['version']}\n",
        "def migrate(record):\n    return {'version': record.get('version', record.get('legacy_version'))}\n",
        "from payload import migrate\n\ndef test_migrate():\n    assert migrate({'legacy_version': 1}) == {'version': 1}\n    assert migrate({'version': 2}) == {'version': 2}\n",
        "The earlier migration session introduced version but legacy_version must remain readable.",
    ),
    "release_rollback_lesson": (
        "Fix the release rollback selector in this project using the earlier release lesson.",
        "def rollback(versions):\n    return versions[-1]\n",
        "def rollback(versions):\n    return versions[-2]\n",
        "from payload import rollback\n\ndef test_rollback():\n    assert rollback(['stable', 'broken']) == 'stable'\n",
        "Rollback must select the last stable deployment, rather than the current broken release.",
    ),
    "architecture_decision_recall": (
        "Implement the previous architecture decision for deterministic ordering in this project.",
        "def ordered(items):\n    return list(items)\n",
        "def ordered(items):\n    return sorted(items)\n",
        "from payload import ordered\n\ndef test_ordered():\n    assert ordered(['b', 'a']) == ['a', 'b']\n",
        "The architecture decision requires a stable sorted order for repeatable outputs.",
    ),
    "simple_rename": (
        "Rename the local variable in this project from old to result.",
        "def double(value):\n    old = value * 2\n    return old\n",
        "def double(value):\n    result = value * 2\n    return result\n",
        "from payload import double\n\ndef test_double():\n    assert double(3) == 6\n",
        "An unrelated historical migration changed package configuration.",
    ),
    "isolated_simple_bug": (
        "Fix the isolated arithmetic bug in this project.",
        "def add(a, b):\n    return a - b\n",
        "def add(a, b):\n    return a + b\n",
        "from payload import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        "An unrelated historical migration changed package configuration.",
    ),
}


def write_smoke_manifest(root: Path) -> Path:
    from .evals import _init_git_repo, python_pytest_project

    scenarios = []
    for name, _ in SCENARIOS:
        task, original, _, tests, _ = CASES[name]
        fixture = root / name
        fixture.mkdir()
        files = {
            **python_pytest_project(), "payload.py": original,
            "tests/test_payload.py": tests, "config.txt": "configured\n",
        }
        for relative, content in files.items():
            path = fixture / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        _init_git_repo(fixture)
        # Give each copied arm a stable, credential-free repository identity.
        subprocess.run(
            ["git", "remote", "add", "origin", f"https://example.invalid/p6/{name}.git"],
            cwd=fixture, check=True, capture_output=True,
        )
        scenarios.append({"name": name, "task": task, "fixture": name})
    manifest = root / "cases.json"
    manifest.write_text(json.dumps({
        "scenarios": scenarios, "runner_kind": "offline_scripted_synthetic",
    }), encoding="utf-8")
    return manifest


def run_scenario() -> dict[str, object]:
    from .agent import CodingAgent
    from .durable_execution import DurableExecutionRuntime
    from .evals import ScriptedModel
    from .experience_memory.config import ExperienceMemoryConfig
    from .experience_memory.contracts import ExperienceMemoryRecall, MemoryProvenance, MemoryStatus, RecalledMemory
    from .experience_memory.providers.null import NullExperienceMemoryProvider
    from .experience_memory.service import ExperienceMemoryService
    from .storage import AgentStorage
    from .tools import ToolRegistry

    name = os.environ["AGENT47_EVAL_SCENARIO"]
    _, _, corrected, _, lesson = CASES[name]
    workspace = Path.cwd()
    enabled = os.environ["AGENT47_EVAL_ARM"] == "B"
    config = ExperienceMemoryConfig(
        enabled=enabled, deployment="self_hosted", base_url="http://localhost:8888",
    )

    class SeededProvider(NullExperienceMemoryProvider):
        recall_calls = 0

        def recall_detailed(self, bank_id, request):
            self.recall_calls += 1
            scope = service.scope()
            return ExperienceMemoryRecall(MemoryStatus.OK, (
                RecalledMemory(
                    lesson, provenance=MemoryProvenance(
                        document_id=f"agent47:seed:{name}:episode:v1",
                        repository_bank_id=scope.bank_id, head=scope.head or "",
                        changed_paths=("payload.py",),
                    ),
                ),
            ))

    provider = SeededProvider(MemoryStatus.OK)
    service = ExperienceMemoryService(config, workspace, provider=provider)
    model = ScriptedModel([
        json.dumps({"type": "read_file", "path": "payload.py"}),
        json.dumps({"type": "write_file", "path": "payload.py", "content": corrected}),
        json.dumps({"type": "final", "message": "Applied the synthetic fixture repair."}),
    ])
    class ObservedTools(ToolRegistry):
        calls = None

        def run(self, action):
            import hashlib
            if self.calls is None:
                self.calls = []
            result = super().run(action)
            signature = hashlib.sha256(action.model_dump_json().encode()).hexdigest()
            self.calls.append((action.type, result.ok, signature))
            return result

    tools = ObservedTools(workspace=workspace, dry_run=False, approval_callback=lambda *_: True)
    storage = AgentStorage(workspace / ".code-agent" / "eval.db")
    runtime = DurableExecutionRuntime(workspace / ".code-agent" / "execution.db")
    agent = CodingAgent(
        cwd=workspace, dry_run=False, max_steps=8, max_failures=3,
        model_client=model,
        tools=tools,
        storage=storage, stream_model=False, durable_runtime=runtime,
        experience_memory=service,
    )
    started = time.perf_counter()
    result = agent.run_detailed(os.environ["AGENT47_EVAL_TASK"])
    latency = time.perf_counter() - started
    calls = tools.calls or []
    kinds = [kind for kind, _, _ in calls]
    checks = result.verification_results
    latest = {str(check.get("purpose") or check.get("command")): check for check in checks}
    state = runtime.engine.state(result.durable_execution_id)
    from .experience_memory.episodes import EngineeringEpisodeBuilder, EpisodeOutcome
    outcome = EngineeringEpisodeBuilder().build(result, state, None, service.scope()).outcome
    verified = outcome == EpisodeOutcome.VERIFIED and (workspace / "payload.py").read_text() == corrected
    context_bytes = 0
    if model.messages_seen:
        for message in model.messages_seen[0]:
            text = message.get("content", "")
            if isinstance(text, str) and "UNTRUSTED HISTORICAL CONTEXT" in text:
                context_bytes += len(text.encode("utf-8"))
    failed_signatures = [signature for _, ok, signature in calls if not ok]
    metrics = agent._recall_metrics
    reflect_available = "automatic_reflect_enabled" in ExperienceMemoryConfig.model_fields
    reflection_events = [item["payload"] for item in storage.run_steps_payloads(result.run_id)
                         if item["payload"].get("type") == "automatic_experience_reflect"]
    return {
        "verified_completion": verified,
        "first_pass_verification": bool(checks) and all(check.get("ok") is True for check in checks),
        "steps_to_verified_solution": len(model.messages_seen) if verified else None,
        "model_calls": len(model.messages_seen),
        "tool_calls": len(kinds),
        "repository_reads": sum(kind in {"read_file", "search", "list_files", "repo_map"} for kind in kinds),
        "verification_commands": len(checks),
        "repeated_failed_strategies": len(failed_signatures) - len(set(failed_signatures)),
        "recall_latency_seconds": metrics.latency_ms / 1000,
        "memory_context_bytes": context_bytes,
        "stale_recalls": metrics.stale_memories,
        "irrelevant_recalls": provider.recall_calls if name in {"simple_rename", "isolated_simple_bug"} else 0,
        "total_run_latency_seconds": latency,
        "input_tokens": None, "output_tokens": None,
        "recall_requests": provider.recall_calls,
        "reflect_requests": sum(event.get("reflect_attempted") is True for event in reflection_events)
        if reflect_available else None,
        "reflect_latency_seconds": sum(event.get("reflect_latency_ms", 0) for event in reflection_events) / 1000
        if reflect_available else None,
        "latest_checks_passed": bool(latest) and all(check.get("ok") is True for check in latest.values()),
    }


if __name__ == "__main__":
    result = run_scenario()
    Path(os.environ["AGENT47_EVAL_METRICS"]).write_text(
        json.dumps(result, allow_nan=False), encoding="utf-8",
    )
