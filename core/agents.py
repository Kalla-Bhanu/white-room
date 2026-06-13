from __future__ import annotations

from dataclasses import asdict, dataclass

from core.db import connect, init_db
from core.memory import get_project, utc_now


BRAIN_FILE_OWNERS = {
    "active_plan.md": "orchestrator",
    "architecture.md": "architecture",
    "business_scope.md": "business_scope",
    "current_status.md": "orchestrator",
    "decisions.md": "manual_claude",
    "errors.md": "verification",
    "handoffs.md": "handoff_memory",
    "model_routes.md": "orchestrator",
    "tasks.md": "orchestrator",
    "verification.md": "verification",
}

VALID_THREADS = {
    "architecture",
    "business_scope",
    "handoff_memory",
    "implementation",
    "manual_claude",
    "orchestrator",
    "verification",
}

ALLOWED_TRANSITIONS = {
    "orchestrator": {"architecture", "business_scope", "implementation", "verification", "handoff_memory"},
    "architecture": {"implementation", "orchestrator"},
    "business_scope": {"orchestrator", "architecture"},
    "implementation": {"verification", "orchestrator"},
    "verification": {"orchestrator", "handoff_memory"},
    "handoff_memory": {"orchestrator"},
    "manual_claude": {"orchestrator"},
}


@dataclass(frozen=True)
class ThreadStateMachine:
    current_thread: str

    def can_transition(self, next_thread: str) -> bool:
        return next_thread in ALLOWED_TRANSITIONS.get(self.current_thread, set())

    def transition(self, next_thread: str) -> "ThreadStateMachine":
        if next_thread not in VALID_THREADS:
            raise ValueError(f"invalid thread '{next_thread}'")
        if not self.can_transition(next_thread):
            raise ValueError(f"illegal thread transition {self.current_thread} -> {next_thread}")
        return ThreadStateMachine(current_thread=next_thread)


@dataclass(frozen=True)
class AgentThreadSpec:
    thread_name: str
    label: str
    purpose: str
    allowed_files: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    lane_preference: str
    escalates_when: str
    writes_handoff: str
    ui: str


THREAD_SPECS: tuple[AgentThreadSpec, ...] = (
    AgentThreadSpec(
        thread_name="orchestrator",
        label="Orchestrator",
        purpose="State, next task, routing",
        allowed_files=("current_status.md", "tasks.md", "model_routes.md"),
        allowed_actions=("classify", "route", "assign", "approve step"),
        lane_preference="manual_claude hard; deterministic else",
        escalates_when="priority/design conflict",
        writes_handoff="every dispatch",
        ui="board column + active chip",
    ),
    AgentThreadSpec(
        thread_name="business_scope",
        label="Business Scope",
        purpose="Purpose, constraints, metrics",
        allowed_files=("business_scope.md",),
        allowed_actions=("define scope", "update scope"),
        lane_preference="manual_claude",
        escalates_when="scope conflict",
        writes_handoff="scope-set",
        ui="column with scope",
    ),
    AgentThreadSpec(
        thread_name="architecture",
        label="Architecture",
        purpose="Design and decisions",
        allowed_files=("architecture.md", "decisions.md"),
        allowed_actions=("design", "record decision"),
        lane_preference="manual_claude / anthropic",
        escalates_when="design risk",
        writes_handoff="design + decision",
        ui="column with latest decision",
    ),
    AgentThreadSpec(
        thread_name="implementation",
        label="Implementation",
        purpose="Build and repo changes",
        allowed_files=("code/artifacts", "tasks.md"),
        allowed_actions=("execute packets",),
        lane_preference="codex_lb (gated); local trivial",
        escalates_when="repeated failure",
        writes_handoff="execution + run",
        ui="column with active run",
    ),
    AgentThreadSpec(
        thread_name="verification",
        label="Verification",
        purpose="Tests, gates, promotion",
        allowed_files=("verification.md", "errors.md"),
        allowed_actions=("test", "score", "promote/reject"),
        lane_preference="deterministic + local; manual judgment",
        escalates_when="persistent failure",
        writes_handoff="pass/fail",
        ui="column with last result",
    ),
    AgentThreadSpec(
        thread_name="handoff_memory",
        label="Handoff / Memory",
        purpose="Continuity and reconciliation",
        allowed_files=("handoffs.md", "current_status.md"),
        allowed_actions=("summarize", "reconcile"),
        lane_preference="local summary; manual conflict",
        escalates_when="files disagree",
        writes_handoff="summary",
        ui="timeline feed",
    ),
    AgentThreadSpec(
        thread_name="manual_claude",
        label="Research",
        purpose="Gather context and compare options",
        allowed_files=("research notes",),
        allowed_actions=("search", "collect"),
        lane_preference="manual_claude / approved cloud",
        escalates_when="needs paid depth",
        writes_handoff="research",
        ui="column with findings",
    ),
    AgentThreadSpec(
        thread_name="ui_design",
        label="UI / Design",
        purpose="Visual design and component direction",
        allowed_files=("design notes", "web/ specs"),
        allowed_actions=("propose design", "specify components"),
        lane_preference="manual_claude / anthropic",
        escalates_when="visual judgment",
        writes_handoff="design",
        ui="column with design status",
    ),
    AgentThreadSpec(
        thread_name="endpoint_provider",
        label="Endpoint / Provider",
        purpose="Profiles, health, key presence",
        allowed_files=("operational tables",),
        allowed_actions=("add profile", "run health"),
        lane_preference="deterministic",
        escalates_when="provider misconfig",
        writes_handoff="provider",
        ui="health panel",
    ),
    AgentThreadSpec(
        thread_name="local_model_runner",
        label="Local Model Runner",
        purpose="Manage local servers",
        allowed_files=("operational",),
        allowed_actions=("check localhost", "run local"),
        lane_preference="ollama / lmstudio local",
        escalates_when="local down",
        writes_handoff="runner",
        ui="runner status panel",
    ),
)

