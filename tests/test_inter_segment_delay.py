from __future__ import annotations

from kernel.config import ReplySegmentationConfig as KernelReplySegmentationConfig
from services.llm.segmentation import ReplySegmentationConfig, inter_segment_delay


def test_empty_segment_uses_lower_bound() -> None:
    assert inter_segment_delay("") == 0.5


def test_quiet_register_extends_delay() -> None:
    neutral = inter_segment_delay("这段文字刚好有一点长度", register="neutral_default")
    quiet = inter_segment_delay("这段文字刚好有一点长度", register="quiet")
    polite = inter_segment_delay("这段文字刚好有一点长度", register="polite_distant")

    assert quiet > neutral
    assert polite > quiet


def test_playful_and_snark_shorten_delay() -> None:
    neutral = inter_segment_delay("这段文字刚好有一点长度", register="neutral_default")
    playful = inter_segment_delay("这段文字刚好有一点长度", register="playful")
    snark = inter_segment_delay("这段文字刚好有一点长度", register="snark")

    assert playful < neutral
    assert snark < playful


def test_long_segment_clamps_to_upper_bound() -> None:
    assert inter_segment_delay("这是一段非常非常非常非常非常非常非常长的中文回复") == 3.0


def test_ascii_text_uses_ascii_rate() -> None:
    delay = inter_segment_delay("ContextService")

    assert 0.9 < delay < 1.1


def test_slot_energy_zero_keeps_half_speed_floor() -> None:
    normal = inter_segment_delay("这段文字稍微长一点", slot_energy=1.0)
    low = inter_segment_delay("这段文字稍微长一点", slot_energy=0.0)

    assert low == max(0.5, normal * 0.5)


def test_unknown_register_matches_neutral() -> None:
    assert inter_segment_delay("这段文字", register="unknown") == inter_segment_delay(
        "这段文字",
        register="neutral_default",
    )


def test_natural_split_flag_defaults_off_in_both_config_models() -> None:
    assert ReplySegmentationConfig().natural_split_enabled is False
    assert KernelReplySegmentationConfig().natural_split_enabled is False
