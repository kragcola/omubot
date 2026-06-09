"""Tests for the durable M1 dialogue-climate tension metrics recorder."""

from __future__ import annotations

import math

from services.dialogue_climate.m1_metrics import (
    EVENT_INJECT,
    EVENT_TRIGGER,
    M1MetricsRecorder,
    summarize_events,
)


class TestSummarizeEvents:
    def test_empty(self) -> None:
        s = summarize_events([])
        assert s["injection_count"] == 0
        assert s["prompt_trigger_count"] == 0
        assert s["prompt_trigger_rate"] == 0.0
        assert s["observed_half_life_s"] == 0.0

    def test_counts_and_trigger_rate(self) -> None:
        rows = [
            {"event_type": EVENT_INJECT, "group_id": "g1", "session_id": "group_g1",
             "monotonic_ts": 0.0, "tension_after": 0.1, "tension_before": 0.0, "tau_s": 600.0},
            {"event_type": EVENT_INJECT, "group_id": "g1", "session_id": "group_g1",
             "monotonic_ts": 10.0, "tension_after": 0.15, "tension_before": 0.098, "tau_s": 600.0},
            {"event_type": EVENT_TRIGGER, "group_id": "g1", "session_id": "group_g1",
             "monotonic_ts": 11.0, "tension_after": 0.15, "tension_before": 0.15, "tau_s": 600.0},
        ]
        s = summarize_events(rows)
        assert s["injection_count"] == 2
        assert s["prompt_trigger_count"] == 1
        assert s["prompt_trigger_rate"] == 0.5
        assert s["key_count"] == 1
        assert s["configured_tau_s"] == 600.0

    def test_observed_half_life_recovers_known_tau(self) -> None:
        # Construct injections whose between-injection decay matches tau=600s
        # exactly, so the fit must recover ln(2)*600 as the half-life.
        tau = 600.0
        dt = 300.0
        prev_after = 0.2
        before_next = prev_after * math.exp(-dt / tau)
        rows = [
            {"event_type": EVENT_INJECT, "group_id": "g1", "session_id": "group_g1",
             "monotonic_ts": 0.0, "tension_after": prev_after, "tension_before": 0.0, "tau_s": tau},
            {"event_type": EVENT_INJECT, "group_id": "g1", "session_id": "group_g1",
             "monotonic_ts": dt, "tension_after": 0.3, "tension_before": before_next, "tau_s": tau},
        ]
        s = summarize_events(rows)
        assert s["decay_sample_count"] == 1
        assert math.isclose(s["observed_tau_s"], tau, rel_tol=1e-6)
        assert math.isclose(s["observed_half_life_s"], math.log(2.0) * tau, rel_tol=1e-6)
        assert math.isclose(s["median_interarrival_s"], dt)

    def test_decay_sample_skips_non_decay_pairs(self) -> None:
        # before_next >= prev_after (no decay observed) must not yield a tau sample.
        rows = [
            {"event_type": EVENT_INJECT, "group_id": "g1", "session_id": "s",
             "monotonic_ts": 0.0, "tension_after": 0.1, "tension_before": 0.0, "tau_s": 600.0},
            {"event_type": EVENT_INJECT, "group_id": "g1", "session_id": "s",
             "monotonic_ts": 5.0, "tension_after": 0.2, "tension_before": 0.1, "tau_s": 600.0},
        ]
        s = summarize_events(rows)
        assert s["decay_sample_count"] == 0
        assert s["observed_half_life_s"] == 0.0

    def test_per_key_isolation(self) -> None:
        rows = [
            {"event_type": EVENT_INJECT, "group_id": "g1", "session_id": "s",
             "monotonic_ts": 0.0, "tension_after": 0.1, "tension_before": 0.0, "tau_s": 600.0},
            {"event_type": EVENT_INJECT, "group_id": "g2", "session_id": "s",
             "monotonic_ts": 1.0, "tension_after": 0.1, "tension_before": 0.0, "tau_s": 600.0},
        ]
        s = summarize_events(rows)
        assert s["key_count"] == 2
        # no within-key decay pairs across two distinct keys
        assert s["decay_sample_count"] == 0


class TestM1MetricsRecorderDurable:
    def test_roundtrip_and_summary(self, tmp_path) -> None:
        db = str(tmp_path / "m1.db")
        rec = M1MetricsRecorder(db_path=db)
        rec.record_inject(
            group_id="g1", session_id="group_g1",
            tension_before=0.0, tension_after=0.1, delta=0.1,
            mention_count=2, poke_count=0, tau_s=600.0, threshold=0.12,
            monotonic_ts=0.0,
        )
        rec.record_inject(
            group_id="g1", session_id="group_g1",
            tension_before=0.098, tension_after=0.16, delta=0.07,
            mention_count=1, poke_count=1, tau_s=600.0, threshold=0.12,
            monotonic_ts=12.0,
        )
        rec.record_trigger(
            group_id="g1", session_id="group_g1",
            tension_after=0.16, threshold=0.12, tau_s=600.0, monotonic_ts=13.0,
        )
        rec.close()

        # Reopen to prove durability across "restart".
        rec2 = M1MetricsRecorder(db_path=db)
        rows = rec2.rows(group_id="g1")
        assert len(rows) == 3
        s = rec2.summary()
        assert s["injection_count"] == 2
        assert s["prompt_trigger_count"] == 1
        assert s["prompt_trigger_rate"] == 0.5
        rec2.close()

    def test_write_never_raises_on_bad_path(self) -> None:
        # An unwritable path must degrade silently, not break the reply path.
        rec = M1MetricsRecorder(db_path="/proc/should-not-exist/m1.db")
        rec.record_inject(
            group_id="g1", session_id="s",
            tension_before=0.0, tension_after=0.1, delta=0.1,
        )
        assert rec.rows() == []
