from __future__ import annotations

import sqlite3
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = APP_ROOT / "data"
DB_PATH = DATA_DIR / "whiteroom.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    one_line_purpose TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS brain_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    checksum TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    UNIQUE (project_id, filename)
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    size_class TEXT NOT NULL,
    preferred_tier TEXT NOT NULL,
    thread TEXT NOT NULL DEFAULT 'orchestrator',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS task_packets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    packet_text TEXT NOT NULL,
    token_estimate INTEGER NOT NULL,
    generated_at TEXT NOT NULL,
    model_route TEXT NOT NULL,
    expected_output TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    task_id INTEGER,
    thread_from TEXT NOT NULL DEFAULT 'orchestrator',
    thread_to TEXT NOT NULL DEFAULT 'orchestrator',
    from_worker TEXT NOT NULL,
    to_worker TEXT NOT NULL,
    summary TEXT NOT NULL,
    artifact_paths TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS agent_threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    thread_name TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'idle',
    current_task_id INTEGER,
    last_handoff_id INTEGER,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (current_task_id) REFERENCES tasks(id),
    FOREIGN KEY (last_handoff_id) REFERENCES handoffs(id),
    UNIQUE (project_id, thread_name)
);

CREATE TABLE IF NOT EXISTS ui_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    endpoint_id INTEGER,
    lane TEXT NOT NULL,
    attached_brain_files TEXT NOT NULL DEFAULT '[]',
    attached_task_id INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id),
    FOREIGN KEY (endpoint_id) REFERENCES endpoints(id),
    FOREIGN KEY (attached_task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    route_decision_id INTEGER,
    citations TEXT NOT NULL DEFAULT '[]',
    token_estimate INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'final',
    endpoint_id INTEGER,
    model_name TEXT,
    error_kind TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id),
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id),
    FOREIGN KEY (route_decision_id) REFERENCES route_decisions(id),
    FOREIGN KEY (endpoint_id) REFERENCES endpoints(id)
);

CREATE TABLE IF NOT EXISTS codex_packets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    conversation_id INTEGER,
    task_id INTEGER,
    mode TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    token_estimate INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id),
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS execution_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    conversation_id INTEGER,
    task_id INTEGER,
    approval_gate_id INTEGER,
    packet_path TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id),
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (approval_gate_id) REFERENCES approval_gates(id)
);

CREATE TABLE IF NOT EXISTS route_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    task_id INTEGER,
    message_id INTEGER,
    task_type TEXT NOT NULL,
    risk TEXT NOT NULL,
    size TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'ask',
    chosen_endpoint_id INTEGER,
    chosen_lane TEXT NOT NULL,
    est_cost_usd REAL,
    candidates TEXT NOT NULL DEFAULT '[]',
    explanation TEXT NOT NULL,
    requires_approval INTEGER NOT NULL DEFAULT 0,
    is_preview INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'dry_run',
    status TEXT NOT NULL DEFAULT 'suggested',
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (message_id) REFERENCES messages(id),
    FOREIGN KEY (chosen_endpoint_id) REFERENCES endpoints(id)
);

CREATE TABLE IF NOT EXISTS endpoint_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id INTEGER NOT NULL,
    reachable INTEGER NOT NULL DEFAULT 0,
    key_present INTEGER NOT NULL DEFAULT 0,
    last_checked TEXT,
    last_error TEXT,
    latency_ms INTEGER,
    FOREIGN KEY (endpoint_id) REFERENCES endpoints(id)
);

CREATE TABLE IF NOT EXISTS availability_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id INTEGER NOT NULL,
    check_type TEXT NOT NULL,
    result TEXT NOT NULL,
    detail TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    FOREIGN KEY (endpoint_id) REFERENCES endpoints(id)
);

CREATE TABLE IF NOT EXISTS approval_gates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    target_endpoint_id INTEGER,
    payload_summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    decided_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (target_endpoint_id) REFERENCES endpoints(id)
);

CREATE TABLE IF NOT EXISTS provider_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    endpoint_class TEXT NOT NULL,
    compatibility_style TEXT NOT NULL,
    base_url TEXT,
    model_name TEXT,
    context_window INTEGER,
    supports_streaming INTEGER NOT NULL DEFAULT 0,
    supports_tools INTEGER NOT NULL DEFAULT 0,
    supports_json INTEGER NOT NULL DEFAULT 0,
    input_cost_per_1m REAL,
    output_cost_per_1m REAL,
    rate_limit_notes TEXT,
    capabilities TEXT NOT NULL DEFAULT '[]',
    required_env_vars TEXT NOT NULL DEFAULT '[]',
    live_calls_allowed INTEGER NOT NULL DEFAULT 0,
    default_role TEXT NOT NULL,
    disabled_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id INTEGER NOT NULL,
    model_name TEXT NOT NULL,
    context_window INTEGER,
    supports_streaming INTEGER,
    supports_tools INTEGER,
    supports_json INTEGER,
    capability_source TEXT NOT NULL DEFAULT 'discovered',
    active INTEGER NOT NULL DEFAULT 1,
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (endpoint_id) REFERENCES endpoints(id),
    UNIQUE (endpoint_id, model_name)
);

