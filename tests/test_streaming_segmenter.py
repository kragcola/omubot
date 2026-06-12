"""Tests for online SSE streaming segmenter."""

from __future__ import annotations

from services.llm.streaming_segmenter import StreamingSegmenter, StreamingSegmenterConfig


def test_flushes_on_sentence_boundary_after_min_chars() -> None:
    segmenter = StreamingSegmenter(StreamingSegmenterConfig(min_chars=3, soft_chars=10, hard_chars=30))

    assert segmenter.push("你好") == []
    assert segmenter.push("呀。后面") == ["你好呀。"]
    assert segmenter.buffered_text == "后面"


def test_does_not_flush_sentence_before_min_chars() -> None:
    segmenter = StreamingSegmenter(StreamingSegmenterConfig(min_chars=6, soft_chars=10, hard_chars=30))

    assert segmenter.push("好。") == []
    assert segmenter.finish() == ["好。"]


def test_clause_boundary_flushes_after_target_chars() -> None:
    segmenter = StreamingSegmenter(StreamingSegmenterConfig(min_chars=3, soft_chars=6, hard_chars=30))

    assert segmenter.push("你好呀呀呀，后半") == ["你好呀呀呀，"]
    assert segmenter.finish() == ["后半"]


def test_hard_limit_flushes_without_punctuation() -> None:
    segmenter = StreamingSegmenter(StreamingSegmenterConfig(min_chars=3, soft_chars=30, hard_chars=8))

    assert segmenter.push("一二三四五六七八九十") == ["一二三四五六七八"]
    assert segmenter.finish() == ["九十"]


def test_finish_drains_tail_once() -> None:
    segmenter = StreamingSegmenter()
    segmenter.push("还没到边界")

    assert segmenter.finish() == ["还没到边界"]
    assert segmenter.finish() == []


def test_cancel_drains_and_prevents_next_reply_contamination() -> None:
    segmenter = StreamingSegmenter(StreamingSegmenterConfig(min_chars=3, soft_chars=10, hard_chars=30))
    segmenter.push("上一条未完")

    assert segmenter.cancel() == ["上一条未完"]
    assert segmenter.buffered_text == ""
    assert segmenter.push("下一句。") == ["下一句。"]


def test_register_and_mood_adjust_target_chars() -> None:
    neutral = StreamingSegmenter(StreamingSegmenterConfig(soft_chars=20), register="neutral", mood="neutral")
    playful = StreamingSegmenter(StreamingSegmenterConfig(soft_chars=20), register="playful", mood="high")
    quiet = StreamingSegmenter(StreamingSegmenterConfig(soft_chars=20), register="quiet", mood="cold")

    assert playful.target_chars < neutral.target_chars
    assert quiet.target_chars > neutral.target_chars


def test_cq_code_is_not_split_before_sentence_boundary() -> None:
    segmenter = StreamingSegmenter(StreamingSegmenterConfig(min_chars=3, soft_chars=5, hard_chars=12))

    assert segmenter.push("[CQ:reply,id=123]你好。") == ["[CQ:reply,id=123]你好。"]


def test_url_is_protected_from_hard_split_until_safe_boundary() -> None:
    segmenter = StreamingSegmenter(StreamingSegmenterConfig(min_chars=3, soft_chars=6, hard_chars=12))

    assert segmenter.push("https://example.com/a/b") == []
    assert segmenter.push(" 后面。") == ["https://example.com/a/b 后面。"]
    assert segmenter.finish() == []


def test_ascii_token_is_protected_until_whitespace() -> None:
    segmenter = StreamingSegmenter(StreamingSegmenterConfig(min_chars=3, soft_chars=6, hard_chars=8))

    assert segmenter.push("super_long_token") == []
    assert segmenter.push(" 后面") == ["super_long_token"]
    assert segmenter.finish() == ["后面"]


def test_repeated_ender_run_split_across_deltas_stays_whole() -> None:
    # A run that straddles deltas (≥2 enders buffered) must defer until it stops growing,
    # never cutting mid-run. Mirrors realistic SSE token granularity.
    segmenter = StreamingSegmenter(StreamingSegmenterConfig(min_chars=2, soft_chars=8, hard_chars=20))
    out: list[str] = []
    for delta in ["好耶", "！！", "！", "太棒了呀"]:
        out += segmenter.push(delta)
    out += segmenter.finish()

    assert "好耶！！！" in out
    assert "！" not in out


def test_ellipsis_run_not_split_in_stream() -> None:
    segmenter = StreamingSegmenter(StreamingSegmenterConfig(min_chars=2, soft_chars=8, hard_chars=20))
    out: list[str] = []
    for delta in ["唔", "……", "在呢", "……"]:
        out += segmenter.push(delta)
    out += segmenter.finish()

    assert out == ["唔……", "在呢……"]
    assert not any(seg == "…" for seg in out)


def test_proper_noun_with_punctuation_protected_in_stream() -> None:
    segmenter = StreamingSegmenter(StreamingSegmenterConfig(min_chars=2, soft_chars=8, hard_chars=30))
    out: list[str] = []
    for delta in ["我喜欢", "MyGO!!!!!", " 你听过吗？"]:
        out += segmenter.push(delta)
    out += segmenter.finish()

    joined = "".join(out)
    assert "MyGO!!!!!" in joined
    assert not any(seg == "!" for seg in out)

