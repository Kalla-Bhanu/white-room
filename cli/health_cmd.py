from __future__ import annotations

import sqlite3

import typer

from core.health import health_check, sync_models


health_app = typer.Typer(help="Endpoint health and runner status commands.")


@health_app.command("check")
def health_check_cmd(endpoint: str) -> None:
    """Check a local or metadata-only endpoint and persist the result."""
    try:
        result = health_check(endpoint)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except sqlite3.IntegrityError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    status = "reachable" if result.reachable else "unavailable"
    key_state = "present" if result.key_present else "missing"
    typer.echo(
        f"{result.endpoint_class}\t{status}\tkey={key_state}\t"
        f"{result.detail}\t{result.last_checked}"
    )


@health_app.command("sync")
def health_sync_cmd(endpoint: str) -> None:
    """Sync provider models for a live endpoint and persist the catalog."""
    try:
        result = sync_models(endpoint)
    except (ValueError, PermissionError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"{result.endpoint_class}\tsynced={result.models_synced}\t"
        f"last_sync={result.last_model_sync}"
    )
