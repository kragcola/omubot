from __future__ import annotations

import asyncio
from itertools import repeat

import pytest

from services.llm import segmentation
from services.llm.segmentation import natural_split


class SequenceRng:
    def __init__(self, *values: float, fallback: float = 0.0) -> None:
        self._values = list(values)
        self._fallback = fallback

    def random(self) -> float:
        if self._values:
            return self._values.pop(0)
        return self._fallback


def _rng(value: float) -> SequenceRng:
    return SequenceRng(*repeat(value, 200), fallback=value)


def test_short_text_merges_most_segments() -> None:
    segments = natural_split("好呀，没问题，我马上来。", rng=_rng(0.99))

    assert len(segments) == 1
    assert "好呀" in segments[0]
    assert "没问题" in segments[0]
    assert "我马上来" in segments[0]


def test_medium_text_can_keep_multiple_segments() -> None:
    text = "今天先把配置看一下，然后再跑一轮测试，最后确认日志。"
    segments = natural_split(text, rng=_rng(0.0))

    assert len(segments) >= 3
    assert segments[0].startswith("今天先把配置")
    assert segments[-1].startswith("最后确认")


def test_long_text_keeps_more_segments_than_short_text_with_same_rng() -> None:
    short = "第一句，第二句，第三句。"
    long = "，".join(f"第{i}段内容稍微长一点" for i in range(1, 9)) + "。"

    short_segments = natural_split(short, rng=_rng(0.8))
    long_segments = natural_split(long, rng=_rng(0.8))

    assert len(short_segments) < len(long_segments)
    assert len(long_segments) >= 3


def test_trailing_punctuation_policy_is_probabilistic() -> None:
    text = "第一句。第二句！第三句，第四句、第五句；提纲：\n第一条"
    rng = SequenceRng(
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0,  # keep all split boundaries
        0.0,  # delete period
        0.5,  # keep comma
        0.5,  # keep ideographic comma
        0.5,  # keep semicolon
    )

    segments = natural_split(text, rng=rng)

    assert segments[0] == "第一句"
    assert segments[1] == "第二句！"
    assert segments[2].endswith("，")
    assert segments[3].endswith("、")
    assert segments[4].endswith("；")
    assert "提纲：" in segments


def test_quotes_are_not_split_inside() -> None:
    text = "她说「今天很忙，但是超开心」，然后继续挥手。"
    segments = natural_split(text, rng=_rng(0.0))
    joined = "\n".join(segments)

    assert "今天很忙，但是超开心" in joined
    assert not any(segment.endswith("今天很忙，") for segment in segments)


def test_colon_is_not_a_split_boundary() -> None:
    segments = natural_split("结论：现在先别急，等一下再说。", rng=_rng(0.0))

    assert not any(segment == "结论：" for segment in segments)
    assert segments[0].startswith("结论：现在先别急")


def test_url_cq_ascii_and_kaomoji_are_preserved() -> None:
    text = "看这里 [CQ:reply,id:123] https://example.com/docs/ContextService?v=1.4.0，然后发(◕‿◕)✧。"
    segments = natural_split(text, soft_max_chars=24, rng=_rng(0.0))
    joined = "\n".join(segments)

    assert "[CQ:reply,id=123]" in joined
    assert "https://example.com/docs/ContextService?v=1.4.0" in joined
    assert "ContextService" in joined
    assert "(◕‿◕)✧" in joined
    assert not any(segment.endswith("ContextServic") for segment in segments)


def test_soft_max_recursively_splits_only_overlong_segments() -> None:
    text = "这一段没有什么标点但它真的非常非常长所以需要软拆一下避免单条消息过长"
    segments = natural_split(text, soft_max_chars=14, rng=_rng(0.0))

    assert len(segments) > 1
    assert max(len(segment) for segment in segments) <= 14
    assert "".join(segments) == text


