"""Tests for Part 1 humanization rollout config."""

from __future__ import annotations

import json
from pathlib import Path

from kernel.config import BotConfig, HumanizationConfig, load_config


def test_humanization_config_defaults_keep_only_safe_runtime_features_on() -> None:
    cfg = BotConfig()

    assert cfg.humanization.context_providers is False
    assert cfg.humanization.register_classifier is False
    assert cfg.humanization.sticker_register_provider is False
    assert cfg.humanization.thinker_provider is False
    assert cfg.humanization.rewrite_threshold == -1.0
    assert cfg.humanization.semantic_gate_dynamic is False
    assert cfg.humanization.kaomoji_enforce_strict is False
    assert cfg.humanization.profile == "custom"
    assert cfg.humanization.runtime_groups == []
    assert cfg.humanization.state_board.layout == "head"
    assert cfg.humanization.state_board.granularity == "fine"
    assert cfg.humanization.streaming_segment.enabled is False
    assert cfg.humanization.pause_then_extend.enabled is True
    assert cfg.humanization.plan_then_utter.enabled is False
    assert cfg.humanization.plan_then_utter.group_whitelist == []
    assert cfg.humanization.rws_shadow is False
    assert cfg.humanization.rws_primary is False
    assert cfg.humanization.rws_threshold == 0.5
    assert cfg.humanization.rws_hawkes is False
    assert cfg.humanization.rws_eot is False
    assert cfg.humanization.rws_bandit is False
    assert cfg.humanization.rws_bandit_freeze is True
    assert cfg.humanization.counterfactual_replay is False
    assert cfg.humanization.pass_turn_confidence_gate is False
    assert cfg.humanization.pass_turn_confidence_threshold == 0.4


