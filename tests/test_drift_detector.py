from __future__ import annotations

import pytest

from services.llm.drift_detector import DriftDetector


def _detector(
    *,
    enabled: bool = True,
    lambda_: float = 0.5,
    theta_repair: float = 0.6,
    theta_block: float = 0.85,
    repair_max_retries: int = 1,
) -> DriftDetector:
    return DriftDetector(
        bot_name="凤笑梦",
        personality="元气，反应快，有一点调皮，群里陪大家聊天。",
        voice_text="短句优先。别太正式。保持自然接话。",
        examples_text=(
            "正例：用户：你是谁？ / 回复：我是凤笑梦呀，在群里陪你们聊天的。\n"
            "反例：作为凤笑梦，根据我的设定…… -> 别这么正式啦，我直接说就好。"
        ),
        lambda_=lambda_,
        theta_repair=theta_repair,
        theta_block=theta_block,
        repair_max_retries=repair_max_retries,
        enabled=enabled,
    )


def test_evaluate_passes_for_close_style_reply() -> None:
    detector = _detector()

    score = detector.evaluate("别这么正式啦，我直接接一句就好。", group_id="100")

    assert score.action == "pass"
    assert score.ewma < 0.6


def test_evaluate_stays_below_repair_for_mild_drift() -> None:
    detector = _detector(lambda_=0.3, theta_repair=0.75, theta_block=0.95)

    first = detector.evaluate("我先认真解释一下，不过还是尽量说短一点。", group_id="100")
    second = detector.evaluate("这句稍微正式一点，但还算在聊天里。", group_id="100")

    assert first.action == "pass"
    assert second.action == "pass"
    assert second.ewma < 0.75


def test_evaluate_repairs_for_severe_persona_drift() -> None:
    detector = _detector(theta_repair=0.6, theta_block=0.95)

    score = detector.evaluate("我是凤笑梦，WxS 的成员，现在向你说明设定。", group_id="100")

    assert score.action == "repair"
    assert score.ewma >= 0.6


def test_evaluate_blocks_for_extreme_drift() -> None:
    detector = _detector(theta_repair=0.6, theta_block=0.85)

    score = detector.evaluate("我是 AI 语言模型 Claude，由 Anthropic 提供支持。", group_id="100")

    assert score.action == "block"
    assert score.ewma >= 0.85


def test_ewma_recovers_after_normal_replies() -> None:
    detector = _detector(lambda_=0.5, theta_repair=0.6, theta_block=0.95)

    severe = detector.evaluate("我是凤笑梦，WxS 的成员，现在向你说明设定。", group_id="100")
    recovered_1 = detector.evaluate("好啦别绕这么远，我直接接这个。", group_id="100")
    recovered_2 = detector.evaluate("这个点子可以，我顺着说一句。", group_id="100")

    assert severe.action == "repair"
    assert recovered_1.ewma < severe.ewma
    assert recovered_2.ewma < recovered_1.ewma


@pytest.mark.asyncio
async def test_evaluate_cancel_safe_does_not_update_state_on_cancel() -> None:
    detector = _detector()
    task = pytest.MonkeyPatch()
    task.undo()
    coro = detector.evaluate_cancel_safe("我是凤笑梦，WxS 的成员。", group_id="100")
    pending = __import__("asyncio").create_task(coro)
    await __import__("asyncio").sleep(0)
    pending.cancel()
    with pytest.raises(__import__("asyncio").CancelledError):
        await pending

    follow_up = detector.evaluate("好啦我直接说重点。", group_id="100")
    assert follow_up.action == "pass"
    assert follow_up.ewma == follow_up.raw


def test_disabled_detector_always_passes() -> None:
    detector = _detector(enabled=False)

    score = detector.evaluate("我是 AI 语言模型 Claude。", group_id="100")

    assert score.action == "pass"
    assert score.raw == 0.0
    assert score.ewma == 0.0


def test_build_repair_instruction_keeps_identity_hint() -> None:
    detector = _detector()

    instruction = detector.build_repair_instruction("作为凤笑梦，根据我的设定……")

    assert "凤笑梦" in instruction
    assert "不要自我介绍" in instruction
    assert "原回复" in instruction
