"""Negative-signal sources for D.3 reflection generation.

Three taxonomy entries mapped 1:1 to the audit's reflection inputs:

1. ``style_feedback(rating='negative')`` — admin / user explicitly
   marked a bot reply as bad-style
2. ``style_expressions(status='rejected')`` — admin rejected a learned
   expression (this whole expression should not be reused)
3. ``slang_drift_reviews(status='rejected')`` — admin rejected a slang
   drift signal (the AI / scout was wrong about the term shift)

Each source produces :class:`NegativeSignal` rows with a stable
``source_table`` + ``source_id`` pair so the reflection store can dedup
exactly-once via its UNIQUE constraint. Sources are kept independently
testable — none of them touch the LLM or candidate store.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from loguru import logger

_L = logger.bind(channel="memory_consolidator")


@dataclass(slots=True)
class NegativeSignal:
    """One actionable "things went wrong" data point.

    Fields are intentionally narrow — anything richer goes in ``meta``.
    The tuple ``(source_table, source_id)`` is unique within this
    process; the reflection log enforces it across processes via SQLite.
    """

    source_table: str
    source_id: str
    group_id: str
    summary: str
    detail: str
    occurred_at: float
    meta: dict[str, Any] = field(default_factory=dict)


def _iso_to_epoch(value: str) -> float:
    """Best-effort ISO-8601 → epoch; returns 0.0 on parse failure."""
    if not value:
        return 0.0
    try:
        cleaned = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    except (TypeError, ValueError):
        return 0.0


async def fetch_style_feedback_signals(
    style_store: Any,
    *,
    group_id: str = "",
    limit: int = 30,
) -> list[NegativeSignal]:
    """Pull recent ``style_feedback`` rows where ``rating='negative'``.

    Uses the existing :meth:`StyleStore.list_feedback` API; rating is
    filtered in Python (the public API does not expose a rating-only
    filter, and the row count is bounded by ``limit`` * a small factor).
    Falls back gracefully if the store is missing the method.
    """
    if style_store is None:
        return []
    list_feedback = getattr(style_store, "list_feedback", None)
    if not callable(list_feedback):
        return []
    list_feedback_fn = cast(
        "Callable[..., Awaitable[tuple[list[Any], int]]]", list_feedback,
    )
    try:
        feedbacks, _total = await list_feedback_fn(
            group_id=group_id or "",
            limit=max(20, int(limit) * 3),
            sort="default",
        )
    except Exception as exc:
        _L.warning("style_feedback fetch failed | error={}", exc)
        return []
    out: list[NegativeSignal] = []
    for fb in feedbacks:
        if getattr(fb, "rating", "") != "negative":
            continue
        raw_text = str(getattr(fb, "raw_text", "") or "")
        context = str(getattr(fb, "context", "") or "")
        target_type = str(getattr(fb, "target_type", "") or "")
        target_id = str(getattr(fb, "target_id", "") or "")
        out.append(
            NegativeSignal(
                source_table="style_feedback",
                source_id=str(getattr(fb, "feedback_id", "")),
                group_id=str(getattr(fb, "group_id", "") or ""),
                summary=(
                    f"管理员对 {target_type}={target_id} 给了负反馈"
                    if target_id
                    else "管理员给了负反馈"
                ),
                detail=(raw_text or context or "（未提供细节）")[:1200],
                occurred_at=_iso_to_epoch(str(getattr(fb, "created_at", ""))),
                meta={
                    "target_type": target_type,
                    "target_id": target_id,
                    "actor": str(getattr(fb, "actor", "") or ""),
                },
            )
        )
        if len(out) >= int(limit):
            break
    return out


async def fetch_style_rejected_expressions(
    style_store: Any,
    *,
    group_id: str = "",
    limit: int = 30,
) -> list[NegativeSignal]:
    """Pull style expressions whose status was set to ``rejected``.

    Returned signals use ``source_id = expression_id`` so the same
    expression rejected twice (e.g. revival re-rejected) still produces
    only one reflection candidate.
    """
    if style_store is None:
        return []
    list_expressions = getattr(style_store, "list_expressions", None)
    if not callable(list_expressions):
        return []
    list_expressions_fn = cast(
        "Callable[..., Awaitable[tuple[list[Any], int]]]", list_expressions,
    )
    try:
        expressions, _total = await list_expressions_fn(
            status="rejected",
            group_id=group_id or "",
            limit=max(20, int(limit)),
            sort="time",
        )
    except ValueError:
        # status whitelist on the store doesn't include 'rejected' — caller
        # passed an unsupported store contract; treat as zero signals.
        return []
    except Exception as exc:
        _L.warning("style_rejected fetch failed | error={}", exc)
        return []
    out: list[NegativeSignal] = []
    for expr in expressions:
        expression_text = str(getattr(expr, "expression", "") or "")
        situation = str(getattr(expr, "situation", "") or "")
        out.append(
            NegativeSignal(
                source_table="style_expressions",
                source_id=str(getattr(expr, "expression_id", "")),
                group_id=str(getattr(expr, "group_id", "") or ""),
                summary=f"被驳回的表达：{expression_text[:80]}",
                detail=(
                    f"表达：{expression_text}\n场景：{situation}"
                ).strip()[:1200],
                occurred_at=_iso_to_epoch(
                    str(getattr(expr, "updated_at", "") or "")
                    or str(getattr(expr, "created_at", "") or "")
                ),
                meta={
                    "expression": expression_text,
                    "scope": str(getattr(expr, "scope", "") or ""),
                    "confidence": float(getattr(expr, "confidence", 0.0) or 0.0),
                },
            )
        )
        if len(out) >= int(limit):
            break
    return out


async def fetch_slang_rejected_drifts(
    slang_store: Any,
    *,
    group_id: str = "",
    limit: int = 30,
) -> list[NegativeSignal]:
    """Pull slang drift reviews whose status was set to ``rejected``.

    Falls back to an empty list if the store does not expose
    :meth:`list_drift_reviews` — keeps the reflector resilient to older
    SlangStore builds without forcing every deployment to migrate.
    """
    if slang_store is None:
        return []
    fetcher = getattr(slang_store, "list_drift_reviews", None)
    if not callable(fetcher):
        return []
    fetcher_fn = cast("Callable[..., Awaitable[Any]]", fetcher)
    try:
        result = await fetcher_fn(
            status="rejected",
            group_id=group_id or "",
            limit=max(20, int(limit)),
        )
    except TypeError:
        try:
            result = await fetcher_fn(status="rejected")
        except Exception as exc:
            _L.warning("slang_drift fetch failed (positional) | error={}", exc)
            return []
    except Exception as exc:
        _L.warning("slang_drift fetch failed | error={}", exc)
        return []
    drifts: list[Any]
    if isinstance(result, tuple):
        drifts = list(result[0]) if result else []
    elif isinstance(result, list):
        drifts = result
    else:
        drifts = []
    out: list[NegativeSignal] = []
    for drift in drifts or []:
        if getattr(drift, "status", "") != "rejected":
            continue
        gid = str(getattr(drift, "group_id", "") or "")
        if group_id and gid and gid != str(group_id):
            continue
        term = str(getattr(drift, "term", "") or "")
        reason = str(getattr(drift, "reason", "") or "")
        out.append(
            NegativeSignal(
                source_table="slang_drift_reviews",
                source_id=str(getattr(drift, "drift_id", "")),
                group_id=gid,
                summary=f"被驳回的黑话漂移：{term[:60]}",
                detail=(f"词条：{term}\n理由：{reason}").strip()[:1200],
                occurred_at=float(getattr(drift, "updated_at", 0) or 0)
                or _iso_to_epoch(str(getattr(drift, "created_at", "") or "")),
                meta={
                    "term": term,
                    "reason": reason,
                },
            )
        )
        if len(out) >= int(limit):
            break
    return out


async def collect_negative_signals(
    *,
    style_store: Any = None,
    slang_store: Any = None,
    group_id: str = "",
    limit_per_source: int = 30,
) -> list[NegativeSignal]:
    """Aggregate all configured sources into one ``NegativeSignal`` list.

    Sources that fail individually are logged and dropped — one broken
    table must not block the others. Result order is the natural
    concatenation; the reflector is responsible for deduping via
    ``(source_table, source_id)``.
    """
    out: list[NegativeSignal] = []
    out.extend(
        await fetch_style_feedback_signals(
            style_store, group_id=group_id, limit=limit_per_source,
        )
    )
    out.extend(
        await fetch_style_rejected_expressions(
            style_store, group_id=group_id, limit=limit_per_source,
        )
    )
    out.extend(
        await fetch_slang_rejected_drifts(
            slang_store, group_id=group_id, limit=limit_per_source,
        )
    )
    return out
