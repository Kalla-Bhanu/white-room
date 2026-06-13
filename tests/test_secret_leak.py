from __future__ import annotations

import io
import logging
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import core.secrets as secrets
import web.server as server


DB_PATH = Path("data/whiteroom.db")
PACKET_DIR = Path("projects/white-room/packets")
FIXTURE_DIR = Path("bench/fixtures")
DOCS_PATH = Path("docs/PROVIDERS.md")


def _point_secret_store(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(secrets, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(secrets, "SECRETS_PATH", tmp_path / "secrets.local.json")
    secrets.reload()


def _read_text_surfaces() -> list[str]:
    surfaces: list[str] = []
    for path in PACKET_DIR.glob("*.md"):
        surfaces.append(path.read_text(encoding="utf-8"))
    if DOCS_PATH.exists():
        surfaces.append(DOCS_PATH.read_text(encoding="utf-8"))
    if FIXTURE_DIR.exists():
        for path in FIXTURE_DIR.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".md", ".txt", ".json", ".yaml", ".yml"}:
                surfaces.append(path.read_text(encoding="utf-8"))
    if DB_PATH.exists():
        with sqlite3.connect(DB_PATH) as connection:
            surfaces.append("\n".join(connection.iterdump()))
    return surfaces


def test_provider_docs_cover_operator_rules() -> None:
    assert DOCS_PATH.exists()
    docs = DOCS_PATH.read_text(encoding="utf-8")

    assert "secrets.local.json" in docs
    assert ".env" in docs
    assert "no double `/v1`" in docs
    assert "approval gates" in docs
    assert "trusted-session grants" in docs
    assert "cooldown" in docs
    assert "est-not-bill" in docs
    assert "key removal" in docs


def test_secret_leak_sweep_across_html_logs_packets_db_and_fixtures(monkeypatch, tmp_path) -> None:
    _point_secret_store(monkeypatch, tmp_path)
    monkeypatch.delenv("WR_SECRET_LEAK_TEST", raising=False)
    secrets.set_secret("WR_SECRET_LEAK_TEST", "gsk-test-leak-value")

    client = TestClient(server.app)
    settings_response = client.get("/settings")
    chat_response = client.get("/chat/white-room")
    assert settings_response.status_code == 200
    assert chat_response.status_code == 200

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(secrets.SecretRedactionFilter())
    logger = logging.getLogger("white-room.secret-leak")
    logger.handlers = []
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info("provider secret=%s header=%s", "gsk-test-leak-value", "Bearer gsk-test-leak-value")
    handler.flush()

    surfaces = [
        settings_response.text,
        chat_response.text,
        stream.getvalue(),
        *_read_text_surfaces(),
    ]
    joined = "\n".join(surfaces)

    assert "gsk-test-leak-value" not in joined
    assert "Bearer gsk-test-leak-value" not in joined
    assert "***redacted***" in joined
