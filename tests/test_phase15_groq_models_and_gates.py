from __future__ import annotations

from types import SimpleNamespace

from adapters import codex_lb as codex_adapter
from adapters import groq_cloud as groq_adapter
import core.providers as providers
import web.server as server


def test_groq_composer_prefers_ranked_chat_model_and_skips_guard_models(monkeypatch) -> None:
    monkeypatch.setattr(
        server,
        "_groq_cloud_settings_state",
        lambda: {
            "endpoint_id": 77,
            "configured_model_name": "groq/llama-3.1-70b",
            "base_url": "https://api.groq.com/openai/v1",
            "base_url_label": "https://api.groq.com/openai/v1",
            "key_present": True,
            "key_fingerprint": "fp_groq_test",
            "connection_state": "connected",
            "rate_limited": False,
            "cooldown_remaining": "",
            "models_synced": True,
            "models_synced_label": "synced",
            "models_sync_required": False,
            "models_probe_url": "https://api.groq.com/openai/v1/models",
            "live_calls_allowed": True,
            "health": {"reachable": True, "key_present": True, "last_checked": "", "last_error": "", "latency_ms": None, "result": "reachable", "detail": "reachable"},
            "runtime": {"failure_count": 0, "cooldown_until": "", "last_rate_limited_at": "", "window_used": 0, "window_reset_at": "", "last_success_at": "", "updated_at": ""},
        },
    )
    monkeypatch.setattr(
        server,
        "_update_groq_cloud_model_name",
        lambda model_name: None,
    )
    monkeypatch.setattr(
        server,
        "list_endpoint_models",
        lambda endpoint_id, active_only=True: [
            {"model_name": "whisper-large-v3", "active": True},
            {"model_name": "llama-prompt-guard-2-22m", "active": True},
            {"model_name": "llama-3.3-70b-versatile", "active": True},
            {"model_name": "qwen/qwen3-32b", "active": True},
            {"model_name": "llama-3.1-8b-instant", "active": True},
        ],
    )

    state = server._groq_cloud_composer_state("white-room")

    assert state["selected_model_name"] == "llama-3.1-8b-instant"
    assert state["selected_model_name"] not in {"whisper-large-v3", "llama-prompt-guard-2-22m"}
    assert state["models_sync_required"] is False


def test_groq_composer_auto_syncs_when_models_empty(monkeypatch) -> None:
    calls: list[str] = []
    synced = {"value": False}
    models_after_sync = [{"model_name": "llama-3.1-8b-instant", "active": True}]

    monkeypatch.setattr(
        server,
        "_groq_cloud_settings_state",
        lambda: {
            "endpoint_id": 78,
            "configured_model_name": "groq/llama-3.1-70b",
            "base_url": "https://api.groq.com/openai/v1",
            "base_url_label": "https://api.groq.com/openai/v1",
            "key_present": True,
            "key_fingerprint": "fp_groq_test",
            "connection_state": "connected",
            "rate_limited": False,
            "cooldown_remaining": "",
            "models_synced": False,
            "models_synced_label": "not synced",
            "models_sync_required": True,
            "models_probe_url": "https://api.groq.com/openai/v1/models",
            "live_calls_allowed": True,
            "health": {"reachable": True, "key_present": True, "last_checked": "", "last_error": "", "latency_ms": None, "result": "reachable", "detail": "reachable"},
            "runtime": {"failure_count": 0, "cooldown_until": "", "last_rate_limited_at": "", "window_used": 0, "window_reset_at": "", "last_success_at": "", "updated_at": ""},
        },
    )
    monkeypatch.setattr(server, "_update_groq_cloud_model_name", lambda model_name: None)

    def fake_list_endpoint_models(endpoint_id, active_only=True):
        calls.append("list")
        return models_after_sync if synced["value"] else []

    def fake_sync_models(endpoint_class):
        calls.append("sync")
        synced["value"] = True
        return SimpleNamespace(endpoint_class="groq_cloud", models_synced=1, last_model_sync="2026-06-13T00:05:00+00:00")

    monkeypatch.setattr(server, "list_endpoint_models", fake_list_endpoint_models)
    monkeypatch.setattr(server, "sync_models", fake_sync_models)

    state = server._groq_cloud_composer_state("white-room")

    assert calls == ["list", "sync", "list"]
    assert state["selected_model_name"] == "llama-3.1-8b-instant"
    assert state["models_sync_required"] is False


def test_chat_context_keeps_lane_specific_model_selection(monkeypatch) -> None:
    monkeypatch.setattr(
        server,
        "_ui_context",
        lambda: {"ui_theme": "dark", "ui_theme_choice": "dark", "ui_theme_toggle": "light"},
    )
    monkeypatch.setattr(
        server,
        "_groq_cloud_composer_state",
        lambda project_slug: {"selected_model_name": "llama-3.1-8b-instant"},
    )
    monkeypatch.setattr(
        server,
        "_codex_lb_composer_state",
        lambda project_slug: {"selected_model_name": "codex-auto-review"},
    )

    context = server._chat_context(
        request=SimpleNamespace(),
        project=server.get_project("white-room"),
        conversations=[],
        selected_conversation=SimpleNamespace(id=5, title="Ollama clean test", mode_default="ask"),
        latest_session=SimpleNamespace(id=234, lane="groq_cloud"),
        messages=[],
    )

    assert context["selected_model_name"] == "codex-auto-review"
    assert context["selected_codex_model_name"] == "codex-auto-review"
    assert context["selected_groq_model_name"] == "llama-3.1-8b-instant"


