from __future__ import annotations

import pytest

from services.llm.llm_request import LLMRequest
from services.llm.thinker import THINKER_SYSTEM_PROMPT, build_thinker_time_text, parse_think_output, think


def test_parse_think_output_accepts_plain_json() -> None:
    decision = parse_think_output('{"action":"reply","thought":"直接回答","sticker":true,"tone":"认真"}')

    assert decision is not None
    assert decision.action == "reply"
    assert decision.thought == "直接回答"
    assert decision.sticker is True
    assert decision.tone == "认真"


def test_parse_think_output_accepts_fenced_json() -> None:
    decision = parse_think_output(
        '```json\n{"action":"wait","thought":"先等一下","sticker":false,"tone":"日常"}\n```'
    )

    assert decision is not None
    assert decision.action == "wait"
    assert decision.thought == "先等一下"
    assert decision.sticker is False
    assert decision.tone == "日常"


def test_parse_think_output_recovers_embedded_json() -> None:
    decision = parse_think_output(
        '我先这样判断：'
        '{"action":"reply","retrieve_mode":"doc","thought":"查一下今天日期",'
        '"sticker":"0","tone":"认真"}'
        ' 然后再说。'
    )

    assert decision is not None
    assert decision.action == "reply"
    assert decision.retrieve_mode == "doc"
    assert decision.thought == "查一下今天日期"
    assert decision.sticker is False
    assert decision.tone == "认真"


def test_parse_think_output_invalid_action_falls_back_to_reply() -> None:
    """PR5: 'search' is no longer valid; thinker must coerce to 'reply'."""
    decision = parse_think_output(
        '{"action":"search","thought":"想查","sticker":false,"tone":"日常"}'
    )

    assert decision is not None
    assert decision.action == "reply"
    assert decision.retrieve_mode == "hybrid"  # default when not specified


def test_parse_think_output_wait_forces_skip_mode() -> None:
    """PR5: action=wait must override retrieve_mode to 'skip'."""
    decision = parse_think_output(
        '{"action":"wait","retrieve_mode":"hybrid","thought":"等一下","sticker":false,"tone":"日常"}'
    )

    assert decision is not None
    assert decision.action == "wait"
    assert decision.retrieve_mode == "skip"


def test_parse_think_output_invalid_mode_falls_back_to_hybrid() -> None:
    decision = parse_think_output(
        '{"action":"reply","retrieve_mode":"banana","thought":"测试","sticker":false,"tone":"日常"}'
    )

    assert decision is not None
    assert decision.retrieve_mode == "hybrid"


def test_parse_think_output_accepts_doc_mode() -> None:
    decision = parse_think_output(
        '{"action":"reply","retrieve_mode":"doc","thought":"查文档","sticker":false,"tone":"认真"}'
    )

    assert decision is not None
    assert decision.action == "reply"
    assert decision.retrieve_mode == "doc"


def test_parse_think_output_accepts_fact_mode() -> None:
    decision = parse_think_output(
        '{"action":"reply","retrieve_mode":"fact","thought":"查记忆","sticker":false,"tone":"日常"}'
    )

    assert decision is not None
    assert decision.action == "reply"
    assert decision.retrieve_mode == "fact"


def test_parse_think_output_accepts_skip_mode() -> None:
    decision = parse_think_output(
        '{"action":"reply","retrieve_mode":"skip","thought":"闲聊不查","sticker":true,"tone":"元气"}'
    )

    assert decision is not None
    assert decision.action == "reply"
    assert decision.retrieve_mode == "skip"


def test_parse_think_output_accepts_rewritten_query() -> None:
    decision = parse_think_output(
        '{"action":"reply","retrieve_mode":"doc","rewritten_query":'
        '"Claude API tools 字段支持哪些参数",'
        '"thought":"查 API","sticker":false,"tone":"认真"}'
    )

    assert decision is not None
    assert decision.rewritten_query == "Claude API tools 字段支持哪些参数"


def test_parse_think_output_missing_rewritten_query_defaults_empty() -> None:
    decision = parse_think_output(
        '{"action":"reply","retrieve_mode":"doc","thought":"查","sticker":false,"tone":"日常"}'
    )

    assert decision is not None
    assert decision.rewritten_query == ""


def test_parse_think_output_caps_rewritten_query_at_160_chars() -> None:
    long_query = "A" * 300
    decision = parse_think_output(
        '{"action":"reply","retrieve_mode":"hybrid","rewritten_query":"'
        + long_query
        + '","thought":"测","sticker":false,"tone":"日常"}'
    )

    assert decision is not None
    assert len(decision.rewritten_query) == 160
    assert decision.rewritten_query == "A" * 160


