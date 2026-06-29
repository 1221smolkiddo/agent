from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .safety import redact_secrets

MEMORY_DIR_NAME = "memory"
MEMORY_FILE_NAME = "project.md"
MAX_MEMORY_FILE_CHARS = 60000
MAX_MEMORY_READ_CHARS = 12000
MAX_MEMORY_ENTRY_CHARS = 1000

SECTION_TITLES: dict[str, str] = {
    "project_conventions": "Project Conventions",
    "user_preferences": "User Project Preferences",
    "architecture_notes": "Important Architecture Notes",
    "common_commands": "Common Commands",
    "known_pitfalls": "Known Pitfalls",
    "project_glossary": "Project Glossary",
    "successful_patterns": "Successful Implementation Patterns",
    "verification_strategy": "Verification Strategy",
    "dependencies_integrations": "Dependencies And Integrations",
    "release_notes": "Release And Migration Notes",
}

SECTION_ALIASES = {
    "conventions": "project_conventions",
    "preferences": "user_preferences",
    "architecture": "architecture_notes",
    "commands": "common_commands",
    "pitfalls": "known_pitfalls",
    "glossary": "project_glossary",
    "patterns": "successful_patterns",
    "verification": "verification_strategy",
    "dependencies": "dependencies_integrations",
    "integrations": "dependencies_integrations",
    "release": "release_notes",
    "migrations": "release_notes",
}

SENSITIVE_MEMORY_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|credential)\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
]


@dataclass(frozen=True)
class MemoryUpdate:
    section: str
    content: str


@dataclass(frozen=True)
class MemoryWritePlan:
    path: Path
    before: str
    after: str
    changed: bool
    sections: list[str]

    @property
    def diff(self) -> str:
        return "\n".join(
            difflib.unified_diff(
                self.before.splitlines(),
                self.after.splitlines(),
                fromfile=f"a/.code-agent/{MEMORY_DIR_NAME}/{MEMORY_FILE_NAME}",
                tofile=f"b/.code-agent/{MEMORY_DIR_NAME}/{MEMORY_FILE_NAME}",
                lineterm="",
            )
        )


def memory_file_path(workspace: Path) -> Path:
    return workspace.resolve() / ".code-agent" / MEMORY_DIR_NAME / MEMORY_FILE_NAME


def ensure_memory_dir(workspace: Path) -> Path:
    directory = memory_file_path(workspace).parent
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def read_project_memory(workspace: Path, *, max_chars: int = MAX_MEMORY_READ_CHARS) -> str:
    path = memory_file_path(workspace)
    if not path.exists():
        return _empty_memory_message(path)
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Project memory exists but is not UTF-8 text: {path}"
    redacted = redact_secrets(content)
    if len(redacted) <= max_chars:
        return redacted
    remaining = len(redacted) - max_chars
    return redacted[:max_chars].rstrip() + f"\n<truncated {remaining} chars>"


def build_memory_write_plan(workspace: Path, updates: list[MemoryUpdate]) -> MemoryWritePlan:
    path = memory_file_path(workspace)
    before = path.read_text(encoding="utf-8") if path.exists() else default_memory_document()
    sections = parse_memory_sections(before)
    changed_sections: list[str] = []
    for update in updates:
        section = normalize_section(update.section)
        content = normalize_entry(update.content)
        if not content:
            continue
        reject_sensitive_memory(content)
        items = sections.setdefault(section, [])
        if content.lower() not in {item.lower() for item in items}:
            items.append(content)
            changed_sections.append(section)
    after = render_memory_document(sections)
    if len(after) > MAX_MEMORY_FILE_CHARS:
        raise ValueError(
            f"Project memory would exceed {MAX_MEMORY_FILE_CHARS} characters. "
            "Remove stale entries before adding more."
        )
    return MemoryWritePlan(
        path=path,
        before=before,
        after=after,
        changed=before != after,
        sections=sorted(set(changed_sections), key=list(SECTION_TITLES).index),
    )


def write_memory_plan(plan: MemoryWritePlan) -> None:
    plan.path.parent.mkdir(parents=True, exist_ok=True)
    plan.path.write_text(plan.after, encoding="utf-8")


def default_memory_document() -> str:
    lines = [
        "# Agent47 Project Memory",
        "",
        "Local, human-readable project facts for Agent47. Keep this file secret-free.",
        f"Last updated: {datetime.now(timezone.utc).date().isoformat()}",
        "",
    ]
    for title in SECTION_TITLES.values():
        lines.extend([f"## {title}", "", "- <empty>", ""])
    return "\n".join(lines).rstrip() + "\n"


def parse_memory_sections(content: str) -> dict[str, list[str]]:
    sections = {key: [] for key in SECTION_TITLES}
    current: str | None = None
    title_to_key = {title.lower(): key for key, title in SECTION_TITLES.items()}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            current = title_to_key.get(line[3:].strip().lower())
            continue
        if current is None or not line.startswith("- "):
            continue
        item = line[2:].strip()
        if item and item != "<empty>":
            sections[current].append(item)
    return sections


def render_memory_document(sections: dict[str, list[str]]) -> str:
    lines = [
        "# Agent47 Project Memory",
        "",
        "Local, human-readable project facts for Agent47. Keep this file secret-free.",
        f"Last updated: {datetime.now(timezone.utc).date().isoformat()}",
        "",
    ]
    for key, title in SECTION_TITLES.items():
        items = sections.get(key, [])
        lines.extend([f"## {title}", ""])
        if items:
            for item in items:
                lines.append(f"- {item}")
        else:
            lines.append("- <empty>")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def normalize_section(section: str) -> str:
    normalized = section.strip().lower().replace("-", "_").replace(" ", "_")
    normalized = SECTION_ALIASES.get(normalized, normalized)
    if normalized not in SECTION_TITLES:
        allowed = ", ".join(SECTION_TITLES)
        raise ValueError(f"Unknown memory section: {section}. Expected one of: {allowed}.")
    return normalized


def normalize_entry(content: str) -> str:
    normalized = " ".join(content.strip().split())
    if normalized.startswith("- "):
        normalized = normalized[2:].strip()
    if len(normalized) > MAX_MEMORY_ENTRY_CHARS:
        raise ValueError(f"Memory entry exceeds {MAX_MEMORY_ENTRY_CHARS} characters.")
    return normalized


def reject_sensitive_memory(content: str) -> None:
    for pattern in SENSITIVE_MEMORY_PATTERNS:
        if pattern.search(content):
            raise ValueError("Refusing to store memory that looks like a secret or credential.")


def _empty_memory_message(path: Path) -> str:
    return (
        f"No project memory exists yet at {path}. "
        "Use update_memory with stable, secret-free project facts after user approval."
    )
