from __future__ import annotations

from typing import Any

from core.db import connect, init_db
from core.http_client import request_json
from core.memory import utc_now


def discover_models(base_url: str, api_key: str) -> list[dict[str, Any]]:
    normalized_base = base_url.rstrip("/")
    request_path = "/models" if normalized_base.endswith("/v1") else "/v1/models"
    payload = request_json(
        normalized_base,
        request_path,
        method="GET",
        headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
        retries=1,
    )
    return normalize_model_rows(payload)


def normalize_model_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_models = payload.get("data")
    if raw_models is None:
        raw_models = payload.get("models")
    if raw_models is None and isinstance(payload, list):
        raw_models = payload
    if not isinstance(raw_models, list):
        return []

    discovered: list[dict[str, Any]] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        model_name = _first_text(item, ("id", "model_name", "name"))
        if not model_name:
            continue
        discovered.append(
            {
                "model_name": model_name,
                "context_window": _first_int(item, ("context_window", "context_length", "max_context_tokens")),
                "supports_streaming": _first_bool(item, ("supports_streaming", "streaming")),
                "supports_tools": _first_bool(item, ("supports_tools", "tools")),
                "supports_json": _first_bool(item, ("supports_json", "json_mode")),
                "capability_source": "discovered",
                "active": 1,
            }
        )
    return discovered


def normalize_groq_model_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    discovered = normalize_model_rows(payload)
    for item in discovered:
        if str(item.get("capability_source") or "").strip() == "discovered":
            item["capability_source"] = "groq_discovered"
    return discovered


def sync_endpoint_models(endpoint_id: int, discovered_models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_models = _normalize_discovered_models(discovered_models)
    now = utc_now()
    with connect() as conn:
        init_db(conn)
        existing_rows = conn.execute(
            "SELECT model_name FROM provider_models WHERE endpoint_id = ?",
            (endpoint_id,),
        ).fetchall()
        existing_names = {str(row["model_name"]) for row in existing_rows}
        discovered_names = {str(row["model_name"]) for row in normalized_models}

        for model in normalized_models:
            conn.execute(
                """
                INSERT INTO provider_models (
                    endpoint_id, model_name, context_window, supports_streaming, supports_tools,
                    supports_json, capability_source, active, discovered_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(endpoint_id, model_name) DO UPDATE SET
                    context_window = excluded.context_window,
                    supports_streaming = excluded.supports_streaming,
                    supports_tools = excluded.supports_tools,
                    supports_json = excluded.supports_json,
                    capability_source = excluded.capability_source,
                    active = excluded.active,
                    discovered_at = excluded.discovered_at,
                    updated_at = excluded.updated_at
                """,
                (
                    endpoint_id,
                    model["model_name"],
                    model.get("context_window"),
                    model.get("supports_streaming"),
                    model.get("supports_tools"),
                    model.get("supports_json"),
                    model.get("capability_source") or "discovered",
                    int(bool(model.get("active", 1))),
                    now,
                    now,
                ),
            )

        missing_names = existing_names - discovered_names
        if missing_names:
            placeholders = ", ".join("?" for _ in missing_names)
            conn.execute(
                f"""
                UPDATE provider_models
                SET active = 0,
                    updated_at = ?
                WHERE endpoint_id = ?
                  AND model_name IN ({placeholders})
                """,
                (now, endpoint_id, *sorted(missing_names)),
            )

        conn.execute(
            "UPDATE endpoints SET last_model_sync = ? WHERE id = ?",
            (now, endpoint_id),
        )
        conn.commit()

    return normalized_models


def list_endpoint_models(endpoint_id: int, active_only: bool = True) -> list[dict[str, Any]]:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            f"""
            SELECT
                endpoint_id,
                model_name,
                context_window,
                supports_streaming,
                supports_tools,
                supports_json,
                capability_source,
                active,
                discovered_at,
                updated_at
            FROM provider_models
            WHERE endpoint_id = ?{" AND active = 1" if active_only else ""}
            ORDER BY active DESC, model_name ASC
            """,
            (endpoint_id,),
        ).fetchall()

    return [
        {
            "endpoint_id": int(row["endpoint_id"]),
            "model_name": str(row["model_name"]),
            "context_window": None if row["context_window"] is None else int(row["context_window"]),
            "supports_streaming": _row_bool(row["supports_streaming"]),
            "supports_tools": _row_bool(row["supports_tools"]),
            "supports_json": _row_bool(row["supports_json"]),
            "capability_source": str(row["capability_source"]),
            "active": _row_bool(row["active"]),
            "discovered_at": str(row["discovered_at"]),
            "updated_at": str(row["updated_at"]),
        }
        for row in rows
    ]


def _normalize_discovered_models(discovered_models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in discovered_models:
        if not isinstance(item, dict):
            continue
        model_name = str(item.get("model_name") or item.get("id") or item.get("name") or "").strip()
        if not model_name:
            continue
        normalized.append(
            {
                "model_name": model_name,
                "context_window": _coerce_int(item.get("context_window") or item.get("context_length") or item.get("max_context_tokens")),
                "supports_streaming": _coerce_bool(item.get("supports_streaming") if "supports_streaming" in item else item.get("streaming")),
                "supports_tools": _coerce_bool(item.get("supports_tools") if "supports_tools" in item else item.get("tools")),
                "supports_json": _coerce_bool(item.get("supports_json") if "supports_json" in item else item.get("json_mode")),
                "capability_source": str(item.get("capability_source") or "discovered"),
                "active": 1 if item.get("active", True) else 0,
            }
        )
    return normalized


def _first_text(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _first_int(item: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = item.get(key)
        coerced = _coerce_int(value)
        if coerced is not None:
            return coerced
    return None


def _first_bool(item: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        if key not in item:
            continue
        coerced = _coerce_bool(item.get(key))
        if coerced is not None:
            return coerced
    return None


def _coerce_int(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(bool(value))
    text = str(value).strip().lower()
    if not text:
        return None
    return int(text in {"1", "true", "yes", "y", "on"})


def _row_bool(value: object | None) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}
