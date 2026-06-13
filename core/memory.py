from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.db import APP_ROOT, connect, init_db


@dataclass(frozen=True)
class ProjectRecord:
    id: int
    name: str
    slug: str
    path: Path
    status: str
    one_line_purpose: str


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def list_projects() -> list[ProjectRecord]:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT id, name, slug, path, status, one_line_purpose
            FROM projects
            ORDER BY created_at ASC
            """
        ).fetchall()
    return [_project_from_row(row) for row in rows]


def get_project(slug: str) -> ProjectRecord:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT id, name, slug, path, status, one_line_purpose
            FROM projects
            WHERE slug = ?
            """,
            (slug,),
        ).fetchone()
    if row is None:
        raise ValueError(f"project '{slug}' does not exist")
    return _project_from_row(row)


def read_current_status(slug: str) -> str:
    project = get_project(slug)
    return (project.path / "brain" / "current_status.md").read_text(encoding="utf-8")


def append_handoff(
    slug: str,
    from_worker: str,
    to_worker: str,
    summary: str,
    artifact_paths: list[str] | None = None,
    task_id: int | None = None,
    thread_from: str = "orchestrator",
    thread_to: str = "orchestrator",
) -> Path:
    project = get_project(slug)
    artifacts = artifact_paths or []
    created_at = utc_now()
    handoffs_path = project.path / "brain" / "handoffs.md"
    artifact_text = ", ".join(artifacts) if artifacts else "none"

    entry = (
        f"\n## Handoff {created_at} -- Manual Entry\n"
        f"- thread_from: {thread_from}\n"
        f"- thread_to: {thread_to}\n"
        f"- from: {from_worker}\n"
        f"- to: {to_worker}\n"
        f"- summary: {summary}\n"
        f"- artifacts: {artifact_text}\n"
    )
    handoffs_path.write_text(
        handoffs_path.read_text(encoding="utf-8") + entry,
        encoding="utf-8",
    )

    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO handoffs
                (project_id, task_id, thread_from, thread_to, from_worker, to_worker, summary, artifact_paths, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project.id,
                task_id,
                thread_from,
                thread_to,
                from_worker,
                to_worker,
                summary,
                "\n".join(artifacts),
                created_at,
            ),
        )
        _refresh_brain_file(conn, project.id, "brain/handoffs.md", handoffs_path, created_at)
        if task_id is not None:
            conn.execute(
                """
                UPDATE tasks
                SET thread = ?, updated_at = ?
                WHERE id = ?
                """,
                (thread_to, created_at, task_id),
            )
        conn.commit()

    return handoffs_path


def write_current_status(slug: str, status_text: str) -> Path:
    project = get_project(slug)
    status_path = project.path / "brain" / "current_status.md"
    status_path.write_text(status_text.rstrip() + "\n", encoding="utf-8")

    with connect() as conn:
        init_db(conn)
        _refresh_brain_file(conn, project.id, "brain/current_status.md", status_path, utc_now())
        conn.commit()

    return status_path


def update_brain_file_index(slug: str, brain_filename: str) -> None:
    project = get_project(slug)
    path = project.path / "brain" / brain_filename
    if not path.exists():
        raise ValueError(f"brain file '{brain_filename}' does not exist for project '{slug}'")

    with connect() as conn:
        init_db(conn)
        _refresh_brain_file(conn, project.id, f"brain/{brain_filename}", path, utc_now())
        conn.commit()


def _refresh_brain_file(conn, project_id: int, filename: str, path: Path, updated_at: str) -> None:
    conn.execute(
        """
        INSERT INTO brain_files (project_id, filename, last_updated, checksum)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(project_id, filename)
        DO UPDATE SET last_updated = excluded.last_updated, checksum = excluded.checksum
        """,
        (project_id, filename, updated_at, _checksum(path)),
    )


def _project_from_row(row) -> ProjectRecord:
    return ProjectRecord(
        id=int(row["id"]),
        name=str(row["name"]),
        slug=str(row["slug"]),
        path=APP_ROOT / str(row["path"]),
        status=str(row["status"]),
        one_line_purpose=str(row["one_line_purpose"]),
    )


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
