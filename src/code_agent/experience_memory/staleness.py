"""Conservative Git provenance checks for advisory historical memory."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .contracts import MemoryProvenance, RecalledMemory
from .scope import RepositoryScope, _git

_HEAD = re.compile(r"[0-9a-f]{40,64}\Z")
_PATH = re.compile(r"[A-Za-z0-9_./-]{1,160}\Z")


class StalenessState(StrEnum):
    CURRENT = "current"
    LIKELY_CURRENT = "likely_current"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class StalenessAssessment:
    state: StalenessState
    reason: str


class StalenessGuard:
    def __init__(self, workspace: Path, scope: RepositoryScope) -> None:
        self.workspace = workspace
        self.scope = scope

    def assess(self, memory: RecalledMemory) -> StalenessAssessment:
        if memory.memory_type == "observation":
            if not memory.source_fact_ids:
                return StalenessAssessment(StalenessState.UNKNOWN, "observation_sources_unavailable")
            facts = {fact.memory_id: fact for fact in memory.source_facts}
            if any(source_id not in facts for source_id in memory.source_fact_ids):
                return StalenessAssessment(StalenessState.UNKNOWN, "observation_sources_incomplete")
            assessments = [self._assess_provenance(facts[source_id])
                           for source_id in memory.source_fact_ids]
            states = {item.state for item in assessments}
            if StalenessState.STALE in states:
                return StalenessAssessment(StalenessState.STALE, "source_fact_path_changed")
            if StalenessState.UNKNOWN in states:
                return StalenessAssessment(StalenessState.UNKNOWN, "mixed_or_unknown_sources")
            if len(states) > 1:
                return StalenessAssessment(StalenessState.UNKNOWN, "mixed_source_provenance")
            return StalenessAssessment(states.pop(), "source_facts_checked")
        return self._assess_provenance(memory.provenance)

    def _assess_provenance(self, provenance: MemoryProvenance) -> StalenessAssessment:
        if provenance.repository_bank_id and provenance.repository_bank_id != self.scope.bank_id:
            return StalenessAssessment(StalenessState.STALE, "repository_mismatch")
        head = provenance.head
        if not head or not _HEAD.fullmatch(head) or not self.scope.head:
            return StalenessAssessment(StalenessState.UNKNOWN, "git_provenance_missing")
        paths = tuple(path for path in provenance.changed_paths[:8]
                      if _PATH.fullmatch(path) and not path.startswith("/")
                      and ".." not in Path(path).parts and not path.startswith(":("))
        if provenance.changed_paths and len(paths) != len(provenance.changed_paths[:8]):
            return StalenessAssessment(StalenessState.UNKNOWN, "unsafe_path_provenance")
        if head == self.scope.head:
            if not paths:
                tracked = _git(self.workspace, "diff", "--name-only", head, "--")
                untracked = _git(self.workspace, "ls-files", "--others", "--exclude-standard")
                if tracked is None or untracked is None:
                    return StalenessAssessment(StalenessState.UNKNOWN, "working_tree_unavailable")
                if tracked or untracked:
                    return StalenessAssessment(StalenessState.UNKNOWN, "relevant_paths_missing_dirty_tree")
            if paths:
                changed = self._changed_since(head, paths)
                if changed is None:
                    return StalenessAssessment(StalenessState.UNKNOWN, "git_diff_unavailable")
                if changed:
                    return StalenessAssessment(StalenessState.STALE, "working_tree_path_changed")
            return StalenessAssessment(StalenessState.CURRENT, "same_head")
        if _git(self.workspace, "merge-base", "--is-ancestor", head, self.scope.head) is None:
            return StalenessAssessment(StalenessState.UNKNOWN, "commit_not_in_current_history")
        if not paths:
            return StalenessAssessment(StalenessState.UNKNOWN, "relevant_paths_missing")
        changed = self._changed_since(head, paths)
        if changed is None:
            return StalenessAssessment(StalenessState.UNKNOWN, "git_diff_unavailable")
        if changed:
            return StalenessAssessment(StalenessState.STALE, "relevant_path_changed")
        return StalenessAssessment(StalenessState.LIKELY_CURRENT, "ancestor_paths_unchanged")

    def _changed_since(self, head: str, paths: tuple[str, ...]) -> bool | None:
        committed = _git(self.workspace, "diff", "--name-only", head, self.scope.head or "HEAD", "--", *paths)
        working = _git(self.workspace, "diff", "--name-only", self.scope.head or "HEAD", "--", *paths)
        if committed is None or working is None:
            return None
        return bool(committed or working)
