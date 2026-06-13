from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from core.db import connect, init_db


VALID_ENDPOINT_CLASSES = {
    "manual_claude",
    "codex_lb",
    "groq_cloud",
    "ollama_local",
    "lmstudio_local",
    "openai_compatible_cloud",
    "anthropic_compatible_cloud",
    "provider_specific_cloud",
}


@dataclass(frozen=True)
class EndpointRecord:
    id: int
    name: str
    endpoint_class: str
    profile_id: int | None
    profile_name: str
    base_url: str
    capabilities: str
    tier: str
    daily_limit: str
    window_limit: str
    status: str
    model_name: str
    supports_streaming: bool
    supports_tools: bool
    supports_json: bool
    input_cost_per_1m: float | None
    output_cost_per_1m: float | None
    rate_limit_notes: str
    disabled_reason: str


def add_endpoint(
    name: str,
    endpoint_class: str,
    tier: str,
    base_url: str,
    capabilities: str,
    daily_limit: str,
    window_limit: str,
    status: str,
    profile_id: int | None = None,
    model_name: str | None = None,
    supports_streaming: bool | None = None,
    supports_tools: bool | None = None,
    supports_json: bool | None = None,
    input_cost_per_1m: float | None = None,
    output_cost_per_1m: float | None = None,
    rate_limit_notes: str | None = None,
    disabled_reason: str | None = None,
) -> None:
    if endpoint_class not in VALID_ENDPOINT_CLASSES:
        valid = ", ".join(sorted(VALID_ENDPOINT_CLASSES))
        raise ValueError(f"invalid endpoint class '{endpoint_class}'. valid: {valid}")

    with connect() as conn:
        init_db(conn)
        profile = _resolve_provider_profile(conn, endpoint_class=endpoint_class, profile_id=profile_id)
        merged_profile_id = profile["id"] if profile else profile_id
        merged_model_name = _coalesce_value(model_name, profile["model_name"] if profile else None)
        merged_supports_streaming = _coerce_bool(
            supports_streaming,
            int(profile["supports_streaming"]) if profile else None,
        )
        merged_supports_tools = _coerce_bool(
            supports_tools,
            int(profile["supports_tools"]) if profile else None,
        )
        merged_supports_json = _coerce_bool(
            supports_json,
            int(profile["supports_json"]) if profile else None,
        )
        merged_input_cost_per_1m = _coalesce_value(input_cost_per_1m, profile["input_cost_per_1m"] if profile else None)
        merged_output_cost_per_1m = _coalesce_value(
            output_cost_per_1m,
            profile["output_cost_per_1m"] if profile else None,
        )
        merged_rate_limit_notes = _coalesce_value(rate_limit_notes, profile["rate_limit_notes"] if profile else None)
        merged_disabled_reason = _coalesce_value(disabled_reason, profile["disabled_reason"] if profile else None)
        conn.execute(
            """
            INSERT INTO endpoints
                (name, endpoint_class, profile_id, base_url, capabilities, tier, daily_limit, window_limit,
                 status, model_name, supports_streaming, supports_tools, supports_json,
                 input_cost_per_1m, output_cost_per_1m, rate_limit_notes, disabled_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                endpoint_class,
                merged_profile_id,
                base_url,
                capabilities,
                tier,
                daily_limit,
                window_limit,
                status,
                merged_model_name,
                int(merged_supports_streaming) if merged_supports_streaming is not None else None,
                int(merged_supports_tools) if merged_supports_tools is not None else None,
                int(merged_supports_json) if merged_supports_json is not None else None,
                merged_input_cost_per_1m,
                merged_output_cost_per_1m,
                merged_rate_limit_notes,
                merged_disabled_reason,
            ),
        )
        conn.commit()


def update_endpoint(
    name: str,
    *,
    endpoint_class: str | None = None,
    tier: str | None = None,
    base_url: str | None = None,
    capabilities: str | None = None,
    daily_limit: str | None = None,
    window_limit: str | None = None,
    status: str | None = None,
    profile_id: int | None = None,
    model_name: str | None = None,
    supports_streaming: bool | None = None,
    supports_tools: bool | None = None,
    supports_json: bool | None = None,
    input_cost_per_1m: float | None = None,
    output_cost_per_1m: float | None = None,
    rate_limit_notes: str | None = None,
    disabled_reason: str | None = None,
) -> None:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT id, name, endpoint_class, profile_id, base_url, capabilities, tier, daily_limit, window_limit,
                   status, model_name, supports_streaming, supports_tools, supports_json,
                   input_cost_per_1m, output_cost_per_1m, rate_limit_notes, disabled_reason
            FROM endpoints
            WHERE name = ?
            """,
            (name,),
        ).fetchone()
        if row is None:
            raise ValueError(f"endpoint '{name}' not found")
        current_profile = _resolve_provider_profile(
            conn,
            endpoint_class=endpoint_class or str(row["endpoint_class"]),
            profile_id=profile_id if profile_id is not None else (None if row["profile_id"] is None else int(row["profile_id"])),
        )
        merged_endpoint_class = endpoint_class or str(row["endpoint_class"])
        merged_profile_id = current_profile["id"] if current_profile else row["profile_id"]
        merged_base_url = base_url if base_url is not None else str(row["base_url"])
        merged_capabilities = capabilities if capabilities is not None else str(row["capabilities"])
        merged_tier = tier if tier is not None else str(row["tier"])
        merged_daily_limit = daily_limit if daily_limit is not None else str(row["daily_limit"])
        merged_window_limit = window_limit if window_limit is not None else str(row["window_limit"])
        merged_status = status if status is not None else str(row["status"])
        merged_model_name = _coalesce_value(model_name, current_profile["model_name"] if current_profile else row["model_name"])
        merged_supports_streaming = _coerce_bool(
            supports_streaming,
            _row_bool(row["supports_streaming"]),
        )
        merged_supports_tools = _coerce_bool(
            supports_tools,
            _row_bool(row["supports_tools"]),
        )
        merged_supports_json = _coerce_bool(
            supports_json,
            _row_bool(row["supports_json"]),
        )
        merged_input_cost_per_1m = _coalesce_value(
            input_cost_per_1m,
            row["input_cost_per_1m"] if row["input_cost_per_1m"] is not None else (current_profile["input_cost_per_1m"] if current_profile else None),
        )
        merged_output_cost_per_1m = _coalesce_value(
            output_cost_per_1m,
            row["output_cost_per_1m"] if row["output_cost_per_1m"] is not None else (current_profile["output_cost_per_1m"] if current_profile else None),
        )
        merged_rate_limit_notes = _coalesce_value(
            rate_limit_notes,
            row["rate_limit_notes"] if row["rate_limit_notes"] is not None else (current_profile["rate_limit_notes"] if current_profile else None),
        )
        merged_disabled_reason = _coalesce_value(
            disabled_reason,
            row["disabled_reason"] if row["disabled_reason"] is not None else (current_profile["disabled_reason"] if current_profile else None),
        )
        conn.execute(
            """
            UPDATE endpoints
            SET endpoint_class = ?,
                profile_id = ?,
                base_url = ?,
                capabilities = ?,
                tier = ?,
                daily_limit = ?,
                window_limit = ?,
                status = ?,
                model_name = ?,
                supports_streaming = ?,
                supports_tools = ?,
                supports_json = ?,
                input_cost_per_1m = ?,
                output_cost_per_1m = ?,
                rate_limit_notes = ?,
                disabled_reason = ?
            WHERE name = ?
            """,
            (
                merged_endpoint_class,
                merged_profile_id,
                merged_base_url,
                merged_capabilities,
                merged_tier,
                merged_daily_limit,
                merged_window_limit,
                merged_status,
                merged_model_name,
                int(merged_supports_streaming) if merged_supports_streaming is not None else None,
                int(merged_supports_tools) if merged_supports_tools is not None else None,
                int(merged_supports_json) if merged_supports_json is not None else None,
                merged_input_cost_per_1m,
                merged_output_cost_per_1m,
                merged_rate_limit_notes,
                merged_disabled_reason,
                name,
            ),
        )
        conn.commit()


