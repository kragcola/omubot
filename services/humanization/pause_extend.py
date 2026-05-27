from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_OPEN_TAIL_RE = re.compile(r"[,，;；:：、]\s*$")
_QUESTION_RE = re.compile(r"[?？]\s*$")
_CONTINUATION_RE = re.compile(r"(还有|另外|不过|但是|然后|顺便|补一句|等下|先别急|话说)")
_CLOSERS = set("。.!！~～…)]）】》\"'")
_ALIASES = {"affectionate": "playful", "distant": "polite_distant", "serious": "polite_distant"}
@dataclass(frozen=True, slots=True)
class PauseExtendConfig:
    min_wait_s: float = 1.2
    max_wait_s: float = 3.0
    base_wait_s: float = 2.2
    min_reply_chars: int = 8
    max_reply_chars: int = 360
    threshold: float = 0.55
@dataclass(frozen=True, slots=True)
class PauseExtendDecision:
    should_extend: bool
    wait_seconds: float
    reasons: tuple[str, ...] = ()
class PauseExtend:
    def __init__(self, config: PauseExtendConfig | None = None) -> None:
        self.config = config or PauseExtendConfig()

    def decide(
        self,
        last_reply: str,
        *,
        register: Any | None = None,
        slot: Any | None = None,
        group_state: Any | None = None,
    ) -> PauseExtendDecision:
        text = " ".join(str(last_reply or "").split())
        wait = self._wait_seconds(register=register, slot=slot, group_state=group_state)
        if not text:
            return PauseExtendDecision(False, wait, ("empty",))
        if _state_bool(group_state, ("user_replied", "user_replied_after_reply", "has_new_user_message")):
            return PauseExtendDecision(False, wait, ("user_replied",))
        if len(text) < self.config.min_reply_chars:
            return PauseExtendDecision(False, wait, ("too_short",))
        if len(text) > self.config.max_reply_chars:
            return PauseExtendDecision(False, wait, ("too_long",))

        score, reasons = self._surface_score(text)
        register_label = _label(register)
        if register_label in {"quiet", "polite_distant"}:
            score -= 0.20
            reasons.append(f"register_{register_label}")
        elif register_label in {"playful", "snark"}:
            score += 0.08
            reasons.append(f"register_{register_label}")

        energy = _number(slot, "energy", 1.0)
        if energy < 0.35:
            score -= 0.18
            reasons.append("low_slot_energy")
        elif energy > 0.75:
            score += 0.04
            reasons.append("high_slot_energy")

        heat = _group_heat(group_state)
        if heat > 0.85:
            score -= 0.08
            reasons.append("hot_group")
        elif heat < 0.25:
            score += 0.06
            reasons.append("quiet_group")
        return PauseExtendDecision(score >= self.config.threshold, wait, tuple(reasons or ("neutral",)))

    def _surface_score(self, text: str) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        if _QUESTION_RE.search(text):
            score -= 0.45
            reasons.append("asks_user")
        if _OPEN_TAIL_RE.search(text) or _has_unclosed_enclosure(text):
            score += 0.42
            reasons.append("open_tail")
        if _CONTINUATION_RE.search(text):
            score += 0.24
            reasons.append("continuation_cue")
        if text[-1] not in _CLOSERS:
            score += 0.18
            reasons.append("unfinished_surface")
        elif not reasons:
            score -= 0.10
            reasons.append("closed_surface")
        return score, reasons

    def _wait_seconds(self, *, register: Any | None, slot: Any | None, group_state: Any | None) -> float:
        wait = self.config.base_wait_s
        register_label = _label(register)
        if register_label in {"quiet", "polite_distant"}:
            wait += 0.35
        elif register_label in {"playful", "snark"}:
            wait -= 0.20
        energy = _number(slot, "energy", 1.0)
        wait += 0.25 if energy < 0.35 else -0.20 if energy > 0.75 else 0.0
        heat = _group_heat(group_state)
        wait += -0.55 if heat > 0.75 else 0.20 if heat < 0.25 else 0.0
        return round(max(self.config.min_wait_s, min(self.config.max_wait_s, wait)), 3)
def _label(value: Any | None) -> str:
    if isinstance(value, str):
        label = value
    elif isinstance(value, dict):
        label = str(value.get("label") or value.get("register") or value.get("name") or "")
    else:
        label = str(getattr(value, "label", "") or getattr(value, "register", "") or getattr(value, "name", ""))
    normalized = label.strip().lower()
    return _ALIASES.get(normalized, normalized)
def _number(value: Any | None, key: str, default: float) -> float:
    raw = value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default
def _state_bool(value: Any | None, keys: tuple[str, ...]) -> bool:
    return (
        any(bool(value.get(key)) for key in keys)
        if isinstance(value, dict)
        else any(bool(getattr(value, key, False)) for key in keys)
    )

def _group_heat(group_state: Any | None) -> float:
    for key in ("heat", "rho", "activity", "message_rate"):
        heat = _number(group_state, key, -1.0)
        if heat >= 0.0:
            return max(0.0, min(1.0, heat))
    return 0.5

def _has_unclosed_enclosure(text: str) -> bool:
    pairs = {"(": ")", "（": "）", "[": "]", "【": "】", "《": "》"}
    stack: list[str] = []
    for ch in text:
        if ch in pairs:
            stack.append(pairs[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
    return bool(stack)
