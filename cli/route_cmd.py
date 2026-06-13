from __future__ import annotations

import typer

from core.router import classify_dimensions, dry_run_route, load_task, run_route


route_app = typer.Typer(help="Routing commands.")


@route_app.command("classify")
def route_classify(
    project: str = typer.Option(..., "--project"),
    task: int = typer.Option(..., "--task"),
) -> None:
    """Print the deterministic task classification without execution."""
    try:
        loaded_task = load_task(project, task)
        result = classify_dimensions(loaded_task)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"task_type: {result.task_type}")
    typer.echo(f"size: {result.size}")
    typer.echo(f"risk: {result.risk}")
    typer.echo(f"rationale: {result.rationale}")


@route_app.command("run")
def route_run(
    project: str = typer.Option(..., "--project"),
    task: int = typer.Option(..., "--task"),
    mode: str = typer.Option("ask", "--mode"),
    lane_override: str = typer.Option("auto", "--lane-override"),
) -> None:
    """Run the safe auto-router and fall back to suggestions when needed."""
    try:
        result = run_route(project, task, mode=mode, lane_override=None if lane_override == "auto" else lane_override)
    except (RuntimeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"mode: {result.mode}")
    typer.echo(f"endpoint: {result.endpoint_class}")
    typer.echo(f"task_type: {result.classification.task_type}")
    typer.echo(f"size: {result.classification.size}")
    typer.echo(f"risk: {result.classification.risk}")
    typer.echo(f"rationale: {result.rationale}")
    typer.echo(f"preview: {result.preview}")
    if result.approval_gate_id is not None:
        typer.echo(f"approval_gate: {result.approval_gate_id}")
    if result.approval_status is not None:
        typer.echo(f"approval_status: {result.approval_status}")
    if result.approval_message:
        typer.echo(f"approval_message: {result.approval_message}")


@route_app.command("dry-run")
def route_dry_run(
    project: str = typer.Option(..., "--project"),
    task: int = typer.Option(..., "--task"),
    mode: str = typer.Option("ask", "--mode"),
    lane_override: str = typer.Option("auto", "--lane-override"),
) -> None:
    """Print the route suggestion and request preview without execution."""
    try:
        result = dry_run_route(project, task, mode=mode, lane_override=None if lane_override == "auto" else lane_override)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"endpoint: {result.endpoint_class}")
    typer.echo(f"requires_approval: {result.requires_approval}")
    typer.echo(f"rationale: {result.rationale}")
    typer.echo(f"request: {result.request}")
    typer.echo(f"preview: {result.preview}")
