from __future__ import annotations

import pytest

from kernel.config import ReplyWorkflowConfig
from services.llm.thinker import THINKER_SYSTEM_PROMPT
from services.reply_workflow import (
    ReplyGateFeatures,
    SemanticGateResult,
    build_semantic_gate_messages,
    classify_closing_intent,
    classify_followup_text,
    evaluate_group_gate_shadow,
    evaluate_semantic_gate,
    is_shadow_mode,
    parse_semantic_gate_output,
    private_current_path_decision,
    scheduler_shadow_decision,
    semantic_gate_threshold,
    should_call_semantic_gate,
    should_consume_semantic_gate,
    workflow_mode,
)


def test_reply_workflow_config_defaults_to_shadow_only() -> None:
    cfg = ReplyWorkflowConfig()

    assert cfg.mode == "shadow"
    assert cfg.semantic_force_threshold == 0.78
    assert cfg.semantic_timeout_ms == 2200
    assert cfg.semantic_max_chars == 48
    assert cfg.directed_followup_window_s == 180.0
    assert cfg.shadow_log_private is True
    assert is_shadow_mode(cfg)
    assert workflow_mode(cfg) == "shadow"


def test_reply_workflow_rules_mode_is_legacy_shadow() -> None:
    cfg = ReplyWorkflowConfig(mode="rules")

    assert cfg.mode == "shadow"
    assert workflow_mode(cfg) == "shadow"


def test_classify_legacy_directed_followup_low_risk() -> None:
    result = classify_followup_text("我也可以吗", legacy_directed=True)

    assert result.matched is True
    assert result.kind == "legacy_directed"
    assert result.risk == "low"


def test_classify_explicit_continuation_medium_risk() -> None:
    result = classify_followup_text("继续说嘛")

    assert result.matched is True
    assert result.kind == "explicit_continuation"
    assert result.risk == "medium"


def test_classify_continuation_variants_do_not_define_behavior() -> None:
    assert classify_followup_text("继续说嘛").kind == "explicit_continuation"
    assert classify_followup_text("继续说呢").kind == "none"


def test_classify_ambiguous_continuation_high_risk() -> None:
    result = classify_followup_text("然后呢？")

    assert result.matched is True
    assert result.kind == "ambiguous_continuation"
    assert result.risk == "high"


def test_group_gate_shadow_explicit_continuation_can_force_when_context_safe() -> None:
    decision, classification = evaluate_group_gate_shadow(
        text="继续说嘛",
        has_trigger=False,
        has_recent_assistant=True,
        has_other_at=False,
        last_assistant_to_user=True,
    )

    assert classification.kind == "explicit_continuation"
    assert decision.action == "force_reply"
    assert decision.reason == "explicit_continuation_with_recent_assistant"


def test_group_gate_shadow_explicit_continuation_requires_target_context() -> None:
    decision, classification = evaluate_group_gate_shadow(
        text="继续说嘛",
        has_trigger=False,
        has_recent_assistant=True,
        has_other_at=False,
        reply_to_bot=False,
        last_assistant_to_user=False,
    )

    assert classification.kind == "explicit_continuation"
    assert decision.action == "boost"
    assert decision.reason == "explicit_continuation_missing_context_constraint"


def test_group_gate_shadow_ambiguous_continuation_does_not_force_without_constraints() -> None:
    decision, classification = evaluate_group_gate_shadow(
        text="然后呢",
        has_trigger=False,
        has_recent_assistant=True,
        has_other_at=False,
        reply_to_bot=False,
    )

    assert classification.kind == "ambiguous_continuation"
    assert decision.action == "pass"
    assert decision.reason == "ambiguous_continuation_not_safe_to_force"


def test_group_gate_shadow_current_trigger_wins() -> None:
    decision, classification = evaluate_group_gate_shadow(
        text="普通文本",
        has_trigger=True,
        trigger_mode="at_mention",
        is_addressed=True,
    )

    assert classification.kind == "none"
    assert decision.action == "force_reply"
    assert decision.reason == "current_trigger:at_mention"


