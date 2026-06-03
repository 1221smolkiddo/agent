from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from .agent import CodingAgent
from .config import Settings
from .models import OpenAIChatClient
from .storage import AgentStorage
from .tools import ToolRegistry

app = typer.Typer(help="A CLI-first coding agent.")


@app.command()
def run(
    task: str = typer.Argument(..., help="The coding task for the agent."),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", help="Workspace directory."),
    model: Optional[str] = typer.Option(None, "--model", help="Model override."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Inspect only; skip writes and shell."),
    max_steps: int = typer.Option(12, "--max-steps", min=1, help="Maximum agent loop steps."),
) -> None:
    """Run the coding agent on a task."""
    settings = Settings()
    selected_model = model or settings.agent_model
    workspace = cwd.resolve()

    client = OpenAIChatClient(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        model=selected_model,
    )
    storage = AgentStorage(settings.agent_db_path)
    agent = CodingAgent(
        cwd=workspace,
        dry_run=dry_run,
        max_steps=max_steps,
        model_client=client,
        tools=ToolRegistry(workspace=workspace, dry_run=dry_run),
        storage=storage,
    )

    result = agent.run(task)
    typer.echo(result)


@app.command()
def history(limit: int = typer.Option(10, "--limit", min=1, help="Number of runs to show.")) -> None:
    """Show recent agent runs."""
    storage = AgentStorage(Settings().agent_db_path)
    rows = storage.recent_runs(limit)
    if not rows:
        typer.echo("No runs recorded yet.")
        return

    for row in rows:
        typer.echo(f"{row['id']} | {row['created_at']} | {row['model']} | {row['task']}")
