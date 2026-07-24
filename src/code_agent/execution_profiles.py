from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Complexity classification
# ---------------------------------------------------------------------------

class ExecutionComplexity(str, Enum):
    """Tri-level task complexity used to select an execution profile."""

    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


# ---------------------------------------------------------------------------
# Task intent – normalized request metadata fed into the assessor
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TaskIntent:
    """Normalized goal metadata passed from the caller into the runtime.

    The caller (agent, CLI) constructs a *TaskIntent* from whatever
    context it has; the runtime owns all downstream policy decisions.
    """

    goal: str
    file_hints: tuple[str, ...] = ()
    symbol_hints: tuple[str, ...] = ()
    workspace_file_count: int = 0
    recent_diff_file_count: int = 0
    expected_tool_kinds: tuple[str, ...] = ()
    tests_required: bool = False
    build_required: bool = False
    explicit_complexity: ExecutionComplexity | None = None


# ---------------------------------------------------------------------------
# Strongly typed policy objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlanningPolicy:
    """Controls planner decomposition depth and replanning behaviour."""

    depth: int = 3
    replanning_mode: str = "limited"          # disabled | limited | autonomous
    allow_graph_mutations: bool = True
    max_graph_size: int = 50

    def __post_init__(self) -> None:
        if self.depth < 0:
            raise ValueError("Planning depth must be non-negative.")
        if self.replanning_mode not in {"disabled", "limited", "autonomous"}:
            raise ValueError(f"Invalid replanning mode: {self.replanning_mode}")
        if self.max_graph_size < 1:
            raise ValueError("Maximum graph size must be at least 1.")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BudgetPolicy:
    """Strongly typed execution budget limits."""

    execution_tokens: float = 50_000
    planning_tokens: float = 10_000
    tool_budget: int = 30
    shell_budget: int = 10
    verification_budget: int = 5
    retries: int = 2
    max_graph_size: int = 50

    def __post_init__(self) -> None:
        for name in (
            "execution_tokens", "planning_tokens", "tool_budget",
            "shell_budget", "verification_budget", "retries", "max_graph_size",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"Budget value {name} must be non-negative.")

    def to_engine_budgets(self) -> dict[str, float]:
        """Convert to the flat budget dict the durable engine expects."""
        return {
            "tokens": float(self.execution_tokens),
            "tool_calls": float(self.tool_budget),
            "shell_commands": float(self.shell_budget),
            "retries": float(self.retries),
        }

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolPolicy:
    """Controls tool execution concurrency and limits."""

    max_fanout: int = 4
    concurrency: int = 4
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        if self.max_fanout < 1:
            raise ValueError("Tool fan-out must be at least 1.")
        if self.concurrency < 1:
            raise ValueError("Tool concurrency must be at least 1.")
        if self.timeout_seconds <= 0:
            raise ValueError("Tool timeout must be positive.")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VerificationPolicy:
    """Controls verification rigor and test requirements."""

    mode: str = "standard"                    # minimal | standard | full
    require_test_pass: bool = False
    max_verification_retries: int = 1

    def __post_init__(self) -> None:
        if self.mode not in {"minimal", "standard", "full"}:
            raise ValueError(f"Invalid verification mode: {self.mode}")
        if self.max_verification_retries < 0:
            raise ValueError("Verification retries must be non-negative.")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Execution profile – composed of the four policy objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExecutionProfile:
    """Immutable composed profile configuring the execution engine."""

    complexity: ExecutionComplexity
    planning: PlanningPolicy
    budgets: BudgetPolicy
    tools: ToolPolicy
    verification: VerificationPolicy
    scheduling_policy: str = "priority"       # priority | dependency_aware | critical_path

    def display_summary(self) -> str:
        lines = [
            f"Execution Profile: {self.complexity.value.upper()}",
            f"  Planner Depth: {'Unlimited' if self.planning.depth == 0 else self.planning.depth}",
            f"  Replanning: {self.planning.replanning_mode.title()}",
            f"  Verification: {self.verification.mode.title()}",
            f"  Budget: {self.budgets.execution_tokens:,.0f} tokens",
            f"  Tool Budget: {self.budgets.tool_budget}",
            f"  Concurrency: {self.tools.concurrency}",
            f"  Scheduling: {self.scheduling_policy}",
        ]
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "complexity": self.complexity.value,
            "planning": self.planning.as_dict(),
            "budgets": self.budgets.as_dict(),
            "tools": self.tools.as_dict(),
            "verification": self.verification.as_dict(),
            "scheduling_policy": self.scheduling_policy,
        }


