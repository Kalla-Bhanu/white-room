from __future__ import annotations

from typing import Any

from adapters.base import AdapterBase


class ManualClaudeAdapter(AdapterBase):
    name = "manual_claude"

    def prepare(self, packet: dict[str, Any]) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "mode": "manual_copy_paste",
            "packet": packet,
        }

    def dry_run(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "preview": "copy/paste packet for manual Claude review",
            "request": request,
        }
