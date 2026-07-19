"""Bounded fiction-worldbook adapter for already-accepted QZone candidates.

Fail-closed: returns empty unless advanced_enabled, the active arc is fiction-
scoped, and the candidate subject_kind is exactly "fiction". Never invents new
candidate sources or reads credentials.
"""

from __future__ import annotations

from typing import Any

from plugins.qzone_journal.public_safety import scrub_public_text
from plugins.qzone_journal.selector import CandidateEvent

_MAX_CONTEXT_CHARS = 1600
_MAX_TITLE_CHARS = 80
_MAX_STAGE_CHARS = 40
_MAX_OPEN_THREADS = 4
_MAX_THREAD_CHARS = 120
_MAX_PARTNERS = 6
_MAX_PARTNER_STATE_CHARS = 100
_MAX_RECENT_EVENTS = 2
_MAX_RECENT_EVENT_CHARS = 80
_MAX_ENTITY_ID_CHARS = 48

def _clip(value: str, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: max(0, limit - 1)].rstrip() + "…"


def _partner_line(entity_id: str, state: dict[str, Any]) -> str | None:
    if str(state.get("kind", "") or "").strip() != "fiction":
        return None
    safe_id = _clip(scrub_public_text(entity_id), _MAX_ENTITY_ID_CHARS)
    if not safe_id or safe_id == "[redacted]":
        return None
    bits: list[str] = []
    current = _clip(
        scrub_public_text(state.get("current_state", "")),
        _MAX_PARTNER_STATE_CHARS,
    )
    if current:
        bits.append(current)
    availability = _clip(scrub_public_text(state.get("availability", "")), 32)
    if availability:
        bits.append(f"availability={availability}")
    recent_raw = state.get("recent_events")
    recent_bits: list[str] = []
    if isinstance(recent_raw, list):
        for item in recent_raw:
            if not isinstance(item, (str, int, float)):
                continue
            cleaned = _clip(scrub_public_text(item), _MAX_RECENT_EVENT_CHARS)
            if cleaned:
                recent_bits.append(cleaned)
            if len(recent_bits) >= _MAX_RECENT_EVENTS:
                break
    if recent_bits:
        bits.append("recent=" + "; ".join(recent_bits))
    if not bits:
        return f"伙伴 {safe_id}：（无可用状态摘要）"
    return f"伙伴 {safe_id}：{' | '.join(bits)}"


def _iter_partner_items(
    partners_raw: dict[Any, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Stable partner pairs; keep original keys so non-string keys still resolve."""
    items: list[tuple[str, dict[str, Any]]] = []
    for key in sorted(partners_raw.keys(), key=lambda k: str(k)):
        state = partners_raw.get(key)
        if not isinstance(state, dict):
            continue
        items.append((str(key), state))
    return items


def build_advanced_fiction_context(
    *,
    advanced_enabled: bool,
    arc: Any | None,
    candidate: CandidateEvent,
) -> str:
    """Return prompt-facing fiction worldbook text, or empty when gates fail."""
    if not advanced_enabled:
        return ""
    if arc is None:
        return ""
    if str(getattr(candidate, "subject_kind", "") or "").strip() != "fiction":
        return ""
    if str(getattr(arc, "scope", "") or "").strip() != "fiction":
        return ""

    title = _clip(scrub_public_text(getattr(arc, "title", "")), _MAX_TITLE_CHARS)
    stage = _clip(scrub_public_text(getattr(arc, "stage", "")), _MAX_STAGE_CHARS)
    lines: list[str] = []
    if title:
        lines.append(f"剧情弧标题：{title}")
    if stage:
        lines.append(f"当前阶段：{stage}")

    threads_raw = getattr(arc, "open_threads", None) or []
    threads: list[str] = []
    if isinstance(threads_raw, list):
        for item in threads_raw:
            if not isinstance(item, (str, int, float)):
                continue
            cleaned = _clip(scrub_public_text(item), _MAX_THREAD_CHARS)
            if cleaned:
                threads.append(cleaned)
            if len(threads) >= _MAX_OPEN_THREADS:
                break
    if threads:
        lines.append("开放线索：")
        for thread in threads:
            lines.append(f"- {thread}")

    partners_raw = getattr(arc, "partner_states", None) or {}
    partner_lines: list[str] = []
    if isinstance(partners_raw, dict):
        for entity_id, state in _iter_partner_items(partners_raw):
            line = _partner_line(entity_id, state)
            if line is None:
                continue
            partner_lines.append(line)
            if len(partner_lines) >= _MAX_PARTNERS:
                break
    if partner_lines:
        lines.append("虚构伙伴状态：")
        lines.extend(f"- {line}" for line in partner_lines)

    if not lines:
        return ""

    text = "\n".join(lines).strip()
    # Public scrub must keep worldbook newlines (normalize only within lines).
    text = scrub_public_text(text, preserve_newlines=True)
    if len(text) > _MAX_CONTEXT_CHARS:
        text = text[: _MAX_CONTEXT_CHARS - 1].rstrip() + "…"
    return text


def collect_fiction_partner_entity_ids(arc: Any | None) -> list[str]:
    """Stable sorted list of fiction partner entity_ids for provenance only."""
    if arc is None:
        return []
    partners_raw = getattr(arc, "partner_states", None) or {}
    if not isinstance(partners_raw, dict):
        return []
    ids: list[str] = []
    for entity_id, state in _iter_partner_items(partners_raw):
        if str(state.get("kind", "") or "").strip() != "fiction":
            continue
        safe = _clip(scrub_public_text(entity_id), _MAX_ENTITY_ID_CHARS)
        if safe and safe != "[redacted]":
            ids.append(safe)
        if len(ids) >= _MAX_PARTNERS:
            break
    return ids


def build_review_provenance(
    *,
    arc: Any | None,
    advanced_context_included: bool,
    public_projection: Any | None = None,
) -> dict[str, Any]:
    """Secret-safe bounded provenance object for operator review.

    schema_version 1: fiction/self advanced context (no public_projection).
    schema_version 2: includes bounded public_projection metadata for factual.
    """
    arc_id = _clip(
        scrub_public_text(getattr(arc, "arc_id", "") if arc is not None else ""),
        64,
    )
    stage = _clip(
        scrub_public_text(getattr(arc, "stage", "") if arc is not None else ""),
        40,
    )
    arc_scope = _clip(
        scrub_public_text(getattr(arc, "scope", "") if arc is not None else ""),
        40,
    )
    try:
        revision = int(getattr(arc, "revision", 0) or 0) if arc is not None else 0
    except (TypeError, ValueError):
        revision = 0
    revision = max(0, min(revision, 1_000_000))

    base: dict[str, Any] = {
        "schema_version": 1,
        "arc_id": arc_id or None,
        "arc_revision": revision,
        "arc_stage": stage or None,
        "arc_scope": arc_scope or None,
        "advanced_context_included": bool(advanced_context_included),
        "fiction_partner_entity_ids": collect_fiction_partner_entity_ids(arc),
    }

    if public_projection is None:
        return base

    from plugins.qzone_journal.public_projection import (
        is_validated_public_projection,
    )

    if not is_validated_public_projection(public_projection):
        raise ValueError("public_projection must be validated for provenance")
    meta = public_projection.public_metadata()
    # Never attach projected_summary raw text into provenance — only bounded meta.
    base["schema_version"] = 2
    base["public_projection"] = meta
    return base


__all__ = [
    "build_advanced_fiction_context",
    "build_review_provenance",
    "collect_fiction_partner_entity_ids",
]
