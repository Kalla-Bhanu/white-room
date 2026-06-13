from __future__ import annotations

import sqlite3

from core.db import init_db


def test_phase15_migration_creates_provider_tables_and_seeded_profiles(tmp_path) -> None:
    db_path = tmp_path / "whiteroom.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        init_db(conn)

        table_names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {"provider_models", "endpoint_runtime", "approval_grants"}.issubset(table_names)

        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(endpoints)").fetchall()
        }
        assert "last_model_sync" in columns

        groq_row = conn.execute(
            "SELECT endpoint_class, compatibility_style, base_url, required_env_vars FROM provider_profiles WHERE name = ?",
            ("Groq Cloud",),
        ).fetchone()
        assert groq_row is not None
        assert groq_row["endpoint_class"] == "groq_cloud"
        assert groq_row["compatibility_style"] == "openai"
        assert groq_row["base_url"] == "https://api.groq.com/openai/v1"
        assert "GROQ_API_KEY" in groq_row["required_env_vars"]

        conn.execute(
            """
            INSERT INTO endpoints (
                name, endpoint_class, profile_id, base_url, capabilities, tier, daily_limit, window_limit,
                status, model_name, supports_streaming, supports_tools, supports_json,
                input_cost_per_1m, output_cost_per_1m, rate_limit_notes, disabled_reason,
                created_at, updated_at
            ) VALUES (
                'groq-test-endpoint', 'groq_cloud',
                (SELECT id FROM provider_profiles WHERE endpoint_class = 'groq_cloud' LIMIT 1),
                'https://api.groq.com/openai/v1', 'planning,execution', 'cloud', '100', '10',
                'active', NULL, 1, 1, 0, NULL, NULL, 'approval-gated cloud lane', NULL,
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
            )
            """
        )
        conn.commit()

        init_db(conn)

        runtime_row = conn.execute(
            "SELECT failure_count, window_used FROM endpoint_runtime WHERE endpoint_id = (SELECT id FROM endpoints WHERE name = ?)",
            ("groq-test-endpoint",),
        ).fetchone()
        assert runtime_row is not None
        assert runtime_row["failure_count"] == 0
        assert runtime_row["window_used"] == 0

        counts = {
            table: conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
            for table in ("provider_models", "endpoint_runtime", "approval_grants")
        }
        init_db(conn)
        repeated_counts = {
            table: conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
            for table in ("provider_models", "endpoint_runtime", "approval_grants")
        }
        assert counts == repeated_counts
    finally:
        conn.close()
