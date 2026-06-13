from __future__ import annotations

from fastapi.testclient import TestClient

import web.server as server


def test_sidebar_pin_toggle_marks_conversation_first() -> None:
    conversations = server.list_conversations("white-room")
    assert conversations
    conversation = conversations[0]
    original_pinned = conversation.pinned

    client = TestClient(server.app)
    response = client.post(f"/chat/white-room/conversations/{conversation.id}/pin", follow_redirects=False)
    assert response.status_code == 303

    updated_conversation = server.get_conversation(conversation.id)
    assert updated_conversation.pinned is (not original_pinned)
    assert ("Pinned" in client.get("/chat/white-room").text) is (not original_pinned)

    client.post(f"/chat/white-room/conversations/{conversation.id}/pin", follow_redirects=False)
    restored_conversation = server.get_conversation(conversation.id)
    assert restored_conversation.pinned is original_pinned
