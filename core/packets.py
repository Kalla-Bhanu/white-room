from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

from core.db import connect, init_db
from core.memory import get_project, update_brain_file_index, utc_now


@dataclass(frozen=True)
class CreatedPacket:
    task_id: int
    packet_id: int
    path: Path
    token_estimate: int


def create_packet(
    slug: str,
    title: str,
    goal: str,
    size_class: str,
    preferred_route: str,
    expected_output: str,
    acceptance: str,
) -> CreatedPacket:
    project = get_project(slug)
    created_at = utc_now()
    packet_text = _render_packet(
        project_slug=project.slug,
        title=title,
        goal=goal,
        size_class=size_class,
        preferred_route=preferred_route,
        expected_output=expected_output,
        acceptance=acceptance,
    )
    token_estimate = estimate_tokens(packet_text)

    with connect() as conn:
        init_db(conn)
        cursor = conn.execute(
            """
            INSERT INTO tasks
                (project_id, title, goal, status, size_class, preferred_tier, thread, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project.id,
                title,
                goal,
                "open",
                size_class,
                preferred_route,
                "orchestrator",
                created_at,
                created_at,
            ),
        )
        task_id = int(cursor.lastrowid)
        cursor = conn.execute(
            """
            INSERT INTO task_packets
                (task_id, packet_text, token_estimate, generated_at, model_route, expected_output)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id, packet_text, token_estimate, created_at, preferred_route, expected_output),
        )
        packet_id = int(cursor.lastrowid)
        conn.commit()

    packet_path = project.path / "packets" / f"packet-{task_id:03d}-{_slugify(title)}.md"
    packet_path.write_text(packet_text, encoding="utf-8")
    _append_task(project.path / "brain" / "tasks.md", task_id, title, size_class, preferred_route)
    update_brain_file_index(project.slug, "tasks.md")

    return CreatedPacket(
        task_id=task_id,
        packet_id=packet_id,
        path=packet_path,
        token_estimate=token_estimate,
    )


def estimate_tokens(text: str) -> int:
    return max(1, len(text.split()) * 4 // 3)


def _render_packet(
    project_slug: str,
    title: str,
    goal: str,
    size_class: str,
    preferred_route: str,
    expected_output: str,
    acceptance: str,
) -> str:
    return textwrap.dedent(
        f"""\
        PROJECT: {project_slug}
        TASK TITLE: {title}
        SIZE CLASS: {size_class}
        PREFERRED MODEL ROUTE: {preferred_route}

        GOAL:
        {goal}

        CONSTRAINTS:
        - Use the project brain as source of truth.
        - Keep changes scoped to this task.
        - Do not add model calls, provider SDKs, chat UI, or dashboard unless the task explicitly asks.
        - Append a handoff and update current status after implementation.

        ACCEPTANCE CRITERIA:
        {acceptance}

        EXPECTED OUTPUT:
        {expected_output}
        """
    )


def _append_task(tasks_path: Path, task_id: int, title: str, size_class: str, route: str) -> None:
    entry = f"\n- [ ] Task {task_id:03d}: {title} ({size_class}, route: {route})\n"
    content = tasks_path.read_text(encoding="utf-8")
    done_marker = "\n## Done"
    if done_marker in content:
        content = content.replace(done_marker, entry + done_marker, 1)
    else:
        content = content.rstrip() + entry
    tasks_path.write_text(content, encoding="utf-8")


def _slugify(value: str) -> str:
    slug = value.strip().lower().replace(" ", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "packet"
