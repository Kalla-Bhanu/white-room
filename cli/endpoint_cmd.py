from __future__ import annotations

import sqlite3

import typer

from core.endpoints import add_endpoint, list_endpoints, update_endpoint


endpoint_app = typer.Typer(help="Metadata-only endpoint registry commands.")


@endpoint_app.command("add")
def endpoint_add(
    name: str,
    endpoint_class: str = typer.Option(..., "--class", "--endpoint-class"),
    tier: str = typer.Option(..., "--tier"),
    base_url: str = typer.Option("metadata-only", "--base-url"),
    capabilities: str = typer.Option("unknown", "--capabilities"),
    daily_limit: str = typer.Option("unknown", "--daily-limit"),
    window_limit: str = typer.Option("unknown", "--window-limit"),
    status_value: str = typer.Option("metadata-only", "--status"),
    profile_id: int | None = typer.Option(None, "--profile-id"),
    model_name: str | None = typer.Option(None, "--model-name"),
    supports_streaming: bool | None = typer.Option(None, "--supports-streaming/--no-supports-streaming"),
    supports_tools: bool | None = typer.Option(None, "--supports-tools/--no-supports-tools"),
    supports_json: bool | None = typer.Option(None, "--supports-json/--no-supports-json"),
    input_cost_per_1m: float | None = typer.Option(None, "--input-cost-per-1m"),
    output_cost_per_1m: float | None = typer.Option(None, "--output-cost-per-1m"),
    rate_limit_notes: str | None = typer.Option(None, "--rate-limit-notes"),
    disabled_reason: str | None = typer.Option(None, "--disabled-reason"),
) -> None:
    """Add a metadata-only endpoint record. This never calls the endpoint."""
    try:
        add_endpoint(
            name=name,
            endpoint_class=endpoint_class,
            tier=tier,
            base_url=base_url,
            capabilities=capabilities,
            daily_limit=daily_limit,
            window_limit=window_limit,
            status=status_value,
            profile_id=profile_id,
            model_name=model_name,
            supports_streaming=supports_streaming,
            supports_tools=supports_tools,
            supports_json=supports_json,
            input_cost_per_1m=input_cost_per_1m,
            output_cost_per_1m=output_cost_per_1m,
            rate_limit_notes=rate_limit_notes,
            disabled_reason=disabled_reason,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"endpoint '{name}' added as {endpoint_class} ({tier})")


@endpoint_app.command("edit")
def endpoint_edit(
    name: str,
    endpoint_class: str | None = typer.Option(None, "--class", "--endpoint-class"),
    tier: str | None = typer.Option(None, "--tier"),
    base_url: str | None = typer.Option(None, "--base-url"),
    capabilities: str | None = typer.Option(None, "--capabilities"),
    daily_limit: str | None = typer.Option(None, "--daily-limit"),
    window_limit: str | None = typer.Option(None, "--window-limit"),
    status_value: str | None = typer.Option(None, "--status"),
    profile_id: int | None = typer.Option(None, "--profile-id"),
    model_name: str | None = typer.Option(None, "--model-name"),
    supports_streaming: bool | None = typer.Option(None, "--supports-streaming/--no-supports-streaming"),
    supports_tools: bool | None = typer.Option(None, "--supports-tools/--no-supports-tools"),
    supports_json: bool | None = typer.Option(None, "--supports-json/--no-supports-json"),
    input_cost_per_1m: float | None = typer.Option(None, "--input-cost-per-1m"),
    output_cost_per_1m: float | None = typer.Option(None, "--output-cost-per-1m"),
    rate_limit_notes: str | None = typer.Option(None, "--rate-limit-notes"),
    disabled_reason: str | None = typer.Option(None, "--disabled-reason"),
) -> None:
    """Update a metadata-only endpoint record without calling the endpoint."""
    try:
        update_endpoint(
            name=name,
            endpoint_class=endpoint_class,
            tier=tier,
            base_url=base_url,
            capabilities=capabilities,
            daily_limit=daily_limit,
            window_limit=window_limit,
            status=status_value,
            profile_id=profile_id,
            model_name=model_name,
            supports_streaming=supports_streaming,
            supports_tools=supports_tools,
            supports_json=supports_json,
            input_cost_per_1m=input_cost_per_1m,
            output_cost_per_1m=output_cost_per_1m,
            rate_limit_notes=rate_limit_notes,
            disabled_reason=disabled_reason,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"endpoint '{name}' updated")


@endpoint_app.command("list")
def endpoint_list() -> None:
    """List metadata-only endpoint records."""
    endpoints = list_endpoints()
    if not endpoints:
        typer.echo("no endpoints")
        return

    for endpoint in endpoints:
        typer.echo(
            f"{endpoint.name}\t{endpoint.endpoint_class}\tprofile={endpoint.profile_name}"
            f"#{'' if endpoint.profile_id is None else endpoint.profile_id}\t"
            f"override=model={endpoint.model_name or 'n/a'};"
            f"stream={int(endpoint.supports_streaming)};"
            f"tools={int(endpoint.supports_tools)};"
            f"json={int(endpoint.supports_json)}\t"
            f"{endpoint.tier}\t{endpoint.status}\t{endpoint.base_url}\t"
            f"{endpoint.input_cost_per_1m if endpoint.input_cost_per_1m is not None else 'n/a'}\t"
            f"{endpoint.output_cost_per_1m if endpoint.output_cost_per_1m is not None else 'n/a'}"
        )