def test_scheduler_shadow_decision_keeps_probability_metadata() -> None:
    decision = scheduler_shadow_decision(
        action="pass",
        reason="probability_skip",
        threshold=0.25,
        mood_mult=1.2,
        time_mult=0.7,
        msg_count=3,
        skips=2,
        trigger_mode="none",
    )

    fields = decision.log_fields()
    assert decision.action == "pass"
    assert fields["threshold"] == 0.25
    assert fields["mood_mult"] == 1.2
    assert fields["time_mult"] == 0.7
    assert fields["msg_count"] == 3
    assert fields["skips"] == 2


def test_private_current_path_decision_documents_current_behavior() -> None:
    decision = private_current_path_decision(text="继续说嘛")

    assert decision.action == "force_reply"
    assert decision.source == "private_current_path"
    assert decision.reason == "private_message_currently_enters_llm_directly"


def test_semantic_gate_candidate_requires_safe_context() -> None:
    features = ReplyGateFeatures(
        current_text="继续说呢",
        current_user_id="111",
        has_recent_assistant=True,
        last_assistant_to_user=True,
    )

    assert should_call_semantic_gate(features) == (True, "short_contextual_candidate")

    with_other_at = ReplyGateFeatures(
        current_text="继续说呢",
        current_user_id="111",
        has_recent_assistant=True,
        has_other_at=True,
        last_assistant_to_user=True,
    )
    assert should_call_semantic_gate(with_other_at) == (False, "has_other_at")

    wrong_user = ReplyGateFeatures(
        current_text="继续说呢",
        current_user_id="222",
        has_recent_assistant=True,
        last_assistant_to_user=False,
    )
    assert should_call_semantic_gate(wrong_user) == (False, "not_targeted_to_bot")


def test_semantic_gate_candidate_rejects_long_non_followup() -> None:
    features = ReplyGateFeatures(
        current_text="我今天继续看视频了然后准备打游戏顺便吃饭再看看作业到底写没写完",
        current_user_id="111",
        has_recent_assistant=True,
        last_assistant_to_user=True,
    )

    assert should_call_semantic_gate(features, max_chars=20) == (False, "too_long")


def test_parse_semantic_gate_output_direct_and_embedded() -> None:
    direct = parse_semantic_gate_output(
        '{"action":"force_reply","confidence":0.91,"intent":"continue_or_expand","reason":"要求继续"}',
    )
    assert direct is not None
    assert direct.action == "force_reply"
    assert direct.confidence == 0.91
    assert direct.intent == "continue_or_expand"

    embedded = parse_semantic_gate_output(
        '好的\n{"action":"pass","confidence":0.7,"intent":"unrelated","reason":"在说自己"}',
    )
    assert embedded is not None
    assert embedded.action == "pass"
    assert embedded.parse_mode == "embedded"


def test_parse_semantic_gate_output_invalid_fails_closed() -> None:
    assert parse_semantic_gate_output("继续说吧") is None


def test_semantic_gate_consumption_requires_high_confidence_force_reply() -> None:
    high = SemanticGateResult(
        action="force_reply",
        confidence=0.8,
        intent="continue_or_expand",
        reason="要求继续",
    )
    low = SemanticGateResult(
        action="force_reply",
        confidence=0.5,
        intent="continue_or_expand",
        reason="不确定",
    )
    passed = SemanticGateResult(
        action="pass",
        confidence=0.9,
        intent="unrelated",
        reason="在说自己",
    )

    assert should_consume_semantic_gate(high, threshold=0.78)
    assert not should_consume_semantic_gate(low, threshold=0.78)
    assert not should_consume_semantic_gate(passed, threshold=0.78)
    assert not should_consume_semantic_gate(None, threshold=0.78)


