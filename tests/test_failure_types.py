from code_agent.failure_types import (
    FailureCategory,
    FailureFingerprint,
    RecoveryStrategy,
    classify_failure,
    recovery_strategy,
)


def test_classify_failure_uses_structured_error_code():
    # Prefers structured error over keyword matching
    assert classify_failure("run_shell", "some random error", {"error_code": "permission_denied"}) == FailureCategory.PERMISSION_DENIED
    assert classify_failure("read_file", "some text", {"error_type": "file_not_found"}) == FailureCategory.FILE_MISSING
    assert classify_failure("edit_file", "unknown", {"budget_exceeded": True}) == FailureCategory.BUDGET_EXCEEDED


def test_classify_failure_uses_keyword_fallback():
    assert classify_failure("read_file", "No such file or directory: 'missing.py'") == FailureCategory.FILE_MISSING
    assert classify_failure("apply_patch", "Hunk #1 FAILED at 10.") == FailureCategory.PATCH_FAILURE
    assert classify_failure("run_shell", "Permission denied: '/etc/shadow'") == FailureCategory.PERMISSION_DENIED
    assert classify_failure("search", "No matches found.") == FailureCategory.SEARCH_NO_RESULTS
    assert classify_failure("read_memory", "database is locked") == FailureCategory.DATABASE_LOCKED
    assert classify_failure("read_file", "just some text without errors") == FailureCategory.UNKNOWN


def test_recovery_strategy_maps_correctly():
    assert recovery_strategy(FailureCategory.PATCH_FAILURE) == RecoveryStrategy.GENERATE_NEW_PATCH
    assert recovery_strategy(FailureCategory.FILE_MISSING) == RecoveryStrategy.REREAD_FILE
    assert recovery_strategy(FailureCategory.PERMISSION_DENIED) == RecoveryStrategy.ASK_USER


def test_failure_fingerprint_uniqueness():
    fp1 = FailureFingerprint.from_action(
        "read_file",
        {"path": "test.py"},
        FailureCategory.FILE_MISSING,
    )
    fp2 = FailureFingerprint.from_action(
        "read_file",
        {"path": "test.py"},
        FailureCategory.FILE_MISSING,
    )
    fp3 = FailureFingerprint.from_action(
        "read_file",
        {"path": "other.py"},
        FailureCategory.FILE_MISSING,
    )
    
    assert fp1 == fp2
    assert fp1 != fp3
    assert hash(fp1) == hash(fp2)
    assert hash(fp1) != hash(fp3)
