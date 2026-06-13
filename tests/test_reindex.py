from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

from cli.reindex_cmd import run_reindex
from core.db import APP_ROOT, DB_PATH, connect, init_db
from core.manual_lane import import_manual_claude_output
from core.packets import create_packet
from core.projects import create_project
from core.router import dry_run_route


def test_create_project_packet_handoff_import_and_cleanup(tmp_path: Path) -> None:
    slug = f"pytest-{uuid4().hex[:8]}"
    project = create_project(slug, template="software")
    try:
        project_id = _project_id(slug)
        assert project.path.joinpath("brain").is_dir()
        assert len(list(project.path.joinpath("brain").iterdir())) == 10

        packet = create_packet(
            slug=slug,
            title="Pytest packet",
            goal="Create a packet for regression coverage.",
            size_class="small",
            preferred_route="deterministic",
            expected_output="A packet file.",
            acceptance="A packet row is stored.",
        )
        assert packet.path.exists()
        assert _count_where("tasks", "project_id = ?", (project_id,)) == 1
        assert _count_where("task_packets", "task_id = ?", (packet.task_id,)) == 1

        import_file = tmp_path / "manual_claude.md"
        import_file.write_text(
            """BEGIN DECISION\n\nDecision: Keep the scaffold deterministic.\n\nRationale: Tests need stable output.\n\nEND DECISION\n""",
            encoding="utf-8",
        )
        imported = import_manual_claude_output(slug, import_file, "current_status.md")
        assert imported.path.exists()
        assert _count_where("handoffs", "project_id = ?", (project_id,)) >= 1
        assert _count_where("decisions", "project_id = ?", (project_id,)) >= 1
    finally:
        _cleanup_project(slug)


def test_route_dry_run_uses_router_gate() -> None:
    result = dry_run_route("white-room", 1)
    assert result.endpoint_class in {"manual_claude", "codex_lb"}
    assert isinstance(result.preview, dict)
    assert "preview" in result.preview or "request" in result.preview


def test_reindex_rebuilds_table_counts() -> None:
    before = _table_counts()
    message = run_reindex()
    after = _table_counts()

    assert "reindexed SQLite from files" in message
    assert before == after


def _project_id(slug: str) -> int:
    with connect() as conn:
        init_db(conn)
        row = conn.execute("SELECT id FROM projects WHERE slug = ?", (slug,)).fetchone()
        assert row is not None
        return int(row["id"])


def _count_where(table: str, clause: str, params: tuple[object, ...]) -> int:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {clause}", params).fetchone()
        return int(row[0])


def _table_counts() -> dict[str, int]:
    with connect() as conn:
        init_db(conn)
        tables = [
            "projects",
            "brain_files",
            "tasks",
            "task_packets",
            "handoffs",
            "decisions",
            "errors",
            "endpoints",
            "usage_events",
            "routes",
            "benchmarks",
            "bench_fixtures",
            "bench_runs",
        ]
        return {table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in tables}


def _cleanup_project(slug: str) -> None:
    project_path = APP_ROOT / "projects" / slug
    with connect() as conn:
        init_db(conn)
        project_row = conn.execute("SELECT id FROM projects WHERE slug = ?", (slug,)).fetchone()
        if project_row is None:
            return
        project_id = int(project_row["id"])
        task_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM tasks WHERE project_id = ?", (project_id,)).fetchall()]
        if task_ids:
            placeholders = ", ".join(["?"] * len(task_ids))
            conn.execute(f"DELETE FROM task_packets WHERE task_id IN ({placeholders})", task_ids)
        conn.execute("DELETE FROM handoffs WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM decisions WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM errors WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM usage_events WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM brain_files WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM tasks WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()
    if project_path.exists():
        shutil.rmtree(project_path)
