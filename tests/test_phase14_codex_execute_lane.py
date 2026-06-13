from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from core.approvals import decide_approval_gate
import web.server as server


DB_PATH = Path("data/whiteroom.db")


def _count_rows(table: str) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])


def _latest_gate_id() -> int:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id FROM approval_gates WHERE action_type = 'codex_execute_packet' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    return int(row["id"])


def _latest_execution_run() -> sqlite3.Row:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM execution_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    return row


def test_execute_lane_requires_approval_then_exports_packet_and_imports_reply() -> None:
    client = TestClient(server.app)
    conversation = server.list_conversations("white-room")[0]
    tasks = server._list_project_tasks(conversation.project_id)
    selected_task = server._next_project_task(tasks)
    assert selected_task is not None
    expected_packet = Path("projects/white-room/packets") / f"codex-execution-task-{int(selected_task['id']):03d}.md"

    prompt = f"Execute the Codex LB lane for this cockpit turn {uuid4().hex}."
    payload = {"content": prompt, "lane_override": "codex_lb", "mode": "execute"}
    response = client.post(f"/chat/{conversation.id}/send", json=payload)

    assert response.status_code == 200
    assert "Approval gate" in response.text
    assert "needs approval" in response.text

    gate_id = _latest_gate_id()
    decide_approval_gate(gate_id, "approve")

    approved_response = client.post(f"/chat/{conversation.id}/send", json=payload)
    assert approved_response.status_code == 200
    assert expected_packet.exists()
    approved_run = _latest_execution_run()
    assert approved_run["status"] == "exported"
    assert approved_run["mode"] == "execute"
    assert Path(approved_run["packet_path"]).name == expected_packet.name

    import_response = client.post(
        f"/chat/{conversation.id}/import-codex",
        data={"content": "Codex execution reply from the cockpit.", "target": "current_status.md"},
    )
    assert import_response.status_code == 200
    latest_run = _latest_execution_run()
    assert latest_run["status"] == "imported"
    assert latest_run["mode"] == "execute"
    assert latest_run["target"] == "current_status.md"

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        assistant_row = conn.execute(
            "SELECT role, status, model_name, content FROM messages ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert assistant_row is not None
    assert assistant_row["role"] == "assistant"
    assert assistant_row["status"] == "final"
    assert assistant_row["model_name"] == "codex_lb"
    assert assistant_row["content"] == "Codex execution reply from the cockpit."