def test_build_semantic_gate_messages_uses_bounded_context() -> None:
    features = ReplyGateFeatures(
        current_text="继续说呢",
        current_user_id="111",
        has_recent_assistant=True,
        last_assistant_to_user=True,
        last_assistant_text="上一轮很长" * 100,
    )

    system, messages = build_semantic_gate_messages(features)

    assert "群聊回复 gate" in system[0]["text"]
    assert "继续说呢" in messages[0]["content"]
    assert len(messages[0]["content"]) < 600


async def test_evaluate_semantic_gate_uses_api_result() -> None:
    async def api_call(_request):
        return {
            "text": '{"action":"force_reply","confidence":0.88,'
            '"intent":"continue_or_expand","reason":"要求继续"}',
        }

    result = await evaluate_semantic_gate(
        ReplyGateFeatures(current_text="继续说呢", current_user_id="111"),
        api_call=api_call,
        timeout_ms=100,
    )

    assert result is not None
    assert result.action == "force_reply"


async def test_evaluate_semantic_gate_timeout_fails_closed() -> None:
    async def api_call(_request):
        import asyncio

        await asyncio.sleep(0.05)
        return {"text": '{"action":"force_reply","confidence":1.0}'}

    result = await evaluate_semantic_gate(
        ReplyGateFeatures(current_text="继续说呢", current_user_id="111"),
        api_call=api_call,
        timeout_ms=1,
    )

    assert result is None


# ---------------------------------------------------------------------------
# mood二轴重构回归 (2026-05-30): mood 不再决定 whether；force_reply 硬下限
# 依据 Forgas AIM / J. Pragmatics 2026 "How we are vs how we are feeling":
# mood 决定 HOW(语气/长短/礼貌)，稳定特质决定 WHETHER(接不接话)。
# ---------------------------------------------------------------------------


def test_semantic_gate_threshold_ignores_low_mood() -> None:
    """A1: 低能量心情不再抬高接话门槛——mood 已从 whether 轴撤出。"""
    th = semantic_gate_threshold(
        fixed_threshold=0.78,
        dynamic_enabled=True,
        familiarity=None,
        mood_energy=0.146,  # 凌晨 incident 的实际值，旧逻辑会 +0.05 → 0.83
    )
    assert th.effective_threshold == 0.78
    assert "mood_low:+0.05" not in th.adjustments
    # mood_energy 仍记录用于可观测性，只是不参与计算
    assert th.mood_energy == 0.146


def test_semantic_gate_threshold_still_honors_familiarity() -> None:
    """A1 防误删: 关系熟悉度(稳定特质)仍降低门槛——这是正确的 whether 接线。"""
    th = semantic_gate_threshold(
        fixed_threshold=0.78,
        dynamic_enabled=True,
        familiarity=0.7,
        mood_energy=0.146,
    )
    assert th.effective_threshold == 0.68
    assert "familiarity_high:-0.10" in th.adjustments


def test_force_reply_bypasses_dynamic_threshold_inflation() -> None:
    """C: LLM 明确 force_reply 时,门槛取 min(动态阈值, force_floor)。

    凌晨 incident: force_reply conf=0.75，旧逻辑 effective=0.83 → 被拦。
    新逻辑 floor=0.7 → consume。
    """
    incident = SemanticGateResult(
        action="force_reply",
        confidence=0.75,
        intent="continue_or_expand",
        reason="暗示让bot继续",
    )
    # 即使动态阈值被抬到 0.83，force_reply 也只看 floor=0.7
    assert should_consume_semantic_gate(incident, threshold=0.83)
    # 低于 floor 仍不放行
    weak = SemanticGateResult(
        action="force_reply",
        confidence=0.65,
        intent="continue_or_expand",
        reason="不太确定",
    )
    assert not should_consume_semantic_gate(weak, threshold=0.83)
    # 非 force_reply 即使高 confidence 也不放行
    passed = SemanticGateResult(
        action="pass",
        confidence=0.95,
        intent="unrelated",
        reason="自言自语",
    )
    assert not should_consume_semantic_gate(passed, threshold=0.5)


