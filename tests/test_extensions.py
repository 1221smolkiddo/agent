from code_agent.extensions import (
    DynamicToolRegistry,
    HookSubscription,
    LifecycleHooks,
    ToolMetadata,
)


def test_dynamic_registry_discovery_validation_reload_and_permissions():
    approvals = []
    registry = DynamicToolRegistry(
        lambda name, permissions, actor: approvals.append((name, permissions, actor)) or True
    )
    metadata = ToolMetadata(
        name="echo",
        namespace="demo",
        description="Echo input",
        aliases=("say",),
        permissions=("external.api",),
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    )
    first = registry.register(metadata, lambda args: args["text"])
    assert registry.resolve("say") is first
    assert registry.invoke("say", {}).ok is False
    result = registry.invoke("demo.echo", {"text": "hello"})
    assert result.ok and result.output == "hello"
    assert approvals == [("demo.echo", ("external.api",), "primary")]
    second = registry.reload(metadata, lambda _args: "new")
    assert second.generation == 2
    assert registry.discover(namespace="demo")[0]["health"]["ok"] is True
    assert registry.unregister("echo") is True
    assert registry.invoke("demo.echo", {"text": "x"}).ok is False


def test_registry_rejects_missing_dependencies_and_unhealthy_tools():
    registry = DynamicToolRegistry()
    try:
        registry.register(
            ToolMetadata("child", "Child", dependencies=("core.parent",)),
            lambda _args: "x",
        )
    except ValueError as exc:
        assert "dependencies" in str(exc)
    else:
        raise AssertionError("missing dependency was accepted")
    registry.register(
        ToolMetadata("offline", "Offline"),
        lambda _args: "x",
        health_check=lambda: (False, "down"),
    )
    assert "unavailable" in registry.invoke("offline", {}).output


def test_lifecycle_hooks_are_prioritized_isolated_and_removable():
    hooks = LifecycleHooks()
    calls = []
    hooks.subscribe(HookSubscription("tool.before", lambda _p: calls.append("late"), 20))
    hooks.subscribe(HookSubscription("tool.before", lambda _p: calls.append("early"), 10))
    hooks.subscribe(
        HookSubscription(
            "tool.before", lambda _p: (_ for _ in ()).throw(RuntimeError("boom")), source="plugin:x"
        )
    )
    results = hooks.emit("tool.before", {"action": "x"})
    assert calls == ["early", "late"]
    assert any(item["ok"] is False for item in results)
    assert hooks.unsubscribe_source("plugin:x") == 1
