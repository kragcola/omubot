import pytest

from services.persona.willingness import Willingness, episodic_situation_lookup, willingness_stage


def test_willingness_stage_stranger_cold_start() -> None:
    result = willingness_stage(interaction_count=0, register_consistency=0.1)

    assert result == Willingness("stranger", 0.7, "cold start with little register evidence")


def test_willingness_stage_acquaint_default() -> None:
    result = willingness_stage(interaction_count=3, register_consistency=0.45)

    assert result.stage == "acquaint"
    assert result.to_state_value()["willingness_stage"] == "acquaint"


def test_willingness_stage_familiar_boundary() -> None:
    result = willingness_stage(interaction_count=8, register_consistency=0.55, recent_reply_delay_s=120)

    assert result.stage == "familiar"


def test_willingness_stage_close_boundary() -> None:
    result = willingness_stage(interaction_count=20, register_consistency=0.75, recent_reply_delay_s=45)

    assert result.stage == "close"


def test_willingness_stage_withdraw_from_silence() -> None:
    result = willingness_stage(interaction_count=20, register_consistency=0.9, consecutive_no_reply=3)

    assert result.stage == "withdraw"


def test_willingness_stage_withdraw_from_delay() -> None:
    result = willingness_stage(interaction_count=2, register_consistency=0.9, recent_reply_delay_s=300)

    assert result.stage == "withdraw"


def test_willingness_stage_shifts_toward_withdraw_on_negative_outcomes() -> None:
    result = willingness_stage(
        interaction_count=8,
        register_consistency=0.7,
        recent_reply_delay_s=60,
        recent_outcomes=["用户拒绝继续聊", "后来开始沉默", "最后有点尴尬"],
    )

    assert result.stage == "acquaint"
    assert "episodic_negative_bias" in result.reason


def test_willingness_stage_shifts_toward_close_on_positive_outcomes() -> None:
    result = willingness_stage(
        interaction_count=3,
        register_consistency=0.45,
        recent_outcomes=["用户后来愿意继续接话", "气氛变好了", "最后大家都笑了"],
    )

    assert result.stage == "familiar"
    assert "episodic_positive_bias" in result.reason


@pytest.mark.asyncio
async def test_episodic_situation_lookup_returns_similar_top3() -> None:
    class _Episode:
        def __init__(self, situation: str, outcome_signal: str, observed_context: str = "") -> None:
            self.situation = situation
            self.outcome_signal = outcome_signal
            self.observed_context = observed_context

    class _Store:
        async def list_for_recall(self, **_kwargs):
            return [
                _Episode("用户在群里提到音游比赛", "用户后来愿意继续聊"),
                _Episode("完全不相关的话题", "用户拒绝"),
                _Episode("大家在聊音游和比赛安排", "气氛很好"),
                _Episode("音游比赛复盘", ""),
            ]

    matched = await episodic_situation_lookup(_Store(), "g1", "今天在说音游比赛")

    assert len(matched) >= 1
    assert all(item.outcome_signal for item in matched)
