"""Pure event selection rules for QZone Journal.

v0.4 adds a closed selection-decision reason enum, deterministic
publish-worth scoring, and ranking helpers. Hard gates remain fail-closed;
no embeddings or LLM ranking.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plugins.qzone_journal.public_projection import ValidatedPublicProjection

# Closed reason codes for every evaluated/adapted selection decision.
SELECTION_REASON_CODES: frozenset[str] = frozenset(
    {
        "reject_source_not_allowed",
        "reject_privacy_not_public",
        "reject_subject_not_allowed",
        "reject_empty_identity",
        "reject_salience_out_of_range",
        "reject_below_threshold",
        "reject_adapter_unparseable",
        "reject_missing_subject_privacy",
        "reject_duplicate_dedupe",
        "reject_out_ranked",
        "reject_day_draft_budget",
        "reject_review_field",
        "reject_public_projection",
        "accept",
    }
)

# Word runs (letters/digits/underscore) or single CJK ideograph.
_TOKEN_RE = re.compile(
    r"[0-9A-Za-z_]+|[\u3400-\u9FFF\uF900-\uFAFF\u3040-\u30FF]",
    re.UNICODE,
)


@dataclass(frozen=True, slots=True)
class CandidateEvent:
    source: str
    event_date: date
    stable_id: str
    subject_kind: str
    privacy: str
    salience: float
    summary: str
    # Factual Part C only: immutable validated public projection (never internal refs).
    public_projection: ValidatedPublicProjection | None = None

    @property
    def dedupe_key(self) -> str:
        identity = "\x1f".join((self.source, self.event_date.isoformat(), self.stable_id))
        return "qzone_event_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]

    def __post_init__(self) -> None:
        """Bound projection field; bare factual is left for selector fail-closed.

        When a projection object is present it must be a validated factual
        projection whose projected_summary matches ``summary``. Non-factual
        subjects must not carry a projection.
        """
        kind = str(self.subject_kind or "").strip()
        projection = self.public_projection
        if projection is None:
            return
        from plugins.qzone_journal.public_projection import (
            is_validated_public_projection,
        )

        if kind != "factual":
            raise ValueError(
                "public_projection is only allowed for subject_kind=factual"
            )
        if not is_validated_public_projection(projection):
            raise ValueError(
                "public_projection must be a ValidatedPublicProjection"
            )
        projected = str(getattr(projection, "projected_summary", "") or "").strip()
        if not projected or projected != str(self.summary or "").strip():
            raise ValueError(
                "factual CandidateEvent summary must equal projected_summary"
            )


@dataclass(frozen=True, slots=True)
class PublishWorthScore:
    """Deterministic publish-worth components in [0, 1]."""

    importance: float
    recency: float
    novelty: float
    relevance: float
    total: float


@dataclass(frozen=True, slots=True)
class SelectionDecision:
    """Closed-reason selection outcome for one candidate evaluation."""

    reason: str
    candidate: CandidateEvent | None = None
    score: PublishWorthScore | None = None
    source: str = ""

    @property
    def accepted(self) -> bool:
        return self.reason == "accept" and self.candidate is not None


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def tokenize_summary(text: str) -> frozenset[str]:
    """Conservative Unicode-aware tokens suitable for Chinese + Latin."""
    raw = str(text or "").strip().casefold()
    if not raw:
        return frozenset()
    return frozenset(_TOKEN_RE.findall(raw))


def jaccard(a: frozenset[str] | set[str], b: frozenset[str] | set[str]) -> float:
    if not a and not b:
        return 0.0
    left = set(a)
    right = set(b)
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def compute_publish_worth(
    event: CandidateEvent,
    *,
    today: date | None = None,
    recent_summaries: Sequence[str] = (),
    day_narrative: str = "",
    novelty_window: int = 32,
) -> PublishWorthScore:
    """Score one accepted-gate candidate; pure and deterministic.

    When ``today`` is omitted, recency is always 0 (no implicit
    event_date-as-today). Production ticks must pass CST today explicitly.
    """
    importance = clamp01(float(event.salience))
    recency = 1.0 if today is not None and event.event_date == today else 0.0

    summary_tokens = tokenize_summary(event.summary)
    window = max(0, int(novelty_window))
    recent = list(recent_summaries)[:window] if window else []
    if not recent or not summary_tokens:
        max_overlap = 0.0
    else:
        max_overlap = max(
            (jaccard(summary_tokens, tokenize_summary(item)) for item in recent),
            default=0.0,
        )
    novelty = clamp01(1.0 - max_overlap)

    narrative = str(day_narrative or "").strip()
    relevance = (
        clamp01(jaccard(summary_tokens, tokenize_summary(narrative)))
        if narrative
        else 0.5
    )

    total = (
        0.50 * importance
        + 0.20 * recency
        + 0.20 * novelty
        + 0.10 * relevance
    )
    return PublishWorthScore(
        importance=importance,
        recency=recency,
        novelty=novelty,
        relevance=relevance,
        total=round(total, 12),
    )


@dataclass(frozen=True, slots=True, order=False)
class _RankingKey:
    """Comparable key for ``sorted(..., key=ranking_key, reverse=True)``.

    Under reverse sort: higher total, then higher salience, then
    lexicographically smaller stable_id. The inverted stable_id comparison
    is required because reverse=True would otherwise prefer larger ids.
    """

    total: float
    salience: float
    stable_id: str

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, _RankingKey):
            return NotImplemented
        if self.total != other.total:
            return self.total < other.total
        if self.salience != other.salience:
            return self.salience < other.salience
        # Invert stable_id so reverse=True yields ascending string order.
        return self.stable_id > other.stable_id

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _RankingKey):
            return NotImplemented
        return (
            self.total == other.total
            and self.salience == other.salience
            and self.stable_id == other.stable_id
        )


def ranking_key(
    decision: SelectionDecision,
) -> _RankingKey:
    """Canonical ranking key matching :func:`rank_accepted`.

    Designed for ``sorted(items, key=ranking_key, reverse=True)`` so that
    higher total and salience win first, then lexicographically smaller
    ``stable_id``. Equivalent ascending tuple form is
    ``(-total, -salience, stable_id)``; this key reuses reverse sort while
    preserving the stable_id ascending tie-break.
    """
    candidate = decision.candidate
    score = decision.score
    if candidate is None or score is None:
        return _RankingKey(total=-1.0, salience=-1.0, stable_id="\uffff")
    return _RankingKey(
        total=float(score.total),
        salience=float(candidate.salience),
        stable_id=str(candidate.stable_id),
    )


def rank_accepted(decisions: Iterable[SelectionDecision]) -> list[SelectionDecision]:
    """Sort accepted decisions with deterministic publish-worth tie-break.

    Reuses :func:`ranking_key` so reverse-sorted ranking_key order cannot
    diverge from rank_accepted.
    """
    accepted = [item for item in decisions if item.accepted and item.score is not None]
    return sorted(accepted, key=ranking_key, reverse=True)


class JournalSelector:
    """Fail-closed selector for verifiable self/fiction events."""

    def __init__(
        self,
        *,
        allowed_sources: set[str],
        salience_threshold: float,
        advanced_enabled: bool = False,
    ) -> None:
        self._allowed_sources = frozenset(str(item).strip() for item in allowed_sources)
        self._salience_threshold = max(0.0, min(1.0, float(salience_threshold)))
        self._advanced_enabled = bool(advanced_enabled)

    def evaluate(
        self,
        event: CandidateEvent,
        *,
        today: date | None = None,
        recent_summaries: Sequence[str] = (),
        day_narrative: str = "",
        novelty_window: int = 32,
    ) -> SelectionDecision:
        """Map one candidate to a closed reason; score only on accept."""
        _ = self._advanced_enabled  # reserved for a separately reviewed Part C adapter
        source = str(event.source or "")
        if event.source not in self._allowed_sources:
            return SelectionDecision(
                reason="reject_source_not_allowed",
                source=source,
            )
        if event.privacy != "public":
            return SelectionDecision(
                reason="reject_privacy_not_public",
                candidate=event,
                source=source,
            )
        if (
            event.subject_kind == "self"
            and event.source in {"dream_reflection", "schedule_generator"}
        ):
            return SelectionDecision(
                reason="reject_subject_not_allowed",
                candidate=event,
                source=source,
            )
        if event.subject_kind == "factual":
            # Factual is a permitted subject only with a validated closed-template
            # projection whose rendered summary matches the candidate summary.
            # Bare factual (constructible) and invalid projection → reject_public_projection.
            from plugins.qzone_journal.public_projection import (
                is_validated_public_projection,
            )

            if not is_validated_public_projection(event.public_projection):
                return SelectionDecision(
                    reason="reject_public_projection",
                    candidate=event,
                    source=source,
                )
            projected = str(
                getattr(event.public_projection, "projected_summary", "") or ""
            ).strip()
            if not projected or projected != str(event.summary or "").strip():
                return SelectionDecision(
                    reason="reject_public_projection",
                    candidate=event,
                    source=source,
                )
        elif event.subject_kind not in {"self", "fiction"}:
            return SelectionDecision(
                reason="reject_subject_not_allowed",
                candidate=event,
                source=source,
            )
        if not event.stable_id.strip() or not event.summary.strip():
            return SelectionDecision(
                reason="reject_empty_identity",
                candidate=event,
                source=source,
            )
        try:
            salience = float(event.salience)
        except (TypeError, ValueError):
            return SelectionDecision(
                reason="reject_salience_out_of_range",
                candidate=event,
                source=source,
            )
        if not 0.0 <= salience <= 1.0:
            return SelectionDecision(
                reason="reject_salience_out_of_range",
                candidate=event,
                source=source,
            )
        if salience < self._salience_threshold:
            return SelectionDecision(
                reason="reject_below_threshold",
                candidate=event,
                source=source,
            )
        # Omit today → recency 0; never treat event.event_date as implicit today.
        score = compute_publish_worth(
            event,
            today=today,
            recent_summaries=recent_summaries,
            day_narrative=day_narrative,
            novelty_window=novelty_window,
        )
        return SelectionDecision(
            reason="accept",
            candidate=event,
            score=score,
            source=source,
        )

    def select(
        self,
        event: CandidateEvent,
        *,
        today: date | None = None,
        recent_summaries: Sequence[str] = (),
        day_narrative: str = "",
    ) -> CandidateEvent | None:
        """Backward-compatible boolean gate over :meth:`evaluate`."""
        decision = self.evaluate(
            event,
            today=today,
            recent_summaries=recent_summaries,
            day_narrative=day_narrative,
        )
        return decision.candidate if decision.accepted else None


__all__ = [
    "SELECTION_REASON_CODES",
    "CandidateEvent",
    "JournalSelector",
    "PublishWorthScore",
    "SelectionDecision",
    "clamp01",
    "compute_publish_worth",
    "jaccard",
    "rank_accepted",
    "ranking_key",
    "tokenize_summary",
]
