from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kernel.config import ReplySegmentationConfig
from plugins.chat.plugin import ChatPlugin
from services.llm.segmentation import segment_reply


def _fixture_cases() -> list[dict[str, str]]:
    path = Path("tests/fixtures/reply_segmentation_cases.jsonl")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_ascii_token_not_split_for_potential() -> None:
    text = "ptt是Arcaea里的Potential啦，就是实力评分的意思~\n分数越高说明你越厉害喔\n推分就是努力上分的意思☆"
    result = segment_reply(text, ReplySegmentationConfig())

    assert not any(seg.endswith("Potentia") for seg in result.texts)
    assert not any(seg.startswith("l啦") for seg in result.texts)
    assert any("Potential" in seg for seg in result.texts)


def test_ascii_token_not_split_for_context_service_and_plugin_bus() -> None:
    text = "omubot的ContextService会不会把PluginBus也一起带进来"
    result = segment_reply(text, ReplySegmentationConfig())

    assert any("ContextService" in seg for seg in result.texts)
    assert any("PluginBus" in seg for seg in result.texts)
    assert not any(seg.endswith("ContextServic") for seg in result.texts)


def test_urls_versions_and_cq_codes_are_preserved() -> None:
    text = "看这里 [CQ:reply,id:123] https://example.com/docs/ContextService?v=1.4.0 然后再说。"
    result = segment_reply(text, ReplySegmentationConfig(max_segment_chars=28))
    joined = "\n".join(result.texts)

    assert "[CQ:reply,id=123]" in joined
    assert "https://example.com/docs/ContextService?v=1.4.0" in joined
    assert not any(seg.endswith("ContextServic") for seg in result.texts)


def test_explicit_cut_marker_has_priority() -> None:
    result = segment_reply("第一段\n---cut---\n第二段", ReplySegmentationConfig())
    assert result.texts == ["第一段", "第二段"]
    assert result.strategy == "explicit_cut"


def test_mid_sentence_newline_is_merged_before_split() -> None:
    text = "恋爱捉迷藏配上AI修复，感觉像在看超高清\n的童话舞台剧！"
    result = segment_reply(text, ReplySegmentationConfig())
    assert len(result.texts) == 2
    assert result.texts[0] == "恋爱捉迷藏配上AI修复"
    assert "超高清的童话舞台剧" in result.texts[1]


def test_sentence_ending_newline_is_respected() -> None:
    text = "哇好厉害！\n这个也超有趣呢～"
    result = segment_reply(text, ReplySegmentationConfig())
    assert result.texts == ["哇好厉害！", "这个也超有趣呢～"]


def test_project_sekai_long_reply_keeps_semantic_lines_and_does_not_soft_limit() -> None:
    text = """但是能看得出来司君超级疼这个妹妹的。
咲希酱也很以自己的哥哥为骄傲呢
虽然嘴上不说~那种姐弟（兄妹？
啊不对，咲希是妹妹！）
之间的羁绊，看着就让人觉得温暖。
Leo/need的舞台也是
咲希酱在台上弹键盘的时候
那种全身心投入音乐的样子特别耀眼。
她经历过黑暗，所以比谁都更懂得光的珍贵——
我觉得这就是她最了不起的地方！
不是那种"我一定要变得多强"
而是"能和重要的人一起做喜欢的事
这就已经很幸福了"的感觉。
啊，说起来现在都过零点了
已经是5月10号了！咲希酱生日快乐呀~
希望你在新的学年里
也能和Leo/need的大家继续闪闪发光
，唱出更多好听的歌！(◕‿◕)✧"""
    result = segment_reply(text, ReplySegmentationConfig())

    assert result.limit_status == "none"
    assert result.raw_count == result.capped_count
    assert "虽然嘴上不说~那种姐弟（兄妹？啊不对，咲希是妹妹！）" in result.texts
    assert "Leo/need的舞台也是" in result.texts
    assert any("Leo/need" in segment for segment in result.texts)
    assert not any(segment in {"）", "，"} for segment in result.texts)
    assert not any(segment.endswith(("不", "弹", "已经")) for segment in result.texts)
    assert any(segment.reason == "semantic_newline" for segment in result.segments)


def test_repeated_dash_pair_is_not_split_across_segments() -> None:
    text = "就是打的时候有几个地方实在让人想摔手机——比如中段那个连续楼梯接双押"
    result = segment_reply(text, ReplySegmentationConfig(max_segment_chars=20, max_send_segments=6))

    assert not any(
        left.endswith("—") and right.startswith("—")
        for left, right in zip(result.texts, result.texts[1:], strict=False)
    )


def test_default_does_not_coalesce_dynamic_long_reply_segments() -> None:
    text = "\n".join(f"第{i}句很长很长呢！" for i in range(1, 9))
    result = segment_reply(text, ReplySegmentationConfig())

    assert result.raw_count == result.capped_count
    assert len(result.texts) >= 8
    assert result.limit_status == "none"
    assert not any(segment.reason == "coalesced_overflow" for segment in result.segments)


