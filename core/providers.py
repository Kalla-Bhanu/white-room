from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

from core.secrets import get_secret

PROVIDER_PROFILE_SEEDS: list[dict[str, object]] = [
    {
        "name": "Manual Claude",
        "endpoint_class": "manual_claude",
        "compatibility_style": "manual",
        "base_url": None,
        "model_name": "manual",
        "context_window": None,
        "supports_streaming": 0,
        "supports_tools": 0,
        "supports_json": 0,
        "input_cost_per_1m": None,
        "output_cost_per_1m": None,
        "rate_limit_notes": "Manual paste/import only.",
        "capabilities": ["planning", "review", "handoff"],
        "required_env_vars": [],
        "live_calls_allowed": 0,
        "default_role": "manual",
        "disabled_reason": None,
    },
    {
        "name": "Codex",
        "endpoint_class": "codex_lb",
        "compatibility_style": "openai",
        "base_url": None,
        "model_name": None,
        "integration_modes": ["manual_execution", "api_preview", "cli_preview"],
        "default_integration_mode": "manual_execution",
        "context_window": None,
        "supports_streaming": 0,
        "supports_tools": 0,
        "supports_json": 0,
        "input_cost_per_1m": None,
        "output_cost_per_1m": None,
        "rate_limit_notes": "Manual trigger only until later approval-gated modes are added.",
        "capabilities": ["execution", "hard-debugging"],
        "required_env_vars": [],
        "live_calls_allowed": 0,
        "default_role": "execution",
        "disabled_reason": None,
    },
    {
        "name": "Ollama Local",
        "endpoint_class": "ollama_local",
        "compatibility_style": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "model_name": None,
        "context_window": None,
        "supports_streaming": 1,
        "supports_tools": 0,
        "supports_json": 0,
        "input_cost_per_1m": 0.0,
        "output_cost_per_1m": 0.0,
        "rate_limit_notes": "Localhost only.",
        "capabilities": ["draft", "summarization", "extraction"],
        "required_env_vars": [],
        "live_calls_allowed": 1,
        "default_role": "draft",
        "disabled_reason": None,
    },
    {
        "name": "LM Studio Local",
        "endpoint_class": "lmstudio_local",
        "compatibility_style": "lmstudio",
        "base_url": "http://127.0.0.1:1234/v1",
        "model_name": None,
        "context_window": None,
        "supports_streaming": 1,
        "supports_tools": 0,
        "supports_json": 0,
        "input_cost_per_1m": 0.0,
        "output_cost_per_1m": 0.0,
        "rate_limit_notes": "Localhost only.",
        "capabilities": ["draft", "summarization", "extraction"],
        "required_env_vars": [],
        "live_calls_allowed": 1,
        "default_role": "draft",
        "disabled_reason": None,
    },
    {
        "name": "OpenAI Compatible Cloud",
        "endpoint_class": "openai_compatible_cloud",
        "compatibility_style": "openai",
        "base_url": None,
        "model_name": None,
        "context_window": None,
        "supports_streaming": 0,
        "supports_tools": 0,
        "supports_json": 0,
        "input_cost_per_1m": None,
        "output_cost_per_1m": None,
        "rate_limit_notes": "Approval-gated cloud overflow.",
        "capabilities": ["overflow", "draft"],
        "required_env_vars": ["OPENAI_COMPAT_API_KEY"],
        "live_calls_allowed": 0,
        "default_role": "draft",
        "disabled_reason": None,
    },
    {
        "name": "Anthropic Compatible Cloud",
        "endpoint_class": "anthropic_compatible_cloud",
        "compatibility_style": "anthropic",
        "base_url": None,
        "model_name": None,
        "context_window": None,
        "supports_streaming": 0,
        "supports_tools": 0,
        "supports_json": 0,
        "input_cost_per_1m": None,
        "output_cost_per_1m": None,
        "rate_limit_notes": "Approval-gated planning/review lane.",
        "capabilities": ["planning", "review"],
        "required_env_vars": ["ANTHROPIC_API_KEY"],
        "live_calls_allowed": 0,
        "default_role": "planning",
        "disabled_reason": None,
    },
    {
        "name": "Provider Specific Cloud",
        "endpoint_class": "provider_specific_cloud",
        "compatibility_style": "custom",
        "base_url": None,
        "model_name": None,
        "context_window": None,
        "supports_streaming": 0,
        "supports_tools": 0,
        "supports_json": 0,
        "input_cost_per_1m": None,
        "output_cost_per_1m": None,
        "rate_limit_notes": "Approval-gated provider-specific overflow.",
        "capabilities": ["overflow", "draft"],
        "required_env_vars": [],
        "live_calls_allowed": 0,
        "default_role": "draft",
        "disabled_reason": None,
    },
    {
        "name": "Gemini Compatible Cloud",
        "endpoint_class": "gemini_compatible_cloud",
        "compatibility_style": "gemini",
        "base_url": None,
        "model_name": "gemini-2.0-flash",
        "context_window": 1048576,
        "supports_streaming": 0,
        "supports_tools": 0,
        "supports_json": 0,
        "input_cost_per_1m": None,
        "output_cost_per_1m": None,
        "rate_limit_notes": "Approval-gated cloud metadata lane.",
        "capabilities": ["planning", "analysis"],
        "required_env_vars": ["GEMINI_API_KEY"],
        "live_calls_allowed": 0,
        "default_role": "planning",
        "disabled_reason": None,
    },
    {
        "name": "DeepSeek Compatible Cloud",
        "endpoint_class": "deepseek_compatible_cloud",
        "compatibility_style": "deepseek",
        "base_url": None,
        "model_name": "deepseek-chat",
        "context_window": 128000,
        "supports_streaming": 0,
        "supports_tools": 0,
        "supports_json": 0,
        "input_cost_per_1m": None,
        "output_cost_per_1m": None,
        "rate_limit_notes": "Approval-gated cloud metadata lane.",
        "capabilities": ["draft", "analysis"],
        "required_env_vars": ["DEEPSEEK_API_KEY"],
        "live_calls_allowed": 0,
        "default_role": "draft",
        "disabled_reason": None,
    },
    {
        "name": "OpenRouter Cloud",
        "endpoint_class": "openrouter_cloud",
        "compatibility_style": "openrouter",
        "base_url": None,
        "model_name": "openrouter/default",
        "context_window": 128000,
        "supports_streaming": 0,
        "supports_tools": 0,
        "supports_json": 0,
        "input_cost_per_1m": None,
        "output_cost_per_1m": None,
        "rate_limit_notes": "Approval-gated routing metadata lane.",
        "capabilities": ["overflow", "routing"],
        "required_env_vars": ["OPENROUTER_API_KEY"],
        "live_calls_allowed": 0,
        "default_role": "draft",
        "disabled_reason": None,
    },
    {
        "name": "Groq Cloud",
        "endpoint_class": "groq_cloud",
        "compatibility_style": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "model_name": "llama-3.1-8b-instant",
        "context_window": 131072,
        "supports_streaming": 0,
        "supports_tools": 0,
        "supports_json": 0,
        "input_cost_per_1m": None,
        "output_cost_per_1m": None,
        "rate_limit_notes": "Approval-gated latency lane.",
        "capabilities": ["draft", "summarization"],
        "required_env_vars": ["GROQ_API_KEY"],
        "live_calls_allowed": 0,
        "default_role": "draft",
        "disabled_reason": None,
    },
    {
        "name": "OpenCode Compatible Cloud",
        "endpoint_class": "opencode_compatible_cloud",
        "compatibility_style": "opencode",
        "base_url": None,
        "model_name": "opencode-default",
        "context_window": 128000,
        "supports_streaming": 0,
        "supports_tools": 0,
        "supports_json": 0,
        "input_cost_per_1m": None,
        "output_cost_per_1m": None,
        "rate_limit_notes": "Approval-gated coding lane.",
        "capabilities": ["execution", "patching"],
        "required_env_vars": ["OPENCODE_API_KEY"],
        "live_calls_allowed": 0,
        "default_role": "execution",
        "disabled_reason": None,
    },
]


