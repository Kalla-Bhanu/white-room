from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass

from core.db import connect, init_db
from core.memory import get_project, utc_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_gates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    target_endpoint_id INTEGER,
    payload_summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    decided_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (target_endpoint_id) REFERENCES endpoints(id)
);
"""


@dataclass(frozen=True)
class ApprovalGateRecord:
    id: int
    project_id: int
    action_type: str
    target_endpoint_id: int | None
    payload_summary: str
    status: str
    decided_at: str | None
    created_at: str


@dataclass(frozen=True)
class ApprovalGrantRecord:
    id: int
    project_id: int | None
    endpoint_id: int | None
    endpoint_class: str | None
    modes: list[str]
    est_cost_ceiling_usd: float | None
    expires_at: str | None
    turns_remaining: int | None
    active: bool
    created_at: str
    revoked_at: str | None


def create_approval_gate(
    *,
    project_slug: str,
    action_type: str,
    target_endpoint_id: int | None,
    payload_summary: str,
) -> ApprovalGateRecord:
    project = get_project(project_slug)
    with connect() as conn:
        init_db(conn)
        existing = _lookup_gate(
            conn,
            project_id=project.id,
            action_type=action_type,
            target_endpoint_id=target_endpoint_id,
            payload_summary=payload_summary,
        )
        if existing is not None:
            return existing

        created_at = utc_now()
        cursor = conn.execute(
            """
            INSERT INTO approval_gates (
                project_id, action_type, target_endpoint_id, payload_summary, status, decided_at, created_at
            ) VALUES (?, ?, ?, ?, 'pending', NULL, ?)
            """,
            (project.id, action_type, target_endpoint_id, payload_summary, created_at),
        )
        gate_id = int(cursor.lastrowid)
        conn.commit()

    return get_approval_gate(gate_id)


def create_approval_grant(
    *,
    project_slug: str | None,
    endpoint_id: int | None = None,
    endpoint_class: str | None = None,
    modes: list[str] | None = None,
    est_cost_ceiling_usd: float | None = None,
    expires_at: str | None = None,
    turns_remaining: int | None = None,
    active: bool = True,
) -> ApprovalGrantRecord:
    project_id = None
    if project_slug is not None:
        project = get_project(project_slug)
        project_id = project.id
    created_at = utc_now()
    normalized_modes = _normalize_modes(modes)
    with connect() as conn:
        init_db(conn)
        cursor = conn.execute(
            """
            INSERT INTO approval_grants (
                project_id, endpoint_id, endpoint_class, modes, est_cost_ceiling_usd,
                expires_at, turns_remaining, active, created_at, revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                project_id,
                endpoint_id,
                endpoint_class,
                json.dumps(normalized_modes),
                est_cost_ceiling_usd,
                expires_at,
                turns_remaining,
                int(active),
                created_at,
            ),
        )
        grant_id = int(cursor.lastrowid)
        conn.commit()
    return get_approval_grant(grant_id)


def get_approval_gate(gate_id: int) -> ApprovalGateRecord:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT id, project_id, action_type, target_endpoint_id, payload_summary, status, decided_at, created_at
            FROM approval_gates
            WHERE id = ?
            """,
            (gate_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"approval gate {gate_id} does not exist")
    return _row_to_gate(row)


def get_approval_grant(grant_id: int) -> ApprovalGrantRecord:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT id, project_id, endpoint_id, endpoint_class, modes, est_cost_ceiling_usd,
                   expires_at, turns_remaining, active, created_at, revoked_at
            FROM approval_grants
            WHERE id = ?
            """,
            (grant_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"approval grant {grant_id} does not exist")
    return _row_to_grant(row)


def decide_approval_gate(gate_id: int, decision: str) -> ApprovalGateRecord:
    normalized = decision.strip().lower()
    if normalized in {"approve", "approved", "yes", "allow"}:
        status = "approved"
    elif normalized in {"deny", "denied", "no", "block"}:
        status = "denied"
    else:
        raise ValueError(f"invalid approval decision '{decision}'")

    decided_at = utc_now()
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            "SELECT id FROM approval_gates WHERE id = ?",
            (gate_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"approval gate {gate_id} does not exist")
        conn.execute(
            """
            UPDATE approval_gates
            SET status = ?, decided_at = ?
            WHERE id = ?
            """,
            (status, decided_at, gate_id),
        )
        conn.commit()
    return get_approval_gate(gate_id)


