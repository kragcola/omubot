from services.persona.willingness import Willingness, willingness_stage


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
