from __future__ import annotations

from fastapi.testclient import TestClient

import web.server as server


def test_chat_route_renders_cockpit_shell() -> None:
    client = TestClient(server.app)
    response = client.get("/chat/white-room")
    assert response.status_code == 200
    assert "Conversation cockpit" in response.text
    assert "chat-send-form" in response.text
    assert "cockpit-frame" in response.text
