from __future__ import annotations

from services.llm.mention_post_processor import process_mentions
from services.name_registry import NameVariationRegistry


def _registry() -> NameVariationRegistry:
    registry = NameVariationRegistry()
    registry.update_from_event("100", 1, "小明", "")
    registry.update_from_event("100", 2, "小红", "红总")
    registry.update_from_event("100", 3, "小蓝", "")
    return registry


def test_mention_post_processor_converts_recent_speaker_name() -> None:
    reply = process_mentions("嗨 @小明 你好", "100", _registry(), bot_self_id="999")

    assert reply == "嗨 [CQ:at,qq=1] 你好"


def test_mention_post_processor_prefers_card_name() -> None:
    reply = process_mentions("@红总 看一下", "100", _registry(), bot_self_id="999")

    assert reply == "[CQ:at,qq=2] 看一下"


def test_mention_post_processor_keeps_unknown_name_literal() -> None:
    reply = process_mentions("@不存在的人 你好", "100", _registry(), bot_self_id="999")

    assert reply == "@不存在的人 你好"


def test_mention_post_processor_supports_at_all() -> None:
    reply = process_mentions("@全体成员 注意", "100", _registry(), bot_self_id="999")

    assert reply == "[CQ:at,qq=all] 注意"


def test_mention_post_processor_skips_bot_self() -> None:
    registry = _registry()
    registry.update_from_event("100", 999, "我自己", "")

    reply = process_mentions("@我自己 不要真的at自己", "100", registry, bot_self_id="999")

    assert reply == "@我自己 不要真的at自己"
