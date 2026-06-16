"""Tests for Dialogue Climate M2 — ClimateState + on-read dynamics engine.

Mirrors the M1 test style in ``tests/test_mood.py``: fixed-Δt closed-form
assertions are the core correctness evidence, plus disabled-path = zero state
writes (regression baseline) and the differential-decay ordering that the
empirical calibration (Verduyn 2015) anchors.
"""

from __future__ import annotations

import math

from services.dialogue_climate.dynamics import (
    DECAY_RATES,
    ClimateDynamics,
    ClimateDynamicsConfig,
    ClimateEngine,
)
from services.dialogue_climate.state import (
    CLIMATE_DIMENSIONS,
    NEUTRAL,
    ClimateSignal,
    ClimateState,
    clamp01,
)

_HOUR = 3600.0


# -- ClimateState data layer -------------------------------------------------


def test_clamp01_bounds():
    assert clamp01(-1.0) == 0.0
    assert clamp01(2.0) == 1.0
    assert clamp01(0.3) == 0.3


def test_state_round_trip():
    s = ClimateState(energy=0.7, tension=0.4, trust=0.9, update_count=3)
    rebuilt = ClimateState.from_dict(s.to_dict())
    assert rebuilt == s


def test_state_from_dict_clamps_and_ignores_unknown():
    s = ClimateState.from_dict({"energy": 5.0, "tension": -2.0, "bogus": 1})
    assert s.energy == 1.0
    assert s.tension == 0.0
    assert not hasattr(s, "bogus")


def test_neutral_state_defaults():
    s = ClimateState.neutral()
    assert s.energy == NEUTRAL and s.valence == NEUTRAL and s.openness == NEUTRAL
    assert s.tension == 0.0 and s.familiarity == 0.0 and s.trust == NEUTRAL


def test_baseline_targets():
    s = ClimateState(baseline_energy=0.6)
    assert s.baseline_for("energy") == 0.6
    assert s.baseline_for("tension") == 0.0
    assert s.baseline_for("trust") == NEUTRAL
    assert s.baseline_for("familiarity") == 0.0


# -- ClimateDynamics: closed-form decay --------------------------------------


def test_resolve_decays_toward_baseline_fixed_dt():
    """At Δt = one half-life, tension should fall to half its distance to 0."""
    dyn = ClimateDynamics()
    half_life_s = math.log(2.0) / DECAY_RATES["tension"] * _HOUR
    s = ClimateState(tension=0.8, last_update_ts=0.0)
    resolved = dyn.resolve(s, now_ts=half_life_s)
    assert abs(resolved.tension - 0.4) < 1e-9


def test_resolve_is_idempotent_at_zero_dt():
    dyn = ClimateDynamics()
    s = ClimateState(energy=0.7, tension=0.5, last_update_ts=100.0)
    resolved = dyn.resolve(s, now_ts=100.0)
    for dim in CLIMATE_DIMENSIONS:
        assert abs(resolved.get(dim) - s.get(dim)) < 1e-12


def test_differential_decay_tension_faster_than_trust():
    """Verduyn anchor: tension dissipates far faster than trust over same Δt."""
    dyn = ClimateDynamics()
    s = ClimateState(tension=0.8, trust=0.8, last_update_ts=0.0)
    resolved = dyn.resolve(s, now_ts=2.0 * _HOUR)
    tension_drop = 0.8 - resolved.tension  # toward 0.0
    trust_drop = 0.8 - resolved.trust  # toward NEUTRAL 0.5
    assert tension_drop > trust_drop
    # trust barely moves over 2h (half-life ~58h)
    assert trust_drop < 0.02


# -- ClimateDynamics: signal smoothing ---------------------------------------


def test_apply_signal_exponential_smoothing():
    dyn = ClimateDynamics(ClimateDynamicsConfig(alpha=0.2, inertia_familiarity_factor=0.0))
    s = ClimateState(tension=0.0, familiarity=0.0, last_update_ts=0.0)
    sig = ClimateSignal(dim="tension", delta=0.5, ts=0.0)
    out = dyn.apply_signal(s, sig, now_ts=0.0)
    # resolved tension=0 at Δt=0; nudged = 0 + α·0.5 = 0.1
    assert abs(out.tension - 0.1) < 1e-9
    assert out.update_count == 1


def test_apply_signal_familiarity_raises_alpha():
    cfg = ClimateDynamicsConfig(alpha=0.2, inertia_familiarity_factor=0.3)
    dyn = ClimateDynamics(cfg)
    base = ClimateState(valence=0.0, familiarity=0.0, last_update_ts=0.0)
    close = ClimateState(valence=0.0, familiarity=1.0, last_update_ts=0.0)
    sig = ClimateSignal(dim="valence", delta=0.4, ts=0.0)
    out_base = dyn.apply_signal(base, sig, now_ts=0.0)
    out_close = dyn.apply_signal(close, sig, now_ts=0.0)
    # familiarity=1.0 → alpha 0.2+0.3=0.5; base alpha 0.2
    assert abs(out_base.valence - 0.08) < 1e-9
    assert abs(out_close.valence - 0.20) < 1e-9


