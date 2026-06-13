from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from core.approvals import create_approval_grant, gate_allows_action, revoke_approval_grant
from core.db import connect, init_db
from core.router import dry_run_route, run_route


def _future_iso(*, hours: int = 1) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(microsecond=0).isoformat()


def test_execute_route_defaults_to_codex_lb_and_marks_approval_required() -> None:
    result = dry_run_route("white-room", 1, mode="execute")

    assert result.endpoint_class == "codex_lb"
    assert "Execute prefers Codex LB" in result.rationale

    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT chosen_lane, requires_approval, candidates
            FROM route_decisions
            WHERE project_id = (SELECT id FROM projects WHERE slug = ?)
              AND task_id = ?
              AND mode = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            ("white-room", 1, "execute"),
        ).fetchone()

    assert row is not None
    assert row["chosen_lane"] == "codex_lb"
    assert int(row["requires_approval"]) == 1
    candidates = json.loads(str(row["candidates"]))
    assert candidates[0]["endpoint_class"] == "codex_lb"


def test_active_codex_grant_skips_per_call_gate() -> None:
    grant = create_approval_grant(
        project_slug="white-room",
        endpoint_class="codex_lb",
        modes=["execute"],
        turns_remaining=2,
        expires_at=_future_iso(hours=1),
    )
    before = _approval_gate_count()

    try:
        result = run_route("white-room", 1, mode="execute")
    finally:
        revoke_approval_grant(grant.id)

    after = _approval_gate_count()
    assert result.endpoint_class == "codex_lb"
    assert result.approval_gate_id is None
    assert result.approval_status == "granted"
    assert after == before


def test_out_of_scope_grant_falls_back_to_per_call_gate() -> None:
    grant = create_approval_grant(
        project_slug="white-room",
        endpoint_class="codex_lb",
        modes=["review"],
        turns_remaining=1,
        expires_at=_future_iso(hours=1),
    )
    try:
        count_before = _approval_gate_count()
        allowed, created_gate, message = gate_allows_action(
            project_slug="white-room",
            action_type="route_run",
            target_endpoint_id=None,
            payload_summary=f'{{"mode": "execute", "nonce": "{uuid4().hex}"}}',
            endpoint_class="codex_lb",
            mode="execute",
            risk="medium",
            est_cost_usd=0.0,
        )
        assert not allowed
        assert created_gate.id > 0
        assert message == "needs approval"
        assert _approval_gate_count() == count_before + 1
    finally:
        revoke_approval_grant(grant.id)


def test_high_risk_route_ignores_matching_grant() -> None:
    grant = create_approval_grant(
        project_slug="white-room",
        endpoint_class="codex_lb",
        modes=["execute"],
        turns_remaining=1,
        expires_at=_future_iso(hours=1),
    )
    try:
        count_before = _approval_gate_count()
        allowed, created_gate, message = gate_allows_action(
            project_slug="white-room",
            action_type="route_run",
            target_endpoint_id=None,
            payload_summary=f'{{"mode": "execute", "nonce": "{uuid4().hex}"}}',
            endpoint_class="codex_lb",
            mode="execute",
            risk="high",
            est_cost_usd=0.0,
        )
        assert not allowed
        assert created_gate.id > 0
        assert message == "needs approval"
        assert _approval_gate_count() == count_before + 1
    finally:
        revoke_approval_grant(grant.id)


def test_execute_route_falls_back_when_codex_lb_is_cooling_down() -> None:
    endpoint_id = _codex_endpoint_id()
    original = _endpoint_runtime_snapshot(endpoint_id)
    cooldown_until = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(microsecond=0).isoformat()
    try:
        _set_endpoint_runtime(endpoint_id=endpoint_id, cooldown_until=cooldown_until)

        result = dry_run_route("white-room", 1, mode="execute")

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
                ("white-room", 1, "execute"),
            ).fetchone()
        assert row is not None
        assert row["chosen_lane"] == "manual_claude"
        candidates = json.loads(str(row["candidates"]))
        assert candidates[0]["endpoint_class"] == "manual_claude"
        assert "cooling down" in str(row["explanation"])
        assert any("cooling down" in str(candidate.get("reject_reason", "")) for candidate in candidates)
    finally:
        _restore_endpoint_runtime(endpoint_id, original)


def _approval_gate_count() -> int:
    with connect() as conn:
        init_db(conn)
        row = conn.execute("SELECT COUNT(*) AS count FROM approval_gates").fetchone()
    assert row is not None
    return int(row["count"])


def _codex_endpoint_id() -> int:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            "SELECT id FROM endpoints WHERE endpoint_class = 'codex_lb' ORDER BY id ASC LIMIT 1"
        ).fetchone()
    assert row is not None
    return int(row["id"])


def _endpoint_runtime_snapshot(endpoint_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        init_db(conn)
        conn.row_factory = sqlite3.Row
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
