from __future__ import annotations

from types import SimpleNamespace

import pytest
from rich.console import Console

from code_agent.command_registry import build_default_registry
from code_agent.diff_launcher import (
    DiffLaunchStatus,
    extract_unified_diff,
    launch_diff_viewer,
)
from code_agent.interactive import handle_command
from code_agent.permissions import confirm_permission
from code_agent.session import SessionState


SAMPLE_PATCH_A = """--- a/foo.py
+++ b/foo.py
@@ -1 +1 @@
-def foo(): return 1
+def foo(): return 42
"""

SAMPLE_PATCH_B = """--- a/bar.py
+++ b/bar.py
@@ -1 +1 @@
-hello world
+hello agent47
"""

MALFORMED_PATCH = "this is not a valid diff format at all"


def test_session_state_diff_tracking() -> None:
    session = SessionState()
    assert session.last_diff is None

    session.set_last_diff(SAMPLE_PATCH_A)
    assert session.last_diff == SAMPLE_PATCH_A.strip() + "\n"

    # Patch A -> Patch B replacement sequence
    session.set_last_diff(SAMPLE_PATCH_B)
    assert session.last_diff == SAMPLE_PATCH_B.strip() + "\n"
    assert "bar.py" in session.last_diff
    assert "foo.py" not in session.last_diff

    # Instance-scoped isolation test
    other_session = SessionState()
    assert other_session.last_diff is None


def test_command_registry_contains_diff_view() -> None:
    registry = build_default_registry()
    meta = registry.lookup("/diff-view")
    assert meta is not None
    assert meta.name == "/diff-view"
    assert "/diff" in meta.aliases

    meta_alias = registry.lookup("/diff")
    assert meta_alias is not None
    assert meta_alias.name == "/diff-view"


def test_extract_unified_diff_helper() -> None:
    assert extract_unified_diff("") is None
    assert extract_unified_diff("   \n ") is None

    # Raw diff string
    extracted = extract_unified_diff(SAMPLE_PATCH_A)
    assert extracted is not None
    assert "foo.py" in extracted

    # Patch preview detail wrapper format
    wrapped = f"Patch preview:\nFiles: 1\n\nUnified diff:\n{SAMPLE_PATCH_A}"
    extracted_wrapped = extract_unified_diff(wrapped)
    assert extracted_wrapped is not None
    assert extracted_wrapped.strip() == SAMPLE_PATCH_A.strip()


def test_launch_diff_viewer_no_diff() -> None:
    res = launch_diff_viewer(None)
    assert res.status == DiffLaunchStatus.NO_DIFF
    assert res.success is False
    assert "No diff is currently available" in (res.message or "")

    res_empty = launch_diff_viewer("   \n ")
    assert res_empty.status == DiffLaunchStatus.NO_DIFF
    assert res_empty.success is False


def test_launch_diff_viewer_valid_diff() -> None:
    keys = iter(["quit"])
    res = launch_diff_viewer(
        SAMPLE_PATCH_A,
        console=Console(quiet=True),
        read_key=lambda: next(keys, "quit"),
    )
    assert res.status == DiffLaunchStatus.SHOWN
    assert res.success is True
    assert res.message is None


def test_launch_diff_viewer_malformed_diff() -> None:
    res = launch_diff_viewer(MALFORMED_PATCH, console=Console(quiet=True))
    assert res.status == DiffLaunchStatus.PARSE_ERROR
    assert res.success is False
    assert "Could not parse diff" in (res.message or "")


def test_launch_diff_viewer_immutability() -> None:
    original = str(SAMPLE_PATCH_A)
    keys = iter(["quit"])
    res = launch_diff_viewer(
        original,
        console=Console(quiet=True),
        read_key=lambda: next(keys, "quit"),
    )
    assert res.status == DiffLaunchStatus.SHOWN
    assert original == SAMPLE_PATCH_A


def test_diff_view_command_handling() -> None:
    session = SessionState()

    # When no diff is stored
    res_state = handle_command(
        "/diff-view",
        settings=None,  # type: ignore[arg-type]
        base_cwd=None,  # type: ignore[arg-type]
        cwd=None,  # type: ignore[arg-type]
        model=None,
        profile=None,
        dry_run=False,
        stream_model=False,
        sandbox_enabled=False,
        max_steps=10,
        max_failures=None,
        session_state=session,
    )
    assert res_state.exit_requested is False

    # Store Patch A, then Patch B
    session.set_last_diff(SAMPLE_PATCH_A)
    session.set_last_diff(SAMPLE_PATCH_B)
    assert session.last_diff is not None
    assert "bar.py" in session.last_diff


