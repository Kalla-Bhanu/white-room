from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # pragma: no cover - optional dependency fallback
    from dotenv import dotenv_values
except Exception:  # pragma: no cover - fallback parser
    dotenv_values = None  # type: ignore[assignment]


APP_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = APP_ROOT / ".env"
SECRETS_PATH = APP_ROOT / "secrets.local.json"
ENC_SECRETS_PATH = APP_ROOT / "secrets.enc"
DEFAULT_REDACTION = "***redacted***"

_SECRET_NAME_HINTS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "BEARER")
_TOKEN_PATTERNS = [
    re.compile(r"\b(?:sk|xai|pk|rk|tok|secret|key)-[A-Za-z0-9_-]{6,}\b", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b"),
]
_FILTER_INSTALLED = False
_LOCK = threading.RLock()


@dataclass(frozen=True)
class _SecretCache:
    env_mtime: float | None
    secrets_mtime: float | None
    env_values: dict[str, str]
    secrets_values: dict[str, str]


_CACHE = _SecretCache(env_mtime=None, secrets_mtime=None, env_values={}, secrets_values={})


def _read_json_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in payload.items():
        if isinstance(key, str) and isinstance(value, str):
            result[key] = value
    return result


def _read_dotenv_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    if dotenv_values is not None:
        values = dotenv_values(str(path))
        return {str(key): str(value) for key, value in values.items() if key and value is not None}
    parsed: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        name = name.strip()
        value = raw_value.strip().strip("'\"")
        if name:
            parsed[name] = value
    return parsed


def _stat_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _refresh_cache() -> _SecretCache:
    env_mtime = _stat_mtime(ENV_PATH)
    secrets_mtime = _stat_mtime(SECRETS_PATH)
    env_values = _read_dotenv_file(ENV_PATH)
    secrets_values = _read_json_file(SECRETS_PATH)
    return _SecretCache(
        env_mtime=env_mtime,
        secrets_mtime=secrets_mtime,
        env_values=env_values,
        secrets_values=secrets_values,
    )


def _ensure_cache() -> _SecretCache:
    global _CACHE
    current_env_mtime = _stat_mtime(ENV_PATH)
    current_secrets_mtime = _stat_mtime(SECRETS_PATH)
    if (
        _CACHE.env_mtime != current_env_mtime
        or _CACHE.secrets_mtime != current_secrets_mtime
    ):
        with _LOCK:
            if (
                _CACHE.env_mtime != current_env_mtime
                or _CACHE.secrets_mtime != current_secrets_mtime
            ):
                _CACHE = _refresh_cache()
    return _CACHE


def reload() -> None:
    global _CACHE
    with _LOCK:
        _CACHE = _refresh_cache()


def get_secret(key: str, default: str | None = None) -> str | None:
    if not key:
        return default
    env_value = os.environ.get(key, "").strip()
    if env_value:
        return env_value
    cache = _ensure_cache()
    dotenv_value = cache.env_values.get(key, "").strip()
    if dotenv_value:
        return dotenv_value
    file_value = cache.secrets_values.get(key, "").strip()
    if file_value:
        return file_value
    return default


def set_secret(key: str, value: str) -> None:
    if not key:
        raise ValueError("key is required")
    with _LOCK:
        secrets = _read_json_file(SECRETS_PATH)
        secrets[key] = value
        _write_secret_file(SECRETS_PATH, secrets)
        reload()


def delete_secret(key: str) -> None:
    if not key:
        return
    with _LOCK:
        secrets = _read_json_file(SECRETS_PATH)
        if key in secrets:
            secrets.pop(key, None)
            _write_secret_file(SECRETS_PATH, secrets)
        reload()


def _write_secret_file(path: Path, payload: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _known_secret_values() -> list[str]:
    cache = _ensure_cache()
    values: list[str] = []
    for name, value in os.environ.items():
        if _looks_secret_name(name) and value.strip():
            values.append(value.strip())
    for name, value in cache.env_values.items():
        if _looks_secret_name(name) and value.strip():
            values.append(value.strip())
    values.extend(value.strip() for value in cache.secrets_values.values() if value.strip())
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _looks_secret_name(name: str) -> bool:
    upper = name.upper()
    return any(hint in upper for hint in _SECRET_NAME_HINTS)


def redact(text: str | None) -> str | None:
    if text is None:
        return None
    redacted = str(text)
    for secret_value in sorted(_known_secret_values(), key=len, reverse=True):
        if secret_value:
            redacted = redacted.replace(secret_value, DEFAULT_REDACTION)
    for pattern in _TOKEN_PATTERNS:
        redacted = pattern.sub(DEFAULT_REDACTION, redacted)
    return redacted


def key_fingerprint(secret_value: str | None) -> str:
    if not secret_value:
        return "missing"
    digest = hashlib.sha256(secret_value.encode("utf-8")).hexdigest()
    return f"fp_{digest[:8]}"


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact(message) or DEFAULT_REDACTION
        record.msg = redacted
        record.args = ()
        return True


def install_redaction_filter(logger: logging.Logger | None = None) -> None:
    global _FILTER_INSTALLED
    target = logger or logging.getLogger()
    if _FILTER_INSTALLED:
        return
    target.addFilter(SecretRedactionFilter())
    _FILTER_INSTALLED = True


install_redaction_filter()
