"""Markdown chunking helpers for the lightweight knowledge service."""

from __future__ import annotations

import re
from pathlib import Path

from services.knowledge.types import KnowledgeChunk


def chunk_markdown(
    *,
    path: Path,
    text: str,
    root: Path,
    source_hash: str,
) -> list[KnowledgeChunk]:
    """Split a markdown file into level-2 sections.

    This preserves the old KnowledgeBase behavior so current retrieval tests
    and prompt shape keep working, while adding source/title metadata.
    """
    source = _relative_source(path, root)
    doc_title = path.name
    title_match = re.search(r"^# (.+)$", text, re.MULTILINE)
    if title_match:
        doc_title = title_match.group(1).strip()

    chunks: list[KnowledgeChunk] = []
    sections = re.split(r"^## (.+)$", text, flags=re.MULTILINE)

    preamble = sections[0].strip()
    if preamble and not preamble.startswith("# "):
        lines = preamble.split("\n")
        meaningful = [line for line in lines if line.strip() and not line.strip().startswith("# ")]
        if meaningful:
            content = "\n".join(meaningful)
            chunks.append(KnowledgeChunk(
                chunk_id=f"{source}::preamble",
                title=doc_title,
                content=content,
                source=source,
                source_path=str(path),
                source_hash=source_hash,
            ))

    for index in range(1, len(sections), 2):
        section_number = (index + 1) // 2
        heading = sections[index].strip()
        body = sections[index + 1].strip() if index + 1 < len(sections) else ""
        if not body:
            continue
        content = f"## {heading}\n{body}"
        chunks.append(KnowledgeChunk(
            chunk_id=f"{source}::section-{section_number}::{heading}",
            title=f"{doc_title} › {heading}",
            content=content,
            source=source,
            source_path=str(path),
            source_hash=source_hash,
            metadata={"heading": heading},
        ))

    return chunks


def _relative_source(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name
