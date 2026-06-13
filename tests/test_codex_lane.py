from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from core.codex_lane import export_codex_execution_packet, import_codex_execution_response
from core.db import APP_ROOT, connect, init_db
from core.packets import create_packet
from core.projects import create_project


def test_codex_execution_packet_export_and_import(tmp_path: Path) -> None:
    slug = f"pytest-{uuid4().hex[:8]}"
    create_project(slug, template="software")
    try:
        packet = create_packet(
            slug=slug,
            title="Codex packet",
            goal="Create a codex execution packet for offline coverage.",
            size_class="small",
            preferred_route="codex_lb",
            expected_output="A packet file that can be imported locally.",
            acceptance="The codex packet exports and imports successfully.",
        )

        export_result = export_codex_execution_packet(slug, packet.task_id)
        assert export_result.path.exists()
        exported_text = export_result.path.read_text(encoding="utf-8")
        assert "MODE:" in exported_text
        assert "manual_execution" in exported_text

        import_file = tmp_path / "codex-import.md"
        content = """BEGIN DECISION\n\nDecision: Keep codex_lb manual-only for now.\n\nRationale: API and CLI preview modes stay gated metadata.\n\nEND DECISION\n"""
        import_file.write_text(content, encoding="utf-8")
        result = import_codex_execution_response(slug, 1, content, "current_status.md")
        assert result.path.exists()
        assert _count_where("handoffs", "project_id = ?", (_project_id(slug),)) >= 1
        assert _count_where("decisions", "project_id = ?", (_project_id(slug),)) >= 1
        assert _count_where("codex_packets", "project_id = ?", (_project_id(slug),)) >= 2
    finally:
        _cleanup_project(slug)


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
            conn.execute(f"DELETE FROM codex_packets WHERE task_id IN ({placeholders})", task_ids)
        conn.execute("DELETE FROM handoffs WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM decisions WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM errors WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM usage_events WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM brain_files WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM codex_packets WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM tasks WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()
    if project_path.exists():
        shutil.rmtree(project_path)
