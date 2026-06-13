from __future__ import annotations

import sqlite3

from typer.testing import CliRunner
from fastapi.testclient import TestClient

from cli.main import app as cli_app
import web.server as server


def test_home_redirects_to_chat_workspace() -> None:
    client = TestClient(server.app)
    response = client.get("/", follow_redirects=False)

    assert response.status_code in {301, 302, 307, 308}
    assert response.headers["location"].startswith("/chat/")


def test_route_dry_run_mode_bias_persists_mode_and_lane() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "route",
            "dry-run",
            "--project",
            "white-room",
            "--task",
            "1",
            "--mode",
            "plan",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "manual_claude" in result.output

    with sqlite3.connect("data/whiteroom.db") as conn:
        row = conn.execute(
            "SELECT mode, chosen_lane, est_cost_usd FROM route_decisions ORDER BY id DESC LIMIT 1"
        ).fetchone()

    assert row is not None
    assert row[0] == "plan"
    assert row[1] == "manual_claude"
    assert row[2] == 0.0
