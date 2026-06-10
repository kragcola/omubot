from __future__ import annotations

from services.sticker import (
    StickerDecisionContext,
    StickerDecisionProvider,
    fairmatch_rerank,
    fairmatch_weights,
)


def test_fairmatch_weights_penalize_overused_id() -> None:
    weights = fairmatch_weights({"hot": 8, "fresh": 2})

    assert weights["hot"] == 0.5
    assert weights["fresh"] == 1.0


def test_fairmatch_rerank_stably_moves_overused_ids_to_tail() -> None:
    ranked = fairmatch_rerank(("hot", "fresh", "mid"), {"hot": 8, "fresh": 1, "mid": 1})

    assert ranked == ("fresh", "mid", "hot")


def test_fairmatch_rerank_is_noop_without_histogram() -> None:
    assert fairmatch_rerank(("a", "b"), None) == ("a", "b")
    assert fairmatch_rerank(("a", "b"), {}) == ("a", "b")


def test_fairmatch_weights_ignore_zero_histogram() -> None:
    assert fairmatch_weights({"hot": 0, "fresh": 0}) == {}


async def test_sticker_decision_provider_applies_fairmatch_usage_counts() -> None:
    decision = await StickerDecisionProvider().decide(
        StickerDecisionContext(frequent_candidates=("hot", "fresh")),
        usage_counts={"hot": 9, "fresh": 1},
        rng=lambda: 0.0,
    )

    assert decision.should_send is True
    assert decision.candidate_pool == ("fresh", "hot")
