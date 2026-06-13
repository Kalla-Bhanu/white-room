from __future__ import annotations

import json
from typing import Any

from adapters.base import AdapterBase
from adapters.cloud_shared import build_cloud_chat_spec, execute_cloud_chat, stream_cloud_chat
from core.http_client import DEFAULT_TIMEOUT, request_json, request_response
from core.secrets import redact


def resolve_url(base_url: str, path: str) -> str:
    base = str(base_url or "").rstrip("/")
    if not base:
        return path if path.startswith("/") else f"/{path}"

    request_path = str(path or "").strip()
    if not request_path.startswith("/"):
        request_path = f"/{request_path}"

    if base.endswith("/v1") and request_path.startswith("/v1/"):
        request_path = request_path[3:]
    return f"{base}{request_path}"


def build_openai_chat_payload(
    messages: list[dict[str, Any]],
    *,
    model_name: str,
    stream: bool = False,
    temperature: float = 0.0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model_name,
        "messages": _sanitize_messages(messages),
        "stream": bool(stream),
        "temperature": temperature,
    }
    if extra:
        payload.update(extra)
    return payload


def parse_openai_chat_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    first = choices[0] or {}
    message = first.get("message") or {}
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "".join(
                str(part.get("text") or part.get("content") or "")
                for part in content
                if isinstance(part, dict)
            ).strip()
    text = first.get("text")
    if isinstance(text, str):
        return text.strip()
    delta = first.get("delta") or {}
    if isinstance(delta, dict):
        content = delta.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "".join(
                str(part.get("text") or part.get("content") or "")
                for part in content
                if isinstance(part, dict)
            ).strip()
    return ""


def parse_openai_usage(payload: dict[str, Any], prompt_text: str = "") -> dict[str, int]:
    usage = payload.get("usage") or {}
    text = parse_openai_chat_text(payload)
    tokens_in = int(usage.get("prompt_tokens") or usage.get("input_tokens") or _estimate_tokens(prompt_text))
    tokens_out = int(usage.get("completion_tokens") or usage.get("output_tokens") or _estimate_tokens(text))
    return {"tokens_in": tokens_in, "tokens_out": tokens_out}


def parse_openai_stream_text(raw_stream: str) -> dict[str, Any]:
    chunks: list[str] = []
    finish_reason = "stop"
    for raw_line in raw_stream.splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        chunk = _parse_stream_chunk_text(data)
        if chunk:
            chunks.append(chunk)
        reason = _openai_finish_reason(data)
        if reason:
            finish_reason = reason
    text = "".join(chunks)
    return {"chunks": chunks, "text": text, "finish_reason": finish_reason}


def normalize_error(raw_error: object) -> dict[str, Any]:
    base = AdapterBase().normalize_error(raw_error)
    safe_message = redact(str(base.get("message") or "adapter error")) or "adapter error"
    safe_message = safe_message.replace("[redacted]", "***redacted***")
    details = dict(base)
    details["message"] = safe_message
    if "detail" in details and details["detail"]:
        details["detail"] = (redact(str(details["detail"])) or "***redacted***").replace("[redacted]", "***redacted***")
    return details


class OpenAICompatibleAdapter(AdapterBase):
    name = "openai_compatible_cloud"

    def __init__(self, base_url: str = "") -> None:
        self.base_url = base_url

    def prepare(self, packet: dict[str, Any]) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "base_url": self.base_url,
            "packet": packet,
        }

    def dry_run(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "preview": "OpenAI-compatible cloud request preview",
            "request": request,
        }

    def send_chat(self, context_packet: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, Any]:
        spec = build_cloud_chat_spec(
            adapter_name=self.name,
            endpoint_class=self.name,
            context_packet=context_packet,
            options=options,
        )
        return execute_cloud_chat(spec)

    def stream_chat(self, context_packet: dict[str, Any], options: dict[str, Any] | None = None):
        spec = build_cloud_chat_spec(
            adapter_name=self.name,
            endpoint_class=self.name,
            context_packet=context_packet,
            options=options,
        )
        yield from stream_cloud_chat(spec)

    def call(self, request: dict[str, Any]) -> dict[str, Any]:
        result = self.send_chat(request, {})
        return {
            "adapter": self.name,
            "base_url": result.get("base_url"),
            "status": "draft",
            "text": result["text"],
            "request": request,
            "response": result.get("raw"),
            "usage": result.get("usage"),
        }

    def normalize_error(self, raw_error: object) -> dict[str, Any]:
        if isinstance(raw_error, PermissionError):
            message = str(raw_error).lower()
            if "configured key missing" in message:
                return {"kind": "missing_key", "retryable": False, "message": "configured key missing"}
            if "approval required" in message:
                return {"kind": "approval_required", "retryable": False, "message": "approval required"}
        return normalize_error(raw_error)


def _sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user").strip() or "user"
        content = item.get("content")
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            text = "".join(
                str(piece.get("text") or piece.get("content") or "")
                for piece in content
                if isinstance(piece, dict)
            ).strip()
        else:
            text = str(content or "").strip()
        if text:
            sanitized.append({"role": role, "content": text})
    return sanitized


def _parse_stream_chunk_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    first = choices[0] or {}
    delta = first.get("delta") or {}
    if isinstance(delta, dict):
        content = delta.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(part.get("text") or part.get("content") or "")
                for part in content
                if isinstance(part, dict)
            )
    message = first.get("message") or {}
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(part.get("text") or part.get("content") or "")
                for part in content
                if isinstance(part, dict)
            )
    text = first.get("text")
    if isinstance(text, str):
        return text
    return ""


def _openai_finish_reason(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return "stop"
    first = choices[0] or {}
    reason = first.get("finish_reason")
    return str(reason or "stop")


def _estimate_tokens(text: str) -> int:
    text = str(text or "").strip()
    if not text:
        return 0
    return max(1, len(text.split()))


__all__ = [
    "DEFAULT_TIMEOUT",
    "OpenAICompatibleAdapter",
    "build_openai_chat_payload",
    "normalize_error",
    "parse_openai_chat_text",
    "parse_openai_stream_text",
    "parse_openai_usage",
    "request_json",
    "request_response",
    "resolve_url",
]
