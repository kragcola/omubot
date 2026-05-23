"""Markdown parser for Persona Source Importer."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

from .models import SourceDocument, SourceField, SourceSection, SourceSpan

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_source_markdown(text: str, *, source_file: str = "source.md") -> SourceDocument:
    normalized = normalize_text(text)
    frontmatter, body, body_start_line = _split_frontmatter(normalized)
    sections = _split_sections(body, body_start_line=body_start_line)
    return SourceDocument(
        path=source_file,
        text=normalized,
        frontmatter=frontmatter,
        body=body,
        body_start_line=body_start_line,
        sections=sections,
        source_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    )


def parse_source_file(path: str | Path) -> SourceDocument:
    p = Path(path)
    return parse_source_markdown(p.read_text(encoding="utf-8"), source_file=p.name)


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str, int]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text, 1
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    body = text[match.end():]
    body_start_line = text[: match.end()].count("\n") + 1
    return data, body, body_start_line


def _split_sections(body: str, *, body_start_line: int) -> list[SourceSection]:
    lines = body.split("\n")
    headings: list[tuple[int, int, str]] = []
    for offset, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if not match:
            continue
        headings.append((body_start_line + offset, len(match.group(1)), match.group(2).strip()))

    sections: list[SourceSection] = []
    for index, (line_no, level, title) in enumerate(headings):
        start_offset = line_no - body_start_line
        body_start_offset = start_offset + 1
        next_offset = (
            headings[index + 1][0] - body_start_line
            if index + 1 < len(headings)
            else len(lines)
        )
        section_body_lines = lines[body_start_offset:next_offset]
        while section_body_lines and not section_body_lines[0].strip():
            section_body_lines.pop(0)
            body_start_offset += 1
        while section_body_lines and not section_body_lines[-1].strip():
            section_body_lines.pop()
        sections.append(
            SourceSection(
                title=title,
                level=level,
                line=line_no,
                body="\n".join(section_body_lines).strip(),
                body_start_line=body_start_line + body_start_offset,
                end_line=body_start_line + next_offset - 1,
            )
        )
    return sections


def clean_inline(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"(\*\*|__)(.*?)\1", r"\2", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    return cleaned.strip()


def bullet_items(section: SourceSection | None, *, source_file: str = "source.md") -> list[SourceField]:
    if section is None:
        return []
    fields: list[SourceField] = []
    for offset, line in enumerate(section.body.split("\n")):
        match = _BULLET_RE.match(line) or _NUMBERED_RE.match(line)
        if not match:
            continue
        value = clean_inline(match.group(1))
        if not value:
            continue
        line_no = section.body_start_line + offset
        fields.append(
            SourceField(
                key="item",
                value=value,
                span=SourceSpan(source_file, (line_no, line_no)),
            )
        )
    return fields


def first_prefixed_value(
    section: SourceSection | None,
    prefix: str,
    *,
    source_file: str = "source.md",
) -> SourceField | None:
    if section is None:
        return None
    needle = prefix.strip().rstrip(":：")
    for offset, line in enumerate(section.body.split("\n")):
        stripped = line.strip()
        match = _BULLET_RE.match(stripped)
        candidate = match.group(1).strip() if match else stripped
        if not candidate.startswith(needle):
            continue
        parts = re.split(r"[:：]", candidate, maxsplit=1)
        if len(parts) != 2:
            continue
        value = clean_inline(parts[1])
        if not value:
            continue
        line_no = section.body_start_line + offset
        return SourceField(
            key=needle,
            value=value,
            span=SourceSpan(source_file, (line_no, line_no)),
        )
    return None


def list_after_label(
    section: SourceSection | None,
    label: str,
    *,
    source_file: str = "source.md",
) -> list[SourceField]:
    if section is None:
        return []
    needle = label.strip().rstrip(":：")
    lines = section.body.split("\n")
    start_index: int | None = None
    for offset, line in enumerate(lines):
        if line.strip().rstrip(":：") == needle:
            start_index = offset + 1
            break
    if start_index is None:
        return []
    result: list[SourceField] = []
    for offset in range(start_index, len(lines)):
        stripped = lines[offset].strip()
        if not stripped:
            if result:
                break
            continue
        if stripped.endswith((":", "：")) and result:
            break
        match = _BULLET_RE.match(stripped) or _NUMBERED_RE.match(stripped)
        if not match:
            if result:
                break
            continue
        value = clean_inline(match.group(1))
        if value:
            line_no = section.body_start_line + offset
            result.append(SourceField(label, value, SourceSpan(source_file, (line_no, line_no))))
    return result
