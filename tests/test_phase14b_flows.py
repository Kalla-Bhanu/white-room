from __future__ import annotations

from fastapi.testclient import TestClient

import web.server as server


def test_phase14b_core_flows_remain_reachable() -> None:
    client = TestClient(server.app)
    conversation = server.list_conversations("white-room")[0]

    cockpit_response = client.get("/chat/white-room")
    assert cockpit_response.status_code == 200
    assert "Manual Claude" in cockpit_response.text
    assert "Ollama Local" in cockpit_response.text
    assert "route-summary-chip" in cockpit_response.text

    manual_response = client.post(f"/chat/{conversation.id}/export-manual")
    assert manual_response.status_code == 200
    assert "Manual Claude" in manual_response.text or "manual export" in manual_response.text.lower()

    task_response = client.post(f"/chat/{conversation.id}/to-task")
    assert task_response.status_code == 200
    assert "Task packet" in task_response.text

    gate_response = client.post(
        f"/chat/{conversation.id}/send",
        json={
            "content": "Execute the Codex LB lane for this cockpit turn.",
            "lane_override": "codex_lb",
            "mode": "execute",
        },
    )
    assert gate_response.status_code == 200
    assert "Approval gate" in gate_response.text or "needs approval" in gate_response.text

    task_context_response = client.get("/chat/white-room?task=1")
    assert task_context_response.status_code == 200
    assert "Task context" in task_context_response.text
