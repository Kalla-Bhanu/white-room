from __future__ import annotations

import json
import os
import secrets
from datetime import timedelta
from urllib.parse import parse_qs
from urllib.parse import quote
from urllib.parse import urlparse
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core.approvals import create_approval_grant, decide_approval_gate, gate_allows_action, get_approval_gate
from core.agents import list_agent_threads
from core.db import connect, init_db
from core.chat import (
    attach_task_to_conversation,
    create_conversation,
    create_chat_session,
    delete_conversation,
    draft_task_packet_from_turn,
    get_conversation,
    get_latest_session,
    get_or_create_first,
    list_conversations,
    list_messages,
    save_message,
    set_conversation_pinned,
    _chunk_text_for_stream,
    stream_local_chat_turn,
)
from core.chat_format import render_message_html
from adapters.codex_lb import CodexLBAdapter
from adapters.groq_cloud import GroqCloudAdapter
from core.codex_lane import export_codex_execution_packet, import_codex_execution_response, record_execution_run
from core.codex_modes import codex_mode_catalog
from core import health as health_runtime
from core.home import resolve_home_resolution
from core.health import runner_status_snapshot, topbar_health_summary
from core.endpoints import add_endpoint, list_endpoints
from core.endpoints import update_endpoint
from core.route_log import latest_route_decision_for_conversation, list_route_decisions, record_route_decision
from core.manual_lane import (
    TARGET_BRAIN_FILES,
    export_manual_chat_packet,
    import_manual_chat_response,
    import_manual_claude_output,
)
from core.packets import create_packet
from core.memory import get_project, list_projects, utc_now
from core.providers import PROVIDER_PROFILE_SEEDS
from core.onboarding import build_onboarding_state
from core.ui_preferences import (
    current_theme,
    get_ui_preference,
    get_ui_preferences,
    set_ui_preference,
    theme_render_value,
    theme_toggle_value,
)
from core.health import health_check, sync_models
from core.secrets import delete_secret, get_secret, key_fingerprint, reload as reload_secrets, set_secret
from core.orchestrator import orchestrate_one_step
from core.router import preview_route, run_route
from core.usage import usage_summary
from core.providers import provider_lane_options
from core.projects import ProjectExistsError, create_project, delete_project
from core.models_catalog import list_endpoint_models
from adapters.openai_compatible import resolve_url


BRAIN_FILE_ORDER = [
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
]

GROQ_CLOUD_DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
_BLOCKED_PROVIDER_PATH_FRAGMENTS = ("/dashboard", "/settings", "/login", "/metrics")
GROQ_CHAT_MODEL_PREFERENCE = (
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "openai/gpt-oss-120b",
    "qwen/qwen3-32b",
    "groq/compound",
    "groq/compound-mini",
)

STATIC_ENDPOINT_ROWS = [
    {
        "name": "Manual Claude",
        "endpoint_class": "manual_claude",
        "tier": "manual",
        "status": "available",
        "base_url": "copy/paste",
        "capabilities": "planning, review, conflict resolution",
        "daily_limit": "manual",
        "window_limit": "manual",
    },
    {
        "name": "Codex",
        "endpoint_class": "codex_lb",
        "tier": "execution",
        "status": "available",
        "base_url": "manual trigger",
        "capabilities": "execution, hard debugging",
        "daily_limit": "manual",
        "window_limit": "manual",
    },
    {
        "name": "Ollama Local",
        "endpoint_class": "ollama_local",
        "tier": "local",
        "status": "metadata-only",
        "base_url": "localhost",
        "capabilities": "drafts, summarization, extraction",
        "daily_limit": "local machine",
        "window_limit": "local machine",
    },
    {
        "name": "LM Studio Local",
        "endpoint_class": "lmstudio_local",
        "tier": "local",
        "status": "metadata-only",
        "base_url": "localhost",
        "capabilities": "drafts, summarization, extraction",
        "daily_limit": "local machine",
        "window_limit": "local machine",
    },
    {
        "name": "OpenAI Compatible Cloud",
        "endpoint_class": "openai_compatible_cloud",
        "tier": "cloud",
        "status": "metadata-only",
        "base_url": "varies",
        "capabilities": "overflow helper work",
        "daily_limit": "published limit",
        "window_limit": "published limit",
    },
    {
        "name": "Anthropic Compatible Cloud",
        "endpoint_class": "anthropic_compatible_cloud",
        "tier": "cloud",
        "status": "metadata-only",
        "base_url": "varies",
        "capabilities": "planning, review",
        "daily_limit": "published limit",
        "window_limit": "published limit",
    },
    {
        "name": "Provider Specific Cloud",
        "endpoint_class": "provider_specific_cloud",
        "tier": "cloud",
        "status": "metadata-only",
        "base_url": "varies",
        "capabilities": "overflow drafts",
        "daily_limit": "published limit",
        "window_limit": "published limit",
    },
]

PROJECT_ACCENT_CLASSES = ("accent-2", "accent-3", "accent-4", "accent-5", "accent")


WEB_ROOT = Path(__file__).resolve().parent
STATIC_DIR = WEB_ROOT / "static"
templates = Jinja2Templates(directory=str(WEB_ROOT / "templates"))
templates.env.globals["all_projects"] = list_projects
templates.env.globals["ui_preferences"] = get_ui_preferences


def asset_url(filename: str) -> str:
    clean_name = filename.lstrip("/").replace("\\", "/")
    path = STATIC_DIR / clean_name
    try:
        version = int(path.stat().st_mtime_ns)
    except OSError:
        version = 0
    return f"/static/{clean_name}?v={version}"


templates.env.globals["asset_url"] = asset_url

app = FastAPI(title="WHITE ROOM", docs_url=None, redoc_url=None, openapi_url=None)
ROUTE_RESULT_CACHE: dict[str, dict[str, object]] = {}
IMPORT_RESULT_CACHE: dict[str, dict[str, object]] = {}
app.mount("/static", StaticFiles(directory=str(WEB_ROOT / "static")), name="static")


@app.get("/", response_class=HTMLResponse, response_model=None)
def home(request: Request):
    resolution = resolve_home_resolution()
    if resolution.kind == "onboarding":
        return templates.TemplateResponse(
            request=request,
            name="onboarding.html",
            context={
                "request": request,
                "onboarding": build_onboarding_state(request.query_params.get("state")),
                "page_title": "Onboarding",
                "page_meta": "Chat-first workspace setup.",
                **_ui_context(),
            },
        )
    if resolution.target is None:
        raise HTTPException(status_code=404, detail="home destination unavailable")
    return RedirectResponse(url=resolution.target, status_code=302)


@app.get("/onboarding", response_class=HTMLResponse)
def onboarding(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="onboarding.html",
        context={
            "request": request,
            "onboarding": build_onboarding_state(request.query_params.get("state")),
            "page_title": "Onboarding",
            "page_meta": "Guided preview states for the local cockpit.",
            **_ui_context(),
        },
    )


@app.get("/projects", response_class=HTMLResponse)
def projects_index(request: Request) -> HTMLResponse:
    projects = list_projects()
    active_project = _resolve_active_project(projects)
    endpoints = _endpoint_rows()
    return templates.TemplateResponse(
        request=request,
        name="projects.html",
        context={
            "request": request,
            "projects": projects,
            "project_count": len(projects),
            "active_project": active_project,
            "active_tasks": _list_project_tasks(active_project.id) if active_project else [],
            "active_packets": _list_project_packets(active_project.id) if active_project else [],
            "endpoints": endpoints,
            "endpoint_count": len(endpoints),
            "page_title": "Projects",
            "page_meta": "Local AI workbench home.",
            **_ui_context(),
        },
    )


@app.get("/project/{slug}", response_class=HTMLResponse)
def project_detail(request: Request, slug: str) -> HTMLResponse:
    try:
        project = get_project(slug)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    brain_files = [_read_brain_file(project.path / "brain" / filename) for filename in BRAIN_FILE_ORDER]
    current_status = next((item["content"] for item in brain_files if item["name"] == "current_status.md"), "")
    project_tasks = _list_project_tasks(project.id)
    recent_handoffs = _list_handoffs(project.id)
    return templates.TemplateResponse(
        request=request,
        name="project_home.html",
        context={
            "request": request,
            "project": project,
            "brain_files": brain_files,
            "brain_file_count": len(brain_files),
            "current_status": current_status,
            "project_tasks": project_tasks,
            "task_count": len(project_tasks),
            "open_task_count": sum(1 for task in project_tasks if task["status"] != "done"),
            "next_task": _next_project_task(project_tasks),
            "recent_handoffs": recent_handoffs[:6],
            "handoff_count": len(recent_handoffs),
            "health_snapshot": runner_status_snapshot(),
            "page_title": f"{project.slug} home",
            "page_meta": project.one_line_purpose,
            **_ui_context(),
        },
    )


@app.get("/board", response_class=HTMLResponse, include_in_schema=False)
def board_root(request: Request) -> HTMLResponse:
    projects = list_projects()
    project = projects[0] if projects else None
    if project is None:
        raise HTTPException(status_code=404, detail="no projects available")
    return board(request, project.slug)


@app.get("/board/{slug}", response_class=HTMLResponse)
def board(request: Request, slug: str) -> HTMLResponse:
    try:
        project = get_project(slug)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    created_task = request.query_params.get("created_task")
    created_packet = request.query_params.get("created_packet")
    notice = None
    if created_task and created_packet:
        notice = f"created task {created_task} and packet {created_packet}"

    return templates.TemplateResponse(
        request=request,
        name="board.html",
        context=_board_context(
            request=request,
            project=project,
            notice=notice,
            error=None,
            created=None,
        ),
    )


@app.get("/chat/{slug}", response_class=HTMLResponse)
def chat(request: Request, slug: str) -> HTMLResponse:
    try:
        project = get_project(slug)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    projects = list_projects()
    conversations = list_conversations(slug)
    if not conversations:
        get_or_create_first(slug)
        conversations = list_conversations(slug)

    selected_conversation_id = request.query_params.get("conversation_id")
    selected_conversation = _resolve_chat_conversation(conversations, selected_conversation_id)
    selected_task_id = request.query_params.get("task")
    selected_lane_override = request.query_params.get("lane_override") or request.query_params.get("lane")
    selected_mode_override = request.query_params.get("mode") or request.query_params.get("chat_mode")
    latest_session = get_latest_session(selected_conversation.id)
    task_context = None
    if selected_task_id:
        selected_task = _resolve_task_selection(project.id, selected_task_id)
        if not selected_task["id"]:
            raise HTTPException(status_code=404, detail=f"task {selected_task_id} does not exist")
        attached_brain_files = ["current_status.md", "tasks.md", "handoffs.md"]
        latest_session = attach_task_to_conversation(
            selected_conversation.id,
            int(selected_task["id"]),
            lane=(latest_session.lane if latest_session is not None else selected_conversation.mode_default),
            attached_brain_files=attached_brain_files,
        )
        selected_packet = next(
            (packet for packet in _list_project_packets(project.id) if packet["task_id"] == selected_task["id"]),
            None,
        )
        task_context = {
            "task": selected_task,
            "packet": selected_packet,
            "attached_brain_files": attached_brain_files,
            "handoff": _list_handoffs(project.id)[0] if _list_handoffs(project.id) else None,
            "conversation": {
                "id": selected_conversation.id,
                "title": selected_conversation.title,
            },
        }
    messages = list_messages(selected_conversation.id)
    latest_route_decision = latest_route_decision_for_conversation(selected_conversation.id)

    return templates.TemplateResponse(
        request=request,
        name="cockpit.html",
        context=_chat_context(
            request=request,
            project=project,
            projects=projects,
            conversations=conversations,
            selected_conversation=selected_conversation,
            latest_session=latest_session,
            messages=messages,
            runner_snapshot=runner_status_snapshot(),
            latest_route_decision=latest_route_decision,
            task_context=task_context,
            selected_lane_override=selected_lane_override,
            selected_mode_override=selected_mode_override,
        ),
    )


@app.post("/projects/create", response_class=HTMLResponse, response_model=None)
async def create_project_from_sidebar(request: Request) -> RedirectResponse | HTMLResponse:
    payload = await _read_payload(request)
    name = str(payload.get("name") or "").strip()
    if not name:
        return RedirectResponse(url="/projects", status_code=303)
    try:
        project = create_project(name)
    except (ProjectExistsError, ValueError):
        return RedirectResponse(url="/projects", status_code=303)
    return RedirectResponse(url=f"/chat/{project.slug}", status_code=303)


@app.post("/projects/{slug}/delete", response_class=HTMLResponse, response_model=None)
def delete_project_from_sidebar(request: Request, slug: str) -> RedirectResponse:
    try:
        delete_project(slug)
    except ValueError:
        return RedirectResponse(url="/projects", status_code=303)
    remaining_projects = list_projects()
    if remaining_projects:
        return RedirectResponse(url=f"/chat/{remaining_projects[0].slug}", status_code=303)
    return RedirectResponse(url="/onboarding", status_code=303)


@app.post("/chat/{slug}/conversations/create", response_class=HTMLResponse, response_model=None)
async def create_chat_conversation(request: Request, slug: str) -> RedirectResponse:
    payload = await _read_payload(request)
    title = str(payload.get("title") or "").strip() or "New conversation"
    try:
        conversation, _session = create_conversation(slug, title, lane="auto")
    except ValueError:
        return RedirectResponse(url=f"/chat/{slug}", status_code=303)
    return RedirectResponse(url=f"/chat/{slug}?conversation_id={conversation.id}", status_code=303)


