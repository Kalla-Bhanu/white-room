from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator

from adapters.lmstudio_local import LMStudioLocalAdapter
from adapters.ollama_local import OllamaLocalAdapter
from core.db import connect, init_db
from core.health import runner_status_snapshot
from core.memory import append_handoff, get_project, utc_now
from core.route_log import record_route_decision


SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    pinned INTEGER NOT NULL DEFAULT 0,
    mode_default TEXT NOT NULL DEFAULT 'ask',
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
    mode TEXT NOT NULL DEFAULT 'ask',
    lane_override TEXT,
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
"""


@dataclass(frozen=True)
class ConversationRecord:
    id: int
    project_id: int
    title: str
    created_at: str
    updated_at: str
    archived: bool
    pinned: bool
    mode_default: str
    session_count: int
    message_count: int


@dataclass(frozen=True)
class ChatSessionRecord:
    id: int
    conversation_id: int
    endpoint_id: int | None
    lane: str
    attached_brain_files: list[str]
    attached_task_id: int | None
    created_at: str


@dataclass(frozen=True)
class MessageRecord:
    id: int
    conversation_id: int
    session_id: int
    role: str
    content: str
    mode: str
    lane_override: str | None
    route_decision_id: int | None
    citations: list[str]
    token_estimate: int
    status: str
    endpoint_id: int | None
    model_name: str | None
    error_kind: str | None
    created_at: str


@dataclass(frozen=True)
class LocalChatTurnRecord:
    project_slug: str
    conversation_id: int
    session_id: int
    user_message_id: int
    assistant_message_id: int | None
    route_decision_id: int
    endpoint_class: str
    endpoint_id: int | None
    model_name: str | None
    status: str
    error_kind: str | None
    detail: str


@dataclass(frozen=True)
class ChatTaskDraft:
    source_message_id: int
    source_role: str
    title: str
    goal: str
    size_class: str
    preferred_route: str
    acceptance: str
    expected_output: str
    excerpt: str


def create_conversation(
    slug: str,
    title: str,
    lane: str = "deterministic",
    endpoint_id: int | None = None,
    attached_brain_files: list[str] | None = None,
    attached_task_id: int | None = None,
) -> tuple[ConversationRecord, ChatSessionRecord]:
    project = get_project(slug)
    created_at = utc_now()
    with connect() as conn:
        init_db(conn)
        conversation_id = int(
            conn.execute(
                """
                INSERT INTO conversations (project_id, title, created_at, updated_at, archived)
                VALUES (?, ?, ?, ?, 0)
                """,
                (project.id, title, created_at, created_at),
            ).lastrowid
        )
        session = _create_chat_session(
            conn,
            conversation_id=conversation_id,
            lane=lane,
            endpoint_id=endpoint_id,
            attached_brain_files=attached_brain_files,
            attached_task_id=attached_task_id,
            created_at=created_at,
        )
        conn.commit()

    conversation = _get_conversation_by_id(conversation_id)
    return conversation, session


def create_chat_session(
    conversation_id: int,
    lane: str,
    endpoint_id: int | None = None,
    attached_brain_files: list[str] | None = None,
    attached_task_id: int | None = None,
) -> ChatSessionRecord:
    created_at = utc_now()
    with connect() as conn:
        init_db(conn)
        session = _create_chat_session(
            conn,
            conversation_id=conversation_id,
            lane=lane,
            endpoint_id=endpoint_id,
            attached_brain_files=attached_brain_files,
            attached_task_id=attached_task_id,
            created_at=created_at,
        )
        conn.commit()
    return session


def attach_task_to_conversation(
    conversation_id: int,
    task_id: int,
    lane: str | None = None,
    attached_brain_files: list[str] | None = None,
) -> ChatSessionRecord:
    latest_session = get_latest_session(conversation_id)
    resolved_brain_files = attached_brain_files or ["current_status.md", "tasks.md", "handoffs.md"]
    if latest_session is not None and latest_session.attached_task_id == task_id:
        if sorted(latest_session.attached_brain_files) == sorted(resolved_brain_files):
            return latest_session

    chosen_lane = lane or (latest_session.lane if latest_session is not None else "deterministic")
    chosen_endpoint_id = latest_session.endpoint_id if latest_session is not None else None
    return create_chat_session(
        conversation_id,
        lane=chosen_lane,
        endpoint_id=chosen_endpoint_id,
        attached_brain_files=resolved_brain_files,
        attached_task_id=task_id,
    )


def save_message(
    conversation_id: int,
    content: str,
    role: str = "user",
    session_id: int | None = None,
    mode: str = "ask",
    lane_override: str | None = None,
    route_decision_id: int | None = None,
    citations: list[str] | None = None,
    status: str = "final",
    endpoint_id: int | None = None,
    model_name: str | None = None,
    error_kind: str | None = None,
) -> MessageRecord:
    created_at = utc_now()
    payload = content.strip()
    token_estimate = estimate_tokens(payload)
    with connect() as conn:
        init_db(conn)
        resolved_session_id = session_id or _latest_session_id(conn, conversation_id)
        if resolved_session_id is None:
            session = _create_chat_session(
                conn,
                conversation_id=conversation_id,
                lane="deterministic",
                endpoint_id=endpoint_id,
                attached_brain_files=[],
                attached_task_id=None,
                created_at=created_at,
            )
            resolved_session_id = session.id
        cursor = conn.execute(
            """
            INSERT INTO messages (
                conversation_id, session_id, role, content, mode, lane_override, route_decision_id, citations,
                token_estimate, status, endpoint_id, model_name, error_kind, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                resolved_session_id,
                role,
                payload,
                mode,
                lane_override,
                route_decision_id,
                json.dumps(citations or []),
                token_estimate,
                status,
                endpoint_id,
                model_name,
                error_kind,
                created_at,
            ),
        )
        message_id = int(cursor.lastrowid)
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (created_at, conversation_id),
        )
        conn.execute(
            "UPDATE conversations SET mode_default = ? WHERE id = ?",
            (mode, conversation_id),
        )
        conn.commit()

    return MessageRecord(
        id=message_id,
        conversation_id=conversation_id,
        session_id=resolved_session_id,
        role=role,
        content=payload,
        mode=mode,
        lane_override=lane_override,
        route_decision_id=route_decision_id,
        citations=citations or [],
        token_estimate=token_estimate,
        status=status,
        endpoint_id=endpoint_id,
        model_name=model_name,
        error_kind=error_kind,
        created_at=created_at,
    )