def test_parse_think_output_wait_clears_rewritten_query() -> None:
    decision = parse_think_output(
        '{"action":"wait","retrieve_mode":"hybrid","rewritten_query":"留作 fallback",'
        '"thought":"等等","sticker":false,"tone":"日常"}'
    )

    assert decision is not None
    assert decision.action == "wait"
    assert decision.retrieve_mode == "skip"
    assert decision.rewritten_query == ""


def test_parse_think_output_skip_mode_clears_rewritten_query() -> None:
    decision = parse_think_output(
        '{"action":"reply","retrieve_mode":"skip","rewritten_query":"应该被清空",'
        '"thought":"闲聊","sticker":false,"tone":"日常"}'
    )

    assert decision is not None
    assert decision.retrieve_mode == "skip"
    assert decision.rewritten_query == ""


def test_parse_think_output_uses_heuristic_reply_fallback() -> None:
    decision = parse_think_output("哇这个话题我有话想接，简单回一下就好。")

    assert decision is not None
    assert decision.action == "reply"
    assert "parse error" not in decision.thought
    assert decision.thought
    assert decision.sticker is False
    assert decision.tone == "日常"


def test_parse_think_output_uses_heuristic_wait_fallback() -> None:
    decision = parse_think_output("这段我先不回，等一下再说。")

    assert decision is not None
    assert decision.action == "wait"
    assert "先不回" in decision.thought or "等一下" in decision.thought


def test_parse_think_output_empty_text_returns_none() -> None:
    assert parse_think_output("   ") is None


def test_build_thinker_time_text_includes_day_flags_and_slot() -> None:
    text = build_thinker_time_text({
        "date": "2026-10-01",
        "hour": 23,
        "minute": 5,
        "weekday_cn": "周四",
        "is_weekend": False,
        "is_holiday": True,
        "slot_time": "22:30",
        "slot_activity": "睡前刷题",
        "slot_mood_hint": "安静",
    })

    assert "2026-10-01 23:05 周四" in text
    assert "节假日" in text
    assert "22:30 睡前刷题" in text
    assert "安静" in text


@pytest.mark.asyncio
async def test_think_with_slang_hint_includes_hint_in_dynamic_blocks() -> None:
    captured_request: list[LLMRequest] = []

    async def mock_api_call(req: LLMRequest) -> dict:
        captured_request.append(req)
        return {
            "content": [{"type": "text", "text": '{"action":"reply","thought":"test","sticker":false,"tone":"日常"}'}],
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }

    hint = "[黑话命中] 对话中出现了以下群内黑话：摸鱼=偷懒不干活"
    await think(
        api_call=mock_api_call,
        recent_messages=[{"role": "user", "content": "今天又摸鱼了"}],
        slang_hint=hint,
    )

    assert len(captured_request) == 1
    assert hint in captured_request[0].dynamic_blocks


@pytest.mark.asyncio
async def test_think_with_time_text_prepends_runtime_clock_block() -> None:
    captured_request: list[LLMRequest] = []

    async def mock_api_call(req: LLMRequest) -> dict:
        captured_request.append(req)
        return {
            "content": [{"type": "text", "text": '{"action":"reply","thought":"test","sticker":false,"tone":"日常"}'}],
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }

    time_text = "【当前时间】2026-05-25 02:30 周一（工作日）"
    await think(
        api_call=mock_api_call,
        recent_messages=[{"role": "user", "content": "hello"}],
        time_text=time_text,
        mood_text="【当前心情】放松",
    )

    assert len(captured_request) == 1
    assert captured_request[0].dynamic_blocks[:2] == [time_text, "【当前心情】放松"]


@pytest.mark.asyncio
async def test_think_without_slang_hint_has_no_extra_dynamic_block() -> None:
    captured_request: list[LLMRequest] = []

    async def mock_api_call(req: LLMRequest) -> dict:
        captured_request.append(req)
        return {
            "content": [{"type": "text", "text": '{"action":"reply","thought":"test","sticker":false,"tone":"日常"}'}],
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }

    await think(
        api_call=mock_api_call,
        recent_messages=[{"role": "user", "content": "hello"}],
    )

    assert len(captured_request) == 1
    for block in captured_request[0].dynamic_blocks:
        text = block if isinstance(block, str) else block.get("text", "")
        assert "黑话命中" not in text


