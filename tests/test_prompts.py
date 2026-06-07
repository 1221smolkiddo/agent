from pathlib import Path

from code_agent.prompts import system_prompt


def test_system_prompt_names_agent47_engineering_protocol() -> None:
    prompt = system_prompt(Path("C:/work/project"), dry_run=False)

    assert "You are Agent47" in prompt
    assert "Operating protocol:" in prompt
    assert "build a short internal plan" in prompt
    assert "Prefer apply_patch for code edits" in prompt
    assert '{ "type": "apply_patch"' in prompt
    assert "run the most focused useful verification command" in prompt
    assert "Final answers must state what changed, what was verified" in prompt


def test_system_prompt_dry_run_blocks_mutations_and_shell() -> None:
    prompt = system_prompt(Path("C:/work/project"), dry_run=True)

    assert "Dry-run mode is enabled" in prompt
    assert "do not request write_file, edit_file, apply_patch, or run_shell" in prompt