def test_humanization_config_from_toml(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
[humanization]
context_providers = true
register_classifier = true
sticker_register_provider = true
thinker_provider = true
rewrite_threshold = 0.4
semantic_gate_dynamic = true
kaomoji_enforce_strict = true
profile = "balanced"
runtime_groups = [993065015, " 984198159 ", ""]
state_board = { layout = "tail", granularity = "coarse" }
streaming_segment = { enabled = true }
pause_then_extend = { enabled = true }
plan_then_utter = { enabled = true, group_whitelist = [993065015, ""] }
rws_shadow = true
rws_primary = true
rws_threshold = 0.42
rws_hawkes = true
rws_eot = true
rws_bandit = true
rws_bandit_freeze = false
counterfactual_replay = true
pass_turn_confidence_gate = true
pass_turn_confidence_threshold = 0.35
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(config_path=str(config_file))

    assert cfg.humanization.context_providers is True
    assert cfg.humanization.register_classifier is True
    assert cfg.humanization.sticker_register_provider is True
    assert cfg.humanization.thinker_provider is True
    assert cfg.humanization.rewrite_threshold == 0.4
    assert cfg.humanization.semantic_gate_dynamic is True
    assert cfg.humanization.kaomoji_enforce_strict is True
    assert cfg.humanization.profile == "balanced"
    assert cfg.humanization.runtime_groups == ["993065015", "984198159"]
    assert cfg.humanization.state_board.layout == "tail"
    assert cfg.humanization.state_board.granularity == "coarse"
    assert cfg.humanization.streaming_segment.enabled is True
    assert cfg.humanization.pause_then_extend.enabled is True
    assert cfg.humanization.plan_then_utter.enabled is True
    assert cfg.humanization.plan_then_utter.group_whitelist == ["993065015"]
    assert cfg.humanization.rws_shadow is True
    assert cfg.humanization.rws_primary is True
    assert cfg.humanization.rws_threshold == 0.42
    assert cfg.humanization.rws_hawkes is True
    assert cfg.humanization.rws_eot is True
    assert cfg.humanization.rws_bandit is True
    assert cfg.humanization.rws_bandit_freeze is False
    assert cfg.humanization.counterfactual_replay is True
    assert cfg.humanization.pass_turn_confidence_gate is True
    assert cfg.humanization.pass_turn_confidence_threshold == 0.35


def test_humanization_config_from_json(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({
            "humanization": {
                "context_providers": True,
                "rewrite_threshold": 0.25,
                "kaomoji_enforce_strict": True,
                "profile": "economy",
                "runtime_groups": [993065015],
                "state_board": {"layout": "tail", "granularity": "coarse"},
                "streaming_segment": {"enabled": True},
                "pause_then_extend": {"enabled": True},
                "plan_then_utter": {
                    "enabled": True,
                    "group_whitelist": [993065015],
                },
                "rws_threshold": 2.0,
                "pass_turn_confidence_threshold": -1.0,
            }
        }),
        encoding="utf-8",
    )

    cfg = load_config(config_path=str(config_file))

    assert cfg.humanization.context_providers is True
    assert cfg.humanization.rewrite_threshold == 0.25
    assert cfg.humanization.kaomoji_enforce_strict is True
    assert cfg.humanization.profile == "economy"
    assert cfg.humanization.runtime_groups == ["993065015"]
    assert cfg.humanization.state_board.layout == "tail"
    assert cfg.humanization.state_board.granularity == "coarse"
    assert cfg.humanization.streaming_segment.enabled is True
    assert cfg.humanization.pause_then_extend.enabled is True
    assert cfg.humanization.plan_then_utter.enabled is True
    assert cfg.humanization.plan_then_utter.group_whitelist == ["993065015"]
    assert cfg.humanization.rws_threshold == 1.0
    assert cfg.humanization.pass_turn_confidence_threshold == 0.0
    assert cfg.humanization.register_classifier is False
    assert cfg.humanization.semantic_gate_dynamic is False


def test_humanization_rewrite_threshold_negative_disables_loop() -> None:
    cfg = HumanizationConfig()

    assert cfg.rewrite_threshold < 0


def test_humanization_resolve_profile_custom_reads_flags() -> None:
    cfg = HumanizationConfig.model_validate({
        "state_board": {"layout": "tail", "granularity": "coarse"},
        "streaming_segment": {"enabled": True},
        "pause_then_extend": {"enabled": True},
        "plan_then_utter": {
            "enabled": True,
            "group_whitelist": ["993065015"],
        },
    })

    allowed = cfg.resolve_profile(group_id="993065015")
    blocked = cfg.resolve_profile(group_id="984198159")

    assert allowed.state_board_layout == "tail"
    assert allowed.state_board_granularity == "coarse"
    assert allowed.streaming_segment_enabled is True
    assert allowed.pause_then_extend_enabled is True
    assert allowed.plan_then_utter_enabled is True
    assert allowed.disable_natural_split is True
    assert blocked.plan_then_utter_enabled is False
    assert blocked.disable_natural_split is True


def test_humanization_resolve_profile_presets() -> None:
    economy = HumanizationConfig(profile="economy").resolve_profile("economy")
    balanced = HumanizationConfig(profile="balanced").resolve_profile("balanced")
    performance = HumanizationConfig.model_validate({
        "profile": "performance",
        "plan_then_utter": {"group_whitelist": ["993065015"]},
    }).resolve_profile("performance", group_id="984198159")

    assert economy.state_board_layout == "tail"
    assert economy.state_board_granularity == "coarse"
    assert economy.streaming_segment_enabled is False
    assert economy.pause_then_extend_enabled is False
    assert economy.plan_then_utter_enabled is False
    assert economy.disable_natural_split is False

    assert balanced.streaming_segment_enabled is True
    assert balanced.pause_then_extend_enabled is True
    assert balanced.plan_then_utter_enabled is False
    assert balanced.disable_natural_split is True

    assert performance.streaming_segment_enabled is True
    assert performance.pause_then_extend_enabled is True
    assert performance.plan_then_utter_enabled is False
    assert performance.disable_natural_split is True

    performance_pilot = HumanizationConfig.model_validate({
        "profile": "performance",
        "plan_then_utter": {"enabled": True, "group_whitelist": ["993065015"]},
    }).resolve_profile("performance", group_id="993065015")
    assert performance_pilot.plan_then_utter_enabled is True


def test_resolve_profile_invariants() -> None:
    cases = [
        HumanizationConfig(profile="economy").resolve_profile("economy"),
        HumanizationConfig(profile="balanced").resolve_profile("balanced"),
        HumanizationConfig.model_validate({
            "profile": "performance",
            "plan_then_utter": {"enabled": True, "group_whitelist": ["993065015"]},
        }).resolve_profile("performance", group_id="993065015"),
        HumanizationConfig.model_validate({
            "profile": "custom",
            "streaming_segment": {"enabled": False},
            "plan_then_utter": {"enabled": True, "group_whitelist": ["993065015"]},
        }).resolve_profile("custom", group_id="993065015"),
    ]

    for resolved in cases:
        if resolved.disable_natural_split:
            assert resolved.streaming_segment_enabled or resolved.plan_then_utter_enabled


def test_humanization_config_allows_single_flag_override() -> None:
    cfg = BotConfig.model_validate({"humanization": {"register_classifier": True}})

    assert cfg.humanization.register_classifier is True
    assert cfg.humanization.context_providers is False
    assert cfg.humanization.sticker_register_provider is False
    assert cfg.humanization.thinker_provider is False
    assert cfg.humanization.semantic_gate_dynamic is False
    assert cfg.humanization.kaomoji_enforce_strict is False
    assert cfg.humanization.rws_shadow is False


def test_humanization_config_ignores_unknown_legacy_fields(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({
            "humanization": {
                "context_providers": False,
                "old_unused_flag": True,
            }
        }),
        encoding="utf-8",
    )

    cfg = load_config(config_path=str(config_file))

    assert cfg.humanization.context_providers is False
    assert not hasattr(cfg.humanization, "old_unused_flag")
