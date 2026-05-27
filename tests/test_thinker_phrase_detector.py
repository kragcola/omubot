from __future__ import annotations

from services.llm.thinker_phrase_detector import detect


def test_detect_hits_phrase_overlap() -> None:
    result = detect("那我就顺着这个问题轻轻接一下", "顺着这个问题轻轻接一下", ngram=2, threshold=0.4)

    assert result.hit is True
    assert result.overlap >= 0.4
    assert result.matched_ngrams


def test_detect_skips_when_thought_missing() -> None:
    result = detect("正常回复", "", ngram=2, threshold=0.4)

    assert result.hit is False
    assert result.overlap == 0.0
    assert result.matched_ngrams == ()
