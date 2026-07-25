# Model Context Protocol (MCP) Integration

Agent47 implements a robust and dynamic integration with the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/), extending the agent's capabilities with external tools, resources, and prompts provided by MCP stdio servers.

## Architecture

The MCP integration is designed to cleanly separate lifecycle management, JSON-RPC communication, and durable runtime execution.

### Components
1. **McpStdioClient**: Wraps the JSON-RPC communication over stdio, managing connection states, caching (for tools, resources, and prompts), bounded timeouts, and transparent reconnection logic.
2. **McpManager**: Orchestrates the server fleet lifecycle (start, stop, restart, validate). It performs atomic `reconcile()` operations, ensuring modified or removed server configurations are handled gracefully without disrupting unchanged servers.
3. **PlatformRuntime**: Acts as the interface between the user (or CLI) and the `McpManager`. It fetches server configuration from `.agents/mcp.json`.
4. **McpAdapter**: Extends the `CallableTransactionalAdapter` within the `DurableExecutionEngine`, guaranteeing that MCP tools participate in Agent47's transactional lifecycle.

## Features

### Dynamic Tool Registration
When an MCP server connects, its exposed tools are dynamically discovered and translated into Agent47's `DynamicToolRegistry`.
- Each tool is prefixed with its server's namespace (e.g., `mcp-filesystem-read_file`).
- Tools inherit the capabilities `"mcp.resource"`, `"external.api"`, and `"network.access"`.
- A background health check ensures tools from disconnected servers fail gracefully.

### Durable Execution Pipeline
MCP tool invocations are not executed blindly; they flow through Agent47's central `DurableExecutionEngine`.
- **Lifecycle Integration**: A tool invocation goes through the formal state machine (`REQUESTED -> AUTHORIZED -> PREPARED -> DISPATCHED -> COMMITTED`).
- **Immutable Evidence**: All state transitions, including the final result (or failure reason), are durably persisted in the workspace's `SQLiteEventStore`.
- **Resource Tracking**: Execution uses process-level tracking (e.g., `wall_seconds`, `network_requests`).

### CLI and Interactive UI
Agent47 provides first-class CLI operations for interacting with your MCP fleet:

```bash
# Lifecycle operations
agent47 mcp validate          # Validate .agents/mcp.json configuration
agent47 mcp list              # List all configured servers
agent47 mcp status            # Show health and connection status
agent47 mcp start <server>    # Start a specific server
agent47 mcp stop <server>     # Stop a running server
agent47 mcp restart <server>  # Restart a server
agent47 mcp reload            # Hot-reload configuration changes from mcp.json

# Capability exploration
agent47 mcp resources         # List all available resources
agent47 mcp prompts           # List all available prompts
```

When operating inside the interactive terminal, use the read-only slash commands to explore server capabilities:
- `/mcp`
- `/mcp tools`
- `/mcp resources`
- `/mcp prompts`

## Configuration

MCP servers are configured per-workspace in `.agents/mcp.json`.

```json
{
  "servers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "./"],
      "enabled": true,
      "timeout_seconds": 30.0,
      "reconnect_attempts": 2
    }
  }
}
```

> [!TIP]
> Use `agent47 mcp reload` after modifying this file to apply changes without restarting the main agent process!

## Future Roadmap

While MCP executions flow through the durable execution engine, a few advanced safety and recovery features are planned:
- **Pre-Dispatch Schema Validation**: Validating the incoming `arguments` payload against the MCP tool's JSON schema *before* dispatching the effect. Currently, validation occurs dynamically during execution.
- **Granular Authorization Policies**: Enforcing per-workspace or per-server checks (beyond generic engine permissions) during the `AUTHORIZATION` phase.
- **Failure Reconciliation**: Supporting MCP-specific state verification to reconcile ambiguous responses during network disconnects.
