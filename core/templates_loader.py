from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined


APP_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = APP_ROOT / "templates"
PROJECT_TEMPLATES_DIR = TEMPLATES_DIR / "projects"


def render_template(template_name: str, **context: Any) -> str:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    template = environment.get_template(template_name)
    return template.render(**context)


def project_template_exists(template_name: str) -> bool:
    if template_name in {"", "default"}:
        return True
    return (PROJECT_TEMPLATES_DIR / template_name).is_dir()


def project_template_path(template_name: str, relative_path: str) -> str:
    if template_name in {"", "default"}:
        return relative_path
    return f"projects/{template_name}/{relative_path}"
