from __future__ import annotations

import shutil
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path

from core.db import APP_ROOT, DB_PATH, connect, init_db


TABLES_COPIED_FROM_SNAPSHOT = (
    "projects",
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
)


@dataclass(frozen=True)
class ReindexResult:
    project_count: int
    brain_file_count: int
    task_count: int
    handoff_count: int
    decision_count: int
    error_count: int
    endpoint_count: int
    usage_event_count: int
    route_count: int
    benchmark_count: int
    bench_fixture_count: int
    bench_run_count: int


def reindex_database(db_path: Path = DB_PATH, app_root: Path = APP_ROOT) -> ReindexResult:
    snapshot_path = _snapshot_database(db_path)
    try:
        with connect(db_path) as conn:
            init_db(conn)
            _clear_all_tables(conn)
            if snapshot_path is not None:
                with sqlite3.connect(snapshot_path) as snapshot:
                    snapshot.row_factory = sqlite3.Row
                    _copy_table(snapshot, conn, "projects")
                    _rebuild_brain_files(app_root, snapshot, conn)
                    for table in TABLES_COPIED_FROM_SNAPSHOT:
                        if table in {"projects"}:
                            continue
                        _copy_table(snapshot, conn, table)
            else:
                _rebuild_projects_from_files(app_root, conn)
                _rebuild_brain_files_from_files(app_root, conn)
                _rebuild_bench_fixtures_from_files(app_root, conn)
                conn.commit()

            return _count_rows(conn)
    finally:
        if snapshot_path is not None and snapshot_path.exists():
            try:
                snapshot_path.unlink()
            except PermissionError:
                pass


def _snapshot_database(db_path: Path) -> Path | None:
    if not db_path.exists():
        return None

    snapshot_path = db_path.with_name(f"{db_path.stem}.snapshot{db_path.suffix}")
    shutil.copy2(db_path, snapshot_path)
    return snapshot_path


def _clear_all_tables(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    for table in (
        "bench_runs",
        "benchmarks",
        "usage_events",
        "errors",
        "handoffs",
        "decisions",
        "task_packets",
        "tasks",
        "brain_files",
        "routes",
        "bench_fixtures",
        "endpoints",
        "projects",
    ):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("PRAGMA foreign_keys = ON")


def _copy_table(snapshot: sqlite3.Connection, target: sqlite3.Connection, table: str) -> None:
    columns = [str(row["name"]) for row in snapshot.execute(f"PRAGMA table_info({table})").fetchall()]
    if not columns:
        return
    rows = snapshot.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY id ASC").fetchall()
    if not rows:
        return

    placeholders = ", ".join(["?"] * len(columns))
    column_sql = ", ".join(columns)
    for row in rows:
        target.execute(
            f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})",
            tuple(row[column] for column in columns),
        )


def _rebuild_projects_from_files(app_root: Path, conn: sqlite3.Connection) -> None:
    projects_root = app_root / "projects"
    project_dirs = sorted(
        path for path in projects_root.iterdir() if path.is_dir() and (path / "brain").is_dir()
    )
    for project_dir in project_dirs:
        brain_dir = project_dir / "brain"
        conn.execute(
            """
            INSERT INTO projects (name, slug, path, created_at, status, one_line_purpose, template)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_dir.name.replace("-", " ").title(),
                project_dir.name,
                f"projects/{project_dir.name}",
                "1970-01-01T00:00:00+00:00",
                "active",
                "Local-first project memory and task packet system.",
                "default",
            ),
        )
        project_id = int(conn.execute("SELECT id FROM projects WHERE slug = ?", (project_dir.name,)).fetchone()[0])
        _rebuild_brain_files_for_project(project_id, brain_dir, conn)


def _rebuild_brain_files(app_root: Path, snapshot: sqlite3.Connection, conn: sqlite3.Connection) -> None:
    projects_root = app_root / "projects"
    rows = snapshot.execute("SELECT id, slug FROM projects ORDER BY id ASC").fetchall()
    for row in rows:
        project_dir = projects_root / str(row["slug"])
        brain_dir = project_dir / "brain"
        if not brain_dir.is_dir():
            raise FileNotFoundError(f"missing brain directory for project '{row['slug']}'")
        _rebuild_brain_files_for_project(int(row["id"]), brain_dir, conn)


def _rebuild_brain_files_for_project(project_id: int, brain_dir: Path, conn: sqlite3.Connection) -> None:
    for path in sorted(brain_dir.glob("*.md")):
        conn.execute(
            """
            INSERT INTO brain_files (project_id, filename, last_updated, checksum)
            VALUES (?, ?, ?, ?)
            """,
            (
                project_id,
                f"brain/{path.name}",
                "1970-01-01T00:00:00+00:00",
                _checksum(path),
            ),
        )


def _rebuild_brain_files_from_files(app_root: Path, conn: sqlite3.Connection) -> None:
    projects_root = app_root / "projects"
    for project_dir in sorted(path for path in projects_root.iterdir() if path.is_dir() and (path / "brain").is_dir()):
        project_row = conn.execute("SELECT id FROM projects WHERE slug = ?", (project_dir.name,)).fetchone()
        if project_row is None:
            continue
        _rebuild_brain_files_for_project(int(project_row["id"]), project_dir / "brain", conn)


def _rebuild_bench_fixtures_from_files(app_root: Path, conn: sqlite3.Connection) -> None:
    fixtures_root = app_root / "bench" / "fixtures"
    if not fixtures_root.exists():
        return
    for folder in sorted(path for path in fixtures_root.iterdir() if path.is_dir()):
        input_path = folder / "input.md"
        rubric_path = folder / "rubric.md"
        if not input_path.exists() or not rubric_path.exists():
            continue
        conn.execute(
            """
            INSERT INTO bench_fixtures (task_type, input_path, rubric_path, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                folder.name,
                f"bench/fixtures/{folder.name}/input.md",
                f"bench/fixtures/{folder.name}/rubric.md",
                "1970-01-01T00:00:00+00:00",
            ),
        )


def _count_rows(conn: sqlite3.Connection) -> ReindexResult:
    def count(table: str) -> int:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    return ReindexResult(
        project_count=count("projects"),
        brain_file_count=count("brain_files"),
        task_count=count("tasks"),
        handoff_count=count("handoffs"),
        decision_count=count("decisions"),
        error_count=count("errors"),
        endpoint_count=count("endpoints"),
        usage_event_count=count("usage_events"),
        route_count=count("routes"),
        benchmark_count=count("benchmarks"),
        bench_fixture_count=count("bench_fixtures"),
        bench_run_count=count("bench_runs"),
    )


def _checksum(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def export_project_archive(slug: str, to_path: Path | None = None, app_root: Path = APP_ROOT) -> Path:
    project_path = app_root / "projects" / slug
    if not project_path.exists():
        raise ValueError(f"project '{slug}' does not exist")
    if not project_path.is_dir():
        raise ValueError(f"project '{slug}' is not a directory")

    archive_path = to_path or (app_root / f"{slug}.zip")
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(project_path.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=path.relative_to(app_root))

    return archive_path
