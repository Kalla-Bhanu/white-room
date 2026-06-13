from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from core.db import connect, init_db
from core.memory import get_project, utc_now


LOCAL_ENDPOINT_CLASSES = {"ollama_local", "lmstudio_local"}


@dataclass(frozen=True)
class UsageRecord:
    endpoint_name: str
    project_slug: str
    task_id: int | None
    tokens_in: int
    tokens_out: int
    est_cost: float
    occurred_at: str


@dataclass(frozen=True)
class UsageReportRow:
    endpoint_name: str
    project_slug: str
    events: int
    tokens_in: int
    tokens_out: int
    est_cost: float
    window_used: int
    window_limit: int | None
    at_limit: bool


def usage_summary(project_slug: str | None = None) -> dict[str, object]:
    rows = usage_report(project_slug=project_slug)
    row_dicts: list[dict[str, object]] = []
    total_events = 0
    total_tokens_in = 0
    total_tokens_out = 0
    total_est_cost = 0.0
    max_token_total = 0
    max_est_cost = 0.0
    total_window_used = 0
    total_window_limit = 0

    for row in rows:
        token_total = int(row.tokens_in) + int(row.tokens_out)
        total_events += int(row.events)
        total_tokens_in += int(row.tokens_in)
        total_tokens_out += int(row.tokens_out)
        total_est_cost += float(row.est_cost)
        max_token_total = max(max_token_total, token_total)
        max_est_cost = max(max_est_cost, float(row.est_cost))
        total_window_used += int(row.window_used)
        if row.window_limit is not None:
            total_window_limit += int(row.window_limit)
        row_dicts.append(
            {
                "endpoint_name": row.endpoint_name,
                "project_slug": row.project_slug,
                "events": row.events,
                "tokens_in": row.tokens_in,
                "tokens_out": row.tokens_out,
                "token_total": token_total,
                "est_cost": row.est_cost,
                "window_used": row.window_used,
                "window_limit": row.window_limit,
                "at_limit": row.at_limit,
            }
        )

    for row in row_dicts:
        row["token_bar_percent"] = 0 if max_token_total == 0 else round((row["token_total"] / max_token_total) * 100, 1)
        row["cost_bar_percent"] = 0 if max_est_cost == 0 else round((float(row["est_cost"]) / max_est_cost) * 100, 1)
        row["cost_label"] = f"est ${float(row['est_cost']):.4f}"

    total_est_cost_label = f"est ${total_est_cost:.4f}"
    total_window_label = (
        f"{total_window_used}/{total_window_limit} window"
        if total_window_limit
        else f"{total_window_used} est"
    )

    gauges = []
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT id, name, endpoint_class, tier, window_used, window_limit, window_reset_at, status
            FROM endpoints
            ORDER BY name ASC
            """
        ).fetchall()

    for row in rows:
        used = int(row["window_used"] or 0)
        limit = _parse_limit(row["window_limit"])
        if limit is None or limit <= 0:
            percent = 0
            state = "unbounded"
        else:
            percent = min(100, round((used / limit) * 100))
            if used >= limit:
                state = "bad"
            elif percent >= 85:
                state = "bad"
            elif percent >= 50:
                state = "warn"
            else:
                state = "ok"
        remaining = None if limit is None else max(limit - used, 0)
        gauges.append(
            {
                "id": int(row["id"]),
                "name": str(row["name"]),
                "endpoint_class": str(row["endpoint_class"]),
                "tier": str(row["tier"]),
                "status": str(row["status"]),
                "window_used": used,
                "window_limit": limit,
                "window_reset_at": "" if row["window_reset_at"] is None else str(row["window_reset_at"]),
                "window_remaining": remaining,
                "window_percent": percent,
                "window_state": state,
                "window_label": f"{used}/{limit}" if limit is not None else f"{used} est",
            }
        )

    return {
        "rows": row_dicts,
        "gauges": gauges,
        "total_events": total_events,
        "total_events_label": f"{total_events} events",
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "total_est_cost": total_est_cost,
        "total_est_cost_label": total_est_cost_label,
        "total_window_used": total_window_used,
        "total_window_limit": total_window_limit,
        "total_window_label": total_window_label,
    }


def record_usage(
    endpoint_name: str,
    endpoint_class: str,
    base_url: str,
    project_slug: str,
    tokens_in: int,
    tokens_out: int,
    est_cost: float,
    task_id: int | None = None,
    occurred_at: str | None = None,
) -> UsageRecord:
    project = get_project(project_slug)
    happened_at = occurred_at or utc_now()

    with connect() as conn:
        init_db(conn)
        endpoint_id = _ensure_endpoint(conn, endpoint_name, endpoint_class, base_url)
        window_used = tokens_in + tokens_out
        _update_endpoint_window(conn, endpoint_id, window_used, happened_at)
        conn.execute(
            """
            INSERT INTO usage_events
                (endpoint_id, project_id, task_id, tokens_in, tokens_out, est_cost, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                endpoint_id,
                project.id,
                task_id,
                tokens_in,
                tokens_out,
                est_cost,
                happened_at,
            ),
        )
        conn.commit()

    return UsageRecord(
        endpoint_name=endpoint_name,
        project_slug=project.slug,
        task_id=task_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        est_cost=est_cost,
        occurred_at=happened_at,
    )


