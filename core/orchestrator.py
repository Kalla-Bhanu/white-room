from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.db import connect, init_db
from core.agents import record_agent_thread_step
from core.memory import append_handoff, get_project, update_brain_file_index, utc_now
from core.router import dry_run_route


@dataclass(frozen=True)
class OrchestrationResult:
    project_slug: str
    task_id: int
    task_title: str
    endpoint_class: str
    next_thread: str
    rationale: str
    handoff_id: int | None
    handoff_path: Path
    model_routes_path: Path


def orchestrate_one_step(project_slug: str) -> OrchestrationResult:
    project = get_project(project_slug)
    task_row = _next_open_task(project.id)
    if task_row is None:
        raise ValueError(f"project '{project_slug}' has no open tasks to advance")

    task_id = int(task_row["id"])
    route_result = dry_run_route(project_slug, task_id)
    next_thread = _thread_for_endpoint(route_result.endpoint_class)
    now = utc_now()

    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            UPDATE tasks
            SET status = ?, thread = ?, updated_at = ?
            WHERE id = ?
            """,
            ("in_progress", next_thread, now, task_id),
        )
        conn.commit()

    model_routes_path = project.path / "brain" / "model_routes.md"
    route_entry = (
        f"\n## Route {now} -- Orchestrate Step\n"
        f"- task: {task_id}\n"
        f"- title: {route_result.task.title}\n"
        f"- endpoint: {route_result.endpoint_class}\n"
        f"- next_thread: {next_thread}\n"
        f"- rationale: {route_result.rationale}\n"
    )
    model_routes_path.write_text(
        model_routes_path.read_text(encoding="utf-8") + route_entry,
        encoding="utf-8",
    )
    update_brain_file_index(project.slug, "model_routes.md")

    handoff_path = append_handoff(
        slug=project.slug,
        from_worker="orchestrator",
        to_worker=next_thread,
        summary=f"Advanced task {task_id} one step via {route_result.endpoint_class}.",
        artifact_paths=[
            "brain/tasks.md",
            "brain/model_routes.md",
            "brain/handoffs.md",
        ],
        task_id=task_id,
        thread_from="orchestrator",
        thread_to=next_thread,
    )
    handoff_id = _latest_handoff_id(project.id, task_id)
    record_agent_thread_step(
        project.slug,
        next_thread,
        task_id=task_id,
        handoff_id=handoff_id,
        state="active",
    )

    return OrchestrationResult(
        project_slug=project.slug,
        task_id=task_id,
        task_title=route_result.task.title,
        endpoint_class=route_result.endpoint_class,
        next_thread=next_thread,
        rationale=route_result.rationale,
        handoff_id=handoff_id,
        handoff_path=handoff_path,
        model_routes_path=model_routes_path,
    )


def _next_open_task(project_id: int):
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT id
            FROM tasks
            WHERE project_id = ? AND status = 'open'
            ORDER BY id ASC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
    return row


def _thread_for_endpoint(endpoint_class: str) -> str:
    if endpoint_class == "manual_claude":
        return "architecture"
    if endpoint_class in {
        "codex_lb",
        "ollama_local",
        "lmstudio_local",
        "openai_compatible_cloud",
        "anthropic_compatible_cloud",
        "provider_specific_cloud",
    }:
        return "implementation"
    return "implementation"


def _latest_handoff_id(project_id: int, task_id: int) -> int | None:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT id
            FROM handoffs
            WHERE project_id = ? AND task_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (project_id, task_id),
        ).fetchone()
    if row is None:
        return None
    return int(row["id"])
