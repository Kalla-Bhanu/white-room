from __future__ import annotations

from fastapi.testclient import TestClient

from core.http_local import guard
import web.server as server


def test_chat_to_task_and_task_to_chat_surface_context() -> None:
    client = TestClient(server.app)
    conversation = server.list_conversations("white-room")[0]

    task_response = client.post(f"/chat/{conversation.id}/to-task")
    assert task_response.status_code == 200
    assert "Task packet" in task_response.text

    task_context_response = client.get("/chat/white-room?task=1")
    assert task_context_response.status_code == 200
    assert "Task context" in task_context_response.text
    assert "handoff" in task_context_response.text.lower()


def test_manual_lane_reports_no_live_model_call() -> None:
    client = TestClient(server.app)
    conversation = server.list_conversations("white-room")[0]

    response = client.post(
        f"/chat/{conversation.id}/send",
        json={
            "content": "Summarize the project in one sentence.",
            "lane_override": "manual_claude",
            "mode": "ask",
        },
    )

    assert response.status_code == 200
    assert "no live model call was made" in response.text.lower()


def test_local_guard_rejects_non_localhost() -> None:
    try:
        guard("http://example.com")
    except ValueError as exc:
        assert "localhost" in str(exc).lower()
    else:
        raise AssertionError("expected localhost guard to reject non-localhost base_url")
