from __future__ import annotations

import json

import pytest

from adapters.codex_lb import CodexLBAdapter
from core.codex_modes import codex_mode_catalog, codex_mode_label, normalize_codex_mode
from core.db import connect, init_db


def test_codex_mode_catalog_marks_preview_modes() -> None:
    catalog = codex_mode_catalog()
    names = [record.name for record in catalog]
    assert names == ["manual_execution", "api_preview", "cli_preview"]
    assert catalog[0].approval_required is False
    assert catalog[0].live_allowed is False
    assert catalog[1].approval_required is True
    assert catalog[2].approval_required is True
    assert codex_mode_label("manual") == "Manual execution packet"
    assert normalize_codex_mode("CLI") == "cli_preview"


def test_codex_adapter_prepare_exposes_mode_metadata() -> None:
    adapter = CodexLBAdapter()
    request = adapter.prepare({"project_slug": "white-room", "task_id": 1})
    assert request["mode"] == "manual_execution"
    assert request["manual_only"] is True
    assert request["mode_label"] == "Manual execution packet"

    preview = adapter.dry_run(request)
    assert preview["mode"] == "manual_execution"
    assert "Manual execution packet" in preview["preview"]

    api_adapter = CodexLBAdapter(mode="api")
    api_request = api_adapter.prepare({"project_slug": "white-room"})
    assert api_request["mode"] == "api_preview"
    assert api_request["manual_only"] is False


def test_codex_adapter_call_stays_manual_only() -> None:
    adapter = CodexLBAdapter(mode="cli")
    with pytest.raises(NotImplementedError, match="manual-only"):
        adapter.call({"project_slug": "white-room"})


def test_codex_profile_seed_includes_integration_modes() -> None:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT integration_modes, default_integration_mode
            FROM provider_profiles
            WHERE endpoint_class = 'codex_lb'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
    assert row is not None
    modes = json.loads(str(row["integration_modes"]))
    assert modes == ["manual_execution", "api_preview", "cli_preview"]
    assert str(row["default_integration_mode"]) == "manual_execution"
