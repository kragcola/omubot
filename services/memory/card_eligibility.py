"""Card category time eligibility for prompt recall (v1).

Read-time, non-destructive, fail-closed filter used by RetrievalGate and
TemporalTrace active-head selection only. Admin / list / tool historical
readers and ``CardStore`` direct APIs are intentionally untouched.

``ttl_turns`` remains a reserved schema field and is **not** interpreted here.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

# Categories with automatic calendar-day decay in v1. All other valid Card
# categories have no automatic expiry under the default policy.
_DEFAULT_CATEGORY_TTL_DAYS: Mapping[str, int] = MappingProxyType({
    "status": 30,
    "event": 180,
})

# Bounds for config-driven TTL days (inclusive).
_MIN_TTL_DAYS = 1
_MAX_TTL_DAYS = 3650


def _clamp_ttl_days(value: int) -> int:
    return max(_MIN_TTL_DAYS, min(_MAX_TTL_DAYS, int(value)))


@dataclass(frozen=True, slots=True)
class CardEligibilityPolicy:
    """Immutable recall eligibility policy.

    When ``enabled`` is False the helpers are identity for active-only recall
    (pre-v1 behavior). Category TTLs only apply to keys present in
    ``category_ttl_days``; missing categories never auto-expire in v1.
    """

    enabled: bool = True
    category_ttl_days: Mapping[str, int] = _DEFAULT_CATEGORY_TTL_DAYS

    def __post_init__(self) -> None:
        # Normalize to an immutable map with clamped positive TTLs.
        raw = dict(self.category_ttl_days or {})
        normalized: dict[str, int] = {}
        for key, value in raw.items():
            cat = str(key).strip()
            if not cat:
                continue
            try:
                days = _clamp_ttl_days(int(value))
            except (TypeError, ValueError):
                continue
            normalized[cat] = days
        object.__setattr__(
            self,
            "category_ttl_days",
            MappingProxyType(normalized),
        )

    def ttl_days_for(self, category: str) -> int | None:
        """Return TTL days for *category*, or None if non-decaying."""
        return self.category_ttl_days.get(str(category or "").strip())


DEFAULT_CARD_ELIGIBILITY_POLICY = CardEligibilityPolicy()


def parse_card_anchor_timestamp(value: str) -> datetime | None:
    """Parse a Card anchor timestamp for eligibility comparison.

    Contract:
    - empty / whitespace-only → ``None`` (caller decides fail-closed)
    - explicit offset or ``Z`` preserved
    - offset-less values treated as ``Asia/Shanghai`` (CardStore write convention)
    - malformed non-empty → ``None`` (fail closed for decaying categories)

    Does **not** change RetrievalGate recency ranking (which still uses UTC for
    naive values).
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    cleaned = raw.replace("Z", "+00:00").replace("z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=TZ_SHANGHAI)
    return parsed


def card_time_anchor(card: Any) -> str:
    """Primary ``updated_at``, fallback ``created_at`` when updated_at empty."""
    updated = str(getattr(card, "updated_at", "") or "").strip()
    if updated:
        return updated
    return str(getattr(card, "created_at", "") or "").strip()


def is_card_eligible_for_recall(
    card: Any,
    *,
    policy: CardEligibilityPolicy | None = None,
    now: datetime | None = None,
) -> bool:
    """Return True when *card* may enter prompt recall under *policy*.

    Fail-closed for decaying categories with missing or malformed anchors.
    Non-decaying categories skip age checks. Disabled policy is identity
    (does not re-check store status beyond optional active guard).
    """
    pol = policy if policy is not None else DEFAULT_CARD_ELIGIBILITY_POLICY
    if not pol.enabled:
        return True

    status = str(getattr(card, "status", "active") or "active").strip()
    if status != "active":
        return False

    category = str(getattr(card, "category", "") or "").strip()
    ttl_days = pol.ttl_days_for(category)
    if ttl_days is None:
        return True

    anchor_raw = card_time_anchor(card)
    if not anchor_raw:
        return False
    anchor = parse_card_anchor_timestamp(anchor_raw)
    if anchor is None:
        return False

    clock = now if now is not None else datetime.now(tz=UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    age = clock.astimezone(UTC) - anchor.astimezone(UTC)
    if age < timedelta(0):
        # Future-dated anchors remain eligible (clock skew / test injection).
        return True
    return age <= timedelta(days=ttl_days)


def filter_cards_for_recall(
    cards: Iterable[Any],
    *,
    policy: CardEligibilityPolicy | None = None,
    now: datetime | None = None,
) -> list[Any]:
    """Filter *cards* to those eligible for prompt recall (stable order)."""
    pol = policy if policy is not None else DEFAULT_CARD_ELIGIBILITY_POLICY
    if not pol.enabled:
        return list(cards)
    return [
        card
        for card in cards
        if is_card_eligible_for_recall(card, policy=pol, now=now)
    ]


def filter_scored_cards_for_recall(
    scored: Sequence[tuple[float, Any]],
    *,
    policy: CardEligibilityPolicy | None = None,
    now: datetime | None = None,
) -> list[tuple[float, Any]]:
    """Filter ``(score, card)`` pairs for prompt recall (stable order)."""
    pol = policy if policy is not None else DEFAULT_CARD_ELIGIBILITY_POLICY
    if not pol.enabled:
        return list(scored)
    return [
        (score, card)
        for score, card in scored
        if is_card_eligible_for_recall(card, policy=pol, now=now)
    ]


def policy_from_config(
    raw: Mapping[str, Any] | CardEligibilityPolicy | None,
) -> CardEligibilityPolicy:
    """Build a policy from nested plugin config or an existing policy."""
    if raw is None:
        return DEFAULT_CARD_ELIGIBILITY_POLICY
    if isinstance(raw, CardEligibilityPolicy):
        return raw
    enabled = bool(raw.get("enabled", True))
    # Prefer explicit per-category map; fall back to status/event day fields.
    cat_map: dict[str, int] = {}
    nested = raw.get("category_ttl_days")
    if isinstance(nested, Mapping):
        for key, value in nested.items():
            try:
                cat_map[str(key)] = int(value)
            except (TypeError, ValueError):
                continue
    if "status_ttl_days" in raw:
        with contextlib.suppress(TypeError, ValueError):
            cat_map["status"] = int(raw["status_ttl_days"])
    if "event_ttl_days" in raw:
        with contextlib.suppress(TypeError, ValueError):
            cat_map["event"] = int(raw["event_ttl_days"])
    if not cat_map:
        cat_map = dict(_DEFAULT_CATEGORY_TTL_DAYS)
    return CardEligibilityPolicy(enabled=enabled, category_ttl_days=cat_map)
