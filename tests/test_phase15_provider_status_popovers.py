from __future__ import annotations

from fastapi.testclient import TestClient

import web.server as server


def test_topbar_provider_popover_shows_codex_and_groq_states(monkeypatch) -> None:
    monkeypatch.setattr(
        server,
        "_codex_lb_settings_state",
        lambda: {
            "connection_state": "connected",
            "key_fingerprint": "fp_codex1234",
            "models_synced_label": "synced",
            "rate_limited": False,
            "cooldown_remaining": "",
            "health": {"latency_ms": 14, "detail": "Codex LB reachable at https://codex.example.com/v1", "last_checked": "2026-06-13T00:00:00+00:00"},
            "runtime": {"last_success_at": "2026-06-13T00:05:00+00:00"},
        },
    )
    monkeypatch.setattr(
        server,
        "_groq_cloud_settings_state",
        lambda: {
            "connection_state": "rate-limited",
            "key_fingerprint": "fp_groq1234",
            "models_synced_label": "synced",
            "rate_limited": True,
            "cooldown_remaining": "2026-06-13T02:00:00+00:00",
            "health": {"latency_ms": 28, "detail": "Groq Cloud reachable at https://api.groq.com/openai/v1", "last_checked": "2026-06-13T00:06:00+00:00"},
            "runtime": {"last_success_at": "2026-06-13T00:06:00+00:00"},
        },
    )

    client = TestClient(server.app)
    response = client.get("/chat/white-room")

    assert response.status_code == 200
    assert "Providers: Codex connected" in response.text
    assert "Groq rate-limited" in response.text
    assert "Provider health" in response.text
    assert "fp_codex1234" in response.text
    assert "fp_groq1234" in response.text
    assert "cooldown 2026-06-13T02:00:00+00:00" in response.text
    assert "28 ms" in response.text
