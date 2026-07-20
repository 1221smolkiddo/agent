from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .extensions import DynamicToolRegistry, LifecycleHooks, ToolMetadata
from .orchestration import AgentCatalog, AgentProfile
from .schema import ToolResult
from .skills import SkillCatalog


PLUGIN_API_VERSION = "1"
SAFE_PLUGIN_ENV = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE"}


@dataclass(frozen=True)
class PluginManifest:
    name: str
    version: str
    description: str
    api_version: str
    root: Path
    dependencies: tuple[str, ...]
    permissions: tuple[str, ...]
    tools: tuple[dict[str, Any], ...]
    skills_directory: str = "skills"
    agents: tuple[dict[str, Any], ...] = ()

    @classmethod
    def load(cls, path: Path) -> "PluginManifest":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Plugin manifest must be an object.")
        required = [key for key in ["name", "version", "description", "api_version"] if not payload.get(key)]
        if required:
            raise ValueError("Plugin manifest is missing: " + ", ".join(required))
        if str(payload["api_version"]) != PLUGIN_API_VERSION:
            raise ValueError(
                f"Plugin {payload['name']} requires API {payload['api_version']}; Agent47 supports {PLUGIN_API_VERSION}."
            )
        return cls(
            name=str(payload["name"]),
            version=str(payload["version"]),
            description=str(payload["description"]),
            api_version=str(payload["api_version"]),
            root=path.parent,
            dependencies=tuple(str(item) for item in payload.get("dependencies", [])),
            permissions=tuple(str(item) for item in payload.get("permissions", [])),
            tools=tuple(item for item in payload.get("tools", []) if isinstance(item, dict)),
            skills_directory=str(payload.get("skills_directory") or "skills"),
            agents=tuple(item for item in payload.get("agents", []) if isinstance(item, dict)),
        )


class PluginManager:
    def __init__(self, registry: DynamicToolRegistry, skills: SkillCatalog, agents: AgentCatalog, hooks: LifecycleHooks) -> None:
        self.registry = registry
        self.skills = skills
        self.agents = agents
        self.hooks = hooks
        self.loaded: dict[str, PluginManifest] = {}

    def load(self, path: Path) -> PluginManifest:
        manifest = PluginManifest.load(path)
        if manifest.name in self.loaded:
            raise ValueError(f"Plugin already loaded: {manifest.name}")
        missing = [name for name in manifest.dependencies if name not in self.loaded]
        if missing:
            raise ValueError(f"Plugin {manifest.name} has missing dependencies: {', '.join(missing)}")
        namespace = f"plugin-{manifest.name}"
        try:
            for raw in manifest.tools:
                name = str(raw.get("name") or "")
                command = tuple(str(item) for item in raw.get("command", []))
                if not name or not command:
                    raise ValueError(f"Plugin {manifest.name} has an invalid tool declaration.")
                permissions = tuple(str(item) for item in raw.get("permissions", manifest.permissions))
                registry_permissions = tuple(item for item in permissions if item in {
                    "file.read", "file.write", "file.delete", "shell.execute", "git.write",
                    "network.access", "mcp.resource", "external.api", "secrets.read", "plugin.execute",
                })
                self.registry.register(
                    ToolMetadata(
                        name=name,
                        namespace=namespace,
                        description=str(raw.get("description") or name),
                        version=str(raw.get("version") or manifest.version),
                        aliases=tuple(str(item) for item in raw.get("aliases", [])),
                        permissions=registry_permissions,  # type: ignore[arg-type]
                        input_schema=dict(raw.get("input_schema") or {}),
                        source=f"plugin:{manifest.name}",
                    ),
                    _command_handler(manifest.root, command, float(raw.get("timeout_seconds", 30))),
                )
            self.skills.load_directory(
                manifest.root / manifest.skills_directory, source=f"plugin:{manifest.name}"
            )
            for raw in manifest.agents:
                profile = AgentProfile(
                    name=str(raw["name"]),
                    description=str(raw.get("description") or raw["name"]),
                    system_prompt=str(raw.get("system_prompt") or ""),
                    capabilities=tuple(str(item) for item in raw.get("capabilities", [])),
                    allowed_tools=tuple(str(item) for item in raw.get("allowed_tools", [])),
                    permissions=tuple(str(item) for item in raw.get("permissions", [])),
                    token_budget=int(raw.get("token_budget", 8000)),
                    execution_budget=int(raw.get("execution_budget", 12)),
                    timeout_seconds=float(raw.get("timeout_seconds", 120)),
                    risk_profile=str(raw.get("risk_profile", "medium")),  # type: ignore[arg-type]
                )
                self.agents.register(profile)
        except Exception:
            self._remove_source(manifest)
            raise
        self.loaded[manifest.name] = manifest
        return manifest

    def unload(self, name: str) -> bool:
        manifest = self.loaded.pop(name, None)
        if manifest is None:
            return False
        self._remove_source(manifest)
        return True

    def _remove_source(self, manifest: PluginManifest) -> None:
        for item in list(self.registry.discover(namespace=f"plugin-{manifest.name}")):
            self.registry.unregister(str(item["name"]))
        self.hooks.unsubscribe_source(f"plugin:{manifest.name}")
        for skill in list(self.skills.discover()):
            if skill.source == f"plugin:{manifest.name}":
                self.skills.unregister(skill.name)
        for raw in manifest.agents:
            if raw.get("name"):
                self.agents.unregister(str(raw["name"]))

    def discover(self, roots: list[Path]) -> list[Path]:
        return sorted(path for root in roots if root.exists() for path in root.glob("*/plugin.json"))


def _command_handler(root: Path, command: tuple[str, ...], timeout_seconds: float):
    def invoke(arguments: dict[str, Any]) -> ToolResult:
        executable = (root / command[0]).resolve() if not Path(command[0]).is_absolute() else Path(command[0]).resolve()
        try:
            executable.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("Plugin executable must remain inside the plugin directory.") from exc
        env = {key: value for key, value in os.environ.items() if key.upper() in SAFE_PLUGIN_ENV}
        completed = subprocess.run(
            [str(executable), *command[1:]],
            cwd=root,
            input=json.dumps(arguments),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            shell=False,
            env=env,
        )
        output = (completed.stdout or completed.stderr).strip()
        return ToolResult(
            ok=completed.returncode == 0,
            output=output or f"Plugin exited with code {completed.returncode}.",
            metadata={"exit_code": completed.returncode, "command": list(command)},
        )
    return invoke