# ---------------------------------------------------------------------------
# Complexity assessor – objective signal scoring
# ---------------------------------------------------------------------------

# Weights for each signal dimension
_SIGNAL_WEIGHTS: dict[str, float] = {
    "prompt": 0.15,
    "symbols": 0.25,
    "dependency_radius": 0.30,
    "tools": 0.15,
    "verification": 0.15,
}

# Thresholds mapping composite score → complexity level
_SIMPLE_THRESHOLD = 0.35
_COMPLEX_THRESHOLD = 0.70


@dataclass
class ComplexitySignals:
    """Raw scores for each assessment dimension (0.0–1.0)."""

    prompt: float = 0.0
    symbols: float = 0.0
    dependency_radius: float = 0.0
    tools: float = 0.0
    verification: float = 0.0

    @property
    def composite(self) -> float:
        total = sum(
            getattr(self, name) * weight
            for name, weight in _SIGNAL_WEIGHTS.items()
        )
        return min(1.0, max(0.0, total))


class ComplexityAssessor:
    """Classify task complexity using objective, scored signals.

    This lives inside the runtime layer; agent.py does not call it directly.
    """

    def __init__(
        self,
        *,
        simple_threshold: float = _SIMPLE_THRESHOLD,
        complex_threshold: float = _COMPLEX_THRESHOLD,
    ) -> None:
        self.simple_threshold = simple_threshold
        self.complex_threshold = complex_threshold

    def assess(self, intent: TaskIntent) -> tuple[ExecutionComplexity, ComplexitySignals]:
        """Return classified complexity and the raw signal scores."""
        if intent.explicit_complexity is not None:
            # Honour explicit override from CLI or user, but still compute signals.
            signals = self._score(intent)
            return intent.explicit_complexity, signals
        signals = self._score(intent)
        return self._classify(signals.composite), signals

    # -- signal scoring -------------------------------------------------------

    def _score(self, intent: TaskIntent) -> ComplexitySignals:
        return ComplexitySignals(
            prompt=self._score_prompt(intent),
            symbols=self._score_symbols(intent),
            dependency_radius=self._score_dependency_radius(intent),
            tools=self._score_tools(intent),
            verification=self._score_verification(intent),
        )

    @staticmethod
    def _score_prompt(intent: TaskIntent) -> float:
        """Score prompt structural complexity (length, multi-step indicators)."""
        goal = intent.goal
        length_score = min(1.0, len(goal) / 600)

        # Multi-step structure indicators: numbered lists, bullet points, "then",
        # semicolons separating clauses, "and" joining imperative verbs.
        step_markers = len(re.findall(
            r"(?:^\s*\d+[\.\)]\s|^\s*[-*]\s|;\s|\bthen\b|\bafter that\b|\bfinally\b|\balso\b)",
            goal,
            re.MULTILINE | re.IGNORECASE,
        ))
        structure_score = min(1.0, step_markers / 4)

        # Sentence/clause count as a proxy for instruction density.
        sentence_count = max(1, len(re.split(r"[.!?;]\s", goal)))
        density_score = min(1.0, sentence_count / 6)

        return min(1.0, 0.35 * length_score + 0.35 * structure_score + 0.30 * density_score)

    @staticmethod
    def _score_symbols(intent: TaskIntent) -> float:
        """Score from referenced symbols, file hints, and workspace size."""
        symbol_count = len(intent.symbol_hints)
        file_count = len(intent.file_hints)
        workspace_scale = min(1.0, intent.workspace_file_count / 200)

        symbol_score = min(1.0, symbol_count / 4)
        file_score = min(1.0, file_count / 4)

        if intent.workspace_file_count > 0:
            return min(1.0, 0.40 * symbol_score + 0.35 * file_score + 0.25 * workspace_scale)
        return min(1.0, 0.50 * symbol_score + 0.50 * file_score)

    @staticmethod
    def _score_dependency_radius(intent: TaskIntent) -> float:
        """Score based on how many files the change might touch."""
        file_hint_count = len(intent.file_hints)
        diff_count = intent.recent_diff_file_count

        # Distinct directories touched hint at cross-module scope.
        unique_dirs = len({
            f.rsplit("/", 1)[0] if "/" in f else f.rsplit("\\", 1)[0] if "\\" in f else "."
            for f in intent.file_hints
        }) if intent.file_hints else 0

        radius = max(file_hint_count, diff_count, unique_dirs * 1.5)
        return min(1.0, radius / 4)

    @staticmethod
    def _score_tools(intent: TaskIntent) -> float:
        """Score expected tool diversity."""
        tool_count = len(intent.expected_tool_kinds)
        has_shell = any(
            kind in {"shell", "run_command", "terminal"} for kind in intent.expected_tool_kinds
        )
        return min(1.0, (tool_count / 6) + (0.2 if has_shell else 0.0))

    @staticmethod
    def _score_verification(intent: TaskIntent) -> float:
        """Score expected verification burden."""
        score = 0.0
        if intent.tests_required:
            score += 0.6
        if intent.build_required:
            score += 0.4
        return min(1.0, score)

    def _classify(self, composite: float) -> ExecutionComplexity:
        if composite < self.simple_threshold:
            return ExecutionComplexity.SIMPLE
        if composite >= self.complex_threshold:
            return ExecutionComplexity.COMPLEX
        return ExecutionComplexity.MEDIUM


