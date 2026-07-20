from __future__ import annotations

import inspect
import re
import threading
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from .schema import ToolResult


SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")
NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
Permission = Literal[
    "file.read", "file.write", "file.delete", "shell.execute", "git.write",
    "network.access", "mcp.resource", "external.api", "secrets.read", "plugin.execute",
]


@dataclass(frozen=True)
class ToolMetadata:
    name: str
    description: str
    version: str = "1.0.0"
    namespace: str = "core"
    aliases: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    permissions: tuple[Permission, ...] = ()
    input_schema: dict[str, Any] = field(default_factory=dict)
    source: str = "builtin"
    streaming: bool = False

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.name}"

    def validate(self) -> None:
        if not NAME.fullmatch(self.name) or not NAME.fullmatch(self.namespace):
            raise ValueError("Tool names and namespaces must use lowercase letters, digits, _ or -.")
        if not self.description.strip():
            raise ValueError(f"Tool {self.qualified_name} requires a description.")
        if not SEMVER.fullmatch(self.version):
            raise ValueError(f"Tool {self.qualified_name} has invalid semantic version {self.version!r}.")
        if len(set(self.aliases)) != len(self.aliases):
            raise ValueError(f"Tool {self.qualified_name} contains duplicate aliases.")


ToolHandler = Callable[[dict[str, Any]], ToolResult | str | dict[str, Any]]
HealthCheck = Callable[[], tuple[bool, str]]
PermissionCheck = Callable[[str, tuple[Permission, ...], str], bool]


@dataclass
class RegisteredTool:
    metadata: ToolMetadata
    handler: ToolHandler
    health_check: HealthCheck | None = None
    available: bool = True
    unavailable_reason: str = ""
    generation: int = 1
    registered_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def health(self) -> dict[str, Any]:
        if not self.available:
            return {"ok": False, "detail": self.unavailable_reason or "unavailable"}
        if self.health_check is None:
            return {"ok": True, "detail": "registered"}
        try:
            ok, detail = self.health_check()
        except Exception as exc:
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        return {"ok": bool(ok), "detail": str(detail)}


class DynamicToolRegistry:
    """Thread-safe runtime registry shared by built-ins, plugins, and MCP servers."""

    def __init__(self, permission_check: PermissionCheck | None = None) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._aliases: dict[str, str] = {}
        self._lock = threading.RLock()
        self.permission_check = permission_check

    def register(
        self,
        metadata: ToolMetadata,
        handler: ToolHandler,
        *,
        health_check: HealthCheck | None = None,
        replace: bool = False,
    ) -> RegisteredTool:
        metadata.validate()
        if not callable(handler):
            raise TypeError("Tool handler must be callable.")
        key = metadata.qualified_name
        with self._lock:
            if key in self._tools and not replace:
                raise ValueError(f"Tool already registered: {key}")
            missing = [item for item in metadata.dependencies if self.resolve(item) is None]
            if missing:
                raise ValueError(f"Tool {key} has unavailable dependencies: {', '.join(missing)}")
            collisions = [alias for alias in metadata.aliases if alias in self._aliases and self._aliases[alias] != key]
            if collisions:
                raise ValueError(f"Tool alias collision: {', '.join(collisions)}")
            previous = self._tools.get(key)
            tool = RegisteredTool(
                metadata=metadata,
                handler=handler,
                health_check=health_check,
                generation=(previous.generation + 1 if previous else 1),
            )
            self._tools[key] = tool
            for alias in metadata.aliases:
                self._aliases[alias] = key
            self._aliases[metadata.name] = key
            return tool

    def unregister(self, name: str) -> bool:
        with self._lock:
            tool = self.resolve(name)
            if tool is None:
                return False
            key = tool.metadata.qualified_name
            del self._tools[key]
            self._aliases = {alias: target for alias, target in self._aliases.items() if target != key}
            return True

    def reload(self, metadata: ToolMetadata, handler: ToolHandler, *, health_check: HealthCheck | None = None) -> RegisteredTool:
        return self.register(metadata, handler, health_check=health_check, replace=True)

    def resolve(self, name: str) -> RegisteredTool | None:
        key = self._aliases.get(name, name)
        return self._tools.get(key)

    def discover(self, *, namespace: str | None = None, permission: Permission | None = None) -> list[dict[str, Any]]:
        with self._lock:
            tools = sorted(self._tools.values(), key=lambda item: item.metadata.qualified_name)
        return [
            {
                "name": tool.metadata.qualified_name,
                "description": tool.metadata.description,
                "version": tool.metadata.version,
                "aliases": list(tool.metadata.aliases),
                "dependencies": list(tool.metadata.dependencies),
                "permissions": list(tool.metadata.permissions),
                "input_schema": tool.metadata.input_schema,
                "source": tool.metadata.source,
                "streaming": tool.metadata.streaming,
                "available": tool.available,
                "generation": tool.generation,
                "health": tool.health(),
            }
            for tool in tools
            if (namespace is None or tool.metadata.namespace == namespace)
            and (permission is None or permission in tool.metadata.permissions)
        ]

    def invoke(self, name: str, arguments: dict[str, Any], *, actor: str = "primary") -> ToolResult:
        tool = self.resolve(name)
        if tool is None:
            return ToolResult(ok=False, output=f"Dynamic tool is not registered: {name}")
        health = tool.health()
        if not health["ok"]:
            return ToolResult(ok=False, output=f"Tool {name} is unavailable: {health['detail']}")
        error = _validate_arguments(arguments, tool.metadata.input_schema)
        if error:
            return ToolResult(ok=False, output=f"Invalid arguments for {name}: {error}")
        if self.permission_check and not self.permission_check(
            tool.metadata.qualified_name, tool.metadata.permissions, actor
        ):
            return ToolResult(ok=False, output=f"Permission denied for dynamic tool {name}.")
        try:
            value = tool.handler(dict(arguments))
        except Exception as exc:
            return ToolResult(ok=False, output=f"Tool {name} failed: {type(exc).__name__}: {exc}")
        if isinstance(value, ToolResult):
            return value
        if isinstance(value, dict):
            return ToolResult(ok=True, output=str(value.get("output", value)), metadata=value)
        return ToolResult(ok=True, output=str(value))


