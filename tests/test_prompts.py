from pathlib import Path

from code_agent.prompts import system_prompt


def test_system_prompt_names_agent47_engineering_protocol() -> None:
    prompt = system_prompt(Path("C:/work/project"), dry_run=False)

    assert "You are Agent47" in prompt
    assert "Operating protocol:" in prompt
    assert "general chat, current external info, general coding help, or workspace coding work" in prompt
    assert "Only use workspace tools when the user asks about this project" in prompt
    assert "For current external info such as time, weather, prices" in prompt
    assert "If web_search returns weak or no results" in prompt
    assert "build a short internal plan" in prompt
    assert "Prefer apply_patch for code edits" in prompt
    assert "use a file mutation tool instead of giving the user a template" in prompt
    assert "never describe unsaved content as a created file" in prompt
    assert '{ "type": "apply_patch"' in prompt
    assert "run the most focused useful verification command" in prompt
    assert "Use detect_verification" in prompt
    assert '{ "type": "detect_verification" }' in prompt
    assert '{ "type": "web_search"' in prompt
    assert "Use suggest_verification with changed paths" in prompt
    assert '{ "type": "suggest_verification"' in prompt
    assert "Final answers must state what changed, what was verified" in prompt
    assert "For non-workspace questions, answer directly or use web_search" in prompt


def test_system_prompt_dry_run_blocks_mutations_and_shell() -> None:
    prompt = system_prompt(Path("C:/work/project"), dry_run=True)

    assert "Dry-run mode is enabled" in prompt
    assert "do not request write_file, edit_file, apply_patch, or run_shell" in prompt
