from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def bootstrap_public_workspace() -> None:
    """Create minimal ignored runtime state for tests in a fresh public clone."""
    from core.chat import create_conversation, list_conversations
    from core.db import connect, init_db
    from core.endpoints import add_endpoint, list_endpoints
    from core.memory import get_project
    from core.projects import ProjectExistsError, create_project

    with connect() as conn:
        init_db(conn)

    try:
        get_project("white-room")
    except ValueError:
        try:
            create_project("white room")
        except ProjectExistsError:
            pass

    existing_classes = {endpoint.endpoint_class for endpoint in list_endpoints()}
    endpoint_defaults = [
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
    for endpoint in endpoint_defaults:
        if endpoint["endpoint_class"] in existing_classes:
            continue
        add_endpoint(**endpoint)

    if not list_conversations("white-room"):
        create_conversation("white-room", "First conversation", lane="auto")
