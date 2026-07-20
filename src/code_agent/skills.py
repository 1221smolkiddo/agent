from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


RISK_LEVELS = {"low", "medium", "high", "critical"}
SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    instructions: str
    root: Path
    triggers: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    verification_steps: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    required_permissions: tuple[str, ...] = ()
    risk_level: str = "medium"
    prompt_template: str = ""
    source: str = "workspace"

    def validate(self) -> None:
        if not SKILL_NAME.fullmatch(self.name):
            raise ValueError(f"Invalid skill name: {self.name}")
        if not self.description.strip() or not self.instructions.strip():
            raise ValueError(f"Skill {self.name} requires description and instructions.")
        if self.risk_level not in RISK_LEVELS:
            raise ValueError(f"Skill {self.name} has invalid risk level {self.risk_level}.")

    def matches(self, task: str) -> bool:
        lowered = task.lower()
        terms = self.triggers or tuple(_keywords(self.description))
        return any(term.lower() in lowered for term in terms if len(term) >= 3)

    def context(self) -> str:
        lines = [f"Skill `{self.name}` ({self.risk_level} risk):", self.instructions.strip()]
        if self.allowed_tools:
            lines.append("Allowed tools: " + ", ".join(self.allowed_tools))
        if self.verification_steps:
            lines.append("Verification: " + "; ".join(self.verification_steps))
        if self.acceptance_criteria:
            lines.append("Acceptance: " + "; ".join(self.acceptance_criteria))
        return "\n".join(lines)


class SkillCatalog:
    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}

    def register(self, skill: SkillDefinition, *, replace: bool = False) -> None:
        skill.validate()
        if skill.name in self._skills and not replace:
            raise ValueError(f"Skill already registered: {skill.name}")
        self._skills[skill.name] = skill

    def unregister(self, name: str) -> bool:
        return self._skills.pop(name, None) is not None

    def get(self, name: str) -> SkillDefinition | None:
        return self._skills.get(name)

    def discover(self) -> list[SkillDefinition]:
        return [self._skills[name] for name in sorted(self._skills)]

    def match(self, task: str, *, limit: int = 3) -> list[SkillDefinition]:
        return [skill for skill in self.discover() if skill.matches(task)][:limit]

    def load_directory(
        self, root: Path, *, source: str = "workspace", replace: bool = False
    ) -> list[SkillDefinition]:
        loaded: list[SkillDefinition] = []
        if not root.exists():
            return loaded
        for path in sorted(root.glob("*/SKILL.md")):
            skill = load_skill(path, source=source)
            self.register(skill, replace=replace)
            loaded.append(skill)
        return loaded

    @classmethod
    def for_workspace(cls, workspace: Path) -> "SkillCatalog":
        catalog = cls()
        catalog.load_directory(Path(__file__).with_name("builtin_skills"), source="builtin")
        catalog.load_directory(workspace / ".agents" / "skills", source="workspace")
        return catalog


def load_skill(path: Path, *, source: str = "workspace") -> SkillDefinition:
    raw = path.read_text(encoding="utf-8")
    frontmatter, body = _frontmatter(raw)
    metadata_path = path.with_name("skill.json")
    metadata: dict[str, Any] = {}
    if metadata_path.exists():
        parsed = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError(f"Skill metadata must be an object: {metadata_path}")
        metadata = parsed
    skill = SkillDefinition(
        name=str(frontmatter.get("name") or path.parent.name),
        description=str(frontmatter.get("description") or ""),
        instructions=body,
        root=path.parent,
        triggers=_strings(metadata.get("triggers")),
        allowed_tools=_strings(metadata.get("allowed_tools")),
        verification_steps=_strings(metadata.get("verification_steps")),
        acceptance_criteria=_strings(metadata.get("acceptance_criteria")),
        required_permissions=_strings(metadata.get("required_permissions")),
        risk_level=str(metadata.get("risk_level") or "medium"),
        prompt_template=str(metadata.get("prompt_template") or ""),
        source=source,
    )
    skill.validate()
    return skill


def _frontmatter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---\n"):
        raise ValueError("SKILL.md must begin with YAML frontmatter.")
    end = raw.find("\n---\n", 4)
    if end < 0:
        raise ValueError("SKILL.md frontmatter is not terminated.")
    values: dict[str, str] = {}
    for line in raw[4:end].splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError(f"Invalid skill frontmatter line: {line}")
        values[key.strip()] = value.strip().strip('"\'')
    unknown = set(values) - {"name", "description"}
    if unknown:
        raise ValueError("SKILL.md frontmatter only supports name and description: " + ", ".join(sorted(unknown)))
    return values, raw[end + 5 :].strip()


def _strings(value: Any) -> tuple[str, ...]:
    return tuple(str(item) for item in value) if isinstance(value, list) else ()


def _keywords(value: str) -> list[str]:
    ignored = {"and", "for", "the", "with", "when", "use", "from", "into", "that"}
    return [word for word in re.findall(r"[a-z0-9+#.-]+", value.lower()) if word not in ignored]


InstructionScope = Literal[
    "global", "organization", "user", "repository", "workspace", "branch", "directory",
    "file", "language", "tool", "agent", "session", "task",
]


@dataclass(frozen=True)
class ScopedInstruction:
    scope: InstructionScope
    content: str
    source: str
    priority: int
    condition: str = ""


class InstructionResolver:
    ORDER: dict[str, int] = {
        "global": 10, "organization": 20, "user": 30, "repository": 40,
        "workspace": 50, "branch": 60, "directory": 70, "file": 80,
        "language": 90, "tool": 100, "agent": 110, "session": 120, "task": 130,
    }

    def __init__(self, instructions: list[ScopedInstruction] | None = None) -> None:
        self.instructions = list(instructions or [])

    def add(self, scope: InstructionScope, content: str, *, source: str, condition: str = "") -> None:
        self.instructions.append(
            ScopedInstruction(scope, content.strip(), source, self.ORDER[scope], condition)
        )

    def resolve(self, *, task: str = "", path: str = "", language: str = "", tool: str = "", agent: str = "") -> list[ScopedInstruction]:
        context = " ".join([task, path, language, tool, agent]).lower()
        active = [item for item in self.instructions if not item.condition or item.condition.lower() in context]
        return sorted(active, key=lambda item: (item.priority, item.source))

    def render(self, **context: str) -> str:
        active = self.resolve(**context)
        if not active:
            return ""
        return "\n\n".join(
            f"[{item.scope} instructions from {item.source}]\n{item.content}" for item in active
        )

    @classmethod
    def for_workspace(cls, workspace: Path) -> "InstructionResolver":
        resolver = cls()
        candidates = [
            ("repository", workspace / "AGENTS.md"),
            ("workspace", workspace / ".agents" / "instructions.md"),
        ]
        for scope, path in candidates:
            if path.exists():
                resolver.add(scope, path.read_text(encoding="utf-8"), source=str(path))  # type: ignore[arg-type]
        return resolver
