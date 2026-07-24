from __future__ import annotations

from pathlib import Path
import pytest
from prompt_toolkit.document import Document

from code_agent.command_registry import (
    CommandMeta,
    CommandRegistry,
    CommandCompleter,
    build_default_registry,
)
from code_agent.fuzzy import search_commands, suggest_commands, levenshtein
from code_agent.interactive import (
    handle_command,
    SESSION_REGISTRY,
)
from code_agent.config import Settings


def test_command_registry_indexing_and_lookup():
    reg = CommandRegistry([
        CommandMeta(name="/test", aliases=("/t",), description="Test command", keywords=("testing",)),
    ])
    assert reg.lookup("/test").name == "/test"
    assert reg.lookup("test").name == "/test"
    assert reg.lookup("/t").name == "/test"
    assert reg.lookup("/t").description == "Test command"
    assert reg.lookup("/nonexistent") is None


def test_command_registry_dynamic_registration():
    reg = CommandRegistry()
    meta = CommandMeta(name="/custom", category="Advanced", description="Custom dynamic command")
    reg.register(meta)
    assert reg.lookup("/custom") is not None
    assert reg.lookup("/custom").description == "Custom dynamic command"

    unregistered = reg.unregister("/custom")
    assert unregistered is True
    assert reg.lookup("/custom") is None


def test_tiered_fuzzy_search_ranking():
    reg = build_default_registry()
    
    # 1. Exact prefix: /m matches both /model and /max-steps, /model ranked first
    matches = search_commands("/m", reg, limit=10, include_hidden=True)
    names = [m.name for m in matches]
    assert "/model" in names
    assert "/max-steps" in names
    assert names.index("/model") < names.index("/max-steps")

    # 2. Keyword search: "gemini" should return /model
    kw_matches = search_commands("gemini", reg, limit=5)
    assert any(m.name == "/model" for m in kw_matches)

    # 3. Fuzzy search for typo /modl -> /model
    typo_suggestions = suggest_commands("/modl", reg)
    assert any(m.name == "/model" for m in typo_suggestions)

    # 4. Fuzzy search for typo /sanbox -> /sandbox
    sandbox_suggestions = suggest_commands("/sanbox", reg)
    assert any(m.name == "/sandbox" for m in sandbox_suggestions)


def test_levenshtein_distance():
    assert levenshtein("model", "model") == 0
    assert levenshtein("modl", "model") == 1
    assert levenshtein("sanbox", "sandbox") == 1
    assert levenshtein("hstory", "history") == 1


def test_command_completer_empty_search():
    reg = build_default_registry()
    completer = CommandCompleter(reg)
    doc = Document("/")
    completions = list(completer.get_completions(doc, None))
    
    comp_texts = [c.text for c in completions]
    assert "/help" in comp_texts
    assert "/status" in comp_texts
    assert "/advanced" in comp_texts


def test_command_completer_prefix_filter():
    reg = build_default_registry()
    completer = CommandCompleter(reg)
    doc = Document("/mo")
    completions = list(completer.get_completions(doc, None))
    
    comp_texts = [c.text for c in completions]
    assert "/model" in comp_texts


def test_command_completer_subcommands():
    reg = build_default_registry()
    completer = CommandCompleter(reg)
    doc = Document("/sandbox ")
    completions = list(completer.get_completions(doc, None))
    
    sub_texts = [c.text for c in completions]
    assert "diff" in sub_texts
    assert "apply" in sub_texts
    assert "off" in sub_texts


def test_handle_command_help_advanced_keys_status(tmp_path):
    settings = Settings()
    cwd = tmp_path

    # /help
    st = handle_command("/help", settings, cwd, cwd, None, None, False, True, False, 12, None)
    assert st.exit_requested is False

    # /advanced
    st = handle_command("/advanced", settings, cwd, cwd, None, None, False, True, False, 12, None)
    assert st.exit_requested is False

    # /keys
    st = handle_command("/keys", settings, cwd, cwd, None, None, False, True, False, 12, None)
    assert st.exit_requested is False

    # /status
    st = handle_command("/status", settings, cwd, cwd, None, None, False, True, False, 12, None)
    assert st.exit_requested is False


def test_handle_command_unknown_with_suggestions(tmp_path):
    settings = Settings()
    cwd = tmp_path

    # /modl should show suggestions without breaking
    st = handle_command("/modl", settings, cwd, cwd, None, None, False, True, False, 12, None)
    assert st.exit_requested is False