def list_endpoints() -> list[EndpointRecord]:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT
                e.id,
                e.name,
                e.endpoint_class,
                e.profile_id,
                COALESCE(p.name, 'unlinked') AS profile_name,
                e.base_url,
                e.capabilities,
                e.tier,
                e.daily_limit,
                e.window_limit,
                e.status,
                e.model_name,
                e.supports_streaming,
                e.supports_tools,
                e.supports_json,
                e.input_cost_per_1m,
                e.output_cost_per_1m,
                e.rate_limit_notes,
                e.disabled_reason
            FROM endpoints AS e
            LEFT JOIN provider_profiles AS p ON p.id = e.profile_id
            ORDER BY e.name ASC
            """
        ).fetchall()

    return [
        EndpointRecord(
            id=int(row["id"]),
            name=str(row["name"]),
            endpoint_class=str(row["endpoint_class"]),
            profile_id=None if row["profile_id"] is None else int(row["profile_id"]),
            profile_name=str(row["profile_name"]),
            base_url=str(row["base_url"]),
            capabilities=str(row["capabilities"]),
            tier=str(row["tier"]),
            daily_limit=str(row["daily_limit"]),
            window_limit=str(row["window_limit"]),
            status=str(row["status"]),
            model_name=str(row["model_name"] or ""),
            supports_streaming=_row_bool(row["supports_streaming"]),
            supports_tools=_row_bool(row["supports_tools"]),
            supports_json=_row_bool(row["supports_json"]),
            input_cost_per_1m=row["input_cost_per_1m"],
            output_cost_per_1m=row["output_cost_per_1m"],
            rate_limit_notes=str(row["rate_limit_notes"] or ""),
            disabled_reason=str(row["disabled_reason"] or ""),
        )
        for row in rows
    ]


def _resolve_provider_profile(
    connection: sqlite3.Connection,
    *,
    endpoint_class: str,
    profile_id: int | None = None,
) -> sqlite3.Row | None:
    if profile_id is not None:
        row = connection.execute(
            "SELECT * FROM provider_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if row is not None:
            return row
    return connection.execute(
        "SELECT * FROM provider_profiles WHERE endpoint_class = ? ORDER BY id ASC LIMIT 1",
        (endpoint_class,),
    ).fetchone()


def _coalesce_value(value: object | None, fallback: object | None) -> object | None:
    if value is None:
        return fallback
    if isinstance(value, str) and not value.strip():
        return fallback
    return value


def _coerce_bool(value: bool | int | None, fallback: int | bool | None) -> bool | None:
    if value is None:
        if fallback is None:
            return None
        return bool(fallback)
    return bool(value)


def _row_bool(value: object | None) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on"}
