from __future__ import annotations

import pytest

from services.scheduler_rws import RWSBandit, RWSFeatures, compute_rws
from services.scheduler_rws.rws import dual_decision


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


def test_rws_bandit_epsilon_freeze_and_negative_reward_bounds_theta() -> None:
    # Legacy epsilon algo (rollback path): fire+negative nudges theta by lr.
    bandit = RWSBandit(theta=0.5, epsilon=0.0, learning_rate=0.1, frozen=False, algo="epsilon")

    theta = bandit.observe(decision=True, reward=-1.0)

    assert theta == pytest.approx(0.4)
    for _ in range(10):
        theta = bandit.observe(decision=True, reward=-1.0)
    assert theta >= bandit.min_theta

    bandit.frozen = True
    frozen = bandit.theta
    assert bandit.observe(decision=False, reward=-1.0) == frozen


def test_rws_bandit_thompson_direction_raises_theta_on_bad_fire() -> None:
    # P4 default: a fire that earned negative reward should RAISE theta
    # (fire less next time) — opposite of the old epsilon bug. Beta posterior
    # updates even before min_obs (min_obs only gates exploration sampling).
    bandit = RWSBandit(theta=0.5, frozen=False)  # algo="thompson" by default
    raised = bandit.observe(decision=True, reward=-1.0)
    assert raised > 0.5
    assert raised <= bandit.max_theta

    # A skip that went cold (reward<0) is weak evidence we should fire more →
    # theta drifts back down.
    cold = RWSBandit(theta=0.6, frozen=False)
    lowered = cold.observe(decision=False, reward=-1.0)
    assert lowered < 0.6


def test_rws_bandit_thompson_frozen_holds_theta() -> None:
    bandit = RWSBandit(theta=0.5, frozen=True)
    assert bandit.observe(decision=True, reward=-1.0) == 0.5


def test_rws_memory_terms_shift_score_in_expected_direction() -> None:
    base = compute_rws(RWSFeatures(old_threshold=0.5, outcome_ratio=0.5, familiarity=0.0, willingness_phase=0.5))
    warmer = compute_rws(RWSFeatures(old_threshold=0.5, outcome_ratio=0.9, familiarity=0.6, willingness_phase=0.8))
    colder = compute_rws(RWSFeatures(old_threshold=0.5, outcome_ratio=0.1, familiarity=0.0, willingness_phase=0.1))

    assert warmer.score > base.score
    assert colder.score < base.score


# --- P5: dual-threshold (im = worth saying, interrupt = good moment) ---------

def test_rws_dual_scores_populated_and_split_by_term() -> None:
    # A busy group (high hawkes) should lower the interrupt (timing) score; an
    # at-mention (strong intent term) should lift the im (intent) score.
    busy = compute_rws(RWSFeatures(old_threshold=0.5, hawkes_rho=0.9, eot_probability=0.5))
    calm = compute_rws(RWSFeatures(old_threshold=0.5, hawkes_rho=0.0, eot_probability=0.9))
    assert busy.interrupt_score < calm.interrupt_score  # timing reacts to hawkes/eot
    assert busy.im_score == pytest.approx(calm.im_score, abs=0.01)  # intent unaffected by timing terms

    addressed = compute_rws(RWSFeatures(mode="at_mention", addressee_self=True, old_threshold=0.5))
    overheard = compute_rws(RWSFeatures(mode="none", old_threshold=0.5))
    assert addressed.im_score > overheard.im_score


def test_dual_decision_requires_both_gates() -> None:
    # Worth saying but bad moment → no fire; good moment but nothing to say → no
    # fire; both pass → fire.
    busy = compute_rws(RWSFeatures(mode="at_mention", addressee_self=True, old_threshold=0.5, hawkes_rho=0.95))
    assert busy.im_score >= 0.5  # at-mention → clearly worth saying
    assert not dual_decision(busy, im_threshold=0.5, interrupt_threshold=0.5)  # busy moment blocks

    ready = compute_rws(RWSFeatures(mode="at_mention", addressee_self=True, old_threshold=0.5, eot_probability=0.9))
    assert dual_decision(ready, im_threshold=0.5, interrupt_threshold=0.5)


def test_dual_decision_proactive_bar_is_stricter() -> None:
    # The same borderline moment fires when addressed (lower bar) but is held
    # back as a proactive interjection (higher interrupt bar).
    expl = compute_rws(RWSFeatures(mode="at_mention", addressee_self=True, old_threshold=0.5, eot_probability=0.62))
    assert dual_decision(expl, im_threshold=0.5, interrupt_threshold=0.5)
    assert not dual_decision(expl, im_threshold=0.5, interrupt_threshold=0.65)
