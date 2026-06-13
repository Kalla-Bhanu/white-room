from __future__ import annotations

from fastapi.testclient import TestClient

import web.server as server
from core.onboarding import build_onboarding_state


def test_onboarding_state_builds_selected_preview(monkeypatch) -> None:
    monkeypatch.setattr("core.onboarding.list_projects", lambda: [])
    monkeypatch.setattr("core.onboarding._has_conversation", lambda: False)
    monkeypatch.setattr("core.onboarding.runner_status_snapshot", lambda: {"reachable": False, "message": "offline -- start Ollama/LM Studio", "status": "unavailable"})
    monkeypatch.setattr("core.onboarding._present_key_labels", lambda: [])

    state = build_onboarding_state("no_local_runner")

    assert state.selected_state == "no_local_runner"
    assert state.hero_card.title == "No local runner"
    assert len(state.state_cards) == 6
    assert any(card.key == "claude_manual_available" and card.primary_label == "Open manual Claude" for card in state.state_cards)


def test_onboarding_route_renders_state_cards() -> None:
    client = TestClient(server.app)
    response = client.get("/onboarding?state=no_api_keys")
    assert response.status_code == 200
    assert "Onboarding cockpit" in response.text
    assert "No API keys" in response.text
    assert "No local runner" in response.text
    assert "Claude manual available" in response.text
    assert "Open manual Claude" in response.text
