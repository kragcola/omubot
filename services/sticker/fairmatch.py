"""Long-tail FairMatch rerank for sticker candidates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

_OVERUSE_SHARE = 0.5
_OVERUSE_WEIGHT = 0.5


def fairmatch_weights(
    usage_counts: Mapping[str, int] | None,
    *,
    overuse_share: float = _OVERUSE_SHARE,
) -> dict[str, float]:
    if not usage_counts:
        return {}
    counts = {str(key): max(0, int(value)) for key, value in usage_counts.items() if str(key).strip()}
    total = sum(counts.values())
    if total <= 0:
        return {}
    threshold = max(0.0, min(1.0, float(overuse_share)))
    return {
        sticker_id: (_OVERUSE_WEIGHT if count / total >= threshold else 1.0)
        for sticker_id, count in counts.items()
    }


def fairmatch_rerank(
    candidates: Sequence[str],
    usage_counts: Mapping[str, int] | None,
    *,
    overuse_share: float = _OVERUSE_SHARE,
) -> tuple[str, ...]:
    if not candidates or not usage_counts:
        return tuple(candidates)
    weights = fairmatch_weights(usage_counts, overuse_share=overuse_share)
    indexed = [(idx, str(sticker_id), weights.get(str(sticker_id), 1.0)) for idx, sticker_id in enumerate(candidates)]
    indexed.sort(key=lambda item: (-item[2], item[0]))
    return tuple(item[1] for item in indexed)