def usage_report(
    project_slug: str | None = None,
    endpoint_name: str | None = None,
) -> list[UsageReportRow]:
    with connect() as conn:
        init_db(conn)
        clauses = []
        params: list[str] = []
        if project_slug is not None:
            clauses.append("p.slug = ?")
            params.append(project_slug)
        if endpoint_name is not None:
            clauses.append("e.name = ?")
            params.append(endpoint_name)
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"""
            SELECT
                e.name AS endpoint_name,
                p.slug AS project_slug,
                COUNT(u.id) AS events,
                COALESCE(SUM(u.tokens_in), 0) AS tokens_in,
                COALESCE(SUM(u.tokens_out), 0) AS tokens_out,
                COALESCE(SUM(u.est_cost), 0.0) AS est_cost,
                COALESCE(e.window_used, 0) AS window_used,
                e.window_limit AS window_limit,
                e.window_reset_at AS window_reset_at
            FROM usage_events AS u
            JOIN endpoints AS e ON e.id = u.endpoint_id
            JOIN projects AS p ON p.id = u.project_id
            {where_clause}
            GROUP BY e.name, p.slug, e.window_used, e.window_limit, e.window_reset_at
            ORDER BY p.slug ASC, e.name ASC
            """,
            params,
        ).fetchall()

    return [
        UsageReportRow(
            endpoint_name=str(row["endpoint_name"]),
            project_slug=str(row["project_slug"]),
            events=int(row["events"]),
            tokens_in=int(row["tokens_in"]),
            tokens_out=int(row["tokens_out"]),
            est_cost=float(row["est_cost"]),
            window_used=int(row["window_used"]),
            window_limit=_parse_limit(row["window_limit"]),
            at_limit=_endpoint_is_at_limit(row["window_used"], row["window_limit"], row["window_reset_at"]),
        )
        for row in rows
    ]


def endpoint_is_at_limit(endpoint_name: str) -> bool:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT window_used, window_limit, window_reset_at
            FROM endpoints
            WHERE name = ?
            """,
            (endpoint_name,),
        ).fetchone()
    if row is None:
        return False
    return _endpoint_is_at_limit(row["window_used"], row["window_limit"], row["window_reset_at"])


def _ensure_endpoint(conn, name: str, endpoint_class: str, base_url: str) -> int:
    row = conn.execute(
        "SELECT id, endpoint_class, window_limit FROM endpoints WHERE name = ?",
        (name,),
    ).fetchone()
    if row is not None:
        if str(row["endpoint_class"]) in LOCAL_ENDPOINT_CLASSES and _parse_limit(row["window_limit"]) is None:
            conn.execute(
                """
                UPDATE endpoints
                SET daily_limit = ?, window_limit = ?
                WHERE id = ?
                """,
                ("10000", "1000", int(row["id"])),
            )
        return int(row["id"])

    conn.execute(
        """
        INSERT INTO endpoints
            (name, endpoint_class, base_url, capabilities, tier, daily_limit, window_limit, window_used, window_reset_at, status, last_checked)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            endpoint_class,
            base_url,
            "live usage recorder",
            "local",
            "10000",
            "1000",
            0,
            None,
            "available",
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        ),
    )
    row = conn.execute("SELECT id FROM endpoints WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise RuntimeError(f"failed to create endpoint record for '{name}'")
    return int(row["id"])


def _update_endpoint_window(conn, endpoint_id: int, window_used_delta: int, occurred_at: str) -> None:
    row = conn.execute(
        """
        SELECT window_used, window_reset_at
        FROM endpoints
        WHERE id = ?
        """,
        (endpoint_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"endpoint id '{endpoint_id}' does not exist")

    current_used = int(row["window_used"] or 0)
    reset_at = row["window_reset_at"]
    if reset_at and _parse_timestamp(reset_at) <= _parse_timestamp(occurred_at):
        current_used = 0
        reset_at = None

    if reset_at is None:
        reset_at = _shift_timestamp(occurred_at, hours=24)

    conn.execute(
        """
        UPDATE endpoints
        SET window_used = ?, window_reset_at = ?
        WHERE id = ?
        """,
        (current_used + window_used_delta, reset_at, endpoint_id),
    )


def _endpoint_is_at_limit(window_used: int | str | None, window_limit: str | None, window_reset_at: str | None) -> bool:
    limit = _parse_limit(window_limit)
    if limit is None:
        return False
    if window_reset_at and _parse_timestamp(window_reset_at) <= _parse_timestamp(utc_now()):
        return False
    return int(window_used or 0) >= limit


def _parse_limit(value: str | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text.isdigit():
        return None
    return int(text)


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _shift_timestamp(value: str, *, hours: int) -> str:
    shifted = _parse_timestamp(value) + timedelta(hours=hours)
    if shifted.tzinfo is None:
        shifted = shifted.replace(tzinfo=timezone.utc)
    return shifted.replace(microsecond=0).isoformat()
