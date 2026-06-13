from __future__ import annotations

import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient

from core.approvals import decide_approval_gate
import web.server as server


def test_chat_composer_renders_codex_model_picker_and_trust_state(monkeypatch) -> None:
    monkeypatch.setattr(
        server,
        "_codex_lb_composer_state",
        lambda project_slug: {
            "connection_state": "connected",
            "key_fingerprint": "fp_test1234",
            "live_calls_allowed": True,
            "models_synced": True,
            "models_probe_url": "https://codex.example.com/v1/models",
            "models": [
                {"model_name": "codex-mini", "context_window": 8192, "active": True},
                {"model_name": "codex-pro", "context_window": 16384, "active": True},
            ],
            "active_grant": {"id": 7},
        },
    )

    client = TestClient(server.app)
    response = client.get("/chat/white-room")

    assert response.status_code == 200
    assert 'name="model_name"' in response.text
    assert 'name="lane_override"' in response.text
    assert 'codex-mini' in response.text
    assert 'codex-pro' in response.text
    assert 'fp_test1234' in response.text
    assert 'trusted session active' in response.text


def test_chat_composer_renders_groq_lane_and_model_picker(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test-123456")
    monkeypatch.setattr(
        server,
        "_groq_cloud_composer_state",
        lambda project_slug: {
            "connection_state": "connected",
            "key_fingerprint": "fp_groq1234",
            "live_calls_allowed": True,
            "models_synced": True,
            "models_probe_url": "https://api.groq.com/openai/v1/models",
            "models": [
                {"model_name": "llama-3.1-70b-versatile", "context_window": 131072, "active": True},
                {"model_name": "llama-3.1-8b-instant", "context_window": 131072, "active": True},
            ],
            "active_grant": {"id": 9},
        },
    )

    client = TestClient(server.app)
    response = client.get("/chat/white-room?lane_override=groq_cloud")

    assert response.status_code == 200
    assert "Groq Cloud" in response.text
    assert 'data-current-lane-chip' in response.text
    assert 'groq_cloud' in response.text
    assert 'name="model_name"' in response.text
    assert 'llama-3.1-70b-versatile' in response.text
    assert 'fp_groq1234' in response.text
    assert 'trusted session active' in response.text


def test_codex_execute_gate_trust_grant_skips_repeat_prompt() -> None:
    with sqlite3.connect("data/whiteroom.db") as conn:
        conn.execute("DELETE FROM approval_grants WHERE endpoint_class = 'codex_lb'")
        conn.commit()

    client = TestClient(server.app)
    conversation = server.list_conversations("white-room")[0]
    tasks = server._list_project_tasks(conversation.project_id)
    selected_task = server._next_project_task(tasks)
    assert selected_task is not None
    expected_packet = (
        server.Path("projects/white-room/packets")
        / f"codex-execution-task-{int(selected_task['id']):03d}.md"
    )

    payload = {
        "content": f"Execute the Codex LB lane {uuid4().hex}.",
        "lane_override": "codex_lb",
        "mode": "execute",
        "model_name": "codex-mini",
    }

    first_response = client.post(f"/chat/{conversation.id}/send", json=payload)
    assert first_response.status_code == 200
    assert "Approval gate" in first_response.text
    assert "needs approval" in first_response.text

    with sqlite3.connect("data/whiteroom.db") as conn:
        conn.row_factory = sqlite3.Row
        gate_row = conn.execute(
            "SELECT id, payload_summary FROM approval_gates WHERE action_type = 'codex_execute_packet' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert gate_row is not None
    assert "model=codex-mini" in str(gate_row["payload_summary"])

    approve_response = client.post(
        f"/approval/{int(gate_row['id'])}/decide",
        data={
            "decision": "approve",
            "trust_session": "1",
            "return_to": f"/chat/{conversation.id}?conversation_id={conversation.id}",
        },
        follow_redirects=False,
    )
    assert approve_response.status_code in {302, 303}

    with sqlite3.connect("data/whiteroom.db") as conn:
        conn.row_factory = sqlite3.Row
        grant_row = conn.execute(
            "SELECT endpoint_class, modes, active FROM approval_grants WHERE endpoint_class = 'codex_lb' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert grant_row is not None
    assert grant_row["endpoint_class"] == "codex_lb"
    assert grant_row["active"] == 1

    second_response = client.post(f"/chat/{conversation.id}/send", json=payload)
    assert second_response.status_code == 200
    assert "Approval gate" not in second_response.text
    assert expected_packet.exists()