def revoke_approval_grant(grant_id: int) -> ApprovalGrantRecord:
    revoked_at = utc_now()
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            "SELECT id FROM approval_grants WHERE id = ?",
            (grant_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"approval grant {grant_id} does not exist")
        conn.execute(
            """
            UPDATE approval_grants
            SET active = 0, revoked_at = ?
            WHERE id = ?
            """,
            (revoked_at, grant_id),
        )
        conn.commit()
    return get_approval_grant(grant_id)


def gate_allows_action(
    *,
    project_slug: str,
    action_type: str,
    target_endpoint_id: int | None,
    payload_summary: str,
    endpoint_class: str,
    mode: str | None = None,
    risk: str | None = None,
    est_cost_usd: float | None = None,
) -> tuple[bool, ApprovalGateRecord, str]:
    project = get_project(project_slug)
    grant = _matching_active_grant(
        project_id=project.id,
        target_endpoint_id=target_endpoint_id,
        endpoint_class=endpoint_class,
        mode=mode,
        risk=risk,
        payload_summary=payload_summary,
        est_cost_usd=est_cost_usd,
    )
    if grant is not None:
        return True, _grant_as_gate_record(project.id, grant, action_type, target_endpoint_id, payload_summary), "approved by grant"

    gate = create_approval_gate(
        project_slug=project_slug,
        action_type=action_type,
        target_endpoint_id=target_endpoint_id,
        payload_summary=payload_summary,
    )
    if gate.status != "approved":
        return False, gate, "needs approval"

    if _requires_configured_key(endpoint_class) and not _configured_key_present(endpoint_class):
        return False, gate, "configured key missing"

    return True, gate, "approved"


def schema_tables() -> tuple[str, ...]:
    return ("approval_gates", "approval_grants")


