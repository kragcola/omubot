"""C-cluster pipeline e2e: ThinkDecision topic_intent_label + schedule enum + phrase detector near-zero."""

from __future__ import annotations

import json
from typing import Any

import pytest

from plugins.schedule.types import ALLOWED_ACTIVITY_LABELS, Schedule, TimeSlot, normalize_activity_label
from services.llm.thinker import (
    _ALLOWED_TOPIC_INTENT_LABELS,
    ThinkDecision,
    _normalize_topic_intent_label,
    _parse_structured_think_output_details,
    think,
)
from services.llm.thinker_phrase_detector import detect


class TestThinkDecisionSchema:
    def test_topic_intent_label_present_and_defaults(self):
        d = ThinkDecision(action="reply", thought="test")
        assert d.topic_intent_label == "闲聊"

    def test_all_allowed_labels_accepted(self):
        for label in _ALLOWED_TOPIC_INTENT_LABELS:
            assert _normalize_topic_intent_label(label) == label

    def test_unknown_label_falls_back(self):
        assert _normalize_topic_intent_label("不存在的标签") == "闲聊"
        assert _normalize_topic_intent_label("") == "闲聊"
        assert _normalize_topic_intent_label(None) == "闲聊"


class TestThinkerBlockEnumInjection:
    """Verify thinker_block injects enum labels, not free-text thought."""

    def test_new_schema_parses_topic_intent_label(self):
        raw = json.dumps({
            "action": "reply",
            "topic_intent_label": "关心",
            "retrieve_mode": "skip",
            "rewritten_query": "",
            "thought": "对方似乎心情不好",
            "sticker": False,
            "tone": "安慰",
        })
        decision, mode = _parse_structured_think_output_details(raw)
        assert decision is not None
        assert decision.topic_intent_label == "关心"
        assert decision.tone == "安慰"
        assert decision.thought == "对方似乎心情不好"
        assert mode in {"direct", "fenced", "embedded"}

    def test_missing_topic_intent_label_defaults(self):
        raw = json.dumps({
            "action": "reply",
            "thought": "随便聊聊",
            "sticker": False,
            "tone": "日常",
        })
        decision, _ = _parse_structured_think_output_details(raw)
        assert decision is not None
        assert decision.topic_intent_label == "闲聊"


class TestPhraseDetectorNearZero:
    """With enum-only thinker_block, phrase detector should never fire."""

    def test_enum_block_does_not_trigger_phrase_detector(self):
        thinker_block_text = "【意图：关心】【tone: 安慰】【sticker: no】"
        reply = "你还好吗？最近是不是压力比较大，有什么我能帮忙的吗？"
        result = detect(reply, thinker_block_text)
        assert result.hit is False

    def test_thought_text_not_in_block_means_no_parrot(self):
        thought = "对方似乎心情不好，我应该关心一下"
        reply = "对方似乎心情不好，我应该关心一下，你还好吗？"
        result_with_thought = detect(reply, thought)
        enum_block = "【意图：关心】【tone: 安慰】【sticker: no】"
        result_with_enum = detect(reply, enum_block)
        assert result_with_enum.hit is False
        assert result_with_thought.hit is True

    def test_all_enum_labels_safe_from_detector(self):
        for label in _ALLOWED_TOPIC_INTENT_LABELS:
            block = f"【意图：{label}】【tone: 日常】【sticker: no】"
            reply = "今天天气真好，我们出去走走吧！"
            result = detect(reply, block)
            assert result.hit is False, f"label={label} triggered detector"


class TestRetryOnParseFail:
    """Verify retry-once + heuristic fallback chain."""

    @pytest.mark.asyncio
    async def test_first_fail_retry_succeeds(self):
        call_count = 0

        async def mock_api(req: Any) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "content": [{"type": "text", "text": "这不是有效JSON"}],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }
            return {
                "content": [{"type": "text", "text": json.dumps({
                    "action": "reply",
                    "topic_intent_label": "打趣",
                    "thought": "retry ok",
                    "sticker": False,
                    "tone": "元气",
                })}],
                "usage": {"input_tokens": 10, "output_tokens": 10},
            }

        decision = await think(
            api_call=mock_api,
            recent_messages=[{"role": "user", "content": "hello"}],
        )
        assert call_count == 2
        assert decision.action == "reply"
        assert decision.topic_intent_label == "打趣"

    @pytest.mark.asyncio
    async def test_both_fail_falls_to_heuristic(self):
        async def mock_api(req: Any) -> dict:
            return {
                "content": [{"type": "text", "text": "我觉得应该回复他"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }

        decision = await think(
            api_call=mock_api,
            recent_messages=[{"role": "user", "content": "hello"}],
        )
        assert decision.action == "reply"
        assert decision.topic_intent_label == "闲聊"


class TestScheduleActivityEnum:
    """Verify schedule activity enum normalization and legacy invalidation."""

    def test_all_allowed_labels_pass_normalization(self):
        for label in ALLOWED_ACTIVITY_LABELS:
            assert normalize_activity_label(label) == label

    def test_unknown_activity_returns_empty(self):
        assert normalize_activity_label("被闹钟吵醒，迷迷糊糊地关掉") == ""
        assert normalize_activity_label("") == ""
        assert normalize_activity_label(None) == ""

    def test_schedule_with_enum_activity_roundtrips(self):
        schedule = Schedule(
            date="2026-05-27",
            day_narrative="test",
            slots=[
                TimeSlot(time="08:00", activity="rest", mood_hint="困倦", description="赖在床上刷手机"),
                TimeSlot(time="12:00", activity="meal", mood_hint="放松", description="食堂吃午饭"),
            ],
        )
        assert schedule.slots[0].activity == "rest"
        assert schedule.slots[0].description == "赖在床上刷手机"
        assert schedule.slots[1].activity == "meal"

    def test_description_used_for_display(self):
        slot = TimeSlot(time="08:00", activity="rest", mood_hint="困倦", description="赖在床上刷手机")
        display = slot.description or slot.activity
        assert display == "赖在床上刷手机"

    def test_description_fallback_to_activity(self):
        slot = TimeSlot(time="08:00", activity="rest", mood_hint="困倦")
        display = slot.description or slot.activity
        assert display == "rest"


class TestCancelPaths:
    """Cancel-path: thinker call cancelled -> heuristic fallback, no dirty state."""

    @pytest.mark.asyncio
    async def test_thinker_api_exception_returns_safe_default(self):
        async def mock_api(req: Any) -> dict:
            raise RuntimeError("simulated cancel")

        decision = await think(
            api_call=mock_api,
            recent_messages=[{"role": "user", "content": "hello"}],
        )
        assert decision.action == "reply"
        assert decision.topic_intent_label == "闲聊"
        assert decision.thought == ""

