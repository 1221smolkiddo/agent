from __future__ import annotations

import subprocess
from dataclasses import replace

import pytest

from code_agent.experience_memory.contracts import MemoryProvenance, RecalledMemory
from code_agent.experience_memory.scope import RepositoryScope, repository_scope
from code_agent.experience_memory.staleness import StalenessGuard, StalenessState


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True,
        text=True, timeout=5,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Agent47 Test")
    git(tmp_path, "config", "user.email", "agent47@example.test")
    (tmp_path / "parser.py").write_text("value = 1\n", encoding="utf-8")
    git(tmp_path, "add", "parser.py")
    git(tmp_path, "commit", "-qm", "initial parser")
    return tmp_path


def memory(head: str, *, paths=("parser.py",)):
    return RecalledMemory(
        text="A historical parser fix.",
        provenance=MemoryProvenance(head=head, changed_paths=paths),
    )


def test_same_head_is_current(repo):
    scope = repository_scope(repo)
    assert StalenessGuard(repo, scope).assess(memory(scope.head)).state == StalenessState.CURRENT


def test_ancestor_with_unchanged_relevant_path_is_likely_current(repo):
    old = repository_scope(repo).head
    (repo / "unrelated.py").write_text("other = 2\n", encoding="utf-8")
    git(repo, "add", "unrelated.py")
    git(repo, "commit", "-qm", "unrelated change")
    scope = repository_scope(repo)
    assert StalenessGuard(repo, scope).assess(memory(old)).state == StalenessState.LIKELY_CURRENT


def test_changed_path_is_stale(repo):
    old = repository_scope(repo).head
    (repo / "parser.py").write_text("value = 2\n", encoding="utf-8")
    git(repo, "add", "parser.py")
    git(repo, "commit", "-qm", "change parser")
    scope = repository_scope(repo)
    assessment = StalenessGuard(repo, scope).assess(memory(old))
    assert assessment.state == StalenessState.STALE
    assert assessment.reason == "relevant_path_changed"


def test_uncommitted_change_is_stale_even_at_same_head(repo):
    scope = repository_scope(repo)
    (repo / "parser.py").write_text("value = 3\n", encoding="utf-8")
    assert StalenessGuard(repo, scope).assess(memory(scope.head)).state == StalenessState.STALE


def test_unavailable_history_and_missing_paths_are_unknown(repo):
    old = repository_scope(repo).head
    (repo / "unrelated.py").write_text("other = 2\n", encoding="utf-8")
    git(repo, "add", "unrelated.py")
    git(repo, "commit", "-qm", "unrelated change")
    guard = StalenessGuard(repo, repository_scope(repo))
    assert guard.assess(memory("f" * 40)).state == StalenessState.UNKNOWN
    assert guard.assess(memory(old, paths=())).state == StalenessState.UNKNOWN


def test_repository_mismatch_is_stale(repo):
    scope = repository_scope(repo)
    foreign = RepositoryScope("agent47-repo-" + "b" * 64, scope.branch, scope.head)
    item = RecalledMemory("wrong repo", provenance=MemoryProvenance(
        repository_bank_id=foreign.bank_id, head=scope.head,
    ))
    assert StalenessGuard(repo, scope).assess(item).state == StalenessState.STALE


def test_observation_mixed_source_provenance_is_honest(repo):
    old = repository_scope(repo).head
    (repo / "parser.py").write_text("value = 2\n", encoding="utf-8")
    git(repo, "add", "parser.py")
    git(repo, "commit", "-qm", "change parser")
    scope = repository_scope(repo)
    current = MemoryProvenance(memory_id="now", head=scope.head)
    stale = MemoryProvenance(memory_id="before", head=old, changed_paths=("parser.py",))
    observation = RecalledMemory(
        "mixed observation", memory_type="observation",
        source_fact_ids=("now", "before"), source_facts=(current, stale),
    )
    guard = StalenessGuard(repo, scope)
    assert guard.assess(observation).state == StalenessState.STALE
    assert guard.assess(replace(observation, source_facts=(current,))).state == StalenessState.UNKNOWN
    assert guard.assess(replace(observation, source_fact_ids=())).state == StalenessState.UNKNOWN


def test_same_head_without_paths_is_unknown_when_worktree_is_dirty(repo):
    scope = repository_scope(repo)
    (repo / "new_parser.py").write_text("new = True\n", encoding="utf-8")
    assessment = StalenessGuard(repo, scope).assess(memory(scope.head, paths=()))
    assert assessment.state == StalenessState.UNKNOWN
    assert assessment.reason == "relevant_paths_missing_dirty_tree"
