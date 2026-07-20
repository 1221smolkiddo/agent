import json

from code_agent.extensions import DynamicToolRegistry, LifecycleHooks
from code_agent.orchestration import AgentCatalog
from code_agent.plugins import PluginManager
from code_agent.skills import SkillCatalog


def test_plugin_load_and_unload_are_atomic_by_source(tmp_path):
    root = tmp_path / "sample"
    root.mkdir()
    (root / "plugin.json").write_text(
        json.dumps({
            "name": "sample",
            "version": "1.0.0",
            "description": "Sample plugin",
            "api_version": "1",
            "tools": [{
                "name": "check",
                "description": "Check",
                "command": ["check.exe"],
                "permissions": ["plugin.execute"],
            }],
        }),
        encoding="utf-8",
    )
    registry = DynamicToolRegistry()
    manager = PluginManager(registry, SkillCatalog(), AgentCatalog(), LifecycleHooks())
    manager.load(root / "plugin.json")
    assert registry.resolve("plugin-sample.check") is not None
    assert manager.unload("sample") is True
    assert registry.resolve("plugin-sample.check") is None
