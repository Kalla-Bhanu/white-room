from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

import httpx

from core.db import connect, init_db
from core.http_local import ensure_reachable, guard
from core.memory import utc_now
from core.models_catalog import discover_models, sync_endpoint_models
from core.secrets import get_secret, redact


LOCAL_ENDPOINT_CLASSES = {"ollama_local", "lmstudio_local"}
REMOTE_MODEL_ENDPOINT_CLASSES = {"codex_lb", "groq_cloud"}
REMOTE_KEY_ENV_NAMES = {
    "codex_lb": ("CODEX_LB_API_KEY",),
    "groq_cloud": ("GROQ_API_KEY",),
}


@dataclass(frozen=True)
class HealthCheckRecord:
    endpoint_id: int
    endpoint_name: str
    endpoint_class: str
    base_url: str
    reachable: bool
    key_present: bool
    last_checked: str
    last_error: str | None
    latency_ms: int | None
    check_type: str
    result: str
    detail: str


def health_check(endpoint_identifier: str) -> HealthCheckRecord:
    endpoint = _resolve_endpoint(endpoint_identifier)
    checked_at = utc_now()
    key_present = _key_presence(endpoint["endpoint_class"], endpoint["required_env_vars"])
    reachable = False
    latency_ms: int | None = None
    last_error: str | None = None
    result = "unavailable"
    detail = "offline -- start Ollama/LM Studio"

    if endpoint["endpoint_class"] in LOCAL_ENDPOINT_CLASSES:
        try:
            base_url = guard(endpoint["base_url"])
        except ValueError as exc:
            last_error = str(exc)
            detail = "non-localhost local refused"
        else:
            start = time.perf_counter()
            try:
                ensure_reachable(base_url, timeout=1.0)
            except RuntimeError as exc:
                last_error = str(exc)
                detail = "offline -- start Ollama/LM Studio"
            else:
                reachable = True
                result = "reachable"
                detail = f"localhost reachable at {base_url}"
                latency_ms = max(1, int((time.perf_counter() - start) * 1000))
                last_error = None
    elif endpoint["endpoint_class"] in REMOTE_MODEL_ENDPOINT_CLASSES:
        if key_present:
            base_url = str(endpoint["base_url"] or "")
            if not base_url:
                detail = "missing base URL"
                last_error = "missing base URL"
            else:
                start = time.perf_counter()
                try:
                    discover_models(base_url, _resolve_key(endpoint["endpoint_class"]))
                except httpx.HTTPStatusError as exc:
                    last_error = redact(str(exc))
                    detail = f"{endpoint['endpoint_class']} health check failed"
                    if getattr(exc.response, "status_code", None) == 429:
                        _record_endpoint_rate_limit(endpoint["id"], retry_after_seconds=_retry_after_seconds(exc.response))
                    else:
                        _record_endpoint_failure(endpoint["id"])
                except Exception as exc:
                    last_error = redact(str(exc))
                    detail = f"{endpoint['endpoint_class']} health check failed"
                    _record_endpoint_failure(endpoint["id"])
                else:
                    reachable = True
                    result = "reachable"
                    detail = f"{endpoint['endpoint_class']} reachable at {base_url}"
                    latency_ms = max(1, int((time.perf_counter() - start) * 1000))
                    last_error = None
                    _record_endpoint_success(endpoint["id"])
        else:
            detail = "key not present; live lane unavailable"
    else:
        if key_present:
            detail = "key presence confirmed; live cloud calls remain disabled"
        else:
            detail = "key not present; metadata-only lane remains unavailable"

    _write_health_rows(
        endpoint_id=endpoint["id"],
        reachable=reachable,
        key_present=key_present,
        last_checked=checked_at,
        last_error=last_error,
        latency_ms=latency_ms,
        detail=detail,
        result=result,
        check_type="health",
    )

    return HealthCheckRecord(
        endpoint_id=endpoint["id"],
        endpoint_name=endpoint["name"],
        endpoint_class=endpoint["endpoint_class"],
        base_url=endpoint["base_url"],
        reachable=reachable,
        key_present=key_present,
        last_checked=checked_at,
        last_error=last_error,
        latency_ms=latency_ms,
        check_type="health",
        result=result,
        detail=detail,
    )


@dataclass(frozen=True)
class ModelSyncRecord:
    endpoint_id: int
    endpoint_name: str
    endpoint_class: str
    models_synced: int
    last_model_sync: str


