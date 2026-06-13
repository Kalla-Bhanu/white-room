from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

import core.chat as chat
import web.server as server


def _ollama_local_endpoint_id() -> int:
    conn = sqlite3.connect("data/whiteroom.db")
    row = conn.execute(
        "SELECT id FROM endpoints WHERE endpoint_class = 'ollama_local' ORDER BY id LIMIT 1"
    ).fetchone()
    assert row is not None
    return int(row[0])


def _reachable_ollama_snapshot() -> dict[str, object]:
    endpoint_id = _ollama_local_endpoint_id()
    return {
        "status": "reachable",
        "reachable": True,
        "message": "ollama local reachable",
        "endpoints": [
            {
                "endpoint_id": endpoint_id,
                "endpoint_class": "ollama_local",
                "base_url": "http://127.0.0.1:11434",
                "model_name": "llama3.1:latest",
                "reachable": True,
                "latency_ms": 12,
                "detail": "local ollama ready",
            }
        ],
    }


def test_ollama_local_turn_streams_and_persists_draft(monkeypatch) -> None:
    monkeypatch.setattr(chat, "runner_status_snapshot", _reachable_ollama_snapshot)
    monkeypatch.setattr(
        chat.OllamaLocalAdapter,
        "list_models",
        lambda self: [{"model_name": "llama3.1:latest"}],
    )
    monkeypatch.setattr(
        chat.OllamaLocalAdapter,
        "send_chat",
        lambda self, context_packet, options: {
            "text": "Local draft from Ollama.",
            "usage": {"input_tokens": 5, "output_tokens": 13},
        },
    )

    client = TestClient(server.app)
    conversation = server.list_conversations("white-room")[0]

    response = client.post(
        f"/chat/{conversation.id}/send",
        json={
            "content": "Summarize the project in one sentence.",
            "lane_override": "ollama_local",
            "mode": "summarize",
        },
    )

    assert response.status_code == 200
    assert "streaming via ollama_local" in response.text
    assert "Local draft from Ollama." in response.text
    assert '"status": "draft"' in response.text

    messages = server.list_messages(conversation.id)
    assistant_message = next(
        message
        for message in messages
        if message.role == "assistant"
        and message.content == "Local draft from Ollama."
        and message.status == "draft"
        and message.endpoint_id == _ollama_local_endpoint_id()
        and message.model_name == "llama3.1:latest"
        and message.lane_override == "ollama_local"
    )
    assert assistant_message.id > 0


def test_ollama_local_turn_reports_unavailable_when_runner_is_offline(monkeypatch) -> None:
    monkeypatch.setattr(
        chat,
        "runner_status_snapshot",
        lambda: {"status": "unavailable", "reachable": False, "message": "offline", "endpoints": []},
    )

    client = TestClient(server.app)
    conversation = server.list_conversations("white-room")[0]

    response = client.post(
        f"/chat/{conversation.id}/send",
        json={
            "content": "Summarize the project in one sentence.",
            "lane_override": "ollama_local",
            "mode": "summarize",
        },
    )

    assert response.status_code == 200
    assert "local runner unavailable" in response.text.lower()
    assert "unavailable" in response.text.lower()


def test_ollama_model_picker_prefers_qwen_for_code_tasks() -> None:
    chosen = chat._pick_ollama_model_name(
        [
            {"model_name": "deepseek-coder:6.7b"},
            {"model_name": "gemma3:4b"},
            {"model_name": "qwen2.5-coder:7b"},
            {"model_name": "llama3.1:latest"},
        ],
        prompt="Debug this Python function and fix the failing test.",
        mode="ask",
    )

    assert chosen == "qwen2.5-coder:7b"


def test_ollama_model_picker_prefers_gemma_for_summaries() -> None:
    chosen = chat._pick_ollama_model_name(
        [
            {"model_name": "deepseek-coder:6.7b"},
            {"model_name": "mistral:7b"},
            {"model_name": "gemma3:4b"},
            {"model_name": "llama3.1:latest"},
        ],
        prompt="Summarize this project into five bullets.",
        mode="summarize",
    )

    assert chosen == "gemma3:4b"


def test_resolve_model_name_respects_endpoint_override(monkeypatch) -> None:
    adapter = chat.OllamaLocalAdapter()
    monkeypatch.setattr(
        chat.OllamaLocalAdapter,
        "list_models",
        lambda self: [{"model_name": "qwen2.5-coder:7b"}],
    )

    chosen = chat._resolve_model_name(
        adapter,
        {"model_name": "mistral:7b"},
        prompt="Debug this function.",
        mode="ask",
    )

    assert chosen == "mistral:7b"
