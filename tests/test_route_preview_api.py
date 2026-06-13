from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from web.server import app


def test_route_preview_json_records_preview_row() -> None:
    client = TestClient(app)
    response = client.get("/api/route/preview", params={"project": "white-room", "task": 1})
    assert response.status_code == 200

    payload = response.json()
    assert payload["source"] == "api_preview"
    assert payload["is_preview"] is True
    assert payload["status"] == "suggested"
    assert payload["route_decision_id"] > 0

    with sqlite3.connect("data/whiteroom.db") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT is_preview, source, status FROM route_decisions WHERE id = ?",
            (payload["route_decision_id"],),
        ).fetchone()
        assert row is not None
        assert int(row["is_preview"]) == 1
        assert str(row["source"]) == "api_preview"
        assert str(row["status"]) == "suggested"
        conn.execute("DELETE FROM route_decisions WHERE id = ?", (payload["route_decision_id"],))
        conn.commit()
