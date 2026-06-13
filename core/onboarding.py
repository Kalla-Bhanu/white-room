from __future__ import annotations

import os
from dataclasses import dataclass

from core.health import runner_status_snapshot
from core.memory import list_projects
from core.providers import PROVIDER_PROFILE_SEEDS


@dataclass(frozen=True)
class OnboardingCard:
    key: str
    title: str
    status: str
    guidance: str
    primary_label: str
    primary_href: str
    secondary_label: str | None = None
    secondary_href: str | None = None
    badge_class: str = "accent-4"
    dot_class: str = "warn"


@dataclass(frozen=True)
class OnboardingState:
    project_count: int
    has_project: bool
    has_conversation: bool
    ollama_reachable: bool
    any_keys_present: bool
    codex_lb_available: bool
    claude_manual_available: bool
    runner_message: str
    key_labels: list[str]
    selected_state: str
    hero_card: OnboardingCard
    state_cards: list[OnboardingCard]


def build_onboarding_state(preview_state: str | None = None) -> OnboardingState:
    projects = list_projects()
    runner = runner_status_snapshot()
    key_labels = _present_key_labels()
    has_conversation = _has_conversation()
    has_project = bool(projects)
    ollama_reachable = bool(runner.get("reachable"))
    any_keys_present = bool(key_labels)
    codex_lb_available = False
    claude_manual_available = True

    state_cards = _state_cards(
        has_project=has_project,
        has_conversation=has_conversation,
        ollama_reachable=ollama_reachable,
        any_keys_present=any_keys_present,
        codex_lb_available=codex_lb_available,
        claude_manual_available=claude_manual_available,
        runner_message=str(runner.get("message") or ""),
    )
    selected_state = _resolve_selected_state(preview_state, state_cards, has_project, has_conversation, ollama_reachable, any_keys_present, codex_lb_available)
    hero_card = next((card for card in state_cards if card.key == selected_state), state_cards[-1])

    return OnboardingState(
        project_count=len(projects),
        has_project=has_project,
        has_conversation=has_conversation,
        ollama_reachable=ollama_reachable,
        any_keys_present=any_keys_present,
        codex_lb_available=codex_lb_available,
        claude_manual_available=claude_manual_available,
        runner_message=str(runner.get("message") or ""),
        key_labels=key_labels,
        selected_state=selected_state,
        hero_card=hero_card,
        state_cards=state_cards,
    )


def _state_cards(
    *,
    has_project: bool,
    has_conversation: bool,
    ollama_reachable: bool,
    any_keys_present: bool,
    codex_lb_available: bool,
    claude_manual_available: bool,
    runner_message: str,
) -> list[OnboardingCard]:
    return [
        OnboardingCard(
            key="first_project",
            title="First project",
            status="first run",
            guidance="Create the first local project to unlock chat, board, routes, and task packets.",
            primary_label="Create project",
            primary_href="/projects",
            secondary_label="Open workspace",
            secondary_href="/chat/white-room",
            badge_class="accent-4" if has_project else "accent-3",
            dot_class="ok" if has_project else "warn",
        ),
        OnboardingCard(
            key="no_conversation",
            title="No conversation",
            status="chat ready",
            guidance="Open a workspace and start the first thread so the local memory rail has something to attach to.",
            primary_label="Open chat",
            primary_href="/chat/white-room",
            secondary_label="Open project brain",
            secondary_href="/project/white-room",
            badge_class="accent-5" if has_conversation else "accent-3",
            dot_class="ok" if has_conversation else "warn",
        ),
        OnboardingCard(
            key="no_local_runner",
            title="No local runner",
            status="localhost only",
            guidance=(
                "Preview the offline lane: stop Ollama or LM Studio locally and the workspace will keep the fallback guidance honest."
                if ollama_reachable
                else (runner_message or "Start Ollama or LM Studio locally; the workspace will stay honest until a runner is reachable.")
            ),
            primary_label="Open runner",
            primary_href="/runner",
            secondary_label="Open settings",
            secondary_href="/settings",
            badge_class="accent-5" if ollama_reachable else "accent-3",
            dot_class="ok" if ollama_reachable else "bad",
        ),
        OnboardingCard(
            key="no_api_keys",
            title="No API keys",
            status="presence only",
            guidance="Connect real keys in Settings when you need them; the UI only shows presence, never values.",
            primary_label="Open settings",
            primary_href="/settings",
            secondary_label="View endpoints",
            secondary_href="/endpoints",
            badge_class="accent-5" if any_keys_present else "accent-3",
            dot_class="ok" if any_keys_present else "warn",
        ),
        OnboardingCard(
            key="codex_lb_unavailable",
            title="Codex LB unavailable",
            status="manual packet lane",
            guidance="Use the offline Codex packet lane until the later gated mode is explicitly available.",
            primary_label="Open chat",
            primary_href="/chat/white-room",
            secondary_label="Open board",
            secondary_href="/board/white-room",
            badge_class="accent-4" if codex_lb_available else "accent-3",
            dot_class="ok" if codex_lb_available else "warn",
        ),
        OnboardingCard(
            key="claude_manual_available",
            title="Claude manual available",
            status="always available",
            guidance="Export a conversation, paste the reply back, and import it as a handoff without touching cloud automation.",
            primary_label="Open manual Claude",
            primary_href="/chat/white-room",
            secondary_label="Open import lane",
            secondary_href="/import/manual",
            badge_class="accent-5" if claude_manual_available else "accent-3",
            dot_class="ok" if claude_manual_available else "warn",
        ),
    ]


def _resolve_selected_state(
    preview_state: str | None,
    cards: list[OnboardingCard],
    has_project: bool,
    has_conversation: bool,
    ollama_reachable: bool,
    any_keys_present: bool,
    codex_lb_available: bool,
) -> str:
    valid_keys = {card.key for card in cards}
    if preview_state and preview_state in valid_keys:
        return preview_state
    if not has_project:
        return "first_project"
    if not has_conversation:
        return "no_conversation"
    if not ollama_reachable:
        return "no_local_runner"
    if not any_keys_present:
        return "no_api_keys"
    if not codex_lb_available:
        return "codex_lb_unavailable"
    return "claude_manual_available"


def _has_conversation() -> bool:
    from core.db import connect, init_db

    with connect() as conn:
        init_db(conn)
        row = conn.execute("SELECT 1 FROM conversations LIMIT 1").fetchone()
    return row is not None


def _present_key_labels() -> list[str]:
    labels: list[str] = []
    required_keys: set[str] = set()
    for profile in PROVIDER_PROFILE_SEEDS:
        for name in profile.get("required_env_vars", []):
            required_keys.add(str(name))
    for name in sorted(required_keys):
        if os.getenv(name, "").strip():
            labels.append(name)
    return labels
