import pytest
from code_agent.execution_adapters import ToolRegistryAdapter, AdapterContext
from code_agent.extensions import DynamicToolRegistry, ToolMetadata, ToolResult

@pytest.fixture
def registry() -> DynamicToolRegistry:
    reg = DynamicToolRegistry()
    reg.register(
        ToolMetadata(name="test_tool", description="A test tool"),
        lambda args: f"Success: {args.get('val')}"
    )
    reg.register(
        ToolMetadata(name="fail_tool", description="A failing tool"),
        lambda args: ToolResult(ok=False, output="Intentional failure")
    )
    return reg

@pytest.fixture
def adapter(registry: DynamicToolRegistry) -> ToolRegistryAdapter:
    return ToolRegistryAdapter(tools=registry)

@pytest.fixture
def context() -> AdapterContext:
    return AdapterContext(
        idempotency_key="test-key",
        compatibility_version="1",
        execution_id="exec-123",
        task_id="task-123",
        actor="test-actor",
        timeout_seconds=30.0,
        metadata={"effect_kind": "tool"}
    )

def test_tool_registry_adapter_success(adapter: ToolRegistryAdapter, context: AdapterContext) -> None:
    request = {
        "action": {
            "type": "invoke_tool",
            "tool": "core.test_tool",
            "arguments": {"val": 42}
        }
    }
    
    prepared = adapter.prepare(request, context)
    outcome = adapter.execute(prepared)
    
    assert outcome.ok is True
    assert outcome.status == "committed"
    assert "Success: 42" in outcome.output

def test_tool_registry_adapter_failure(adapter: ToolRegistryAdapter, context: AdapterContext) -> None:
    request = {
        "action": {
            "type": "invoke_tool",
            "tool": "core.fail_tool",
            "arguments": {}
        }
    }
    
    prepared = adapter.prepare(request, context)
    outcome = adapter.execute(prepared)
    
    assert outcome.ok is False
    assert outcome.status == "failed"
    assert "Intentional failure" in outcome.output

def test_tool_registry_adapter_unknown_tool(adapter: ToolRegistryAdapter, context: AdapterContext) -> None:
    request = {
        "action": {
            "type": "invoke_tool",
            "tool": "core.unknown_tool",
            "arguments": {}
        }
    }
    
    prepared = adapter.prepare(request, context)
    outcome = adapter.execute(prepared)
    
    assert outcome.ok is False
    assert "not registered" in outcome.output

def test_tool_registry_adapter_invalid_action(adapter: ToolRegistryAdapter, context: AdapterContext) -> None:
    request = {
        "action": {
            "type": "read_file",
            "path": "README.md"
        }
    }
    
    prepared = adapter.prepare(request, context)
    outcome = adapter.execute(prepared)
    
    assert outcome.ok is False
    assert outcome.status == "invalid"
    assert "not a valid tool invocation" in outcome.output
