from __future__ import annotations

from dataclasses import dataclass


CODEX_MODE_DEFAULT = "manual_execution"
CODEX_MODE_PREVIEWS = ("api_preview", "cli_preview")
CODEX_MODE_MANUAL = "manual_execution"
CODEX_MODE_CHOICES = (CODEX_MODE_MANUAL, *CODEX_MODE_PREVIEWS)


@dataclass(frozen=True)
class CodexModeRecord:
    name: str
    label: str
    description: str
    approval_required: bool
    live_allowed: bool


def codex_mode_catalog() -> list[CodexModeRecord]:
    return [
        CodexModeRecord(
            name="manual_execution",
            label="Manual execution packet",
            description="Exports a packet for human-triggered Codex work and waits for import.",
            approval_required=False,
            live_allowed=False,
        ),
        CodexModeRecord(
            name="api_preview",
            label="API preview",
            description="Shows the request shape for an approval-gated future API lane without calling it.",
            approval_required=True,
            live_allowed=False,
        ),
        CodexModeRecord(
            name="cli_preview",
            label="CLI preview",
            description="Shows the command shape for an approval-gated future CLI lane without running it.",
            approval_required=True,
            live_allowed=False,
        ),
    ]


def normalize_codex_mode(value: str | None) -> str:
    if value is None:
        return CODEX_MODE_DEFAULT
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"manual", "manual_execution", "manual_exec"}:
        return CODEX_MODE_MANUAL
    if text in {"api", "api_preview", "preview_api"}:
        return "api_preview"
    if text in {"cli", "cli_preview", "preview_cli"}:
        return "cli_preview"
    return CODEX_MODE_DEFAULT


def codex_mode_label(mode: str | None) -> str:
    normalized = normalize_codex_mode(mode)
    for record in codex_mode_catalog():
        if record.name == normalized:
            return record.label
    return "Manual execution packet"
