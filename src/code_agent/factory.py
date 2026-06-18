from __future__ import annotations

from pathlib import Path
from collections.abc import Callable

from .agent import CodingAgent
from .config import Settings
from .model_profiles import resolve_model_profile
from .models import ModelProviderConfig, create_openai_compatible_client
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
    profile: str | None = None,
) -> CodingAgent:
    workspace = cwd.resolve()
    selected_profile = resolve_model_profile(
        profile or settings.agent_profile,
        default_model=model or settings.agent_model,
        max_tokens=settings.agent_max_tokens,
        planner_model=None if model else settings.agent_planner_model,
        coder_model=None if model else settings.agent_coder_model,
        reviewer_model=None if model else settings.agent_reviewer_model,
        fast_model=None if model else settings.agent_fast_model,
    )
    provider = ModelProviderConfig(
        api_key=settings.model_api_key,
        base_url=settings.model_base_url,
        default_headers=settings.model_headers,
    )
    client = create_openai_compatible_client(provider, selected_profile)
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
