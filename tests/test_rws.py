from __future__ import annotations

import pytest

from services.scheduler_rws import RWSBandit, RWSFeatures, compute_rws


def test_rws_default_score_tracks_legacy_probability() -> None:
    low = compute_rws(RWSFeatures(old_threshold=0.2))
    high = compute_rws(RWSFeatures(old_threshold=0.8))

    assert low.score == pytest.approx(0.2, abs=0.01)
    assert high.score == pytest.approx(0.8, abs=0.01)
    assert low.decision is False
    assert high.decision is True


def test_rws_eot_and_hawkes_are_monotonic() -> None:
    base = compute_rws(RWSFeatures(old_threshold=0.5, eot_probability=0.5, hawkes_rho=0.0))
    ready = compute_rws(RWSFeatures(old_threshold=0.5, eot_probability=0.9, hawkes_rho=0.0))
    hot = compute_rws(RWSFeatures(old_threshold=0.5, eot_probability=0.5, hawkes_rho=0.9))

    assert ready.score > base.score
    assert hot.score < base.score


def test_rws_bandit_freeze_and_negative_reward_bounds_theta() -> None:
    bandit = RWSBandit(theta=0.5, epsilon=0.0, learning_rate=0.1, frozen=False)

    theta = bandit.observe(decision=True, reward=-1.0)

    assert theta == pytest.approx(0.4)
    for _ in range(10):
        theta = bandit.observe(decision=True, reward=-1.0)
    assert theta >= bandit.min_theta

    bandit.frozen = True
    frozen = bandit.theta
    assert bandit.observe(decision=False, reward=-1.0) == frozen
