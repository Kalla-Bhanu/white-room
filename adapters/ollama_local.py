from __future__ import annotations

import time
from typing import Any

from adapters.base import AdapterBase
from core.http_local import create_client, ensure_reachable, guard
from core.usage import record_usage as record_usage_event


class OllamaLocalAdapter(AdapterBase):
    name = "ollama_local"

    def __init__(self, base_url: str = "http://127.0.0.1:11434") -> None:
        self.base_url = guard(base_url)

    def prepare(self, packet: dict[str, Any]) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "base_url": self.base_url,
            "local_only": True,
            "packet": packet,
        }

    def dry_run(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "base_url": self.base_url,
            "preview": "localhost-only Ollama request preview",
            "request": request,
        }

    def health_check(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            ensure_reachable(self.base_url, timeout=0.5)
        except Exception:
            return {
                "reachable": False,
                "key_present": False,
                "latency_ms": None,
                "detail": "localhost runner unavailable",
            }
        latency_ms = max(1, int((time.perf_counter() - started) * 1000))
        return {
            "reachable": True,
            "key_present": False,
            "latency_ms": latency_ms,
            "detail": "localhost Ollama runner reachable",
        }

    def list_models(self) -> list[dict[str, Any]]:
        try:
            ensure_reachable(self.base_url, timeout=0.5)
            with create_client(self.base_url, timeout=5.0) as client:
                response = client.get("/api/tags")
                response.raise_for_status()
                data = response.json()
        except Exception:
            return [
                {
                    "model_name": "llama3.1:latest",
                    "context_window": 128000,
                    "supports_streaming": True,
                    "supports_tools": False,
                    "supports_json": False,
                }
            ]

        models = data.get("models") or []
        if not models:
            return [
                {
                    "model_name": "llama3.1:latest",
                    "context_window": 128000,
                    "supports_streaming": True,
                    "supports_tools": False,
                    "supports_json": False,
                }
            ]
        return [
            {
                "model_name": str(item.get("name") or item.get("model") or "llama3.1:latest"),
                "context_window": int((item.get("details") or {}).get("context_length") or 128000),
                "supports_streaming": True,
                "supports_tools": False,
                "supports_json": False,
            }
            for item in models
        ]

    def send_chat(self, context_packet: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, Any]:
        options = options or {}
        prompt = _request_prompt(context_packet)
        model = _request_model(context_packet, default=str(options.get("model_name") or options.get("model") or "llama3.1"))
        payload = {"model": model, "prompt": prompt, "stream": False}
        ensure_reachable(self.base_url)
        with create_client(self.base_url, timeout=60.0) as client:
            response = client.post("/api/generate", json=payload)
            response.raise_for_status()
            data = response.json()

        text = str(data.get("response") or data.get("message", {}).get("content") or "").strip()
        if not text:
            raise RuntimeError("ollama response did not include output text")

        _record_live_usage(self, context_packet, prompt, text)
        usage = {"tokens_in": _estimate_tokens(prompt), "tokens_out": _estimate_tokens(text)}
        return {
            "text": text,
            "finish_reason": str(data.get("done_reason") or "stop"),
            "usage": usage,
            "raw": data,
        }

    def stream_chat(self, context_packet: dict[str, Any], options: dict[str, Any] | None = None):
        options = options or {}
        prompt = _request_prompt(context_packet)
        model = _request_model(context_packet, default=str(options.get("model_name") or options.get("model") or "llama3.1"))
        payload = {"model": model, "prompt": prompt, "stream": True}
        ensure_reachable(self.base_url)
        with create_client(self.base_url, timeout=60.0) as client:
            with client.stream("POST", "/api/generate", json=payload) as response:
                response.raise_for_status()
                final_text = ""
                final_raw: dict[str, Any] | None = None
                for line in response.iter_lines():
                    if not line:
                        continue
                    data = _decode_json_line(line)
                    if not data:
                        continue
                    final_raw = data
                    delta = str(data.get("response") or "")
                    if delta:
                        final_text += delta
                        yield {"delta": delta, "done": False}
                    if data.get("done"):
                        break
        if final_text:
            _record_live_usage(self, context_packet, prompt, final_text)
        yield {
            "delta": "",
            "done": True,
            "text": final_text,
            "usage": {
                "tokens_in": _estimate_tokens(prompt),
                "tokens_out": _estimate_tokens(final_text),
            },
            "raw": final_raw or {},
        }

    def estimate_cost(self, tokens_in: int, tokens_out: int) -> dict[str, Any]:
        return {"est_usd": 0.0, "basis": "free/local"}

    def record_usage(self, context_packet: dict[str, Any], usage: dict[str, Any], est_cost: float = 0.0) -> dict[str, Any]:
        prompt = str(context_packet.get("prompt") or context_packet.get("packet_text") or context_packet.get("input_text") or "")
        text = str(context_packet.get("response_text") or context_packet.get("output_text") or "")
        record = record_usage_event(
            endpoint_name=self.name.replace("_", "-"),
            endpoint_class=self.name,
            base_url=self.base_url,
            project_slug=str(context_packet.get("project_slug") or "white-room"),
            task_id=context_packet.get("task_id"),
            tokens_in=int(usage.get("tokens_in") or _estimate_tokens(prompt)),
            tokens_out=int(usage.get("tokens_out") or _estimate_tokens(text)),
            est_cost=est_cost,
        )
        return {"usage_event_id": None, "usage_record": record.__dict__}

    def call(self, request: dict[str, Any]) -> dict[str, Any]:
        result = self.send_chat(request, {})
        return {
            "adapter": self.name,
            "base_url": self.base_url,
            "status": "draft",
            "text": result["text"],
            "request": request,
            "response": result["raw"],
        }


def _request_prompt(request: dict[str, Any]) -> str:
    packet = request.get("packet") if isinstance(request, dict) else None
    source = packet if isinstance(packet, dict) else request
    prompt = str(
        source.get("input_text")
        or source.get("prompt")
        or source.get("packet_text")
        or ""
    ).strip()
    if not prompt:
        raise ValueError("missing prompt text for local call")
    return prompt


def _request_model(request: dict[str, Any], default: str) -> str:
    packet = request.get("packet") if isinstance(request, dict) else None
    source = packet if isinstance(packet, dict) else request
    model = str(source.get("model") or request.get("model") or default).strip()
    return model or default


def _record_live_usage(adapter: OllamaLocalAdapter, request: dict[str, Any], prompt: str, text: str) -> None:
    packet = request.get("packet") if isinstance(request, dict) else None
    source = packet if isinstance(packet, dict) else request
    try:
        record_usage_event(
            endpoint_name=adapter.name.replace("_", "-"),
            endpoint_class=adapter.name,
            base_url=adapter.base_url,
            project_slug=str(source.get("project_slug") or "white-room"),
            task_id=source.get("task_id"),
            tokens_in=_estimate_tokens(prompt),
            tokens_out=_estimate_tokens(text),
            est_cost=0.0,
        )
    except Exception as exc:
        raise RuntimeError(f"failed to record usage for {adapter.name}: {exc}") from exc


def _estimate_tokens(text: str) -> int:
    return max(1, len(text.split()))


def _decode_json_line(line: str) -> dict[str, Any] | None:
    import json

    text = line.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
