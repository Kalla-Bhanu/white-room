from __future__ import annotations

import json
import os
from typing import Any

from adapters.base import AdapterBase, NotSupported
from adapters.openai_compatible import build_openai_chat_payload, normalize_error as normalize_openai_error, parse_openai_chat_text, parse_openai_usage
from core.approvals import gate_allows_action
from core.codex_modes import codex_mode_label, normalize_codex_mode
from core.http_client import request_json
from core.models_catalog import discover_models


ACTION_TYPE = "codex_chat"
CODEX_API_KEY_ENV = "CODEX_LB_API_KEY"
CODEX_BASE_URL_ENV = "CODEX_LB_BASE_URL"


class CodexLBAdapter(AdapterBase):
    name = "codex_lb"

    def __init__(
        self,
        mode: str = "manual_execution",
        base_url: str = "",
        api_key: str = "",
        live_enabled: bool = False,
    ) -> None:
        self.mode = normalize_codex_mode(mode)
        self.base_url = str(base_url or "").strip()
        self.api_key = str(api_key or "").strip()
        self.live_enabled = bool(live_enabled)

    def prepare(self, packet: dict[str, Any]) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "mode": self.mode,
            "mode_label": codex_mode_label(self.mode),
            "manual_only": self.mode == "manual_execution",
            "live_mode": self.is_live_mode,
            "base_url": self.base_url,
            "packet": packet,
        }

    def dry_run(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "mode": self.mode,
            "preview": f"{codex_mode_label(self.mode)} preview",
            "request": request,
        }

    def list_models(self) -> list[dict[str, Any]]:
        if not self.is_live_mode:
            raise NotSupported(f"{self.name} list_models is not enabled")
        base_url = self._resolve_base_url({}, {})
        api_key = self._resolve_api_key({}, {}, required=True)
        return discover_models(base_url, api_key)

    def send_chat(self, context_packet: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.is_live_mode:
            raise NotImplementedError(
                f"{self.name} remains manual-only in mode '{self.mode}' until a later approval-gated packet"
            )

        options = options or {}
        project_slug = str(context_packet.get("project_slug") or "white-room")
        chat_mode = str(context_packet.get("mode") or options.get("mode") or "ask").strip() or "ask"
        prompt = _extract_prompt(context_packet)
        model_name = self._resolve_model_name(context_packet, options)
        base_url = self._resolve_base_url(context_packet, options)
        api_key = self._resolve_api_key(context_packet, options, required=True)
        messages = _build_messages(context_packet, prompt)
        payload = build_openai_chat_payload(messages, model_name=model_name, stream=False, temperature=0.0)
        payload_summary = json.dumps(
            {
                "adapter": self.name,
                "base_url": base_url,
                "chat_mode": chat_mode,
                "conversation_id": context_packet.get("conversation_id"),
                "mode": self.mode,
                "model_name": model_name,
                "project_slug": project_slug,
                "prompt": prompt[:160],
                "task_id": context_packet.get("task_id"),
            },
            sort_keys=True,
        )
        allowed, gate, message = gate_allows_action(
            project_slug=project_slug,
            action_type=ACTION_TYPE,
            target_endpoint_id=None,
            payload_summary=payload_summary,
            endpoint_class=self.name,
            mode=chat_mode,
        )
        if not allowed:
            raise PermissionError(f"approval required for Codex LB {chat_mode} chat")

        request_path = self._request_path(base_url, "/chat/completions")
        data = request_json(
            base_url,
            request_path,
            method="POST",
            headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
            payload=payload,
            retries=1,
        )
        text = parse_openai_chat_text(data)
        usage = parse_openai_usage(data, prompt_text=prompt)
        return {
            "adapter": self.name,
            "endpoint_class": self.name,
            "provider_family": "openai",
            "base_url": base_url,
            "model_name": model_name,
            "approval_gate_id": gate.id,
            "approval_status": gate.status,
            "text": text,
            "finish_reason": str((data.get("choices") or [{}])[0].get("finish_reason") or "stop"),
            "raw": data,
            "usage": usage,
        }

    def stream_chat(self, context_packet: dict[str, Any], options: dict[str, Any] | None = None):
        result = self.send_chat(context_packet, options)
        for chunk in _chunk_text(result["text"]):
            yield {"delta": chunk, "done": False}
        yield {
            "delta": "",
            "done": True,
            "text": result["text"],
            "usage": result["usage"],
            "raw": result["raw"],
            "finish_reason": result["finish_reason"],
        }

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
            if "approval required" in message or "needs approval" in message:
                return {"kind": "approval_required", "retryable": False, "message": str(raw_error)}
        return normalize_openai_error(raw_error)

    @property
    def is_live_mode(self) -> bool:
        return self.live_enabled and self.mode in {"api_preview", "cli_preview"}

    def _resolve_base_url(self, context_packet: dict[str, Any], options: dict[str, Any] | None = None) -> str:
        options = options or {}
        candidates = (
            options.get("base_url"),
            context_packet.get("base_url"),
            self.base_url,
            os.environ.get(CODEX_BASE_URL_ENV, ""),
        )
        for candidate in candidates:
            text = str(candidate or "").strip()
            if text:
                return text.rstrip("/")
        raise ValueError("missing base URL")

    def _resolve_api_key(
        self,
        context_packet: dict[str, Any],
        options: dict[str, Any] | None = None,
        *,
        required: bool = False,
    ) -> str:
        options = options or {}
        candidates = (
            options.get("api_key"),
            context_packet.get("api_key"),
            self.api_key,
            os.environ.get(CODEX_API_KEY_ENV, ""),
        )
        for candidate in candidates:
            text = str(candidate or "").strip()
            if text:
                return text
        if required:
            raise PermissionError("configured key missing")
        return ""

    def _resolve_model_name(self, context_packet: dict[str, Any], options: dict[str, Any]) -> str:
        candidates = (
            options.get("model_name"),
            context_packet.get("model_name"),
            context_packet.get("preferred_model"),
        )
        for candidate in candidates:
            text = str(candidate or "").strip()
            if text:
                return text
        discovered = self.list_models()
        if discovered:
            first = discovered[0]
            text = str(first.get("model_name") or "").strip()
            if text:
                return text
        return "codex-lb"

    def _request_path(self, base_url: str, suffix: str) -> str:
        normalized = base_url.rstrip("/")
        return suffix if normalized.endswith("/v1") else f"/v1{suffix}"


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
                text = "".join(
                    str(piece.get("text") or piece.get("content") or "")
                    for piece in content
                    if isinstance(piece, dict)
                ).strip()
            else:
                text = str(content or "").strip()
            if text:
                sanitized.append({"role": role, "content": text})
        if sanitized:
            return sanitized
    return [{"role": "user", "content": prompt}]


def _extract_prompt(context_packet: dict[str, Any]) -> str:
    prompt = context_packet.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    messages = context_packet.get("messages")
    if isinstance(messages, list):
        for item in reversed(messages):
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    return ""


def _chunk_text(text: str, chunk_size: int = 10) -> list[str]:
    clean = str(text or "")
    if not clean:
        return [""]
    return [clean[index : index + chunk_size] for index in range(0, len(clean), chunk_size)]
