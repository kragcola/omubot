"""CatchphraseProvider — normalized catchphrase hints for prompt blocks."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any

from services.block_trace.providers import ContextProvider, QueryContext
from services.block_trace.types import PromptBlockCandidate
from services.humanization import REGISTER_LABEL_SLOT, REGISTER_RECENT_USED_SLOT, humanization_source
from services.system_module import Scope

_PRIORITY = 46
_RECENT_LIMIT = 12
_RECENT_TTL = timedelta(minutes=30)
_CONSERVATIVE_LABELS = {"serious", "distant"}
_EXPRESSIVE_LABELS = {"playful", "affectionate"}


class CatchphraseProvider:
    """Surface a tiny set of normalized catchphrases without forcing usage."""

    name = "catchphrase"

    def __init__(self, store_getter: Any, *, max_items: int = 2, enabled: bool = True) -> None:
        self._get_store = store_getter
        self._max_items = max(1, min(int(max_items or 2), 3))
        self._enabled = bool(enabled)

    async def provide(self, ctx: QueryContext) -> list[PromptBlockCandidate]:
        if not self._enabled or not ctx.group_id:
            return []
        store = self._get_store()
        if store is None:
            return []
        label = _register_label(ctx)
        max_items = 1 if label in _CONSERVATIVE_LABELS else self._max_items
        if max_items <= 0:
            return []
        recent = _recent_cluster_ids(ctx)
        try:
            candidates = await store.list_prompt_candidates(
                domain="catchphrase",
                group_id=str(ctx.group_id),
                limit=max_items,
                exclude_cluster_ids=tuple(recent),
            )
        except Exception:
            return []
        selected = [item for item in candidates if _is_prompt_safe(item.canonical_text)][:max_items]
        if not selected:
            return []
        text = _render_block([item.canonical_text for item in selected], label=label)
        if not text:
            return []
        _write_recent(ctx, [item.cluster_id for item in selected])
        refs = tuple(item.cluster_id for item in selected)
        return [PromptBlockCandidate(
            candidate_id="pbc_" + secrets.token_hex(6),
            source="slang",
            provider="catchphrase_provider",
            layer="dynamic",
            label="口头禅候选",
            text=text,
            priority=_PRIORITY,
            position="dynamic",
            scope="group",
            group_id=ctx.group_id or "",
            hit_reason=f"catchphrase:{label or 'neutral'}",
            char_count=len(text),
            evidence_refs=refs,
            metadata={
                "register_label": label,
                "catchphrase_count": len(selected),
                "recent_suppressed": len(recent),
            },
        )]


def _register_label(ctx: QueryContext) -> str:
    value = _state_value(
        ctx.runtime_state,
        REGISTER_LABEL_SLOT,
        Scope(session_id=ctx.session_id, group_id=ctx.group_id, user_id=ctx.user_id),
    )
    label = str(value.get("label", "") if isinstance(value, dict) else "").strip().lower()
    return label if label else "neutral"


def _recent_cluster_ids(ctx: QueryContext) -> list[str]:
    value = _state_value(
        ctx.runtime_state,
        REGISTER_RECENT_USED_SLOT,
        Scope(session_id=ctx.session_id, group_id=ctx.group_id, user_id=ctx.user_id),
    )
    items = value.get("catchphrase_cluster_ids", []) if isinstance(value, dict) else []
    if not isinstance(items, list):
        return []
    return [str(item).strip() for item in items if str(item).strip()]


def _write_recent(ctx: QueryContext, cluster_ids: list[str]) -> None:
    if ctx.runtime_state is None or not cluster_ids:
        return
    merged = [*cluster_ids, *_recent_cluster_ids(ctx)]
    seen: set[str] = set()
    deduped = []
    for item in merged:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
        if len(deduped) >= _RECENT_LIMIT:
            break
    try:
        ctx.runtime_state.set(
            REGISTER_RECENT_USED_SLOT,
            {
                "catchphrase_cluster_ids": deduped,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            scope=Scope(session_id=ctx.session_id, group_id=ctx.group_id, user_id=ctx.user_id),
            source=humanization_source("catchphrase_provider:recent_used"),
            confidence=1.0,
            decay_at=datetime.now() + _RECENT_TTL,
        )
    except Exception:
        return


def _state_value(bus: Any, slot_id: str, scope: Scope) -> dict[str, Any]:
    if bus is None:
        return {}
    try:
        snapshot = bus.get(slot_id, scope=scope)
    except Exception:
        return {}
    value = getattr(snapshot, "value", None)
    return value if isinstance(value, dict) else {}


def _is_prompt_safe(text: str) -> bool:
    stripped = str(text or "").strip()
    return 1 < len(stripped) <= 28 and "\n" not in stripped


def _render_block(items: list[str], *, label: str) -> str:
    clean = [item.strip() for item in items if _is_prompt_safe(item)]
    if not clean:
        return ""
    prefix = "本轮可自然借用的群内口头禅候选："
    lines = [prefix, *[f"- {item}" for item in clean]]
    if label in _CONSERVATIVE_LABELS:
        lines.append("当前语域偏克制，只在非常贴合时轻轻带一下。")
    elif label in _EXPRESSIVE_LABELS:
        lines.append("可以顺手借一点语气，但不要连续复读。")
    else:
        lines.append("只在顺口时借用，不要为了显得有梗而硬套。")
    lines.append("不要解释这些候选，也不要逐字照搬成固定开场。")
    return "\n".join(lines)


assert isinstance(CatchphraseProvider(store_getter=lambda: None), ContextProvider)
