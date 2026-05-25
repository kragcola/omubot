import secrets
from typing import Any

from services.block_trace.providers import QueryContext
from services.block_trace.types import PromptBlockCandidate
from services.humanization import STICKER_RECENT_USED_SLOT
from services.system_module import Scope

_PRIORITY = 47
_MAX_RECENT = 5

class StickerRegisterProvider:
    """Warn the main prompt about stickers used recently in this session."""
    name = "sticker_register"

    def __init__(self, store_getter: Any = lambda: None, *, enabled: bool = True) -> None:
        self._get_store = store_getter
        self._enabled = bool(enabled)

    async def provide(self, ctx: QueryContext) -> list[PromptBlockCandidate]:
        if not self._enabled:
            return []
        recent = _recent_sticker_ids(ctx)
        if not recent:
            return []
        other_count = _other_sticker_count(self._get_store, recent)
        text = _render_block(recent, other_count=other_count)
        return [PromptBlockCandidate(
            candidate_id="pbc_" + secrets.token_hex(6), source="sticker",
            provider="sticker_register_provider", layer="dynamic", label="表情包近期使用",
            text=text, priority=_PRIORITY, position="dynamic",
            scope="group" if ctx.group_id else "session",
            group_id=ctx.group_id or "",
            hit_reason="sticker:recent_used",
            char_count=len(text),
            evidence_refs=tuple(recent),
            metadata={"recent_sticker_ids": recent, "other_sticker_count": other_count},
        )]

def _recent_sticker_ids(ctx: QueryContext) -> list[str]:
    if ctx.runtime_state is None:
        return []
    try:
        snapshot = ctx.runtime_state.get(
            STICKER_RECENT_USED_SLOT,
            scope=Scope(session_id=ctx.session_id, group_id=ctx.group_id, user_id=ctx.user_id),
        )
    except Exception:
        return []
    value = getattr(snapshot, "value", None)
    items = value.get("sticker_ids", []) if isinstance(value, dict) else []
    cleaned: list[str] = []
    seen: set[str] = set()
    if not isinstance(items, list):
        return cleaned
    for raw in items:
        sticker_id = str(raw).strip()
        if not sticker_id or sticker_id in seen:
            continue
        seen.add(sticker_id)
        cleaned.append(sticker_id)
        if len(cleaned) >= _MAX_RECENT:
            break
    return cleaned

def _other_sticker_count(store_getter: Any, recent: list[str]) -> int:
    try:
        store = store_getter()
        stickers = store.list_all()
    except Exception:
        return 0
    recent_set = set(recent)
    return sum(1 for sticker_id in stickers if str(sticker_id) not in recent_set) if isinstance(stickers, dict) else 0

def _render_block(recent: list[str], *, other_count: int) -> str:
    lines = ["近期已发送过这些表情包，30 分钟内建议换一个："]
    lines.extend(f"- «表情包:{sticker_id}»：近期已用，除非非常贴切，否则不要复读。" for sticker_id in recent)
    if other_count > 0:
        lines.append(f"表情包库里还有 {other_count} 个其它候选，优先从其它候选里挑。")
    lines.append("如果这轮不适合配图，可以不调用 send_sticker；不要为了完成规则硬发。")
    return "\n".join(lines)
