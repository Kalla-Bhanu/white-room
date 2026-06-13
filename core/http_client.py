from __future__ import annotations

import time
from typing import Any

import httpx


DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 60.0
DEFAULT_WRITE_TIMEOUT = 10.0
DEFAULT_POOL_TIMEOUT = 10.0
DEFAULT_TIMEOUT = httpx.Timeout(
    connect=DEFAULT_CONNECT_TIMEOUT,
    read=DEFAULT_READ_TIMEOUT,
    write=DEFAULT_WRITE_TIMEOUT,
    pool=DEFAULT_POOL_TIMEOUT,
)


def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: httpx.Timeout | float = DEFAULT_TIMEOUT,
    retries: int = 1,
) -> dict[str, Any]:
    response = request_response(
        base_url,
        path,
        method=method,
        headers=headers,
        payload=payload,
        timeout=timeout,
        retries=retries,
    )
    return response.json()


def request_response(
    base_url: str,
    path: str,
    *,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: httpx.Timeout | float = DEFAULT_TIMEOUT,
    retries: int = 1,
) -> httpx.Response:
    normalized_base = base_url.rstrip("/")
    request_path = path if path.startswith("/") else f"/{path}"
    attempts = max(1, retries + 1)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with httpx.Client(base_url=normalized_base, timeout=timeout, headers=headers) as client:
                request_method = method.upper()
                if request_method == "GET" and hasattr(client, "get"):
                    response = client.get(request_path)
                elif request_method == "POST" and hasattr(client, "post"):
                    response = client.post(request_path, json=payload)
                elif hasattr(client, "request"):
                    response = client.request(request_method, request_path, json=payload)
                else:  # pragma: no cover - defensive fallback for unusual fakes
                    raise AttributeError(f"client does not support {request_method.lower()}")
                if response.status_code >= 500 and attempt < attempts - 1:
                    time.sleep(0.1)
                    last_error = httpx.HTTPStatusError(
                        f"server error {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                    continue
                response.raise_for_status()
                return response
        except httpx.HTTPStatusError as exc:
            status_code = getattr(exc.response, "status_code", None)
            if status_code is not None and int(status_code) >= 500 and attempt < attempts - 1:
                time.sleep(0.1)
                last_error = exc
                continue
            raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(0.1)
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("request failed")
