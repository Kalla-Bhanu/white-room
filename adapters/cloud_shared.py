from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

from core.approvals import gate_allows_action
from core.http_client import request_json as request_json_with_retry
from core.usage import record_usage as record_usage_event


ACTION_TYPE = "cloud_chat"


@dataclass(frozen=True)
class CloudChatSpec:
    adapter_name: str
    endpoint_class: str
    provider_family: str
    project_slug: str
    task_id: int | None
    conversation_id: int | None
    base_url: str
    api_key: str
    model_name: str
    prompt: str
    messages: list[dict[str, Any]]
    request_path: str
    headers: dict[str, str]
    payload: dict[str, Any]
    approval_gate_id: int
    approval_status: str
    payload_summary: str


def build_cloud_chat_spec(
    *,
    adapter_name: str,
    endpoint_class: str,
    context_packet: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> CloudChatSpec:
    options = options or {}
    project_slug = str(context_packet.get("project_slug") or "white-room")
    task_id = _optional_int(context_packet.get("task_id") or options.get("task_id"))
    conversation_id = _optional_int(context_packet.get("conversation_id") or options.get("conversation_id"))
    provider_family = _normalize_provider_family(
        context_packet.get("provider_family") or options.get("provider_family") or _default_provider_family(endpoint_class)
    )
    prompt = _extract_prompt(context_packet)
    model_name = _resolve_model_name(context_packet, options, endpoint_class, provider_family)
    api_key = _resolve_api_key(endpoint_class, provider_family, context_packet, options)
    if not api_key:
        raise PermissionError("configured key missing")

    base_url = _resolve_base_url(endpoint_class, provider_family, context_packet, options)
    if not base_url:
        raise ValueError("missing base URL")

    payload_summary = build_cloud_payload_summary(
        endpoint_class=endpoint_class,
        project_slug=project_slug,
        task_id=task_id,
        conversation_id=conversation_id,
        provider_family=provider_family,
        model_name=model_name,
        prompt=prompt,
    )
    allowed, gate, message = gate_allows_action(
        project_slug=project_slug,
        action_type=ACTION_TYPE,
        target_endpoint_id=None,
        payload_summary=payload_summary,
        endpoint_class=endpoint_class,
    )
    if not allowed:
        if message == "configured key missing":
            raise PermissionError("configured key missing")
        raise PermissionError("approval required")

    messages = _build_messages(context_packet, prompt)
    request_path, headers = _request_details(endpoint_class, provider_family, api_key)
    payload = _request_payload(endpoint_class, model_name, messages)

    return CloudChatSpec(
        adapter_name=adapter_name,
        endpoint_class=endpoint_class,
        provider_family=provider_family,
        project_slug=project_slug,
        task_id=task_id,
        conversation_id=conversation_id,
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
        prompt=prompt,
        messages=messages,
        request_path=request_path,
        headers=headers,
        payload=payload,
        approval_gate_id=gate.id,
        approval_status=gate.status,
        payload_summary=payload_summary,
    )


def execute_cloud_chat(spec: CloudChatSpec) -> dict[str, Any]:
    if spec.endpoint_class == "anthropic_compatible_cloud":
        result = _execute_anthropic_chat(spec)
    else:
        result = _execute_openai_style_chat(spec)

    tokens_in = _estimate_tokens(spec.prompt)
    tokens_out = _estimate_tokens(str(result.get("text") or ""))
    record_usage_event(
        endpoint_name=spec.adapter_name.replace("_", "-"),
        endpoint_class=spec.endpoint_class,
        base_url=spec.base_url,
        project_slug=spec.project_slug,
        task_id=spec.task_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        est_cost=float(result.get("est_cost") or 0.0),
    )
    return {
        "adapter": spec.adapter_name,
        "endpoint_class": spec.endpoint_class,
        "provider_family": spec.provider_family,
        "base_url": spec.base_url,
        "model_name": spec.model_name,
        "approval_gate_id": spec.approval_gate_id,
        "approval_status": spec.approval_status,
        **result,
        "usage": {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        },
    }


def stream_cloud_chat(spec: CloudChatSpec):
    result = execute_cloud_chat(spec)
    text = str(result.get("text") or "")
    for delta in _chunk_text_for_stream(text):
        yield {"delta": delta, "done": False}
    yield {
        "delta": "",
        "done": True,
        "text": text,
        "usage": result.get("usage") or {},
        "raw": result.get("raw") or {},
        "finish_reason": result.get("finish_reason") or "stop",
    }


def build_cloud_payload_summary(
    *,
    endpoint_class: str,
    project_slug: str,
    task_id: int | None,
    conversation_id: int | None,
    provider_family: str,
    model_name: str,
    prompt: str,
) -> str:
    payload = {
        "endpoint_class": endpoint_class,
        "project_slug": project_slug,
        "task_id": task_id,
        "conversation_id": conversation_id,
        "provider_family": provider_family,
        "model_name": model_name,
        "prompt": prompt[:160],
    }
    return json.dumps(payload, sort_keys=True)


def _execute_openai_style_chat(spec: CloudChatSpec) -> dict[str, Any]:
    payload = dict(spec.payload)
    payload["stream"] = False
    data = request_json_with_retry(
        spec.base_url,
        spec.request_path,
        method="POST",
        headers=spec.headers,
        payload=payload,
        retries=1,
    )

    text = _openai_text(data)
    if not text:
        raise RuntimeError(f"{spec.endpoint_class} response did not include output text")

    usage = data.get("usage") or {}
    tokens_in = int(usage.get("prompt_tokens") or usage.get("input_tokens") or _estimate_tokens(spec.prompt))
    tokens_out = int(usage.get("completion_tokens") or usage.get("output_tokens") or _estimate_tokens(text))
    return {
        "text": text,
        "finish_reason": _openai_finish_reason(data),
        "raw": data,
        "est_cost": 0.0,
        "usage": {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        },
    }


def _execute_anthropic_chat(spec: CloudChatSpec) -> dict[str, Any]:
    payload = dict(spec.payload)
    payload["stream"] = False
    data = request_json_with_retry(
        spec.base_url,
        spec.request_path,
        method="POST",
        headers=spec.headers,
        payload=payload,
        retries=1,
    )

    text = _anthropic_text(data)
    if not text:
        raise RuntimeError(f"{spec.endpoint_class} response did not include output text")

    usage = data.get("usage") or {}
    tokens_in = int(usage.get("input_tokens") or _estimate_tokens(spec.prompt))
    tokens_out = int(usage.get("output_tokens") or _estimate_tokens(text))
    return {
        "text": text,
        "finish_reason": str(data.get("stop_reason") or "stop"),
        "raw": data,
        "est_cost": 0.0,
        "usage": {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        },
    }


def _request_details(endpoint_class: str, provider_family: str, api_key: str) -> tuple[str, dict[str, str]]:
    if endpoint_class == "anthropic_compatible_cloud":
        return "/v1/messages", {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    if provider_family == "gemini":
        return "/chat/completions", {
            "x-goog-api-key": api_key,
            "content-type": "application/json",
        }
    return "/chat/completions", {
        "Authorization": f"Bearer {api_key}",
        "content-type": "application/json",
    }


def _request_payload(endpoint_class: str, model_name: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    if endpoint_class == "anthropic_compatible_cloud":
        return {
            "model": model_name,
            "max_tokens": 1024,
            "messages": messages,
        }
    return {
        "model": model_name,
        "messages": messages,
        "temperature": 0,
    }


def _openai_text(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    first = choices[0] or {}
    message = first.get("message") or {}
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "".join(str(part.get("text") or part.get("content") or "") for part in content if isinstance(part, dict)).strip()
    text = first.get("text")
    if isinstance(text, str):
        return text.strip()
    delta = first.get("delta") or {}
    if isinstance(delta, dict):
        content = delta.get("content")
        if isinstance(content, str):
            return content.strip()
    return ""


def _anthropic_text(data: dict[str, Any]) -> str:
    content = data.get("content") or []
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
        return "".join(parts).strip()
    if isinstance(content, str):
        return content.strip()
    return ""


def _openai_finish_reason(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return "stop"
    first = choices[0] or {}
    reason = first.get("finish_reason")
    return str(reason or "stop")


def _build_messages(context_packet: dict[str, Any], prompt: str) -> list[dict[str, Any]]:
    messages = context_packet.get("messages")
    if isinstance(messages, list) and messages:
        sanitized: list[dict[str, Any]] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user").strip() or "user"
            content = item.get("content")
            if isinstance(content, str):
                text = content.strip()
            elif isinstance(content, list):
                text = "".join(str(piece.get("text") or piece.get("content") or "") for piece in content if isinstance(piece, dict)).strip()
            else:
                text = str(content or "").strip()
            if text:
                sanitized.append({"role": role, "content": text})
        if sanitized:
            return sanitized
    return [{"role": "user", "content": prompt}]


def _resolve_api_key(endpoint_class: str, provider_family: str, context_packet: dict[str, Any], options: dict[str, Any]) -> str:
    explicit = str(options.get("api_key") or context_packet.get("api_key") or "").strip()
    if explicit:
        return explicit

    env_names: list[str]
    if endpoint_class == "openai_compatible_cloud":
        env_names = ["OPENAI_COMPAT_API_KEY", "OPENAI_API_KEY"]
    elif endpoint_class == "anthropic_compatible_cloud":
        env_names = ["ANTHROPIC_API_KEY"]
    else:
        env_names = _provider_env_names(provider_family)

    for name in env_names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _resolve_base_url(endpoint_class: str, provider_family: str, context_packet: dict[str, Any], options: dict[str, Any]) -> str:
    explicit = str(options.get("base_url") or context_packet.get("base_url") or "").strip()
    if explicit:
        return explicit.rstrip("/")

    env_name = {
        "openai_compatible_cloud": "OPENAI_COMPAT_BASE_URL",
        "anthropic_compatible_cloud": "ANTHROPIC_BASE_URL",
        "provider_specific_cloud": _provider_base_url_env(provider_family),
    }.get(endpoint_class, "")
    if env_name:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value.rstrip("/")

    defaults = {
        "openai_compatible_cloud": "https://api.openai.com/v1",
        "anthropic_compatible_cloud": "https://api.anthropic.com",
        "provider_specific_cloud": _provider_default_base_url(provider_family),
    }
    return defaults.get(endpoint_class, "").rstrip("/")


def _default_provider_family(endpoint_class: str) -> str:
    if endpoint_class == "provider_specific_cloud":
        return "openrouter"
    if endpoint_class == "anthropic_compatible_cloud":
        return "anthropic"
    return "openai"


def _provider_env_names(provider_family: str) -> list[str]:
    mapping = {
        "gemini": ["GEMINI_API_KEY"],
        "deepseek": ["DEEPSEEK_API_KEY"],
        "openrouter": ["OPENROUTER_API_KEY"],
        "groq": ["GROQ_API_KEY"],
        "opencode": ["OPENCODE_API_KEY"],
        "provider_specific": ["PROVIDER_SPECIFIC_API_KEY"],
        "openai": ["OPENAI_COMPAT_API_KEY", "OPENAI_API_KEY"],
        "anthropic": ["ANTHROPIC_API_KEY"],
    }
    return mapping.get(provider_family, ["PROVIDER_SPECIFIC_API_KEY"])


def _provider_base_url_env(provider_family: str) -> str:
    return {
        "gemini": "GEMINI_BASE_URL",
        "deepseek": "DEEPSEEK_BASE_URL",
        "openrouter": "OPENROUTER_BASE_URL",
        "groq": "GROQ_BASE_URL",
        "opencode": "OPENCODE_BASE_URL",
        "provider_specific": "PROVIDER_SPECIFIC_BASE_URL",
    }.get(provider_family, "PROVIDER_SPECIFIC_BASE_URL")


def _provider_default_base_url(provider_family: str) -> str:
    return {
        "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
        "deepseek": "https://api.deepseek.com",
        "openrouter": "https://openrouter.ai/api/v1",
        "groq": "https://api.groq.com/openai/v1",
        "opencode": "https://api.opencode.ai/v1",
        "provider_specific": "https://openrouter.ai/api/v1",
    }.get(provider_family, "https://openrouter.ai/api/v1")


def _resolve_model_name(
    context_packet: dict[str, Any],
    options: dict[str, Any],
    endpoint_class: str,
    provider_family: str,
) -> str:
    model = str(
        options.get("model_name")
        or options.get("model")
        or context_packet.get("model_name")
        or context_packet.get("model")
        or ""
    ).strip()
    if model:
        return model
    if endpoint_class == "anthropic_compatible_cloud":
        return "claude-3-5-sonnet-latest"
    if provider_family == "gemini":
        return "gemini-2.0-flash"
    if provider_family == "deepseek":
        return "deepseek-chat"
    if provider_family == "groq":
        return "llama-3.1-70b-versatile"
    if provider_family == "opencode":
        return "opencode-default"
    return "gpt-4.1-mini"


def _normalize_provider_family(value: object) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    return text or "provider_specific"


def _extract_prompt(context_packet: dict[str, Any]) -> str:
    prompt = str(
        context_packet.get("prompt")
        or context_packet.get("input_text")
        or context_packet.get("packet_text")
        or ""
    ).strip()
    if prompt:
        return prompt
    messages = context_packet.get("messages")
    if isinstance(messages, list):
        parts: list[str] = []
        for item in messages:
            if isinstance(item, dict):
                content = item.get("content")
                if isinstance(content, str):
                    parts.append(content.strip())
        joined = "\n".join(part for part in parts if part)
        if joined.strip():
            return joined.strip()
    raise ValueError("missing prompt text for cloud call")


def _optional_int(value: object) -> int | None:
    if value in {None, "", "none"}:
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _estimate_tokens(text: str) -> int:
    words = len(text.split())
    return max(1, words or 1)


def _chunk_text_for_stream(text: str, chunk_size: int = 10) -> list[str]:
    words = text.split()
    if len(words) <= chunk_size:
        return [text] if text else []
    chunks: list[str] = []
    for index in range(0, len(words), chunk_size):
        chunk = " ".join(words[index : index + chunk_size]).strip()
        if chunk:
            chunks.append(chunk + (" " if index + chunk_size < len(words) else ""))
    return chunks
