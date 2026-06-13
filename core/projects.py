from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.db import APP_ROOT, connect, init_db
from core.agents import seed_agent_threads_for_project
from core.chat import _delete_conversation_records
from core.templates_loader import project_template_exists, project_template_path, render_template


BRAIN_TEMPLATES = {
    "business_scope.md": "brain/business_scope.md.j2",
    "active_plan.md": "brain/active_plan.md.j2",
    "architecture.md": "brain/architecture.md.j2",
    "tasks.md": "brain/tasks.md.j2",
    "decisions.md": "brain/decisions.md.j2",
    "errors.md": "brain/errors.md.j2",
    "handoffs.md": "brain/handoffs.md.j2",
    "model_routes.md": "brain/model_routes.md.j2",
    "verification.md": "brain/verification.md.j2",
    "current_status.md": "brain/current_status.md.j2",
}

THREAD_TEMPLATES = {
    "orchestrator.md": "threads/orchestrator.md.j2",
    "business_scope.md": "threads/business_scope.md.j2",
    "architecture.md": "threads/architecture.md.j2",
    "implementation.md": "threads/implementation.md.j2",
    "verification.md": "threads/verification.md.j2",
    "handoff_memory.md": "threads/handoff_memory.md.j2",
}


class ProjectExistsError(RuntimeError):
    pass


@dataclass(frozen=True)
class CreatedProject:
    name: str
    slug: str
    path: Path


def slugify(name: str) -> str:
    slug = name.strip().lower().replace(" ", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug:
        raise ValueError("project name must contain at least one letter or number")
    return slug


def create_project(name: str, template: str = "default") -> CreatedProject:
    slug = slugify(name)
    template_name = template.strip() or "default"
    if not project_template_exists(template_name):
        raise ValueError(f"project template '{template_name}' does not exist")
    projects_root = APP_ROOT / "projects"
    project_path = projects_root / slug

    with connect() as conn:
        init_db(conn)
        if project_path.exists() or _project_slug_exists(conn, slug):
            raise ProjectExistsError(f"project '{slug}' already exists")

        created_at = _utc_now()
        project_path.mkdir(parents=True)
        try:
            _create_project_folders(project_path)
            _write_project_files(
                project_path,
                name=name,
                slug=slug,
                created_at=created_at,
                template=template_name,
            )
            project_id = _insert_project(conn, name, slug, project_path, created_at, template_name)
            _insert_brain_files(conn, project_id, project_path, created_at)
            seed_agent_threads_for_project(conn, project_id, created_at)
            conn.commit()
        except Exception:
            conn.rollback()
            if project_path.exists():
                shutil.rmtree(project_path)
            raise

    return CreatedProject(name=name, slug=slug, path=project_path)


def delete_project(slug: str) -> None:
    with connect() as conn:
        init_db(conn)
        row = conn.execute("SELECT id, path FROM projects WHERE slug = ?", (slug,)).fetchone()
        if row is None:
            raise ValueError(f"project '{slug}' does not exist")
        project_id = int(row["id"])
        project_path = APP_ROOT / str(row["path"])

        conversation_rows = conn.execute("SELECT id FROM conversations WHERE project_id = ?", (project_id,)).fetchall()
        for conversation_row in conversation_rows:
            _delete_conversation_records(conn, int(conversation_row["id"]))

        task_rows = conn.execute("SELECT id FROM tasks WHERE project_id = ?", (project_id,)).fetchall()
        task_ids = [int(task_row["id"]) for task_row in task_rows]
        if task_ids:
            placeholders = ", ".join("?" for _ in task_ids)
            conn.execute(f"DELETE FROM task_packets WHERE task_id IN ({placeholders})", task_ids)
            conn.execute(f"DELETE FROM handoffs WHERE task_id IN ({placeholders})", task_ids)
            conn.execute(f"DELETE FROM route_decisions WHERE task_id IN ({placeholders})", task_ids)
            conn.execute(f"DELETE FROM execution_runs WHERE task_id IN ({placeholders})", task_ids)
            conn.execute(f"DELETE FROM codex_packets WHERE task_id IN ({placeholders})", task_ids)

        conn.execute("DELETE FROM handoffs WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM agent_threads WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM brain_files WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM approval_gates WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM route_decisions WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM execution_runs WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM codex_packets WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM tasks WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()

    projects_root = (APP_ROOT / "projects").resolve()
    resolved_path = project_path.resolve()
    if projects_root not in resolved_path.parents:
        raise ValueError(f"refusing to delete unexpected project path: {resolved_path}")
    if resolved_path.exists():
        shutil.rmtree(resolved_path)


def _project_slug_exists(conn: sqlite3.Connection, slug: str) -> bool:
    row = conn.execute("SELECT 1 FROM projects WHERE slug = ?", (slug,)).fetchone()
    return row is not None


def _create_project_folders(project_path: Path) -> None:
    for folder in ("brain", "threads", "packets", "artifacts"):
        (project_path / folder).mkdir(parents=True, exist_ok=False)


def _write_project_files(project_path: Path, name: str, slug: str, created_at: str, template: str) -> None:
    context = {"project_name": name, "project_slug": slug, "created_at": created_at}
    for filename, template_name in BRAIN_TEMPLATES.items():
        content = render_template(project_template_path(template, template_name), **context)
        (project_path / "brain" / filename).write_text(content, encoding="utf-8")

    for filename, template_name in THREAD_TEMPLATES.items():
        content = render_template(template_name, **context)
        (project_path / "threads" / filename).write_text(content, encoding="utf-8")


def _insert_project(
    conn: sqlite3.Connection,
    name: str,
    slug: str,
    project_path: Path,
    created_at: str,
    template: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO projects (name, slug, path, created_at, status, one_line_purpose, template)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            slug,
            str(project_path.relative_to(APP_ROOT)),
            created_at,
            "active",
            "Local-first project memory and task packet system.",
            template,
        ),
    )
    return int(cursor.lastrowid)


def _insert_brain_files(
    conn: sqlite3.Connection,
    project_id: int,
    project_path: Path,
    created_at: str,
) -> None:
    for filename in BRAIN_TEMPLATES:
        path = project_path / "brain" / filename
        conn.execute(
            """
            INSERT INTO brain_files (project_id, filename, last_updated, checksum)
            VALUES (?, ?, ?, ?)
            """,
            (
                project_id,
                f"brain/{filename}",
                created_at,
                _checksum(path),
            ),
        )


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
