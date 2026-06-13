from __future__ import annotations

import sqlite3
from pathlib import Path

import typer
import uvicorn

from core.bench import (
    list_bench_runs,
    list_fixtures,
    promote_verified_benchmark,
    run_local_benchmark,
    score_fixture_output,
)
from core.codex_lane import export_codex_execution_packet, import_codex_execution_output
from core.manual_lane import export_manual_claude_packet, import_manual_claude_output
from core.memory import append_handoff, list_projects, read_current_status, write_current_status
from core.packets import create_packet
from cli.orchestrate_cmd import run_orchestrate
from cli.endpoint_cmd import endpoint_app
from cli.export_project_cmd import run_export_project
from cli.chat_cmd import chat_app
from cli.health_cmd import health_app
from cli.reindex_cmd import run_reindex
from cli.project_cmd import create_project_with_template, project_app, tasks_app
from cli.route_cmd import route_app
from cli.usage_cmd import usage_app


app = typer.Typer(help="WHITE ROOM deterministic project memory CLI.")
projects_app = typer.Typer(help="Project registry commands.")
handoff_app = typer.Typer(help="Project handoff commands.")
packet_app = typer.Typer(help="Task packet commands.")
bench_app = typer.Typer(help="Benchmark fixture and run commands.")
bench_fixtures_app = typer.Typer(help="Benchmark fixture commands.")
bench_runs_app = typer.Typer(help="Benchmark run commands.")


@app.callback()
def main() -> None:
    """Run WHITE ROOM commands."""


@app.command()
def new(
    name: str,
    template: str = typer.Option("default", "--template"),
) -> None:
    """Create a managed project folder and index it in SQLite."""
    try:
        project = create_project_with_template(name, template=template)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"created project '{project.slug}' at {project.path}")


@projects_app.command("list")
def projects_list() -> None:
    """List managed projects."""
    projects = list_projects()
    if not projects:
        typer.echo("no projects")
        return

    for project in projects:
        typer.echo(
            f"{project.slug}\t{project.status}\t{project.path.relative_to(project.path.parents[1])}"
        )


@app.command()
def reindex() -> None:
    """Rebuild SQLite from project files and the current snapshot."""
    typer.echo(run_reindex())


@app.command()
def export_project(
    slug: str,
    to_path: Path = typer.Option(..., "--to"),
) -> None:
    """Export a project folder as a zip archive."""
    try:
        archive_path = run_export_project(slug, to_path)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"exported {slug} to {archive_path}")


@app.command()
def status(slug: str) -> None:
    """Print the current status brain file for a project."""
    try:
        typer.echo(read_current_status(slug))
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command("status-set")
def status_set(
    slug: str,
    text: str = typer.Option(..., "--text"),
) -> None:
    """Replace the current status brain file for a project."""
    try:
        path = write_current_status(slug, text)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"status updated at {path}")


@app.command()
def orchestrate(
    slug: str,
    step: bool = typer.Option(False, "--step"),
) -> None:
    """Advance exactly one task through the orchestrator."""
    try:
        message = run_orchestrate(slug, step)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(message)


@app.command()
def export(
    manual_for: str = typer.Option(..., "--for"),
    project: str = typer.Option(..., "--project"),
    task: int = typer.Option(..., "--task"),
) -> None:
    """Export a task packet for manual copy/paste."""
    if manual_for not in {"manual_claude", "codex_lb"}:
        typer.echo("only manual_claude and codex_lb exports are supported in this phase", err=True)
        raise typer.Exit(code=1)

    try:
        result = (
            export_manual_claude_packet(project, task)
            if manual_for == "manual_claude"
            else export_codex_execution_packet(project, task)
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"export written to {result.path} (estimated tokens: {result.token_estimate})")


@app.command("import")
def import_(
    manual_from: str = typer.Option(..., "--from"),
    project: str = typer.Option(..., "--project"),
    file: Path = typer.Option(..., "--file", exists=False, readable=True),
    target: str = typer.Option(..., "--target"),
) -> None:
    """Import a manually pasted response into one brain file."""
    if manual_from not in {"manual_claude", "codex_lb"}:
        typer.echo("only manual_claude and codex_lb imports are supported in this phase", err=True)
        raise typer.Exit(code=1)

    try:
        result = (
            import_manual_claude_output(project, file, target)
            if manual_from == "manual_claude"
            else import_codex_execution_output(project, file, target)
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"imported {result.path} and logged handoff at {result.handoff_path}")