def ensure_provider_profiles_migration(connection: sqlite3.Connection) -> None:
    _ensure_endpoint_columns(connection)
    _ensure_provider_profile_mode_columns(connection)
    _seed_provider_profiles(connection)
    _backfill_endpoint_profiles(connection)
    _ensure_endpoint_runtime_rows(connection)


def profile_names() -> tuple[str, ...]:
    return tuple(str(profile["name"]) for profile in PROVIDER_PROFILE_SEEDS)


def provider_lane_options() -> list[dict[str, object]]:
    options: list[dict[str, object]] = [
        {"value": "auto", "label": "Auto", "group": "Workspace", "disabled": False, "reason": "router decides"},
        {"value": "ollama_local", "label": "Ollama Local", "group": "Local", "disabled": False, "reason": "localhost only"},
        {"value": "lmstudio_local", "label": "LM Studio Local", "group": "Local", "disabled": False, "reason": "localhost only"},
        {"value": "codex_lb", "label": "Codex LB", "group": "Manual", "disabled": False, "reason": "manual packet lane"},
        {"value": "manual_claude", "label": "Manual Claude", "group": "Manual", "disabled": False, "reason": "export/import only"},
    ]

    groq_key_present = bool(os.getenv("GROQ_API_KEY", "").strip() or str(get_secret("GROQ_API_KEY") or "").strip())
    if groq_key_present:
        options.append(
            {
                "value": "groq_cloud",
                "label": "Groq Cloud",
                "group": "Cloud",
                "disabled": False,
                "reason": "approval-gated cloud lane",
            }
        )

    base_values = {"auto", "ollama_local", "lmstudio_local", "codex_lb", "manual_claude"}
    if groq_key_present:
        base_values.add("groq_cloud")
    future_profiles = [profile for profile in PROVIDER_PROFILE_SEEDS if str(profile["endpoint_class"]) not in base_values]

    for profile in future_profiles:
        required_env_vars = [str(name) for name in profile.get("required_env_vars", [])]
        key_present = all(
            bool(os.getenv(name, "").strip() or str(get_secret(name) or "").strip())
            for name in required_env_vars
        ) if required_env_vars else False
        if required_env_vars:
            disabled = not key_present
            reason = "connect key in Settings" if disabled else "approval-gated later"
            label_suffix = "needs key in Settings" if disabled else "enabled"
        else:
            disabled = True
            reason = "configure provider in Settings"
            label_suffix = "configure in Settings"
        options.append(
            {
                "value": str(profile["endpoint_class"]),
                "label": f"{profile['name']} - {label_suffix}",
                "group": "More lanes",
                "disabled": disabled,
                "reason": reason,
            }
        )
    return options


