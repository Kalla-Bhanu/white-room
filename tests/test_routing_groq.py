from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from core.db import connect, init_db
from core import health, models_catalog
from core.router import dry_run_route


class _FakeResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 200, headers: dict[str, str] | None = None) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class _FakeGroqClient:
    def __init__(self, *args, **kwargs) -> None:
        return None

    def __enter__(self) -> "_FakeGroqClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # pragma: no cover - context protocol
        return None

    def get(self, path):  # noqa: ANN001
        if path == "/models":
            return _FakeResponse(
                {
                    "data": [
                        {"id": "llama-3.1-70b-versatile", "supports_tools": True},
                        {"id": "llama-3.1-8b-instant", "supports_streaming": True},
                    ]
                }
            )
        return _FakeResponse({})


def test_groq_health_and_sync_update_runtime_and_models(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "whiteroom.db"

    def _connect():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(health, "connect", _connect)
    monkeypatch.setattr(models_catalog, "connect", _connect)
    monkeypatch.setattr("core.http_client.httpx.Client", _FakeGroqClient)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test-123456")

    conn = _connect()
    try:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO endpoints (
                name, endpoint_class, profile_id, base_url, capabilities, tier, daily_limit, window_limit,
                status, model_name, supports_streaming, supports_tools, supports_json,
                input_cost_per_1m, output_cost_per_1m, rate_limit_notes, disabled_reason,
                created_at, updated_at
            ) VALUES (
                'groq-live', 'groq_cloud',
                (SELECT id FROM provider_profiles WHERE endpoint_class = 'groq_cloud' LIMIT 1),
                'https://api.groq.com/openai/v1', 'draft', 'cloud', '100', '10',
                'active', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
            )
            """
        )
        conn.commit()

        result = health.health_check("groq-live")
        assert result.endpoint_class == "groq_cloud"
        assert result.reachable is True
        assert result.key_present is True

        runtime_row = conn.execute(
            """
            SELECT failure_count, cooldown_until, last_rate_limited_at, last_success_at
            FROM endpoint_runtime
            WHERE endpoint_id = (SELECT id FROM endpoints WHERE name = ?)
            """,
            ("groq-live",),
        ).fetchone()
        assert runtime_row is not None
        assert int(runtime_row["failure_count"]) == 0
        assert runtime_row["last_success_at"]

        sync_result = health.sync_models("groq-live")
        assert sync_result.endpoint_class == "groq_cloud"
        assert sync_result.models_synced == 2

        models = conn.execute(
            """
            SELECT model_name, capability_source, active
            FROM provider_models
            WHERE endpoint_id = (SELECT id FROM endpoints WHERE name = ?)
            ORDER BY model_name ASC
            """,
            ("groq-live",),
        ).fetchall()
        assert [(row["model_name"], row["capability_source"], row["active"]) for row in models] == [
            ("llama-3.1-70b-versatile", "groq_discovered", 1),
            ("llama-3.1-8b-instant", "groq_discovered", 1),
        ]
    finally:
        conn.close()


def test_groq_lane_override_respects_cooldown(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test-123456")

    endpoint_id = _ensure_groq_endpoint()
    original = _runtime_snapshot(endpoint_id)
    cooldown_until = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(microsecond=0).isoformat()
    try:
        _set_endpoint_runtime(endpoint_id=endpoint_id, cooldown_until=cooldown_until)

        result = dry_run_route("white-room", 1, mode="plan", lane_override="groq_cloud")

        assert result.endpoint_class == "manual_claude"
        assert "cooling down" in result.rationale

        with connect() as conn:
            init_db(conn)
            row = conn.execute(
                """
                SELECT chosen_lane, candidates, explanation
                FROM route_decisions
                WHERE project_id = (SELECT id FROM projects WHERE slug = ?)
                  AND task_id = ?
                  AND mode = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                ("white-room", 1, "plan"),
            ).fetchone()
        assert row is not None
        assert row["chosen_lane"] == "manual_claude"
        candidates = json.loads(str(row["candidates"]))
        assert any(candidate.get("endpoint_class") == "groq_cloud" for candidate in candidates)
        assert "cooling down" in str(row["explanation"])
    finally:
        _restore_endpoint_runtime(endpoint_id, original)

    result = dry_run_route("white-room", 1, mode="plan", lane_override="groq_cloud")
    assert result.endpoint_class == "groq_cloud"


def _ensure_groq_endpoint() -> int:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            "SELECT id FROM endpoints WHERE endpoint_class = 'groq_cloud' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if row is not None:
            return int(row["id"])
        profile = conn.execute(
            "SELECT id FROM provider_profiles WHERE endpoint_class = 'groq_cloud' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        assert profile is not None
        cursor = conn.execute(
            """
            INSERT INTO endpoints (
                name, endpoint_class, profile_id, base_url, capabilities, tier, daily_limit, window_limit,
                status, model_name, supports_streaming, supports_tools, supports_json,
                input_cost_per_1m, output_cost_per_1m, rate_limit_notes, disabled_reason,
                created_at, updated_at
            ) VALUES (
                'groq-cloud-live', 'groq_cloud', ?,
                'https://api.groq.com/openai/v1', 'draft,summarization', 'cloud', '100', '10',
                'active', 'groq/llama-3.1-70b', 0, 0, 0, NULL, NULL, 'approval-gated latency lane', NULL,
                ?, ?
            )
            """,
            (
                int(profile["id"]),
                datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def _runtime_snapshot(endpoint_id: int) -> sqlite3.Row | None:
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
    return row


def _set_endpoint_runtime(*, endpoint_id: int, cooldown_until: str | None) -> None:
    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            UPDATE endpoint_runtime
            SET cooldown_until = ?, updated_at = ?
            WHERE endpoint_id = ?
            """,
            (cooldown_until, datetime.now(timezone.utc).replace(microsecond=0).isoformat(), endpoint_id),
        )
        conn.commit()


def _restore_endpoint_runtime(endpoint_id: int, original: sqlite3.Row | None) -> None:
    if original is None:
        return
    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            UPDATE endpoint_runtime
            SET failure_count = ?, cooldown_until = ?, last_rate_limited_at = ?, window_used = ?,
                window_reset_at = ?, last_success_at = ?, updated_at = ?
            WHERE endpoint_id = ?
            """,
            (
                int(original["failure_count"] or 0),
                original["cooldown_until"],
                original["last_rate_limited_at"],
                int(original["window_used"] or 0),
                original["window_reset_at"],
                original["last_success_at"],
                original["updated_at"],
                endpoint_id,
            ),
        )
        conn.commit()