def list_conversations(slug: str) -> list[ConversationRecord]:
    project = get_project(slug)
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT
                c.id,
                c.project_id,
                c.title,
                c.created_at,
                c.updated_at,
                c.archived,
                c.pinned,
                c.mode_default,
                COUNT(DISTINCT s.id) AS session_count,
                COUNT(DISTINCT m.id) AS message_count
            FROM conversations AS c
            LEFT JOIN chat_sessions AS s ON s.conversation_id = c.id
            LEFT JOIN messages AS m ON m.conversation_id = c.id
            WHERE c.project_id = ?
            GROUP BY c.id
            ORDER BY c.pinned DESC, c.updated_at DESC, c.id DESC
            """,
            (project.id,),
        ).fetchall()
    return [
        ConversationRecord(
            id=int(row["id"]),
            project_id=int(row["project_id"]),
            title=str(row["title"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            archived=bool(row["archived"]),
            pinned=bool(row["pinned"]),
            mode_default=str(row["mode_default"]),
            session_count=int(row["session_count"]),
            message_count=int(row["message_count"]),
        )
        for row in rows
    ]


def get_conversation(conversation_id: int) -> ConversationRecord:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT
                c.id,
                c.project_id,
                c.title,
                c.created_at,
                c.updated_at,
                c.archived,
                c.pinned,
                c.mode_default,
                COUNT(DISTINCT s.id) AS session_count,
                COUNT(DISTINCT m.id) AS message_count
            FROM conversations AS c
            LEFT JOIN chat_sessions AS s ON s.conversation_id = c.id
            LEFT JOIN messages AS m ON m.conversation_id = c.id
            WHERE c.id = ?
            GROUP BY c.id
            """,
            (conversation_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"conversation {conversation_id} does not exist")
    return ConversationRecord(
        id=int(row["id"]),
        project_id=int(row["project_id"]),
        title=str(row["title"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        archived=bool(row["archived"]),
        pinned=bool(row["pinned"]),
        mode_default=str(row["mode_default"]),
        session_count=int(row["session_count"]),
        message_count=int(row["message_count"]),
    )


def get_or_create_first(slug: str) -> tuple[ConversationRecord, ChatSessionRecord]:
    conversations = list_conversations(slug)
    if conversations:
        conversation = conversations[0]
        session = _latest_session_for_conversation(conversation.id)
        if session is None:
            session = create_chat_session(conversation.id, lane="deterministic")
        return conversation, session
    return create_conversation(slug, "First conversation")


def set_conversation_pinned(conversation_id: int, pinned: bool) -> ConversationRecord:
    created_at = utc_now()
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "UPDATE conversations SET pinned = ?, updated_at = ? WHERE id = ?",
            (int(pinned), created_at, conversation_id),
        )
        conn.commit()
    return get_conversation(conversation_id)


def delete_conversation(conversation_id: int) -> None:
    with connect() as conn:
        init_db(conn)
        _delete_conversation_records(conn, conversation_id)
        conn.commit()


def list_messages(conversation_id: int, limit: int = 120) -> list[MessageRecord]:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT
                id, conversation_id, session_id, role, content, mode, lane_override, route_decision_id, citations,
                token_estimate, status, endpoint_id, model_name, error_kind, created_at
            FROM messages
            WHERE conversation_id = ?
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (conversation_id, limit),
        ).fetchall()
    return [
        MessageRecord(
            id=int(row["id"]),
            conversation_id=int(row["conversation_id"]),
            session_id=int(row["session_id"]),
            role=str(row["role"]),
            content=str(row["content"]),
            mode=str(row["mode"]),
            lane_override=None if row["lane_override"] is None else str(row["lane_override"]),
            route_decision_id=int(row["route_decision_id"]) if row["route_decision_id"] is not None else None,
            citations=json.loads(str(row["citations"])),
            token_estimate=int(row["token_estimate"]),
            status=str(row["status"]),
            endpoint_id=int(row["endpoint_id"]) if row["endpoint_id"] is not None else None,
            model_name=None if row["model_name"] is None else str(row["model_name"]),
            error_kind=None if row["error_kind"] is None else str(row["error_kind"]),
            created_at=str(row["created_at"]),
        )
        for row in rows
    ]


