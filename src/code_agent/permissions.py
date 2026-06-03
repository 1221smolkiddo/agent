from __future__ import annotations

import typer


def confirm_permission(action: str, detail: str) -> bool:
    typer.echo("")
    typer.echo(f"Permission requested: {action}")
    typer.echo(detail)
    return typer.confirm("Allow this action?", default=False)
