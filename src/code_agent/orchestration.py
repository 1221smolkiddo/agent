from __future__ import annotations

import concurrent.futures
import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal


AgentRisk = Literal["low", "medium", "high", "critical"]


@dataclass(frozen=True)
class AgentProfile:
    name: str
    description: str
    system_prompt: str
    capabilities: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    permissions: tuple[str, ...] = ()
    model_profile: str = "default"
    token_budget: int = 8_000
    execution_budget: int = 12
    timeout_seconds: float = 120.0
    risk_profile: AgentRisk = "medium"
    verification_policy: tuple[str, ...] = ()


class AgentCatalog:
    def __init__(self, profiles: list[AgentProfile] | None = None) -> None:
        self._profiles = {profile.name: profile for profile in profiles or builtin_agent_profiles()}

    def register(self, profile: AgentProfile, *, replace: bool = False) -> None:
        if profile.name in self._profiles and not replace:
            raise ValueError(f"Agent profile already registered: {profile.name}")
        if profile.token_budget < 1 or profile.execution_budget < 1 or profile.timeout_seconds <= 0:
            raise ValueError(f"Agent profile {profile.name} has invalid budgets.")
        self._profiles[profile.name] = profile

    def get(self, name: str) -> AgentProfile | None:
        return self._profiles.get(name)

    def unregister(self, name: str) -> bool:
        return self._profiles.pop(name, None) is not None

    def discover(self) -> list[AgentProfile]:
        return [self._profiles[name] for name in sorted(self._profiles)]

    def select(self, task: str) -> AgentProfile:
        lowered = task.lower()
        ranked = sorted(
            self._profiles.values(),
            key=lambda profile: sum(capability.lower() in lowered for capability in profile.capabilities),
            reverse=True,
        )
        return ranked[0] if ranked else builtin_agent_profiles()[0]


@dataclass(frozen=True)
class SubagentRequest:
    task: str
    agent: str
    context: dict[str, Any] = field(default_factory=dict)
    allowed_tools: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    token_budget: int | None = None
    execution_budget: int | None = None
    timeout_seconds: float | None = None
    parent_id: str | None = None


@dataclass(frozen=True)
class SubagentResult:
    id: str
    agent: str
    ok: bool
    summary: str
    output: dict[str, Any]
    elapsed_ms: float
    error: str = ""


SubagentRunner = Callable[[SubagentRequest, AgentProfile, threading.Event], dict[str, Any] | str]


class SubagentManager:
    """In-process isolation boundary for contexts, budgets, failures, and cancellation."""

    def __init__(self, catalog: AgentCatalog, runner: SubagentRunner, *, max_workers: int = 4) -> None:
        self.catalog = catalog
        self.runner = runner
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="agent47-subagent")
        self._cancellations: dict[str, threading.Event] = {}
        self._lock = threading.RLock()

    def spawn(self, request: SubagentRequest) -> concurrent.futures.Future[SubagentResult]:
        profile = self.catalog.get(request.agent)
        if profile is None:
            raise ValueError(f"Unknown agent profile: {request.agent}")
        child_id = f"sub-{uuid.uuid4().hex[:12]}"
        cancellation = threading.Event()
        with self._lock:
            self._cancellations[child_id] = cancellation
        isolated = SubagentRequest(
            task=request.task,
            agent=request.agent,
            context=json.loads(json.dumps(request.context)),
            allowed_tools=tuple(set(request.allowed_tools or profile.allowed_tools) & set(profile.allowed_tools)),
            permissions=tuple(set(request.permissions or profile.permissions) & set(profile.permissions)),
            token_budget=min(request.token_budget or profile.token_budget, profile.token_budget),
            execution_budget=min(request.execution_budget or profile.execution_budget, profile.execution_budget),
            timeout_seconds=min(request.timeout_seconds or profile.timeout_seconds, profile.timeout_seconds),
            parent_id=request.parent_id,
        )
        return self.executor.submit(self._execute, child_id, isolated, profile, cancellation)

    def _execute(self, child_id: str, request: SubagentRequest, profile: AgentProfile, cancellation: threading.Event) -> SubagentResult:
        started = time.perf_counter()
        try:
            value = self.runner(request, profile, cancellation)
            output = value if isinstance(value, dict) else {"message": str(value)}
            summary = str(output.get("summary") or output.get("message") or "completed")
            return SubagentResult(child_id, profile.name, True, summary[:2000], output, round((time.perf_counter() - started) * 1000, 2))
        except Exception as exc:
            return SubagentResult(child_id, profile.name, False, "subagent failed", {}, round((time.perf_counter() - started) * 1000, 2), f"{type(exc).__name__}: {exc}")
        finally:
            with self._lock:
                self._cancellations.pop(child_id, None)

    def cancel(self, child_id: str) -> bool:
        with self._lock:
            event = self._cancellations.get(child_id)
        if event is None:
            return False
        event.set()
        return True

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)