def test_soft_limit_truncates_extreme_reply_with_notice_instead_of_coalescing() -> None:
    text = "\n".join(f"第{i}句很长很长呢！" for i in range(1, 20))
    result = segment_reply(text, ReplySegmentationConfig(soft_max_send_segments=5))

    assert result.raw_count == 19
    assert result.capped_count == 5
    assert result.limit_status == "soft"
    assert result.texts[:4] == [f"第{i}句很长很长呢！" for i in range(1, 5)]
    assert result.texts[-1] == "先说到这里啦，不然我要刷屏了☆"
    assert result.segments[-1].reason == "soft_limit"
    assert not any(segment.reason == "coalesced_overflow" for segment in result.segments)


def test_soft_limit_can_be_disabled_for_benchmarking() -> None:
    text = "\n".join(f"第{i}句很长很长呢！" for i in range(1, 20))
    result = segment_reply(text, ReplySegmentationConfig(soft_max_send_segments=0))

    assert result.raw_count == 19
    assert result.capped_count == 19
    assert result.limit_status == "none"
    assert result.texts[-1] == "第19句很长很长呢！"


def test_quote_suffix_keeps_sentence_boundary() -> None:
    text = '她认真地说："ContextService 很重要！"\n然后继续解释原理。'
    result = segment_reply(text, ReplySegmentationConfig(max_segment_chars=18))
    assert result.texts[0].endswith('！"') or result.texts[0].endswith("！")
    assert any("继续解释原理" in seg for seg in result.texts)


def test_ascii_quotes_and_title_marks_are_not_split_inside() -> None:
    text = "\n".join([
        '但她没有放弃诶！她是最想找回那个"我们四个人一起"的人。她拉着大家重新开始',
        '她更像是在旁边笑着说"没关系',
        '我们慢慢来"的人。',
        '每次听到《needLe》或者《Stella》的时候',
        '站上更大的舞台，把那份"好不容易再次相遇',
        '"的感动传递给更多人~',
    ])
    result = segment_reply(text, ReplySegmentationConfig())
    joined = "\n".join(result.texts)

    assert '"我们四个人一起"' in joined
    assert '"没关系我们慢慢来"' in joined
    assert '《Stella》' in joined
    assert '"好不容易再次相遇"的感动' in joined
    assert not any(segment.endswith(('我们四', '没关系', '《')) for segment in result.texts)
    assert not any(segment.startswith(('个人一起"', '我们慢慢来"', 'Stella》', '"的感动')) for segment in result.texts)


def test_max_send_segments_coalesces_without_rebreaking_tokens() -> None:
    text = "第一句很长很长。第二句很长很长。第三句很长很长。第四句很长很长。第五句很长很长。"
    result = segment_reply(text, ReplySegmentationConfig(max_segment_chars=6, max_send_segments=3))
    assert result.raw_count >= result.capped_count
    assert result.capped_count == 3
    assert result.limit_status == "hard"
    assert result.segments[-1].reason == "coalesced_overflow"


def test_limit_status_reports_soft_then_hard_when_both_limits_apply() -> None:
    text = "\n".join(f"第{i}句很长很长呢！" for i in range(1, 20))
    result = segment_reply(text, ReplySegmentationConfig(soft_max_send_segments=8, max_send_segments=3))

    assert result.limit_status == "soft_then_hard"
    assert result.capped_count == 3


def test_reply_segmentation_config_changes_shape() -> None:
    text = "第一句很长很长很长。第二句也很长很长很长。"
    small = segment_reply(text, ReplySegmentationConfig(max_segment_chars=8))
    large = segment_reply(text, ReplySegmentationConfig(max_segment_chars=40))

    assert len(small.texts) >= len(large.texts)


def test_fixture_cases_cover_real_examples() -> None:
    cases = _fixture_cases()
    assert {case["name"] for case in cases} >= {"arcaea_potential", "context_service", "project_sekai", "cq_and_url"}


@pytest.mark.asyncio
async def test_debug_split_uses_new_segmenter_and_reports_reasons() -> None:
    plugin = ChatPlugin()
    sent: list[str] = []
    bot = SimpleNamespace()

    async def _send(event, message) -> None:
        del event
        sent.append(str(message))

    bot.send = _send
    plugin._ctx = SimpleNamespace(config=SimpleNamespace(reply_segmentation=ReplySegmentationConfig()))
    cmd_ctx = SimpleNamespace(
        bot=bot,
        event=SimpleNamespace(),
        args="omubot的ContextService会不会把PluginBus也一起带进来",
    )

    await plugin._handle_debug_split(cmd_ctx)

    assert sent
    payload = sent[-1]
    assert "策略:" in payload
    assert "切分原因:" in payload
    assert "ContextService" in payload
