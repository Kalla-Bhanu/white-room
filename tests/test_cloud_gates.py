from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from adapters.anthropic_compatible import AnthropicCompatibleAdapter
from adapters.cloud_shared import ACTION_TYPE, build_cloud_payload_summary
from adapters.openai_compatible import OpenAICompatibleAdapter
from adapters.provider_specific import ProviderSpecificAdapter
from core.approvals import create_approval_gate, decide_approval_gate
from core.db import connect, init_db


@dataclass
class _FakeResponse:
    payload: dict[str, Any]
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self.payload


class _FakeClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = _FakeResponse(response)

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # pragma: no cover - context protocol
        return None

    def post(self, *_args: Any, **_kwargs: Any) -> _FakeResponse:
        return self.response


@pytest.mark.parametrize(
    ("adapter", "key_name", "provider_family", "response_payload"),
    [
        (
            OpenAICompatibleAdapter(base_url="https://example.invalid/v1"),
            "OPENAI_COMPAT_API_KEY",
            "openai",
            {
                "choices": [{"message": {"content": "openai result"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
        ),
        (
            AnthropicCompatibleAdapter(base_url="https://example.invalid"),
            "ANTHROPIC_API_KEY",
            "anthropic",
            {
                "content": [{"type": "text", "text": "anthropic result"}],
                "usage": {"input_tokens": 10, "output_tokens": 6},
                "stop_reason": "end_turn",
            },
        ),
        (
            ProviderSpecificAdapter(base_url="https://example.invalid/v1"),
            "OPENROUTER_API_KEY",
            "openrouter",
            {
                "choices": [{"message": {"content": "provider result"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 14, "completion_tokens": 5},
            },
        ),
    ],
)
def test_cloud_lane_approved_call_records_usage(monkeypatch: pytest.MonkeyPatch, adapter, key_name, provider_family, response_payload) -> None:
    prompt = f"cloud gated prompt {uuid4().hex}"
    context_packet = {
        "project_slug": "white-room",
        "task_id": None,
        "conversation_id": None,
        "prompt": prompt,
        "provider_family": provider_family,
        "base_url": "https://example.invalid/v1",
    }
    options = {"model_name": "test-model", "provider_family": provider_family}
    summary = build_cloud_payload_summary(
        endpoint_class=adapter.name,
        project_slug="white-room",
        task_id=None,
        conversation_id=None,
        provider_family=provider_family,
        model_name="test-model",
        prompt=prompt,
    )
    gate = create_approval_gate(
        project_slug="white-room",
        action_type=ACTION_TYPE,
        target_endpoint_id=None,
        payload_summary=summary,
    )
    decide_approval_gate(gate.id, "approve")
    monkeypatch.setenv(key_name, "sk-test-value")
    monkeypatch.setattr("adapters.cloud_shared.httpx.Client", lambda *args, **kwargs: _FakeClient(response_payload))

    before = _usage_count()
    result = adapter.send_chat(context_packet, options)
    after = _usage_count()

    assert result["text"]
    assert result["usage"]["tokens_in"] > 0
    assert result["usage"]["tokens_out"] > 0
    assert after == before + 1


@pytest.mark.parametrize(
    ("adapter", "key_name", "provider_family"),
    [
        (OpenAICompatibleAdapter(base_url="https://example.invalid/v1"), "OPENAI_COMPAT_API_KEY", "openai"),
        (AnthropicCompatibleAdapter(base_url="https://example.invalid"), "ANTHROPIC_API_KEY", "anthropic"),
        (ProviderSpecificAdapter(base_url="https://example.invalid/v1"), "OPENROUTER_API_KEY", "openrouter"),
    ],
)
def test_cloud_lane_blocks_without_approval_even_with_key(monkeypatch: pytest.MonkeyPatch, adapter, key_name, provider_family) -> None:
    prompt = f"approval blocked prompt {uuid4().hex}"
    monkeypatch.setenv(key_name, "sk-test-value")
    monkeypatch.setattr("adapters.cloud_shared.httpx.Client", lambda *args, **kwargs: _FakeClient({}))

    before = _usage_count()
    with pytest.raises(PermissionError, match="approval required"):
        adapter.send_chat(
            {
                "project_slug": "white-room",
                "task_id": None,
                "conversation_id": None,
                "prompt": prompt,
                "provider_family": provider_family,
                "base_url": "https://example.invalid/v1",
            },
            {"model_name": "test-model", "provider_family": provider_family},
        )
    after = _usage_count()

    assert after == before


def test_cloud_lane_blocks_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    prompt = f"missing key prompt {uuid4().hex}"
    monkeypatch.delenv("OPENAI_COMPAT_API_KEY", raising=False)

    before = _usage_count()
    with pytest.raises(PermissionError, match="configured key missing"):
        OpenAICompatibleAdapter(base_url="https://example.invalid/v1").send_chat(
            {
                "project_slug": "white-room",
                "task_id": None,
                "conversation_id": None,
                "prompt": prompt,
                "base_url": "https://example.invalid/v1",
            },
            {"model_name": "test-model"},
        )
    after = _usage_count()

    assert after == before


@pytest.mark.parametrize(
    ("adapter", "key_name", "provider_family", "response_payload"),
    [
        (
            ProviderSpecificAdapter(base_url="https://example.invalid/v1"),
            "OPENROUTER_API_KEY",
            "openrouter",
            {
                "choices": [{"message": {"content": "stream result"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 3},
            },
        ),
    ],
)
def test_cloud_stream_chat_yields_chunks(monkeypatch: pytest.MonkeyPatch, adapter, key_name, provider_family, response_payload) -> None:
    prompt = f"stream prompt {uuid4().hex}"
    context_packet = {
        "project_slug": "white-room",
        "task_id": None,
        "conversation_id": None,
        "prompt": prompt,
        "provider_family": provider_family,
        "base_url": "https://example.invalid/v1",
    }
    summary = build_cloud_payload_summary(
        endpoint_class=adapter.name,
        project_slug="white-room",
        task_id=None,
        conversation_id=None,
        provider_family=provider_family,
        model_name="test-model",
        prompt=prompt,
    )
    gate = create_approval_gate(
        project_slug="white-room",
        action_type=ACTION_TYPE,
        target_endpoint_id=None,
        payload_summary=summary,
    )
    decide_approval_gate(gate.id, "approve")
    monkeypatch.setenv(key_name, "sk-test-value")
    monkeypatch.setattr("adapters.cloud_shared.httpx.Client", lambda *args, **kwargs: _FakeClient(response_payload))

    events = list(adapter.stream_chat(context_packet, {"model_name": "test-model", "provider_family": provider_family}))
    assert events[-1]["done"] is True
    assert events[-1]["text"] == "stream result"
    assert any(event.get("delta") for event in events[:-1])


def _usage_count() -> int:
    with connect() as conn:
        init_db(conn)
        row = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()
        return int(row[0])