def _ensure_endpoint_columns(connection: sqlite3.Connection) -> None:
    columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(endpoints)").fetchall()}
    additions: list[tuple[str, str]] = [
        ("profile_id", "INTEGER"),
        ("model_name", "TEXT"),
        ("supports_streaming", "INTEGER"),
        ("supports_tools", "INTEGER"),
        ("supports_json", "INTEGER"),
        ("input_cost_per_1m", "REAL"),
        ("output_cost_per_1m", "REAL"),
        ("rate_limit_notes", "TEXT"),
        ("disabled_reason", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
        ("last_model_sync", "TEXT"),
    ]
    for name, declaration in additions:
        if name not in columns:
            connection.execute(f"ALTER TABLE endpoints ADD COLUMN {name} {declaration}")


def _ensure_provider_profile_mode_columns(connection: sqlite3.Connection) -> None:
    columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(provider_profiles)").fetchall()}
    if "integration_modes" not in columns:
        connection.execute("ALTER TABLE provider_profiles ADD COLUMN integration_modes TEXT NOT NULL DEFAULT '[]'")
    if "default_integration_mode" not in columns:
        connection.execute(
            "ALTER TABLE provider_profiles ADD COLUMN default_integration_mode TEXT NOT NULL DEFAULT 'manual_execution'"
        )


def _seed_provider_profiles(connection: sqlite3.Connection) -> None:
    existing = {
        str(row["name"])
        for row in connection.execute("SELECT name FROM provider_profiles").fetchall()
    }
    created_at = utc_now()
    for profile in PROVIDER_PROFILE_SEEDS:
        if str(profile["name"]) in existing:
            continue
        connection.execute(
            """
            INSERT INTO provider_profiles (
                name, endpoint_class, compatibility_style, base_url, model_name, integration_modes,
                default_integration_mode, context_window, supports_streaming, supports_tools,
                supports_json, input_cost_per_1m, output_cost_per_1m, rate_limit_notes,
                capabilities, required_env_vars, live_calls_allowed, default_role,
                disabled_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile["name"],
                profile["endpoint_class"],
                profile["compatibility_style"],
                profile["base_url"],
                profile["model_name"],
                json.dumps(profile.get("integration_modes", [])),
                profile.get("default_integration_mode", "manual_execution"),
                profile["context_window"],
                int(profile["supports_streaming"]),
                int(profile["supports_tools"]),
                int(profile["supports_json"]),
                profile["input_cost_per_1m"],
                profile["output_cost_per_1m"],
                profile["rate_limit_notes"],
                json.dumps(profile["capabilities"]),
                json.dumps(profile["required_env_vars"]),
                int(profile["live_calls_allowed"]),
                profile["default_role"],
                profile["disabled_reason"],
                created_at,
                created_at,
            ),
        )

    codex_modes = json.dumps(["manual_execution", "api_preview", "cli_preview"])
    connection.execute(
        """
        UPDATE provider_profiles
        SET integration_modes = COALESCE(NULLIF(integration_modes, '[]'), ?),
            default_integration_mode = COALESCE(NULLIF(default_integration_mode, ''), 'manual_execution')
        WHERE endpoint_class = 'codex_lb'
        """,
        (codex_modes,),
    )


def _backfill_endpoint_profiles(connection: sqlite3.Connection) -> None:
    profile_rows = connection.execute(
        "SELECT id, endpoint_class FROM provider_profiles"
    ).fetchall()
    profile_ids = {str(row["endpoint_class"]): int(row["id"]) for row in profile_rows}
    if not profile_ids:
        return

    created_at = utc_now()
    for row in connection.execute(
        "SELECT id, endpoint_class, profile_id, created_at, updated_at FROM endpoints"
    ).fetchall():
        profile_id = profile_ids.get(str(row["endpoint_class"]))
        connection.execute(
            """
            UPDATE endpoints
            SET
                profile_id = COALESCE(profile_id, ?),
                created_at = COALESCE(created_at, ?),
                updated_at = COALESCE(updated_at, ?)
            WHERE id = ?
            """,
            (profile_id, created_at, created_at, int(row["id"])),
        )


def _ensure_endpoint_runtime_rows(connection: sqlite3.Connection) -> None:
    columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(endpoint_runtime)").fetchall()}
    if not columns:
        return

    existing = {
        int(row["endpoint_id"])
        for row in connection.execute("SELECT endpoint_id FROM endpoint_runtime").fetchall()
    }
    endpoints = connection.execute(
        "SELECT id FROM endpoints ORDER BY id ASC"
    ).fetchall()
    created_at = utc_now()
    for row in endpoints:
        endpoint_id = int(row["id"])
        if endpoint_id in existing:
            continue
        connection.execute(
            """
            INSERT INTO endpoint_runtime (
                endpoint_id, failure_count, cooldown_until, last_rate_limited_at, window_used,
                window_reset_at, last_success_at, updated_at
            ) VALUES (?, 0, NULL, NULL, 0, NULL, NULL, ?)
            """,
            (endpoint_id, created_at),
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


