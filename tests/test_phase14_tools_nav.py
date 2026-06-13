from __future__ import annotations

from fastapi.testclient import TestClient

import web.server as server


def test_chat_sidebar_demotes_tools_section() -> None:
    client = TestClient(server.app)
    response = client.get("/chat/white-room")
    assert response.status_code == 200
    assert "Tools" in response.text
    assert "Project brain" in response.text
    assert "Benchmarks" in response.text
    assert "Usage" in response.text
    assert "Endpoints" in response.text
