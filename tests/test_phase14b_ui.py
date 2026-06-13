from __future__ import annotations

from fastapi.testclient import TestClient

from core.ui_preferences import set_ui_preference
import web.server as server


def test_quiet_default_sidebar_and_drawer_are_hidden_by_default() -> None:
    set_ui_preference("sidebar_collapsed", "1")
    set_ui_preference("context_drawer_open", "0")
    set_ui_preference("context_drawer_section", "memory")
    client = TestClient(server.app)

    response = client.get("/chat/white-room")

    assert response.status_code == 200
    assert '<div class="section-label">Agents</div>' not in response.text
    assert response.text.count('<section class="sidebar-section">') == 4
    assert 'id="sidebar-toggle"' in response.text
    assert 'id="context-drawer-toggle"' in response.text
    assert 'checked' not in response.text.split('id="sidebar-toggle"', 1)[1].split(">", 1)[0]


def test_quiet_default_retains_route_chip_and_tools_menu() -> None:
    client = TestClient(server.app)
    response = client.get("/chat/white-room")

    assert response.status_code == 200
    assert "route-summary-chip" in response.text
    assert "Tools" in response.text
    assert "7 destinations" in response.text
