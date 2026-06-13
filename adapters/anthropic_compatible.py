from __future__ import annotations

from typing import Any

from adapters.base import AdapterBase
from adapters.cloud_shared import build_cloud_chat_spec, execute_cloud_chat, stream_cloud_chat


class AnthropicCompatibleAdapter(AdapterBase):
    name = "anthropic_compatible_cloud"

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
            "preview": "Anthropic-compatible cloud request preview",
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
        return super().normalize_error(raw_error)