def test_confirm_permission_view_option_then_approve(monkeypatch) -> None:
    session = SessionState()
    session.set_last_diff(SAMPLE_PATCH_A)

    user_inputs = iter(["v", "y"])
    monkeypatch.setattr("rich.prompt.Prompt.ask", lambda *args, **kwargs: next(user_inputs))

    keys = iter(["quit"])
    result = confirm_permission(
        "apply_patch",
        SAMPLE_PATCH_A,
        session_state=session,
        read_key=lambda: next(keys, "quit"),
    )
    assert result == "y"


def test_confirm_permission_view_option_then_deny(monkeypatch) -> None:
    session = SessionState()

    user_inputs = iter(["v", "n"])
    monkeypatch.setattr("rich.prompt.Prompt.ask", lambda *args, **kwargs: next(user_inputs))

    keys = iter(["quit"])
    result = confirm_permission(
        "apply_patch",
        SAMPLE_PATCH_A,
        session_state=session,
        read_key=lambda: next(keys, "quit"),
    )
    assert result == "n"


def test_session_preferred_diff_mode() -> None:
    from code_agent.diff_types import DiffViewMode

    session = SessionState()
    assert session.preferred_diff_mode == DiffViewMode.UNIFIED

    session.set_preferred_diff_mode("side-by-side")
    assert session.preferred_diff_mode == DiffViewMode.SIDE_BY_SIDE

    session.set_preferred_diff_mode("unified")
    assert session.preferred_diff_mode == DiffViewMode.UNIFIED

    import pytest
    with pytest.raises(ValueError):
        session.set_preferred_diff_mode("invalid")


def test_diff_mode_command_handling() -> None:
    from code_agent.diff_types import DiffViewMode

    session = SessionState()

    # No args -> inspect mode
    handle_command(
        "/diff-mode",
        settings=None,  # type: ignore[arg-type]
        base_cwd=None,  # type: ignore[arg-type]
        cwd=None,  # type: ignore[arg-type]
        model=None,
        profile=None,
        dry_run=False,
        stream_model=False,
        sandbox_enabled=False,
        max_steps=10,
        max_failures=None,
        session_state=session,
    )
    assert session.preferred_diff_mode == DiffViewMode.UNIFIED

    # Update mode
    handle_command(
        "/diff-mode side-by-side",
        settings=None,  # type: ignore[arg-type]
        base_cwd=None,  # type: ignore[arg-type]
        cwd=None,  # type: ignore[arg-type]
        model=None,
        profile=None,
        dry_run=False,
        stream_model=False,
        sandbox_enabled=False,
        max_steps=10,
        max_failures=None,
        session_state=session,
    )
    assert session.preferred_diff_mode == DiffViewMode.SIDE_BY_SIDE


@pytest.mark.parametrize("preferred_mode", ["side-by-side", "unified"])
def test_diff_view_command_forwards_session_preferred_mode(monkeypatch, preferred_mode) -> None:
    from code_agent.diff_types import DiffViewMode

    session = SessionState()
    session.set_preferred_diff_mode(preferred_mode)
    session.set_last_diff(SAMPLE_PATCH_A)

    calls: list[dict[str, object]] = []

    def fake_launch_diff_viewer(diff_text, *, mode=None, console=None, read_key=None):
        calls.append({"diff_text": diff_text, "mode": mode, "console": console})
        return SimpleNamespace(success=True)

    monkeypatch.setattr("code_agent.interactive.launch_diff_viewer", fake_launch_diff_viewer)

    handle_command(
        "/diff-view",
        settings=None,  # type: ignore[arg-type]
        base_cwd=None,  # type: ignore[arg-type]
        cwd=None,  # type: ignore[arg-type]
        model=None,
        profile=None,
        dry_run=False,
        stream_model=False,
        sandbox_enabled=False,
        max_steps=10,
        max_failures=None,
        session_state=session,
    )

    assert len(calls) == 1
    assert calls[0]["diff_text"] == SAMPLE_PATCH_A
    assert calls[0]["mode"] == DiffViewMode.normalize(preferred_mode)
