from __future__ import annotations

import re
import secrets
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.chat import create_chat_session, get_conversation, get_latest_session, list_messages, save_message
from core.db import connect, init_db
from core.memory import append_handoff, get_project, read_current_status, update_brain_file_index
from core.packets import estimate_tokens


TOKEN_BUDGETS = {
    "small": 2000,
    "medium": 6000,
    "hard": 12000,
}

TARGET_BRAIN_FILES = {
    "business_scope.md",
    "active_plan.md",
    "architecture.md",
    "tasks.md",
    "decisions.md",
    "errors.md",
    "handoffs.md",
    "model_routes.md",
    "verification.md",
    "current_status.md",
}


@dataclass(frozen=True)
class ManualExportResult:
    path: Path
    token_estimate: int
    task_id: int


@dataclass(frozen=True)
class ManualImportResult:
    path: Path
    target: str
    handoff_path: Path


@dataclass(frozen=True)
class ManualChatExportResult:
    path: Path
    token_estimate: int
    conversation_id: int


@dataclass(frozen=True)
class ManualChatImportResult:
    path: Path
    target: str
    handoff_path: Path
    message_id: int
    conversation_id: int


def export_manual_claude_packet(
    project_slug: str,
    task_id: int,
    output_path: Path | None = None,
) -> ManualExportResult:
    project = get_project(project_slug)
    task = _load_task(project.id, task_id)
    packet_row = _load_task_packet(task_id)
    current_status = read_current_status(project_slug)
    latest_handoff = _latest_handoff_summary(project.id)
    task_line = _task_line_from_tasks_file(project.path / "brain" / "tasks.md", task_id)

    export_path = output_path or (project.path / "packets" / f"manual-claude-task-{task_id:03d}.md")
    export_text = _render_export(
        project_slug=project.slug,
        task_id=task_id,
        task=task,
        packet_row=packet_row,
        current_status=current_status,
        latest_handoff=latest_handoff,
        task_line=task_line,
    )
    token_estimate = estimate_tokens(export_text)
    _enforce_budget(task["size_class"], token_estimate)
    export_path.write_text(export_text, encoding="utf-8")

    return ManualExportResult(path=export_path, token_estimate=token_estimate, task_id=task_id)


def import_manual_claude_output(
    project_slug: str,
    import_file: Path,
    target: str,
) -> ManualImportResult:
    project = get_project(project_slug)
    if target not in TARGET_BRAIN_FILES:
        valid = ", ".join(sorted(TARGET_BRAIN_FILES))
        raise ValueError(f"invalid target brain file '{target}'. valid: {valid}")
    if not import_file.exists():
        raise ValueError(f"import file '{import_file}' does not exist")
    if not import_file.is_file():
        raise ValueError(f"import file '{import_file}' is not a file")

    imported_text = import_file.read_text(encoding="utf-8")
    decision = _extract_decision(imported_text)
    target_path = project.path / "brain" / target
    if not target_path.exists():
        raise ValueError(f"target brain file '{target}' does not exist for project '{project_slug}'")

    target_path.write_text(imported_text.rstrip() + "\n", encoding="utf-8")
    update_brain_file_index(project.slug, target)

    if decision is not None:
        _record_decision(project.id, project.slug, decision["decision"], decision["rationale"])

    handoff_path = append_handoff(
        slug=project.slug,
        from_worker="manual_claude",
        to_worker="orchestrator",
        summary=f"Imported manual Claude output from {import_file.name} into {target}.",
        artifact_paths=[
            str(import_file.resolve().relative_to(project.path.parent.parent))
            if import_file.is_absolute() and project.path.parent.parent in import_file.parents
            else str(import_file),
            f"brain/{target}",
        ],
    )

    return ManualImportResult(path=target_path, target=target, handoff_path=handoff_path)