CREATE TABLE IF NOT EXISTS endpoint_runtime (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id INTEGER NOT NULL UNIQUE,
    failure_count INTEGER NOT NULL DEFAULT 0,
    cooldown_until TEXT,
    last_rate_limited_at TEXT,
    window_used INTEGER NOT NULL DEFAULT 0,
    window_reset_at TEXT,
    last_success_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (endpoint_id) REFERENCES endpoints(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS approval_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id INTEGER,
    endpoint_class TEXT,
    project_id INTEGER,
    modes TEXT,
    est_cost_ceiling_usd REAL,
    expires_at TEXT,
    turns_remaining INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY (endpoint_id) REFERENCES endpoints(id),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    decision TEXT NOT NULL,
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    task_id INTEGER,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS endpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    endpoint_class TEXT NOT NULL,
    base_url TEXT NOT NULL,
    capabilities TEXT NOT NULL,
    tier TEXT NOT NULL,
    daily_limit TEXT NOT NULL,
    window_limit TEXT NOT NULL,
    window_used INTEGER NOT NULL DEFAULT 0,
    window_reset_at TEXT,
    cost_per_1k_in REAL,
    cost_per_1k_out REAL,
    status TEXT NOT NULL,
    last_checked TEXT
);

CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    task_id INTEGER,
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    est_cost REAL NOT NULL,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY (endpoint_id) REFERENCES endpoints(id),
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL UNIQUE,
    default_tier TEXT NOT NULL,
    fallback_tier TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS benchmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id INTEGER NOT NULL,
    task_type TEXT NOT NULL,
    score REAL NOT NULL,
    latency_ms INTEGER NOT NULL,
    cost_est REAL NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    run_at TEXT NOT NULL,
    FOREIGN KEY (endpoint_id) REFERENCES endpoints(id)
);

CREATE TABLE IF NOT EXISTS bench_fixtures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,
    input_path TEXT NOT NULL,
    rubric_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (task_type, input_path, rubric_path)
);

CREATE TABLE IF NOT EXISTS bench_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id INTEGER,
    fixture_id INTEGER NOT NULL,
    output_path TEXT NOT NULL,
    score REAL NOT NULL,
    latency_ms INTEGER NOT NULL,
    cost_est REAL NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    run_at TEXT NOT NULL,
    FOREIGN KEY (endpoint_id) REFERENCES endpoints(id),
    FOREIGN KEY (fixture_id) REFERENCES bench_fixtures(id)
);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(connection: sqlite3.Connection | None = None) -> None:
    owns_connection = connection is None
    conn = connection or connect()
    try:
        conn.executescript(SCHEMA)
        _ensure_projects_template_column(conn)
        _ensure_tasks_thread_column(conn)
        _ensure_handoffs_thread_columns(conn)
        _ensure_conversations_pinned_column(conn)
        _ensure_conversations_mode_default_column(conn)
        _ensure_messages_mode_columns(conn)
        _ensure_route_decisions_mode_columns(conn)
        _ensure_agent_threads_table(conn)
        _ensure_ui_preferences_table(conn)
        _ensure_provider_profiles_and_endpoint_columns(conn)
        _ensure_endpoints_usage_columns(conn)
        _seed_ui_preferences(conn)
        _seed_agent_threads(conn)
        conn.commit()
    finally:
        if owns_connection:
            conn.close()


def _ensure_projects_template_column(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(projects)").fetchall()
    }
    if "template" in columns:
        return
    connection.execute("ALTER TABLE projects ADD COLUMN template TEXT NOT NULL DEFAULT 'default'")


def _ensure_tasks_thread_column(connection: sqlite3.Connection) -> None:
    columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(tasks)").fetchall()}
    if "thread" not in columns:
        connection.execute("ALTER TABLE tasks ADD COLUMN thread TEXT NOT NULL DEFAULT 'orchestrator'")


def _ensure_handoffs_thread_columns(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(handoffs)").fetchall()
    }
    if "thread_from" not in columns:
        connection.execute(
            "ALTER TABLE handoffs ADD COLUMN thread_from TEXT NOT NULL DEFAULT 'orchestrator'"
        )
    if "thread_to" not in columns:
        connection.execute(
            "ALTER TABLE handoffs ADD COLUMN thread_to TEXT NOT NULL DEFAULT 'orchestrator'"
        )


