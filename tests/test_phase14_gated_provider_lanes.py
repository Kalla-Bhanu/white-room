from __future__ import annotations

from fastapi.testclient import TestClient

import web.server as server


def test_gated_provider_lanes_show_disabled_reason() -> None:
    client = TestClient(server.app)
    response = client.get("/chat/white-room")
    assert response.status_code == 200
    assert "needs key in Settings" in response.text
    assert "Gemini Compatible Cloud" in response.text
    assert "Groq Cloud" in response.text
    assert "OpenRouter Cloud" in response.text
    assert "DeepSeek Compatible Cloud" in response.text


def test_settings_uses_present_absent_presence_labels() -> None:
    profiles = server._provider_profile_rows()
    assert profiles
    labels = {profile["env_presence_label"] for profile in profiles}
    assert "present" in labels or "absent" in labels
    assert "missing" not in labels
