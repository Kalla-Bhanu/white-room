from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from core.manual_lane import ManualImportResult, export_manual_chat_packet, import_manual_chat_response
import web.server as server


def test_cockpit_renders_manual_claude_lane() -> None:
    client = TestClient(server.app)
    response = client.get("/chat/white-room")

    assert response.status_code == 200
    assert "Manual Claude lane" in response.text
    assert "Paste-only lane." in response.text
    assert "Export context" in response.text


def test_manual_chat_export_writes_packet(tmp_path) -> None:
    conversation = server.list_conversations("white-room")[0]
    output_path = tmp_path / "manual-chat-conversation.md"

    result = export_manual_chat_packet("white-room", conversation.id, output_path=output_path)

    assert result.path == output_path
    assert result.path.exists()
    text = result.path.read_text(encoding="utf-8")
    assert conversation.title in text
    assert "manual Claude" in text


def test_manual_chat_import_persists_assistant_message_and_handoff(monkeypatch, tmp_path) -> None:
    conversation = server.list_conversations("white-room")[0]
    target_path = tmp_path / "current_status.md"
    handoff_path = tmp_path / "handoff.md"

    monkeypatch.setattr(
        "core.manual_lane.import_manual_claude_output",
        lambda project_slug, import_file, target: ManualImportResult(
            path=target_path,
            target=target,
            handoff_path=handoff_path,
        ),
    )
    monkeypatch.setattr(
        "core.manual_lane.append_handoff",
        lambda **kwargs: handoff_path,
    )

    result = import_manual_chat_response(
        "white-room",
        conversation.id,
        "Manual Claude reply from the cockpit.",
        "current_status.md",
    )

    assert result.path == target_path
    assert result.handoff_path == handoff_path

    messages = server.list_messages(conversation.id)
    assistant_message = next(
        message
        for message in messages
        if message.role == "assistant"
        and message.content == "Manual Claude reply from the cockpit."
        and message.status == "final"
        and message.model_name == "manual_claude"
    )
    assert assistant_message.id > 0