def _lookup_gate(
    connection: sqlite3.Connection,
    *,
    project_id: int,
    action_type: str,
    target_endpoint_id: int | None,
    payload_summary: str,
) -> ApprovalGateRecord | None:
    row = connection.execute(
        """
        SELECT id, project_id, action_type, target_endpoint_id, payload_summary, status, decided_at, created_at
        FROM approval_gates
        WHERE project_id = ?
          AND action_type = ?
          AND COALESCE(target_endpoint_id, -1) = COALESCE(?, -1)
          AND payload_summary = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (project_id, action_type, target_endpoint_id, payload_summary),
    ).fetchone()
    if row is None:
        return None
    return _row_to_gate(row)


def _row_to_gate(row: sqlite3.Row) -> ApprovalGateRecord:
    return ApprovalGateRecord(
        id=int(row["id"]),
        project_id=int(row["project_id"]),
        action_type=str(row["action_type"]),
        target_endpoint_id=None if row["target_endpoint_id"] is None else int(row["target_endpoint_id"]),
        payload_summary=str(row["payload_summary"]),
        status=str(row["status"]),
        decided_at=None if row["decided_at"] is None else str(row["decided_at"]),
        created_at=str(row["created_at"]),
    )


def _row_to_grant(row: sqlite3.Row) -> ApprovalGrantRecord:
    modes = _normalize_modes(_json_list(str(row["modes"] or "[]")))
    return ApprovalGrantRecord(
        id=int(row["id"]),
        project_id=None if row["project_id"] is None else int(row["project_id"]),
        endpoint_id=None if row["endpoint_id"] is None else int(row["endpoint_id"]),
        endpoint_class=None if row["endpoint_class"] is None else str(row["endpoint_class"]),
        modes=modes,
        est_cost_ceiling_usd=None if row["est_cost_ceiling_usd"] is None else float(row["est_cost_ceiling_usd"]),
        expires_at=None if row["expires_at"] is None else str(row["expires_at"]),
        turns_remaining=None if row["turns_remaining"] is None else int(row["turns_remaining"]),
        active=bool(int(row["active"])),
        created_at=str(row["created_at"]),
        revoked_at=None if row["revoked_at"] is None else str(row["revoked_at"]),
    )


def _requires_configured_key(endpoint_class: str) -> bool:
    return endpoint_class in {
        "openai_compatible_cloud",
        "anthropic_compatible_cloud",
        "provider_specific_cloud",
    }


def _configured_key_present(endpoint_class: str) -> bool:
    required_env_vars = {
        "openai_compatible_cloud": ("OPENAI_COMPAT_API_KEY",),
        "anthropic_compatible_cloud": ("ANTHROPIC_API_KEY",),
        "provider_specific_cloud": tuple(_provider_specific_env_vars()),
    }.get(endpoint_class, ())
    if not required_env_vars:
        return True
    return any(bool(os.environ.get(name, "").strip()) for name in required_env_vars)


def _matching_active_grant(
    *,
    project_id: int,
    target_endpoint_id: int | None,
    endpoint_class: str,
    mode: str | None,
    risk: str | None,
    payload_summary: str,
    est_cost_usd: float | None,
) -> ApprovalGrantRecord | None:
    if mode is None:
        return None
    if (risk or "").strip().lower() == "high":
        return None

    now = utc_now()
    cost_estimate = est_cost_usd if est_cost_usd is not None else _payload_cost_estimate(payload_summary)
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT id, project_id, endpoint_id, endpoint_class, modes, est_cost_ceiling_usd,
                   expires_at, turns_remaining, active, created_at, revoked_at
            FROM approval_grants
            WHERE active = 1
            ORDER BY id DESC
            """
        ).fetchall()

    for row in rows:
        grant = _row_to_grant(row)
        if grant.project_id is not None and grant.project_id != project_id:
            continue
        if grant.endpoint_id is not None and target_endpoint_id is not None and grant.endpoint_id != target_endpoint_id:
            continue
        if grant.endpoint_id is not None and target_endpoint_id is None:
            continue
        if grant.endpoint_class is not None and grant.endpoint_class != endpoint_class:
            continue
        if grant.expires_at is not None and grant.expires_at <= now:
            continue
        if grant.turns_remaining is not None and grant.turns_remaining <= 0:
            continue
        if grant.est_cost_ceiling_usd is not None and cost_estimate is not None and cost_estimate > grant.est_cost_ceiling_usd:
            continue
        if grant.modes and "*" not in grant.modes:
            normalized_mode = (mode or "").strip().lower()
            if normalized_mode not in {item.strip().lower() for item in grant.modes}:
                continue
        return grant
    return None


def _grant_as_gate_record(
    project_id: int,
    grant: ApprovalGrantRecord,
    action_type: str,
    target_endpoint_id: int | None,
    payload_summary: str,
) -> ApprovalGateRecord:
    created_at = utc_now()
    return ApprovalGateRecord(
        id=0,
        project_id=project_id,
        action_type=action_type,
        target_endpoint_id=target_endpoint_id,
        payload_summary=payload_summary,
        status="granted",
        decided_at=created_at,
        created_at=created_at,
    )


def _payload_cost_estimate(payload_summary: str) -> float | None:
    try:
        payload = json.loads(payload_summary)
    except json.JSONDecodeError:
        return None
    value = payload.get("est_cost_usd") if isinstance(payload, dict) else None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_list(value: str) -> list[str]:
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded if str(item).strip()]


def _normalize_modes(modes: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for mode in modes or []:
        text = str(mode).strip().lower()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _provider_specific_env_vars() -> list[str]:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT required_env_vars
            FROM provider_profiles
            WHERE compatibility_style IN ('gemini', 'deepseek', 'openrouter', 'groq', 'opencode')
               OR endpoint_class = 'provider_specific_cloud'
            ORDER BY id ASC
            """
        ).fetchall()
    env_names: list[str] = ["PROVIDER_SPECIFIC_API_KEY"]
    for row in rows:
        required_env_vars = str(row["required_env_vars"] or "").strip()
        if not required_env_vars:
            continue
        try:
            values = json.loads(required_env_vars)
        except json.JSONDecodeError:
            continue
        for value in values:
            text = str(value).strip()
            if text and text not in env_names:
                env_names.append(text)
    return env_names
