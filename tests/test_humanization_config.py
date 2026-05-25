"""Tests for Part 1 humanization rollout config."""

from __future__ import annotations

import json
from pathlib import Path

from kernel.config import BotConfig, HumanizationConfig, load_config


def test_humanization_config_defaults_all_features_off() -> None:
    cfg = BotConfig()

    assert cfg.humanization.context_providers is False
    assert cfg.humanization.register_classifier is False
    assert cfg.humanization.sticker_register_provider is False
    assert cfg.humanization.thinker_provider is False
    assert cfg.humanization.rewrite_threshold == -1.0
    assert cfg.humanization.semantic_gate_dynamic is False
    assert cfg.humanization.runtime_groups == []


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
runtime_groups = [993065015, " 984198159 ", ""]
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
    assert cfg.humanization.runtime_groups == ["993065015", "984198159"]


def test_humanization_config_from_json(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({
            "humanization": {
                "context_providers": True,
                "rewrite_threshold": 0.25,
                "runtime_groups": [993065015],
            }
        }),
        encoding="utf-8",
    )

    cfg = load_config(config_path=str(config_file))

    assert cfg.humanization.context_providers is True
    assert cfg.humanization.rewrite_threshold == 0.25
    assert cfg.humanization.runtime_groups == ["993065015"]
    assert cfg.humanization.register_classifier is False
    assert cfg.humanization.semantic_gate_dynamic is False


def test_humanization_rewrite_threshold_negative_disables_loop() -> None:
    cfg = HumanizationConfig()

    assert cfg.rewrite_threshold < 0


def test_humanization_config_allows_single_flag_override() -> None:
    cfg = BotConfig.model_validate({"humanization": {"register_classifier": True}})

    assert cfg.humanization.register_classifier is True
    assert cfg.humanization.context_providers is False
    assert cfg.humanization.sticker_register_provider is False
    assert cfg.humanization.thinker_provider is False
    assert cfg.humanization.semantic_gate_dynamic is False


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
