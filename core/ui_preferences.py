from __future__ import annotations

from core.db import connect, init_db
from core.memory import utc_now


THEME_VALUES = ("dark", "light", "system")
DEFAULT_THEME = "dark"
DEFAULT_DENSITY = "comfortable"
DEFAULT_SIDEBAR = "collapsed"


def get_ui_preferences() -> dict[str, str]:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            "SELECT key, value FROM ui_preferences ORDER BY key ASC"
        ).fetchall()
    prefs = {str(row["key"]): str(row["value"]) for row in rows}
    prefs.setdefault("theme", DEFAULT_THEME)
    prefs.setdefault("density", DEFAULT_DENSITY)
    prefs.setdefault("sidebar", DEFAULT_SIDEBAR)
    prefs.setdefault("sidebar_collapsed", "1")
    prefs.setdefault("context_drawer_open", "0")
    prefs.setdefault("context_drawer_section", "memory")
    prefs.setdefault("home_redirect", "chat")
    prefs.setdefault("chat_last_lane", "auto")
    prefs.setdefault("chat_last_mode", "ask")
    return prefs


def get_ui_preference(key: str, default: str) -> str:
    return get_ui_preferences().get(key, default)


def set_ui_preference(key: str, value: str) -> None:
    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO ui_preferences (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, utc_now()),
        )
        conn.commit()


def current_theme() -> str:
    theme = get_ui_preference("theme", DEFAULT_THEME)
    return theme if theme in THEME_VALUES else DEFAULT_THEME


def theme_toggle_value() -> str:
    theme = current_theme()
    cycle = {"dark": "light", "light": "system", "system": "dark"}
    return cycle.get(theme, DEFAULT_THEME)


def theme_render_value() -> str:
    theme = current_theme()
    return "dark" if theme == "system" else theme