def _validate_arguments(arguments: dict[str, Any], schema: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return "arguments must be an object"
    required = schema.get("required", []) if isinstance(schema, dict) else []
    missing = [str(key) for key in required if key not in arguments]
    if missing:
        return "missing required fields: " + ", ".join(missing)
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    if isinstance(properties, dict) and schema.get("additionalProperties") is False:
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            return "unknown fields: " + ", ".join(unknown)
    return ""


HookMode = Literal["sync", "async"]
HookHandler = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class HookSubscription:
    event: str
    handler: HookHandler
    priority: int = 100
    mode: HookMode = "sync"
    source: str = "core"


class LifecycleHooks:
    EVENTS = frozenset({
        "agent.startup", "agent.shutdown", "session.start", "session.end",
        "planning.before", "planning.after", "tool.before", "tool.after",
        "shell.before", "shell.after", "edit.before", "edit.after",
        "verification.before", "verification.after", "response.before", "response.after",
        "approval", "cancellation", "error", "checkpoint",
    })

    def __init__(self) -> None:
        self._subscriptions: dict[str, list[HookSubscription]] = defaultdict(list)
        self._lock = threading.RLock()

    def subscribe(self, subscription: HookSubscription) -> None:
        if subscription.event not in self.EVENTS:
            raise ValueError(f"Unsupported lifecycle event: {subscription.event}")
        with self._lock:
            self._subscriptions[subscription.event].append(subscription)
            self._subscriptions[subscription.event].sort(key=lambda item: item.priority)

    def unsubscribe_source(self, source: str) -> int:
        removed = 0
        with self._lock:
            for event, items in self._subscriptions.items():
                retained = [item for item in items if item.source != source]
                removed += len(items) - len(retained)
                self._subscriptions[event] = retained
        return removed

    def emit(self, event: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if event not in self.EVENTS:
            raise ValueError(f"Unsupported lifecycle event: {event}")
        with self._lock:
            subscriptions = list(self._subscriptions[event])
        results: list[dict[str, Any]] = []
        threads: list[threading.Thread] = []

        def invoke(subscription: HookSubscription) -> None:
            try:
                value = subscription.handler(dict(payload))
                if inspect.isawaitable(value):
                    raise TypeError("Coroutine hooks require an async host; use a synchronous wrapper.")
                results.append({"source": subscription.source, "ok": True, "result": value})
            except Exception as exc:
                results.append({"source": subscription.source, "ok": False, "error": f"{type(exc).__name__}: {exc}"})

        for subscription in subscriptions:
            if subscription.mode == "async":
                thread = threading.Thread(target=invoke, args=(subscription,), daemon=True)
                thread.start()
                threads.append(thread)
            else:
                invoke(subscription)
        for thread in threads:
            thread.join(timeout=5)
        return results
