from __future__ import annotations

from types import SimpleNamespace

from services.humanization.pause_extend import PauseExtend, PauseExtendConfig


def test_closed_short_reply_does_not_extend() -> None:
    decision = PauseExtend().decide("嗯。")

    assert decision.should_extend is False
    assert decision.reasons == ("too_short",)


def test_open_continuation_surface_extends() -> None:
    decision = PauseExtend().decide("我先说第一层，不过，")

    assert decision.should_extend is True
    assert "open_tail" in decision.reasons
    assert "continuation_cue" in decision.reasons


def test_question_to_user_does_not_extend() -> None:
    decision = PauseExtend().decide("这边你觉得这样可以吗？")

    assert decision.should_extend is False
    assert "asks_user" in decision.reasons


def test_user_reply_signal_blocks_extension() -> None:
    decision = PauseExtend().decide("我还有一点想补，", group_state={"user_replied": True})

    assert decision.should_extend is False
    assert decision.reasons == ("user_replied",)


def test_quiet_register_and_low_energy_suppress_borderline_extension() -> None:
    decision = PauseExtend().decide(
        "我先说第一层，不过，",
        register={"label": "quiet"},
        slot={"energy": 0.2},
    )

    assert decision.should_extend is False
    assert "register_quiet" in decision.reasons
    assert "low_slot_energy" in decision.reasons


def test_hot_group_shortens_wait_and_quiet_group_extends_wait() -> None:
    decisioner = PauseExtend()
    hot = decisioner.decide("我先说第一层，不过，", group_state={"heat": 0.95})
    quiet = decisioner.decide("我先说第一层，不过，", group_state={"heat": 0.05})

    assert hot.wait_seconds < quiet.wait_seconds
    assert "hot_group" in hot.reasons
    assert "quiet_group" in quiet.reasons


def test_config_limits_overlong_reply() -> None:
    config = PauseExtendConfig(max_reply_chars=12)
    decision = PauseExtend(config).decide("这是一段明显超过限制的回复，不过，")

    assert decision.should_extend is False
    assert decision.reasons == ("too_long",)


def test_accepts_object_like_inputs() -> None:
    register = SimpleNamespace(label="playful")
    slot = SimpleNamespace(energy=0.9)
    group_state = SimpleNamespace(rho=0.1)

    decision = PauseExtend().decide("然后还有一个小尾巴，", register=register, slot=slot, group_state=group_state)

    assert decision.should_extend is True
    assert "register_playful" in decision.reasons
    assert "high_slot_energy" in decision.reasons
    assert "quiet_group" in decision.reasons