# ---------------------------------------------------------------------------
# Preset profiles
# ---------------------------------------------------------------------------

SIMPLE_PROFILE = ExecutionProfile(
    complexity=ExecutionComplexity.SIMPLE,
    planning=PlanningPolicy(depth=1, replanning_mode="disabled", allow_graph_mutations=False, max_graph_size=5),
    budgets=BudgetPolicy(
        execution_tokens=5_000, planning_tokens=1_000, tool_budget=8,
        shell_budget=3, verification_budget=1, retries=0, max_graph_size=5,
    ),
    tools=ToolPolicy(max_fanout=1, concurrency=1, timeout_seconds=120.0),
    verification=VerificationPolicy(mode="minimal", require_test_pass=False, max_verification_retries=0),
    scheduling_policy="priority",
)

MEDIUM_PROFILE = ExecutionProfile(
    complexity=ExecutionComplexity.MEDIUM,
    planning=PlanningPolicy(depth=3, replanning_mode="limited", allow_graph_mutations=True, max_graph_size=25),
    budgets=BudgetPolicy(
        execution_tokens=50_000, planning_tokens=10_000, tool_budget=30,
        shell_budget=10, verification_budget=5, retries=2, max_graph_size=25,
    ),
    tools=ToolPolicy(max_fanout=4, concurrency=4, timeout_seconds=300.0),
    verification=VerificationPolicy(mode="standard", require_test_pass=True, max_verification_retries=1),
    scheduling_policy="dependency_aware",
)

COMPLEX_PROFILE = ExecutionProfile(
    complexity=ExecutionComplexity.COMPLEX,
    planning=PlanningPolicy(depth=0, replanning_mode="autonomous", allow_graph_mutations=True, max_graph_size=100),
    budgets=BudgetPolicy(
        execution_tokens=100_000, planning_tokens=25_000, tool_budget=80,
        shell_budget=30, verification_budget=15, retries=4, max_graph_size=100,
    ),
    tools=ToolPolicy(max_fanout=8, concurrency=8, timeout_seconds=600.0),
    verification=VerificationPolicy(mode="full", require_test_pass=True, max_verification_retries=3),
    scheduling_policy="critical_path",
)

_PRESETS: dict[ExecutionComplexity, ExecutionProfile] = {
    ExecutionComplexity.SIMPLE: SIMPLE_PROFILE,
    ExecutionComplexity.MEDIUM: MEDIUM_PROFILE,
    ExecutionComplexity.COMPLEX: COMPLEX_PROFILE,
}


# ---------------------------------------------------------------------------
# Policy selector – maps complexity to a profile with override support
# ---------------------------------------------------------------------------