def _ensure_conversations_pinned_column(connection: sqlite3.Connection) -> None:
    columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(conversations)").fetchall()}
    if "pinned" not in columns:
        connection.execute("ALTER TABLE conversations ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")


def _ensure_conversations_mode_default_column(connection: sqlite3.Connection) -> None:
    columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(conversations)").fetchall()}
    if "mode_default" not in columns:
        connection.execute("ALTER TABLE conversations ADD COLUMN mode_default TEXT NOT NULL DEFAULT 'ask'")


def _ensure_messages_mode_columns(connection: sqlite3.Connection) -> None:
    columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(messages)").fetchall()}
    if "mode" not in columns:
        connection.execute("ALTER TABLE messages ADD COLUMN mode TEXT NOT NULL DEFAULT 'ask'")
    if "lane_override" not in columns:
        connection.execute("ALTER TABLE messages ADD COLUMN lane_override TEXT")


def _ensure_route_decisions_mode_columns(connection: sqlite3.Connection) -> None:
    columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(route_decisions)").fetchall()}
    if "mode" not in columns:
        connection.execute("ALTER TABLE route_decisions ADD COLUMN mode TEXT NOT NULL DEFAULT 'ask'")
    if "est_cost_usd" not in columns:
        connection.execute("ALTER TABLE route_decisions ADD COLUMN est_cost_usd REAL")


def _ensure_agent_threads_table(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(agent_threads)").fetchall()
    }
    if not columns:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_threads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                thread_name TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'idle',
                current_task_id INTEGER,
                last_handoff_id INTEGER,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY (current_task_id) REFERENCES tasks(id),
                FOREIGN KEY (last_handoff_id) REFERENCES handoffs(id),
                UNIQUE (project_id, thread_name)
            )
            """
        )
        return

    fk_rows = connection.execute("PRAGMA foreign_key_list(agent_threads)").fetchall()
    has_cascade = any(
        str(row["from"]) == "project_id"
        and str(row["table"]) == "projects"
        and str(row["on_delete"]).upper() == "CASCADE"
        for row in fk_rows
    )
    if has_cascade:
        return

    connection.execute("ALTER TABLE agent_threads RENAME TO agent_threads_legacy")
    connection.execute(
        """
        CREATE TABLE agent_threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            thread_name TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'idle',
            current_task_id INTEGER,
            last_handoff_id INTEGER,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY (current_task_id) REFERENCES tasks(id),
            FOREIGN KEY (last_handoff_id) REFERENCES handoffs(id),
            UNIQUE (project_id, thread_name)
        )
        """
    )
    legacy_rows = connection.execute(
        """
        SELECT id, project_id, thread_name, state, current_task_id, last_handoff_id, updated_at
        FROM agent_threads_legacy
        ORDER BY id ASC
        """
    ).fetchall()
    for row in legacy_rows:
        connection.execute(
            """
            INSERT INTO agent_threads (
                id, project_id, thread_name, state, current_task_id, last_handoff_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["project_id"],
                row["thread_name"],
                row["state"],
                row["current_task_id"],
                row["last_handoff_id"],
                row["updated_at"],
            ),
        )
    connection.execute("DROP TABLE agent_threads_legacy")


def _ensure_ui_preferences_table(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(ui_preferences)").fetchall()
    }
    if columns:
        return
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ui_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _seed_ui_preferences(connection: sqlite3.Connection) -> None:
    from core.memory import utc_now

    defaults = {
        "theme": "dark",
        "density": "comfortable",
        "sidebar": "expanded",
        "sidebar_collapsed": "1",
        "context_drawer_open": "0",
        "context_drawer_section": "memory",
        "home_redirect": "chat",
        "chat_last_lane": "auto",
        "chat_last_mode": "ask",
    }
    created_at = utc_now()
    for key, value in defaults.items():
        connection.execute(
            """
            INSERT OR IGNORE INTO ui_preferences (key, value, updated_at)
            VALUES (?, ?, ?)
            """,
            (key, value, created_at),
        )


def _seed_agent_threads(connection: sqlite3.Connection) -> None:
    from core.agents import seed_agent_threads_for_all_projects

    seed_agent_threads_for_all_projects(connection)


def _ensure_endpoints_usage_columns(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(endpoints)").fetchall()
    }
    if "window_used" not in columns:
        connection.execute("ALTER TABLE endpoints ADD COLUMN window_used INTEGER NOT NULL DEFAULT 0")
    if "window_reset_at" not in columns:
        connection.execute("ALTER TABLE endpoints ADD COLUMN window_reset_at TEXT")
    if "last_model_sync" not in columns:
        connection.execute("ALTER TABLE endpoints ADD COLUMN last_model_sync TEXT")


def _ensure_provider_profiles_and_endpoint_columns(connection: sqlite3.Connection) -> None:
    from core.providers import ensure_provider_profiles_migration

    ensure_provider_profiles_migration(connection)
