from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import web.server as server


DB_PATH = Path("data/whiteroom.db")


def test_task_opens_as_chat_thread_with_packet_context() -> None:
    client = TestClient(server.app)
    response = client.get("/chat/white-room?task=1")

    assert response.status_code == 200
    assert "Task context" in response.text
    assert "packet" in response.text
    assert "handoff" in response.text

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT attached_task_id FROM chat_sessions WHERE attached_task_id IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert int(row["attached_task_id"]) > 0