def sync_models(endpoint_identifier: str) -> ModelSyncRecord:
    endpoint = _resolve_endpoint(endpoint_identifier)
    if endpoint["endpoint_class"] not in REMOTE_MODEL_ENDPOINT_CLASSES:
        raise ValueError(f"model sync is not enabled for '{endpoint['endpoint_class']}'")

    if not _key_presence(endpoint["endpoint_class"], endpoint["required_env_vars"]):
        raise PermissionError("configured key missing")

    base_url = str(endpoint["base_url"] or "")
    if not base_url:
        raise ValueError("missing base URL")

    api_key = _resolve_key(endpoint["endpoint_class"])
    try:
        discovered = discover_models(base_url, api_key)
    except httpx.HTTPStatusError as exc:
        if getattr(exc.response, "status_code", None) == 429:
            _record_endpoint_rate_limit(endpoint["id"], retry_after_seconds=_retry_after_seconds(exc.response))
            raise RuntimeError(f"{endpoint['endpoint_class']} rate limited") from exc
        _record_endpoint_failure(endpoint["id"])
        raise
    except Exception:
        _record_endpoint_failure(endpoint["id"])
        raise
    synced = sync_endpoint_models(int(endpoint["id"]), discovered)
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            "SELECT last_model_sync FROM endpoints WHERE id = ?",
            (int(endpoint["id"]),),
        ).fetchone()
    last_model_sync = str(row["last_model_sync"]) if row and row["last_model_sync"] else utc_now()
    _record_endpoint_success(endpoint["id"])
    return ModelSyncRecord(
        endpoint_id=int(endpoint["id"]),
        endpoint_name=str(endpoint["name"]),
        endpoint_class=str(endpoint["endpoint_class"]),
        models_synced=len(synced),
        last_model_sync=last_model_sync,
    )


def runner_status_snapshot() -> dict[str, object]:
    endpoints = [
        health_check(str(row["endpoint_class"]).replace("_", "-"))
        for row in _list_local_endpoint_rows()
    ]
    reachable = any(endpoint.reachable for endpoint in endpoints)
    return {
        "status": "reachable" if reachable else "unavailable",
        "reachable": reachable,
        "message": "local runner is available" if reachable else "offline -- start Ollama/LM Studio",
        "checked_at": utc_now(),
        "endpoints": [
            {
                "endpoint_id": item.endpoint_id,
                "name": item.endpoint_name,
                "endpoint_class": item.endpoint_class,
                "base_url": item.base_url,
                "reachable": item.reachable,
                "key_present": item.key_present,
                "last_checked": item.last_checked,
                "last_error": item.last_error,
                "latency_ms": item.latency_ms,
                "result": item.result,
                "detail": item.detail,
            }
            for item in endpoints
        ],
    }


def topbar_health_summary(
    active_lane: str | None = None,
    runner_snapshot: dict[str, object] | None = None,
) -> dict[str, object]:
    snapshot = runner_snapshot or runner_status_snapshot()
    lane = _normalize_identifier(active_lane or "")
    reachable = bool(snapshot.get("reachable"))

    if lane in {"manual-claude", "manual"}:
        return {
            "lane": "manual_claude",
            "label": "Claude",
            "status": "manual",
            "detail": "manual lane",
            "badge_class": "accent-4",
            "dot_class": "warn",
            "reachable": False,
        }
    if lane == "codex-lb":
        return {
            "lane": "codex_lb",
            "label": "Codex",
            "status": "manual",
            "detail": "manual execution lane",
            "badge_class": "accent-3",
            "dot_class": "warn",
            "reachable": False,
        }
    if lane == "ollama-local":
        return {
            "lane": "ollama_local",
            "label": "Ollama",
            "status": "reachable" if reachable else "offline",
            "detail": snapshot["message"] if reachable else "offline -- start Ollama/LM Studio",
            "badge_class": "accent-5" if reachable else "accent-3",
            "dot_class": "ok" if reachable else "bad",
            "reachable": reachable,
        }
    if lane == "lmstudio-local":
        return {
            "lane": "lmstudio_local",
            "label": "LM Studio",
            "status": "reachable" if reachable else "offline",
            "detail": snapshot["message"] if reachable else "offline -- start Ollama/LM Studio",
            "badge_class": "accent-5" if reachable else "accent-3",
            "dot_class": "ok" if reachable else "bad",
            "reachable": reachable,
        }
    if lane.endswith("-cloud"):
        family = lane.replace("-cloud", "").replace("-", " ").title()
        return {
            "lane": lane.replace("-", "_"),
            "label": family if family else "Cloud",
            "status": "gated",
            "detail": "key-gated lane",
            "badge_class": "accent-4",
            "dot_class": "warn",
            "reachable": False,
        }
    return {
        "lane": lane or "deterministic",
        "label": "Router" if lane else "Router",
        "status": "reachable" if reachable else "offline",
        "detail": snapshot["message"],
        "badge_class": "accent-2" if reachable else "accent-3",
        "dot_class": "ok" if reachable else "bad",
        "reachable": reachable,
    }


