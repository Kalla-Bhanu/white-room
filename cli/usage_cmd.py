from __future__ import annotations

import typer

from core.usage import usage_report


usage_app = typer.Typer(help="Usage reporting commands.")


@usage_app.command("report")
def usage_report_cmd(
    project: str = typer.Option(None, "--project"),
    endpoint: str = typer.Option(None, "--endpoint"),
) -> None:
    """Print usage totals and window guard state."""
    rows = usage_report(project_slug=project, endpoint_name=endpoint)
    if not rows:
        typer.echo("no usage events")
        typer.echo("guard: no endpoints at limit")
        return

    typer.echo("endpoint\tproject\tevents\ttokens_in\ttokens_out\test_cost\twindow_used\twindow_limit\tat_limit")
    at_limit_count = 0
    for row in rows:
        at_limit = "yes" if row.at_limit else "no"
        at_limit_count += 1 if row.at_limit else 0
        window_limit = row.window_limit if row.window_limit is not None else "unlimited"
        typer.echo(
            f"{row.endpoint_name}\t{row.project_slug}\t{row.events}\t{row.tokens_in}\t"
            f"{row.tokens_out}\t{row.est_cost}\t{row.window_used}\t{window_limit}\t{at_limit}"
        )

    guard_text = "some endpoints at limit" if at_limit_count else "no endpoints at limit"
    typer.echo(f"guard: {guard_text}")