def test_apply_invalid_signal_is_resolve_only():
    dyn = ClimateDynamics()
    s = ClimateState(tension=0.8, last_update_ts=0.0)
    bad = ClimateSignal(dim="not_a_dim", delta=0.5, ts=0.0)
    half_life_s = math.log(2.0) / DECAY_RATES["tension"] * _HOUR
    out = dyn.apply_signal(s, bad, now_ts=half_life_s)
    assert abs(out.tension - 0.4) < 1e-9  # decayed, not nudged
    assert out.update_count == 0


# -- ClimateDynamics: baseline drift -----------------------------------------


def test_drift_baseline_moves_toward_current_and_neutral():
    cfg = ClimateDynamicsConfig(baseline_drift_rate=0.5, baseline_regression_rate=0.1)
    dyn = ClimateDynamics(cfg)
    s = ClimateState(energy=1.0, baseline_energy=0.5, last_update_ts=0.0)
    drifted = dyn.drift_baseline(s, now_ts=10.0 * _HOUR)
    # baseline should rise toward current (1.0) but stay below it
    assert 0.5 < drifted.baseline_energy < 1.0


# -- ClimateEngine: disabled path = zero behaviour change --------------------


def test_engine_disabled_register_is_noop():
    eng = ClimateEngine(m2_enabled=False)
    changed = eng.register_signal(dim="tension", delta=0.5, group_id="g1", user_id="u1")
    assert changed is False
    assert eng.state_count() == 0


def test_engine_disabled_resolve_returns_neutral():
    eng = ClimateEngine(m2_enabled=False)
    state = eng.resolve(group_id="g1", user_id="u1")
    assert state == ClimateState.neutral()
    assert eng.state_count() == 0


def test_engine_enabled_records_per_group_user_on_read_decay():
    eng = ClimateEngine(m2_enabled=True)
    assert eng.register_signal(dim="tension", delta=0.5, group_id="g1", user_id="u1", now_ts=0.0)
    assert eng.state_count() == 1
    half_life_s = math.log(2.0) / DECAY_RATES["tension"] * _HOUR
    # registered tension = α·0.5 = 0.1 (default alpha 0.2); after one half-life → 0.05
    resolved = eng.resolve(group_id="g1", user_id="u1", now_ts=half_life_s)
    assert abs(resolved.tension - 0.05) < 1e-9
    # same group, different user is independent / neutral
    other_user = eng.resolve(group_id="g1", user_id="u2", now_ts=half_life_s)
    assert other_user == ClimateState.neutral()
    # same user, different group is independent / neutral
    other_group = eng.resolve(group_id="g2", user_id="u1", now_ts=half_life_s)
    assert other_group == ClimateState.neutral()


def test_engine_per_user_states_isolated_within_group():
    eng = ClimateEngine(m2_enabled=True)
    eng.register_signal(dim="familiarity", delta=0.8, group_id="g1", user_id="u1", now_ts=0.0)
    eng.register_signal(dim="familiarity", delta=0.2, group_id="g1", user_id="u2", now_ts=0.0)
    assert eng.state_count() == 2
    s1 = eng.resolve(group_id="g1", user_id="u1", now_ts=0.0)
    s2 = eng.resolve(group_id="g1", user_id="u2", now_ts=0.0)
    assert s1.familiarity > s2.familiarity


def test_engine_clear_stale_prunes_old_keys():
    eng = ClimateEngine(m2_enabled=True)
    eng.register_signal(dim="tension", delta=0.5, group_id="g1", user_id="u1", now_ts=0.0)
    eng.register_signal(dim="tension", delta=0.5, group_id="g1", user_id="u2", now_ts=1000.0)
    assert eng.state_count() == 2
    # prune anything older than 500s as of t=1000 → u1 (last update 0) goes, u2 stays
    pruned = eng.clear_stale(max_age_s=500.0, now_ts=1000.0)
    assert pruned == 1
    assert eng.state_count() == 1
    assert eng.resolve(group_id="g1", user_id="u1", now_ts=1000.0) == ClimateState.neutral()


def test_engine_clear_stale_disabled_is_noop():
    eng = ClimateEngine(m2_enabled=False)
    assert eng.clear_stale() == 0


# -- tension migration groundwork (F2): M1 → ClimateEngine closed-form equivalence


def test_tension_migration_closed_form_equivalence_with_m1():
    """ClimateDynamics tension resolution == M1 resolve_m1_tension_on_read.

    Both decay toward 0 as ``v·exp(-Δt/τ)``. M1 uses τ in seconds; ClimateDynamics
    uses per-hour λ (= 3600/τ). Given matching constants the two must agree to
    floating point, proving the tension migration (F2) is a pure owner swap, not
    a behaviour change in the decay law. The *tuned* time constant differs (M1
    τ=600s vs M2 ~5217s) — that is a deliberate calibration decision recorded in
    the M3 plan, separate from the law's correctness asserted here.
    """
    from plugins.schedule.mood import resolve_m1_tension_on_read

    tau_s = 600.0
    lam_per_hour = 3600.0 / tau_s
    cfg = ClimateDynamicsConfig(decay_rates={**DECAY_RATES, "tension": lam_per_hour})
    dyn = ClimateDynamics(cfg)
    for dt in (0.0, 60.0, 300.0, 600.0, 1800.0):
        m1 = resolve_m1_tension_on_read(0.8, 0.0, 0.0, dt, tau_s=tau_s)
        state = ClimateState(tension=0.8, last_update_ts=0.0)
        m2 = dyn.resolve(state, now_ts=dt).tension
        assert abs(m1 - m2) < 1e-9

