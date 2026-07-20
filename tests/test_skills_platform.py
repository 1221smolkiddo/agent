import json

from code_agent.platform_runtime import PlatformRuntime
from code_agent.skills import InstructionResolver, SkillCatalog


def test_builtin_skill_catalog_contains_major_agent_skills(tmp_path):
    catalog = SkillCatalog.for_workspace(tmp_path)
    names = {skill.name for skill in catalog.discover()}
    assert {
        "security-auditor",
        "test-generator",
        "code-reviewer",
        "documentation-writer",
        "dependency-analyzer",
        "refactoring-expert",
        "performance-optimizer",
        "devops-assistant",
    } <= names
    assert catalog.match("audit security vulnerabilities")[0].name == "security-auditor"


def test_workspace_skills_and_extensions_have_separate_trust_boundaries(tmp_path):
    skill_dir = tmp_path / ".agents" / "skills" / "local-helper"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: local-helper\ndescription: Helps with widgets\n---\n\nInspect widgets.",
        encoding="utf-8",
    )
    plugin_dir = tmp_path / ".agents" / "plugins" / "example"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({
            "name": "example", "version": "1.0.0", "description": "Example",
            "api_version": "1", "tools": [],
        }),
        encoding="utf-8",
    )
    runtime = PlatformRuntime.create(tmp_path)
    assert runtime.skills.get("local-helper") is not None
    assert runtime.plugins.loaded == {}
    trusted = PlatformRuntime.create(tmp_path, trust_workspace_extensions=True)
    assert "example" in trusted.plugins.loaded


def test_scoped_instructions_resolve_by_precedence_and_condition():
    resolver = InstructionResolver()
    resolver.add("global", "base", source="global")
    resolver.add("task", "python only", source="task", condition="python")
    assert [item.scope for item in resolver.resolve(task="fix python")] == ["global", "task"]
    assert [item.scope for item in resolver.resolve(task="fix rust")] == ["global"]
