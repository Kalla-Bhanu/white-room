from __future__ import annotations

import secrets

from fastapi.testclient import TestClient

import web.server as server
from core.chat import create_conversation, delete_conversation, save_message


def test_sidebar_can_create_and_delete_project_and_conversation() -> None:
    client = TestClient(server.app)
    slug = f"sidebar-crud-{secrets.token_hex(3)}"
    name = slug

    response = client.post("/projects/create", data={"name": name}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith(f"/chat/{slug}")

    project = server.get_project(slug)
    assert project.slug == slug

    response = client.post(
        f"/chat/{slug}/conversations/create",
        data={"title": "Scratch thread"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "conversation_id=" in response.headers["location"]

    conversations = server.list_conversations(slug)
    created = next(conversation for conversation in conversations if conversation.title == "Scratch thread")
    delete_response = client.post(
        f"/chat/{slug}/conversations/{created.id}/delete",
        follow_redirects=False,
    )
    assert delete_response.status_code == 303
    assert all(conversation.id != created.id for conversation in server.list_conversations(slug))

    project_delete_response = client.post(f"/projects/{slug}/delete", follow_redirects=False)
    assert project_delete_response.status_code == 303
    try:
        server.get_project(slug)
    except ValueError:
        pass
    else:
        raise AssertionError(f"{slug} should have been deleted")


def test_chat_renders_structured_message_html() -> None:
    client = TestClient(server.app)
    conversation, _session = create_conversation("white-room", "Rendering thread", lane="auto")
    try:
        save_message(
            conversation.id,
            "# Tree Notes\n\n- root\n- leaf\n\n```python\nclass Node:\n    pass\n```",
            role="assistant",
            mode="ask",
        )

        response = client.get(f"/chat/white-room?conversation_id={conversation.id}")
        assert response.status_code == 200
        assert '<pre class="chat-code-block"><code data-language="python">' in response.text
        assert "<ul><li>root</li><li>leaf</li></ul>" in response.text
    finally:
        delete_conversation(conversation.id)
