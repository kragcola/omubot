"""Dialogue Climate turn snapshot and PromptProviderBus adapter."""

from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from services.block_trace.providers import ContextProvider, QueryContext
from services.block_trace.types import PromptBlockCandidate
from services.dialogue_climate.policy import PolicyOutput, synthesize
from services.dialogue_climate.state import ClimateState
from services.humanization import CLIMATE_CURRENT_SLOT, humanization_source
from services.system_module import Scope

_PRIORITY = 12
_SNAPSHOT_TTL = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class ClimateTurnSnapshot:
    group_id: str
    user_id: str
    state: dict[str, Any]
    policy: PolicyOutput
    relationship_text: str
    prompt_text: str

    def to_state_value(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "user_id": self.user_id,
            "state": dict(self.state),
            "policy": asdict(self.policy),
            "relationship_text": self.relationship_text,
            "prompt_text": self.prompt_text,
        }


def build_climate_turn_snapshot(
    *,
    state: ClimateState,
    group_id: str | int | None,
    user_id: str | int | None,
    relationship_text: str = "",
) -> ClimateTurnSnapshot:
    policy = synthesize(state)
    prompt_text = _render_merged_block(
        relationship_text=relationship_text,
        climate_guidance=policy.guidance,
    )
    return ClimateTurnSnapshot(
        group_id=str(group_id or ""),
        user_id=str(user_id or ""),
        state=state.to_dict(),
        policy=policy,
        relationship_text=str(relationship_text or "").strip(),
        prompt_text=prompt_text,
    )


def write_climate_turn_snapshot(
    bus: Any,
    snapshot: ClimateTurnSnapshot,
    *,
    session_id: str,
) -> None:
    if bus is None or not session_id:
        return
    bus.set(
        CLIMATE_CURRENT_SLOT,
        snapshot.to_state_value(),
        scope=Scope(
            session_id=session_id,
            group_id=snapshot.group_id or None,
            user_id=snapshot.user_id,
        ),
        source=humanization_source("dialogue_climate:turn_snapshot"),
        confidence=1.0,
        decay_at=datetime.now() + _SNAPSHOT_TTL,
    )


def read_climate_turn_snapshot(
    bus: Any,
    *,
    session_id: str,
    group_id: str | int | None,
    user_id: str | int | None,
) -> dict[str, Any]:
    if bus is None or not session_id:
        return {}
    expected_group = str(group_id or "")
    expected_user = str(user_id or "")
    try:
        snapshot = bus.get(
            CLIMATE_CURRENT_SLOT,
            scope=Scope(
                session_id=session_id,
                group_id=expected_group or None,
                user_id=expected_user,
            ),
        )
    except Exception:
        return {}
    value = getattr(snapshot, "value", None)
    if not isinstance(value, dict):
        return {}
    if str(value.get("group_id", "")) != expected_group:
        return {}
    if str(value.get("user_id", "")) != expected_user:
        return {}
    return value


def has_climate_prompt_candidate(
    bus: Any,
    *,
    session_id: str,
    group_id: str | int | None,
    user_id: str | int | None,
) -> bool:
    value = read_climate_turn_snapshot(
        bus,
        session_id=session_id,
        group_id=group_id,
        user_id=user_id,
    )
    return bool(str(value.get("prompt_text", "") or "").strip())


class ClimateProvider:
    """Emit the sole merged relationship + climate prompt candidate."""

    name = "climate"

    async def provide(self, ctx: QueryContext) -> list[PromptBlockCandidate]:
        value = read_climate_turn_snapshot(
            ctx.runtime_state,
            session_id=ctx.session_id,
            group_id=ctx.group_id,
            user_id=ctx.user_id,
        )
        text = str(value.get("prompt_text", "") or "").strip()
        if not text:
            return []
        raw_policy = value.get("policy")
        policy: dict[str, Any] = raw_policy if isinstance(raw_policy, dict) else {}
        return [PromptBlockCandidate(
            candidate_id="pbc_" + secrets.token_hex(6),
            source="context",
            provider="climate_provider",
            layer="dynamic",
            label="当前关系与对话气候",
            text=text,
            priority=_PRIORITY,
            position="dynamic",
            scope="group" if ctx.group_id else "session",
            group_id=ctx.group_id or "",
            hit_reason="dialogue_climate:turn_snapshot",
            char_count=len(text),
            metadata={
                "mood_label": str(policy.get("mood_label", "neutral")),
                "reply_bias": str(policy.get("reply_bias", "full")),
                "delay_multiplier": float(policy.get("delay_multiplier", 1.0) or 1.0),
                "openness_hint": str(policy.get("openness_hint", "")),
            },
        )]


def _render_merged_block(*, relationship_text: str, climate_guidance: str) -> str:
    relationship = _without_outer_heading(relationship_text)
    climate = _without_outer_heading(climate_guidance)
    parts: list[str] = []
    if relationship:
        parts.append(f"关系上下文：\n{relationship}")
    if climate:
        parts.append(f"本轮气候方向：\n{climate}")
    return "\n\n".join(parts)


def _without_outer_heading(text: str) -> str:
    value = str(text or "").strip()
    if not value.startswith("【"):
        return value
    closing = value.find("】")
    if closing < 0:
        return value
    return value[closing + 1 :].lstrip(" \n")


assert isinstance(ClimateProvider(), ContextProvider)


__all__ = [
    "ClimateProvider",
    "ClimateTurnSnapshot",
    "build_climate_turn_snapshot",
    "has_climate_prompt_candidate",
    "read_climate_turn_snapshot",
    "write_climate_turn_snapshot",
]
