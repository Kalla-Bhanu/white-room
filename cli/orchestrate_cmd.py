from __future__ import annotations

from core.orchestrator import orchestrate_one_step


def run_orchestrate(project_slug: str, step: bool) -> str:
    if not step:
        raise ValueError("orchestrate requires --step in this phase")

    result = orchestrate_one_step(project_slug)
    return (
        f"step orchestrator: task {result.task_id} ({result.task_title}) -> {result.next_thread} "
        f"via {result.endpoint_class}"
    )
