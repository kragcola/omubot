from __future__ import annotations

from services.llm.dedup_gate import DuplicateDecision, is_near_duplicate, normalize_text


def test_normalize_text_collapses_width_and_punctuation() -> None:
    assert normalize_text("  Ｈｅｌｌｏ，世界！ ") == "hello世界"


def test_is_near_duplicate_hits_containment_shortcut() -> None:
    decision = is_near_duplicate("我今天真的很困啊", "我今天真的很困啊……", ngram=3, threshold=0.4)

    assert decision == DuplicateDecision(True, 1.0)


def test_is_near_duplicate_uses_ngram_overlap() -> None:
    decision = is_near_duplicate("等我查一下文档再回你", "我先去查一下文档然后回你", ngram=2, threshold=0.2)

    assert decision.is_duplicate is True
    assert decision.overlap >= 0.2


def test_is_near_duplicate_ignores_empty_history() -> None:
    decision = is_near_duplicate("新的回复", "", ngram=5, threshold=0.4)

    assert decision.is_duplicate is False
    assert decision.overlap == 0.0
