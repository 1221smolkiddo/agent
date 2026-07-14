"""Tests for the reviewer pass module and its integration with CodingAgent."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from code_agent.reviewer import (
    ReviewerPassResult,
    _parse_reviewer_decision,
    reviewer_messages,
    run_reviewer_pass,
)


# ---------------------------------------------------------------------------
# Parsing tests
# ---------------------------------------------------------------------------


def test_parse_reviewer_decision_valid_ok():
    raw = json.dumps({"ok": True, "summary": "Looks good.", "issues": [], "required_actions": []})
    decision = _parse_reviewer_decision(raw)
    assert decision.ok is True
    assert decision.summary == "Looks good."
    assert decision.issues == []
    assert decision.required_actions == []


def test_parse_reviewer_decision_valid_rejected():
    raw = json.dumps({
        "ok": False,
        "summary": "Missing test coverage.",
        "issues": ["no test for edge case"],
        "required_actions": ["add test_edge_case"],
    })
    decision = _parse_reviewer_decision(raw)
    assert decision.ok is False
    assert "Missing test coverage" in decision.summary
    assert len(decision.issues) == 1
    assert len(decision.required_actions) == 1


def test_parse_reviewer_decision_wrapped_in_prose():
    """Parser should extract JSON even when the model wraps it in markdown or prose."""
    raw = (
        "Here is my review:\n"
        '```json\n'
        '{"ok": true, "summary": "All clear."}\n'
        '```\n'
    )
    decision = _parse_reviewer_decision(raw)
    assert decision.ok is True
    assert decision.summary == "All clear."


def test_parse_reviewer_decision_no_json_raises():
    with pytest.raises((json.JSONDecodeError, ValueError)):
        _parse_reviewer_decision("This response has no JSON at all.")


def test_parse_reviewer_decision_defaults():
    """Missing optional fields should get sensible defaults."""
    raw = json.dumps({"ok": True, "summary": ""})
    decision = _parse_reviewer_decision(raw)
    assert decision.ok is True
    assert decision.summary == ""
    assert decision.issues == []
    assert decision.required_actions == []


# ---------------------------------------------------------------------------
# ReviewerPassResult record
# ---------------------------------------------------------------------------


def test_reviewer_pass_result_as_record():
    result = ReviewerPassResult(
        ok=False,
        summary="Bug found.",
        issues=["off-by-one"],
        required_actions=["fix loop bound"],
        raw='{"ok": false, "summary": "Bug found.", "issues": ["off-by-one"], "required_actions": ["fix loop bound"]}',
    )
    record = result.as_record()
    assert record["ok"] is False
    assert record["summary"] == "Bug found."
    assert "off-by-one" in record["issues"]
    assert record["error"] is None


def test_reviewer_pass_result_error_record():
    result = ReviewerPassResult(
        ok=True,
        summary="Reviewer unavailable.",
        error="ConnectionError: timeout",
    )
    record = result.as_record()
    assert record["ok"] is True
    assert record["error"] == "ConnectionError: timeout"


# ---------------------------------------------------------------------------
# Reviewer messages construction
# ---------------------------------------------------------------------------


def test_reviewer_messages_structure():
    messages = reviewer_messages(
        task="fix the calculator",
        final_message="Fixed the add function.",
        changed_paths=["calculator.py"],
        mutation_records=[{"action": "edit_file", "path": "calculator.py", "ok": True}],
        command_records=[{"command": "python -m pytest", "status": "passed", "ok": True}],
        verification_results=[{"purpose": "test", "command": "python -m pytest", "ok": True, "status": "passed"}],
    )
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "reviewer pass" in messages[0]["content"].lower()
    assert messages[1]["role"] == "user"

    payload = json.loads(messages[1]["content"])
    assert payload["task"] == "fix the calculator"
    assert payload["final_message"] == "Fixed the add function."
    assert payload["changed_paths"] == ["calculator.py"]
    assert len(payload["mutations"]) == 1
    assert len(payload["commands"]) == 1
    assert len(payload["verification"]) == 1


def test_reviewer_messages_truncates_long_output():
    """Mutation outputs longer than 1200 chars should be truncated in the reviewer payload."""
    long_output = "x" * 5000
    messages = reviewer_messages(
        task="check truncation",
        final_message="Done.",
        changed_paths=["big.py"],
        mutation_records=[{"action": "write_file", "path": "big.py", "ok": True, "output": long_output}],
        command_records=[],
        verification_results=[],
    )
    payload = json.loads(messages[1]["content"])
    mutation_output = payload["mutations"][0]["output"]
    assert len(mutation_output) < len(long_output)
    assert "<truncated" in mutation_output


# ---------------------------------------------------------------------------
# run_reviewer_pass with mock model client
# ---------------------------------------------------------------------------


@dataclass
class MockReviewerClient:
    model: str = "mock-reviewer"
    response: str = ""
    error: Exception | None = None
    _usage_records: list = None

    def __post_init__(self):
        if self._usage_records is None:
            self._usage_records = []

    def complete(self, messages):
        if self.error:
            raise self.error
        return self.response

    def drain_usage_records(self):
        records = self._usage_records
        self._usage_records = []
        return records


def test_run_reviewer_pass_approved():
    client = MockReviewerClient(
        response=json.dumps({"ok": True, "summary": "All changes look correct."})
    )
    result = run_reviewer_pass(
        client,
        task="fix calculator",
        final_message="Fixed.",
        changed_paths=["calc.py"],
        mutation_records=[],
        command_records=[],
        verification_results=[],
    )
    assert result.ok is True
    assert "correct" in result.summary
    assert result.error is None


def test_run_reviewer_pass_rejected():
    client = MockReviewerClient(
        response=json.dumps({
            "ok": False,
            "summary": "Missing edge-case test.",
            "issues": ["no test for negative input"],
            "required_actions": ["add test_negative"],
        })
    )
    result = run_reviewer_pass(
        client,
        task="fix calculator",
        final_message="Fixed.",
        changed_paths=["calc.py"],
        mutation_records=[],
        command_records=[],
        verification_results=[],
    )
    assert result.ok is False
    assert "edge-case" in result.summary
    assert len(result.issues) == 1
    assert len(result.required_actions) == 1


def test_run_reviewer_pass_model_failure_is_graceful():
    """If the reviewer model fails, the result should be ok=True (fail-open) with an error recorded."""
    client = MockReviewerClient(error=RuntimeError("Model service unavailable"))
    result = run_reviewer_pass(
        client,
        task="fix calculator",
        final_message="Fixed.",
        changed_paths=["calc.py"],
        mutation_records=[],
        command_records=[],
        verification_results=[],
    )
    assert result.ok is True
    assert result.error is not None
    assert "RuntimeError" in result.error


def test_run_reviewer_pass_malformed_json_is_graceful():
    """If the reviewer returns garbage, result should be ok=True (fail-open)."""
    client = MockReviewerClient(response="This is not JSON at all")
    result = run_reviewer_pass(
        client,
        task="fix calculator",
        final_message="Fixed.",
        changed_paths=["calc.py"],
        mutation_records=[],
        command_records=[],
        verification_results=[],
    )
    assert result.ok is True
    assert result.error is not None


# ---------------------------------------------------------------------------
# CodingAgent._review_final_answer integration
# ---------------------------------------------------------------------------


def _make_agent_with_reviewer(workspace: Path, reviewer_client=None):
    """Build a CodingAgent with minimal config for reviewer testing."""
    from code_agent.agent import CodingAgent
    from code_agent.storage import AgentStorage
    from code_agent.tools import ToolRegistry

    storage = AgentStorage(workspace / ".code-agent" / "test.db")
    storage.create_run(task="review fixture", model="primary", cwd=workspace)
    return CodingAgent(
        cwd=workspace,
        dry_run=False,
        max_steps=5,
        max_failures=3,
        model_client=MockReviewerClient(model="primary"),
        tools=ToolRegistry(workspace=workspace, dry_run=False, approval_callback=lambda a, d: True),
        storage=storage,
        stream_model=False,
        reviewer_client=reviewer_client,
    )


def test_review_final_answer_skips_when_no_reviewer():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        workspace = Path(tmp)
        agent = _make_agent_with_reviewer(workspace, reviewer_client=None)
        review_records: list[dict[str, Any]] = []
        rejection = agent._review_final_answer(
            run_id=1,
            task="fix something",
            final_message="Fixed.",
            step=1,
            changed_paths=["file.py"],
            mutation_records=[],
            command_records=[],
            verification_results=[],
            model_usage_records=[],
            review_records=review_records,
        )
        assert rejection is None
        assert len(review_records) == 0


def test_review_final_answer_skips_when_no_changed_paths():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        workspace = Path(tmp)
        reviewer = MockReviewerClient(
            response=json.dumps({"ok": True, "summary": "Approved."})
        )
        agent = _make_agent_with_reviewer(workspace, reviewer_client=reviewer)
        review_records: list[dict[str, Any]] = []
        rejection = agent._review_final_answer(
            run_id=1,
            task="explain something",
            final_message="Here is the explanation.",
            step=1,
            changed_paths=[],
            mutation_records=[],
            command_records=[],
            verification_results=[],
            model_usage_records=[],
            review_records=review_records,
        )
        assert rejection is None
        assert len(review_records) == 0


def test_review_final_answer_approved():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        workspace = Path(tmp)
        reviewer = MockReviewerClient(
            response=json.dumps({"ok": True, "summary": "Changes look correct."})
        )
        agent = _make_agent_with_reviewer(workspace, reviewer_client=reviewer)
        review_records: list[dict[str, Any]] = []
        rejection = agent._review_final_answer(
            run_id=1,
            task="fix calculator",
            final_message="Fixed the add function.",
            step=3,
            changed_paths=["calculator.py"],
            mutation_records=[{"action": "edit_file", "path": "calculator.py", "ok": True}],
            command_records=[],
            verification_results=[],
            model_usage_records=[],
            review_records=review_records,
        )
        assert rejection is None
        assert len(review_records) == 1
        assert review_records[0]["ok"] is True


def test_review_final_answer_rejected():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        workspace = Path(tmp)
        reviewer = MockReviewerClient(
            response=json.dumps({
                "ok": False,
                "summary": "Bug: off-by-one error.",
                "issues": ["loop bound wrong"],
                "required_actions": ["fix line 42"],
            })
        )
        agent = _make_agent_with_reviewer(workspace, reviewer_client=reviewer)
        review_records: list[dict[str, Any]] = []
        rejection = agent._review_final_answer(
            run_id=1,
            task="fix calculator",
            final_message="Fixed the add function.",
            step=3,
            changed_paths=["calculator.py"],
            mutation_records=[{"action": "edit_file", "path": "calculator.py", "ok": True}],
            command_records=[],
            verification_results=[],
            model_usage_records=[],
            review_records=review_records,
        )
        assert rejection is not None
        assert rejection["ok"] is False
        assert "reviewer_rejected_final" in rejection["kind"]
        assert "off-by-one" in rejection["output"]
        assert len(review_records) == 1
        assert review_records[0]["ok"] is False


def test_review_final_answer_model_failure_is_transparent():
    """If the reviewer model itself fails, the final answer should NOT be blocked."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        workspace = Path(tmp)
        reviewer = MockReviewerClient(error=RuntimeError("Service down"))
        agent = _make_agent_with_reviewer(workspace, reviewer_client=reviewer)
        review_records: list[dict[str, Any]] = []
        rejection = agent._review_final_answer(
            run_id=1,
            task="fix calculator",
            final_message="Fixed.",
            step=2,
            changed_paths=["calc.py"],
            mutation_records=[],
            command_records=[],
            verification_results=[],
            model_usage_records=[],
            review_records=review_records,
        )
        assert rejection is None
        assert len(review_records) == 1
        assert review_records[0]["ok"] is True
        assert review_records[0]["error"] is not None
