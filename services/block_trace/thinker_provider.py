import secrets
from typing import Any

from services.block_trace.providers import QueryContext
from services.block_trace.types import PromptBlockCandidate
from services.humanization import THINKER_LAST_DECISION_SLOT
from services.system_module import Scope

_PRIORITY = 48


class ThinkerProvider:
    """Render the current per-turn thinker decision as a prompt block."""

    name = "thinker"

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = bool(enabled)

    async def provide(self, ctx: QueryContext) -> list[PromptBlockCandidate]:
        if not self._enabled or not ctx.turn_id:
            return []
        state = _read_state(ctx)
        if not state or str(state.get("action", "")).strip() != "reply":
            return []
        text = _render_block(state)
        if not text:
            return []
        return [PromptBlockCandidate(
            candidate_id="pbc_" + secrets.token_hex(6),
            source="context",
            provider="thinker_provider",
            layer="dynamic",
            label="本轮意图",
            text=text,
            priority=_PRIORITY,
            position="dynamic",
            scope="group" if ctx.group_id else "session",
            group_id=ctx.group_id or "",
            hit_reason=f"thinker:{state.get('retrieve_mode') or 'hybrid'}",
            char_count=len(text),
            metadata={
                "action": state.get("action", ""),
                "retrieve_mode": state.get("retrieve_mode", ""),
                "sticker": bool(state.get("sticker")),
                "tone": state.get("tone", ""),
            },
        )]


def _read_state(ctx: QueryContext) -> dict[str, Any]:
    if ctx.runtime_state is None:
        return {}
    try:
        snapshot = ctx.runtime_state.get(
            THINKER_LAST_DECISION_SLOT,
            scope=Scope(
                session_id=ctx.session_id,
                group_id=ctx.group_id,
                user_id=ctx.user_id,
                turn_id=ctx.turn_id,
            ),
        )
    except Exception:
        return {}
    value = getattr(snapshot, "value", None)
    return value if isinstance(value, dict) else {}


def _render_block(state: dict[str, Any]) -> str:
    thought = str(state.get("thought", "") or "").strip()
    tone = str(state.get("tone", "") or "").strip()
    retrieve = str(state.get("retrieve_mode", "") or "hybrid").strip()
    if not thought and not tone:
        return ""
    lines = ["本轮回复意图：按 thinker 的方向回应，但不要复述 thinker 原句。"]
    if thought:
        lines.append(f"- 意图：{thought[:120]}")
    if tone:
        lines.append(f"- 语气：{tone[:40]}")
    if retrieve:
        lines.append(f"- 检索模式：{retrieve}")
    if bool(state.get("sticker")):
        lines.append("- 表情包：适合时同时调用 send_sticker，发送后不要解释已发送表情包。")
    else:
        lines.append("- 表情包：本轮不主动配图，除非后续工具规则强制要求。")
    lines.append("把以上当作行动边界，不要把这些标签写给用户看。")
    return "\n".join(lines)
