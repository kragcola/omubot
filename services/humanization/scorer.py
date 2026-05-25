"""Local humanization scorer for reply quality observability."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from services.humanization.contract import LAST_METRICS_SLOT
from services.humanization.state import humanization_source
from services.system_module import Scope

_STICKER_RE = re.compile(r"«表情包:([^»]+)»")
_KAOMOJI_RE = re.compile(r"[\(（][^()\n（）]{0,12}[\)）]|[｡ωд▽≧≦╥﹏]")
_DECOR_RE = re.compile(r"[☆♪✦★♡♥]")
_EXCITED_RE = re.compile(r"[!！]{2,}|哈哈哈|太棒|冲冲|绝了|笑死")
_TEMPLATE_RE = re.compile(r"作为一个AI|根据你的要求|我将|我会尽力|以下是")
_OVERFAMILIAR_RE = re.compile(r"亲亲|宝贝|老婆|贴贴|抱抱")


@dataclass(frozen=True, slots=True)
class HumanizationScore:
    total: float
    axes: dict[str, float]
    issues: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_state_value(self) -> dict[str, Any]:
        return {
            "score": self.total,
            "axes": dict(self.axes),
            "issues": list(self.issues),
            "meta": dict(self.meta),
        }


class StylometricScorer:
    """Score a candidate reply with deterministic, zero-LLM heuristics."""

    async def score_async(self, *args: Any, **kwargs: Any) -> HumanizationScore:
        await asyncio.sleep(0)
        return self.score(*args, **kwargs)

    def score(
        self,
        text: str,
        *,
        register: object | None = None,
        mood: object | None = None,
        recent_sticker_ids: tuple[str, ...] | list[str] = (),
        references: tuple[str, ...] | list[str] = (),
        bus: Any = None,
        scope: Scope | None = None,
    ) -> HumanizationScore:
        reply = str(text or "").strip()
        issues: list[str] = []
        axes = {
            "content": self._content(reply, references, issues),
            "register": self._register(reply, _label(register), issues),
            "mood": self._mood(reply, mood, issues),
            "surface": self._surface(reply, issues),
            "sticker_reuse": self._sticker(reply, recent_sticker_ids, issues),
        }
        total = _clamp(sum(axes[k] * w for k, w in {
            "content": 0.25, "register": 0.20, "mood": 0.20, "surface": 0.25, "sticker_reuse": 0.10,
        }.items()))
        result = HumanizationScore(total=round(total, 4), axes={k: round(v, 4) for k, v in axes.items()}, issues=issues)
        if bus is not None and scope is not None:
            bus.set(
                LAST_METRICS_SLOT,
                result.to_state_value(),
                scope=scope,
                source=humanization_source("stylometric_scorer:score"),
                confidence=result.total,
            )
        return result

    @staticmethod
    def _content(text: str, refs: tuple[str, ...] | list[str], issues: list[str]) -> float:
        if not text:
            issues.append("content.empty")
            return 0.2
        score = 1.0
        if len(text) < 2:
            issues.append("content.too_short")
            score = min(score, 0.45)
        if len(text) > 500:
            issues.append("content.too_long")
            score = min(score, 0.7)
        for ref in refs:
            if ref and fuzz.ratio(text, str(ref)) >= 92:
                issues.append("content.too_similar_reference")
                score = min(score, 0.58)
                break
        return score

    @staticmethod
    def _register(text: str, label: str, issues: list[str]) -> float:
        score = 1.0
        if label == "quiet" and (_EXCITED_RE.search(text) or _KAOMOJI_RE.search(text)):
            issues.append("register.quiet_too_loud")
            score = min(score, 0.55)
        if label == "quiet" and len(text) > 120:
            issues.append("register.quiet_too_long")
            score = min(score, 0.72)
        if label == "distant" and _OVERFAMILIAR_RE.search(text):
            issues.append("register.distant_overfamiliar")
            score = min(score, 0.45)
        if label == "serious" and (_KAOMOJI_RE.search(text) or _DECOR_RE.search(text)):
            issues.append("register.serious_too_decorative")
            score = min(score, 0.55)
        if label == "playful" and len(text) > 220:
            issues.append("register.playful_too_verbose")
            score = min(score, 0.78)
        return score

    @staticmethod
    def _mood(text: str, mood: object | None, issues: list[str]) -> float:
        energy = _num(mood, "energy", 0.5)
        valence = _num(mood, "valence", 0.5)
        if energy < 0.35 and _EXCITED_RE.search(text):
            issues.append("mood.low_energy_overexcited")
            return 0.5
        if valence < 0.35 and _DECOR_RE.search(text):
            issues.append("mood.low_valence_decorative")
            return 0.6
        if energy > 0.75 and len(text) <= 2:
            issues.append("mood.high_energy_too_cold")
            return 0.65
        return 1.0

    @staticmethod
    def _surface(text: str, issues: list[str]) -> float:
        score = 1.0
        if "—" in text:
            issues.append("surface.em_dash")
            score = min(score, 0.72)
        if _DECOR_RE.search(text):
            issues.append("surface.decorative_symbol")
            score = min(score, 0.7)
        if _TEMPLATE_RE.search(text):
            issues.append("surface.template_phrase")
            score = min(score, 0.55)
        if len(re.findall(r"[!！?？]", text)) >= 4:
            issues.append("surface.too_many_punctuations")
            score = min(score, 0.62)
        if text.endswith("。") and len(text) < 8:
            issues.append("surface.short_periodic")
            score = min(score, 0.82)
        return score

    @staticmethod
    def _sticker(text: str, recent: tuple[str, ...] | list[str], issues: list[str]) -> float:
        used = set(_STICKER_RE.findall(text))
        repeated = used.intersection(str(item) for item in recent)
        if repeated:
            issues.append("sticker.reuse_recent")
            return 0.4
        return 1.0


def _label(value: object | None) -> str:
    if isinstance(value, str):
        return value.strip().lower()
    if isinstance(value, dict):
        return str(value.get("label") or value.get("register") or "").strip().lower()
    return str(getattr(value, "label", "") or getattr(value, "register", "")).strip().lower()


def _num(value: object | None, key: str, default: float) -> float:
    raw = value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
