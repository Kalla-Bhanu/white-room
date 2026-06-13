from __future__ import annotations

from fastapi.testclient import TestClient

import web.server as server


def test_composer_renders_lane_and_mode_controls() -> None:
    client = TestClient(server.app)
    response = client.get("/chat/white-room")
    assert response.status_code == 200
    assert 'name="lane_override"' in response.text
    assert 'name="mode"' in response.text
    assert "needs key" in response.text


def test_manual_lane_turn_persists_mode_and_lane_override() -> None:
    client = TestClient(server.app)
    conversation = server.list_conversations("white-room")[0]

    response = client.post(
        f"/chat/{conversation.id}/send",
        json={
            "content": "Wire the cockpit composer through a manual lane.",
            "lane_override": "manual_claude",
            "mode": "review",
        },
    )
    assert response.status_code == 200
    assert "manual" in response.text.lower()

    messages = server.list_messages(conversation.id)
    user_message = next(message for message in messages if message.content == "Wire the cockpit composer through a manual lane.")
    assert user_message.mode == "review"
    assert user_message.lane_override == "manual_claude"

    updated_conversation = server.get_conversation(conversation.id)
    assert updated_conversation.mode_default == "review"
