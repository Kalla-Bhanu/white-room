from __future__ import annotations

import io
import json
import logging

import core.secrets as secrets


def _point_secret_store(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    secrets_path = tmp_path / "secrets.local.json"
    monkeypatch.setattr(secrets, "ENV_PATH", env_path)
    monkeypatch.setattr(secrets, "SECRETS_PATH", secrets_path)
    secrets.reload()
    return env_path, secrets_path


def test_secret_accessor_precedence_env_then_dotenv_then_file(monkeypatch, tmp_path):
    env_path, secrets_path = _point_secret_store(monkeypatch, tmp_path)
    env_path.write_text("WR_TEST_API_KEY=dotenv-secret\n", encoding="utf-8")
    secrets_path.write_text(json.dumps({"WR_TEST_API_KEY": "file-secret"}), encoding="utf-8")
    monkeypatch.setenv("WR_TEST_API_KEY", "env-secret")
    secrets.reload()

    assert secrets.get_secret("WR_TEST_API_KEY") == "env-secret"

    monkeypatch.delenv("WR_TEST_API_KEY", raising=False)
    secrets.reload()
    assert secrets.get_secret("WR_TEST_API_KEY") == "dotenv-secret"

    env_path.unlink()
    secrets.reload()
    assert secrets.get_secret("WR_TEST_API_KEY") == "file-secret"


def test_secret_write_reload_and_read(monkeypatch, tmp_path):
    _point_secret_store(monkeypatch, tmp_path)
    monkeypatch.delenv("WR_TEST_API_KEY", raising=False)

    secrets.set_secret("WR_TEST_API_KEY", "persisted-secret")
    assert secrets.get_secret("WR_TEST_API_KEY") == "persisted-secret"

    payload = json.loads(secrets.SECRETS_PATH.read_text(encoding="utf-8"))
    assert payload["WR_TEST_API_KEY"] == "persisted-secret"

    secrets.SECRETS_PATH.write_text(json.dumps({"WR_TEST_API_KEY": "updated-secret"}), encoding="utf-8")
    secrets.reload()
    assert secrets.get_secret("WR_TEST_API_KEY") == "updated-secret"


def test_redaction_and_fingerprint_never_expose_raw_secret(monkeypatch, tmp_path):
    _point_secret_store(monkeypatch, tmp_path)
    monkeypatch.delenv("WR_TEST_API_KEY", raising=False)
    secrets.set_secret("WR_TEST_API_KEY", "super-secret-value")

    sample = "Bearer super-secret-value and sk-test-abc123 and xai-live-token"
    redacted = secrets.redact(sample)
    assert redacted is not None
    assert "super-secret-value" not in redacted
    assert "sk-test-abc123" not in redacted
    assert "xai-live-token" not in redacted
    assert "***redacted***" in redacted

    fingerprint = secrets.key_fingerprint("super-secret-value")
    assert fingerprint.startswith("fp_")
    assert fingerprint != "super-secret-value"


def test_redaction_filter_masks_log_output(monkeypatch, tmp_path):
    _point_secret_store(monkeypatch, tmp_path)
    monkeypatch.delenv("WR_TEST_API_KEY", raising=False)
    secrets.set_secret("WR_TEST_API_KEY", "log-secret-value")

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(secrets.SecretRedactionFilter())
    logger = logging.getLogger("white-room.secrets.test")
    logger.handlers = []
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.info("payload %s / %s", "log-secret-value", "Bearer log-secret-value")
    handler.flush()

    output = stream.getvalue()
    assert "log-secret-value" not in output
    assert "Bearer" not in output or "***redacted***" in output
    assert "***redacted***" in output