THREAD_NAME_LOOKUP = {spec.thread_name: spec for spec in THREAD_SPECS}


@dataclass(frozen=True)
class AgentThreadRecord:
    id: int
    project_id: int
    thread_name: str
    label: str
    purpose: str
    allowed_files: tuple[str, ...]
    allowed_files_text: str
    allowed_actions: tuple[str, ...]
    allowed_actions_text: str
    lane_preference: str
    escalates_when: str
    writes_handoff: str
    ui: str
    state: str
    state_label: str
    current_task_id: int | None
    current_task_title: str
    current_task_status: str
    current_task_updated_at: str
    task_count: int
    last_handoff_id: int | None
    last_handoff_summary: str
    last_handoff_created_at: str
    updated_at: str


def assert_single_writer(brain_filename: str, thread_name: str) -> None:
    owner = BRAIN_FILE_OWNERS.get(brain_filename)
    if owner is None:
        raise ValueError(f"unknown brain file '{brain_filename}'")
    if thread_name != owner:
        raise ValueError(f"brain file '{brain_filename}' is owned by '{owner}', not '{thread_name}'")


def seed_agent_threads_for_project(connection, project_id: int, created_at: str | None = None) -> None:
    timestamp = created_at or utc_now()
    for spec in THREAD_SPECS:
        connection.execute(
            """
            INSERT OR IGNORE INTO agent_threads (
                project_id, thread_name, state, current_task_id, last_handoff_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (project_id, spec.thread_name, "idle", None, None, timestamp),
        )


def seed_agent_threads_for_all_projects(connection) -> None:
    rows = connection.execute("SELECT id, created_at FROM projects ORDER BY id ASC").fetchall()
    for row in rows:
        seed_agent_threads_for_project(connection, int(row["id"]), str(row["created_at"]))


def record_agent_thread_step(
    project_slug: str,
    thread_name: str,
    *,
    task_id: int | None,
    handoff_id: int | None,
    state: str = "active",
) -> None:
    if thread_name not in THREAD_NAME_LOOKUP:
        raise ValueError(f"unknown agent thread '{thread_name}'")

    project = get_project(project_slug)
    updated_at = utc_now()
    with connect() as conn:
        init_db(conn)
        seed_agent_threads_for_project(conn, project.id, updated_at)
        conn.execute(
            """
            UPDATE agent_threads
            SET state = ?, current_task_id = ?, last_handoff_id = ?, updated_at = ?
            WHERE project_id = ? AND thread_name = ?
            """,
            (state, task_id, handoff_id, updated_at, project.id, thread_name),
        )
        conn.commit()


def list_agent_threads(project_slug: str) -> list[dict[str, object]]:
    project = get_project(project_slug)
    with connect() as conn:
        init_db(conn)
        seed_agent_threads_for_project(conn, project.id)

        thread_rows = conn.execute(
            """
            SELECT id, project_id, thread_name, state, current_task_id, last_handoff_id, updated_at
            FROM agent_threads
            WHERE project_id = ?
            ORDER BY id ASC
            """,
            (project.id,),
        ).fetchall()
        task_rows = conn.execute(
            """
            SELECT id, title, status, thread, updated_at
            FROM tasks
            WHERE project_id = ?
            ORDER BY updated_at DESC, id DESC
            """,
            (project.id,),
        ).fetchall()
        handoff_rows = conn.execute(
            """
            SELECT id, summary, thread_to, created_at
            FROM handoffs
            WHERE project_id = ?
            ORDER BY id DESC
            """,
            (project.id,),
        ).fetchall()

    latest_task_by_thread: dict[str, object] = {}
    task_by_id: dict[int, object] = {}
    task_count_by_thread: dict[str, int] = {}
    for row in task_rows:
        thread_name = str(row["thread"] or "")
        task_id = int(row["id"])
        task_by_id[task_id] = row
        task_count_by_thread[thread_name] = task_count_by_thread.get(thread_name, 0) + 1
        latest_task_by_thread.setdefault(thread_name, row)

    latest_handoff_by_thread: dict[str, object] = {}
    for row in handoff_rows:
        thread_name = str(row["thread_to"] or "")
        latest_handoff_by_thread.setdefault(thread_name, row)

    records: list[dict[str, object]] = []
    for row in thread_rows:
        spec = THREAD_NAME_LOOKUP[str(row["thread_name"])]
        current_task = None
        if row["current_task_id"] is not None:
            current_task = task_by_id.get(int(row["current_task_id"]))
        if current_task is None:
            current_task = latest_task_by_thread.get(spec.thread_name)

        current_state = str(row["state"] or "idle")
        if current_task is not None:
            task_status = str(current_task["status"])
            if task_status == "blocked":
                current_state = "blocked"
            elif task_status in {"in_progress", "running"}:
                current_state = "active"
            elif current_state not in {"active", "blocked"} and task_status == "done":
                current_state = "complete"

        last_handoff = latest_handoff_by_thread.get(spec.thread_name)
        records.append(
            {
                "id": int(row["id"]),
                "project_id": int(row["project_id"]),
                "thread_name": spec.thread_name,
                "label": spec.label,
                "purpose": spec.purpose,
                "allowed_files": list(spec.allowed_files),
                "allowed_files_text": ", ".join(spec.allowed_files),
                "allowed_actions": list(spec.allowed_actions),
                "allowed_actions_text": ", ".join(spec.allowed_actions),
                "lane_preference": spec.lane_preference,
                "escalates_when": spec.escalates_when,
                "writes_handoff": spec.writes_handoff,
                "ui": spec.ui,
                "state": current_state,
                "state_label": current_state.replace("_", " "),
                "current_task_id": None if current_task is None else int(current_task["id"]),
                "current_task_title": "" if current_task is None else str(current_task["title"]),
                "current_task_status": "" if current_task is None else str(current_task["status"]),
                "current_task_updated_at": "" if current_task is None else str(current_task["updated_at"]),
                "task_count": task_count_by_thread.get(spec.thread_name, 0),
                "last_handoff_id": None if last_handoff is None else int(last_handoff["id"]),
                "last_handoff_summary": "" if last_handoff is None else str(last_handoff["summary"]),
                "last_handoff_created_at": "" if last_handoff is None else str(last_handoff["created_at"]),
                "updated_at": str(row["updated_at"]),
            }
        )
    return records


def thread_catalog() -> tuple[dict[str, object], ...]:
    return tuple(asdict(spec) for spec in THREAD_SPECS)
