from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any

from adapters.anthropic_compatible import AnthropicCompatibleAdapter
from adapters.codex_lb import CodexLBAdapter
from adapters.groq_cloud import GroqCloudAdapter
from adapters.lmstudio_local import LMStudioLocalAdapter
from adapters.manual_claude import ManualClaudeAdapter
from adapters.ollama_local import OllamaLocalAdapter
from adapters.openai_compatible import OpenAICompatibleAdapter
from adapters.provider_specific import ProviderSpecificAdapter
from core.approvals import gate_allows_action
from core.db import connect, init_db
from core.escalation import escalate_task_failure
from core.health import runner_status_snapshot
from core.route_log import record_route_decision
from core.secrets import get_secret
from core.usage import endpoint_is_at_limit


@dataclass(frozen=True)
class RouterTask:
    project_slug: str
    task_id: int
    title: str
    goal: str
    size_class: str
    preferred_tier: str
    packet_text: str
    token_estimate: int


@dataclass(frozen=True)
class RouteDryRunResult:
    route_decision_id: int
    endpoint_class: str
    request: dict[str, Any]
    preview: dict[str, Any]
    rationale: str
    task: RouterTask
    requires_approval: bool


@dataclass(frozen=True)
class RouteRunResult:
    route_decision_id: int
    mode: str
    endpoint_class: str
    request: dict[str, Any]
    preview: dict[str, Any]
    rationale: str
    task: RouterTask
    classification: TaskClassification
    approval_gate_id: int | None = None
    approval_status: str | None = None
    approval_message: str | None = None


@dataclass(frozen=True)
class TaskClassification:
    task_type: str
    size: str
    risk: str
    rationale: str


MODE_LANE_DEFAULTS: dict[str, tuple[str, ...]] = {
    "ask": ("ollama_local", "lmstudio_local", "manual_claude"),
    "plan": ("manual_claude",),
    "execute": ("codex_lb",),
    "review": ("manual_claude", "ollama_local", "lmstudio_local"),
    "summarize": ("ollama_local", "lmstudio_local", "manual_claude"),
    "route": ("manual_claude", "codex_lb"),
}

MODE_COST_ESTIMATES: dict[str, float] = {
    "manual_claude": 0.0,
    "codex_lb": 0.0,
    "ollama_local": 0.0,
    "lmstudio_local": 0.0,
    "openai_compatible_cloud": 0.02,
    "anthropic_compatible_cloud": 0.03,
    "provider_specific_cloud": 0.02,
    "gemini_compatible_cloud": 0.02,
    "deepseek_compatible_cloud": 0.02,
    "openrouter_cloud": 0.02,
    "groq_cloud": 0.01,
    "opencode_compatible_cloud": 0.02,
}