def draft_task_packet_from_turn(conversation_id: int, source_message_id: int | None = None) -> ChatTaskDraft:
    messages = list_messages(conversation_id)
    if not messages:
        raise ValueError("conversation has no messages to promote")

    source_message = None
    if source_message_id is not None:
        for message in messages:
            if message.id == source_message_id:
                source_message = message
                break
    if source_message is None:
        for message in reversed(messages):
            if message.role in {"user", "assistant"}:
                source_message = message
                break
    if source_message is None:
        source_message = messages[-1]

    content = source_message.content.strip()
    if not content:
        raise ValueError("selected turn is empty")

    size_class = _size_class_for_turn(content)
    preferred_route = _preferred_route_for_turn(source_message)
    excerpt = _turn_excerpt(content)
    title = f"Chat turn {source_message.id}: {excerpt}"
    acceptance = "Task packet exists, is listed on the board, and carries the promoted chat context."
    expected_output = "A scoped task packet created from the selected chat turn."

    return ChatTaskDraft(
        source_message_id=source_message.id,
        source_role=source_message.role,
        title=title,
        goal=content,
        size_class=size_class,
        preferred_route=preferred_route,
        acceptance=acceptance,
        expected_output=expected_output,
        excerpt=excerpt,
    )


def get_latest_session(conversation_id: int) -> ChatSessionRecord | None:
    return _latest_session_for_conversation(conversation_id)


