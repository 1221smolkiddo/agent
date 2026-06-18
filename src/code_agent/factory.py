from __future__ import annotations

from pathlib import Path
from collections.abc import Callable

from .agent import CodingAgent
from .config import Settings
from .models import OpenAICompatibleChatClient
from .storage import AgentStorage
from .status import StatusReporter
from .tools import ToolRegistry


def create_agent(
    settings: Settings,
    cwd: Path,
    model: str | None,
    dry_run: bool,
    max_steps: int,
    max_failures: int | None = None,
    approval_callback: Callable[[str, str], bool] | None = None,
    reporter: StatusReporter | None = None,
    stream_model: bool | None = None,
) -> CodingAgent:
    workspace = cwd.resolve()
    selected_model = model or settings.agent_model
    client = OpenAICompatibleChatClient(
        api_key=settings.model_api_key,
        base_url=settings.model_base_url,
        model=selected_model,
        max_tokens=settings.agent_max_tokens,
        default_headers=settings.model_headers,
    )
    storage = AgentStorage(settings.agent_db_path)
    return CodingAgent(
        cwd=workspace,
        dry_run=dry_run,
        max_steps=max_steps,
        max_failures=max_failures or settings.agent_max_failures,
        model_client=client,
        tools=ToolRegistry(
            workspace=workspace,
            dry_run=dry_run,
            approval_callback=approval_callback,
        ),
        storage=storage,
        reporter=reporter,
        stream_model=settings.agent_stream if stream_model is None else stream_model,
    )