def _write_health_rows(
    *,
    endpoint_id: int,
    reachable: bool,
    key_present: bool,
    last_checked: str,
    last_error: str | None,
    latency_ms: int | None,
    detail: str,
    result: str,
    check_type: str,
) -> None:
    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO endpoint_health (endpoint_id, reachable, key_present, last_checked, last_error, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                endpoint_id,
                int(reachable),
                int(key_present),
                last_checked,
                last_error,
                latency_ms,
            ),
        )
        conn.execute(
            """
            INSERT INTO availability_checks (endpoint_id, check_type, result, detail, checked_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                endpoint_id,
                check_type,
                result,
                detail,
                last_checked,
            ),
        )
        conn.commit()


def _key_presence(endpoint_class: str, required_env_vars_json: str | None) -> bool:
    required_env_vars: list[str] = []
    try:
        names = json.loads(required_env_vars_json) if required_env_vars_json else []
    except json.JSONDecodeError:
        names = []
    if not isinstance(names, list):
        names = []
    required_env_vars.extend(str(name) for name in names if str(name).strip())
    for fallback in REMOTE_KEY_ENV_NAMES.get(endpoint_class, ()):  # codex-lb and remote provider keys
        if fallback not in required_env_vars:
            required_env_vars.append(fallback)
    if not required_env_vars:
        return True
    return all(bool(get_secret(name)) for name in required_env_vars)


def _resolve_key(endpoint_class: str) -> str:
    for name in REMOTE_KEY_ENV_NAMES.get(endpoint_class, ()):
        value = get_secret(name)
        if value:
            return value
    return ""


def _resolve_endpoint(identifier: str) -> dict[str, object]:
    target = _normalize_identifier(identifier)
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT
                e.id,
                e.name,
                e.endpoint_class,
                COALESCE(e.base_url, p.base_url, '') AS base_url,
                e.profile_id,
                COALESCE(p.required_env_vars, '[]') AS required_env_vars
            FROM endpoints AS e
            LEFT JOIN provider_profiles AS p ON p.id = e.profile_id
            """
        ).fetchall()

    for row in rows:
        if target in {
            _normalize_identifier(str(row["name"])),
            _normalize_identifier(str(row["endpoint_class"])),
        }:
            return {
                "id": int(row["id"]),
                "name": str(row["name"]),
                "endpoint_class": str(row["endpoint_class"]),
                "base_url": str(row["base_url"] or ""),
                "profile_id": row["profile_id"],
                "required_env_vars": str(row["required_env_vars"]),
            }
    valid = ", ".join(sorted(str(row["endpoint_class"]) for row in rows))
    raise ValueError(f"unknown endpoint '{identifier}'. valid: {valid}")


def _normalize_identifier(text: str) -> str:
    return text.strip().lower().replace(" ", "-").replace("_", "-")


