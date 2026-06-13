from __future__ import annotations

from fastapi.testclient import TestClient

import web.server as server


def test_home_redirects_to_latest_project_chat() -> None:
    client = TestClient(server.app)
    expected_slug = server.list_projects()[-1].slug
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].endswith(f"/chat/{expected_slug}")


def test_home_renders_onboarding_when_no_projects(monkeypatch) -> None:
    monkeypatch.setattr("core.home.list_projects", lambda: [])
    monkeypatch.setattr("core.onboarding.list_projects", lambda: [])
    monkeypatch.setattr("core.onboarding._has_conversation", lambda: False)
    monkeypatch.setattr("core.onboarding.runner_status_snapshot", lambda: {"reachable": False, "message": "offline", "status": "unavailable"})
    client = TestClient(server.app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Chat-first workspace" in response.text