class ExecutionPolicySelector:
    """Select and optionally override an execution profile for a given complexity level."""

    def __init__(
        self,
        *,
        presets: dict[ExecutionComplexity, ExecutionProfile] | None = None,
        assessor: ComplexityAssessor | None = None,
    ) -> None:
        self.presets = dict(presets or _PRESETS)
        self.assessor = assessor or ComplexityAssessor()

    def select(
        self,
        intent: TaskIntent,
        *,
        overrides: dict[str, Any] | None = None,
    ) -> tuple[ExecutionProfile, ComplexitySignals]:
        """Assess, select preset, apply overrides, return profile and signals."""
        complexity, signals = self.assessor.assess(intent)
        profile = self.presets[complexity]

        if overrides:
            profile = self._apply_overrides(profile, overrides)

        return profile, signals

    @staticmethod
    def _apply_overrides(
        profile: ExecutionProfile, overrides: dict[str, Any]
    ) -> ExecutionProfile:
        """Merge caller-supplied overrides into a profile without mutating the preset."""
        planning_overrides = overrides.get("planning", {})
        budget_overrides = overrides.get("budgets", {})
        tool_overrides = overrides.get("tools", {})
        verification_overrides = overrides.get("verification", {})

        return ExecutionProfile(
            complexity=profile.complexity,
            planning=PlanningPolicy(**{**asdict(profile.planning), **planning_overrides}),
            budgets=BudgetPolicy(**{**asdict(profile.budgets), **budget_overrides}),
            tools=ToolPolicy(**{**asdict(profile.tools), **tool_overrides}),
            verification=VerificationPolicy(**{**asdict(profile.verification), **verification_overrides}),
            scheduling_policy=overrides.get("scheduling_policy", profile.scheduling_policy),
        )

    def for_complexity(self, complexity: ExecutionComplexity) -> ExecutionProfile:
        """Return the preset profile for a given complexity level."""
        return self.presets[complexity]


# ---------------------------------------------------------------------------
# Profile escalation – runtime-driven upgrade without state reset
# ---------------------------------------------------------------------------

@dataclass
class EscalationTrigger:
    """Describes why an escalation happened."""

    reason: str
    affected_files: int = 0
    test_failures: int = 0
    graph_growth: int = 0
    budget_pressure: float = 0.0


class ProfileEscalator:
    """Evaluate whether the current profile should be escalated.

    Called during execution cycles to detect when a SIMPLE run has grown
    beyond its profile's bounds.
    """

    def __init__(
        self,
        *,
        file_threshold: int = 3,
        test_failure_threshold: int = 1,
        graph_growth_threshold: int = 5,
        budget_pressure_threshold: float = 0.85,
    ) -> None:
        self.file_threshold = file_threshold
        self.test_failure_threshold = test_failure_threshold
        self.graph_growth_threshold = graph_growth_threshold
        self.budget_pressure_threshold = budget_pressure_threshold

    def evaluate(
        self,
        current: ExecutionComplexity,
        *,
        affected_files: int = 0,
        test_failures: int = 0,
        graph_size: int = 0,
        budget_utilization: float = 0.0,
    ) -> tuple[ExecutionComplexity | None, EscalationTrigger | None]:
        """Return the escalated level and trigger, or (None, None) if no escalation is needed."""
        if current == ExecutionComplexity.COMPLEX:
            return None, None

        reasons: list[str] = []
        if affected_files >= self.file_threshold:
            reasons.append(f"affected {affected_files} files (threshold={self.file_threshold})")
        if test_failures >= self.test_failure_threshold:
            reasons.append(f"{test_failures} test failure(s)")
        if graph_size >= self.graph_growth_threshold and current == ExecutionComplexity.SIMPLE:
            reasons.append(f"graph has {graph_size} nodes")
        if budget_utilization >= self.budget_pressure_threshold:
            reasons.append(f"budget utilization {budget_utilization:.0%}")

        if not reasons:
            return None, None

        target = ExecutionComplexity.COMPLEX if current == ExecutionComplexity.MEDIUM else ExecutionComplexity.MEDIUM
        # Escalate directly to COMPLEX if multiple severe signals fire.
        if current == ExecutionComplexity.SIMPLE and len(reasons) >= 3:
            target = ExecutionComplexity.COMPLEX

        trigger = EscalationTrigger(
            reason="; ".join(reasons),
            affected_files=affected_files,
            test_failures=test_failures,
            graph_growth=graph_size,
            budget_pressure=budget_utilization,
        )
        return target, trigger


# ---------------------------------------------------------------------------
# Execution statistics – recorded at completion for future feedback
# ---------------------------------------------------------------------------

@dataclass
class ExecutionStatistics:
    """Captures estimated vs. actual complexity and runtime metrics."""

    estimated_complexity: str = ""
    actual_complexity: str = ""
    composite_score: float = 0.0
    files_changed: int = 0
    planner_iterations: int = 0
    verification_failures: int = 0
    wall_seconds: float = 0.0
    escalations: list[dict[str, Any]] = field(default_factory=list)
    outcome: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
