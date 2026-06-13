from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path

from core.chat import create_conversation, list_conversations
from core.db import APP_ROOT, connect, init_db
from core.endpoints import add_endpoint, list_endpoints
from core.memory import get_project
from core.packets import create_packet
from core.projects import ProjectExistsError, create_project


PROJECT_NAME = "white room"
PROJECT_SLUG = "white-room"


DEMO_ENDPOINTS = [
    {
        "name": "manual-claude",
        "endpoint_class": "manual_claude",
        "tier": "manual",
        "base_url": "manual://claude",
        "capabilities": '["planning", "review", "handoff"]',
        "daily_limit": "manual",
        "window_limit": "manual",
        "status": "manual",
    },
    {
        "name": "codex-gateway",
        "endpoint_class": "codex_lb",
        "tier": "custom",
        "base_url": "https://your-gateway.example/v1",
        "capabilities": '["execution", "hard-debugging"]',
        "daily_limit": "user-configured",
        "window_limit": "approval-gated",
        "status": "needs_key",
    },
    {
        "name": "ollama-local",
        "endpoint_class": "ollama_local",
        "tier": "local",
        "base_url": "http://127.0.0.1:11434",
        "capabilities": '["draft", "summarization", "extraction"]',
        "daily_limit": "local",
        "window_limit": "local",
        "status": "local",
    },
    {
        "name": "lmstudio-local",
        "endpoint_class": "lmstudio_local",
        "tier": "local",
        "base_url": "http://127.0.0.1:1234/v1",
        "capabilities": '["draft", "summarization", "extraction"]',
        "daily_limit": "local",
        "window_limit": "local",
        "status": "local",
    },
    {
        "name": "groq-cloud",
        "endpoint_class": "groq_cloud",
        "tier": "cloud",
        "base_url": "https://api.groq.com/openai/v1",
        "capabilities": '["draft", "summarization"]',
        "daily_limit": "provider-managed",
        "window_limit": "approval-gated",
        "status": "needs_key",
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a clean local WHITE ROOM demo workspace.")
    parser.add_argument("--reset", action="store_true", help="Remove ignored local runtime state before bootstrapping.")
    args = parser.parse_args()

    if args.reset:
        _remove_runtime_path(APP_ROOT / "data")
        _remove_runtime_path(APP_ROOT / "projects")
        for filename in (".env", "secrets.local.json", "secrets.enc"):
            path = APP_ROOT / filename
            if path.exists():
                path.unlink()

    with connect() as conn:
        init_db(conn)

    _ensure_project()
    _ensure_endpoints()
    _ensure_conversation()
    _ensure_demo_task()
    print("WHITE ROOM demo workspace is ready.")
    print("Open http://127.0.0.1:8765/chat/white-room after starting the server.")


def _remove_runtime_path(path: Path) -> None:
    resolved = path.resolve()
    root = APP_ROOT.resolve()
    if resolved == root or root not in resolved.parents:
        raise RuntimeError(f"refusing to remove unexpected path: {resolved}")
    if path.exists():
        shutil.rmtree(path)


def _ensure_project() -> None:
    try:
        get_project(PROJECT_SLUG)
        return
    except ValueError:
        pass

    try:
        create_project(PROJECT_NAME)
    except ProjectExistsError:
        return


def _ensure_endpoints() -> None:
    existing_names = {endpoint.name for endpoint in list_endpoints()}
    for endpoint in DEMO_ENDPOINTS:
        if endpoint["name"] in existing_names:
            continue
        add_endpoint(**endpoint)


def _ensure_conversation() -> None:
    if list_conversations(PROJECT_SLUG):
        return
    create_conversation(PROJECT_SLUG, "Demo build thread", lane="auto")


def _ensure_demo_task() -> None:
    with sqlite3.connect(APP_ROOT / "data" / "whiteroom.db") as conn:
        row = conn.execute(
            """
            SELECT t.id
            FROM tasks AS t
            JOIN projects AS p ON p.id = t.project_id
            WHERE p.slug = ?
            ORDER BY t.id ASC
            LIMIT 1
            """,
            (PROJECT_SLUG,),
        ).fetchone()
    if row is not None:
        return

    create_packet(
        slug=PROJECT_SLUG,
        title="Create a provider-safe implementation packet",
        goal="Demonstrate how WHITE ROOM turns a project objective into a scoped, auditable task packet.",
        size_class="small",
        preferred_route="execution",
        expected_output="A scoped packet plus local status and handoff notes.",
        acceptance="The packet exists locally and does not require a cloud provider call.",
    )


if __name__ == "__main__":
    main()
