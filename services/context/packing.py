"""Prompt packing helpers for unified context hits."""

from __future__ import annotations

from collections import defaultdict

from services.context.types import ContextHit, ContextPack

_TYPE_LABELS = {
    "memory_card": "记忆卡片",
    "doc_chunk": "文档资料",
    "graph_fact": "关系事实",
}


def pack_context_hits(
    hits: list[ContextHit],
    *,
    max_chars: int = 2400,
) -> ContextPack:
    """Pack hits into a compact, readable prompt block."""
    selected: list[ContextHit] = []
    grouped: dict[str, list[str]] = defaultdict(list)
    used_chars = 0

    for hit in hits:
        title = hit.title or hit.source or hit.id
        line = f"- [{title}] {hit.content.strip()}"
        line_len = len(line) + 1
        if selected and used_chars + line_len > max_chars:
            break
        if line_len > max_chars:
            line = line[: max_chars - 1] + "…"
            line_len = len(line) + 1
        grouped[hit.type].append(line)
        selected.append(hit)
        used_chars += line_len

    parts: list[str] = []
    for hit_type in ("memory_card", "doc_chunk", "graph_fact"):
        lines = grouped.get(hit_type)
        if lines:
            parts.append(f"【{_TYPE_LABELS.get(hit_type, hit_type)}】\n" + "\n".join(lines))

    return ContextPack(
        text="\n\n".join(parts),
        hits=selected,
        omitted_count=max(0, len(hits) - len(selected)),
    )
