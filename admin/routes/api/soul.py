"""JSON API: soul editor — structured editor model for identity.md/instruction.md."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_SUBHEADING_RE = re.compile(r"^#{3,6}\s+(.+?)\s*$")
_PROACTIVE_RE = re.compile(r"^##\s+插话方式\s*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^-\s+(.*)$")
_NUMBERED_RE = re.compile(r"^\d+\.\s+(.*)$")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MARKDOWN_STRONG_RE = re.compile(r"(\*\*|__)(.*?)\1")
_MARKDOWN_EM_RE = re.compile(r"(?<!\*)\*(?!\*)([^*\n]+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)([^_\n]+?)(?<!_)_(?!_)")
_MARKDOWN_CODE_RE = re.compile(r"`([^`]+)`")

_DEFAULT_PERSONA_SECTION_TITLE = "概述"
_DEFAULT_INSTRUCTION_SECTION_TITLE = "行为规则"
_ALLOWED_BLOCK_TYPES = {
    "paragraph",
    "bullet_list",
    "numbered_list",
    "kv_table",
    "free_text",
}
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PERSONA_GUIDE_PATH = _REPO_ROOT / "docs/ai-persona-generation-rules.md"


def create_soul_router(
    *,
    soul_dir: str = "config/soul",
    persona_runtime: Any = None,
) -> APIRouter:
    router = APIRouter()
    _soul = Path(soul_dir)

    def _read(name: str) -> str:
        path = _soul / name
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def _write(name: str, content: str) -> None:
        _soul.mkdir(parents=True, exist_ok=True)
        (_soul / name).write_text(content, encoding="utf-8")

    def _normalize_text(text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def _clean_markdown_markup(text: str) -> str:
        """Remove inline Markdown markers before exposing text in structured fields."""
        cleaned = _normalize_text(str(text))
        cleaned = _MARKDOWN_IMAGE_RE.sub(lambda match: match.group(1), cleaned)
        cleaned = _MARKDOWN_LINK_RE.sub(lambda match: match.group(1), cleaned)
        cleaned = _MARKDOWN_STRONG_RE.sub(lambda match: match.group(2), cleaned)
        cleaned = _MARKDOWN_EM_RE.sub(lambda match: match.group(1) or match.group(2) or "", cleaned)
        cleaned = _MARKDOWN_CODE_RE.sub(lambda match: match.group(1), cleaned)
        cleaned = re.sub(r"(?m)^\s*>\s?", "", cleaned)
        cleaned = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", cleaned)
        cleaned = re.sub(r"(?m)^\s*[-*+]\s+", "", cleaned)
        cleaned = re.sub(r"(?m)^\s*\d+\.\s+", "", cleaned)
        return cleaned.strip()

    def _slugify(text: str) -> str:
        slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", text).strip("-").lower()
        return slug or "section"

    def _make_section_id(title: str, index: int) -> str:
        return f"section-{index}-{_slugify(title)}"

    def _make_block_id(section_id: str, index: int) -> str:
        return f"{section_id}-block-{index}"

    def _extract_title(text: str, fallback: str = "") -> tuple[str, str]:
        normalized = _normalize_text(text)
        match = _TITLE_RE.search(normalized)
        if match:
            return _clean_markdown_markup(match.group(1)), normalized[match.end():].strip()
        return _clean_markdown_markup(fallback), normalized.strip()

    def _split_proactive(text: str) -> tuple[str, str]:
        normalized = _normalize_text(text).strip()
        match = _PROACTIVE_RE.search(normalized)
        if not match:
            return normalized, ""
        personality = normalized[:match.start()].strip()
        proactive = normalized[match.end():].strip()
        return personality, proactive

    def _extract_leading_description(text: str) -> tuple[str, str]:
        normalized = _normalize_text(text).strip()
        match = _H2_RE.search(normalized)
        if not match:
            return normalized, ""
        return normalized[:match.start()].strip(), normalized[match.start():].strip()

    def _is_table_start(lines: list[str], index: int) -> bool:
        if index + 1 >= len(lines):
            return False
        head = lines[index].strip()
        separator = lines[index + 1].strip()
        return (
            head.startswith("|")
            and head.endswith("|")
            and separator.startswith("|")
            and separator.endswith("|")
            and "---" in separator
        )

    def _parse_table_row(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    def _looks_like_free_text(lines: list[str]) -> bool:
        return any(
            line.strip().startswith((">", "```", "<!--"))
            or line.startswith("    ")
            for line in lines
        )

    def _consume_table(lines: list[str], index: int) -> tuple[dict[str, Any], int]:
        header = _parse_table_row(lines[index])
        columns = [
            _clean_markdown_markup(header[0]) if len(header) > 0 and header[0] else "项",
            _clean_markdown_markup(header[1]) if len(header) > 1 and header[1] else "内容",
        ]
        rows: list[dict[str, str]] = []
        index += 2
        while index < len(lines):
            stripped = lines[index].strip()
            if not stripped or not stripped.startswith("|") or not stripped.endswith("|"):
                break
            cells = _parse_table_row(lines[index])
            key = _clean_markdown_markup(cells[0]) if len(cells) > 0 else ""
            value = _clean_markdown_markup(" | ".join(cells[1:])) if len(cells) > 1 else ""
            rows.append({"key": key, "value": value})
            index += 1
        return {
            "type": "kv_table",
            "columns": columns,
            "rows": rows,
        }, index

    def _consume_list(
        lines: list[str],
        index: int,
        *,
        numbered: bool,
    ) -> tuple[dict[str, Any], int]:
        pattern = _NUMBERED_RE if numbered else _BULLET_RE
        items: list[str] = []
        while index < len(lines):
            match = pattern.match(lines[index].strip())
            if not match:
                break
            item_lines = [match.group(1).strip()]
            index += 1
            while index < len(lines):
                stripped = lines[index].strip()
                if (
                    not stripped
                    or _SUBHEADING_RE.match(stripped)
                    or _is_table_start(lines, index)
                    or _BULLET_RE.match(stripped)
                    or _NUMBERED_RE.match(stripped)
                ):
                    break
                item_lines.append(stripped)
                index += 1
            items.append(_clean_markdown_markup("\n".join(part for part in item_lines if part)))
            if index < len(lines) and not lines[index].strip():
                break
        return {
            "type": "numbered_list" if numbered else "bullet_list",
            "items": items,
        }, index

    def _consume_text(lines: list[str], index: int) -> tuple[dict[str, Any], int]:
        text_lines: list[str] = []
        while index < len(lines):
            stripped = lines[index].strip()
            if (
                not stripped
                or _SUBHEADING_RE.match(stripped)
                or _is_table_start(lines, index)
                or _BULLET_RE.match(stripped)
                or _NUMBERED_RE.match(stripped)
            ):
                break
            text_lines.append(lines[index].rstrip())
            index += 1
        text = "\n".join(text_lines).strip()
        return {
            "type": "free_text" if _looks_like_free_text(text_lines) else "paragraph",
            "text": _clean_markdown_markup(text),
        }, index

    def _parse_blocks(section_id: str, content: str) -> list[dict[str, Any]]:
        lines = _normalize_text(content).split("\n")
        blocks: list[dict[str, Any]] = []
        pending_heading = ""
        index = 0
        block_index = 0

        while index < len(lines):
            stripped = lines[index].strip()
            if not stripped:
                index += 1
                continue

            heading_match = _SUBHEADING_RE.match(stripped)
            if heading_match:
                pending_heading = _clean_markdown_markup(heading_match.group(1))
                index += 1
                continue

            if _is_table_start(lines, index):
                block, index = _consume_table(lines, index)
            elif _BULLET_RE.match(stripped):
                block, index = _consume_list(lines, index, numbered=False)
            elif _NUMBERED_RE.match(stripped):
                block, index = _consume_list(lines, index, numbered=True)
            else:
                block, index = _consume_text(lines, index)

            block["id"] = _make_block_id(section_id, block_index)
            block["heading"] = pending_heading
            blocks.append(block)
            pending_heading = ""
            block_index += 1

        return blocks

    def _split_sections(text: str, default_title: str) -> list[dict[str, Any]]:
        normalized = _normalize_text(text).strip()
        if not normalized:
            return []

        matches = list(_H2_RE.finditer(normalized))
        sections: list[dict[str, Any]] = []

        def add_section(title: str, body: str) -> None:
            section_id = _make_section_id(title, len(sections))
            sections.append({
                "id": section_id,
                "title": title,
                "blocks": _parse_blocks(section_id, body),
            })

        if not matches:
            add_section(default_title, normalized)
            return sections

        intro = normalized[:matches[0].start()].strip()
        if intro:
            add_section(default_title, intro)

        for idx, match in enumerate(matches):
            title = _clean_markdown_markup(match.group(1))
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(normalized)
            body = normalized[match.end():end].strip()
            add_section(title, body)

        return sections

    def _build_legacy_editor() -> dict[str, Any]:
        identity_title, identity_body = _extract_title(_read("identity.md"))
        personality_text, proactive_text = _split_proactive(identity_body)
        description, persona_text = _extract_leading_description(personality_text)
        persona_sections = _split_sections(persona_text, _DEFAULT_PERSONA_SECTION_TITLE)
        instruction_sections = _split_sections(_read("instruction.md"), _DEFAULT_INSTRUCTION_SECTION_TITLE)

        return {
            "format_mode": "legacy",
            "migration_pending": False,
            "editor": {
                "meta": {
                    "name": identity_title,
                    "description": description,
                    "display_title": identity_title,
                },
                "persona_sections": persona_sections,
                "instruction_sections": instruction_sections,
                "proactive": {
                    "enabled": bool(proactive_text.strip()),
                    "text": proactive_text,
                },
            },
        }

    def _normalize_rows(rows: Any) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        if not isinstance(rows, list):
            return normalized
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = _clean_markdown_markup(row.get("key", ""))
            value = _clean_markdown_markup(row.get("value", ""))
            if key or value:
                normalized.append({"key": key, "value": value})
        return normalized

    def _normalize_items(items: Any) -> list[str]:
        normalized: list[str] = []
        if not isinstance(items, list):
            return normalized
        for item in items:
            text = _clean_markdown_markup(item)
            if text:
                normalized.append(text)
        return normalized

    def _normalize_blocks(section_id: str, blocks: Any) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        if not isinstance(blocks, list):
            return normalized

        for index, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type", "free_text"))
            if block_type not in _ALLOWED_BLOCK_TYPES:
                block_type = "free_text"
            normalized_block: dict[str, Any] = {
                "id": str(block.get("id", "")).strip() or _make_block_id(section_id, index),
                "type": block_type,
                "heading": _clean_markdown_markup(block.get("heading", "")),
            }
            if block_type in {"paragraph", "free_text"}:
                normalized_block["text"] = _clean_markdown_markup(block.get("text", ""))
            elif block_type in {"bullet_list", "numbered_list"}:
                normalized_block["items"] = _normalize_items(block.get("items", []))
            elif block_type == "kv_table":
                columns = block.get("columns", ["项", "内容"])
                if not isinstance(columns, list):
                    columns = ["项", "内容"]
                left = _clean_markdown_markup(columns[0] if len(columns) > 0 else "项") or "项"
                right = _clean_markdown_markup(columns[1] if len(columns) > 1 else "内容") or "内容"
                normalized_block["columns"] = [left, right]
                normalized_block["rows"] = _normalize_rows(block.get("rows", []))
            normalized.append(normalized_block)

        return normalized

    def _normalize_sections(sections: Any) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        if not isinstance(sections, list):
            return normalized

        for index, section in enumerate(sections):
            if not isinstance(section, dict):
                continue
            title = _clean_markdown_markup(section.get("title", "")) or f"章节 {index + 1}"
            section_id = str(section.get("id", "")).strip() or _make_section_id(title, index)
            normalized.append({
                "id": section_id,
                "title": title,
                "blocks": _normalize_blocks(section_id, section.get("blocks", [])),
            })

        return normalized

    def _normalize_editor(payload: Any) -> dict[str, Any]:
        editor = payload if isinstance(payload, dict) else {}
        meta = editor.get("meta", {}) if isinstance(editor, dict) else {}
        proactive = editor.get("proactive", {}) if isinstance(editor, dict) else {}

        display_title = _clean_markdown_markup(meta.get("display_title", ""))
        name = _clean_markdown_markup(meta.get("name", "")) or display_title
        description = _clean_markdown_markup(meta.get("description", ""))

        return {
            "meta": {
                "name": name,
                "description": description,
                "display_title": display_title or name,
            },
            "persona_sections": _normalize_sections(editor.get("persona_sections", [])),
            "instruction_sections": _normalize_sections(editor.get("instruction_sections", [])),
            "proactive": {
                "enabled": bool(proactive.get("enabled", False)),
                "text": _clean_markdown_markup(proactive.get("text", "")),
            },
        }

    def _render_block(block: dict[str, Any]) -> str:
        block_type = block.get("type", "free_text")
        heading = str(block.get("heading", "")).strip()
        content = ""

        if block_type in {"paragraph", "free_text"}:
            content = str(block.get("text", "")).strip()
        elif block_type == "bullet_list":
            items = _normalize_items(block.get("items", []))
            content = "\n".join(f"- {item}" for item in items)
        elif block_type == "numbered_list":
            items = _normalize_items(block.get("items", []))
            content = "\n".join(f"{idx}. {item}" for idx, item in enumerate(items, start=1))
        elif block_type == "kv_table":
            columns = block.get("columns", ["项", "内容"])
            if not isinstance(columns, list):
                columns = ["项", "内容"]
            left = str(columns[0] if len(columns) > 0 else "项").strip() or "项"
            right = str(columns[1] if len(columns) > 1 else "内容").strip() or "内容"
            rows = _normalize_rows(block.get("rows", []))
            table_lines = [
                f"| {left} | {right} |",
                "| --- | --- |",
            ]
            table_lines.extend(
                f"| {row['key']} | {row['value']} |"
                for row in rows
            )
            content = "\n".join(table_lines)

        if heading and content:
            return f"### {heading}\n\n{content}"
        if heading:
            return f"### {heading}"
        return content

    def _render_section(section: dict[str, Any]) -> str:
        title = str(section.get("title", "")).strip()
        blocks = section.get("blocks", [])
        rendered_blocks = [
            block_md
            for block_md in (_render_block(block) for block in blocks if isinstance(block, dict))
            if block_md.strip()
        ]
        if not title:
            return "\n\n".join(rendered_blocks)
        if rendered_blocks:
            return f"## {title}\n\n" + "\n\n".join(rendered_blocks)
        return f"## {title}"

    def _render_identity_markdown(editor: dict[str, Any]) -> str:
        meta = editor["meta"]
        body_parts = [f"# {meta['display_title'].strip() or meta['name'].strip() or '未命名人设'}"]
        description = meta["description"].strip()
        if description:
            body_parts.append(description)
        body_parts.extend(
            section_md
            for section_md in (_render_section(section) for section in editor["persona_sections"])
            if section_md.strip()
        )

        proactive_text = editor["proactive"]["text"].strip()
        if editor["proactive"]["enabled"] and proactive_text:
            body_parts.append(f"## 插话方式\n\n{proactive_text}")

        return "\n\n".join(body_parts).strip() + "\n"

    def _render_instruction_markdown(editor: dict[str, Any]) -> str:
        rendered_sections = [
            section_md
            for section_md in (_render_section(section) for section in editor["instruction_sections"])
            if section_md.strip()
        ]
        return "\n\n".join(rendered_sections).strip() + "\n"

    def _read_persona_guide() -> str:
        if _PERSONA_GUIDE_PATH.is_file():
            return _PERSONA_GUIDE_PATH.read_text(encoding="utf-8")
        return ""

    @router.get("/soul/persona-guide")
    async def get_persona_guide():
        guide = _read_persona_guide()
        if not guide:
            return JSONResponse({"detail": "Persona guide not found"}, status_code=404)
        return {
            "title": "AI 自主生成双文件人设规则",
            "markdown": guide,
        }

    @router.get("/soul")
    async def get_soul():
        return _build_legacy_editor()

    @router.post("/soul/save")
    async def save_soul(request: Request):
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            payload = await request.json()
        else:
            form = await request.form()
            payload = dict(form)

        editor = _normalize_editor(payload.get("editor", payload))
        if not editor["meta"]["display_title"].strip():
            return {"ok": False, "error": "展示标题不能为空"}
        if editor["proactive"]["enabled"] and not editor["proactive"]["text"].strip():
            return {"ok": False, "error": "启用插话方式时必须填写规则内容"}

        identity_markdown = _render_identity_markdown(editor)
        instruction_markdown = _render_instruction_markdown(editor)

        try:
            _write("identity.md", identity_markdown)
            _write("instruction.md", instruction_markdown)
        except Exception as error:
            return {"ok": False, "error": str(error)}

        reload_ok = True
        reload_msg = "已自动重载，无需重启"
        if persona_runtime is not None:
            try:
                persona_id = persona_runtime.bundle.persona_id if persona_runtime.bundle else ""
                if persona_id:
                    persona_runtime.swap_bundle(persona_id)
            except Exception:
                reload_ok = False
                reload_msg = "运行时同步失败，请重启"

        return {
            "ok": True,
            "reload_ok": reload_ok,
            "format_mode": "legacy",
            "migration_pending": False,
            "message": f"config/soul/identity.md 与 config/soul/instruction.md 已保存。{reload_msg}",
        }

    return router
