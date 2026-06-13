from __future__ import annotations

import socket
from urllib.parse import urlparse

import httpx


LOCALHOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_TIMEOUT = 30.0


def guard(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("local HTTP base_url must use http or https")
    if parsed.hostname not in LOCALHOSTS:
        raise ValueError("local HTTP base_url must point to localhost")
    return base_url.rstrip("/")


def create_client(base_url: str, timeout: float = DEFAULT_TIMEOUT) -> httpx.Client:
    return httpx.Client(base_url=guard(base_url), timeout=timeout)


def ensure_reachable(base_url: str, timeout: float = 1.0) -> None:
    parsed = urlparse(guard(base_url))
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname or "127.0.0.1", port), timeout=timeout):
            return
    except OSError as exc:
        raise RuntimeError(f"no local server reachable at {parsed.geturl()}") from exc
