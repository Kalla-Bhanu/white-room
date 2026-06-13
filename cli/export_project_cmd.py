from __future__ import annotations

from pathlib import Path

from core.reindex import export_project_archive


def run_export_project(slug: str, to_path: Path | None) -> Path:
    return export_project_archive(slug, to_path=to_path)