def test_thinker_system_prompt_clears_deepseek_cache_threshold() -> None:
    """方案 D.1 — DeepSeek 词级前缀缓存最小可缓存前缀 1024 token。

    THINKER_SYSTEM_PROMPT 之前 ~1229 token 紧贴边界，命中率在 ~30% 浮动。
    本测试守住静态系统块至少 1300 token（CJK 1:1 + ASCII 1:0.3 估算），
    把缓存前缀稳定推过门槛留 ~280 token 安全余量。下次有人改 prompt 时
    pytest 失败兜底，避免静默回退到 30% 命中。

    详见 services/slang/shared_prefix.py 注释 1-15 行说明的 1024-token
    门槛事实，以及 maintenance-log.md 方案 D 条目。
    """
    text = THINKER_SYSTEM_PROMPT
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    ascii_chars = len(text) - cjk
    estimated_tokens = cjk + ascii_chars // 3
    assert estimated_tokens >= 1300, (
        f"thinker static system prompt is {estimated_tokens} tokens, "
        f"below the 1300 lower bound chosen to clear DeepSeek's 1024 cache "
        f"threshold with margin. Adding new sections is fine; trimming below "
        f"this floor will silently regress cache hit rate."
    )


# ---------------------------------------------------------------------------
# light_reply / light_kind (弱回复 P0)
# ---------------------------------------------------------------------------


def test_parse_think_output_closing_light_reply() -> None:
    decision = parse_think_output(
        '{"action":"light_reply","light_kind":"closing","thought":"对方道晚安","tone":"日常"}'
    )
    assert decision is not None
    assert decision.action == "light_reply"
    assert decision.light_kind == "closing"
    # A light reply skips retrieval (no information payload).
    assert decision.retrieve_mode == "skip"


def test_parse_think_output_light_reply_defaults_to_companion() -> None:
    decision = parse_think_output('{"action":"light_reply","thought":"轻轻应一声"}')
    assert decision is not None
    assert decision.light_kind == "companion"


def test_parse_think_output_light_kind_cleared_for_non_light() -> None:
    # light_kind only meaningful on light_reply; a stray value on reply is dropped.
    decision = parse_think_output('{"action":"reply","light_kind":"closing","thought":"正常回复"}')
    assert decision is not None
    assert decision.action == "reply"
    assert decision.light_kind == ""


def test_parse_think_output_invalid_light_kind_falls_back_empty() -> None:
    decision = parse_think_output('{"action":"light_reply","light_kind":"nonsense","thought":"x"}')
    assert decision is not None
    # invalid light_kind normalized to "" then defaulted to companion for light_reply
    assert decision.light_kind == "companion"


def test_parse_think_output_missing_light_kind_defaults_empty() -> None:
    # Old JSON without light_kind still parses; non-light actions keep "".
    decision = parse_think_output('{"action":"reply","thought":"普通"}')
    assert decision is not None
    assert decision.light_kind == ""


def test_thinker_prompt_mentions_closing_light_reply() -> None:
    prompt = THINKER_SYSTEM_PROMPT.format(name="测试")
    assert "light_reply" in prompt
    assert "closing" in prompt
    assert "收尾" in prompt


# ---------------------------------------------------------------------------
# reply_necessity (B3 — suppress 'showing off')
# ---------------------------------------------------------------------------


def test_parse_think_output_accepts_reply_necessity() -> None:
    for value in ("high", "medium", "low"):
        decision = parse_think_output(
            f'{{"action":"reply","thought":"x","reply_necessity":"{value}"}}'
        )
        assert decision is not None
        assert decision.reply_necessity == value


def test_parse_think_output_invalid_necessity_falls_back_high() -> None:
    decision = parse_think_output('{"action":"reply","thought":"x","reply_necessity":"nonsense"}')
    assert decision is not None
    assert decision.reply_necessity == "high"  # safe default = do not suppress


def test_parse_think_output_missing_necessity_defaults_high() -> None:
    decision = parse_think_output('{"action":"reply","thought":"x"}')
    assert decision is not None
    assert decision.reply_necessity == "high"


def test_think_decision_default_necessity_is_high() -> None:
    from services.llm.thinker import ThinkDecision

    assert ThinkDecision(action="reply", thought="x").reply_necessity == "high"


def test_thinker_prompt_mentions_reply_necessity() -> None:
    prompt = THINKER_SYSTEM_PROMPT.format(name="测试")
    assert "reply_necessity" in prompt
    assert "刷存在感" in prompt