def test_max_sentence_num_merges_tail() -> None:
    text = "".join(f"第{i}句。" for i in range(1, 11))
    segments = natural_split(text, max_sentence_num=4, rng=_rng(0.0))

    assert len(segments) == 4
    assert segments[-1].startswith("第4句")
    assert "第10句" in segments[-1]


def test_register_quiet_merges_more_than_playful() -> None:
    text = (
        "第一段内容稍微长一点！第二段内容稍微长一点！第三段内容稍微长一点！"
        "第四段内容稍微长一点！第五段内容稍微长一点！"
    )
    quiet = natural_split(text, register="quiet", rng=_rng(0.75))
    playful = natural_split(text, register="playful", rng=_rng(0.75))
    snark = natural_split(text, register="snark", rng=_rng(0.75))

    assert len(quiet) < len(playful)
    assert len(snark) == len(playful)


def test_unknown_register_matches_neutral() -> None:
    text = "第一句！第二句！第三句！第四句！"

    assert natural_split(text, register=None, rng=_rng(0.8)) == natural_split(
        text,
        register="unknown",
        rng=_rng(0.8),
    )


def test_cancel_during_recursive_split_does_not_pollute_external_state(monkeypatch) -> None:
    state: list[str] = []

    def _raise_cancel(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(segmentation, "_natural_split_overlong", _raise_cancel)

    with pytest.raises(asyncio.CancelledError):
        natural_split("这一段需要触发递归拆分因为它足够长。", soft_max_chars=8, rng=_rng(0.0))

    assert state == []


def test_ellipsis_run_is_never_split_inside() -> None:
    # `……` (two U+2026) used to break into `…` / `…`. It is one prosodic unit now.
    segments = natural_split("唔……在呢……", rng=_rng(0.0))

    assert "……" in segments[0]
    assert not any(segment == "…" for segment in segments)
    assert "".join(segments).count("…") == 4


def test_repeated_enders_stay_one_unit() -> None:
    # `！！！` must not shatter into lone `！` bubbles, nor fold to a single `！`.
    segments = natural_split("好耶！！！太棒了", rng=_rng(0.0))

    assert "好耶！！！" in segments
    assert not any(segment == "！" for segment in segments)
    assert "好耶！！！太棒了".count("！") == sum(s.count("！") for s in segments)


def test_proper_noun_with_internal_punctuation_not_split() -> None:
    # `BanG Dream! MyGO!!!!!` shattered into `BanG Dream!`/`MyGO!`/`!`/`!` before the fix.
    segments = natural_split("BanG Dream! MyGO!!!!!那个乐队的鼓手", rng=_rng(0.0))

    assert "MyGO!!!!!" in segments
    assert "BanG Dream!" in segments
    assert not any(segment == "!" for segment in segments)


def test_ascii_token_trailing_emphasis_is_kept_whole() -> None:
    segments = natural_split("cool!!! 对吧", rng=_rng(0.0))

    assert "cool!!!" in segments
    assert not any(segment == "!" for segment in segments)


def test_llm_newlines_are_hard_bubble_boundaries() -> None:
    # Even at max merge probability, LLM-authored newlines must survive as separate bubbles.
    segments = natural_split("第一条消息\n第二条消息\n第三条", rng=_rng(0.99))

    assert segments == ["第一条消息", "第二条消息", "第三条"]


def test_within_line_still_merges_under_high_strength() -> None:
    # The newline hard boundary must NOT disable intra-line probabilistic merging.
    segments = natural_split("你好呀，今天，过得怎样", rng=_rng(0.99))

    assert len(segments) == 1


def test_merge_keeps_space_between_ascii_proper_nouns() -> None:
    # Merging `BanG Dream!` + `MyGO!!!!!` must not fuse into `BanG Dream!MyGO!!!!!`.
    merged = segmentation._natural_merge_segments(
        ["BanG Dream!", "MyGO!!!!!"], split_strength=0.0, rng=_rng(0.99)
    )

    assert merged == ["BanG Dream! MyGO!!!!!"]
