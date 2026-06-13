from __future__ import annotations

import httpx

from adapters.openai_compatible import (
    normalize_error,
    parse_openai_chat_text,
    parse_openai_stream_text,
    parse_openai_usage,
    resolve_url,
)
from core.http_client import request_json


def test_resolve_url_handles_v1_and_trailing_slash() -> None:
    assert resolve_url("https://h", "/v1/models") == "https://h/v1/models"
    assert resolve_url("https://h/v1", "/v1/models") == "https://h/v1/models"
    assert resolve_url("https://h/v1/", "/v1/chat/completions") == "https://h/v1/chat/completions"


def test_parse_openai_chat_text_and_usage() -> None:
    payload = {
        "choices": [
            {
                "message": {"content": "hello world"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 5},
    }

    assert parse_openai_chat_text(payload) == "hello world"
    assert parse_openai_usage(payload) == {"tokens_in": 12, "tokens_out": 5}


def test_parse_openai_stream_text_combines_chunks() -> None:
    raw_stream = "\n".join(
        [
            'data: {"choices":[{"delta":{"content":"hello "},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"content":"world"},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ]
    )

    parsed = parse_openai_stream_text(raw_stream)
    assert parsed["chunks"] == ["hello ", "world"]
    assert parsed["text"] == "hello world"
    assert parsed["finish_reason"] == "stop"


def test_normalize_error_redacts_raw_secret_values() -> None:
    details = normalize_error(ValueError("api_key=sk-live-secret Bearer sk-live-secret"))

    assert details["kind"] == "bad_request"
    assert "sk-live-secret" not in details["message"]
    assert "***redacted***" in details["message"]


def test_request_json_retries_once_on_timeout(monkeypatch) -> None:
    calls = {"count": 0}

    class _FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload
            self.status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # pragma: no cover - context protocol
            return None

        def request(self, method, path, json=None):  # noqa: A002
            calls["count"] += 1
            if calls["count"] == 1:
                raise httpx.TimeoutException("timeout")
            return _FakeResponse({"ok": True, "method": method, "path": path, "json": json})

    monkeypatch.setattr("core.http_client.httpx.Client", _FakeClient)

    result = request_json(
        "https://example.invalid/v1",
        "/chat/completions",
        headers={"Authorization": "Bearer test"},
        payload={"model": "test-model"},
        retries=1,
    )

    assert result["ok"] is True
    assert calls["count"] == 2
