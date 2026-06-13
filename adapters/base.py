from __future__ import annotations

import re
from typing import Any

import httpx

from core.http_local import guard


class NotSupported(RuntimeError):
    """Raised when an adapter lane is not enabled yet."""


def _safe_message(text: str) -> str:
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+", r"\1=[redacted]", text)
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-[redacted]", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer [redacted]", text)
    return text[:240].strip() or "adapter error"


class Adapter:
    """Shared adapter contract for all endpoint classes."""

    name = "base"

    def __init__(self, base_url: str = "") -> None:
        self.base_url = base_url

    def health_check(self) -> dict[str, Any]:
        raise NotSupported(f"{self.name} health_check is not enabled")

    def list_models(self) -> list[dict[str, Any]]:
        raise NotSupported(f"{self.name} list_models is not enabled")

    def send_chat(self, context_packet: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, Any]:
        raise NotSupported(f"{self.name} send_chat is not enabled")

    def stream_chat(self, context_packet: dict[str, Any], options: dict[str, Any] | None = None):
        raise NotSupported(f"{self.name} stream_chat is not enabled")

    def estimate_cost(self, tokens_in: int, tokens_out: int) -> dict[str, Any]:
        raise NotSupported(f"{self.name} estimate_cost is not enabled")

    def normalize_error(self, raw_error: object) -> dict[str, Any]:
        kind = "unknown"
        retryable = False
        message = "adapter error"
        if isinstance(raw_error, NotSupported):
            kind = "unavailable"
            message = "lane not enabled"
        elif isinstance(raw_error, TimeoutError):
            kind = "timeout"
            retryable = True
            message = "request timed out"
        elif isinstance(raw_error, httpx.TimeoutException):
            kind = "timeout"
            retryable = True
            message = "request timed out"
        elif isinstance(raw_error, httpx.HTTPStatusError):
            status_code = getattr(raw_error.response, "status_code", None)
            if status_code == 401 or status_code == 403:
                kind = "auth"
                message = "authorization failed"
            elif status_code == 429:
                kind = "rate_limit"
                retryable = True
                message = "rate limited"
            elif status_code and int(status_code) >= 500:
                kind = "unavailable"
                retryable = True
                message = "remote service unavailable"
            else:
                kind = "bad_request"
                message = "remote request rejected"
        elif isinstance(raw_error, ValueError):
            kind = "bad_request"
            message = _safe_message(str(raw_error))
        elif isinstance(raw_error, ConnectionError):
            kind = "unavailable"
            retryable = True
            message = "local runner unavailable"
        elif isinstance(raw_error, PermissionError):
            kind = "auth"
            message = "permission denied"
        return {"kind": kind, "retryable": retryable, "message": message}

    def record_usage(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotSupported(f"{self.name} record_usage is not enabled")

    # Compatibility layer for earlier packets.
    def prepare(self, packet: dict[str, Any]) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "base_url": getattr(self, "base_url", ""),
            "local_only": False,
            "packet": packet,
        }

    def dry_run(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "preview": "adapter dry-run preview",
            "request": request,
        }

    def call(self, request: dict[str, Any]) -> dict[str, Any]:
        raise NotSupported(f"{self.name} call is not enabled")


AdapterBase = Adapter


__all__ = ["Adapter", "AdapterBase", "NotSupported", "guard"]
