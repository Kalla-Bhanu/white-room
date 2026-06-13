from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import web.server as server


DB_PATH = Path("data/whiteroom.db")


def _task_count() -> int:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT COUNT(*) AS count FROM tasks").fetchone()
    assert row is not None
    return int(row["count"])


def test_chat_turn_can_be_promoted_to_task_packet() -> None:
    client = TestClient(server.app)
    conversation = server.list_conversations("white-room")[0]
    before = _task_count()

    response = client.post(f"/chat/{conversation.id}/to-task")

    assert response.status_code == 200
    assert "Task packet" in response.text
    assert "Create task from latest turn" in response.text
    assert "Open board" in response.text
    assert _task_count() == before + 1
