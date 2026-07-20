from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .extensions import DynamicToolRegistry, ToolMetadata
from .schema import ToolResult


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    command: tuple[str, ...]
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    timeout_seconds: float = 30.0
    reconnect_attempts: int = 2
    auth_env_keys: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()


class McpError(RuntimeError):
    pass


class McpStdioClient:
    """Minimal MCP JSON-RPC client with lifecycle, discovery, caching, and reconnect."""

    def __init__(self, config: McpServerConfig) -> None:
        self.config = config
        self.process: subprocess.Popen[str] | None = None
        self._request_id = 0
        self._lock = threading.RLock()
        self._cache: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}
        self.server_info: dict[str, Any] = {}
        self._responses: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self._reader: threading.Thread | None = None

    @property
    def connected(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def connect(self) -> dict[str, Any]:
        if not self.config.enabled:
            raise McpError(f"MCP server {self.config.name} is disabled.")
        if self.connected:
            return {"capabilities": self.capabilities, "serverInfo": self.server_info}
        if not self.config.command:
            raise McpError(f"MCP server {self.config.name} has no command.")
        self.process = subprocess.Popen(
            list(self.config.command),
            cwd=self.config.cwd,
            env=dict(self.config.env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            shell=False,
        )
        self._responses = queue.Queue()
        self._reader = threading.Thread(
            target=self._read_loop,
            args=(self.process,),
            name=f"agent47-mcp-{self.config.name}",
            daemon=True,
        )
        self._reader.start()
        response = self.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {"roots": {"listChanged": True}, "sampling": {}},
                "clientInfo": {"name": "agent47", "version": "0.1.0"},
            },
            reconnect=False,
        )
        self.capabilities = dict(response.get("capabilities") or {})
        self.server_info = dict(response.get("serverInfo") or {})
        self.notify("notifications/initialized", {})
        return response

    def close(self) -> None:
        process = self.process
        self.process = None
        self._cache.clear()
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()

    def request(self, method: str, params: dict[str, Any] | None = None, *, reconnect: bool = True) -> dict[str, Any]:
        attempts = self.config.reconnect_attempts + 1 if reconnect else 1
        last_error: Exception | None = None
        for _attempt in range(attempts):
            try:
                if not self.connected:
                    if reconnect:
                        self.connect()
                    else:
                        raise McpError(f"MCP server {self.config.name} is not connected.")
                with self._lock:
                    self._request_id += 1
                    request_id = self._request_id
                    self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
                    response = self._read_response(request_id)
                if "error" in response:
                    raise McpError(f"MCP {method} failed: {response['error']}")
                result = response.get("result", {})
                return result if isinstance(result, dict) else {"value": result}
            except (OSError, EOFError, BrokenPipeError, McpError) as exc:
                last_error = exc
                self.close()
                if not reconnect:
                    break
                time.sleep(0.1)
        raise McpError(f"MCP request {method} failed after {attempts} attempt(s): {last_error}")

    def notify(self, method: str, params: dict[str, Any]) -> None:
        if not self.connected:
            raise McpError(f"MCP server {self.config.name} is not connected.")
        with self._lock:
            self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def list_tools(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        return self._cached_list("tools", "tools/list", "tools", refresh)

    def list_resources(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        return self._cached_list("resources", "resources/list", "resources", refresh)

    def list_prompts(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        return self._cached_list("prompts", "prompts/list", "prompts", refresh)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.config.allowed_tools and name not in self.config.allowed_tools:
            raise McpError(f"MCP tool {name} is not allowed for server {self.config.name}.")
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def read_resource(self, uri: str) -> dict[str, Any]:
        return self.request("resources/read", {"uri": uri})

    def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("prompts/get", {"name": name, "arguments": arguments or {}})

    def subscribe_resource(self, uri: str) -> dict[str, Any]:
        return self.request("resources/subscribe", {"uri": uri})

    def health(self) -> tuple[bool, str]:
        if not self.connected:
            return False, "disconnected"
        return True, f"connected to {self.server_info.get('name', self.config.name)}"

    def register_tools(self, registry: DynamicToolRegistry) -> list[str]:
        registered: list[str] = []
        for item in self.list_tools(refresh=True):
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            qualified = _safe_name(name)
            registry.register(
                ToolMetadata(
                    name=qualified,
                    namespace=f"mcp-{_safe_name(self.config.name)}",
                    description=str(item.get("description") or f"MCP tool {name}"),
                    version="1.0.0",
                    aliases=(f"{self.config.name}:{name}",),
                    permissions=("mcp.resource", "external.api"),
                    input_schema=dict(item.get("inputSchema") or {}),
                    source=f"mcp:{self.config.name}",
                ),
                lambda arguments, tool_name=name: _mcp_result(self.call_tool(tool_name, arguments)),
                health_check=self.health,
                replace=True,
            )
            registered.append(f"mcp-{_safe_name(self.config.name)}.{qualified}")
        return registered

    def _cached_list(self, cache_key: str, method: str, result_key: str, refresh: bool) -> list[dict[str, Any]]:
        if not refresh and cache_key in self._cache:
            return list(self._cache[cache_key])
        result = self.request(method)
        items = [item for item in result.get(result_key, []) if isinstance(item, dict)]
        self._cache[cache_key] = items
        return list(items)

    def _write(self, payload: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise McpError("MCP stdin is unavailable.")
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def _read_response(self, request_id: int) -> dict[str, Any]:
        deadline = time.monotonic() + self.config.timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpError(f"MCP request timed out after {self.config.timeout_seconds:g}s.")
            try:
                payload = self._responses.get(timeout=remaining)
            except queue.Empty as exc:
                raise McpError(
                    f"MCP request timed out after {self.config.timeout_seconds:g}s."
                ) from exc
            if isinstance(payload, BaseException):
                raise EOFError(str(payload)) from payload
            if payload.get("id") == request_id:
                return payload

    def _read_loop(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            self._responses.put(EOFError("MCP stdout is unavailable."))
            return
        try:
            for line in process.stdout:
                if line.strip():
                    self._responses.put(json.loads(line))
            self._responses.put(EOFError("MCP server closed stdout."))
        except (OSError, ValueError) as exc:
            self._responses.put(exc)


class McpManager:
    def __init__(self, registry: DynamicToolRegistry) -> None:
        self.registry = registry
        self.clients: dict[str, McpStdioClient] = {}

    def add(self, config: McpServerConfig) -> McpStdioClient:
        if config.name in self.clients:
            raise ValueError(f"MCP server already configured: {config.name}")
        client = McpStdioClient(config)
        self.clients[config.name] = client
        return client

    def start(self, name: str) -> dict[str, Any]:
        client = self.clients[name]
        handshake = client.connect()
        tools = client.register_tools(self.registry)
        return {"server": name, "handshake": handshake, "tools": tools}

    def start_enabled(self) -> list[dict[str, Any]]:
        started: list[dict[str, Any]] = []
        for name, client in sorted(self.clients.items()):
            if client.config.enabled:
                started.append(self.start(name))
        return started

    def stop(self, name: str) -> None:
        client = self.clients[name]
        for item in list(self.registry.discover(namespace=f"mcp-{_safe_name(name)}")):
            self.registry.unregister(str(item["name"]))
        client.close()

    def close(self) -> None:
        for name in list(self.clients):
            self.stop(name)

    def status(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "connected": client.connected,
                "capabilities": client.capabilities,
                "server_info": client.server_info,
                "health": client.health(),
            }
            for name, client in sorted(self.clients.items())
        ]


def _safe_name(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")
    return cleaned[:64] or "tool"


def _mcp_result(result: dict[str, Any]) -> ToolResult:
    content = result.get("content", [])
    output = "\n".join(
        str(item.get("text") or item.get("resource") or item)
        for item in content
        if isinstance(item, dict)
    )
    return ToolResult(ok=not bool(result.get("isError")), output=output or json.dumps(result), metadata=result)
