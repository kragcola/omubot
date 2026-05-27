"""Tests for Part 6 QQ interaction profile decisions."""

from __future__ import annotations

from kernel.config import GroupConfig, HumanizationConfig, ResolvedHumanization


def _qq_flags(resolved: ResolvedHumanization) -> tuple[bool, bool, bool, bool, bool]:
    return (
        resolved.qq_interactions_poke_inbound_response_enabled,
        resolved.qq_interactions_reaction_inbound_response_enabled,
        resolved.qq_interactions_poke_outbound_enabled,
        resolved.qq_interactions_reaction_outbound_enabled,
        resolved.qq_interactions_quote_reply_enabled,
    )


def test_economy_profile_disables_all_qq_interactions() -> None:
    resolved = HumanizationConfig(profile="economy").resolve_profile("economy")

    assert _qq_flags(resolved) == (False, False, False, False, False)


def test_balanced_profile_enables_inbound_and_quote_only() -> None:
    resolved = HumanizationConfig(profile="balanced").resolve_profile("balanced")

    assert _qq_flags(resolved) == (True, True, False, False, True)


def test_performance_profile_enables_all_qq_interactions() -> None:
    resolved = HumanizationConfig(profile="performance").resolve_profile("performance")

    assert _qq_flags(resolved) == (True, True, True, True, True)


def test_custom_profile_reads_qq_interaction_flags() -> None:
    cfg = HumanizationConfig.model_validate({
        "qq_interactions": {
            "poke_inbound_response_enabled": True,
            "reaction_inbound_response_enabled": False,
            "poke_outbound_enabled": True,
            "reaction_outbound_enabled": False,
            "quote_reply_enabled": True,
        },
    })

    assert _qq_flags(cfg.resolve_profile("custom")) == (True, False, True, False, True)


def test_group_override_accepts_qq_interactions_profile_override() -> None:
    group = GroupConfig.model_validate({
        "allowed_groups": [1001],
        "overrides": {
            "1001": {
                "humanization_profile": "balanced",
                "qq_interactions_profile_override": False,
            },
        },
    })

    resolved = group.resolve(1001)

    assert resolved.humanization_profile == "balanced"
    assert resolved.qq_interactions_profile_override is False
