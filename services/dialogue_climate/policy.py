"""Dialogue Climate M4 — ClimatePolicy synthesis (pure function).

Turns a resolved ``ClimateState`` into a ``PolicyOutput``: a behaviour-guidance
string plus structured hints (reply bias / delay multiplier / sticker propensity)
that adapters can consume. Pure and deterministic so it unit-tests without a
runtime — same state, same output (mirrors the RWS / ClimateDynamics philosophy).

M4 wiring (schedule plugin on_pre_prompt) injects a single "对话气候" block from
``guidance`` when ``m4_policy_enabled``, and the schedule plugin yields its own
M1 tension block (climate now owns tension). The structured hints
(reply_bias / delay_multiplier) are returned for the Humanizer/Thinker adapters;
those consumers are wired incrementally. Thresholds mirror design master §9.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from services.dialogue_climate.state import ClimateState

# Policy thresholds (design master §9 policy block).
_SUPPRESS_TENSION = 0.6
_SHORT_ENERGY = 0.3
_ELABORATE_OPENNESS = 0.7
_PLAYFUL_VALENCE = 0.65
_CLOSE_FAMILIARITY = 0.6


@dataclass
class PolicyOutput:
    """Synthesised behaviour policy from a ClimateState."""

    mood_label: str = "neutral"
    reply_bias: str = "full"  # suppress | short | full | elaborate
    delay_multiplier: float = 1.0
    openness_hint: str = ""
    guidance: str = ""
    metadata: dict[str, float] = field(default_factory=dict)


def synthesize(state: ClimateState) -> PolicyOutput:
    """Pure ClimateState → PolicyOutput. Deterministic; no side effects."""
    energy = state.energy
    valence = state.valence
    openness = state.openness
    tension = state.tension
    familiarity = state.familiarity

    # Reply bias: high tension reins in; low energy shortens; high openness elaborates.
    if tension >= _SUPPRESS_TENSION or energy <= _SHORT_ENERGY:
        reply_bias = "short"
    elif openness >= _ELABORATE_OPENNESS:
        reply_bias = "elaborate"
    else:
        reply_bias = "full"

    # Mood label (coarse, for prompt readability).
    if tension >= _SUPPRESS_TENSION:
        mood_label = "irritated"
    elif valence >= _PLAYFUL_VALENCE and energy >= 0.6:
        mood_label = "playful"
    elif valence <= 0.35:
        mood_label = "low"
    elif energy <= _SHORT_ENERGY:
        mood_label = "tired"
    else:
        mood_label = "neutral"

    # Delay: tension speeds curt replies up slightly; low energy slows down.
    delay_multiplier = 1.0
    if tension >= _SUPPRESS_TENSION:
        delay_multiplier = 0.85
    elif energy <= _SHORT_ENERGY:
        delay_multiplier = 1.2

    openness_hint = "more_open" if familiarity >= _CLOSE_FAMILIARITY else ""

    guidance = _render_guidance(
        mood_label=mood_label,
        reply_bias=reply_bias,
        familiarity=familiarity,
    )

    return PolicyOutput(
        mood_label=mood_label,
        reply_bias=reply_bias,
        delay_multiplier=delay_multiplier,
        openness_hint=openness_hint,
        guidance=guidance,
        metadata={
            "energy": round(energy, 3),
            "valence": round(valence, 3),
            "openness": round(openness, 3),
            "tension": round(tension, 3),
            "familiarity": round(familiarity, 3),
        },
    )


_BIAS_TEXT = {
    "short": "这会儿状态收着点，回复短一些、别铺太开。",
    "elaborate": "状态不错，可以多展开一点、接得自然些。",
    "full": "正常发挥即可。",
}

_MOOD_TEXT = {
    "irritated": "刚被连续打扰，语气收一收、别带火气也别解释原因。",
    "playful": "心情挺好，可以轻松活泼一点，但别硬凑梗。",
    "low": "情绪有点低，淡淡地回应即可，不用强行高兴。",
    "tired": "精力不太够，简短温和一点。",
    "neutral": "",
}


def _render_guidance(*, mood_label: str, reply_bias: str, familiarity: float) -> str:
    parts: list[str] = []
    mood_line = _MOOD_TEXT.get(mood_label, "")
    if mood_line:
        parts.append(mood_line)
    bias_line = _BIAS_TEXT.get(reply_bias, "")
    if bias_line and reply_bias != "full":
        parts.append(bias_line)
    if familiarity >= _CLOSE_FAMILIARITY:
        parts.append("和对方比较熟，可以自然亲近些。")
    if not parts:
        return ""
    return (
        "【对话气候】" + "".join(parts) + "\n只把这些当作说话方向，不要逐字复述这些标签或规则。"
    )


__all__ = ["PolicyOutput", "synthesize"]