@app.post("/chat/{slug}/conversations/{conversation_id}/pin", response_class=HTMLResponse, response_model=None)
def chat_pin_conversation(request: Request, slug: str, conversation_id: int) -> HTMLResponse | RedirectResponse:
    try:
        project = get_project(slug)
        conversation = get_conversation(conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if conversation.project_id != project.id:
        raise HTTPException(status_code=404, detail="conversation does not belong to project")

    updated = set_conversation_pinned(conversation_id, pinned=not conversation.pinned)
    return RedirectResponse(
        url=f"/chat/{project.slug}?conversation_id={updated.id}",
        status_code=303,
    )


@app.post("/chat/{slug}/conversations/{conversation_id}/delete", response_class=HTMLResponse, response_model=None)
def chat_delete_conversation(request: Request, slug: str, conversation_id: int) -> RedirectResponse:
    try:
        project = get_project(slug)
        conversation = get_conversation(conversation_id)
    except ValueError:
        return RedirectResponse(url=f"/chat/{slug}", status_code=303)

    if conversation.project_id != project.id:
        raise HTTPException(status_code=404, detail="conversation does not belong to project")

    delete_conversation(conversation_id)
    return RedirectResponse(url=f"/chat/{slug}", status_code=303)


@app.post("/chat/{conversation_id}/send", response_model=None)
async def chat_send(request: Request, conversation_id: int) -> StreamingResponse | JSONResponse | HTMLResponse:
    payload = await _read_payload(request)
    content = (payload.get("content") or payload.get("message") or payload.get("prompt") or "").strip()
    lane_hint = (payload.get("lane_override") or payload.get("lane") or payload.get("route") or "auto").strip()
    mode = (payload.get("mode") or "ask").strip() or "ask"
    selected_model_name = str(payload.get("model_name") or payload.get("selected_model") or "").strip()
    if not content:
        return JSONResponse({"status": "error", "detail": "chat content is required"}, status_code=400)

    try:
        project = _project_for_conversation(conversation_id)
    except ValueError as exc:
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=404)

    if lane_hint == "codex_lb" and mode.lower() == "execute":
        conversations = list_conversations(project.slug)
        selected_conversation = _resolve_chat_conversation(conversations, str(conversation_id))
        latest_session = get_latest_session(conversation_id)
        tasks = _list_project_tasks(project.id)
        selected_task = _next_project_task(tasks)
        if selected_task is None:
            return _render_chat_error(request, conversation_id, "execute mode needs an attached task packet")

        approval_allowed, approval_gate, approval_message = gate_allows_action(
            project_slug=project.slug,
            action_type="codex_execute_packet",
            target_endpoint_id=None,
            payload_summary=(
                f"execute packet for conversation {conversation_id} and task {selected_task['id']}: "
                f"{content[:120]}{f' | model={selected_model_name}' if selected_model_name else ''}"
            ),
            endpoint_class="codex_lb",
            mode=mode,
        )
        if not approval_allowed:
            return templates.TemplateResponse(
                request=request,
                name="cockpit.html",
                context=_chat_context(
                    request=request,
                    project=project,
                    conversations=conversations,
                    selected_conversation=selected_conversation,
                    latest_session=latest_session,
                    messages=list_messages(conversation_id),
                    codex_error=approval_message,
                    approval_gate={
                        "id": approval_gate.id,
                        "project_id": approval_gate.project_id,
                        "action_type": approval_gate.action_type,
                        "target_endpoint_id": approval_gate.target_endpoint_id,
                        "payload_summary": approval_gate.payload_summary,
                        "status": approval_gate.status,
                        "decided_at": approval_gate.decided_at,
                        "created_at": approval_gate.created_at,
                    },
                    approval_return_to=f"/chat/{project.slug}?conversation_id={conversation_id}",
                    selected_model_name=selected_model_name,
                    selected_mode_override=mode,
                    selected_lane_override=lane_hint,
                ),
            )

        export_result = export_codex_execution_packet(project.slug, int(selected_task["id"]))
        execution_run = record_execution_run(
            project_slug=project.slug,
            conversation_id=conversation_id,
            task_id=int(selected_task["id"]),
            approval_gate_id=None if approval_gate.id == 0 else approval_gate.id,
            packet_path=export_result.path,
            target="manual_execution",
            mode=mode,
            status="exported",
        )
        return templates.TemplateResponse(
            request=request,
            name="cockpit.html",
            context=_chat_context(
                request=request,
                project=project,
                conversations=conversations,
                selected_conversation=selected_conversation,
                latest_session=latest_session,
                messages=list_messages(conversation_id),
                codex_export_result={
                    "path": str(export_result.path),
                    "token_estimate": export_result.token_estimate,
                    "task_id": export_result.task_id,
                    "execution_run_id": execution_run.id,
                },
                codex_notice="Codex execution packet written",
                selected_model_name=selected_model_name,
                selected_mode_override=mode,
                selected_lane_override=lane_hint,
            ),
        )

    if lane_hint in {"codex_lb", "groq_cloud"}:
        return StreamingResponse(
            _provider_chat_turn_stream(
                project_slug=project.slug,
                conversation_id=conversation_id,
                content=content,
                lane=lane_hint,
                mode=mode,
                selected_model_name=selected_model_name,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    if lane_hint == "manual_claude":
        return StreamingResponse(
            _manual_chat_turn_stream(
                project_slug=project.slug,
                conversation_id=conversation_id,
                content=content,
                lane=lane_hint,
                mode=mode,
                detail="manual Claude stays export/import only in this phase; no live model call was made.",
                route_status="manual",
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    gated_lane = _chat_gated_lane_for_override(lane_hint)
    if gated_lane is not None:
        return StreamingResponse(
            _manual_chat_turn_stream(
                project_slug=project.slug,
                conversation_id=conversation_id,
                content=content,
                lane=gated_lane["value"],
                mode=mode,
                detail=gated_lane["reason"],
                route_status="blocked",
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    def event_stream():
        try:
            for event in stream_local_chat_turn(
                project.slug,
                conversation_id,
                content,
                lane_hint=lane_hint,
                mode=mode,
                lane_override=lane_hint,
            ):
                yield _sse_payload(event)
        except Exception as exc:  # pragma: no cover - defensive stream guard
            yield _sse_payload(
                {
                    "event": "error",
                    "conversation_id": conversation_id,
                    "detail": str(exc),
                    "status": "error",
                }
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/chat/{conversation_id}/export-manual", response_class=HTMLResponse)
async def chat_export_manual(request: Request, conversation_id: int) -> HTMLResponse:
    try:
        project = _project_for_conversation(conversation_id)
        conversations = list_conversations(project.slug)
        selected_conversation = _resolve_chat_conversation(conversations, str(conversation_id))
        latest_session = get_latest_session(conversation_id)
        messages = list_messages(conversation_id)
        export_result = export_manual_chat_packet(project.slug, conversation_id)
    except ValueError as exc:
        return _render_chat_error(request, conversation_id, str(exc))

    return templates.TemplateResponse(
        request=request,
        name="cockpit.html",
        context=_chat_context(
            request=request,
            project=project,
            conversations=conversations,
            selected_conversation=selected_conversation,
            latest_session=latest_session,
            messages=messages,
            manual_export_result={
                "path": str(export_result.path),
                "token_estimate": export_result.token_estimate,
                "conversation_id": export_result.conversation_id,
            },
            manual_notice="manual Claude export written",
            manual_content="",
        ),
    )


@app.post("/chat/{conversation_id}/export-codex", response_class=HTMLResponse)
async def chat_export_codex(request: Request, conversation_id: int) -> HTMLResponse:
    try:
        project = _project_for_conversation(conversation_id)
        conversations = list_conversations(project.slug)
        selected_conversation = _resolve_chat_conversation(conversations, str(conversation_id))
        latest_session = get_latest_session(conversation_id)
        messages = list_messages(conversation_id)
        export_result = export_codex_execution_packet(project.slug, conversation_id)
    except ValueError as exc:
        return _render_chat_error(request, conversation_id, str(exc))

    return templates.TemplateResponse(
        request=request,
        name="cockpit.html",
        context=_chat_context(
            request=request,
            project=project,
            conversations=conversations,
            selected_conversation=selected_conversation,
            latest_session=latest_session,
            messages=messages,
            codex_export_result={
                "path": str(export_result.path),
                "token_estimate": export_result.token_estimate,
                "conversation_id": export_result.task_id,
            },
            codex_notice="Codex execution packet written",
            codex_content="",
        ),
    )


@app.post("/chat/{conversation_id}/import-manual", response_class=HTMLResponse)
async def chat_import_manual(request: Request, conversation_id: int) -> HTMLResponse:
    payload = await _read_payload(request)
    content = payload.get("content") or payload.get("text") or ""
    target = payload.get("target") or "current_status.md"

    try:
        project = _project_for_conversation(conversation_id)
        conversations = list_conversations(project.slug)
        selected_conversation = _resolve_chat_conversation(conversations, str(conversation_id))
        import_result = import_manual_chat_response(project.slug, conversation_id, content, target)
        latest_session = get_latest_session(conversation_id)
        messages = list_messages(conversation_id)
    except ValueError as exc:
        try:
            project = _project_for_conversation(conversation_id)
            conversations = list_conversations(project.slug)
            selected_conversation = _resolve_chat_conversation(conversations, str(conversation_id))
            latest_session = get_latest_session(conversation_id)
            messages = list_messages(conversation_id)
        except ValueError as nested_exc:
            return _render_chat_error(request, conversation_id, str(nested_exc))
        return templates.TemplateResponse(
            request=request,
            name="cockpit.html",
            status_code=400,
            context=_chat_context(
                request=request,
                project=project,
                conversations=conversations,
                selected_conversation=selected_conversation,
                latest_session=latest_session,
                messages=messages,
                manual_error=str(exc),
                manual_content=content,
            ),
        )

    return templates.TemplateResponse(
        request=request,
        name="cockpit.html",
        context=_chat_context(
            request=request,
            project=project,
            conversations=conversations,
            selected_conversation=selected_conversation,
            latest_session=latest_session,
            messages=messages,
            manual_import_result={
                "path": str(import_result.path),
                "target": import_result.target,
                "handoff_path": str(import_result.handoff_path),
                "message_id": import_result.message_id,
            },
            manual_notice="manual Claude response imported",
            manual_content=content,
        ),
    )


@app.post("/chat/{conversation_id}/import-codex", response_class=HTMLResponse)
async def chat_import_codex(request: Request, conversation_id: int) -> HTMLResponse:
    payload = await _read_payload(request)
    content = payload.get("content") or payload.get("text") or ""
    target = payload.get("target") or "current_status.md"

    try:
        project = _project_for_conversation(conversation_id)
        conversations = list_conversations(project.slug)
        selected_conversation = _resolve_chat_conversation(conversations, str(conversation_id))
        import_result = import_codex_execution_response(project.slug, conversation_id, content, target)
        session = create_chat_session(
            conversation_id,
            lane="codex_lb",
            endpoint_id=None,
            attached_brain_files=[target],
            attached_task_id=None,
        )
        assistant_message = save_message(
            conversation_id=conversation_id,
            content=content,
            role="assistant",
            session_id=session.id,
            mode="execute",
            lane_override="codex_lb",
            status="final",
            model_name="codex_lb",
        )
        execution_run = record_execution_run(
            project_slug=project.slug,
            conversation_id=conversation_id,
            task_id=None,
            approval_gate_id=None,
            packet_path=import_result.path,
            target=target,
            mode="execute",
            status="imported",
        )
        latest_session = get_latest_session(conversation_id)
        messages = list_messages(conversation_id)
    except ValueError as exc:
        try:
            project = _project_for_conversation(conversation_id)
            conversations = list_conversations(project.slug)
            selected_conversation = _resolve_chat_conversation(conversations, str(conversation_id))
            latest_session = get_latest_session(conversation_id)
            messages = list_messages(conversation_id)
        except ValueError as nested_exc:
            return _render_chat_error(request, conversation_id, str(nested_exc))
        return templates.TemplateResponse(
            request=request,
            name="cockpit.html",
            status_code=400,
            context=_chat_context(
                request=request,
                project=project,
                conversations=conversations,
                selected_conversation=selected_conversation,
                latest_session=latest_session,
                messages=messages,
                codex_error=str(exc),
                codex_content=content,
            ),
        )

    return templates.TemplateResponse(
        request=request,
        name="cockpit.html",
        context=_chat_context(
            request=request,
            project=project,
            conversations=conversations,
            selected_conversation=selected_conversation,
            latest_session=latest_session,
            messages=messages,
            codex_import_result={
                "path": str(import_result.path),
                "target": import_result.target,
                "handoff_path": str(import_result.handoff_path),
                "message_id": assistant_message.id,
                "execution_run_id": execution_run.id,
            },
            codex_notice="Codex execution reply imported",
            codex_content=content,
            selected_mode_override="execute",
            selected_lane_override="codex_lb",
        ),
    )


@app.post("/chat/{conversation_id}/to-task", response_class=HTMLResponse)
async def chat_to_task(request: Request, conversation_id: int) -> HTMLResponse:
    payload = await _read_payload(request)
    source_message_id_raw = payload.get("message_id") or payload.get("source_message_id")
    source_message_id = None
    if source_message_id_raw not in (None, ""):
        try:
            source_message_id = int(source_message_id_raw)
        except (TypeError, ValueError):
            return JSONResponse({"status": "error", "detail": f"invalid message id: {source_message_id_raw}"}, status_code=400)

    try:
        project = _project_for_conversation(conversation_id)
        conversations = list_conversations(project.slug)
        selected_conversation = _resolve_chat_conversation(conversations, str(conversation_id))
        latest_session = get_latest_session(conversation_id)
        messages = list_messages(conversation_id)
        task_draft = draft_task_packet_from_turn(conversation_id, source_message_id=source_message_id)
        result = create_packet(
            slug=project.slug,
            title=task_draft.title,
            goal=task_draft.goal,
            size_class=task_draft.size_class,
            preferred_route=task_draft.preferred_route,
            expected_output=task_draft.expected_output,
            acceptance=task_draft.acceptance,
        )
    except ValueError as exc:
        try:
            project = _project_for_conversation(conversation_id)
            conversations = list_conversations(project.slug)
            selected_conversation = _resolve_chat_conversation(conversations, str(conversation_id))
            latest_session = get_latest_session(conversation_id)
            messages = list_messages(conversation_id)
        except ValueError as nested_exc:
            return _render_chat_error(request, conversation_id, str(nested_exc))
        return templates.TemplateResponse(
            request=request,
            name="cockpit.html",
            status_code=400,
            context=_chat_context(
                request=request,
                project=project,
                conversations=conversations,
                selected_conversation=selected_conversation,
                latest_session=latest_session,
                messages=messages,
                chat_task_error=str(exc),
            ),
        )

    return templates.TemplateResponse(
        request=request,
        name="cockpit.html",
        context=_chat_context(
            request=request,
            project=project,
            conversations=conversations,
            selected_conversation=selected_conversation,
            latest_session=latest_session,
            messages=messages,
            chat_task_result={
                "task_id": result.task_id,
                "packet_id": result.packet_id,
                "path": str(result.path),
                "token_estimate": result.token_estimate,
                "title": task_draft.title,
                "size_class": task_draft.size_class,
                "preferred_route": task_draft.preferred_route,
                "source_message_id": task_draft.source_message_id,
                "source_role": task_draft.source_role,
                "excerpt": task_draft.excerpt,
            },
            chat_task_notice="chat turn promoted to task packet",
        ),
    )


@app.get("/route/{slug}", response_class=HTMLResponse)
def route_page(request: Request, slug: str) -> HTMLResponse:
    try:
        project = get_project(slug)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    route_token = request.query_params.get("result")
    result = ROUTE_RESULT_CACHE.get(route_token) if route_token else None
    selected_task_id = request.query_params.get("task")
    selected_task = _resolve_task_selection(project.id, selected_task_id)
    approval_gate = _approval_gate_from_result(result)
    return templates.TemplateResponse(
        request=request,
        name="route.html",
        context=_route_context(
            request=request,
            project=project,
            route_result=result,
            approval_gate=approval_gate,
            error=None,
            selected_task_id=selected_task["id"],
        ),
    )


@app.get("/routes/{slug}", response_class=HTMLResponse)
def routes_index(request: Request, slug: str) -> HTMLResponse:
    try:
        project = get_project(slug)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    decisions = list_route_decisions(project.slug, limit=100)
    return templates.TemplateResponse(
        request=request,
        name="routes.html",
        context={
            "request": request,
            "project": project,
            "decisions": decisions,
            "decision_count": len(decisions),
            "approval_count": sum(1 for item in decisions if item["requires_approval"]),
            "preview_count": sum(1 for item in decisions if item["is_preview"]),
            "page_title": f"{project.slug} routes",
            "page_meta": "Route decision explorer and candidate history.",
            **_ui_context(),
        },
    )


@app.get("/agents/{slug}", response_class=HTMLResponse)
def agents_index(request: Request, slug: str) -> HTMLResponse:
    try:
        project = get_project(slug)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    threads = list_agent_threads(project.slug)
    return templates.TemplateResponse(
        request=request,
        name="agents.html",
        context=_agents_context(
            request=request,
            project=project,
            threads=threads,
            notice=None,
            error=None,
        ),
    )


@app.post("/agents/{slug}/step", response_class=HTMLResponse)
async def agents_step(request: Request, slug: str) -> HTMLResponse:
    try:
        project = get_project(slug)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    payload = await _read_payload(request)
    approved = str(payload.get("approved") or payload.get("decision") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "approved",
        "on",
    }
    threads = list_agent_threads(project.slug)
    if not approved:
        return templates.TemplateResponse(
            request=request,
            name="agents.html",
            status_code=400,
            context=_agents_context(
                request=request,
                project=project,
                threads=threads,
                notice=None,
                error="step requires explicit approval",
            ),
        )

    try:
        result = orchestrate_one_step(project.slug)
    except ValueError as exc:
        return templates.TemplateResponse(
            request=request,
            name="agents.html",
            status_code=400,
            context=_agents_context(
                request=request,
                project=project,
                threads=list_agent_threads(project.slug),
                notice=None,
                error=str(exc),
            ),
        )

    notice = f"approved step advanced task {result.task_id} via {result.endpoint_class} into {result.next_thread}"
    return templates.TemplateResponse(
        request=request,
        name="agents.html",
        context=_agents_context(
            request=request,
            project=project,
            threads=list_agent_threads(project.slug),
            notice=notice,
            error=None,
        ),
    )


@app.post("/route/run", response_class=HTMLResponse)
async def route_run(request: Request) -> HTMLResponse:
    payload = await _read_payload(request)
    slug = payload.get("slug") or payload.get("project") or payload.get("project_slug")
    task_id_text = payload.get("task_id") or payload.get("task")
    if not slug or not task_id_text:
        return _render_route_error(request, slug, "missing project or task selection")

    try:
        task_id = int(task_id_text)
    except ValueError:
        return _render_route_error(request, slug, f"invalid task id '{task_id_text}'")

    try:
        project = get_project(slug)
        result = run_route(project.slug, task_id)
    except ValueError as exc:
        status = 404 if "does not exist" in str(exc) else 400
        return _render_route_error(request, slug, str(exc), status_code=status)
    except RuntimeError as exc:
        return _render_route_error(request, slug, str(exc))

    token = secrets.token_hex(8)
    ROUTE_RESULT_CACHE[token] = {
        "task_id": result.task.task_id,
        "task_title": result.task.title,
        "mode": result.mode,
        "endpoint_class": result.endpoint_class,
        "rationale": result.rationale,
        "preview": result.preview,
        "approval_gate_id": result.approval_gate_id,
        "approval_status": result.approval_status,
        "approval_message": result.approval_message,
        "classification": {
            "task_type": result.classification.task_type,
            "size": result.classification.size,
            "risk": result.classification.risk,
        },
    }
    return RedirectResponse(url=f"/route/{project.slug}?result={token}&task={task_id}", status_code=303)


@app.post("/approval/{gate_id}/decide")
async def approval_decide(gate_id: int, request: Request) -> RedirectResponse:
    payload = await _read_payload(request)
    decision = payload.get("decision") or payload.get("status") or ""
    trust_session = str(payload.get("trust_session") or payload.get("trust_provider") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return_to = payload.get("return_to") or request.headers.get("referer") or "/"

    try:
        gate = decide_approval_gate(gate_id, decision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if trust_session and gate.status == "approved":
        project_slug = _project_slug_for_id(gate.project_id)
        create_approval_grant(
            project_slug=project_slug,
            endpoint_class="codex_lb",
            modes=["execute"],
            turns_remaining=3,
            expires_at=utc_now_plus(hours=2),
        )

    if not _is_safe_local_return(return_to):
        return_to = "/"
    return RedirectResponse(url=return_to, status_code=303)


@app.get("/import/{slug}", response_class=HTMLResponse)
def import_page(request: Request, slug: str) -> HTMLResponse:
    try:
        project = get_project(slug)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    import_token = request.query_params.get("result")
    result = IMPORT_RESULT_CACHE.get(import_token) if import_token else None
    return templates.TemplateResponse(
        request=request,
        name="import.html",
        context=_import_context(
            request=request,
            project=project,
            import_result=result,
            error=None,
        ),
    )


@app.post("/import/manual", response_class=HTMLResponse)
async def import_manual(request: Request) -> HTMLResponse:
    payload = await _read_payload(request)
    slug = payload.get("slug") or payload.get("project") or payload.get("project_slug")
    target = payload.get("target") or payload.get("brain_file")
    content = payload.get("content") or payload.get("text") or ""
    if not slug or not target:
        return _render_import_error(request, slug, "missing project or target selection")
    if not content.strip():
        return _render_import_error(request, slug, "manual Claude content is required")

    try:
        project = get_project(slug)
    except ValueError as exc:
        return _render_import_error(request, slug, str(exc), status_code=404)

    import_dir = project.path / "artifacts" / "private" / "manual_imports"
    import_dir.mkdir(parents=True, exist_ok=True)
    import_path = import_dir / f"manual-import-{secrets.token_hex(8)}.md"
    import_path.write_text(content.rstrip() + "\n", encoding="utf-8")

    try:
        result = import_manual_claude_output(project.slug, import_path, target)
    except ValueError as exc:
        return _render_import_error(request, slug, str(exc))

    token = secrets.token_hex(8)
    IMPORT_RESULT_CACHE[token] = {
        "target": result.target,
        "path": str(result.path),
        "handoff_path": str(result.handoff_path),
        "source_file": str(import_path),
    }
    return RedirectResponse(url=f"/import/{project.slug}?result={token}", status_code=303)


@app.post("/task/create", response_class=HTMLResponse)
async def task_create(request: Request) -> HTMLResponse:
    payload = await _read_payload(request)
    slug = payload.get("slug") or payload.get("project") or payload.get("project_slug")
    if not slug:
        return templates.TemplateResponse(
            request=request,
            name="board.html",
            status_code=400,
            context=_error_context(request, "missing project slug"),
        )

    try:
        project = get_project(slug)
        result = create_packet(
            slug=project.slug,
            title=payload.get("title", "").strip(),
            goal=payload.get("goal", "").strip(),
            size_class=payload.get("size_class", "small").strip() or "small",
            preferred_route=payload.get("preferred_route", "execution").strip() or "execution",
            expected_output=payload.get("expected_output", "").strip(),
            acceptance=payload.get("acceptance", "").strip(),
        )
    except ValueError as exc:
        status = 404 if "does not exist" in str(exc) else 400
        return templates.TemplateResponse(
            request=request,
            name="board.html",
            status_code=status,
            context=_error_context(request, str(exc), slug=slug),
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="board.html",
            status_code=400,
            context=_error_context(request, str(exc), slug=slug),
        )

    notice = f"created task {result.task_id} and packet {result.packet_id}"
    return RedirectResponse(
        url=(
            f"/board/{project.slug}"
            f"?created_task={result.task_id}&created_packet={result.packet_id}"
        ),
        status_code=303,
    )


@app.get("/timeline/{slug}", response_class=HTMLResponse)
def project_timeline(request: Request, slug: str) -> HTMLResponse:
    try:
        project = get_project(slug)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    handoffs = _list_handoffs(project.id)
    return templates.TemplateResponse(
        request=request,
        name="timeline.html",
        context={
            "request": request,
            "project": project,
            "handoffs": handoffs,
            "handoff_count": len(handoffs),
            "page_title": f"{project.slug} timeline",
            "page_meta": f"{len(handoffs)} handoffs recorded locally.",
            **_ui_context(),
        },
    )


@app.get("/benchmarks", response_class=HTMLResponse)
def benchmarks_index(request: Request) -> HTMLResponse:
    rows = _benchmark_lab_rows()
    endpoint_count = len({row["endpoint_name"] for row in rows if row["endpoint_name"]})
    task_type_count = len({row["task_type"] for row in rows if row["task_type"]})
    verified_count = sum(1 for row in rows if row["verified"])
    return templates.TemplateResponse(
        request=request,
        name="benchmarks.html",
        context={
            "request": request,
            "benchmark_rows": rows,
            "benchmark_count": len(rows),
            "verified_count": verified_count,
            "endpoint_count": endpoint_count,
            "task_type_count": task_type_count,
            "page_title": "Benchmark Lab",
            "page_meta": "Read-only benchmark score grid with verified, latency, and cost signals.",
            **_ui_context(),
        },
    )


@app.get("/usage", response_class=HTMLResponse)
def usage_index(request: Request) -> HTMLResponse:
    summary = usage_summary()
    return templates.TemplateResponse(
        request=request,
        name="usage.html",
        context={
            "request": request,
            "usage_rows": summary["rows"],
            "gauges": summary["gauges"],
            "total_events": summary["total_events"],
            "total_tokens_in": summary["total_tokens_in"],
            "total_tokens_out": summary["total_tokens_out"],
            "total_est_cost": summary["total_est_cost"],
            "page_title": "Usage Monitor",
            "page_meta": "Token and estimated cost bars with endpoint window gauges.",
            **_ui_context(),
        },
    )


@app.get("/endpoints", response_class=HTMLResponse)
def endpoints_index(request: Request) -> HTMLResponse:
    profiles = _provider_profile_rows()
    return templates.TemplateResponse(
        request=request,
        name="endpoints.html",
        context={
            "request": request,
            "profiles": profiles,
            "profile_count": len(profiles),
            "local_profile_count": sum(1 for profile in profiles if profile["tier"] == "local"),
            "cloud_profile_count": sum(1 for profile in profiles if profile["tier"] == "cloud"),
            "manual_profile_count": sum(1 for profile in profiles if profile["tier"] == "manual"),
            "page_title": "Provider Registry",
            "page_meta": "Provider profiles, cost columns, and lane metadata.",
            **_ui_context(),
        },
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_index(request: Request) -> HTMLResponse:
    _recover_provider_api_base_urls()
    profiles = _provider_profile_rows()
    codex_settings = _codex_lb_settings_state()
    groq_settings = _groq_cloud_settings_state()
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "request": request,
            "profiles": profiles,
            "profile_count": len(profiles),
            "profiles_with_live_calls": sum(1 for profile in profiles if profile["live_calls_allowed"]),
            "profiles_with_keys": sum(1 for profile in profiles if profile["env_presence_count"]),
            "codex_settings": codex_settings,
            "groq_settings": groq_settings,
            "settings_notice": request.query_params.get("notice", ""),
            "settings_detail": request.query_params.get("detail", ""),
            "settings_status": request.query_params.get("status", ""),
            "page_title": "Settings",
            "page_meta": "Presence-only provider settings and key status.",
            **_ui_context(),
        },
    )


@app.post("/settings/providers/codex-lb")
async def settings_codex_lb_update(request: Request) -> RedirectResponse:
    payload = await _read_payload(request)
    action = str(payload.get("action") or "save").strip().lower()
    try:
        base_url = _normalize_codex_lb_base_url(payload.get("base_url"))
    except ValueError as exc:
        query = "&".join(
            [
                "status=error",
                f"notice={quote('Codex LB settings not saved')}",
                f"detail={quote(str(exc))}",
            ]
        )
        return RedirectResponse(url=f"/settings?{query}", status_code=303)
    notice = ""
    detail = ""
    status = "ok"

    if base_url:
        _update_codex_lb_base_url(base_url)

    if action == "remove_key":
        delete_secret("CODEX_LB_API_KEY")
        reload_secrets()
        _set_codex_lb_live_calls_allowed(False)
        _revoke_provider_approval_grant("codex_lb")
        notice = "Codex LB key removed"
        detail = "presence cleared and live lane turned off"
    elif action == "test_connection":
        result = health_check("codex_lb")
        notice = "Codex LB test complete"
        detail = f"{result.result} | {result.detail}"
        status = "reachable" if result.reachable else "unhealthy"
    elif action == "sync_models":
        try:
            result = sync_models("codex_lb")
        except Exception as exc:
            notice = "Codex LB model sync failed"
            detail = str(exc)
            status = "unhealthy"
        else:
            notice = f"Codex LB models synced ({result.models_synced})"
            detail = f"last sync {result.last_model_sync}"
            status = "synced"
    else:
        key_value = str(payload.get("api_key") or "").strip()
        if key_value:
            set_secret("CODEX_LB_API_KEY", key_value)
            reload_secrets()
            _set_codex_lb_live_calls_allowed(True)
            _ensure_codex_chat_approval_grant()
            notice = "Codex LB key saved"
            detail = f"fingerprint {key_fingerprint(key_value)}"
        else:
            _set_codex_lb_live_calls_allowed(bool(get_secret("CODEX_LB_API_KEY")))
            notice = "Codex LB settings updated"
            detail = "base URL refreshed" if base_url else "no key value submitted"

    query = "&".join(
        [
            f"status={quote(status)}",
            f"notice={quote(notice)}",
            f"detail={quote(detail)}",
        ]
    )
    return RedirectResponse(url=f"/settings?{query}", status_code=303)


@app.post("/settings/providers/groq-cloud")
async def settings_groq_cloud_update(request: Request) -> RedirectResponse:
    payload = await _read_payload(request)
    action = str(payload.get("action") or "save").strip().lower()
    try:
        base_url = _normalize_groq_cloud_base_url(payload.get("base_url"))
    except ValueError as exc:
        query = "&".join(
            [
                "status=error",
                f"notice={quote('Groq Cloud settings not saved')}",
                f"detail={quote(str(exc))}",
            ]
        )
        return RedirectResponse(url=f"/settings?{query}", status_code=303)
    notice = ""
    detail = ""
    status = "ok"

    if base_url:
        _update_groq_cloud_base_url(base_url)

    if action == "remove_key":
        delete_secret("GROQ_API_KEY")
        reload_secrets()
        _set_groq_cloud_live_calls_allowed(False)
        _revoke_provider_approval_grant("groq_cloud")
        notice = "Groq Cloud key removed"
        detail = "presence cleared and live lane turned off"
    elif action == "test_connection":
        result = health_check("groq_cloud")
        notice = "Groq Cloud test complete"
        detail = f"{result.result} | {result.detail}"
        status = "reachable" if result.reachable else "unhealthy"
    elif action == "sync_models":
        try:
            result = sync_models("groq_cloud")
        except Exception as exc:
            notice = "Groq Cloud model sync failed"
            detail = str(exc)
            status = "unhealthy"
        else:
            notice = f"Groq Cloud models synced ({result.models_synced})"
            detail = f"last sync {result.last_model_sync}"
            status = "synced"
    else:
        key_value = str(payload.get("api_key") or "").strip()
        if key_value:
            set_secret("GROQ_API_KEY", key_value)
            reload_secrets()
            _set_groq_cloud_live_calls_allowed(True)
            _refresh_provider_approval_grant("groq_cloud")
            try:
                result = sync_models("groq_cloud")
            except Exception:
                notice = "Groq Cloud key saved"
                detail = f"fingerprint {key_fingerprint(key_value)} | sync models required"
                status = "ok"
            else:
                notice = "Groq Cloud key saved and models synced"
                detail = f"fingerprint {key_fingerprint(key_value)} | last sync {result.last_model_sync}"
                status = "synced"
        else:
            _set_groq_cloud_live_calls_allowed(bool(get_secret("GROQ_API_KEY")))
            notice = "Groq Cloud settings updated"
            detail = "base URL refreshed" if base_url else "no key value submitted"

    query = "&".join(
        [
            f"status={quote(status)}",
            f"notice={quote(notice)}",
            f"detail={quote(detail)}",
        ]
    )
    return RedirectResponse(url=f"/settings?{query}", status_code=303)


@app.get("/api/runner/status")
def api_runner_status() -> JSONResponse:
    return JSONResponse(runner_status_snapshot())


@app.get("/api/route/preview")
def api_route_preview(project: str, task: int) -> JSONResponse:
    try:
        result = preview_route(project, task)
    except ValueError as exc:
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=404)

    decision = {
        "route_decision_id": result.route_decision_id,
        "endpoint_class": result.endpoint_class,
        "request": result.request,
        "preview": result.preview,
        "rationale": result.rationale,
        "task": {
            "project_slug": result.task.project_slug,
            "task_id": result.task.task_id,
            "title": result.task.title,
            "goal": result.task.goal,
            "size_class": result.task.size_class,
            "preferred_tier": result.task.preferred_tier,
        },
        "source": "api_preview",
        "status": "suggested",
        "is_preview": True,
    }
    return JSONResponse(decision)


@app.get("/runner", response_class=HTMLResponse)
def runner_page(request: Request) -> HTMLResponse:
    snapshot = runner_status_snapshot()
    return templates.TemplateResponse(
        request=request,
        name="runner.html",
        context={
            "request": request,
            "snapshot": snapshot,
            "page_title": "Runner",
            "page_meta": "Local runner health and availability status.",
            **_ui_context(),
        },
    )


@app.post("/preferences/theme")
async def theme_preference(request: Request) -> RedirectResponse:
    payload = await _read_payload(request)
    selected = payload.get("theme") or theme_toggle_value()
    if selected not in {"dark", "light", "system"}:
        selected = "dark"
    set_ui_preference("theme", selected)
    referrer = request.headers.get("referer") or "/"
    return RedirectResponse(url=referrer, status_code=303)


@app.post("/preferences/layout")
async def layout_preference(request: Request) -> JSONResponse:
    payload = await _read_payload(request)
    key = str(payload.get("key") or "").strip()
    value = str(payload.get("value") or "").strip()
    if key not in {"sidebar_collapsed", "context_drawer_open", "context_drawer_section"}:
        return JSONResponse({"status": "error", "detail": f"unsupported layout preference '{key}'"}, status_code=400)

    if key in {"sidebar_collapsed", "context_drawer_open"}:
        value = "1" if value in {"1", "true", "yes", "on", "open", "collapsed"} else "0"
    elif not value:
        value = "memory"

    set_ui_preference(key, value)
    return JSONResponse({"status": "ok", "key": key, "value": value})


def _read_brain_file(path: Path) -> dict[str, str]:
    return {
        "name": path.name,
        "content": path.read_text(encoding="utf-8"),
    }


async def _read_payload(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    raw_body = await request.body()
    if not raw_body:
        return {}
    if content_type == "application/json":
        data = json.loads(raw_body.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return {str(key): str(value) for key, value in data.items()}

    parsed = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _board_context(
    *,
    request: Request,
    project,
    notice: str | None,
    error: str | None,
    created: dict[str, str | int] | None,
) -> dict[str, object]:
    return {
        "request": request,
        "project": project,
        "brain_files": [_read_brain_file(project.path / "brain" / filename) for filename in BRAIN_FILE_ORDER],
        "current_status": _read_status(project.path / "brain" / "current_status.md"),
        "tasks": _list_project_tasks(project.id),
        "packets": _list_project_packets(project.id),
        "task_count": _count_tasks(project.id),
        "packet_count": _count_packets(project.id),
        "notice": notice,
        "error": error,
        "created": created,
        "page_title": f"{project.slug} board",
        "page_meta": project.one_line_purpose,
        **_ui_context(),
        "form_defaults": {
            "title": "",
            "goal": "",
            "size_class": "small",
            "preferred_route": "execution",
            "expected_output": "A scoped implementation plus handoff/status update.",
            "acceptance": "The task output is present and can be verified locally.",
        },
    }


def _agents_context(
    *,
    request: Request,
    project,
    threads: list[dict[str, object]],
    notice: str | None,
    error: str | None,
) -> dict[str, object]:
    thread_count = len(threads)
    active_count = sum(1 for thread in threads if thread["state"] == "active")
    blocked_count = sum(1 for thread in threads if thread["state"] == "blocked")
    idle_count = sum(1 for thread in threads if thread["state"] == "idle")
    next_task = _next_open_task(project.id)
    return {
        "request": request,
        "project": project,
        "threads": threads,
        "thread_count": thread_count,
        "active_count": active_count,
        "blocked_count": blocked_count,
        "idle_count": idle_count,
        "next_task": next_task,
        "notice": notice,
        "error": error,
        "page_title": f"{project.slug} agents",
        "page_meta": "Agent and thread board with explicit one-step approval.",
        **_ui_context(),
    }


def _error_context(request: Request, error: str, slug: str | None = None) -> dict[str, object]:
    project = _resolve_project_for_board(slug)
    return _board_context(
        request=request,
        project=project,
        notice=None,
        error=error,
        created=None,
    )


def _route_context(
    request: Request,
    project,
    route_result: dict[str, object] | None,
    approval_gate: dict[str, object] | None,
    error: str | None,
    selected_task_id: str,
) -> dict[str, object]:
    return {
        "request": request,
        "project": project,
        "tasks": _list_project_tasks(project.id),
        "selected_task_id": selected_task_id,
        "route_result": route_result,
        "approval_gate": approval_gate,
        "approval_return_to": _request_path_with_query(request),
        "error": error,
        "handoffs": _list_handoffs(project.id),
        "route_count": _count_tasks(project.id),
        "page_title": f"{project.slug} route",
        "page_meta": "Deterministic routing preview and manual gate surface.",
        **_ui_context(),
    }


def _chat_context(
    *,
    request: Request,
    project,
    conversations,
    selected_conversation,
    latest_session,
    messages,
    runner_snapshot: dict[str, object] | None = None,
    manual_export_result: dict[str, object] | None = None,
    manual_import_result: dict[str, object] | None = None,
    manual_notice: str | None = None,
    manual_error: str | None = None,
    manual_content: str | None = None,
    codex_export_result: dict[str, object] | None = None,
    codex_import_result: dict[str, object] | None = None,
    codex_notice: str | None = None,
    codex_error: str | None = None,
    codex_content: str | None = None,
    chat_task_result: dict[str, object] | None = None,
    chat_task_notice: str | None = None,
    chat_task_error: str | None = None,
    task_context: dict[str, object] | None = None,
    approval_gate: dict[str, object] | None = None,
    approval_return_to: str | None = None,
    projects: list[dict[str, object]] | None = None,
    selected_mode_override: str | None = None,
    selected_lane_override: str | None = None,
    selected_model_name: str | None = None,
    latest_route_decision: dict[str, object] | None = None,
) -> dict[str, object]:
    rendered_messages = [_chat_message_view_model(message) for message in messages]
    available_projects = projects or list_projects()
    resolved_latest_session = latest_session or {
        "id": "",
        "lane": "deterministic",
        "attached_task_id": "",
        "attached_brain_files": [],
    }
    if isinstance(resolved_latest_session, dict):
        selected_lane = str(resolved_latest_session.get("lane") or "auto")
    else:
        selected_lane = str(getattr(resolved_latest_session, "lane", "auto"))
    lane_values = {str(option["value"]) for option in _chat_lane_options()}
    if selected_lane not in lane_values:
        selected_lane = "auto"
    selected_lane = (
        selected_lane_override
        or get_ui_preference("chat_last_lane", "auto")
        or selected_lane
    )
    selected_mode = (
        selected_mode_override
        or get_ui_preference("chat_last_mode", getattr(selected_conversation, "mode_default", "ask"))
    )
    codex_lb_state = _codex_lb_composer_state(project.slug)
    groq_cloud_state = _groq_cloud_composer_state(project.slug)
    resolved_codex_selected_model_name = str(selected_model_name or codex_lb_state.get("selected_model_name") or "")
    resolved_groq_selected_model_name = str(groq_cloud_state.get("selected_model_name") or "")
    return {
        "request": request,
        "project": project,
        "workspace_mode": "quiet",
        "project_accent_class": _project_accent_class(project.slug),
        "projects": available_projects,
        "project_count_total": len(available_projects),
        "conversations": conversations,
        "selected_conversation": selected_conversation,
        "selected_conversation_id": str(selected_conversation.id),
        "selected_lane": selected_lane,
        "latest_session": resolved_latest_session,
        "messages": rendered_messages,
        "message_count": len(rendered_messages),
        "conversation_count": len(conversations),
        "agent_threads": list_agent_threads(project.slug),
        "chat_lane_options": _chat_lane_options(),
        "chat_mode_options": _chat_mode_options(),
        **_ui_context(),
        "codex_lb_state": codex_lb_state,
        "groq_cloud_state": groq_cloud_state,
        "selected_model_name": resolved_codex_selected_model_name,
        "selected_codex_model_name": resolved_codex_selected_model_name,
        "selected_groq_model_name": resolved_groq_selected_model_name,
        "selected_mode": selected_mode,
        "current_status": _read_status(project.path / "brain" / "current_status.md"),
        "runner_snapshot": runner_snapshot or runner_status_snapshot(),
        "latest_route_decision": latest_route_decision,
        "page_title": f"{project.slug} chat",
        "page_meta": "Dark cockpit workspace with chat, route, and project memory rails.",
        "manual_target_options": sorted(TARGET_BRAIN_FILES),
        "manual_export_result": manual_export_result,
        "manual_import_result": manual_import_result,
        "manual_notice": manual_notice,
        "manual_error": manual_error,
        "manual_default_target": "current_status.md",
        "manual_default_content": "",
        "manual_content": manual_content,
        "codex_mode_catalog": [
            {
                "name": mode.name,
                "label": mode.label,
                "description": mode.description,
                "approval_required": mode.approval_required,
                "live_allowed": mode.live_allowed,
                "current": mode.name == "manual_execution",
            }
            for mode in codex_mode_catalog()
        ],
        "codex_target_options": sorted(TARGET_BRAIN_FILES),
        "codex_export_result": codex_export_result,
        "codex_import_result": codex_import_result,
        "codex_notice": codex_notice,
        "codex_error": codex_error,
        "codex_default_target": "current_status.md",
        "codex_default_content": "",
        "codex_content": codex_content,
        "chat_task_result": chat_task_result,
        "chat_task_notice": chat_task_notice,
        "chat_task_error": chat_task_error,
        "task_context": task_context,
        "codex_packet_history": _list_codex_packets(project.id),
        "approval_gate": approval_gate,
        "approval_return_to": approval_return_to,
        "usage_snapshot": usage_summary(project.slug),
        "topbar_health": topbar_health_summary(
            (
                str(latest_route_decision.get("chosen_lane"))
                if isinstance(latest_route_decision, dict) and latest_route_decision.get("chosen_lane")
                else selected_lane_override or selected_lane
            ),
            runner_snapshot or runner_status_snapshot(),
        ),
    }


def _chat_lane_options() -> list[dict[str, object]]:
    return provider_lane_options()


def _chat_mode_options() -> list[dict[str, object]]:
    return [
        {"value": "ask", "label": "Ask", "reason": "default"},
        {"value": "plan", "label": "Plan", "reason": "outline work"},
        {"value": "execute", "label": "Execute", "reason": "carry out work"},
        {"value": "review", "label": "Review", "reason": "inspect changes"},
        {"value": "summarize", "label": "Summarize", "reason": "compact context"},
        {"value": "route", "label": "Route", "reason": "inspect lane"},
    ]


def _chat_gated_lane_for_override(lane_override: str) -> dict[str, str] | None:
    if not lane_override:
        return None
    gated_match: dict[str, str] | None = None
    for option in _chat_lane_options():
        if option["value"] != lane_override:
            continue
        if not bool(option["disabled"]):
            return None
        gated_match = {"value": str(option["value"]), "reason": str(option["reason"])}
    return gated_match


def _manual_chat_turn_stream(
    *,
    project_slug: str,
    conversation_id: int,
    content: str,
    lane: str,
    mode: str,
    detail: str,
    route_status: str,
):
    project = get_project(project_slug)
    session = create_chat_session(
        conversation_id,
        lane=lane,
        endpoint_id=None,
        attached_brain_files=["current_status.md"],
        attached_task_id=None,
    )
    user_message = save_message(
        conversation_id=conversation_id,
        content=content,
        role="user",
        session_id=session.id,
        mode=mode,
        lane_override=lane,
        status="final",
    )
    route_decision = record_route_decision(
        project_slug=project.slug,
        task_id=None,
        task_type="chat",
        risk="low",
        size="small",
        chosen_lane=lane,
        explanation=detail,
        source="chat",
        status=route_status,
        requires_approval=False,
        is_preview=False,
        chosen_endpoint_id=None,
        candidates=[],
        message_id=user_message.id,
    )
    yield _sse_payload(
        {
            "event": "unavailable",
            "conversation_id": conversation_id,
            "user_message_id": user_message.id,
            "route_decision_id": route_decision.id,
            "lane": lane,
            "detail": detail,
        }
    )


def _provider_chat_turn_stream(
    *,
    project_slug: str,
    conversation_id: int,
    content: str,
    lane: str,
    mode: str,
    selected_model_name: str,
):
    project = get_project(project_slug)
    prompt = content.strip()
    if lane == "groq_cloud":
        lane_label = "Groq Cloud"
        lane_state = _groq_cloud_composer_state(project.slug)
        adapter = GroqCloudAdapter(
            base_url=str(lane_state.get("base_url") or ""),
            api_key=str(get_secret("GROQ_API_KEY") or ""),
            live_enabled=bool(lane_state.get("live_calls_allowed")),
        )
        endpoint_key_name = "GROQ_API_KEY"
    else:
        lane_label = "Codex LB"
        lane_state = _codex_lb_composer_state(project.slug)
        adapter = CodexLBAdapter(
            mode="api_preview",
            base_url=str(lane_state.get("base_url") or ""),
            api_key=str(get_secret("CODEX_LB_API_KEY") or ""),
            live_enabled=bool(lane_state.get("live_calls_allowed")),
        )
        endpoint_key_name = "CODEX_LB_API_KEY"

    endpoint_id = lane_state.get("endpoint_id")
    endpoint_id_int = int(endpoint_id) if endpoint_id not in (None, "") else None
    resolved_model_name = str(selected_model_name or lane_state.get("selected_model_name") or "").strip()
    reasons: list[str] = []
    if not bool(lane_state.get("key_present")):
        reasons.append("key missing")
    if not str(lane_state.get("base_url") or "").strip():
        reasons.append("base URL missing")
    if lane == "groq_cloud":
        if not lane_state.get("models"):
            reasons.append("sync models required")
        elif not resolved_model_name:
            reasons.append("model missing")
    elif not resolved_model_name:
        reasons.append("model missing")
    if not bool(lane_state.get("live_calls_allowed")):
        reasons.append("live calls disabled")

    session = create_chat_session(
        conversation_id,
        lane=lane,
        endpoint_id=endpoint_id_int,
        attached_brain_files=["current_status.md"],
        attached_task_id=None,
    )
    user_message = save_message(
        conversation_id=conversation_id,
        content=prompt,
        role="user",
        session_id=session.id,
        mode=mode,
        lane_override=lane,
        status="final",
    )
    explanation = f"{lane_label} live chat via configured endpoint"
    route_status = "suggested"
    if reasons:
        explanation = f"{lane_label} unavailable ({', '.join(reasons)})"
        route_status = "unavailable"

    route_decision = record_route_decision(
        project_slug=project.slug,
        task_id=None,
        task_type="chat",
        risk="low",
        size="small",
        chosen_lane=lane,
        explanation=explanation,
        source="chat",
        mode=mode,
        status=route_status,
        requires_approval=True,
        is_preview=False,
        chosen_endpoint_id=endpoint_id_int,
        message_id=user_message.id,
        candidates=[],
    )

    if reasons:
        detail = f"{lane_label} live chat unavailable: {', '.join(reasons)}."
        assistant_message = save_message(
            conversation_id=conversation_id,
            content=detail,
            role="assistant",
            session_id=session.id,
            mode=mode,
            lane_override=lane,
            route_decision_id=route_decision.id,
            status="error",
            endpoint_id=endpoint_id_int,
            model_name=resolved_model_name or None,
            error_kind="missing_config",
        )
        yield _sse_payload(
            {
                "event": "unavailable",
                "conversation_id": conversation_id,
                "user_message_id": user_message.id,
                "assistant_message_id": assistant_message.id,
                "route_decision_id": route_decision.id,
                "lane": lane,
                "endpoint_class": lane,
                "detail": detail,
                "status": "error",
            }
        )
        return

    context_packet = {
        "project_slug": project.slug,
        "conversation_id": conversation_id,
        "prompt": prompt,
        "input_text": prompt,
        "packet_text": prompt,
        "task_id": None,
        "messages": [{"role": "user", "content": prompt}],
        "lane_override": lane,
        "mode": mode,
        "model_name": resolved_model_name,
        "base_url": str(lane_state.get("base_url") or ""),
        "api_key": str(get_secret(endpoint_key_name) or ""),
    }

    yield _sse_payload(
        {
            "event": "status",
            "conversation_id": conversation_id,
            "user_message_id": user_message.id,
            "route_decision_id": route_decision.id,
            "lane": lane,
            "endpoint_class": lane,
            "model_name": resolved_model_name,
            "detail": f"streaming via {lane_label}",
        }
    )

    try:
        sent = adapter.send_chat(context_packet, {"model_name": resolved_model_name})
        assistant_text = str(sent.get("text") or "").strip() or f"{lane_label} returned no text."
        final_usage = dict(sent.get("usage") or {})
        for chunk in _chunk_text_for_stream(assistant_text):
            yield _sse_payload(
                {
                    "event": "delta",
                    "conversation_id": conversation_id,
                    "route_decision_id": route_decision.id,
                    "delta": chunk,
                }
            )
        assistant_message = save_message(
            conversation_id=conversation_id,
            content=assistant_text,
            role="assistant",
            session_id=session.id,
            mode=mode,
            lane_override=lane,
            route_decision_id=route_decision.id,
            status="draft",
            endpoint_id=endpoint_id_int,
            model_name=str(sent.get("model_name") or resolved_model_name or ""),
        )
        if endpoint_id_int is not None:
            health_runtime._record_endpoint_success(endpoint_id_int)
        yield _sse_payload(
            {
                "event": "complete",
                "conversation_id": conversation_id,
                "route_decision_id": route_decision.id,
                "assistant_message_id": assistant_message.id,
                "endpoint_class": lane,
                "endpoint_id": endpoint_id_int,
                "model_name": str(sent.get("model_name") or resolved_model_name or ""),
                "approval_gate_id": sent.get("approval_gate_id"),
                "approval_status": sent.get("approval_status"),
                "status": "draft",
                "usage": final_usage,
                "text": assistant_text,
            }
        )
    except Exception as exc:
        error_info = adapter.normalize_error(exc)
        error_kind = str(error_info.get("kind") or "unknown")
        detail = str(error_info.get("message") or error_info.get("detail") or f"{lane_label} chat failed")
        response = getattr(exc, "response", None)
        if endpoint_id_int is not None:
            if getattr(response, "status_code", None) == 429:
                health_runtime._record_endpoint_rate_limit(
                    endpoint_id_int,
                    retry_after_seconds=health_runtime._retry_after_seconds(response),
                )
            else:
                health_runtime._record_endpoint_failure(endpoint_id_int)
        assistant_message = save_message(
            conversation_id=conversation_id,
            content=detail,
            role="assistant",
            session_id=session.id,
            mode=mode,
            lane_override=lane,
            route_decision_id=route_decision.id,
            status="error",
            endpoint_id=endpoint_id_int,
            model_name=resolved_model_name or None,
            error_kind=error_kind,
        )
        yield _sse_payload(
            {
                "event": "error",
                "conversation_id": conversation_id,
                "route_decision_id": route_decision.id,
                "assistant_message_id": assistant_message.id,
                "endpoint_class": lane,
                "endpoint_id": endpoint_id_int,
                "model_name": resolved_model_name,
                "error_kind": error_kind,
                "detail": detail,
                "status": "error",
            }
        )


def _project_accent_class(slug: str) -> str:
    index = sum(ord(char) for char in slug) % len(PROJECT_ACCENT_CLASSES)
    return PROJECT_ACCENT_CLASSES[index]


def _import_context(
    request: Request,
    project,
    import_result: dict[str, object] | None,
    error: str | None,
) -> dict[str, object]:
    return {
        "request": request,
        "project": project,
        "target_options": [
            "active_plan.md",
            "architecture.md",
            "business_scope.md",
            "current_status.md",
            "decisions.md",
            "errors.md",
            "handoffs.md",
            "model_routes.md",
            "tasks.md",
            "verification.md",
        ],
        "import_result": import_result,
        "error": error,
        "handoffs": _list_handoffs(project.id),
        "page_title": f"{project.slug} import",
        "page_meta": "Manual Claude import lane and handoff capture.",
        **_ui_context(),
    }


def _chat_message_view_model(message) -> dict[str, object]:
    def read(name: str, default=None):
        if isinstance(message, dict):
            return message.get(name, default)
        return getattr(message, name, default)

    payload = {
        "id": str(read("id", "")),
        "conversation_id": str(read("conversation_id", "")),
        "session_id": str(read("session_id", "")),
        "role": str(read("role", "assistant")),
        "content": str(read("content", "")),
        "mode": str(read("mode", "ask")),
        "lane_override": read("lane_override", None),
        "route_decision_id": read("route_decision_id", None),
        "citations": list(read("citations", []) or []),
        "token_estimate": int(read("token_estimate", 0) or 0),
        "status": str(read("status", "final")),
        "endpoint_id": read("endpoint_id", None),
        "endpoint_class": str(read("endpoint_class", "") or ""),
        "route_mode": str(read("route_mode", read("lane_override", "") or "") or ""),
        "attached_task_id": read("attached_task_id", None),
        "attached_brain_files": list(read("attached_brain_files", []) or []),
        "model_name": str(read("model_name", "") or ""),
        "error_kind": str(read("error_kind", "") or ""),
        "created_at": str(read("created_at", "")),
    }
    payload["content_html"] = render_message_html(payload["content"])
    return payload


def _read_status(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _resolve_chat_conversation(conversations, selected_conversation_id: str | None):
    if selected_conversation_id is not None:
        for conversation in conversations:
            if str(conversation.id) == str(selected_conversation_id):
                return conversation
    return conversations[0]


def _sse_payload(event: dict[str, object]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _resolve_task_selection(project_id: int, selected_task_id: str | None) -> dict[str, str]:
    tasks = _list_project_tasks(project_id)
    if selected_task_id is not None:
        for task in tasks:
            if task["id"] == str(selected_task_id):
                return task
    return tasks[0] if tasks else {"id": "", "title": "", "goal": "", "status": "", "size_class": "", "preferred_tier": "", "thread": "", "updated_at": ""}


def _next_open_task(project_id: int) -> dict[str, str]:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT id, title, goal, status, size_class, preferred_tier, thread, updated_at
            FROM tasks
            WHERE project_id = ? AND status = 'open'
            ORDER BY id ASC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
    if row is None:
        return {"id": "", "title": "", "goal": "", "status": "", "size_class": "", "preferred_tier": "", "thread": "", "updated_at": ""}
    return {
        "id": str(row["id"]),
        "title": str(row["title"]),
        "goal": str(row["goal"]),
        "status": str(row["status"]),
        "size_class": str(row["size_class"]),
        "preferred_tier": str(row["preferred_tier"]),
        "thread": str(row["thread"]),
        "updated_at": str(row["updated_at"]),
    }


def _render_route_error(request: Request, slug: str | None, error: str, status_code: int = 400) -> HTMLResponse:
    project = _resolve_project_for_board(slug)
    return templates.TemplateResponse(
        request=request,
        name="route.html",
        status_code=status_code,
        context=_route_context(
            request=request,
            project=project,
            route_result=None,
            approval_gate=None,
            error=error,
            selected_task_id=_resolve_task_selection(project.id, None)["id"],
        ),
    )


def _render_import_error(request: Request, slug: str | None, error: str, status_code: int = 400) -> HTMLResponse:
    project = _resolve_project_for_board(slug)
    return templates.TemplateResponse(
        request=request,
        name="import.html",
        status_code=status_code,
        context=_import_context(
            request=request,
            project=project,
            import_result=None,
            error=error,
        ),
    )


def _render_chat_error(request: Request, conversation_id: int, error: str, status_code: int = 400) -> HTMLResponse:
    try:
        project = _project_for_conversation(conversation_id)
        conversations = list_conversations(project.slug)
        selected_conversation = _resolve_chat_conversation(conversations, str(conversation_id))
        latest_session = get_latest_session(conversation_id)
        messages = list_messages(conversation_id)
    except ValueError:
        projects = list_projects()
        project = projects[0] if projects else None
        if project is None:
            raise HTTPException(status_code=404, detail="no projects available")
        conversations = list_conversations(project.slug)
        selected_conversation = conversations[0]
        latest_session = None
        messages = []

    return templates.TemplateResponse(
        request=request,
        name="cockpit.html",
        status_code=status_code,
        context=_chat_context(
            request=request,
            project=project,
            conversations=conversations,
            selected_conversation=selected_conversation,
            latest_session=latest_session,
            messages=messages,
            manual_error=error,
        ),
    )


def _list_project_tasks(project_id: int) -> list[dict[str, str]]:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT id, title, goal, status, size_class, preferred_tier, thread, updated_at
            FROM tasks
            WHERE project_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 12
            """,
            (project_id,),
        ).fetchall()

    tasks: list[dict[str, str]] = []
    for row in rows:
        tasks.append(
            {
                "id": str(row["id"]),
                "title": str(row["title"]),
                "goal": str(row["goal"]),
                "status": str(row["status"]),
                "size_class": str(row["size_class"]),
                "preferred_tier": str(row["preferred_tier"]),
                "thread": str(row["thread"]),
                "updated_at": str(row["updated_at"]),
            }
        )
    return tasks


def _next_project_task(tasks: list[dict[str, str]]) -> dict[str, str] | None:
    if not tasks:
        return None
    priority = {"in_progress": 0, "open": 1, "blocked": 2, "done": 3}
    ordered = sorted(tasks, key=lambda task: (priority.get(task["status"], 4), task["updated_at"], task["id"]))
    return ordered[0]


def _list_project_packets(project_id: int) -> list[dict[str, str]]:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT tp.id AS packet_id, tp.task_id, t.title, tp.model_route, tp.token_estimate, tp.generated_at, tp.expected_output
            FROM task_packets AS tp
            JOIN tasks AS t ON t.id = tp.task_id
            WHERE t.project_id = ?
            ORDER BY tp.generated_at DESC, tp.id DESC
            LIMIT 12
            """,
            (project_id,),
        ).fetchall()

    packets: list[dict[str, str]] = []
    for row in rows:
        packets.append(
            {
                "packet_id": str(row["packet_id"]),
                "task_id": str(row["task_id"]),
                "title": str(row["title"]),
                "model_route": str(row["model_route"]),
                "token_estimate": str(row["token_estimate"]),
                "generated_at": str(row["generated_at"]),
                "expected_output": str(row["expected_output"]),
            }
        )
    return packets


def _benchmark_lab_rows() -> list[dict[str, object]]:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT
                b.id AS benchmark_id,
                b.endpoint_id,
                COALESCE(e.name, e.endpoint_class, 'unassigned') AS endpoint_name,
                COALESCE(e.endpoint_class, 'unassigned') AS endpoint_class,
                b.task_type,
                b.score,
                b.latency_ms,
                b.cost_est,
                b.verified,
                b.run_at
            FROM benchmarks AS b
            LEFT JOIN endpoints AS e ON e.id = b.endpoint_id
            ORDER BY b.run_at DESC, b.id DESC
            """
        ).fetchall()

    return [
        {
            "benchmark_id": int(row["benchmark_id"]),
            "endpoint_id": None if row["endpoint_id"] is None else int(row["endpoint_id"]),
            "endpoint_name": str(row["endpoint_name"]),
            "endpoint_class": str(row["endpoint_class"]),
            "task_type": str(row["task_type"]),
            "score": float(row["score"]),
            "latency_ms": int(row["latency_ms"]),
            "cost_est": float(row["cost_est"]),
            "verified": bool(row["verified"]),
            "run_at": str(row["run_at"]),
            "source": "verified" if bool(row["verified"]) else "draft",
        }
        for row in rows
    ]


def _latest_chat_session(conversation_id: int) -> dict[str, object]:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT
                s.id,
                s.conversation_id,
                s.endpoint_id,
                s.lane,
                s.attached_brain_files,
                s.attached_task_id,
                s.created_at,
                e.endpoint_class
            FROM chat_sessions AS s
            LEFT JOIN endpoints AS e ON e.id = s.endpoint_id
            WHERE s.conversation_id = ?
            ORDER BY s.created_at DESC, s.id DESC
            LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()

    if row is None:
        return {
            "id": "",
            "conversation_id": str(conversation_id),
            "endpoint_id": "",
            "endpoint_class": "",
            "lane": "",
            "attached_brain_files": [],
            "attached_task_id": "",
            "created_at": "",
        }

    return {
        "id": str(row["id"]),
        "conversation_id": str(row["conversation_id"]),
        "endpoint_id": "" if row["endpoint_id"] is None else str(row["endpoint_id"]),
        "endpoint_class": "" if row["endpoint_class"] is None else str(row["endpoint_class"]),
        "lane": str(row["lane"]),
        "attached_brain_files": json.loads(str(row["attached_brain_files"])),
        "attached_task_id": "" if row["attached_task_id"] is None else str(row["attached_task_id"]),
        "created_at": str(row["created_at"]),
    }


def _list_chat_messages(conversation_id: int) -> list[dict[str, object]]:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT
                m.id,
                m.conversation_id,
                m.session_id,
                m.role,
                m.content,
                m.route_decision_id,
                m.citations,
                m.token_estimate,
                m.status,
                m.endpoint_id,
                m.model_name,
                m.error_kind,
                m.created_at,
                s.lane,
                s.attached_task_id,
                s.attached_brain_files,
                e.endpoint_class
            FROM messages AS m
            JOIN chat_sessions AS s ON s.id = m.session_id
            LEFT JOIN endpoints AS e ON e.id = m.endpoint_id
            WHERE m.conversation_id = ?
            ORDER BY m.created_at ASC, m.id ASC
            LIMIT 120
            """,
            (conversation_id,),
        ).fetchall()

    messages: list[dict[str, object]] = []
    for row in rows:
        citations = json.loads(str(row["citations"])) if row["citations"] else []
        messages.append(
            {
                "id": str(row["id"]),
                "conversation_id": str(row["conversation_id"]),
                "session_id": str(row["session_id"]),
                "role": str(row["role"]),
                "content": str(row["content"]),
                "route_decision_id": "" if row["route_decision_id"] is None else str(row["route_decision_id"]),
                "citations": citations,
                "token_estimate": str(row["token_estimate"]),
                "status": str(row["status"]),
                "endpoint_id": "" if row["endpoint_id"] is None else str(row["endpoint_id"]),
                "endpoint_class": "" if row["endpoint_class"] is None else str(row["endpoint_class"]),
                "route_mode": str(row["lane"]),
                "attached_task_id": "" if row["attached_task_id"] is None else str(row["attached_task_id"]),
                "attached_brain_files": json.loads(str(row["attached_brain_files"])),
                "model_name": "" if row["model_name"] is None else str(row["model_name"]),
                "error_kind": "" if row["error_kind"] is None else str(row["error_kind"]),
                "created_at": str(row["created_at"]),
            }
        )
    return messages


def _resolve_project_for_board(slug: str | None):
    if slug:
        try:
            return get_project(slug)
        except ValueError:
            pass
    projects = list_projects()
    if not projects:
        raise HTTPException(status_code=404, detail="no projects available")
    return projects[0]


def _resolve_active_project(projects):
    for project in projects:
        if project.slug == "white-room":
            return project
    return projects[0] if projects else None


def _count_tasks(project_id: int) -> int:
    with connect() as conn:
        init_db(conn)
        row = conn.execute("SELECT COUNT(*) AS count FROM tasks WHERE project_id = ?", (project_id,)).fetchone()
    return int(row["count"]) if row is not None else 0


def _count_packets(project_id: int) -> int:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM task_packets AS tp
            JOIN tasks AS t ON t.id = tp.task_id
            WHERE t.project_id = ?
            """,
            (project_id,),
        ).fetchone()
    return int(row["count"]) if row is not None else 0


def _list_handoffs(project_id: int) -> list[dict[str, str]]:
    from core.db import connect, init_db

    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT id, task_id, from_worker, to_worker, summary, artifact_paths, created_at
            FROM handoffs
            WHERE project_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (project_id,),
        ).fetchall()

    handoffs: list[dict[str, str]] = []
    for row in rows:
        handoffs.append(
            {
                "id": str(row["id"]),
                "task_id": "" if row["task_id"] is None else str(row["task_id"]),
                "from_worker": str(row["from_worker"]),
                "to_worker": str(row["to_worker"]),
                "summary": str(row["summary"]),
                "artifact_paths": str(row["artifact_paths"]),
                "created_at": str(row["created_at"]),
            }
        )
    return handoffs


def _list_codex_packets(project_id: int, limit: int = 6) -> list[dict[str, str]]:
    from core.db import connect, init_db

    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT id, conversation_id, task_id, mode, artifact_path, target, status, token_estimate, created_at
            FROM codex_packets
            WHERE project_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (project_id, limit),
        ).fetchall()

    packets: list[dict[str, str]] = []
    for row in rows:
        packets.append(
            {
                "id": str(row["id"]),
                "conversation_id": "" if row["conversation_id"] is None else str(row["conversation_id"]),
                "task_id": "" if row["task_id"] is None else str(row["task_id"]),
                "mode": str(row["mode"]),
                "artifact_path": str(row["artifact_path"]),
                "target": str(row["target"]),
                "status": str(row["status"]),
                "token_estimate": str(row["token_estimate"]),
                "created_at": str(row["created_at"]),
            }
        )
    return packets


def _endpoint_rows() -> list[dict[str, str]]:
    endpoint_records = list_endpoints()
    if endpoint_records:
        return [
            {
                "id": record.id,
                "name": record.name,
                "endpoint_class": record.endpoint_class,
                "profile_id": record.profile_id,
                "profile_name": record.profile_name,
                "tier": record.tier,
                "status": record.status,
                "base_url": record.base_url,
                "capabilities": record.capabilities,
                "daily_limit": record.daily_limit,
                "window_limit": record.window_limit,
                "model_name": record.model_name or "n/a",
                "supports_streaming": record.supports_streaming,
                "supports_tools": record.supports_tools,
                "supports_json": record.supports_json,
                "input_cost_per_1m": record.input_cost_per_1m,
                "output_cost_per_1m": record.output_cost_per_1m,
                "rate_limit_notes": record.rate_limit_notes,
                "disabled_reason": record.disabled_reason,
            }
            for record in endpoint_records
    ]
    return STATIC_ENDPOINT_ROWS


def _provider_profile_rows() -> list[dict[str, object]]:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT
                id, name, endpoint_class, compatibility_style, base_url, model_name, context_window,
                supports_streaming, supports_tools, supports_json, input_cost_per_1m,
                output_cost_per_1m, rate_limit_notes, capabilities, required_env_vars,
                live_calls_allowed, default_role, disabled_reason, integration_modes,
                default_integration_mode
            FROM provider_profiles
            ORDER BY name ASC
            """
        ).fetchall()

    profiles: list[dict[str, object]] = []
    for row in rows:
        capabilities = _json_list(str(row["capabilities"]))
        required_env_vars = _json_list(str(row["required_env_vars"]))
        env_states = [
            {
                "name": name,
                "present": bool(os.environ.get(name, "").strip()),
            }
            for name in required_env_vars
        ]
        env_presence_count = sum(1 for item in env_states if item["present"])
        env_presence_total = len(env_states)
        if env_presence_total == 0:
            env_presence_label = "none"
        elif env_presence_count == env_presence_total:
            env_presence_label = "present"
        elif env_presence_count == 0:
            env_presence_label = "absent"
        else:
            env_presence_label = "partial"
        profiles.append(
            {
                "id": int(row["id"]),
                "name": str(row["name"]),
                "endpoint_class": str(row["endpoint_class"]),
                "compatibility_style": str(row["compatibility_style"]),
                "tier": _profile_tier(str(row["endpoint_class"])),
                "base_url": _display_base_url(row["base_url"], str(row["endpoint_class"])),
                "model_name": str(row["model_name"]) if row["model_name"] else "n/a",
                "context_window": row["context_window"],
                "context_window_label": _format_count(row["context_window"]),
                "supports_streaming": bool(row["supports_streaming"]),
                "supports_tools": bool(row["supports_tools"]),
                "supports_json": bool(row["supports_json"]),
                "input_cost_per_1m": row["input_cost_per_1m"],
                "output_cost_per_1m": row["output_cost_per_1m"],
                "cost_label": _format_cost_label(row["input_cost_per_1m"], row["output_cost_per_1m"]),
                "rate_limit_notes": str(row["rate_limit_notes"] or ""),
                "capabilities": capabilities,
                "capabilities_text": ", ".join(capabilities) if capabilities else "none",
                "required_env_vars": required_env_vars,
                "required_env_states": env_states,
                "env_presence_count": env_presence_count,
                "env_presence_total": env_presence_total,
                "env_presence_label": env_presence_label,
                "integration_modes": _json_list(str(row["integration_modes"])),
                "integration_modes_text": ", ".join(_json_list(str(row["integration_modes"])))
                if _json_list(str(row["integration_modes"]))
                else "manual execution only",
                "default_integration_mode": str(row["default_integration_mode"] or "manual_execution"),
                "live_calls_allowed": bool(row["live_calls_allowed"]),
                "default_role": str(row["default_role"]),
                "disabled_reason": str(row["disabled_reason"] or ""),
            }
        )
    return profiles


def _display_base_url(base_url: object, endpoint_class: str) -> str:
    if not base_url:
        if endpoint_class.endswith("_cloud"):
            return "metadata only"
        if endpoint_class.endswith("_local"):
            return "localhost"
        return "manual"
    return str(base_url)


def _profile_tier(endpoint_class: str) -> str:
    if endpoint_class in {"manual_claude"}:
        return "manual"
    if endpoint_class in {"ollama_local", "lmstudio_local"}:
        return "local"
    return "cloud"


def _format_count(value: object) -> str:
    if value in (None, ""):
        return "n/a"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _format_cost_label(input_cost: object, output_cost: object) -> str:
    input_label = _format_money(input_cost)
    output_label = _format_money(output_cost)
    return f"in {input_label} / out {output_label}"


def _format_money(value: object) -> str:
    if value in (None, ""):
        return "n/a"
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _json_list(raw: str) -> list[str]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if str(item).strip()]


def _normalize_codex_lb_base_url(raw_value: object | None) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Codex LB base URL must be an http(s) API base")
    path = parsed.path.rstrip("/")
    lowered_path = path.lower()
    if any(fragment in lowered_path for fragment in _BLOCKED_PROVIDER_PATH_FRAGMENTS):
        raise ValueError("Use the Codex LB API root, not the dashboard URL.")
    if path in {"", "/"}:
        return f"{parsed.scheme}://{parsed.netloc}"
    if path == "/v1":
        return f"{parsed.scheme}://{parsed.netloc}/v1"
    raise ValueError("Use the Codex LB API root or /v1 API base, not a dashboard URL.")


def _normalize_groq_cloud_base_url(raw_value: object | None) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return GROQ_CLOUD_DEFAULT_BASE_URL
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Groq Cloud base URL must be an http(s) API base")
    host = parsed.netloc.lower()
    if host == "console.groq.com" or any(fragment in parsed.path.lower() for fragment in _BLOCKED_PROVIDER_PATH_FRAGMENTS):
        raise ValueError("Use https://api.groq.com/openai/v1, not the Groq Console URL.")
    normalized = value.rstrip("/")
    if normalized != GROQ_CLOUD_DEFAULT_BASE_URL:
        raise ValueError("Use https://api.groq.com/openai/v1 for Groq Cloud.")
    return GROQ_CLOUD_DEFAULT_BASE_URL


def _recover_provider_api_base_urls() -> None:
    with connect() as conn:
        init_db(conn)
        codex_endpoint = conn.execute(
            """
            SELECT id, base_url
            FROM endpoints
            WHERE endpoint_class = 'codex_lb'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
        codex_profile = conn.execute(
            """
            SELECT id, base_url
            FROM provider_profiles
            WHERE endpoint_class = 'codex_lb'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
        groq_endpoint = conn.execute(
            """
            SELECT id, base_url
            FROM endpoints
            WHERE endpoint_class = 'groq_cloud'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
        groq_profile = conn.execute(
            """
            SELECT id, base_url
            FROM provider_profiles
            WHERE endpoint_class = 'groq_cloud'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()

        if codex_endpoint is not None:
            codex_base_url = str(codex_endpoint["base_url"] or "")
            repaired_codex = _repair_codex_lb_base_url(codex_base_url)
            if repaired_codex != codex_base_url:
                conn.execute(
                    "UPDATE endpoints SET base_url = ? WHERE id = ?",
                    (repaired_codex, int(codex_endpoint["id"])),
                )
        if codex_profile is not None:
            codex_profile_base_url = str(codex_profile["base_url"] or "")
            repaired_codex_profile = _repair_codex_lb_base_url(codex_profile_base_url)
            if repaired_codex_profile != codex_profile_base_url:
                conn.execute(
                    "UPDATE provider_profiles SET base_url = ? WHERE id = ?",
                    (repaired_codex_profile, int(codex_profile["id"])),
                )

        if groq_endpoint is not None:
            groq_base_url = str(groq_endpoint["base_url"] or "")
            repaired_groq = _repair_groq_cloud_base_url(groq_base_url)
            if repaired_groq != groq_base_url:
                conn.execute(
                    "UPDATE endpoints SET base_url = ? WHERE id = ?",
                    (repaired_groq, int(groq_endpoint["id"])),
                )
        if groq_profile is not None:
            groq_profile_base_url = str(groq_profile["base_url"] or "")
            repaired_groq_profile = _repair_groq_cloud_base_url(groq_profile_base_url)
            if repaired_groq_profile != groq_profile_base_url:
                conn.execute(
                    "UPDATE provider_profiles SET base_url = ? WHERE id = ?",
                    (repaired_groq_profile, int(groq_profile["id"])),
                )
        conn.commit()


def _repair_codex_lb_base_url(base_url: str) -> str:
    value = str(base_url or "").strip()
    if not value:
        return value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return value.rstrip("/")
    path = parsed.path.rstrip("/")
    lowered_path = path.lower()
    if any(fragment in lowered_path for fragment in _BLOCKED_PROVIDER_PATH_FRAGMENTS):
        return f"{parsed.scheme}://{parsed.netloc}"
    if path in {"", "/", "/v1"}:
        return f"{parsed.scheme}://{parsed.netloc}" if path in {"", "/"} else f"{parsed.scheme}://{parsed.netloc}/v1"
    return value.rstrip("/")


def _repair_groq_cloud_base_url(base_url: str) -> str:
    value = str(base_url or "").strip()
    if not value:
        return GROQ_CLOUD_DEFAULT_BASE_URL
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return value.rstrip("/")
    host = parsed.netloc.lower()
    if host == "console.groq.com" or any(fragment in parsed.path.lower() for fragment in _BLOCKED_PROVIDER_PATH_FRAGMENTS):
        return GROQ_CLOUD_DEFAULT_BASE_URL
    normalized = value.rstrip("/")
    if normalized != GROQ_CLOUD_DEFAULT_BASE_URL:
        return GROQ_CLOUD_DEFAULT_BASE_URL
    return GROQ_CLOUD_DEFAULT_BASE_URL


def _update_codex_lb_base_url(base_url: str) -> None:
    with connect() as conn:
        init_db(conn)
        endpoint_row = conn.execute(
            """
            SELECT name
            FROM endpoints
            WHERE endpoint_class = 'codex_lb'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
    if endpoint_row is None:
        add_endpoint(
            "codex-lb",
            "codex_lb",
            "cloud",
            base_url or "https://api.openai.com/v1",
            "execution,hard-debugging",
            "100",
            "10",
            "active" if bool(get_secret("CODEX_LB_API_KEY")) and bool(base_url) else "metadata-only",
            model_name=None,
            rate_limit_notes="Manual trigger only until later approval-gated modes are added.",
        )
    else:
        update_endpoint(
            str(endpoint_row["name"]),
            endpoint_class="codex_lb",
            base_url=base_url,
            status="active" if bool(get_secret("CODEX_LB_API_KEY")) and bool(base_url) else "metadata-only",
        )
    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            UPDATE provider_profiles
            SET base_url = ?, live_calls_allowed = ?
            WHERE endpoint_class = 'codex_lb'
            """,
            (base_url, int(bool(get_secret("CODEX_LB_API_KEY")) and bool(base_url))),
        )
        conn.commit()


def _update_groq_cloud_base_url(base_url: str) -> None:
    with connect() as conn:
        init_db(conn)
        endpoint_row = conn.execute(
            """
            SELECT name
            FROM endpoints
            WHERE endpoint_class = 'groq_cloud'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
    if endpoint_row is None:
        add_endpoint(
            "groq-cloud",
            "groq_cloud",
            "cloud",
            base_url or "https://api.groq.com/openai/v1",
            "draft,summarization",
            "100",
            "10",
            "active" if bool(get_secret("GROQ_API_KEY")) and bool(base_url) else "metadata-only",
            model_name="groq/llama-3.1-70b",
            rate_limit_notes="approval-gated latency lane",
        )
    else:
        update_endpoint(
            str(endpoint_row["name"]),
            endpoint_class="groq_cloud",
            base_url=base_url,
            status="active" if bool(get_secret("GROQ_API_KEY")) and bool(base_url) else "metadata-only",
        )
    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            UPDATE provider_profiles
            SET base_url = ?, live_calls_allowed = ?
            WHERE endpoint_class = 'groq_cloud'
            """,
            (base_url, int(bool(get_secret("GROQ_API_KEY")) and bool(base_url))),
        )
        conn.commit()


def _set_codex_lb_live_calls_allowed(allowed: bool) -> None:
    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            UPDATE provider_profiles
            SET live_calls_allowed = ?
            WHERE endpoint_class = 'codex_lb'
            """,
            (int(bool(allowed)),),
        )
        conn.commit()


def _set_groq_cloud_live_calls_allowed(allowed: bool) -> None:
    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            UPDATE provider_profiles
            SET live_calls_allowed = ?
            WHERE endpoint_class = 'groq_cloud'
            """,
            (int(bool(allowed)),),
        )
        conn.commit()


def _refresh_provider_approval_grant(endpoint_class: str) -> None:
    modes = ["ask", "plan", "review", "summarize", "execute"]
    if endpoint_class == "codex_lb":
        modes = ["ask", "plan", "review", "summarize"]
    create_approval_grant(
        project_slug="white-room",
        endpoint_class=endpoint_class,
        modes=modes,
        turns_remaining=20,
        expires_at=utc_now_plus(hours=6),
    )


def _ensure_codex_chat_approval_grant() -> None:
    grant = _active_approval_grant_for_project("white-room", "codex_lb")
    desired_modes = {"ask", "plan", "review", "summarize"}
    current_modes = {str(mode).strip().lower() for mode in (grant.get("modes") if grant else [])}
    if grant is not None and desired_modes.issubset(current_modes):
        return
    _refresh_provider_approval_grant("codex_lb")


def _revoke_provider_approval_grant(endpoint_class: str) -> None:
    project = get_project("white-room")
    revoked_at = utc_now()
    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            UPDATE approval_grants
            SET active = 0, revoked_at = ?
            WHERE project_id = ? AND endpoint_class = ? AND active = 1
            """,
            (revoked_at, project.id, endpoint_class),
        )
        conn.commit()


def _codex_lb_settings_state() -> dict[str, object]:
    _recover_provider_api_base_urls()
    key_value = get_secret("CODEX_LB_API_KEY")
    base_url = ""
    endpoint_row = None
    health_row = None
    availability_row = None
    runtime_row = None
    models_synced = False
    models_synced_at = ""
    with connect() as conn:
        init_db(conn)
        endpoint_row = conn.execute(
            """
            SELECT e.id, e.name, e.endpoint_class, COALESCE(e.base_url, p.base_url, '') AS base_url,
                   COALESCE(e.model_name, p.model_name, '') AS model_name,
                   e.status, e.last_model_sync, p.name AS profile_name, p.live_calls_allowed
            FROM endpoints AS e
            LEFT JOIN provider_profiles AS p ON p.id = e.profile_id
            WHERE e.endpoint_class = 'codex_lb'
            ORDER BY e.id ASC
            LIMIT 1
            """
        ).fetchone()
        if endpoint_row is not None:
            base_url = str(endpoint_row["base_url"] or "")
            models_synced_at = str(endpoint_row["last_model_sync"] or "")
            models_synced = bool(models_synced_at)
            health_row = conn.execute(
                """
                SELECT reachable, key_present, last_checked, last_error, latency_ms
                FROM endpoint_health
                WHERE endpoint_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(endpoint_row["id"]),),
            ).fetchone()
            availability_row = conn.execute(
                """
                SELECT result, detail, checked_at
                FROM availability_checks
                WHERE endpoint_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(endpoint_row["id"]),),
            ).fetchone()
            runtime_row = conn.execute(
                """
                SELECT failure_count, cooldown_until, last_rate_limited_at, window_used, window_reset_at,
                       last_success_at, updated_at
                FROM endpoint_runtime
                WHERE endpoint_id = ?
                """,
                (int(endpoint_row["id"]),),
            ).fetchone()
    key_present = bool(key_value)
    fingerprint = key_fingerprint(key_value) if key_value else "missing"
    rate_limited = False
    cooldown_remaining = ""
    if runtime_row and runtime_row["cooldown_until"]:
        cooldown_text = str(runtime_row["cooldown_until"])
        rate_limited = True
        cooldown_remaining = cooldown_text
    if health_row is None:
        connection_state = "checking" if key_present else "key missing"
    elif key_present and bool(health_row["reachable"]):
        connection_state = "connected"
    elif key_present:
        connection_state = "unhealthy"
    else:
        connection_state = "key missing"
    if rate_limited:
        connection_state = "rate-limited"
    live_calls_allowed = bool(endpoint_row["live_calls_allowed"]) if endpoint_row else False
    if key_present and live_calls_allowed:
        _ensure_codex_chat_approval_grant()
    return {
        "endpoint_id": int(endpoint_row["id"]) if endpoint_row else None,
        "name": str(endpoint_row["name"]) if endpoint_row else "Codex LB",
        "endpoint_class": "codex_lb",
        "profile_name": str(endpoint_row["profile_name"]) if endpoint_row else "Codex",
        "base_url": base_url,
        "base_url_label": base_url or "not configured",
        "models_probe_url": resolve_url(base_url, "/v1/models") if base_url else "",
        "key_present": key_present,
        "key_fingerprint": fingerprint,
        "connection_state": connection_state,
        "rate_limited": rate_limited,
        "cooldown_remaining": cooldown_remaining,
        "models_synced": models_synced,
        "models_synced_at": models_synced_at,
        "models_sync_required": not models_synced,
            "health": {
                "reachable": bool(health_row["reachable"]) if health_row else False,
                "key_present": bool(health_row["key_present"]) if health_row else key_present,
                "last_checked": str(health_row["last_checked"]) if health_row and health_row["last_checked"] else "",
                "last_error": str(health_row["last_error"]) if health_row and health_row["last_error"] else "",
                "latency_ms": int(health_row["latency_ms"]) if health_row and health_row["latency_ms"] is not None else None,
                "result": str(availability_row["result"]) if availability_row else "checking",
                "detail": str(availability_row["detail"]) if availability_row else "checking",
            },
        "runtime": {
            "failure_count": int(runtime_row["failure_count"]) if runtime_row else 0,
            "cooldown_until": str(runtime_row["cooldown_until"]) if runtime_row and runtime_row["cooldown_until"] else "",
            "last_rate_limited_at": str(runtime_row["last_rate_limited_at"]) if runtime_row and runtime_row["last_rate_limited_at"] else "",
            "window_used": int(runtime_row["window_used"]) if runtime_row else 0,
            "window_reset_at": str(runtime_row["window_reset_at"]) if runtime_row and runtime_row["window_reset_at"] else "",
            "last_success_at": str(runtime_row["last_success_at"]) if runtime_row and runtime_row["last_success_at"] else "",
            "updated_at": str(runtime_row["updated_at"]) if runtime_row and runtime_row["updated_at"] else "",
        },
        "models_synced_label": "synced" if models_synced else "not synced",
        "live_calls_allowed": live_calls_allowed,
    }


def _groq_cloud_settings_state() -> dict[str, object]:
    _recover_provider_api_base_urls()
    key_value = get_secret("GROQ_API_KEY")
    base_url = ""
    endpoint_row = None
    health_row = None
    availability_row = None
    runtime_row = None
    models_synced = False
    models_synced_at = ""
    with connect() as conn:
        init_db(conn)
        endpoint_row = conn.execute(
            """
            SELECT e.id, e.name, e.endpoint_class, COALESCE(e.base_url, p.base_url, '') AS base_url,
                   COALESCE(e.model_name, p.model_name, '') AS model_name,
                   e.status, e.last_model_sync, p.name AS profile_name, p.live_calls_allowed
            FROM endpoints AS e
            LEFT JOIN provider_profiles AS p ON p.id = e.profile_id
            WHERE e.endpoint_class = 'groq_cloud'
            ORDER BY e.id ASC
            LIMIT 1
            """
        ).fetchone()
        if endpoint_row is not None:
            base_url = str(endpoint_row["base_url"] or "")
            models_synced_at = str(endpoint_row["last_model_sync"] or "")
            models_synced = bool(models_synced_at)
            health_row = conn.execute(
                """
                SELECT reachable, key_present, last_checked, last_error, latency_ms
                FROM endpoint_health
                WHERE endpoint_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(endpoint_row["id"]),),
            ).fetchone()
            availability_row = conn.execute(
                """
                SELECT result, detail, checked_at
                FROM availability_checks
                WHERE endpoint_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(endpoint_row["id"]),),
            ).fetchone()
            runtime_row = conn.execute(
                """
                SELECT failure_count, cooldown_until, last_rate_limited_at, window_used, window_reset_at,
                       last_success_at, updated_at
                FROM endpoint_runtime
                WHERE endpoint_id = ?
                """,
                (int(endpoint_row["id"]),),
            ).fetchone()
    key_present = bool(key_value)
    fingerprint = key_fingerprint(key_value) if key_value else "missing"
    rate_limited = False
    cooldown_remaining = ""
    if runtime_row and runtime_row["cooldown_until"]:
        cooldown_text = str(runtime_row["cooldown_until"])
        rate_limited = True
        cooldown_remaining = cooldown_text
    if health_row is None:
        connection_state = "checking" if key_present else "key missing"
    elif key_present and bool(health_row["reachable"]):
        connection_state = "connected"
    elif key_present:
        connection_state = "unhealthy"
    else:
        connection_state = "key missing"
    if rate_limited:
        connection_state = "rate-limited"
    live_calls_allowed = bool(endpoint_row["live_calls_allowed"]) if endpoint_row else False
    return {
        "endpoint_id": int(endpoint_row["id"]) if endpoint_row else None,
        "name": str(endpoint_row["name"]) if endpoint_row else "Groq Cloud",
        "endpoint_class": "groq_cloud",
        "profile_name": str(endpoint_row["profile_name"]) if endpoint_row else "Groq Cloud",
        "base_url": base_url,
        "base_url_label": base_url or "not configured",
        "models_probe_url": resolve_url(base_url, "/v1/models") if base_url else "",
        "configured_model_name": str(endpoint_row["model_name"]) if endpoint_row and endpoint_row["model_name"] else "",
        "key_present": key_present,
        "key_fingerprint": fingerprint,
        "connection_state": connection_state,
        "rate_limited": rate_limited,
        "cooldown_remaining": cooldown_remaining,
        "models_synced": models_synced,
        "models_synced_at": models_synced_at,
            "health": {
                "reachable": bool(health_row["reachable"]) if health_row else False,
                "key_present": bool(health_row["key_present"]) if health_row else key_present,
                "last_checked": str(health_row["last_checked"]) if health_row and health_row["last_checked"] else "",
                "last_error": str(health_row["last_error"]) if health_row and health_row["last_error"] else "",
                "latency_ms": int(health_row["latency_ms"]) if health_row and health_row["latency_ms"] is not None else None,
                "result": str(availability_row["result"]) if availability_row else "checking",
                "detail": str(availability_row["detail"]) if availability_row else "checking",
            },
        "runtime": {
            "failure_count": int(runtime_row["failure_count"]) if runtime_row else 0,
            "cooldown_until": str(runtime_row["cooldown_until"]) if runtime_row and runtime_row["cooldown_until"] else "",
            "last_rate_limited_at": str(runtime_row["last_rate_limited_at"]) if runtime_row and runtime_row["last_rate_limited_at"] else "",
            "window_used": int(runtime_row["window_used"]) if runtime_row else 0,
            "window_reset_at": str(runtime_row["window_reset_at"]) if runtime_row and runtime_row["window_reset_at"] else "",
            "last_success_at": str(runtime_row["last_success_at"]) if runtime_row and runtime_row["last_success_at"] else "",
            "updated_at": str(runtime_row["updated_at"]) if runtime_row and runtime_row["updated_at"] else "",
        },
        "models_synced_label": "synced" if models_synced else "not synced",
        "live_calls_allowed": live_calls_allowed,
    }


def _groq_chat_models(endpoint_id: object) -> list[dict[str, object]]:
    if endpoint_id in (None, ""):
        return []
    try:
        models = list_endpoint_models(int(endpoint_id))
    except Exception:
        models = []
    if models:
        return models
    try:
        sync_models("groq_cloud")
    except Exception:
        return []
    try:
        return list_endpoint_models(int(endpoint_id))
    except Exception:
        return []


def _update_groq_cloud_model_name(model_name: str) -> None:
    selected = str(model_name or "").strip()
    if not selected:
        return
    with connect() as conn:
        init_db(conn)
        endpoint_row = conn.execute(
            """
            SELECT id
            FROM endpoints
            WHERE endpoint_class = 'groq_cloud'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
        profile_row = conn.execute(
            """
            SELECT id
            FROM provider_profiles
            WHERE endpoint_class = 'groq_cloud'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
        if endpoint_row is not None:
            conn.execute("UPDATE endpoints SET model_name = ? WHERE id = ?", (selected, int(endpoint_row["id"])))
        if profile_row is not None:
            conn.execute("UPDATE provider_profiles SET model_name = ? WHERE id = ?", (selected, int(profile_row["id"])))
        conn.commit()


def _preferred_groq_chat_model_name(models: list[dict[str, object]], *, configured_model_name: str = "") -> str:
    active_models = [model for model in models if bool(model.get("active", True)) and _is_groq_chat_model(str(model.get("model_name") or ""))]
    active_by_name = {str(model.get("model_name") or ""): model for model in active_models if str(model.get("model_name") or "").strip()}
    configured = configured_model_name.strip()
    if configured and configured in active_by_name:
        return configured
    for preferred in GROQ_CHAT_MODEL_PREFERENCE:
        if preferred in active_by_name:
            return preferred
    if active_models:
        return str(active_models[0].get("model_name") or "").strip()
    return ""


def _is_groq_chat_model(model_name: str) -> bool:
    lowered = model_name.strip().lower()
    if not lowered:
        return False
    blocked_fragments = ("whisper", "prompt-guard", "guard", "distill-")
    if any(fragment in lowered for fragment in blocked_fragments):
        return False
    return True


def _codex_lb_composer_state(project_slug: str) -> dict[str, object]:
    settings_state = _codex_lb_settings_state()
    endpoint_id = settings_state.get("endpoint_id")
    models: list[dict[str, object]] = []
    if endpoint_id not in (None, ""):
        try:
            models = list_endpoint_models(int(endpoint_id))
        except Exception:
            models = []
    selected_model_name = ""
    if models:
        selected_model_name = str(
            next(
                (model["model_name"] for model in models if bool(model.get("active", True))),
                models[0]["model_name"],
            )
        )
    active_grant = _active_approval_grant_for_project(project_slug, "codex_lb")
    return {
        **settings_state,
        "models": models,
        "selected_model_name": selected_model_name,
        "active_grant": active_grant,
    }


def _groq_cloud_composer_state(project_slug: str) -> dict[str, object]:
    settings_state = _groq_cloud_settings_state()
    endpoint_id = settings_state.get("endpoint_id")
    models = _groq_chat_models(endpoint_id)
    selected_model_name = _preferred_groq_chat_model_name(
        models,
        configured_model_name=str(settings_state.get("configured_model_name") or ""),
    )
    configured_model_name = str(settings_state.get("configured_model_name") or "").strip()
    if selected_model_name and selected_model_name != configured_model_name:
        _update_groq_cloud_model_name(selected_model_name)
        settings_state["configured_model_name"] = selected_model_name
    active_grant = _active_approval_grant_for_project(project_slug, "groq_cloud")
    return {
        **settings_state,
        "models": models,
        "selected_model_name": selected_model_name,
        "active_grant": active_grant,
        "models_sync_required": not bool(models),
    }


def _active_approval_grant_for_project(project_slug: str, endpoint_class: str) -> dict[str, object] | None:
    project = get_project(project_slug)
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT id, project_id, endpoint_id, endpoint_class, modes, est_cost_ceiling_usd, expires_at,
                   turns_remaining, active, created_at, revoked_at
            FROM approval_grants
            WHERE active = 1
              AND project_id = ?
              AND endpoint_class = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (project.id, endpoint_class),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "project_id": int(row["project_id"]),
        "endpoint_id": None if row["endpoint_id"] is None else int(row["endpoint_id"]),
        "endpoint_class": str(row["endpoint_class"] or ""),
        "modes": json.loads(str(row["modes"] or "[]")),
        "est_cost_ceiling_usd": row["est_cost_ceiling_usd"],
        "expires_at": None if row["expires_at"] is None else str(row["expires_at"]),
        "turns_remaining": None if row["turns_remaining"] is None else int(row["turns_remaining"]),
        "active": bool(int(row["active"])),
        "created_at": str(row["created_at"]),
        "revoked_at": None if row["revoked_at"] is None else str(row["revoked_at"]),
    }


def _project_slug_for_id(project_id: int) -> str:
    with connect() as conn:
        init_db(conn)
        row = conn.execute("SELECT slug FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise ValueError(f"project {project_id} does not exist")
    return str(row["slug"])


def utc_now_plus(*, hours: int = 0, minutes: int = 0) -> str:
    from core.memory import utc_now
    from datetime import datetime, timezone

    parsed = datetime.fromisoformat(utc_now())
    return (parsed + timedelta(hours=hours, minutes=minutes)).astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _ui_context() -> dict[str, object]:
    codex_state = _codex_lb_settings_state()
    groq_state = _groq_cloud_settings_state()
    return {
        "ui_theme": theme_render_value(),
        "ui_theme_choice": current_theme(),
        "ui_theme_toggle": theme_toggle_value(),
        "ui_sidebar_collapsed": get_ui_preference("sidebar_collapsed", "1"),
        "ui_context_drawer_open": get_ui_preference("context_drawer_open", "0"),
        "ui_context_drawer_section": get_ui_preference("context_drawer_section", "memory"),
        "codex_lb_state": codex_state,
        "groq_cloud_state": groq_state,
        "provider_health_cards": [
            _provider_health_card("Codex LB", codex_state, "codex_lb"),
            _provider_health_card("Groq Cloud", groq_state, "groq_cloud"),
        ],
        "provider_health_summary": f"Codex {codex_state.get('connection_state', 'checking')} | Groq {groq_state.get('connection_state', 'checking')}",
    }


def _provider_health_card(label: str, state: dict[str, object], endpoint_class: str) -> dict[str, object]:
    return {
        "label": label,
        "endpoint_class": endpoint_class,
        "connection_state": str(state.get("connection_state") or "checking"),
        "key_fingerprint": str(state.get("key_fingerprint") or "missing"),
        "models_synced_label": str(state.get("models_synced_label") or "not synced"),
        "rate_limited": bool(state.get("rate_limited")),
        "cooldown_remaining": str(state.get("cooldown_remaining") or ""),
        "latency_ms": state.get("health", {}).get("latency_ms") if isinstance(state.get("health"), dict) else None,
        "health_detail": str(state.get("health", {}).get("detail") if isinstance(state.get("health"), dict) else ""),
        "last_checked": str(state.get("health", {}).get("last_checked") if isinstance(state.get("health"), dict) else ""),
        "last_success_at": str(state.get("runtime", {}).get("last_success_at") if isinstance(state.get("runtime"), dict) else ""),
    }


def _approval_gate_from_result(result: dict[str, object] | None) -> dict[str, object] | None:
    if not result:
        return None
    gate_id = result.get("approval_gate_id")
    if gate_id in (None, "", 0):
        return None
    try:
        gate = get_approval_gate(int(gate_id))
    except ValueError:
        return None
    return {
        "id": str(gate.id),
        "project_id": str(gate.project_id),
        "action_type": gate.action_type,
        "target_endpoint_id": "" if gate.target_endpoint_id is None else str(gate.target_endpoint_id),
        "payload_summary": gate.payload_summary,
        "status": gate.status,
        "decided_at": "" if gate.decided_at is None else gate.decided_at,
        "created_at": gate.created_at,
    }


def _is_safe_local_return(value: str) -> bool:
    from urllib.parse import urlparse

    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return False
    return value.startswith("/") or value == ""


def _request_path_with_query(request: Request) -> str:
    path = str(request.url.path)
    query = str(request.url.query)
    if query:
        return f"{path}?{query}"
    return path


def _project_for_conversation(conversation_id: int):
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT p.slug
            FROM conversations AS c
            JOIN projects AS p ON p.id = c.project_id
            WHERE c.id = ?
            """,
            (conversation_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"conversation {conversation_id} does not exist")
    return get_project(str(row["slug"]))

