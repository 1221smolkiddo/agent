import queue

import pytest

from code_agent.extensions import DynamicToolRegistry
from code_agent.mcp import McpError, McpServerConfig, McpStdioClient


def test_mcp_discovery_registers_allowlisted_tool(monkeypatch, tmp_path):
    client = McpStdioClient(
        McpServerConfig("docs", ("server",), tmp_path, allowed_tools=("lookup",))
    )
    monkeypatch.setattr(
        client,
        "list_tools",
        lambda refresh=False: [{
            "name": "lookup",
            "description": "Look up docs",
            "inputSchema": {"type": "object", "required": ["query"]},
        }],
    )
    monkeypatch.setattr(
        client,
        "call_tool",
        lambda name, arguments: {"content": [{"type": "text", "text": arguments["query"]}]},
    )
    monkeypatch.setattr(type(client), "connected", property(lambda _self: True))
    registry = DynamicToolRegistry(lambda *_args: True)
    assert client.register_tools(registry) == ["mcp-docs.lookup"]
    result = registry.invoke("docs:lookup", {"query": "MCP"})
    assert result.ok and result.output == "MCP"


def test_mcp_read_response_has_a_real_timeout():
    client = McpStdioClient(McpServerConfig("slow", ("server",), timeout_seconds=0.01))
    client._responses = queue.Queue()
    with pytest.raises(McpError, match="timed out"):
        client._read_response(1)
