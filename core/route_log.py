from __future__ import annotations

import json
from dataclasses import dataclass

from core.db import connect, init_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS route_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    task_id INTEGER,
    message_id INTEGER,
    task_type TEXT NOT NULL,
    risk TEXT NOT NULL,
    size TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'ask',
    chosen_endpoint_id INTEGER,
    chosen_lane TEXT NOT NULL,
    est_cost_usd REAL,
    candidates TEXT NOT NULL DEFAULT '[]',
    explanation TEXT NOT NULL,
    requires_approval INTEGER NOT NULL DEFAULT 0,
    is_preview INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'dry_run',
    status TEXT NOT NULL DEFAULT 'suggested',
    created_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class RouteDecisionRecord:
    id: int
    project_id: int
    task_id: int | None
    message_id: int | None
    task_type: str
    risk: str
    size: str
    mode: str
    chosen_endpoint_id: int | None
    chosen_lane: str
    est_cost_usd: float | None
    candidates: list[dict[str, object]]
    explanation: str
    requires_approval: bool
    is_preview: bool
    source: str
    status: str
    created_at: str


def record_route_decision(
    *,
    project_slug: str,
    task_id: int | None,
    task_type: str,
    risk: str,
    size: str,
    chosen_lane: str,
    explanation: str,
    source: str,
    mode: str = "ask",
    est_cost_usd: float | None = None,
    status: str = "suggested",
    requires_approval: bool = False,
    is_preview: bool = False,
    chosen_endpoint_id: int | None = None,
    message_id: int | None = None,
    candidates: list[dict[str, object]] | None = None,
) -> RouteDecisionRecord:
    created_at = _utc_now()
    candidate_rows = candidates or []
    with connect() as conn:
        init_db(conn)
        project_id = _project_id(conn, project_slug)
        if chosen_endpoint_id is None and chosen_lane:
            chosen_endpoint_id = _endpoint_id(conn, chosen_lane)
        cursor = conn.execute(
            """
            INSERT INTO route_decisions (
                project_id, task_id, message_id, task_type, risk, size, mode, chosen_endpoint_id,
                chosen_lane, est_cost_usd, candidates, explanation, requires_approval, is_preview, source,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                task_id,
                message_id,
                task_type,
                risk,
                size,
                mode,
                chosen_endpoint_id,
                chosen_lane,
                est_cost_usd,
                json.dumps(candidate_rows),
                explanation,
                int(requires_approval),
                int(is_preview),
                source,
                status,
                created_at,
            ),
        )
        decision_id = int(cursor.lastrowid)
        conn.commit()

    return RouteDecisionRecord(
        id=decision_id,
        project_id=project_id,
        task_id=task_id,
        message_id=message_id,
        task_type=task_type,
        risk=risk,
        size=size,
        mode=mode,
        chosen_endpoint_id=chosen_endpoint_id,
        chosen_lane=chosen_lane,
        est_cost_usd=est_cost_usd,
        candidates=candidate_rows,
        explanation=explanation,
        requires_approval=requires_approval,
        is_preview=is_preview,
        source=source,
        status=status,
        created_at=created_at,
    )


def schema_tables() -> tuple[str, ...]:
    return ("route_decisions",)


def list_route_decisions(project_slug: str, limit: int = 100) -> list[dict[str, object]]:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT
                rd.id,
                rd.project_id,
                rd.task_id,
                rd.message_id,
                rd.task_type,
                rd.risk,
                rd.size,
                rd.mode,
                rd.chosen_endpoint_id,
                rd.chosen_lane,
                rd.est_cost_usd,
                rd.candidates,
                rd.explanation,
                rd.requires_approval,
                rd.is_preview,
                rd.source,
                rd.status,
                rd.created_at,
                p.slug AS project_slug,
                t.title AS task_title,
                t.status AS task_status,
                m.role AS message_role,
                m.content AS message_content
            FROM route_decisions AS rd
            JOIN projects AS p ON p.id = rd.project_id
            LEFT JOIN tasks AS t ON t.id = rd.task_id
            LEFT JOIN messages AS m ON m.id = rd.message_id
            WHERE p.slug = ?
            ORDER BY rd.created_at DESC, rd.id DESC
            LIMIT ?
            """,
            (project_slug, limit),
        ).fetchall()

    return [_row_to_dict(row) for row in rows]


def latest_route_decision_for_conversation(conversation_id: int) -> dict[str, object] | None:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT
                rd.id,
                rd.project_id,
                rd.task_id,
                rd.message_id,
                rd.task_type,
                rd.risk,
                rd.size,
                rd.mode,
                rd.chosen_endpoint_id,
                rd.chosen_lane,
                rd.est_cost_usd,
                rd.candidates,
                rd.explanation,
                rd.requires_approval,
                rd.is_preview,
                rd.source,
                rd.status,
                rd.created_at,
                p.slug AS project_slug,
                m.role AS message_role,
                m.content AS message_content
            FROM messages AS m
            JOIN route_decisions AS rd ON rd.id = m.route_decision_id
            JOIN projects AS p ON p.id = rd.project_id
            WHERE m.conversation_id = ? AND m.route_decision_id IS NOT NULL
            ORDER BY m.id DESC
            LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()

    if row is None:
        return None
    return _row_to_dict(row)


def _project_id(connection, slug: str) -> int:
    row = connection.execute("SELECT id FROM projects WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        raise ValueError(f"project '{slug}' does not exist")
    return int(row["id"])


def _endpoint_id(connection, endpoint_class: str) -> int | None:
    row = connection.execute(
        "SELECT id FROM endpoints WHERE endpoint_class = ? ORDER BY id ASC LIMIT 1",
        (endpoint_class,),
    ).fetchone()
    if row is None:
        return None
    return int(row["id"])


def _utc_now() -> str:
    from core.memory import utc_now

    return utc_now()


def _row_to_dict(row) -> dict[str, object]:
    candidates = _parse_candidates(str(row["candidates"] or "[]"))
    return {
        "id": int(row["id"]),
        "project_id": int(row["project_id"]),
        "project_slug": str(row["project_slug"]),
        "task_id": None if row["task_id"] is None else int(row["task_id"]),
        "message_id": None if row["message_id"] is None else int(row["message_id"]),
        "task_type": str(row["task_type"]),
        "risk": str(row["risk"]),
        "size": str(row["size"]),
        "mode": str(row["mode"] or "ask"),
        "chosen_endpoint_id": None if row["chosen_endpoint_id"] is None else int(row["chosen_endpoint_id"]),
        "chosen_lane": str(row["chosen_lane"]),
        "est_cost_usd": None if row["est_cost_usd"] is None else float(row["est_cost_usd"]),
        "candidates": candidates,
        "explanation": str(row["explanation"]),
        "requires_approval": bool(row["requires_approval"]),
        "is_preview": bool(row["is_preview"]),
        "source": str(row["source"]),
        "status": str(row["status"]),
        "created_at": str(row["created_at"]),
        "task_title": _row_text(row, "task_title"),
        "task_status": _row_text(row, "task_status"),
        "message_role": _row_text(row, "message_role"),
        "message_content": _row_text(row, "message_content"),
        "selected_candidate": next((candidate for candidate in candidates if candidate.get("selected")), None),
    }


def _parse_candidates(raw: str) -> list[dict[str, object]]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [candidate for candidate in data if isinstance(candidate, dict)]


def _row_text(row, key: str) -> str:
    keys = row.keys() if hasattr(row, "keys") else []
    if key not in keys:
        return ""
    value = row[key]
    return "" if value is None else str(value)