def export_manual_chat_packet(
    project_slug: str,
    conversation_id: int,
    output_path: Path | None = None,
) -> ManualChatExportResult:
    project = get_project(project_slug)
    conversation = get_conversation(conversation_id)
    if conversation.project_id != project.id:
        raise ValueError(f"conversation '{conversation_id}' does not belong to project '{project_slug}'")

    latest_session = get_latest_session(conversation_id)
    messages = list_messages(conversation_id)
    current_status = read_current_status(project_slug)

    export_path = output_path or (
        project.path / "packets" / f"manual-chat-conversation-{conversation_id:03d}.md"
    )
    export_text = _render_chat_export(
        project_slug=project.slug,
        conversation_title=conversation.title,
        conversation_id=conversation.id,
        latest_session=latest_session,
        messages=messages,
        current_status=current_status,
    )
    token_estimate = estimate_tokens(export_text)
    export_path.write_text(export_text, encoding="utf-8")
    return ManualChatExportResult(
        path=export_path,
        token_estimate=token_estimate,
        conversation_id=conversation.id,
    )


def import_manual_chat_response(
    project_slug: str,
    conversation_id: int,
    content: str,
    target: str,
) -> ManualChatImportResult:
    project = get_project(project_slug)
    conversation = get_conversation(conversation_id)
    if conversation.project_id != project.id:
        raise ValueError(f"conversation '{conversation_id}' does not belong to project '{project_slug}'")
    if not content.strip():
        raise ValueError("manual Claude content is required")

    latest_session = get_latest_session(conversation_id)
    attached_task_id = latest_session.attached_task_id if latest_session is not None else None
    temp_dir = project.path / "artifacts" / "private" / "manual_imports"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"manual-chat-import-{secrets.token_hex(8)}.md"
    temp_path.write_text(content.rstrip() + "\n", encoding="utf-8")

    imported = import_manual_claude_output(project.slug, temp_path, target)
    session = create_chat_session(
        conversation_id=conversation_id,
        lane="manual_claude",
        endpoint_id=None,
        attached_brain_files=[target],
        attached_task_id=attached_task_id,
    )
    message = save_message(
        conversation_id=conversation_id,
        content=content,
        role="assistant",
        session_id=session.id,
        status="final",
        model_name="manual_claude",
    )

    return ManualChatImportResult(
        path=imported.path,
        target=imported.target,
        handoff_path=imported.handoff_path,
        message_id=message.id,
        conversation_id=conversation.id,
    )


def _extract_decision(imported_text: str) -> dict[str, str] | None:
    begin_present = "BEGIN DECISION" in imported_text.upper()
    end_present = "END DECISION" in imported_text.upper()
    match = re.search(r"BEGIN DECISION\s*(.*?)\s*END DECISION", imported_text, re.DOTALL | re.IGNORECASE)
    if match is None:
        if begin_present or end_present:
            raise ValueError("invalid DECISION block: missing BEGIN DECISION / END DECISION delimiter")
        return None

    block = match.group(1).strip()
    decision: list[str] = []
    rationale: list[str] = []
    current: list[str] | None = None

    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("decision:"):
            current = decision
            content = line.split(":", 1)[1].strip()
            if content:
                decision.append(content)
            continue
        if lower.startswith("rationale:"):
            current = rationale
            content = line.split(":", 1)[1].strip()
            if content:
                rationale.append(content)
            continue
        if current is None:
            raise ValueError("invalid DECISION block: expected Decision: or Rationale: lines")
        current.append(line)

    decision_text = " ".join(decision).strip()
    rationale_text = " ".join(rationale).strip()
    if not decision_text or not rationale_text:
        raise ValueError("invalid DECISION block: both Decision and Rationale are required")
    return {"decision": decision_text, "rationale": rationale_text}


def _record_decision(project_id: int, project_slug: str, decision_text: str, rationale_text: str) -> None:
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    project_root = get_project(project_slug).path
    decisions_path = project_root / "brain" / "decisions.md"
    entry = (
        f"\n## Decision {created_at} -- manual_claude import\n"
        f"- decision: {decision_text}\n"
        f"- rationale: {rationale_text}\n"
    )
    decisions_path.write_text(
        decisions_path.read_text(encoding="utf-8") + entry,
        encoding="utf-8",
    )

    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO decisions (project_id, decision, rationale, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (project_id, decision_text, rationale_text, created_at),
        )
        conn.commit()

    update_brain_file_index(project_slug, "decisions.md")


