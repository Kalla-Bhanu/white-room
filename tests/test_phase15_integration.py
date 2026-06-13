from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from core.db import connect, init_db
from core import health
from core.router import dry_run_route
import web.server as server


DOCS_PATH = Path("docs/PROVIDERS.md")


def test_phase15_route_continuity_and_fallback_across_mocked_lanes(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_API_KEY", "sk-phase15-codex-test")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-phase15-groq-test")

    client = TestClient(server.app)
    page = client.get("/chat/white-room")
    assert page.status_code == 200
    assert "route-summary-chip" in page.text
    assert "Provider health" in page.text

    codex_endpoint_id = _ensure_endpoint(
        endpoint_class="codex_lb",
        name="codex-phase15-test",
        base_url="https://codex.example.com/v1",
    )
    groq_endpoint_id = _ensure_endpoint(
        endpoint_class="groq_cloud",
        name="groq-phase15-test",
        base_url="https://api.groq.com/openai/v1",
    )

    codex_original = _runtime_snapshot(codex_endpoint_id)
    groq_original = _runtime_snapshot(groq_endpoint_id)
    groq_cooldown_until = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat()

    try:
        _set_endpoint_runtime(endpoint_id=groq_endpoint_id, cooldown_until=groq_cooldown_until)

        groq_fallback = dry_run_route("white-room", 1, mode="plan", lane_override="groq_cloud")
        assert groq_fallback.endpoint_class == "manual_claude"
        assert "cooling down" in groq_fallback.rationale
        _delete_route_decision(groq_fallback.route_decision_id)

        _restore_endpoint_runtime(groq_endpoint_id, groq_original)
        groq_result = dry_run_route("white-room", 1, mode="plan", lane_override="groq_cloud")
        assert groq_result.endpoint_class == "groq_cloud"
        assert "groq_cloud" in groq_result.rationale
        _delete_route_decision(groq_result.route_decision_id)

        codex_result = dry_run_route("white-room", 1, mode="execute", lane_override="codex_lb")
        assert codex_result.endpoint_class == "codex_lb"
        assert "codex_lb" in codex_result.rationale
        _delete_route_decision(codex_result.route_decision_id)
    finally:
        _restore_endpoint_runtime(codex_endpoint_id, codex_original)
        _restore_endpoint_runtime(groq_endpoint_id, groq_original)


def test_phase15_provider_docs_include_manual_live_checklist() -> None:
    assert DOCS_PATH.exists()
    docs = DOCS_PATH.read_text(encoding="utf-8")

    assert "Manual live checklist" in docs
    assert "Codex LB" in docs
    assert "Groq Cloud" in docs
    assert "approval gate" in docs
    assert "remove the key" in docs


def _ensure_endpoint(*, endpoint_class: str, name: str, base_url: str) -> int:
    with connect() as conn:
        init_db(conn)
        existing = conn.execute(
            "SELECT id FROM endpoints WHERE endpoint_class = ? ORDER BY id ASC LIMIT 1",
            (endpoint_class,),
        ).fetchone()
        if existing is not None:
            endpoint_id = int(existing["id"])
            conn.execute(
                """
                UPDATE endpoints
                SET name = ?, base_url = ?, status = 'active', updated_at = ?
                WHERE id = ?
                """,
                (name, base_url, _utc_now(), endpoint_id),
            )
            conn.commit()
            return endpoint_id

        profile = conn.execute(
            "SELECT id FROM provider_profiles WHERE endpoint_class = ? ORDER BY id ASC LIMIT 1",
            (endpoint_class,),
        ).fetchone()
        assert profile is not None
        cursor = conn.execute(
            """
            INSERT INTO endpoints (
                name, endpoint_class, profile_id, base_url, capabilities, tier, daily_limit, window_limit,
                status, model_name, supports_streaming, supports_tools, supports_json,
                input_cost_per_1m, output_cost_per_1m, rate_limit_notes, disabled_reason,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'draft,execution', 'cloud', '100', '10', 'active', NULL, 0, 0, 0, NULL, NULL, NULL, NULL, ?, ?)
            """,
            (name, endpoint_class, int(profile["id"]), base_url, _utc_now(), _utc_now()),
        )
        conn.commit()
        return int(cursor.lastrowid)


def _runtime_snapshot(endpoint_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        init_db(conn)
        health._ensure_runtime_row(endpoint_id)
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT endpoint_id, failure_count, cooldown_until, last_rate_limited_at, window_used,
                   window_reset_at, last_success_at, updated_at
            FROM endpoint_runtime
            WHERE endpoint_id = ?
            """,
            (endpoint_id,),
        ).fetchone()


def _set_endpoint_runtime(*, endpoint_id: int, cooldown_until: str | None) -> None:
    with connect() as conn:
        init_db(conn)
        health._ensure_runtime_row(endpoint_id)
        conn.execute(
            """
            UPDATE endpoint_runtime
            SET cooldown_until = ?, updated_at = ?
            WHERE endpoint_id = ?
            """,
            (cooldown_until, _utc_now(), endpoint_id),
        )
        conn.commit()


def _restore_endpoint_runtime(endpoint_id: int, original: sqlite3.Row | None) -> None:
    with connect() as conn:
        init_db(conn)
        health._ensure_runtime_row(endpoint_id)
        if original is None:
            conn.execute(
                """
                UPDATE endpoint_runtime
                SET cooldown_until = NULL, updated_at = ?
                WHERE endpoint_id = ?
                """,
                (_utc_now(), endpoint_id),
            )
        else:
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


def _delete_route_decision(route_decision_id: int) -> None:
    with connect() as conn:
        init_db(conn)
        conn.execute("DELETE FROM route_decisions WHERE id = ?", (route_decision_id,))
        conn.commit()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
