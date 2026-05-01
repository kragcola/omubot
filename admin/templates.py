"""Template helper: locate and render Jinja2 templates from admin/templates/."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

_TEMPLATE_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

# Make settings globally available to all templates.
templates.env.globals.update(
    admin_title="Omubot Admin",
)


async def render(name: str, context: dict):
    request = context["request"]
    return templates.TemplateResponse(request=request, name=name, context=context)
