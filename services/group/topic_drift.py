"""Inline topic drift detection for recent group messages."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from services.similarity import NgramSimilarityProvider, SimilarityProvider, normalize_text_key

_CQ_RE = re.compile(r"\[CQ:[^\]]+\]")
_URL_RE = re.compile(r"https?://\S+")
_QQ_FROM_SPEAKER_RE = re.compile(r"\((\d+)\)$")


@dataclass(frozen=True)
class TopicDriftResult:
    topic: str
    drift_score: float
    is_new_topic: bool
    participants: tuple[str, ...]


class TopicDriftDetector:
    """Compare the latest user message against the prior two-message topic."""

    def __init__(
        self,
        *,
        similarity: SimilarityProvider | None = None,
        drift_threshold: float = 0.6,
    ) -> None:
        self._similarity = similarity or NgramSimilarityProvider()
        self.drift_threshold = max(0.0, min(1.0, float(drift_threshold)))

    async def detect(self, messages: Sequence[Mapping[str, Any] | object]) -> TopicDriftResult:
        recent = [_as_message(row) for row in messages if _is_user_message(row)][-3:]
        participants = _participants(recent)
        if not recent:
            return TopicDriftResult(topic="", drift_score=0.0, is_new_topic=False, participants=participants)

        current = _clean_topic(recent[-1].get("text", ""))
        if len(recent) < 2:
            return TopicDriftResult(topic=current, drift_score=0.0, is_new_topic=False, participants=participants)

        previous = " ".join(_clean_topic(row.get("text", "")) for row in recent[:-1])
        similarity = self._safe_similarity(previous, current)
        drift_score = round(1.0 - similarity, 3)
        return TopicDriftResult(
            topic=current,
            drift_score=drift_score,
            is_new_topic=drift_score > self.drift_threshold,
            participants=participants,
        )

    def _safe_similarity(self, previous: str, current: str) -> float:
        if not normalize_text_key(previous) or not normalize_text_key(current):
            return 0.0
        try:
            value = self._similarity.similarity(previous, current)
        except RuntimeError:
            value = NgramSimilarityProvider().similarity(previous, current)
        return max(0.0, min(1.0, float(value)))


def _as_message(row: Mapping[str, Any] | object) -> dict[str, str]:
    speaker = (
        _field(row, "speaker_id")
        or _field(row, "user_id")
        or _field(row, "sender_id")
        or _field(row, "speaker")
        or ""
    )
    text = (
        _field(row, "content_text")
        or _field(row, "text")
        or _field(row, "raw_message")
        or _field(row, "message")
        or ""
    )
    return {
        "role": str(_field(row, "role") or ""),
        "speaker": str(speaker),
        "text": str(text),
    }


def _is_user_message(row: Mapping[str, Any] | object) -> bool:
    role = str(_field(row, "role") or "").lower()
    return role in {"", "user", "human"}


def _participants(rows: Sequence[dict[str, str]]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for row in rows:
        speaker = row["speaker"].strip()
        match = _QQ_FROM_SPEAKER_RE.search(speaker)
        participant = match.group(1) if match else speaker
        if participant and participant not in seen:
            seen.add(participant)
            out.append(participant)
    return tuple(out)


def _clean_topic(text: str) -> str:
    text = _CQ_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    topic = normalize_text_key(text)
    return topic[:80]


def _field(payload: Any, key: str) -> Any:
    if payload is None:
        return None
    if isinstance(payload, Mapping):
        return payload.get(key)
    return getattr(payload, key, None)
