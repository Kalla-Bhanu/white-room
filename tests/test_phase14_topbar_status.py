from __future__ import annotations

from fastapi.testclient import TestClient

import web.server as server
from core.health import topbar_health_summary


def test_topbar_health_summary_maps_manual_and_local_lanes() -> None:
    snapshot = {"reachable": True, "message": "local runner is available"}

    manual = topbar_health_summary("manual_claude", snapshot)
    assert manual["label"] == "Claude"
    assert manual["status"] == "manual"
    assert manual["dot_class"] == "warn"

    local = topbar_health_summary("ollama_local", snapshot)
    assert local["label"] == "Ollama"
    assert local["reachable"] is True
    assert local["dot_class"] == "ok"


def test_chat_topbar_renders_lane_mode_health_and_usage() -> None:
    client = TestClient(server.app)
    response = client.get("/chat/white-room")
    assert response.status_code == 200
    assert "Lane:" in response.text
    assert "Status:" in response.text
    assert "Cost:" in response.text
    assert "est $" in response.text
