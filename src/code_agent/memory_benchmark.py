"""Measured P6 disabled-path and fresh-process CLI startup observations."""
from __future__ import annotations

import builtins
import gc
import importlib
import json
import statistics
import subprocess
import sys
import tempfile
import warnings
import time
from pathlib import Path
from unittest.mock import patch


def benchmark_disabled(*, iterations: int = 10000, samples: int = 7) -> dict[str, object]:
    if not 1 <= iterations <= 100000 or not 3 <= samples <= 30:
        raise ValueError("Iterations or samples outside bounded benchmark range.")
    from .experience_memory.config import ExperienceMemoryConfig
    from .experience_memory.service import ExperienceMemoryService
    from .experience_memory.recall import MemoryRecallCoordinator
    from .experience_memory.recall_policy import MemoryRecallPolicy

    config = ExperienceMemoryConfig()
    workspace = Path.cwd()
    network_attempts = 0
    sdk_import_attempts = 0
    automatic_recall_calls = 0
    automatic_reflect_calls = 0
    agent_construction = []
    agent_runs = []
    original_import = builtins.__import__
    original_import_module = importlib.import_module

    def deny_network(*args, **kwargs):
        nonlocal network_attempts
        network_attempts += 1
        raise AssertionError("Disabled memory attempted network I/O.")

    def track_import(name, *args, **kwargs):
        nonlocal sdk_import_attempts
        if name.startswith("hindsight_client"):
            sdk_import_attempts += 1
            raise AssertionError("Disabled memory attempted an SDK import.")
        return original_import(name, *args, **kwargs)

    def track_import_module(name, *args, **kwargs):
        nonlocal sdk_import_attempts
        if name.startswith("hindsight_client"):
            sdk_import_attempts += 1
            raise AssertionError("Disabled memory attempted an SDK import.")
        return original_import_module(name, *args, **kwargs)

    def deny_recall(*args, **kwargs):
        nonlocal automatic_recall_calls
        automatic_recall_calls += 1
        raise AssertionError("Disabled agent attempted automatic recall.")

    def deny_reflect(*args, **kwargs):
        nonlocal automatic_reflect_calls
        automatic_reflect_calls += 1
        raise AssertionError("Disabled agent attempted automatic Reflect.")

    with (
        patch("socket.socket.connect", deny_network),
        patch("socket.socket.connect_ex", deny_network),
        patch("socket.getaddrinfo", deny_network),
        patch("socket.create_connection", deny_network),
        patch("builtins.__import__", track_import),
        patch("importlib.import_module", track_import_module),
        patch.object(ExperienceMemoryService, "recall_detailed", deny_recall),
        patch.object(ExperienceMemoryService, "reflect_detailed", deny_reflect, create=True),
    ):
        started = time.perf_counter()
        for _ in range(iterations):
            service = ExperienceMemoryService(config, workspace)
        construction = time.perf_counter() - started
        coordinator = MemoryRecallCoordinator(service)
        started = time.perf_counter()
        for _ in range(iterations):
            context, metrics = coordinator.before_planning("Fix a recurring failure", workspace_task=True)
        policy = time.perf_counter() - started
        for operation in (
            lambda: service.health(), lambda: service.recall("Historical decision"),
            lambda: service.retain("Synthetic engineering lesson."),
            lambda: service.reflect("Historical decision"),
            lambda: service.get_operation("agent47-repo-" + "a" * 64, "0" * 36),
        ):
            operation()
        # Real factory, runtime, storage, preflight and one scripted read-only model
        # response. No provider model calls are made, and credentials are not read.
        from .config import Settings
        from .evals import ScriptedModel
        from .factory import create_agent
        for _ in range(samples):
            with tempfile.TemporaryDirectory(prefix="agent47-disabled-construction-") as temp:
                root = Path(temp)
                (root / "payload.py").write_text("value = 1\n", encoding="utf-8")
                settings = Settings(
                    _env_file=None, agent_provider="openrouter", agent_model="test-model",
                    openrouter_api_key="qualification-placeholder", agent_fallback_models="",
                    agent_model_preset=None, agent_reviewer_pass=False, agent_stream=False,
                    agent_execution_mode="shadow", agent_shadow_planner="deterministic",
                    agent_experience_memory_enabled=False, hindsight_api_key=None,
                    hindsight_base_url=None, agent_experience_memory_provider="none",
                    agent_db_path=root / "runs.db", agent_execution_db_path=root / "execution.db",
                )
                with (patch("code_agent.factory.create_fallback_client", return_value=ScriptedModel([
                    '{"type":"final","message":"Inspection finished."}',
                ])), patch("code_agent.credentials.keyring.CredentialStore.get_provider_key", return_value=None),
                      warnings.catch_warnings()):
                    warnings.simplefilter("ignore", RuntimeWarning)
                    started = time.perf_counter()
                    agent = create_agent(settings, root, None, True, 2, stream_model=False)
                    agent_construction.append(time.perf_counter() - started)
                    try:
                        started = time.perf_counter()
                        agent.run_detailed("Inspect the repository")
                        agent_runs.append(time.perf_counter() - started)
                    finally:
                        agent.tools.close()
                        del agent
                        # SQLite context managers commit but Windows handles may remain
                        # in reference cycles until collection. Cleanup is not timed.
                        gc.collect()
        if any((network_attempts, sdk_import_attempts, automatic_recall_calls, automatic_reflect_calls)):
            raise AssertionError("Disabled qualification observed forbidden memory activity.")
    enabled_policy = MemoryRecallPolicy(ExperienceMemoryConfig(
        enabled=True, deployment="self_hosted", base_url="http://localhost:8888",
    ))
    rename = enabled_policy.decide("Rename the local variable", workspace_task=True)
    isolated = enabled_policy.decide("Fix the isolated simple bug", workspace_task=True)

    cli_path = Path(__file__).with_name("cli.py")
    child = """
import builtins, importlib, json, socket, sys, time, types
from pathlib import Path
network_attempts = 0
sdk_import_attempts = 0
original_import = builtins.__import__
original_import_module = importlib.import_module
def deny_network(*args, **kwargs):
    global network_attempts
    network_attempts += 1
    raise AssertionError("CLI startup attempted network I/O.")
def track_import(name, *args, **kwargs):
    global sdk_import_attempts
    if name.startswith("hindsight_client"):
        sdk_import_attempts += 1
        raise AssertionError("CLI startup attempted an SDK import.")
    return original_import(name, *args, **kwargs)
def track_import_module(name, *args, **kwargs):
    global sdk_import_attempts
    if name.startswith("hindsight_client"):
        sdk_import_attempts += 1
        raise AssertionError("CLI startup attempted an SDK import.")
    return original_import_module(name, *args, **kwargs)
builtins.__import__ = track_import
importlib.import_module = track_import_module
socket.socket.connect = socket.socket.connect_ex = socket.create_connection = socket.getaddrinfo = deny_network
source = Path(sys.argv[1]).read_text(encoding="utf-8")
if sys.argv[2] == "baseline":
    source = source.replace("from .memory_operator import memory_app\\n", "")
    source = source.replace('app.add_typer(memory_app, name="memory")\\n', "")
module = types.ModuleType("code_agent.cli")
module.__file__ = sys.argv[1]
module.__package__ = "code_agent"
sys.modules["code_agent.cli"] = module
start = time.perf_counter()
exec(compile(source, sys.argv[1], "exec"), module.__dict__)
print(json.dumps({"seconds": time.perf_counter()-start, "sdk_loaded": "hindsight_client" in sys.modules, "network_attempts": network_attempts, "sdk_import_attempts": sdk_import_attempts}))
"""
    startup: dict[str, list[float]] = {"baseline": [], "p6": []}
    for index in range(samples):
        arms = ("baseline", "p6") if index % 2 == 0 else ("p6", "baseline")
        for arm in arms:
            completed = subprocess.run(
                [sys.executable, "-c", child, str(cli_path), arm],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, timeout=60,
                text=True,
            )
            measured = json.loads(completed.stdout)
            if measured["sdk_loaded"] or measured["network_attempts"] or measured["sdk_import_attempts"]:
                raise AssertionError("CLI startup imported Hindsight SDK.")
            startup[arm].append(measured["seconds"])
    baseline = statistics.median(startup["baseline"])
    p6 = statistics.median(startup["p6"])
    return {
        "schema_version": 1,
        "environment": {"platform": sys.platform, "python": sys.version.split()[0]},
        "iterations": iterations,
        "construction_mean_microseconds": construction / iterations * 1e6,
        "disabled_recall_mean_microseconds": policy / iterations * 1e6,
        "network_attempts": network_attempts, "sdk_import_attempts": sdk_import_attempts,
        "disabled_context_bytes": len(context.encode()), "disabled_recall_selected": metrics.attempted,
        "trivial_rename_recall_selected": rename.should_recall,
        "isolated_bug_recall_selected": isolated.should_recall,
        "eligibility_model_calls": 0,
        "automatic_recall_calls": automatic_recall_calls,
        "automatic_reflect_calls": automatic_reflect_calls,
        "agent_construction_samples_seconds": agent_construction,
        "agent_run_samples_seconds": agent_runs,
        "agent_construction_median_seconds": statistics.median(agent_construction),
        "agent_construction_min_seconds": min(agent_construction),
        "agent_construction_max_seconds": max(agent_construction),
        "agent_run_median_seconds": statistics.median(agent_runs),
        "agent_run_min_seconds": min(agent_runs),
        "agent_run_max_seconds": max(agent_runs),
        "baseline_startup_min_seconds": min(startup["baseline"]),
        "baseline_startup_max_seconds": max(startup["baseline"]),
        "p6_startup_min_seconds": min(startup["p6"]),
        "p6_startup_max_seconds": max(startup["p6"]),
        "measurement_scope": "Factory/runtime construction and scripted read-only run; no real model request. Fresh CLI child processes guard network and SDK imports.",
        "startup_samples_seconds": startup,
        "baseline_startup_median_seconds": baseline,
        "p6_startup_median_seconds": p6,
        "startup_delta_median_seconds": p6 - baseline,
        "startup_baseline": "current CLI with only P6 import and registration removed",
        "acceptance_threshold": None,
        "conclusion": "Observed timings only; no predeclared startup acceptance threshold.",
    }
