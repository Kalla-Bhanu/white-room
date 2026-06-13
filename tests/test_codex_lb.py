from __future__ import annotations

import json
import sqlite3

import pytest

from adapters.codex_lb import ACTION_TYPE, CODEX_API_KEY_ENV, CODEX_BASE_URL_ENV, CodexLBAdapter
from core.approvals import create_approval_gate, decide_approval_gate
from core.db import init_db
from core import models_catalog


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
                        {
                            "id": "codex-mini",
                            "context_window": 8192,
                            "supports_streaming": True,
                            "supports_tools": False,
                            "supports_json": True,
                        },
                        {
                            "id": "codex-pro",
                            "context_window": 16384,
                            "supports_streaming": False,
                            "supports_tools": True,
                            "supports_json": False,
                        },
                    ]
                }
            )
        return _FakeResponse({})

    def post(self, path, json=None):  # noqa: ANN001, A002
        if path == "/chat/completions":
            return _FakeResponse(
                {
                    "choices": [
                        {
                            "message": {"content": "codex live result"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 4},
                }
            )
        return _FakeResponse({})


def test_codex_adapter_prepare_and_manual_call_stay_manual_only() -> None:
    adapter = CodexLBAdapter()
    request = adapter.prepare({"project_slug": "white-room", "task_id": 1})
    assert request["mode"] == "manual_execution"
    assert request["manual_only"] is True
    assert request["live_mode"] is False
    assert request["mode_label"] == "Manual execution packet"

    with pytest.raises(NotImplementedError, match="manual-only"):
        adapter.call({"project_slug": "white-room"})


def test_codex_live_chat_runs_after_approval(monkeypatch) -> None:
    monkeypatch.setattr("core.http_client.httpx.Client", _FakeClient)
    monkeypatch.setenv(CODEX_API_KEY_ENV, "sk-codex-test")
    monkeypatch.setenv(CODEX_BASE_URL_ENV, "https://example.invalid/v1")

    adapter = CodexLBAdapter(mode="api_preview", live_enabled=True)
    prompt = "Implement the new model sync helper."
    payload_summary = json.dumps(
        {
            "adapter": "codex_lb",
            "base_url": "https://example.invalid/v1",
            "conversation_id": None,
            "mode": "api_preview",
            "model_name": "codex-mini",
            "project_slug": "white-room",
            "prompt": prompt,
            "task_id": None,
        },
        sort_keys=True,
    )
    gate = create_approval_gate(
        project_slug="white-room",
        action_type=ACTION_TYPE,
        target_endpoint_id=None,
        payload_summary=payload_summary,
    )
    decide_approval_gate(gate.id, "approve")

    result = adapter.send_chat(
        {
            "project_slug": "white-room",
            "task_id": None,
            "conversation_id": None,
            "prompt": prompt,
            "base_url": "https://example.invalid/v1",
        },
        {"model_name": "codex-mini"},
    )

    assert result["text"] == "codex live result"
    assert result["usage"] == {"tokens_in": 9, "tokens_out": 4}
    assert result["approval_gate_id"] == gate.id
    assert result["approval_status"] == "approved"


def test_codex_model_discovery_and_sync_soft_deletes(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.http_client.httpx.Client", _FakeClient)

    db_path = tmp_path / "whiteroom.db"

    def _connect():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(models_catalog, "connect", _connect)

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

        discovered = CodexLBAdapter(
            mode="api_preview",
            base_url="https://example.invalid/v1",
            api_key="sk-test",
            live_enabled=True,
        ).list_models()
        assert [row["model_name"] for row in discovered] == ["codex-mini", "codex-pro"]

        synced = models_catalog.sync_endpoint_models(endpoint_id, discovered)
        assert [row["model_name"] for row in synced] == ["codex-mini", "codex-pro"]

        rows = conn.execute(
            """
            SELECT model_name, context_window, active
            FROM provider_models
            WHERE endpoint_id = ?
            ORDER BY model_name ASC
            """,
            (endpoint_id,),
        ).fetchall()
        assert [(row["model_name"], row["context_window"], row["active"]) for row in rows] == [
            ("codex-mini", 8192, 1),
            ("codex-pro", 16384, 1),
        ]

        sync_again = models_catalog.sync_endpoint_models(endpoint_id, [discovered[0]])
        assert [row["model_name"] for row in sync_again] == ["codex-mini"]

        rows_after = conn.execute(
            """
            SELECT model_name, active
            FROM provider_models
            WHERE endpoint_id = ?
            ORDER BY model_name ASC
            """,
            (endpoint_id,),
        ).fetchall()
        assert [(row["model_name"], row["active"]) for row in rows_after] == [
            ("codex-mini", 1),
            ("codex-pro", 0),
        ]

        sync_row = conn.execute(
            "SELECT last_model_sync FROM endpoints WHERE id = ?",
            (endpoint_id,),
        ).fetchone()
        assert sync_row is not None
        assert sync_row["last_model_sync"]
    finally:
        conn.close()