def estimate_tokens(text: str) -> int:
    words = len(text.split())
    return max(1, words * 4 // 3)


def schema_tables() -> tuple[str, ...]:
    return ("conversations", "chat_sessions", "messages")


def _size_class_for_turn(content: str) -> str:
    word_count = len(content.split())
    if word_count <= 20:
        return "small"
    if word_count <= 55:
        return "medium"
    return "large"


def _preferred_route_for_turn(message: MessageRecord) -> str:
    lane = (message.lane_override or "").strip().lower()
    if lane in {"manual_claude", "codex_lb"}:
        return "execution"
    if lane in {"ollama_local", "lmstudio_local"}:
        return "deterministic"
    if message.mode.lower() in {"execute", "plan"}:
        return "execution"
    return "deterministic"


def _turn_excerpt(content: str, limit: int = 8) -> str:
    words = content.split()
    if not words:
        return "turn"
    excerpt = " ".join(words[:limit]).strip()
    if len(words) > limit:
        excerpt += "..."
    return excerpt


def _create_chat_session(
    connection,
    conversation_id: int,
    lane: str,
    endpoint_id: int | None,
    attached_brain_files: list[str] | None,
    attached_task_id: int | None,
    created_at: str,
) -> ChatSessionRecord:
    cursor = connection.execute(
        """
        INSERT INTO chat_sessions (
            conversation_id, endpoint_id, lane, attached_brain_files, attached_task_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            endpoint_id,
            lane,
            json.dumps(attached_brain_files or []),
            attached_task_id,
            created_at,
        ),
    )
    session_id = int(cursor.lastrowid)
    return ChatSessionRecord(
        id=session_id,
        conversation_id=conversation_id,
        endpoint_id=endpoint_id,
        lane=lane,
        attached_brain_files=attached_brain_files or [],
        attached_task_id=attached_task_id,
        created_at=created_at,
    )


def _get_conversation_by_id(conversation_id: int) -> ConversationRecord:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT
                c.id,
                c.project_id,
                c.title,
                c.created_at,
                c.updated_at,
                c.archived,
                c.pinned,
                c.mode_default,
                COUNT(DISTINCT s.id) AS session_count,
                COUNT(DISTINCT m.id) AS message_count
            FROM conversations AS c
            LEFT JOIN chat_sessions AS s ON s.conversation_id = c.id
            LEFT JOIN messages AS m ON m.conversation_id = c.id
            WHERE c.id = ?
            GROUP BY c.id
            """,
            (conversation_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"conversation {conversation_id} does not exist")
    return ConversationRecord(
        id=int(row["id"]),
        project_id=int(row["project_id"]),
        title=str(row["title"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        archived=bool(row["archived"]),
        pinned=bool(row["pinned"]),
        mode_default=str(row["mode_default"]),
        session_count=int(row["session_count"]),
        message_count=int(row["message_count"]),
    )


def _delete_conversation_records(conn, conversation_id: int) -> None:
    row = conn.execute("SELECT project_id FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if row is None:
        raise ValueError(f"conversation {conversation_id} does not exist")
    project_id = int(row["project_id"])
    route_rows = conn.execute(
        "SELECT DISTINCT route_decision_id FROM messages WHERE conversation_id = ? AND route_decision_id IS NOT NULL",
        (conversation_id,),
    ).fetchall()
    route_decision_ids = [int(row["route_decision_id"]) for row in route_rows]
    conn.execute("UPDATE route_decisions SET message_id = NULL WHERE message_id IN (SELECT id FROM messages WHERE conversation_id = ?)", (conversation_id,))
    conn.execute("UPDATE messages SET route_decision_id = NULL WHERE conversation_id = ?", (conversation_id,))
    conn.execute("DELETE FROM execution_runs WHERE conversation_id = ?", (conversation_id,))
    conn.execute("DELETE FROM codex_packets WHERE conversation_id = ?", (conversation_id,))
    conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
    conn.execute("DELETE FROM chat_sessions WHERE conversation_id = ?", (conversation_id,))
    conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    if route_decision_ids:
        placeholders = ", ".join("?" for _ in route_decision_ids)
        conn.execute(
            f"DELETE FROM route_decisions WHERE id IN ({placeholders}) AND project_id = ?",
            (*route_decision_ids, project_id),
        )


def _latest_session_id(connection, conversation_id: int) -> int | None:
    row = connection.execute(
        """
        SELECT id
        FROM chat_sessions
        WHERE conversation_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (conversation_id,),
    ).fetchone()
    if row is None:
        return None
    return int(row["id"])


def _latest_session_for_conversation(conversation_id: int) -> ChatSessionRecord | None:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT id, conversation_id, endpoint_id, lane, attached_brain_files, attached_task_id, created_at
            FROM chat_sessions
            WHERE conversation_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
    if row is None:
        return None
    return ChatSessionRecord(
        id=int(row["id"]),
        conversation_id=int(row["conversation_id"]),
        endpoint_id=int(row["endpoint_id"]) if row["endpoint_id"] is not None else None,
        lane=str(row["lane"]),
        attached_brain_files=json.loads(str(row["attached_brain_files"])),
        attached_task_id=int(row["attached_task_id"]) if row["attached_task_id"] is not None else None,
        created_at=str(row["created_at"]),
    )


def stream_local_chat_turn(
    project_slug: str,
    conversation_id: int,
    content: str,
    lane_hint: str | None = None,
    mode: str = "ask",
    lane_override: str | None = None,
) -> Iterator[dict[str, Any]]:
    project = get_project(project_slug)
    prompt = content.strip()
    if not prompt:
        raise ValueError("chat content is required")

    snapshot = runner_status_snapshot()
    available = [row for row in snapshot.get("endpoints", []) if bool(row.get("reachable"))]
    chosen = _choose_local_endpoint(available, lane_override or lane_hint)
    created_at = utc_now()
    session = create_chat_session(
        conversation_id,
        lane=chosen["endpoint_class"] if chosen else "local-unavailable",
        endpoint_id=chosen["endpoint_id"] if chosen else None,
        attached_brain_files=["current_status.md"],
        attached_task_id=None,
    )
    user_message = save_message(
        conversation_id=conversation_id,
        content=prompt,
        role="user",
        session_id=session.id,
        mode=mode,
        lane_override=lane_override or lane_hint,
        status="final",
    )

    if chosen is None:
        route_decision = record_route_decision(
            project_slug=project.slug,
            task_id=None,
            task_type="chat",
            risk="low",
            size="small",
            chosen_lane="local-unavailable",
            explanation="local runner unavailable; no assistant response was generated",
            source="chat",
            status="unavailable",
            requires_approval=False,
            is_preview=False,
            chosen_endpoint_id=None,
            candidates=[],
        )
        append_handoff(
            project.slug,
            from_worker="codex",
            to_worker="orchestrator",
            summary="WR-212 attempted a local chat turn but no local runner was reachable, so the workbench stayed in an unavailable state without recording an assistant response.",
            artifact_paths=[
                "core/chat.py",
                "web/server.py",
                "web/static/app.js",
                "projects/white-room/brain/current_status.md",
                "projects/white-room/brain/tasks.md",
                "projects/white-room/brain/handoffs.md",
            ],
            thread_from="implementation",
            thread_to="orchestrator",
        )
        yield {
            "event": "unavailable",
            "conversation_id": conversation_id,
            "user_message_id": user_message.id,
            "route_decision_id": route_decision.id,
            "detail": "local runner unavailable",
            "lane": "local-unavailable",
        }
        return

    adapter = _local_adapter_for(chosen["endpoint_class"], chosen["base_url"])
    model_name = _resolve_model_name(
        adapter,
        chosen,
        prompt=prompt,
        mode=mode,
    )
    context_packet = {
        "project_slug": project.slug,
        "conversation_id": conversation_id,
        "prompt": prompt,
        "input_text": prompt,
        "packet_text": prompt,
        "model": model_name,
        "task_id": None,
    }
    route_decision = record_route_decision(
        project_slug=project.slug,
        task_id=None,
        task_type="chat",
        risk="low",
        size="small",
        chosen_lane=chosen["endpoint_class"],
        explanation=f"local runner reachable via {chosen['endpoint_class']}",
        source="chat",
        status="suggested",
        requires_approval=False,
        is_preview=False,
        chosen_endpoint_id=chosen["endpoint_id"],
        candidates=_local_chat_candidates(snapshot),
    )

    yield {
        "event": "status",
        "conversation_id": conversation_id,
        "user_message_id": user_message.id,
        "route_decision_id": route_decision.id,
        "lane": chosen["endpoint_class"],
        "model_name": model_name,
        "detail": f"streaming via {chosen['endpoint_class']}",
    }

    try:
        sent = adapter.send_chat(context_packet, {"model_name": model_name})
        assistant_text = str(sent.get("text") or "").strip()
        final_usage = dict(sent.get("usage") or {})
        if assistant_text:
            for delta in _chunk_text_for_stream(assistant_text):
                yield {
                    "event": "delta",
                    "conversation_id": conversation_id,
                    "route_decision_id": route_decision.id,
                    "delta": delta,
                }
        assistant_message = save_message(
            conversation_id=conversation_id,
            content=assistant_text or "Local chat produced no text.",
            role="assistant",
            session_id=session.id,
            mode=mode,
            lane_override=lane_override or lane_hint,
            route_decision_id=route_decision.id,
            status="draft",
            endpoint_id=chosen["endpoint_id"],
            model_name=model_name,
        )
        append_handoff(
            project.slug,
            from_worker="codex",
            to_worker="orchestrator",
            summary=f"WR-212 complete: a local chat turn streamed through {chosen['endpoint_class']} and saved assistant message {assistant_message.id} with route decision {route_decision.id}.",
            artifact_paths=[
                "core/chat.py",
                "web/server.py",
                "web/static/app.js",
                "projects/white-room/brain/current_status.md",
                "projects/white-room/brain/tasks.md",
                "projects/white-room/brain/handoffs.md",
            ],
            thread_from="implementation",
            thread_to="orchestrator",
        )
        yield {
            "event": "complete",
            "conversation_id": conversation_id,
            "route_decision_id": route_decision.id,
            "assistant_message_id": assistant_message.id,
            "endpoint_class": chosen["endpoint_class"],
            "endpoint_id": chosen["endpoint_id"],
            "model_name": model_name,
            "status": "draft",
            "usage": final_usage,
            "text": assistant_text,
        }
    except Exception as exc:
        error_info = adapter.normalize_error(exc)
        error_kind = str(error_info.get("kind") or "unknown")
        detail = str(error_info.get("message") or "local chat failed")
        assistant_message = save_message(
            conversation_id=conversation_id,
            content=detail,
            role="assistant",
            session_id=session.id,
            mode=mode,
            lane_override=lane_override or lane_hint,
            route_decision_id=route_decision.id,
            status="error",
            endpoint_id=chosen["endpoint_id"],
            model_name=model_name,
            error_kind=error_kind,
        )
        append_handoff(
            project.slug,
            from_worker="codex",
            to_worker="orchestrator",
            summary=f"WR-212 local chat turn failed on {chosen['endpoint_class']} with error kind {error_kind}; assistant message {assistant_message.id} captured the failure locally.",
            artifact_paths=[
                "core/chat.py",
                "web/server.py",
                "web/static/app.js",
                "projects/white-room/brain/current_status.md",
                "projects/white-room/brain/tasks.md",
                "projects/white-room/brain/handoffs.md",
            ],
            thread_from="implementation",
            thread_to="orchestrator",
        )
        yield {
            "event": "error",
            "conversation_id": conversation_id,
            "route_decision_id": route_decision.id,
            "assistant_message_id": assistant_message.id,
            "error_kind": error_kind,
            "detail": detail,
            "status": "error",
        }


def _choose_local_endpoint(endpoints: list[dict[str, Any]], lane_hint: str | None) -> dict[str, Any] | None:
    if not endpoints:
        return None
    normalized_hint = _normalize_lane_hint(lane_hint)
    if normalized_hint:
        for endpoint in endpoints:
            if _normalize_lane_hint(str(endpoint.get("endpoint_class"))) == normalized_hint:
                return endpoint
    for preferred in ("ollama_local", "lmstudio_local"):
        for endpoint in endpoints:
            if str(endpoint.get("endpoint_class")) == preferred:
                return endpoint
    return endpoints[0]


def _normalize_lane_hint(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace("-", "_")
    return text or None


def _local_adapter_for(endpoint_class: str, base_url: str):
    if endpoint_class == "lmstudio_local":
        return LMStudioLocalAdapter(base_url or "http://127.0.0.1:1234")
    return OllamaLocalAdapter(base_url or "http://127.0.0.1:11434")


def _resolve_model_name(
    adapter,
    endpoint: dict[str, Any],
    *,
    prompt: str = "",
    mode: str = "ask",
) -> str:
    endpoint_model = str(endpoint.get("model_name") or "").strip()
    if endpoint_model:
        return endpoint_model

    profiles = adapter.list_models()
    if not profiles:
        return "local-model"

    if isinstance(adapter, OllamaLocalAdapter):
        chosen = _pick_ollama_model_name(profiles, prompt=prompt, mode=mode)
        if chosen:
            return chosen

    model_name = str(profiles[0].get("model_name") or "").strip()
    return model_name or "local-model"


def _pick_ollama_model_name(
    profiles: list[dict[str, Any]],
    *,
    prompt: str,
    mode: str,
) -> str:
    available = [str(item.get("model_name") or "").strip() for item in profiles]
    available = [name for name in available if name]
    if not available:
        return "llama3.1:latest"

    prompt_lower = prompt.lower()
    mode_lower = mode.strip().lower()
    is_code_work = mode_lower in {"execute", "review"} or any(
        keyword in prompt_lower
        for keyword in (
            "code",
            "bug",
            "debug",
            "stack trace",
            "traceback",
            "error",
            "exception",
            "function",
            "class ",
            "refactor",
            "implement",
            "typescript",
            "javascript",
            "python",
            "sql",
            "api",
            "endpoint",
            "regex",
            "test",
            "repository",
            "repo",
            "script",
        )
    )
    is_summary_work = mode_lower == "summarize" or any(
        keyword in prompt_lower
        for keyword in ("summarize", "summary", "compress", "shorten", "tldr", "bullet")
    )

    preference_groups: list[tuple[str, ...]]
    if is_code_work:
        preference_groups = (
            ("qwen2.5-coder",),
            ("deepseek-coder",),
            ("llama3.1", "llama3"),
            ("mistral",),
            ("gemma3", "gemma"),
        )
    elif is_summary_work:
        preference_groups = (
            ("gemma3", "gemma"),
            ("mistral",),
            ("llama3.1", "llama3"),
            ("qwen2.5-coder",),
            ("deepseek-coder",),
        )
    else:
        preference_groups = (
            ("llama3.1", "llama3"),
            ("mistral",),
            ("gemma3", "gemma"),
            ("qwen2.5-coder",),
            ("deepseek-coder",),
        )

    for group in preference_groups:
        match = _first_model_match(available, group)
        if match:
            return match
    return available[0]


def _first_model_match(available: list[str], needles: tuple[str, ...]) -> str | None:
    lowered = [(name, name.lower()) for name in available]
    for needle in needles:
        for original, normalized in lowered:
            if needle in normalized:
                return original
    return None


def _local_chat_candidates(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in snapshot.get("endpoints", []):
        candidates.append(
            {
                "endpoint_class": row.get("endpoint_class"),
                "reachable": bool(row.get("reachable")),
                "latency_ms": row.get("latency_ms"),
                "detail": row.get("detail"),
            }
        )
    return candidates


def _chunk_text_for_stream(text: str, chunk_size: int = 8) -> list[str]:
    words = text.split()
    if len(words) <= chunk_size:
        return [text]
    chunks: list[str] = []
    for index in range(0, len(words), chunk_size):
        chunk = " ".join(words[index : index + chunk_size]).strip()
        if chunk:
            chunks.append(chunk + (" " if index + chunk_size < len(words) else ""))
    return chunks or [text]
