from __future__ import annotations

from fastapi.testclient import TestClient

import web.server as server


class _FakeCodexLBAdapter:
    last_init: dict[str, object] | None = None
    last_context: dict[str, object] | None = None
    last_options: dict[str, object] | None = None

    def __init__(self, **kwargs) -> None:
        type(self).last_init = dict(kwargs)

    def send_chat(self, context_packet, options=None):
        type(self).last_context = dict(context_packet)
        type(self).last_options = dict(options or {})
        return {
            "endpoint_class": "codex_lb",
            "provider_family": "openai",
            "base_url": str(context_packet.get("base_url") or ""),
            "model_name": str((options or {}).get("model_name") or context_packet.get("model_name") or "codex-mini"),
            "approval_gate_id": 0,
            "approval_status": "allowed",
            "text": "Codex live response",
            "finish_reason": "stop",
            "raw": {"choices": [{"finish_reason": "stop"}]},
            "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
        }

    def normalize_error(self, raw_error):
        return {"kind": "unknown", "message": str(raw_error)}


class _FakeGroqCloudAdapter:
    last_init: dict[str, object] | None = None
    last_context: dict[str, object] | None = None
    last_options: dict[str, object] | None = None

    def __init__(self, **kwargs) -> None:
        type(self).last_init = dict(kwargs)

    def send_chat(self, context_packet, options=None):
        type(self).last_context = dict(context_packet)
        type(self).last_options = dict(options or {})
        return {
            "endpoint_class": "groq_cloud",
            "provider_family": "openai",
            "base_url": str(context_packet.get("base_url") or ""),
            "model_name": str((options or {}).get("model_name") or context_packet.get("model_name") or "llama-3.1-70b-versatile"),
            "approval_gate_id": 0,
            "approval_status": "allowed",
            "text": "Groq live response",
            "finish_reason": "stop",
            "raw": {"choices": [{"finish_reason": "stop"}]},
            "usage": {"input_tokens": 13, "output_tokens": 5, "total_tokens": 18},
        }

    def normalize_error(self, raw_error):
        return {"kind": "unknown", "message": str(raw_error)}


def test_codex_lb_live_chat_uses_adapter_and_saves_assistant_message(monkeypatch) -> None:
    settings_state = server._codex_lb_settings_state()
    endpoint_id = settings_state["endpoint_id"]
    assert endpoint_id is not None

    monkeypatch.setattr(server, "_codex_lb_composer_state", lambda project_slug: _codex_lane_state(endpoint_id))
    monkeypatch.setattr(server, "CodexLBAdapter", _FakeCodexLBAdapter)
    monkeypatch.setattr(server, "get_secret", lambda name: "sk-test-codex-live" if name == "CODEX_LB_API_KEY" else "")

    conversation, _ = server.create_conversation("white-room", title="Phase 15 WR-415 Codex live test")
    client = TestClient(server.app)

    try:
        response = client.post(
            f"/chat/{conversation.id}/send",
            json={
                "content": "Draft a short live note for the Codex lane.",
                "lane_override": "codex_lb",
                "mode": "ask",
                "model_name": "codex-mini",
            },
        )

        assert response.status_code == 200
        assert "Codex live response" in response.text
        assert "no live model call was made" not in response.text.lower()
        assert _FakeCodexLBAdapter.last_init is not None
        assert _FakeCodexLBAdapter.last_init["mode"] == "api_preview"
        assert _FakeCodexLBAdapter.last_init["live_enabled"] is True
        assert _FakeCodexLBAdapter.last_context is not None
        assert _FakeCodexLBAdapter.last_context["model_name"] == "codex-mini"
        assistant_messages = [message for message in server.list_messages(conversation.id) if message.role == "assistant"]
        assert assistant_messages
        assert assistant_messages[-1].content == "Codex live response"
        assert assistant_messages[-1].route_decision_id is not None
        assert assistant_messages[-1].endpoint_id == endpoint_id
    finally:
        server.delete_conversation(conversation.id)


def test_groq_cloud_live_chat_uses_adapter_and_saves_assistant_message(monkeypatch) -> None:
    settings_state = server._groq_cloud_settings_state()
    endpoint_id = settings_state["endpoint_id"]
    assert endpoint_id is not None

    monkeypatch.setattr(server, "_groq_cloud_composer_state", lambda project_slug: _groq_lane_state(endpoint_id))
    monkeypatch.setattr(server, "GroqCloudAdapter", _FakeGroqCloudAdapter)
    monkeypatch.setattr(server, "get_secret", lambda name: "gsk-test-groq-live" if name == "GROQ_API_KEY" else "")

    conversation, _ = server.create_conversation("white-room", title="Phase 15 WR-415 Groq live test")
    client = TestClient(server.app)

    try:
        response = client.post(
            f"/chat/{conversation.id}/send",
            json={
                "content": "Draft a short live note for the Groq lane.",
                "lane_override": "groq_cloud",
                "mode": "ask",
                "model_name": "llama-3.1-70b-versatile",
            },
        )

        assert response.status_code == 200
        assert "Groq live response" in response.text
        assert "no live model call was made" not in response.text.lower()
        assert _FakeGroqCloudAdapter.last_init is not None
        assert _FakeGroqCloudAdapter.last_init["live_enabled"] is True
        assert _FakeGroqCloudAdapter.last_context is not None
        assert _FakeGroqCloudAdapter.last_context["model_name"] == "llama-3.1-70b-versatile"
        assistant_messages = [message for message in server.list_messages(conversation.id) if message.role == "assistant"]
        assert assistant_messages
        assert assistant_messages[-1].content == "Groq live response"
        assert assistant_messages[-1].route_decision_id is not None
        assert assistant_messages[-1].endpoint_id == endpoint_id
    finally:
        server.delete_conversation(conversation.id)


def _codex_lane_state(endpoint_id: int) -> dict[str, object]:
    return {
        "endpoint_id": endpoint_id,
        "base_url": "https://codex.example.com/v1",
        "base_url_label": "https://codex.example.com/v1",
        "key_present": True,
        "key_fingerprint": "fp_codex_test",
        "connection_state": "connected",
        "rate_limited": False,
        "cooldown_remaining": "",
        "models_synced": True,
        "models_synced_label": "synced",
        "models_probe_url": "https://codex.example.com/v1/models",
        "live_calls_allowed": True,
        "selected_model_name": "codex-mini",
        "models": [{"model_name": "codex-mini", "context_window": 8192, "active": True}],
        "active_grant": {"id": 1},
    }


def _groq_lane_state(endpoint_id: int) -> dict[str, object]:
    return {
        "endpoint_id": endpoint_id,
        "base_url": "https://api.groq.com/openai/v1",
        "base_url_label": "https://api.groq.com/openai/v1",
        "key_present": True,
        "key_fingerprint": "fp_groq_test",
        "connection_state": "connected",
        "rate_limited": False,
        "cooldown_remaining": "",
        "models_synced": True,
        "models_synced_label": "synced",
        "models_probe_url": "https://api.groq.com/openai/v1/models",
        "live_calls_allowed": True,
        "selected_model_name": "llama-3.1-70b-versatile",
        "models": [{"model_name": "llama-3.1-70b-versatile", "context_window": 131072, "active": True}],
        "active_grant": {"id": 2},
    }
