from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.db import connect, init_db
from core.memory import get_project, update_brain_file_index, utc_now


@dataclass(frozen=True)
class EscalationResult:
    project_slug: str
    task_id: int
    from_tier: str
    to_tier: str
    attempts: int
    error_id: int
    model_routes_path: Path


def escalate_task_failure(
    project_slug: str,
    task_id: int,
    description: str,
    current_tier: str | None = None,
) -> EscalationResult:
    project = get_project(project_slug)
    now = utc_now()

    with connect() as conn:
        init_db(conn)
        task_row = conn.execute(
            """
            SELECT id, preferred_tier
            FROM tasks
            WHERE project_id = ? AND id = ?
            """,
            (project.id, task_id),
        ).fetchone()
        if task_row is None:
            raise ValueError(f"task '{task_id}' does not exist for project '{project_slug}'")

        from_tier = _canonical_tier(current_tier or str(task_row["preferred_tier"]))
        to_tier = _next_tier(from_tier)

        error_row = conn.execute(
            """
            SELECT id, attempts
            FROM errors
            WHERE project_id = ? AND task_id = ? AND description = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (project.id, task_id, description),
        ).fetchone()
        if error_row is None:
            cursor = conn.execute(
                """
                INSERT INTO errors
                    (project_id, task_id, description, status, attempts, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (project.id, task_id, description, "escalated", 1, now),
            )
            error_id = int(cursor.lastrowid)
            attempts = 1
        else:
            attempts = int(error_row["attempts"]) + 1
            error_id = int(error_row["id"])
            conn.execute(
                """
                UPDATE errors
                SET attempts = ?, status = ?
                WHERE id = ?
                """,
                (attempts, "escalated", error_id),
            )

        conn.execute(
            """
            UPDATE tasks
            SET preferred_tier = ?, updated_at = ?
            WHERE id = ?
            """,
            (to_tier, now, task_id),
        )
        conn.commit()

    model_routes_path = project.path / "brain" / "model_routes.md"
    entry = (
        f"\n## Route {now} -- Escalation\n"
        f"- task: {task_id}\n"
        f"- from: {from_tier}\n"
        f"- to: {to_tier}\n"
        f"- attempts: {attempts}\n"
        f"- reason: {description}\n"
    )
    model_routes_path.write_text(
        model_routes_path.read_text(encoding="utf-8") + entry,
        encoding="utf-8",
    )
    update_brain_file_index(project.slug, "model_routes.md")

    return EscalationResult(
        project_slug=project.slug,
        task_id=task_id,
        from_tier=from_tier,
        to_tier=to_tier,
        attempts=attempts,
        error_id=error_id,
        model_routes_path=model_routes_path,
    )


def _canonical_tier(tier: str) -> str:
    normalized = tier.strip().lower().replace(" ", "_").replace("-", "_")
    if normalized in {"codex", "codex_lb"}:
        return "execution"
    if normalized in {"manual", "manual_claude"}:
        return "manual_claude"
    if normalized in {"deterministic", "execution"}:
        return normalized
    if "local" in normalized:
        return "execution"
    return "execution"


def _next_tier(tier: str) -> str:
    order = ["deterministic", "execution", "manual_claude"]
    try:
        index = order.index(tier)
    except ValueError:
        return "execution"
    if index + 1 >= len(order):
        return order[-1]
    return order[index + 1]
