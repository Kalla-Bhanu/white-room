from __future__ import annotations

import sqlite3

from typer.testing import CliRunner

import cli.main as cli_main
import core.health as health
from core import models_catalog
from core.db import init_db


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class _FakeClient:
    def __init__(self, *args, **kwargs) -> None:
        return None

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # pragma: no cover - context protocol
        return None

    def get(self, path):  # noqa: ANN001
        if path == "/models":
            return _FakeResponse(
                {
                    "data": [
                        {"id": "codex-mini", "context_window": 8192},
                        {"id": "codex-pro", "context_window": 16384},
                    ]
                }
            )
        return _FakeResponse({})


def test_codex_health_check_and_model_sync(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "whiteroom.db"

    def _connect():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(health, "connect", _connect)
    monkeypatch.setattr(models_catalog, "connect", _connect)
    monkeypatch.setattr("core.http_client.httpx.Client", _FakeClient)
    monkeypatch.setenv("CODEX_LB_API_KEY", "sk-codex-test")

    conn = _connect()
    try:
        init_db(conn)
        endpoint_id = int(
            conn.execute(
                """
                INSERT INTO endpoints (
                    name, endpoint_class, profile_id, base_url, capabilities, tier, daily_limit, window_limit,
                    status, model_name, supports_streaming, supports_tools, supports_json,
                    input_cost_per_1m, output_cost_per_1m, rate_limit_notes, disabled_reason,
                    created_at, updated_at
                ) VALUES (
                    'codex-live', 'codex_lb',
                    (SELECT id FROM provider_profiles WHERE endpoint_class = 'codex_lb' LIMIT 1),
                    'https://example.invalid/v1', 'execution', 'cloud', '100', '10',
                    'active', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                    '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
                )
                """,
            ).lastrowid
        )
        conn.commit()

        result = health.health_check("codex-live")
        assert result.endpoint_id == endpoint_id
        assert result.endpoint_class == "codex_lb"
        assert result.key_present is True
        assert result.reachable is True
        assert "reachable" in result.detail

        health_row = conn.execute(
            "SELECT reachable, key_present FROM endpoint_health WHERE endpoint_id = ? ORDER BY id DESC LIMIT 1",
            (endpoint_id,),
        ).fetchone()
        assert health_row is not None
        assert health_row["reachable"] == 1
        assert health_row["key_present"] == 1

        sync_result = health.sync_models("codex-live")
        assert sync_result.models_synced == 2
        assert sync_result.endpoint_class == "codex_lb"
        assert sync_result.last_model_sync

        model_rows = conn.execute(
            """
            SELECT model_name, active
            FROM provider_models
            WHERE endpoint_id = ?
            ORDER BY model_name ASC
            """,
            (endpoint_id,),
        ).fetchall()
        assert [(row["model_name"], row["active"]) for row in model_rows] == [
            ("codex-mini", 1),
            ("codex-pro", 1),
        ]
    finally:
        conn.close()


def test_health_sync_command_prints_model_sync_summary(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "whiteroom.db"

    def _connect():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(health, "connect", _connect)
    monkeypatch.setattr(models_catalog, "connect", _connect)
    monkeypatch.setattr("core.http_client.httpx.Client", _FakeClient)
    monkeypatch.setenv("CODEX_LB_API_KEY", "sk-codex-test")

    conn = _connect()
    try:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO endpoints (
                name, endpoint_class, profile_id, base_url, capabilities, tier, daily_limit, window_limit,
                status, model_name, supports_streaming, supports_tools, supports_json,
                input_cost_per_1m, output_cost_per_1m, rate_limit_notes, disabled_reason,
                created_at, updated_at
            ) VALUES (
                'codex-live', 'codex_lb',
                (SELECT id FROM provider_profiles WHERE endpoint_class = 'codex_lb' LIMIT 1),
                'https://example.invalid/v1', 'execution', 'cloud', '100', '10',
                'active', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
            )
            """
        )
        conn.commit()

        runner = CliRunner()
        result = runner.invoke(cli_main.app, ["health", "sync", "codex-live"])
        assert result.exit_code == 0, result.output
        assert "codex_lb" in result.output
        assert "synced=2" in result.output
    finally:
        conn.close()