@dataclass(frozen=True)
class OrchestrationTask:
    id: str
    task: str
    depends_on: tuple[str, ...] = ()
    preferred_agent: str | None = None


class MultiAgentOrchestrator:
    def __init__(self, catalog: AgentCatalog, manager: SubagentManager) -> None:
        self.catalog = catalog
        self.manager = manager

    def execute(self, tasks: list[OrchestrationTask], *, parent_id: str | None = None) -> dict[str, SubagentResult]:
        _validate_graph(tasks)
        pending = {task.id: task for task in tasks}
        completed: dict[str, SubagentResult] = {}
        while pending:
            ready = [task for task in pending.values() if all(dep in completed and completed[dep].ok for dep in task.depends_on)]
            if not ready:
                blocked = ", ".join(sorted(pending))
                raise RuntimeError(f"Orchestration cannot make progress; blocked tasks: {blocked}")
            futures: dict[str, concurrent.futures.Future[SubagentResult]] = {}
            for task in ready:
                profile = self.catalog.get(task.preferred_agent or "") or self.catalog.select(task.task)
                futures[task.id] = self.manager.spawn(SubagentRequest(task.task, profile.name, parent_id=parent_id))
            for task_id, future in futures.items():
                completed[task_id] = future.result()
                del pending[task_id]
        return completed


def builtin_agent_profiles() -> list[AgentProfile]:
    common_tools = ("core.read_file", "core.search", "core.repo_map", "core.rank_context")
    return [
        AgentProfile("planner", "Decomposes complex work", "Create dependency-aware plans and acceptance gates.", ("plan", "decompose", "architecture"), common_tools, token_budget=10000),
        AgentProfile("researcher", "Collects focused evidence", "Investigate without mutating the workspace.", ("research", "investigate", "explain"), common_tools, risk_profile="low"),
        AgentProfile("coder", "Implements scoped changes", "Make minimal reviewable edits and verify them.", ("implement", "fix", "code"), common_tools + ("core.apply_patch", "core.run_shell"), ("file.write", "shell.execute")),
        AgentProfile("reviewer", "Reviews correctness and regressions", "Review diffs and report only actionable findings.", ("review", "bug", "regression"), common_tools + ("core.inspect_git_diff",), risk_profile="low"),
        AgentProfile("tester", "Designs and runs tests", "Generate focused tests and verify observable behavior.", ("test", "coverage", "verify"), common_tools + ("core.apply_patch", "core.run_shell"), ("file.write", "shell.execute")),
        AgentProfile("security-auditor", "Audits security boundaries", "Trace trust boundaries and validate exploitability.", ("security", "vulnerability", "threat"), common_tools + ("core.inspect_git_diff",), risk_profile="high"),
        AgentProfile("documentation-writer", "Writes code-grounded docs", "Derive concise documentation from implementation.", ("documentation", "readme", "docs"), common_tools + ("core.apply_patch",), ("file.write",), risk_profile="low"),
        AgentProfile("performance-optimizer", "Measures and improves performance", "Benchmark before optimizing and preserve behavior.", ("performance", "optimize", "benchmark"), common_tools + ("core.run_shell", "core.apply_patch"), ("file.write", "shell.execute"), risk_profile="high"),
        AgentProfile("refactoring-expert", "Performs behavior-preserving refactors", "Keep refactors incremental and test-backed.", ("refactor", "cleanup", "architecture"), common_tools + ("core.apply_patch", "core.run_shell"), ("file.write", "shell.execute")),
        AgentProfile("dependency-analyzer", "Analyzes dependency impact", "Inspect manifests, graph risk, and upgrade blast radius.", ("dependency", "upgrade", "supply chain"), common_tools + ("core.dependency_graph",), risk_profile="medium"),
    ]


def _validate_graph(tasks: list[OrchestrationTask]) -> None:
    ids = {task.id for task in tasks}
    if len(ids) != len(tasks):
        raise ValueError("Orchestration task IDs must be unique.")
    for task in tasks:
        unknown = set(task.depends_on) - ids
        if unknown:
            raise ValueError(f"Task {task.id} has unknown dependencies: {', '.join(sorted(unknown))}")
    visiting: set[str] = set()
    visited: set[str] = set()
    by_id = {task.id: task for task in tasks}

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise ValueError("Orchestration dependency graph contains a cycle.")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in by_id[task_id].depends_on:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in ids:
        visit(task_id)
