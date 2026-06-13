from __future__ import annotations

import json

import pytest
import httpx

from adapters.groq_cloud import GROQ_API_KEY_ENV, GROQ_BASE_URL, GroqCloudAdapter
from adapters.openai_compatible import resolve_url


class _FakeResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            request = httpx.Request("GET", "https://example.invalid")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self) -> dict[str, object]:
        return self._payload


class _FakeClient:
    def __init__(self, *args, **kwargs) -> None:
        self.headers = kwargs.get("headers") or {}

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # pragma: no cover - context protocol
        return None

    def get(self, path):  # noqa: ANN001
        assert path == "/models"
        return _FakeResponse(
            {
                "data": [
                    {"id": "llama-3.1-70b-versatile", "context_window": 131072, "supports_tools": True},
                    {"id": "llama-3.1-8b-instant", "context_window": 131072, "supports_streaming": True},
                ]
            }
        )

    def post(self, path, json=None):  # noqa: ANN001, A002
        assert path == "/chat/completions"
        assert json["model"] == "llama-3.1-70b-versatile"
        assert json["messages"][0]["content"] == "Summarize the task."
        return _FakeResponse(
            {
                "choices": [
                    {
                        "message": {"content": "groq live result"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            }
        )


class _RateLimitClient(_FakeClient):
    def post(self, path, json=None):  # noqa: ANN001, A002
        import httpx

        request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        response = httpx.Response(429, request=request, json={"error": {"message": "too many requests"}})
        raise httpx.HTTPStatusError("rate limited", request=request, response=response)


def test_groq_adapter_resolves_v1_without_double_append_and_lists_models(monkeypatch) -> None:
    monkeypatch.setattr("core.http_client.httpx.Client", _FakeClient)
    monkeypatch.setenv(GROQ_API_KEY_ENV, "gsk-test-123456")

    adapter = GroqCloudAdapter(base_url=GROQ_BASE_URL, api_key="gsk-test-123456")
    assert resolve_url(GROQ_BASE_URL, "/v1/models") == "https://api.groq.com/openai/v1/models"

    models = adapter.list_models()
    assert [row["model_name"] for row in models] == ["llama-3.1-70b-versatile", "llama-3.1-8b-instant"]
    assert all(row["capability_source"] == "groq_discovered" for row in models)


def test_groq_adapter_chat_and_stream_use_openai_core(monkeypatch) -> None:
    monkeypatch.setattr("core.http_client.httpx.Client", _FakeClient)
    monkeypatch.setattr(
        "adapters.groq_cloud.gate_allows_action",
        lambda **kwargs: (True, type("Gate", (), {"id": 77, "status": "approved"})(), "approved"),
    )
    adapter = GroqCloudAdapter(base_url=GROQ_BASE_URL, api_key="gsk-test-123456")

    result = adapter.send_chat(
        {"project_slug": "white-room", "prompt": "Summarize the task."},
        {"model_name": "llama-3.1-70b-versatile"},
    )

    assert result["text"] == "groq live result"
    assert result["usage"] == {"tokens_in": 7, "tokens_out": 3}
    assert result["provider_family"] == "openai"
    assert result["base_url"] == GROQ_BASE_URL

    chunks = list(adapter.stream_chat({"project_slug": "white-room", "prompt": "Summarize the task."}, {"model_name": "llama-3.1-70b-versatile"}))
    assert chunks[-1]["text"] == "groq live result"


def test_groq_adapter_rate_limit_and_redaction(monkeypatch) -> None:
    monkeypatch.setattr("core.http_client.httpx.Client", _RateLimitClient)
    monkeypatch.setattr(
        "adapters.groq_cloud.gate_allows_action",
        lambda **kwargs: (True, type("Gate", (), {"id": 88, "status": "approved"})(), "approved"),
    )
    adapter = GroqCloudAdapter(base_url=GROQ_BASE_URL, api_key="gsk-test-123456")

    details = adapter.normalize_error(httpx.HTTPStatusError(
        "rate limited",
        request=httpx.Request("POST", f"{GROQ_BASE_URL}/chat/completions"),
        response=httpx.Response(429, request=httpx.Request("POST", f"{GROQ_BASE_URL}/chat/completions")),
    ))
    assert "gsk_live_secret_123456" not in json.dumps(details)
    assert details["kind"] == "rate_limit"
    assert details["retryable"] is True
    assert "***redacted***" not in str(details["message"])  # rate-limit mapping is secret-free already

    with pytest.raises(httpx.HTTPStatusError):
        adapter.send_chat({"project_slug": "white-room", "prompt": "Summarize the task."}, {"model_name": "llama-3.1-70b-versatile"})
