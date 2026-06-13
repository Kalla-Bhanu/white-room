from __future__ import annotations

from dataclasses import dataclass

from core.memory import list_projects
from core.ui_preferences import get_ui_preference


DEFAULT_HOME_REDIRECT = "chat"
HOME_REDIRECT_VALUES = {"chat", "dashboard"}


@dataclass(frozen=True)
class HomeResolution:
    kind: str
    target: str | None
    project_slug: str | None
    preference: str


def resolve_home_resolution() -> HomeResolution:
    preference = get_ui_preference("home_redirect", DEFAULT_HOME_REDIRECT)
    if preference not in HOME_REDIRECT_VALUES:
        preference = DEFAULT_HOME_REDIRECT

    projects = list_projects()
    if not projects:
        return HomeResolution(kind="onboarding", target=None, project_slug=None, preference=preference)

    project = projects[-1]
    if preference == "dashboard":
        return HomeResolution(kind="dashboard", target=f"/projects?project={project.slug}", project_slug=project.slug, preference=preference)
    return HomeResolution(kind="chat", target=f"/chat/{project.slug}", project_slug=project.slug, preference=preference)
