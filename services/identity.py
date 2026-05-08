"""Identity model and manager — persona configuration.

Runtime identity is loaded from identity.md. The parser accepts plain Markdown
and tolerates optional YAML frontmatter for imported content, but SKILL.md is
not a runtime source.
"""

from __future__ import annotations

import re
from pathlib import Path

import aiofiles
import yaml
from pydantic import BaseModel, Field

_TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_PROACTIVE_RE = re.compile(r"^##\s+插话方式\s*$", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


class Identity(BaseModel):
    """Persona."""

    id: str = Field(default="default", description="identifier")
    name: str = Field(description="Name of the persona")
    description: str = Field(default="", description="Short description from optional frontmatter")
    personality: str = Field(description="Core personality, written into System Prompt")
    proactive: str | None = Field(
        default=None,
        description="Proactive interjection rules; None means don't interject proactively",
    )


def _strip_frontmatter(text: str) -> tuple[dict, str]:
    """Split Markdown into (frontmatter_dict, body_text).

    Returns ({}, text) if no frontmatter is found.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, m.group(2)


def parse_identity(text: str) -> Identity | None:
    """Parse a persona Markdown file with optional YAML frontmatter."""
    fm, body = _strip_frontmatter(text)

    # Resolve name: frontmatter name > H1 title > fail
    fm_name = fm.get("name", "") if isinstance(fm, dict) else ""
    description = fm.get("description", "") if isinstance(fm, dict) else ""

    title_m = _TITLE_RE.search(body)
    if title_m:
        name = fm_name or title_m.group(1).strip()
        body_content = body[title_m.end():].strip()
    elif fm_name:
        name = fm_name
        body_content = body.strip()
    else:
        return None

    proactive: str | None = None
    split = _PROACTIVE_RE.search(body_content)
    if split:
        personality = body_content[: split.start()].strip()
        proactive = body_content[split.end():].strip() or None
    else:
        personality = body_content

    return Identity(
        id="default",
        name=name,
        description=description,
        personality=personality,
        proactive=proactive,
    )


class IdentityManager:
    def __init__(self) -> None:
        self._identity: Identity = _builtin_default()

    async def load_file(self, path: str | Path) -> None:
        p = Path(path)
        if not p.exists():
            return
        async with aiofiles.open(p, encoding="utf-8") as f:
            text = await f.read()
        identity = parse_identity(text)
        if identity:
            self._identity = identity

    def resolve(self) -> Identity:
        return self._identity


def _builtin_default() -> Identity:
    return Identity(
        id="default",
        name="默认",
        personality=(
            "你是一个QQ群聊机器人。\n"
            "- 说话简洁自然，像真人群友\n"
            "- 适当使用网络用语和表情\n"
            '- 不要自称"AI"或"语言模型"\n'
            "- 回复不要太长，一般1-3句话"
        ),
    )
