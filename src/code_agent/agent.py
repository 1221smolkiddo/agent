from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from .models import ChatMessage, ModelClient
from .prompts import system_prompt
from .schema import AgentAction, FinalAction, ToolResult
from .storage import AgentStorage
from .tools import ToolRegistry

ACTION_ADAPTER = TypeAdapter(AgentAction)


class CodingAgent:
    def __init__(
        self,
        cwd: Path,
        dry_run: bool,
        max_steps: int,
        model_client: ModelClient,
        tools: ToolRegistry,
        storage: AgentStorage,
    ) -> None:
        self.cwd = cwd
        self.dry_run = dry_run
        self.max_steps = max_steps
        self.model_client = model_client
        self.tools = tools
        self.storage = storage

    def run(self, task: str) -> str:
        run_id = self.storage.create_run(task=task, model=self.model_client.model, cwd=self.cwd)
        messages: list[ChatMessage] = [
            {"role": "system", "content": system_prompt(self.cwd, self.dry_run)},
            {"role": "user", "content": task},
        ]

        for step in range(1, self.max_steps + 1):
            response = self.model_client.complete(messages)
            action = self._parse_action(response)
            self.storage.add_step(run_id, "assistant", action.model_dump())

            if isinstance(action, FinalAction):
                return action.message

            result = self._run_tool(action)
            tool_payload = {
                "type": "tool_result",
                "step": step,
                "ok": result.ok,
                "output": result.output,
            }
            self.storage.add_step(run_id, "tool", tool_payload)
            messages.append({"role": "assistant", "content": action.model_dump_json()})
            messages.append({"role": "user", "content": json.dumps(tool_payload)})

        return f"Stopped after {self.max_steps} steps. Increase --max-steps if the task needs more work."

    def _parse_action(self, raw: str) -> AgentAction:
        try:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            data: Any = json.loads(raw[start:end])
            return ACTION_ADAPTER.validate_python(data)
        except (ValueError, json.JSONDecodeError, ValidationError):
            return FinalAction(type="final", message=raw.strip())

    def _run_tool(self, action: AgentAction) -> ToolResult:
        try:
            return self.tools.run(action)
        except Exception as exc:
            return ToolResult(ok=False, output=str(exc))
