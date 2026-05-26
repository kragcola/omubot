"""Mood classifier for short-lived humanization state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any, Literal, cast

from services.humanization.contract import MOOD_CURRENT_SLOT
from services.humanization.state import humanization_source
from services.system_module import RuntimeStateBus, Scope

MoodLabel = Literal["cold", "tired", "neutral", "playful", "high"]
_VALID_MOODS: set[str] = {"cold", "tired", "neutral", "playful", "high"}
_TONE_TOKENS = ("啊", "呀", "啦", "呢", "嘛", "哈", "哈哈", "hhh", "笑", "草")
_STICKER_TOKENS = ("[CQ:image", "[CQ:mface", "sticker", "mface", "表情")
_MAX_WINDOW = 12
_MOOD_TTL_S = 300
_FEEDBACK_STICKER_DENSITY_CAP = 0.3


@dataclass(frozen=True, slots=True)
class MoodSignals:
    reply_delay_s: float = 0.0
    short_reply_ratio: float = 0.0
    sticker_density: float = 0.0
    feedback_sticker_density: float = 0.0
    tone_particle_rate: float = 0.0


@dataclass(frozen=True, slots=True)
class MoodDecision:
    label: MoodLabel = "neutral"
    confidence: float = 0.0
    reason: str = ""
    signals: MoodSignals = MoodSignals()

    def to_state_value(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "confidence": self.confidence,
            "reason": self.reason,
            "signals": asdict(self.signals),
            "ttl_s": _MOOD_TTL_S,
        }


class MoodClassifier:
    """Classify current group mood with a small FiSMiness-style FSM."""

    async def classify(
        self,
        messages: list[dict[str, Any]],
        *,
        feedback_sticker_density: float = 0.0,
    ) -> MoodDecision:
        signals = _signals(messages[-_MAX_WINDOW:], feedback_sticker_density=feedback_sticker_density)
        label, confidence, reason = _transition(signals)
        return MoodDecision(label=label, confidence=confidence, reason=reason, signals=signals)

    async def classify_and_write(
        self,
        messages: list[dict[str, Any]],
        *,
        bus: RuntimeStateBus,
        scope: Scope,
    ) -> MoodDecision:
        feedback_density = _feedback_sticker_density(bus.get(MOOD_CURRENT_SLOT, scope=scope))
        decision = await self.classify(messages, feedback_sticker_density=feedback_density)
        state_value = decision.to_state_value()
        state_value["signals"]["feedback_sticker_density"] = 0.0
        bus.set(
            MOOD_CURRENT_SLOT,
            state_value,
            scope=scope,
            source=humanization_source("mood_classifier:classify"),
            confidence=decision.confidence,
            decay_at=datetime.now() + timedelta(seconds=_MOOD_TTL_S),
        )
        return decision


def _signals(messages: list[dict[str, Any]], *, feedback_sticker_density: float = 0.0) -> MoodSignals:
    user_rows = [row for row in messages if str(row.get("role") or "user") != "assistant"]
    texts = [_text(row) for row in user_rows if _text(row)]
    if not texts:
        return MoodSignals()
    short_ratio = sum(1 for text in texts if len(text) <= 6) / len(texts)
    observed_density = sum(1 for text in texts if _has_any(text, _STICKER_TOKENS)) / len(texts)
    feedback_density = _clamp_feedback_density(feedback_sticker_density)
    sticker_density = min(1.0, observed_density + feedback_density)
    tone_rate = sum(1 for text in texts if _has_any(text, _TONE_TOKENS)) / len(texts)
    delay = _avg_user_gap(user_rows)
    return MoodSignals(
        reply_delay_s=round(delay, 3),
        short_reply_ratio=round(short_ratio, 4),
        sticker_density=round(sticker_density, 4),
        feedback_sticker_density=round(feedback_density, 4),
        tone_particle_rate=round(tone_rate, 4),
    )


def _transition(signals: MoodSignals) -> tuple[MoodLabel, float, str]:
    if signals.short_reply_ratio >= 0.7 and signals.tone_particle_rate <= 0.2 and signals.sticker_density <= 0.1:
        return "cold", 0.74, "short replies without warm particles"
    if signals.reply_delay_s >= 120 or (signals.short_reply_ratio >= 0.55 and signals.tone_particle_rate <= 0.35):
        return "tired", 0.68, "slow or low-effort replies"
    if signals.sticker_density >= 0.35:
        return "playful", 0.76, "sticker density is high"
    if signals.tone_particle_rate >= 0.55 and signals.short_reply_ratio <= 0.45:
        return "high", 0.72, "tone particles indicate high energy"
    return "neutral", 0.55, "no strong mood signal"


def _text(row: dict[str, Any]) -> str:
    return str(row.get("content_text") or row.get("content") or row.get("text") or "").strip()


def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(token.lower() in lowered for token in tokens)


def _avg_user_gap(rows: list[dict[str, Any]]) -> float:
    times = [float(row["created_at"]) for row in rows if _has_number(row.get("created_at"))]
    gaps = [now - prev for prev, now in pairwise(times) if 0 <= now - prev <= 3600]
    return sum(gaps) / len(gaps) if gaps else 0.0


def _has_number(value: object) -> bool:
    try:
        float(value)  # type: ignore[arg-type]
        return True
    except Exception:
        return False


def _feedback_sticker_density(snapshot: Any) -> float:
    if snapshot is None:
        return 0.0
    value = getattr(snapshot, "value", None)
    if not isinstance(value, dict):
        return 0.0
    signals = value.get("signals")
    if not isinstance(signals, dict):
        return 0.0
    return _clamp_feedback_density(signals.get("feedback_sticker_density", 0.0))


def _clamp_feedback_density(value: object) -> float:
    try:
        density = float(cast(Any, value))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(_FEEDBACK_STICKER_DENSITY_CAP, density))
