from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import core.secrets as secrets
import web.server as server


DB_PATH = Path("data/whiteroom.db")
CODEX_SECRET_NAME = "CODEX_LB_API_KEY"
GROQ_SECRET_NAME = "GROQ_API_KEY"


def test_settings_codex_key_save_remove_and_render_are_presence_only(monkeypatch, tmp_path) -> None:
    _point_secret_store(monkeypatch, tmp_path)
    monkeypatch.delenv(CODEX_SECRET_NAME, raising=False)
    client = TestClient(server.app)
    original = _snapshot_codex_state()
    secret_value = "fake-openai-secret-value"
    base_url = "https://codex.example.com/v1"

    try:
        response = client.post(
            "/settings/providers/codex-lb",
            data={"action": "save", "base_url": base_url, "api_key": secret_value},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert secrets.get_secret(CODEX_SECRET_NAME) == secret_value
        stored = json.loads(secrets.SECRETS_PATH.read_text(encoding="utf-8"))
        assert stored[CODEX_SECRET_NAME] == secret_value

        page = client.get("/settings")
        assert page.status_code == 200
        assert "key active" in page.text
        assert secret_value not in page.text
        assert secrets.key_fingerprint(secret_value) in page.text
        assert "https://codex.example.com/v1/models" in page.text

        bad_response = client.post(
            "/settings/providers/codex-lb",
            data={"action": "save", "base_url": "https://kbloadbalancer.198.199.88.10.sslip.io/dashboard", "api_key": secret_value},
            follow_redirects=False,
        )
        assert bad_response.status_code == 303
        assert "Codex%20LB%20settings%20not%20saved" in bad_response.headers["location"]
        assert "dashboard%20URL" in bad_response.headers["location"]

        settings_state = server._codex_lb_settings_state()
        assert settings_state["base_url"] == base_url
        assert settings_state["models_probe_url"] == "https://codex.example.com/v1/models"
        assert settings_state["key_present"] is True
        assert settings_state["live_calls_allowed"] is True
        grant_state = server._active_approval_grant_for_project("white-room", "codex_lb")
        assert grant_state is not None
        assert "ask" in grant_state["modes"]

        remove_response = client.post(
            "/settings/providers/codex-lb",
            data={"action": "remove_key", "base_url": base_url},
            follow_redirects=False,
        )
        assert remove_response.status_code == 303
        assert secrets.get_secret(CODEX_SECRET_NAME) in (None, "")
        stored_after = json.loads(secrets.SECRETS_PATH.read_text(encoding="utf-8"))
        assert CODEX_SECRET_NAME not in stored_after

        removed_page = client.get("/settings")
        assert removed_page.status_code == 200
        assert secret_value not in removed_page.text
        assert "key missing" in removed_page.text
        removed_state = server._codex_lb_settings_state()
        assert removed_state["key_present"] is False
        assert removed_state["live_calls_allowed"] is False
        removed_grant = server._active_approval_grant_for_project("white-room", "codex_lb")
        assert removed_grant is None
    finally:
        _restore_codex_state(original)


def test_settings_test_connection_and_sync_models_use_local_routes(monkeypatch, tmp_path) -> None:
    _point_secret_store(monkeypatch, tmp_path)
    monkeypatch.delenv(CODEX_SECRET_NAME, raising=False)
    client = TestClient(server.app)
    original = _snapshot_codex_state()
    secret_value = "sk-test-live-value"
    base_url = "https://codex.example.com/v1"

    monkeypatch.setattr(
        server,
        "health_check",
        lambda endpoint: SimpleNamespace(
            endpoint_class="codex_lb",
            reachable=True,
            key_present=True,
            result="reachable",
            detail="codex_lb reachable at https://codex.example.com/v1",
            last_checked="2026-06-13T00:00:00+00:00",
        ),
    )
    monkeypatch.setattr(
        server,
        "sync_models",
        lambda endpoint: SimpleNamespace(
            endpoint_class="codex_lb",
            models_synced=2,
            last_model_sync="2026-06-13T00:05:00+00:00",
        ),
    )

    try:
        save_response = client.post(
            "/settings/providers/codex-lb",
            data={"action": "save", "base_url": base_url, "api_key": secret_value},
            follow_redirects=False,
        )
        assert save_response.status_code == 303

        test_response = client.post(
            "/settings/providers/codex-lb",
            data={"action": "test_connection", "base_url": base_url},
            follow_redirects=False,
        )
        assert test_response.status_code == 303
        assert "status=reachable" in test_response.headers["location"]
        assert "Codex%20LB%20test%20complete" in test_response.headers["location"]

        sync_response = client.post(
            "/settings/providers/codex-lb",
            data={"action": "sync_models", "base_url": base_url},
            follow_redirects=False,
        )
        assert sync_response.status_code == 303
        assert "status=synced" in sync_response.headers["location"]
        assert "Codex%20LB%20models%20synced" in sync_response.headers["location"]

        settings_state = server._codex_lb_settings_state()
        assert settings_state["models_probe_url"] == "https://codex.example.com/v1/models"
        assert settings_state["key_present"] is True
        assert settings_state["live_calls_allowed"] is True
        grant_state = server._active_approval_grant_for_project("white-room", "codex_lb")
        assert grant_state is not None
        assert "ask" in grant_state["modes"]
    finally:
        _restore_codex_state(original)


def test_settings_groq_key_save_remove_and_render_are_presence_only(monkeypatch, tmp_path) -> None:
    _point_secret_store(monkeypatch, tmp_path)
    monkeypatch.delenv(GROQ_SECRET_NAME, raising=False)
    client = TestClient(server.app)
    secret_value = "fake-groq-secret-value"
    base_url = "https://api.groq.com/openai/v1"

    response = client.post(
        "/settings/providers/groq-cloud",
        data={"action": "save", "base_url": base_url, "api_key": secret_value},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert secrets.get_secret(GROQ_SECRET_NAME) == secret_value
    stored = json.loads(secrets.SECRETS_PATH.read_text(encoding="utf-8"))
    assert stored[GROQ_SECRET_NAME] == secret_value

    page = client.get("/settings")
    assert page.status_code == 200
    assert "key active" in page.text
    assert secret_value not in page.text
    assert secrets.key_fingerprint(secret_value) in page.text
    assert "https://api.groq.com/openai/v1" in page.text

    bad_response = client.post(
        "/settings/providers/groq-cloud",
        data={"action": "save", "base_url": "https://console.groq.com/dashboard/metrics", "api_key": secret_value},
        follow_redirects=False,
    )
    assert bad_response.status_code == 303
    assert "Groq%20Cloud%20settings%20not%20saved" in bad_response.headers["location"]
    assert "Groq%20Console%20URL" in bad_response.headers["location"]

    settings_state = server._groq_cloud_settings_state()
    assert settings_state["base_url"] == base_url
    assert settings_state["models_probe_url"] == "https://api.groq.com/openai/v1/models"
    assert settings_state["key_present"] is True
    assert settings_state["live_calls_allowed"] is True
    grant_state = server._active_approval_grant_for_project("white-room", "groq_cloud")
    assert grant_state is not None
    assert "ask" in grant_state["modes"]

    remove_response = client.post(
        "/settings/providers/groq-cloud",
        data={"action": "remove_key", "base_url": base_url},
        follow_redirects=False,
    )
    assert remove_response.status_code == 303
    assert secrets.get_secret(GROQ_SECRET_NAME) in (None, "")
    stored_after = json.loads(secrets.SECRETS_PATH.read_text(encoding="utf-8"))
    assert GROQ_SECRET_NAME not in stored_after

    removed_page = client.get("/settings")
    assert removed_page.status_code == 200
    assert secret_value not in removed_page.text
    assert "key missing" in removed_page.text
    removed_state = server._groq_cloud_settings_state()
    assert removed_state["key_present"] is False
    assert removed_state["live_calls_allowed"] is False
    removed_grant = server._active_approval_grant_for_project("white-room", "groq_cloud")
    assert removed_grant is None


def test_settings_groq_test_connection_and_sync_models_use_local_routes(monkeypatch, tmp_path) -> None:
    _point_secret_store(monkeypatch, tmp_path)
    monkeypatch.delenv(GROQ_SECRET_NAME, raising=False)
    client = TestClient(server.app)
    secret_value = "gsk-test-live-value"
    base_url = "https://api.groq.com/openai/v1"

    monkeypatch.setattr(
        server,
        "health_check",
        lambda endpoint: SimpleNamespace(
            endpoint_class="groq_cloud",
            reachable=True,
            key_present=True,
            result="reachable",
            detail="groq_cloud reachable at https://api.groq.com/openai/v1",
            last_checked="2026-06-13T00:00:00+00:00",
        ),
    )
    monkeypatch.setattr(
        server,
        "sync_models",
        lambda endpoint: SimpleNamespace(
            endpoint_class="groq_cloud",
            models_synced=2,
            last_model_sync="2026-06-13T00:05:00+00:00",
        ),
    )

    save_response = client.post(
        "/settings/providers/groq-cloud",
        data={"action": "save", "base_url": base_url, "api_key": secret_value},
        follow_redirects=False,
    )
    assert save_response.status_code == 303
    grant_state = server._active_approval_grant_for_project("white-room", "groq_cloud")
    assert grant_state is not None
    assert "ask" in grant_state["modes"]

    test_response = client.post(
        "/settings/providers/groq-cloud",
        data={"action": "test_connection", "base_url": base_url},
        follow_redirects=False,
    )
    assert test_response.status_code == 303
    assert "status=reachable" in test_response.headers["location"]
    assert "Groq%20Cloud%20test%20complete" in test_response.headers["location"]

    sync_response = client.post(
        "/settings/providers/groq-cloud",
        data={"action": "sync_models", "base_url": base_url},
        follow_redirects=False,
    )
    assert sync_response.status_code == 303
    assert "status=synced" in sync_response.headers["location"]
    assert "Groq%20Cloud%20models%20synced" in sync_response.headers["location"]

    settings_state = server._groq_cloud_settings_state()
    assert settings_state["models_probe_url"] == "https://api.groq.com/openai/v1/models"
    assert settings_state["key_present"] is True
    assert settings_state["live_calls_allowed"] is True


def test_settings_codex_dashboard_url_recovers_to_host_root_on_render(tmp_path) -> None:
    original = _snapshot_codex_state()
    bad_url = "https://kbloadbalancer.198.199.88.10.sslip.io/dashboard"
    try:
        _set_codex_base_urls(bad_url, bad_url)
        client = TestClient(server.app)
        page = client.get("/settings")
        assert page.status_code == 200
        settings_state = server._codex_lb_settings_state()
        assert settings_state["base_url"] == "https://kbloadbalancer.198.199.88.10.sslip.io"
        assert settings_state["models_probe_url"] == "https://kbloadbalancer.198.199.88.10.sslip.io/v1/models"
        assert bad_url not in page.text
    finally:
        _restore_codex_state(original)


def test_settings_groq_console_url_recovers_to_api_base_on_render(tmp_path) -> None:
    original = _snapshot_groq_state()
    bad_url = "https://console.groq.com/dashboard/metrics"
    try:
        _set_groq_base_urls(bad_url, bad_url)
        client = TestClient(server.app)
        page = client.get("/settings")
        assert page.status_code == 200
        settings_state = server._groq_cloud_settings_state()
        assert settings_state["base_url"] == "https://api.groq.com/openai/v1"
        assert settings_state["models_probe_url"] == "https://api.groq.com/openai/v1/models"
        assert "console.groq.com/dashboard/metrics" not in page.text
    finally:
        _restore_groq_state(original)


def _point_secret_store(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(secrets, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(secrets, "SECRETS_PATH", tmp_path / "secrets.local.json")
    secrets.reload()


def _snapshot_codex_state() -> dict[str, object]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        endpoint = conn.execute(
            """
            SELECT e.base_url, e.status, p.base_url AS profile_base_url, p.live_calls_allowed
            FROM endpoints AS e
            LEFT JOIN provider_profiles AS p ON p.id = e.profile_id
            WHERE e.endpoint_class = 'codex_lb'
            ORDER BY e.id ASC
            LIMIT 1
            """
        ).fetchone()
    assert endpoint is not None
    return {
        "base_url": endpoint["base_url"],
        "status": endpoint["status"],
        "profile_base_url": endpoint["profile_base_url"],
        "live_calls_allowed": endpoint["live_calls_allowed"],
    }


def _restore_codex_state(original: dict[str, object]) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        endpoint = conn.execute(
            "SELECT name FROM endpoints WHERE endpoint_class = 'codex_lb' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if endpoint is not None:
            conn.execute(
                """
                UPDATE endpoints
                SET base_url = ?, status = ?
                WHERE name = ?
                """,
                (original["base_url"], original["status"], endpoint["name"]),
            )
        conn.execute(
            """
            UPDATE provider_profiles
            SET base_url = ?, live_calls_allowed = ?
            WHERE endpoint_class = 'codex_lb'
            """,
            (original["profile_base_url"], original["live_calls_allowed"]),
        )
        conn.commit()


def _snapshot_groq_state() -> dict[str, object]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        endpoint = conn.execute(
            """
            SELECT e.base_url, e.status, p.base_url AS profile_base_url, p.live_calls_allowed
            FROM endpoints AS e
            LEFT JOIN provider_profiles AS p ON p.id = e.profile_id
            WHERE e.endpoint_class = 'groq_cloud'
            ORDER BY e.id ASC
            LIMIT 1
            """
        ).fetchone()
    assert endpoint is not None
    return {
        "base_url": endpoint["base_url"],
        "status": endpoint["status"],
        "profile_base_url": endpoint["profile_base_url"],
        "live_calls_allowed": endpoint["live_calls_allowed"],
    }


def _restore_groq_state(original: dict[str, object]) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        endpoint = conn.execute(
            "SELECT name FROM endpoints WHERE endpoint_class = 'groq_cloud' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if endpoint is not None:
            conn.execute(
                """
                UPDATE endpoints
                SET base_url = ?, status = ?
                WHERE name = ?
                """,
                (original["base_url"], original["status"], endpoint["name"]),
            )
        conn.execute(
            """
            UPDATE provider_profiles
            SET base_url = ?, live_calls_allowed = ?
            WHERE endpoint_class = 'groq_cloud'
            """,
            (original["profile_base_url"], original["live_calls_allowed"]),
        )
        conn.commit()


def _set_codex_base_urls(endpoint_base_url: str, profile_base_url: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE endpoints SET base_url = ? WHERE endpoint_class = 'codex_lb'",
            (endpoint_base_url,),
        )
        conn.execute(
            "UPDATE provider_profiles SET base_url = ? WHERE endpoint_class = 'codex_lb'",
            (profile_base_url,),
        )
        conn.commit()


def _set_groq_base_urls(endpoint_base_url: str, profile_base_url: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE endpoints SET base_url = ? WHERE endpoint_class = 'groq_cloud'",
            (endpoint_base_url,),
        )
        conn.execute(
            "UPDATE provider_profiles SET base_url = ? WHERE endpoint_class = 'groq_cloud'",
            (profile_base_url,),
        )
        conn.commit()
