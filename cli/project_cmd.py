from __future__ import annotations

import typer

from core.db import connect, init_db
from core.projects import ProjectExistsError, create_project


project_app = typer.Typer(help="Project commands.")
tasks_app = typer.Typer(help="Task listing commands.")


def create_project_with_template(name: str, template: str = "default"):
    try:
        return create_project(name, template=template)
    except ProjectExistsError:
        raise
    except ValueError:
        raise


@project_app.command("new")
def project_new(
    name: str,
    template: str = typer.Option("default", "--template"),
) -> None:
    """Create a managed project folder and index it in SQLite."""
    try:
        project = create_project_with_template(name, template=template)
    except ProjectExistsError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"created project '{project.slug}' at {project.path}")


@tasks_app.command("list")
def tasks_list(
    all_projects: bool = typer.Option(False, "--all-projects"),
) -> None:
    """List tasks across all projects without mutating state."""
    if not all_projects:
        typer.echo("use --all-projects in this phase", err=True)
        raise typer.Exit(code=1)

    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT
                p.slug AS project_slug,
                t.id AS task_id,
                t.status AS status,
                t.title AS title,
                t.preferred_tier AS preferred_tier
            FROM tasks AS t
            JOIN projects AS p ON p.id = t.project_id
            ORDER BY p.slug ASC, t.id ASC
            """
        ).fetchall()

    if not rows:
        typer.echo("no tasks")
        return

    for row in rows:
        typer.echo(
            f"{row['project_slug']}\t{row['task_id']}\t{row['status']}\t"
            f"{row['preferred_tier']}\t{row['title']}"
        )