def load_task(project_slug: str, task_id: int) -> RouterTask:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT
                p.slug AS project_slug,
                t.id AS task_id,
                t.title AS title,
                t.goal AS goal,
                t.size_class AS size_class,
                t.preferred_tier AS preferred_tier,
                tp.packet_text AS packet_text,
                tp.token_estimate AS token_estimate
            FROM tasks AS t
            JOIN projects AS p ON p.id = t.project_id
            LEFT JOIN task_packets AS tp ON tp.task_id = t.id
            WHERE p.slug = ? AND t.id = ?
            ORDER BY tp.generated_at DESC, tp.id DESC
            LIMIT 1
            """,
            (project_slug, task_id),
        ).fetchone()

    if row is None:
        raise ValueError(f"task '{task_id}' does not exist for project '{project_slug}'")

    return RouterTask(
        project_slug=str(row["project_slug"]),
        task_id=int(row["task_id"]),
        title=str(row["title"]),
        goal=str(row["goal"]),
        size_class=str(row["size_class"]),
        preferred_tier=str(row["preferred_tier"]),
        packet_text=str(row["packet_text"] or ""),
        token_estimate=int(row["token_estimate"] or 0),
    )


def dry_run_route(
    project_slug: str,
    task_id: int,
    mode: str = "ask",
    lane_override: str | None = None,
) -> RouteDryRunResult:
    task = load_task(project_slug, task_id)
    classification = classify_dimensions(task)
    endpoint_class, rationale, fallback_reason = choose_route_lane(task, classification, mode=mode, lane_override=lane_override)
    adapter = _adapter_for_endpoint(endpoint_class)
    request = adapter.prepare(_task_payload(task))
    preview = adapter.dry_run(request)
    route_decision = record_route_decision(
        project_slug=task.project_slug,
        task_id=task.task_id,
        task_type=classification.task_type,
        risk=classification.risk,
        size=classification.size,
        mode=mode,
        chosen_lane=endpoint_class,
        explanation=rationale,
        source="dry_run",
        requires_approval=_requires_approval(endpoint_class),
        is_preview=False,
        chosen_endpoint_id=_endpoint_id_for_class(endpoint_class),
        est_cost_usd=estimate_route_cost_usd(endpoint_class, classification),
        candidates=_candidate_rows(
            endpoint_class,
            rationale,
            classification.task_type,
            mode,
            fallback_reason=fallback_reason,
        ),
    )
    return RouteDryRunResult(
        route_decision_id=route_decision.id,
        endpoint_class=endpoint_class,
        request=request,
        preview=preview,
        rationale=rationale,
        task=task,
        requires_approval=_requires_approval(endpoint_class),
    )


def preview_route(
    project_slug: str,
    task_id: int,
    mode: str = "ask",
    lane_override: str | None = None,
) -> RouteDryRunResult:
    task = load_task(project_slug, task_id)
    classification = classify_dimensions(task)
    endpoint_class, rationale, fallback_reason = choose_route_lane(task, classification, mode=mode, lane_override=lane_override)
    adapter = _adapter_for_endpoint(endpoint_class)
    request = adapter.prepare(_task_payload(task))
    preview = adapter.dry_run(request)
    route_decision = record_route_decision(
        project_slug=task.project_slug,
        task_id=task.task_id,
        task_type=classification.task_type,
        risk=classification.risk,
        size=classification.size,
        mode=mode,
        chosen_lane=endpoint_class,
        explanation=rationale,
        source="api_preview",
        status="suggested",
        requires_approval=_requires_approval(endpoint_class),
        is_preview=True,
        chosen_endpoint_id=_endpoint_id_for_class(endpoint_class),
        est_cost_usd=estimate_route_cost_usd(endpoint_class, classification),
        candidates=_candidate_rows(
            endpoint_class,
            rationale,
            classification.task_type,
            mode,
            fallback_reason=fallback_reason,
        ),
    )
    return RouteDryRunResult(
        route_decision_id=route_decision.id,
        endpoint_class=endpoint_class,
        request=request,
        preview=preview,
        rationale=rationale,
        task=task,
        requires_approval=_requires_approval(endpoint_class),
    )


def run_route(
    project_slug: str,
    task_id: int,
    mode: str = "ask",
    lane_override: str | None = None,
) -> RouteRunResult:
    task = load_task(project_slug, task_id)
    classification = classify_dimensions(task)
    endpoint_class, rationale, fallback_reason = choose_route_lane(task, classification, mode=mode, lane_override=lane_override)
    adapter = _adapter_for_endpoint(endpoint_class)
    request = adapter.prepare(_task_payload(task))
    target_endpoint_id = _endpoint_id_for_class(endpoint_class)
    approval_gate_id: int | None = None
    approval_status: str | None = None
    approval_message: str | None = None
    decision_status = "suggested"
    needs_approval = _requires_approval(endpoint_class)
    execute_allowed = mode.lower() in {"ask", "summarize"} and not needs_approval
    if needs_approval and mode.lower() != "route":
        gate_allows, gate, approval_message = gate_allows_action(
            project_slug=task.project_slug,
            action_type="route_run",
            target_endpoint_id=target_endpoint_id,
            payload_summary=_approval_payload_summary(task, endpoint_class, classification, mode),
            endpoint_class=endpoint_class,
            mode=mode,
            risk=classification.risk,
            est_cost_usd=estimate_route_cost_usd(endpoint_class, classification),
        )
        approval_gate_id = gate.id if gate.id > 0 else None
        approval_status = gate.status
        decision_status = "suggested" if gate_allows else "needs approval"
        if not gate_allows:
            preview = adapter.dry_run(request)
            route_decision = record_route_decision(
                project_slug=task.project_slug,
                task_id=task.task_id,
                task_type=classification.task_type,
                risk=classification.risk,
                size=classification.size,
                mode=mode,
                chosen_lane=endpoint_class,
                explanation=rationale,
                source="task",
                requires_approval=True,
                is_preview=False,
                chosen_endpoint_id=target_endpoint_id,
                est_cost_usd=estimate_route_cost_usd(endpoint_class, classification),
                candidates=_candidate_rows(
                    endpoint_class,
                    rationale,
                    classification.task_type,
                    mode,
                    fallback_reason=fallback_reason,
                ),
                status=decision_status,
            )
            return RouteRunResult(
                route_decision_id=route_decision.id,
                mode=mode,
                endpoint_class=endpoint_class,
                request=request,
                preview=preview,
                rationale=rationale,
                task=task,
                classification=classification,
                approval_gate_id=approval_gate_id,
                approval_status=approval_status,
                approval_message=approval_message,
            )
    route_decision = record_route_decision(
        project_slug=task.project_slug,
        task_id=task.task_id,
        task_type=classification.task_type,
        risk=classification.risk,
        size=classification.size,
        mode=mode,
        chosen_lane=endpoint_class,
        explanation=rationale,
        source="task",
        requires_approval=needs_approval,
        is_preview=False,
        chosen_endpoint_id=target_endpoint_id,
        est_cost_usd=estimate_route_cost_usd(endpoint_class, classification),
        candidates=_candidate_rows(
            endpoint_class,
            rationale,
            classification.task_type,
            mode,
            fallback_reason=fallback_reason,
        ),
        status=decision_status,
    )

    if mode.lower() == "route":
        preview = adapter.dry_run(request)
    elif execute_allowed and _should_auto_execute(endpoint_class, classification.task_type):
        try:
            preview = adapter.call(request)
        except Exception as exc:
            escalate_task_failure(
                project_slug=project_slug,
                task_id=task_id,
                description=f"auto execution failed for {endpoint_class}: {exc}",
                current_tier=task.preferred_tier,
            )
            raise RuntimeError(f"auto execution failed for {endpoint_class}: {exc}") from exc
    else:
        preview = adapter.dry_run(request)

    return RouteRunResult(
        route_decision_id=route_decision.id,
        mode=mode,
        endpoint_class=endpoint_class,
        request=request,
        preview=preview,
        rationale=rationale,
        task=task,
        classification=classification,
        approval_gate_id=approval_gate_id,
        approval_status=approval_status,
        approval_message=approval_message,
    )


def classify_dimensions(task: RouterTask) -> TaskClassification:
    text = f"{task.title} {task.goal}".lower()
    task_type = _classify_task_type(text)
    size = task.size_class.lower()
    risk = _classify_risk(text, size, task.preferred_tier.lower())
    rationale = (
        f"task type '{task_type}' from title/goal keywords, "
        f"size '{size}' from task metadata, risk '{risk}' from routing keywords"
    )
    return TaskClassification(
        task_type=task_type,
        size=size,
        risk=risk,
        rationale=rationale,
    )


def classify_task(task: RouterTask) -> tuple[str, str]:
    text = f"{task.title} {task.goal}".lower()
    if any(keyword in text for keyword in ("decision", "manual claude", "import", "export")):
        return "manual_claude", "manual review or import work routes to the manual Claude lane"
    if any(keyword in text for keyword in ("benchmark", "fixture", "adapter", "server", "route", "ui")):
        return "codex_lb", "implementation work routes to the execution lane"
    if task.preferred_tier.lower() in {"execution", "codex"}:
        return "codex_lb", "preferred tier indicates execution lane"
    return "manual_claude", "deterministic fallback prefers the manual Claude lane"


def choose_route_lane(
    task: RouterTask,
    classification: TaskClassification,
    mode: str = "ask",
    lane_override: str | None = None,
) -> tuple[str, str, str | None]:
    normalized_mode = (mode or "ask").strip().lower() or "ask"
    preferred_lane, rationale = _mode_preferred_lane(task, classification, normalized_mode)
    chosen_lane = preferred_lane
    explanation = rationale
    fallback_reason: str | None = None
    if normalized_mode == "execute" and (lane_override is None or lane_override == "auto" or lane_override == "codex_lb"):
        codex_available, codex_reason = _codex_lb_available()
        if not codex_available:
            chosen_lane = "manual_claude"
            fallback_reason = codex_reason
            explanation = f"{rationale}; codex_lb unavailable ({codex_reason}), falling back to Manual Claude"
    if lane_override == "groq_cloud":
        groq_available, groq_reason = _groq_cloud_available()
        chosen_lane = "groq_cloud" if groq_available else "manual_claude"
        fallback_reason = None if groq_available else groq_reason
        explanation = (
            f"{rationale}; lane override '{lane_override}' applied"
            if groq_available
            else f"{rationale}; lane override '{lane_override}' applied but groq_cloud unavailable ({groq_reason}), falling back to Manual Claude"
        )
    if lane_override and lane_override != "auto":
        chosen_lane = lane_override
        explanation = f"{rationale}; lane override '{lane_override}' applied"
        if lane_override == "codex_lb":
            codex_available, codex_reason = _codex_lb_available()
            if not codex_available:
                chosen_lane = "manual_claude"
                fallback_reason = codex_reason
                explanation = (
                    f"{rationale}; lane override '{lane_override}' applied but codex_lb unavailable "
                    f"({codex_reason}), falling back to Manual Claude"
                )
        if lane_override == "groq_cloud":
            groq_available, groq_reason = _groq_cloud_available()
            if not groq_available:
                chosen_lane = "manual_claude"
                fallback_reason = groq_reason
                explanation = (
                    f"{rationale}; lane override '{lane_override}' applied but groq_cloud unavailable "
                    f"({groq_reason}), falling back to Manual Claude"
                )
    return chosen_lane, explanation, fallback_reason


def estimate_route_cost_usd(endpoint_class: str, classification: TaskClassification) -> float | None:
    base = MODE_COST_ESTIMATES.get(endpoint_class)
    if base is None:
        return None
    if classification.size == "large":
        return round(base * 1.5, 4)
    if classification.size == "medium":
        return round(base * 1.2, 4)
    return round(base, 4)


def _mode_preferred_lane(task: RouterTask, classification: TaskClassification, mode: str) -> tuple[str, str]:
    normalized_mode = (mode or "ask").strip().lower() or "ask"
    if normalized_mode == "plan":
        return "manual_claude", "Plan prefers Manual Claude for deep planning"
    if normalized_mode == "execute":
        return "codex_lb", "Execute prefers Codex LB and stays behind a manual packet gate"
    if normalized_mode == "review":
        local_lane = _best_local_lane(classification.task_type)
        if local_lane is not None:
            return local_lane, "Review uses a local pre-check lane when reachable"
        return "manual_claude", "Review falls back to Manual Claude"
    if normalized_mode == "summarize":
        local_lane = _best_local_lane(classification.task_type)
        if local_lane is not None:
            return local_lane, "Summarize prefers the cheapest reachable local lane"
        return "manual_claude", "Summarize falls back to Manual Claude"
    if normalized_mode == "route":
        return classify_task(task)
    local_lane = _best_local_lane(classification.task_type)
    if local_lane is not None:
        return local_lane, "Ask prefers the cheapest capable reachable local lane"
    return "manual_claude", "Ask falls back to Manual Claude"


def _best_local_lane(task_type: str) -> str | None:
    snapshot = runner_status_snapshot()
    reachable = {
        str(row.get("endpoint_class"))
        for row in snapshot.get("endpoints", [])
        if bool(row.get("reachable"))
    }
    for lane in ("ollama_local", "lmstudio_local"):
        if lane in reachable and _has_verified_benchmark(task_type) and not endpoint_is_at_limit(lane.replace("_", "-")):
            return lane
    return None


def _task_payload(task: RouterTask) -> dict[str, Any]:
    return {
        "project_slug": task.project_slug,
        "task_id": task.task_id,
        "title": task.title,
        "goal": task.goal,
        "size_class": task.size_class,
        "preferred_tier": task.preferred_tier,
        "packet_text": task.packet_text,
        "token_estimate": task.token_estimate,
    }


def _approval_payload_summary(task: RouterTask, endpoint_class: str, classification: TaskClassification, mode: str) -> str:
    payload = {
        "project_slug": task.project_slug,
        "task_id": task.task_id,
        "endpoint_class": endpoint_class,
        "task_type": classification.task_type,
        "risk": classification.risk,
        "size": classification.size,
        "mode": mode,
        "title": task.title,
    }
    return json.dumps(payload, sort_keys=True)


def _adapter_for_endpoint(endpoint_class: str):
    if endpoint_class == "manual_claude":
        return ManualClaudeAdapter()
    if endpoint_class == "codex_lb":
        return CodexLBAdapter()
    if endpoint_class == "ollama_local":
        return OllamaLocalAdapter()
    if endpoint_class == "lmstudio_local":
        return LMStudioLocalAdapter()
    if endpoint_class == "openai_compatible_cloud":
        return OpenAICompatibleAdapter()
    if endpoint_class == "anthropic_compatible_cloud":
        return AnthropicCompatibleAdapter()
    if endpoint_class == "groq_cloud":
        return GroqCloudAdapter()
    return ProviderSpecificAdapter()


def _classify_task_type(text: str) -> str:
    if any(keyword in text for keyword in ("benchmark", "fixture", "score", "verify")):
        return "benchmark"
    if any(keyword in text for keyword in ("decision", "import", "export", "handoff", "manual")):
        return "manual"
    if any(keyword in text for keyword in ("route", "router", "classify", "escalation")):
        return "routing"
    if any(keyword in text for keyword in ("server", "dashboard", "ui", "screen")):
        return "ui"
    return "implementation"


def _classify_risk(text: str, size: str, preferred_tier: str) -> str:
    if any(keyword in text for keyword in ("cloud", "provider", "openrouter", "gemini", "groq", "anthropic", "openai")):
        return "high"
    if any(keyword in text for keyword in ("benchmark", "server", "ui", "route", "execution", "local")):
        return "medium"
    if any(keyword in text for keyword in ("decision", "manual", "import", "export", "handoff")):
        return "low"
    if preferred_tier in {"execution", "codex"}:
        return "medium"
    if size in {"large", "medium"}:
        return "medium"
    return "low"


def _should_auto_execute(endpoint_class: str, task_type: str) -> bool:
    if endpoint_class not in {"ollama_local", "lmstudio_local"}:
        return False
    endpoint_name = endpoint_class.replace("_", "-")
    return _has_verified_benchmark(task_type) and not endpoint_is_at_limit(endpoint_name)


def _requires_approval(endpoint_class: str) -> bool:
    return endpoint_class in {
        "codex_lb",
        "groq_cloud",
        "openai_compatible_cloud",
        "anthropic_compatible_cloud",
        "provider_specific_cloud",
    }


def _endpoint_id_for_class(endpoint_class: str) -> int | None:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            "SELECT id FROM endpoints WHERE endpoint_class = ? ORDER BY id ASC LIMIT 1",
            (endpoint_class,),
        ).fetchone()
    if row is None:
        return None
    return int(row["id"])


def _candidate_rows(
    endpoint_class: str,
    rationale: str,
    task_type: str,
    mode: str = "ask",
    *,
    fallback_reason: str | None = None,
) -> list[dict[str, Any]]:
    alternate = "manual_claude" if endpoint_class != "manual_claude" else "codex_lb"
    if fallback_reason and endpoint_class == "manual_claude":
        alternate = "codex_lb"
    candidates = [
        {
            "endpoint_class": endpoint_class,
            "selected": True,
            "reason": rationale,
            "reject_reason": "",
        },
        {
            "endpoint_class": alternate,
            "selected": False,
            "reason": f"fallback lane for {task_type} tasks in {mode} mode",
            "reject_reason": fallback_reason or f"lower priority than {endpoint_class}",
        },
    ]
    if endpoint_class != "groq_cloud":
        groq_available, groq_reason = _groq_cloud_available()
        if groq_available:
            candidates.append(
                {
                    "endpoint_class": "groq_cloud",
                    "selected": False,
                    "reason": f"gated cloud lane for {task_type} tasks in {mode} mode",
                    "reject_reason": f"gated cloud lane; {groq_reason}" if groq_reason else "gated cloud lane",
                }
            )
    return candidates


def _codex_lb_available() -> tuple[bool, str | None]:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT e.id, e.name, e.status, e.window_used, e.window_limit, e.window_reset_at, er.cooldown_until
            FROM endpoints AS e
            LEFT JOIN endpoint_runtime AS er ON er.endpoint_id = e.id
            WHERE e.endpoint_class = 'codex_lb'
            ORDER BY e.id ASC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return False, "codex-lb endpoint not configured"

    endpoint_name = str(row["name"])
    if _timestamp_in_future(row["cooldown_until"]):
        return False, f"{endpoint_name} is cooling down until {row['cooldown_until']}"
    if endpoint_is_at_limit(endpoint_name):
        return False, f"{endpoint_name} is at limit"
    status = str(row["status"] or "").strip().lower()
    if status in {"disabled", "offline", "degraded"}:
        return False, f"{endpoint_name} status is {status or 'unknown'}"
    return True, None


def _groq_cloud_available() -> tuple[bool, str | None]:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT e.id, e.name, e.status, e.base_url, er.cooldown_until
            FROM endpoints AS e
            LEFT JOIN endpoint_runtime AS er ON er.endpoint_id = e.id
            WHERE e.endpoint_class = 'groq_cloud'
            ORDER BY e.id ASC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return False, "groq_cloud endpoint not configured"
    if not get_secret("GROQ_API_KEY"):
        return False, "GROQ_API_KEY missing"
    if not str(row["base_url"] or "").strip():
        return False, "groq_cloud base URL missing"
    if _timestamp_in_future(row["cooldown_until"]):
        return False, f"{row['name']} is cooling down until {row['cooldown_until']}"
    status = str(row["status"] or "").strip().lower()
    if status in {"disabled", "offline", "degraded"}:
        return False, f"{row['name']} status is {status or 'unknown'}"
    return True, None


def _timestamp_in_future(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    try:
        instant = datetime.fromisoformat(text)
    except ValueError:
        return False
    return instant > datetime.now(timezone.utc)


def _has_verified_benchmark(task_type: str) -> bool:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT 1
            FROM benchmarks
            WHERE task_type = ? AND verified = 1
            LIMIT 1
            """,
            (task_type,),
        ).fetchone()
    return row is not None