def test_force_floor_does_not_raise_already_low_threshold() -> None:
    """C 边界: familiarity 已把门槛降到 floor 以下时,floor 不得反向抬高。"""
    result = SemanticGateResult(
        action="force_reply",
        confidence=0.68,
        intent="continue_or_expand",
        reason="熟人接话",
    )
    # familiarity_high → effective 0.68; min(0.68, 0.7) = 0.68; 0.68 >= 0.68
    assert should_consume_semantic_gate(result, threshold=0.68)


def test_incident_full_chain_now_replies() -> None:
    """全链复刻凌晨 incident: threshold 计算 → consume 判定 == True。"""
    th = semantic_gate_threshold(
        fixed_threshold=0.78,
        dynamic_enabled=True,
        familiarity=None,
        mood_energy=0.146,
    )
    result = SemanticGateResult(
        action="force_reply",
        confidence=0.75,
        intent="continue_or_expand",
        reason="暗示让bot继续之前的对话",
    )
    assert should_consume_semantic_gate(result, threshold=th.effective_threshold)


def test_thinker_prompt_decouples_mood_from_whether() -> None:
    """A2/B1: thinker prompt 中 mood 只管 how，不再管 whether。"""
    prompt = THINKER_SYSTEM_PROMPT.format(name="测试")
    # whether 与 mood 解耦的显式声明
    assert "不影响「要不要回」" in prompt
    # AIM 反直觉特征: 低落→更礼貌更周到，而非更冷淡
    assert "更礼貌" in prompt
    # 旧的错配措辞已删除
    assert "更容易选择 wait" not in prompt


# ---------------------------------------------------------------------------
# classify_closing_intent (弱回复 P0: closing 收尾型检测)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "晚安",
    "好吧晚安",
    "好的那晚安。",
    "晚安啦~",
    "睡了",
    "我去睡了",
    "先这样",
    "明天见",
    "拜拜",
    "溜了",
    "88",
    "不聊了",
])
def test_classify_closing_intent_positive(text: str) -> None:
    assert classify_closing_intent(text) is True


@pytest.mark.parametrize("text", [
    "晚安是什么意思",          # 带疑问，非 closing
    "睡了吗",                  # 问句（你睡了吗）
    "先这样吧我觉得X方案更好一点你看呢",  # 长句带后续内容
    "今天好累",                # 无 closing token
    "你为什么不理我",          # 疑问
    "",                        # 空
    "我们来安排一下明天的事情吧今天先到这里然后",  # 超长
])
def test_classify_closing_intent_negative(text: str) -> None:
    assert classify_closing_intent(text) is False


def test_classify_closing_intent_too_long_rejected() -> None:
    # 句尾有 closing token 但整句过长 → 不算 closing。
    assert classify_closing_intent("今天真的聊了好多东西好开心那就先这样") is False


# ---------------------------------------------------------------------------
# classify_greeting_intent (弱回复: greeting 招呼型检测, mirror closing)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "早安",
    "早上好",
    "早",
    "早呀~",
    "早上好哇",
    "晚上好",
    "在吗",
    "在不在",
    "hi",
    "hello",
    "你好",
    "morning",
])
def test_classify_greeting_intent_positive(text: str) -> None:
    from services.reply_workflow import classify_greeting_intent

    assert classify_greeting_intent(text) is True


@pytest.mark.parametrize("text", [
    "早安是什么意思",          # 定义式疑问，非招呼
    "早上好为什么这么说",      # 疑问
    "今天好累",                # 无 greeting token
    "",                        # 空
    "早上好我跟你说个事情今天的安排有变要重新讨论一下",  # 超长
])
def test_classify_greeting_intent_negative(text: str) -> None:
    from services.reply_workflow import classify_greeting_intent

    assert classify_greeting_intent(text) is False