def _list_local_endpoint_rows() -> list[dict[str, object]]:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT
                e.id,
                e.name,
                e.endpoint_class,
                COALESCE(e.base_url, p.base_url, '') AS base_url,
                e.profile_id,
                COALESCE(p.required_env_vars, '[]') AS required_env_vars
            FROM endpoints AS e
            LEFT JOIN provider_profiles AS p ON p.id = e.profile_id
            WHERE e.endpoint_class IN ('ollama_local', 'lmstudio_local')
            ORDER BY e.name ASC
            """
        ).fetchall()

    return [
        {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "endpoint_class": str(row["endpoint_class"]),
            "base_url": str(row["base_url"] or ""),
            "profile_id": row["profile_id"],
            "required_env_vars": str(row["required_env_vars"]),
        }
        for row in rows
    ]


def _record_endpoint_success(endpoint_id: int) -> None:
    now = utc_now()
    _update_endpoint_runtime(
        endpoint_id,
        failure_count=0,
        cooldown_until=None,
        last_rate_limited_at=None,
        last_success_at=now,
        updated_at=now,
    )


def _record_endpoint_failure(endpoint_id: int) -> None:
    now = utc_now()
    row = _ensure_runtime_row(endpoint_id)
    _update_endpoint_runtime(
        endpoint_id,
        failure_count=int(row["failure_count"] or 0) + 1,
        updated_at=now,
    )


def _record_endpoint_rate_limit(endpoint_id: int, *, retry_after_seconds: int | None = None) -> None:
    now = utc_now()
    cooldown_seconds = max(30, int(retry_after_seconds or 0) or 300)
    cooldown_until = _shift_timestamp(now, seconds=cooldown_seconds)
    row = _ensure_runtime_row(endpoint_id)
    _update_endpoint_runtime(
        endpoint_id,
        failure_count=int(row["failure_count"] or 0) + 1,
        cooldown_until=cooldown_until,
        last_rate_limited_at=now,
        updated_at=now,
    )


def _update_endpoint_runtime(
    endpoint_id: int,
    *,
    failure_count: int | None = None,
    cooldown_until: str | None = None,
    last_rate_limited_at: str | None = None,
    window_used: int | None = None,
    window_reset_at: str | None = None,
    last_success_at: str | None = None,
    updated_at: str | None = None,
) -> None:
    now = updated_at or utc_now()
    row = _ensure_runtime_row(endpoint_id)
    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            UPDATE endpoint_runtime
            SET failure_count = ?,
                cooldown_until = ?,
                last_rate_limited_at = ?,
                window_used = ?,
                window_reset_at = ?,
                last_success_at = ?,
                updated_at = ?
            WHERE endpoint_id = ?
            """,
            (
                int(failure_count if failure_count is not None else row["failure_count"]),
                cooldown_until if cooldown_until is not None else row["cooldown_until"],
                last_rate_limited_at if last_rate_limited_at is not None else row["last_rate_limited_at"],
                int(window_used if window_used is not None else row["window_used"]),
                window_reset_at if window_reset_at is not None else row["window_reset_at"],
                last_success_at if last_success_at is not None else row["last_success_at"],
                now,
                endpoint_id,
            ),
        )
        conn.commit()


def _ensure_runtime_row(endpoint_id: int) -> dict[str, object]:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT endpoint_id, failure_count, cooldown_until, last_rate_limited_at, window_used,
                   window_reset_at, last_success_at, updated_at
            FROM endpoint_runtime
            WHERE endpoint_id = ?
            """,
            (endpoint_id,),
        ).fetchone()
        if row is None:
            now = utc_now()
            conn.execute(
                """
                INSERT INTO endpoint_runtime (
                    endpoint_id, failure_count, cooldown_until, last_rate_limited_at, window_used,
                    window_reset_at, last_success_at, updated_at
                ) VALUES (?, 0, NULL, NULL, 0, NULL, NULL, ?)
                """,
                (endpoint_id, now),
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT endpoint_id, failure_count, cooldown_until, last_rate_limited_at, window_used,
                       window_reset_at, last_success_at, updated_at
                FROM endpoint_runtime
                WHERE endpoint_id = ?
                """,
                (endpoint_id,),
            ).fetchone()
    if row is None:
        raise RuntimeError(f"endpoint_runtime row missing for endpoint {endpoint_id}")
    return {
        "endpoint_id": int(row["endpoint_id"]),
        "failure_count": int(row["failure_count"] or 0),
        "cooldown_until": None if row["cooldown_until"] is None else str(row["cooldown_until"]),
        "last_rate_limited_at": None if row["last_rate_limited_at"] is None else str(row["last_rate_limited_at"]),
        "window_used": int(row["window_used"] or 0),
        "window_reset_at": None if row["window_reset_at"] is None else str(row["window_reset_at"]),
        "last_success_at": None if row["last_success_at"] is None else str(row["last_success_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _retry_after_seconds(response: httpx.Response | None) -> int | None:
    if response is None:
        return None
    header = response.headers.get("retry-after") or response.headers.get("Retry-After")
    if header is None:
        return None
    text = str(header).strip()
    if not text:
        return None
    try:
        return max(0, int(float(text)))
    except ValueError:
        return None


def _shift_timestamp(value: str, *, seconds: int = 0, hours: int = 0) -> str:
    instant = datetime.fromisoformat(value)
    shifted = instant + timedelta(seconds=seconds, hours=hours)
    if shifted.tzinfo is None:
        shifted = shifted.replace(tzinfo=timezone.utc)
    return shifted.replace(microsecond=0).isoformat()
