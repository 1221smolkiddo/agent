from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .extensions import DynamicToolRegistry, LifecycleHooks, ToolMetadata
from .mcp import McpManager, McpServerConfig, ReloadResult
from .orchestration import AgentCatalog
from .plugins import PluginManager
from .skills import InstructionResolver, SkillCatalog


@dataclass
class PlatformRuntime:
    workspace: Path
    tools: DynamicToolRegistry
    hooks: LifecycleHooks
    instructions: InstructionResolver
    skills: SkillCatalog
    agents: AgentCatalog
    plugins: PluginManager
    mcp: McpManager

    @classmethod
    def create(
        cls,
        workspace: Path,
        *,
        trust_workspace_extensions: bool = False,
    ) -> "PlatformRuntime":
        root = workspace.resolve()
        tools = DynamicToolRegistry()
        tools.register(
            ToolMetadata(
                name="list-tools",
                namespace="platform",
                description="Discover runtime tools, schemas, permissions, versions, and health.",
                aliases=("list_dynamic_tools",),
                input_schema={"type": "object", "additionalProperties": False},
            ),
            lambda _arguments: {"tools": tools.discover()},
        )
        hooks = LifecycleHooks()
        skills = SkillCatalog.for_workspace(root)
        agents = AgentCatalog()
        runtime = cls(
            root,
            tools,
            hooks,
            InstructionResolver.for_workspace(root),
            skills,
            agents,
            PluginManager(tools, skills, agents, hooks),
            McpManager(tools),
        )
        if trust_workspace_extensions:
            runtime._discover_plugins()
            runtime.configure_mcp()
            runtime.mcp.start_enabled()
        runtime.hooks.emit("agent.startup", {"workspace": str(root)})
        return runtime

    def task_context(self, task: str, *, agent: str = "primary") -> str:
        sections: list[str] = []
        instructions = self.instructions.render(task=task, agent=agent)
        if instructions:
            sections.append("Active scoped instructions:\n" + instructions)
        matched = self.skills.match(task)
        if matched:
            sections.append("Active skills:\n\n" + "\n\n".join(skill.context() for skill in matched))
        return "\n\n".join(sections)

    def capabilities(self) -> dict[str, Any]:
        return {
            "tools": self.tools.discover(),
            "skills": [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "risk_level": skill.risk_level,
                    "source": skill.source,
                    "allowed_tools": list(skill.allowed_tools),
                }
                for skill in self.skills.discover()
            ],
            "agents": [
                {
                    "name": profile.name,
                    "description": profile.description,
                    "capabilities": list(profile.capabilities),
                    "risk_profile": profile.risk_profile,
                    "token_budget": profile.token_budget,
                    "execution_budget": profile.execution_budget,
                }
                for profile in self.agents.discover()
            ],
            "plugins": [
                {"name": plugin.name, "version": plugin.version, "description": plugin.description}
                for plugin in self.plugins.loaded.values()
            ],
            "mcp": self.mcp.status(),
        }

    def close(self) -> None:
        self.hooks.emit("agent.shutdown", {"workspace": str(self.workspace)})
        self.mcp.close()

    def _discover_plugins(self) -> None:
        manifests = self.plugins.discover([self.workspace / ".agents" / "plugins"])
        for path in manifests:
            self.plugins.load(path)

    def _parse_mcp_config(self) -> dict[str, McpServerConfig]:
        path = self.workspace / ".agents" / "mcp.json"
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        servers = payload.get("servers", {}) if isinstance(payload, dict) else {}
        if not isinstance(servers, dict):
            raise ValueError(".agents/mcp.json servers must be an object.")
        
        configs: dict[str, McpServerConfig] = {}
        for name, raw in servers.items():
            if not isinstance(raw, dict):
                continue
            auth_keys = tuple(str(item) for item in raw.get("auth_env_keys", []))
            env = {
                key: os.environ[key]
                for key in auth_keys
                if key in os.environ
            }
            for key in ["PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE", "TEMP", "TMP"]:
                if key in os.environ:
                    env[key] = os.environ[key]
            configs[str(name)] = McpServerConfig(
                name=str(name),
                command=tuple(str(item) for item in raw.get("command", [])),
                cwd=self.workspace,
                env=env,
                enabled=bool(raw.get("enabled", True)),
                timeout_seconds=float(raw.get("timeout_seconds", 30)),
                reconnect_attempts=int(raw.get("reconnect_attempts", 2)),
                auth_env_keys=auth_keys,
                allowed_tools=tuple(str(item) for item in raw.get("allowed_tools", [])),
            )
        return configs

    def configure_mcp(self) -> None:
        configs = self._parse_mcp_config()
        for config in configs.values():
            self.mcp.add(config)

    def start_mcp(self, name: str) -> dict[str, Any]:
        return self.mcp.start(name)

    def stop_mcp(self, name: str) -> bool:
        return self.mcp.stop(name)

    def restart_mcp(self, name: str) -> dict[str, Any]:
        return self.mcp.restart(name)

    def reload_mcp(self) -> ReloadResult:
        configs = self._parse_mcp_config()
        return self.mcp.reconcile(configs)