def test_provider_lane_options_show_groq_when_saved_secret_exists(monkeypatch) -> None:
    monkeypatch.setattr(providers.os, "getenv", lambda name, default="": "")
    monkeypatch.setattr(providers, "get_secret", lambda name: "gsk-test" if name == "GROQ_API_KEY" else "")

    options = providers.provider_lane_options()
    groq_option = next(option for option in options if option["value"] == "groq_cloud")
    assert groq_option["label"] == "Groq Cloud"
    assert groq_option["disabled"] is False


def test_groq_adapter_passes_mode_to_gate_and_succeeds(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_gate_allows_action(**kwargs):
        captured.update(kwargs)
        gate = SimpleNamespace(id=41, status="granted")
        return True, gate, "approved by grant"

    def fake_request_json(base_url, request_path, **kwargs):
        assert base_url == "https://api.groq.com/openai/v1"
        assert request_path == "/chat/completions"
        return {"choices": [{"message": {"content": "Groq live response"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16}}

    monkeypatch.setattr(groq_adapter, "gate_allows_action", fake_gate_allows_action)
    monkeypatch.setattr(groq_adapter, "request_json", fake_request_json)

    adapter = groq_adapter.GroqCloudAdapter(base_url="https://api.groq.com/openai/v1", api_key="gsk-test", live_enabled=True)
    result = adapter.send_chat(
        {
            "project_slug": "white-room",
            "conversation_id": 5,
            "mode": "ask",
            "prompt": "Hello",
            "model_name": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": "Hello"}],
        }
    )

    assert captured["mode"] == "ask"
    assert captured["endpoint_class"] == "groq_cloud"
    assert result["text"] == "Groq live response"
    assert result["approval_gate_id"] == 41


def test_groq_adapter_approval_required_message_is_clear(monkeypatch) -> None:
    def fake_gate_allows_action(**kwargs):
        gate = SimpleNamespace(id=42, status="pending")
        return False, gate, "needs approval"

    monkeypatch.setattr(groq_adapter, "gate_allows_action", fake_gate_allows_action)

    adapter = groq_adapter.GroqCloudAdapter(base_url="https://api.groq.com/openai/v1", api_key="gsk-test", live_enabled=True)

    try:
        adapter.send_chat(
            {
                "project_slug": "white-room",
                "conversation_id": 5,
                "mode": "plan",
                "prompt": "Hello",
                "model_name": "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )
    except PermissionError as exc:
        assert "approval required for Groq Cloud plan chat" in str(exc)
    else:
        raise AssertionError("Groq adapter should have raised PermissionError")


def test_codex_adapter_passes_mode_to_gate_and_succeeds(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_gate_allows_action(**kwargs):
        captured.update(kwargs)
        gate = SimpleNamespace(id=51, status="granted")
        return True, gate, "approved by grant"

    def fake_request_json(base_url, request_path, **kwargs):
        assert request_path == "/v1/chat/completions"
        return {"choices": [{"message": {"content": "Codex live response"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13}}

    monkeypatch.setattr(codex_adapter, "gate_allows_action", fake_gate_allows_action)
    monkeypatch.setattr(codex_adapter, "request_json", fake_request_json)

    adapter = codex_adapter.CodexLBAdapter(mode="api_preview", base_url="https://codex.example.com", api_key="sk-test", live_enabled=True)
    result = adapter.send_chat(
        {
            "project_slug": "white-room",
            "conversation_id": 7,
            "mode": "ask",
            "prompt": "Hello",
            "model_name": "codex-mini",
            "messages": [{"role": "user", "content": "Hello"}],
        }
    )

    assert captured["mode"] == "ask"
    assert captured["endpoint_class"] == "codex_lb"
    assert result["text"] == "Codex live response"
    assert result["approval_gate_id"] == 51


def test_codex_adapter_approval_required_message_is_clear(monkeypatch) -> None:
    def fake_gate_allows_action(**kwargs):
        gate = SimpleNamespace(id=52, status="pending")
        return False, gate, "needs approval"

    monkeypatch.setattr(codex_adapter, "gate_allows_action", fake_gate_allows_action)

    adapter = codex_adapter.CodexLBAdapter(mode="api_preview", base_url="https://codex.example.com", api_key="sk-test", live_enabled=True)

    try:
        adapter.send_chat(
            {
                "project_slug": "white-room",
                "conversation_id": 7,
                "mode": "ask",
                "prompt": "Hello",
                "model_name": "codex-mini",
                "messages": [{"role": "user", "content": "Hello"}],
            }
        )
    except PermissionError as exc:
        assert "approval required for Codex LB ask chat" in str(exc)
    else:
        raise AssertionError("Codex adapter should have raised PermissionError")
