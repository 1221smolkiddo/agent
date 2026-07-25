from code_agent.diff_viewer import parse_unified_diff
from code_agent.interactive_diff import DiffReviewResult
from code_agent.patch_reconstruction import reconstruct_selected_patch

def test_reconstruct_selected_patch():
    patch_text = """diff --git a/test.py b/test.py
--- a/test.py
+++ b/test.py
@@ -1,3 +1,3 @@
 line 1
-line 2
+line 2 mod
 line 3
@@ -10,3 +10,3 @@
 line 10
-line 11
+line 11 mod
 line 12
"""

    review_all = DiffReviewResult(
        accepted=frozenset({(0, 0), (0, 1)}),
        rejected=frozenset(),
        cancelled=False,
    )
    result_all = reconstruct_selected_patch(patch_text, review_all)
    assert "line 2 mod" in result_all
    assert "line 11 mod" in result_all

    review_first = DiffReviewResult(
        accepted=frozenset({(0, 0)}),
        rejected=frozenset({(0, 1)}),
        cancelled=False,
    )
    result_first = reconstruct_selected_patch(patch_text, review_first)
    assert "line 2 mod" in result_first
    assert "line 11 mod" not in result_first
    assert "@@ -10,3 +10,3 @@" not in result_first

    review_none = DiffReviewResult(
        accepted=frozenset(),
        rejected=frozenset({(0, 0), (0, 1)}),
        cancelled=False,
    )
    result_none = reconstruct_selected_patch(patch_text, review_none)
    assert result_none == ""


def test_reconstruct_selected_patch_preserves_multiple_files_in_order() -> None:
    patch_text = """diff --git a/one.txt b/one.txt
--- a/one.txt
+++ b/one.txt
@@ -1 +1 @@
-old one
+new one

diff --git a/two.txt b/two.txt
--- a/two.txt
+++ b/two.txt
@@ -1 +1 @@
-old two
+new two
"""

    review = DiffReviewResult(
        accepted=frozenset({(0, 0), (1, 0)}),
        rejected=frozenset(),
        cancelled=False,
    )

    reconstructed = reconstruct_selected_patch(patch_text, review)

    assert "b/one.txt" in reconstructed
    assert "b/two.txt" in reconstructed
    assert reconstructed.index("b/one.txt") < reconstructed.index("b/two.txt")
    assert "+new one" in reconstructed
    assert "+new two" in reconstructed


def test_reconstruct_selected_patch_is_deterministic() -> None:
    patch_text = """diff --git a/test.py b/test.py
--- a/test.py
+++ b/test.py
@@ -1 +1 @@
-old
+new
"""

    review = DiffReviewResult(
        accepted=frozenset({(0, 0)}),
        rejected=frozenset(),
        cancelled=False,
    )

    first = reconstruct_selected_patch(patch_text, review)
    second = reconstruct_selected_patch(patch_text, review)

    assert first == second


def test_reconstruct_selected_patch_reparses_to_only_accepted_hunks() -> None:
    patch_text = """diff --git a/test.py b/test.py
--- a/test.py
+++ b/test.py
@@ -1,3 +1,3 @@
 context
-old first
+new first
 context
@@ -4,3 +4,3 @@
 context
-old second
+new second
 context
"""

    review = DiffReviewResult(
        accepted=frozenset({(0, 0)}),
        rejected=frozenset({(0, 1)}),
        cancelled=False,
    )

    reconstructed = reconstruct_selected_patch(patch_text, review)
    parsed_files = parse_unified_diff(reconstructed)

    assert len(parsed_files) == 1
    assert len(parsed_files[0].hunks) == 1
    assert parsed_files[0].hunks[0].header == "@@ -1,3 +1,3 @@"
    hunk_text = [line.text for line in parsed_files[0].hunks[0].lines]
    assert "old first" in hunk_text
    assert "new first" in hunk_text
    assert "old second" not in hunk_text
    assert "new second" not in hunk_text