@handoff_app.command("add")
def handoff_add(
    slug: str,
    from_worker: str = typer.Option(..., "--from", "--from-worker"),
    to_worker: str = typer.Option(..., "--to", "--to-worker"),
    summary: str = typer.Option(..., "--summary"),
    artifact: list[str] = typer.Option([], "--artifact"),
) -> None:
    """Append a handoff to the project brain and SQLite index."""
    try:
        path = append_handoff(
            slug=slug,
            from_worker=from_worker,
            to_worker=to_worker,
            summary=summary,
            artifact_paths=artifact,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"handoff appended to {path}")


@packet_app.command("new")
def packet_new(
    slug: str,
    title: str = typer.Option(..., "--title"),
    goal: str = typer.Option(..., "--goal"),
    size: str = typer.Option("small", "--size"),
    route: str = typer.Option("deterministic", "--route"),
    expected_output: str = typer.Option("A scoped implementation plus handoff/status update.", "--expected-output"),
    acceptance: str = typer.Option("The task output is present and can be verified locally.", "--acceptance"),
) -> None:
    """Create a copy-paste-ready task packet without calling any model."""
    try:
        packet = create_packet(
            slug=slug,
            title=title,
            goal=goal,
            size_class=size,
            preferred_route=route,
            expected_output=expected_output,
            acceptance=acceptance,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"created packet {packet.packet_id} for task {packet.task_id} at {packet.path} "
        f"(estimated tokens: {packet.token_estimate})"
    )


@bench_fixtures_app.command("list")
def bench_fixtures_list() -> None:
    """List available benchmark fixtures without scoring or model calls."""
    fixtures = list_fixtures()
    if not fixtures:
        typer.echo("no fixtures")
        return

    for fixture in fixtures:
        typer.echo(
            f"{fixture.task_type}\t{fixture.input_path.relative_to(fixture.folder.parent)}\t"
            f"{fixture.rubric_path.relative_to(fixture.folder.parent)}"
        )


@bench_app.command("score")
def bench_score(
    task_type: str,
    output: Path = typer.Option(..., "--output", exists=False, readable=True),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """Score a local output file against a benchmark rubric and store the run."""
    try:
        result = score_fixture_output(task_type, output, confirm_verified=confirm)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"bench run {result.run_id} stored for {result.fixture.task_type} "
        f"(score={result.score}, verified={result.verified})"
    )


@bench_app.command("run")
def bench_run(
    endpoint: str = typer.Option(..., "--endpoint"),
    task_type: str = typer.Option(..., "--task-type"),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """Run a live local benchmark against a localhost-only adapter."""
    try:
        result = run_local_benchmark(endpoint, task_type, confirm=confirm)
        if confirm and not result.verified:
            raise RuntimeError("benchmark did not pass; not promoting to verified")
        if confirm:
            benchmark_id = promote_verified_benchmark(endpoint, task_type, result)
    except (RuntimeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    message = (
        f"bench run {result.run_id} stored for {result.fixture.task_type} "
        f"(score={result.score}, verified={result.verified})"
    )
    if confirm and result.verified:
        message = f"{message}; benchmark {benchmark_id} promoted"
    typer.echo(message)


@bench_runs_app.command("list")
def bench_runs_list() -> None:
    """List stored benchmark runs without mutating state."""
    runs = list_bench_runs()
    if not runs:
        typer.echo("no benchmark runs")
        return

    for run in runs:
        verified = "yes" if run.verified else "no"
        typer.echo(
            f"{run.run_id}\t{run.task_type or 'unknown'}\t{run.score}\t{verified}\t"
            f"{run.output_path}\t{run.run_at}"
        )


@app.command()
def serve(
    port: int = typer.Option(8765, "--port"),
) -> None:
    """Start the local read-only FastAPI server on 127.0.0.1."""
    uvicorn.run(
        "web.server:app",
        host="127.0.0.1",
        port=port,
        reload=False,
        log_level="info",
    )


app.add_typer(projects_app, name="projects")
app.add_typer(project_app, name="project")
app.add_typer(tasks_app, name="tasks")
app.add_typer(handoff_app, name="handoff")
app.add_typer(packet_app, name="packet")
app.add_typer(chat_app, name="chat")
app.add_typer(health_app, name="health")
app.add_typer(endpoint_app, name="endpoint")
app.add_typer(route_app, name="route")
app.add_typer(usage_app, name="usage")
bench_app.add_typer(bench_fixtures_app, name="fixtures")
bench_app.add_typer(bench_runs_app, name="runs")
app.add_typer(bench_app, name="bench")


if __name__ == "__main__":
    app()