def _load_task(project_id: int, task_id: int) -> dict[str, str]:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT id, title, goal, status, size_class, preferred_tier, created_at, updated_at
            FROM tasks
            WHERE project_id = ? AND id = ?
            """,
            (project_id, task_id),
        ).fetchone()
    if row is None:
        raise ValueError(f"task '{task_id}' does not exist for this project")
    return {key: str(row[key]) for key in row.keys()}


def _load_task_packet(task_id: int) -> dict[str, str] | None:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT packet_text, token_estimate, generated_at, model_route, expected_output
            FROM task_packets
            WHERE task_id = ?
            ORDER BY generated_at DESC, id DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
    if row is None:
        return None
    return {key: str(row[key]) for key in row.keys()}


def _latest_handoff_summary(project_id: int) -> str:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT created_at, from_worker, to_worker, summary
            FROM handoffs
            WHERE project_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
    if row is None:
        return "No handoffs recorded yet."
    return (
        f"{row['created_at']} from {row['from_worker']} to {row['to_worker']}: "
        f"{row['summary']}"
    )


def _task_line_from_tasks_file(tasks_path: Path, task_id: int) -> str:
    pattern = re.compile(rf"^- \[.\] Task {task_id:03d}: .+$", re.MULTILINE)
    match = pattern.search(tasks_path.read_text(encoding="utf-8"))
    return match.group(0) if match else "Task line not found."


def _render_export(
    project_slug: str,
    task_id: int,
    task: dict[str, str],
    packet_row: dict[str, str] | None,
    current_status: str,
    latest_handoff: str,
    task_line: str,
) -> str:
    preferred_route = packet_row["model_route"] if packet_row else task["preferred_tier"]
    expected_output = packet_row["expected_output"] if packet_row else (
        "A concise Claude response that can be imported into the target brain file."
    )
    source_packet = packet_row["packet_text"] if packet_row else _fallback_packet_text(project_slug, task)
    lines = [
        f"PROJECT: {project_slug}",
        f"TASK ID: {task_id}",
        f"TASK TITLE: {task['title']}",
        f"SIZE CLASS: {task['size_class']}",
        "",
        "GOAL:",
        task["goal"],
        "",
        "FILES TO READ:",
        f"- projects/{project_slug}/brain/current_status.md",
        f"- projects/{project_slug}/brain/tasks.md",
        f"- projects/{project_slug}/brain/handoffs.md",
        "",
        "CONSTRAINTS:",
        "- No model calls from WHITE ROOM.",
        "- Keep the response concise and directly importable.",
        "- Respect the project brain as source of truth.",
        "",
        "CURRENT TASK:",
        task_line,
        "",
        "ACCEPTANCE CRITERIA:",
        "- Produce an importable response for the current task.",
        "- Preserve the existing project brain structure.",
        "- Update only the targeted brain files when imported.",
        "",
        "PREFERRED MODEL ROUTE:",
        preferred_route,
        "",
        "EXPECTED OUTPUT:",
        expected_output,
        "",
        "WRITE HANDOFF/STATUS TO:",
        f"- projects/{project_slug}/brain/handoffs.md",
        f"- projects/{project_slug}/brain/current_status.md",
        "",
        "RELEVANT BRAIN SUMMARY:",
        f"- current_status.md: {current_status.splitlines()[0] if current_status.splitlines() else ''}",
        f"- latest handoff: {latest_handoff}",
        "",
        "TASK PACKET:",
        source_packet,
        "",
        "IMPORT INSTRUCTIONS:",
        "- Paste Claude's response back into WHITE ROOM.",
        "- Save the response to a local file.",
        "- Import it with the manual import lane once available, targeting the appropriate brain file.",
    ]
    export_body = "\n".join(lines).strip() + "\n"
    token_estimate = estimate_tokens(export_body)
    while True:
        export_text = f"{export_body}\nTOKEN ESTIMATE: {token_estimate}\n"
        final_estimate = estimate_tokens(export_text)
        if final_estimate == token_estimate:
            return export_text
        token_estimate = final_estimate


def _fallback_packet_text(project_slug: str, task: dict[str, str]) -> str:
    return textwrap.dedent(
        f"""\
        PROJECT: {project_slug}
        TASK ID: {task['id']}
        TASK TITLE: {task['title']}
        SIZE CLASS: {task['size_class']}
        PREFERRED MODEL ROUTE: {task['preferred_tier']}

        GOAL:
        {task['goal']}
        """
    ).strip()


def _render_chat_export(
    *,
    project_slug: str,
    conversation_title: str,
    conversation_id: int,
    latest_session,
    messages,
    current_status: str,
) -> str:
    session_lane = latest_session.lane if latest_session is not None else "deterministic"
    session_id = latest_session.id if latest_session is not None else "n/a"
    attached_task_id = latest_session.attached_task_id if latest_session is not None else None
    attached_brain_files = ", ".join(latest_session.attached_brain_files) if latest_session is not None else "none"
    summary_lines = []
    for message in messages[-12:]:
        snippet = " ".join(message.content.split())
        if len(snippet) > 280:
            snippet = snippet[:277].rstrip() + "..."
        summary_lines.append(
            f"- {message.role} @ {message.created_at} [session {message.session_id}]: {snippet}"
        )
    if not summary_lines:
        summary_lines.append("- No messages recorded yet.")

    lines = [
        f"PROJECT: {project_slug}",
        f"CONVERSATION ID: {conversation_id}",
        f"CONVERSATION TITLE: {conversation_title}",
        "",
        "GOAL:",
        "Export the selected conversation context for manual Claude review, then import the pasted reply back into WHITE ROOM.",
        "",
        "FILES TO READ:",
        f"- projects/{project_slug}/brain/current_status.md",
        f"- projects/{project_slug}/brain/handoffs.md",
        f"- projects/{project_slug}/brain/tasks.md",
        "",
        "CONSTRAINTS:",
        "- Export only; no model calls from WHITE ROOM.",
        "- Keep the response paste-ready and local-first.",
        "- When imported, write at most one target brain file plus one handoff.",
        "",
        "CURRENT SESSION:",
        f"- session_id: {session_id}",
        f"- lane: {session_lane}",
        f"- attached_task_id: {attached_task_id if attached_task_id is not None else 'none'}",
        f"- attached_brain_files: {attached_brain_files}",
        "",
        "RECENT MESSAGES:",
        *summary_lines,
        "",
        "CURRENT STATUS:",
        current_status.strip(),
        "",
        "EXPECTED OUTPUT:",
        "A concise Claude response that can be pasted back into the manual import lane.",
        "",
        "IMPORT INSTRUCTIONS:",
        "- Paste the Claude response into the manual box.",
        "- Choose one target brain file if the reply should be written to disk.",
        "- Import it locally so WHITE ROOM records the assistant message and handoff.",
    ]
    export_body = "\n".join(lines).strip() + "\n"
    token_estimate = estimate_tokens(export_body)
    while True:
        export_text = f"{export_body}\nTOKEN ESTIMATE: {token_estimate}\n"
        final_estimate = estimate_tokens(export_text)
        if final_estimate == token_estimate:
            return export_text
        token_estimate = final_estimate


def _enforce_budget(size_class: str, token_estimate: int) -> None:
    budget = TOKEN_BUDGETS.get(size_class.lower())
    if budget is None:
        raise ValueError(f"unknown size class '{size_class}'")
    if token_estimate >= budget:
        raise ValueError(
            f"export for size class '{size_class}' is over budget: {token_estimate} >= {budget}"
        )
