"""RegisterProvider — humanization register hints through PromptProviderBus."""

from __future__ import annotations

import secrets
from typing import Any

from services.block_trace.providers import ContextProvider, QueryContext
from services.block_trace.types import PromptBlockCandidate
from services.humanization import (
    AFFECTION_FAMILIARITY_SLOT,
    CLOCK_CURRENT_SLOT,
    REGISTER_LABEL_SLOT,
)
from services.system_module import Scope

_PRIORITY = 43
_VALID_LABELS = {"neutral", "quiet", "playful", "affectionate", "serious", "distant"}


class RegisterProvider:
    """Render a short target-register block from RuntimeStateBus signals."""

    name = "register"

    async def provide(self, ctx: QueryContext) -> list[PromptBlockCandidate]:
        state = _read_register_state(ctx)
        target = _target_register(
            state["label"],
            tier=state["tier"],
            familiarity=state["familiarity"],
            hour=state["hour"],
            is_holiday=state["is_holiday"],
            is_weekend=state["is_weekend"],
        )
        text = _render_register_block(
            target,
            label=state["label"],
            confidence=state["confidence"],
            tier=state["tier"],
            familiarity=state["familiarity"],
            slot_activity=state["slot_activity"],
        )
        if not text:
            return []
        return [PromptBlockCandidate(
            candidate_id="pbc_" + secrets.token_hex(6),
            source="context",
            provider="register_provider",
            layer="stable",
            label="语域目标",
            text=text,
            priority=_PRIORITY,
            position="stable",
            scope="group" if ctx.group_id else "session",
            group_id=ctx.group_id or "",
            hit_reason=f"register:{target}",
            char_count=len(text),
            metadata={
                "target_register": target,
                "register_label": state["label"],
                "confidence": state["confidence"],
                "tier": state["tier"],
                "familiarity": state["familiarity"],
            },
        )]


def _read_register_state(ctx: QueryContext) -> dict[str, Any]:
    register = _get_state_value(
        ctx.runtime_state,
        REGISTER_LABEL_SLOT,
        Scope(session_id=ctx.session_id, group_id=ctx.group_id, user_id=ctx.user_id),
    )
    affection = _get_state_value(
        ctx.runtime_state,
        AFFECTION_FAMILIARITY_SLOT,
        Scope(user_id=ctx.user_id),
    )
    clock = _get_state_value(
        ctx.runtime_state,
        CLOCK_CURRENT_SLOT,
        Scope(session_id=ctx.session_id, group_id=ctx.group_id, user_id=ctx.user_id, turn_id=ctx.turn_id),
    )
    label = str(register.get("label", "neutral") if isinstance(register, dict) else "neutral").strip().lower()
    if label not in _VALID_LABELS:
        label = "neutral"
    return {
        "label": label,
        "confidence": _clamp01(register.get("confidence", 0.0) if isinstance(register, dict) else 0.0),
        "tier": str(affection.get("tier", "") if isinstance(affection, dict) else ""),
        "familiarity": _clamp01(affection.get("familiarity", 0.0) if isinstance(affection, dict) else 0.0),
        "hour": _safe_int(clock.get("hour", -1) if isinstance(clock, dict) else -1),
        "is_weekend": bool(clock.get("is_weekend", False)) if isinstance(clock, dict) else False,
        "is_holiday": bool(clock.get("is_holiday", False)) if isinstance(clock, dict) else False,
        "slot_activity": str(clock.get("slot_activity", "") if isinstance(clock, dict) else ""),
    }


def _get_state_value(bus: Any, slot_id: str, scope: Scope) -> dict[str, Any]:
    if bus is None:
        return {}
    try:
        snapshot = bus.get(slot_id, scope=scope)
    except Exception:
        return {}
    value = getattr(snapshot, "value", None)
    return value if isinstance(value, dict) else {}


def _target_register(
    label: str,
    *,
    tier: str,
    familiarity: float,
    hour: int,
    is_holiday: bool,
    is_weekend: bool,
) -> str:
    if label == "serious":
        return "serious_clear"
    if label == "distant" or tier == "陌生人":
        return "polite_distant"
    if label == "quiet" or (0 <= hour < 7 and not is_holiday and not is_weekend):
        return "quiet_soft"
    if label == "playful":
        return "playful_light"
    if label == "affectionate" or familiarity >= 0.6 or tier in {"好朋友", "重要的人"}:
        return "casual_close"
    return "neutral_daily"


def _render_register_block(
    target: str,
    *,
    label: str,
    confidence: float,
    tier: str,
    familiarity: float,
    slot_activity: str,
) -> str:
    guidance = {
        "quiet_soft": "轻一点、短一点，少抢话；有回应即可，不要把氛围抬得很高。",
        "playful_light": "可以接梗和轻微吐槽，但不要为了显得活泼而堆口头禅。",
        "casual_close": "更亲近一点，允许自然偏爱；仍要根据话题收放，不要强行撒娇。",
        "polite_distant": "保持礼貌和边界，少用过熟称呼，不主动拉近关系。",
        "serious_clear": "清楚、稳一点，先给结论或判断，再补必要解释。",
        "neutral_daily": "日常自然即可，像参与群聊一样回应，不照搬任何固定人设句。",
    }.get(target, "日常自然即可。")
    context_bits = [f"classifier={label}:{confidence:.2f}", f"familiarity={familiarity:.2f}"]
    if tier:
        context_bits.append(f"tier={tier}")
    if slot_activity:
        context_bits.append(f"slot={slot_activity[:24]}")
    return (
        f"本轮语域目标：{target}。\n"
        f"{guidance}\n"
        f"参考信号：{'; '.join(context_bits)}。\n"
        "只把这些当作说话方向，不要逐字复述这些标签或规则。"
    )


def _clamp01(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


assert isinstance(RegisterProvider(), ContextProvider)
