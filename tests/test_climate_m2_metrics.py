"""Tests for Dialogue Climate M3 — ClimateMetricsRecorder (Wave M3-3)."""

from __future__ import annotations

from services.dialogue_climate.dynamics import ClimateEngine
from services.dialogue_climate.m2_metrics import (
    ClimateMetricsRecorder,
    summarize_climate_events,
)
from services.dialogue_climate.state import ClimateState


def test_recorder_round_trip(tmp_path):
    db = str(tmp_path / "m2.db")
    rec = ClimateMetricsRecorder(db_path=db)
    state = ClimateState(tension=0.3, valence=0.6)
    rec.record_signal(
        group_id="g1",
        user_id="u1",
        signal_dim="tension",
        signal_delta=0.1,
        signal_source="irritation",
        state=state,
        monotonic_ts=100.0,
    )
    rows = rec.rows()
    assert len(rows) == 1
    r = rows[0]
    assert r["group_id"] == "g1" and r["user_id"] == "u1"
    assert r["signal_dim"] == "tension"
    assert abs(r["tension"] - 0.3) < 1e-9
    rec.close()


def test_recorder_filters_by_group(tmp_path):
    rec = ClimateMetricsRecorder(db_path=str(tmp_path / "m2.db"))
    for g in ("g1", "g1", "g2"):
        rec.record_signal(
            group_id=g, user_id="u", signal_dim="energy", signal_delta=0.1,
            signal_source="schedule", state=ClimateState(), monotonic_ts=1.0,
        )
    assert len(rec.rows(group_id="g1")) == 2
    assert len(rec.rows(group_id="g2")) == 1
    rec.close()


def test_engine_with_recorder_persists_on_register(tmp_path):
    rec = ClimateMetricsRecorder(db_path=str(tmp_path / "m2.db"))
    eng = ClimateEngine(m2_enabled=True)
    eng.set_recorder(rec)
    eng.register_signal(dim="tension", delta=0.5, source="irritation", group_id="g1", user_id="u1", now_ts=0.0)
    rows = rec.rows()
    assert len(rows) == 1
    assert rows[0]["signal_source"] == "irritation"
    rec.close()


def test_engine_disabled_does_not_record(tmp_path):
    rec = ClimateMetricsRecorder(db_path=str(tmp_path / "m2.db"))
    eng = ClimateEngine(m2_enabled=False)
    eng.set_recorder(rec)
    eng.register_signal(dim="tension", delta=0.5, group_id="g1", user_id="u1")
    assert rec.rows() == []
    rec.close()


def test_summarize_climate_events_pure():
    rows = [
        {"group_id": "g1", "user_id": "u1", "signal_source": "irritation", "tension": 0.4},
        {"group_id": "g1", "user_id": "u2", "signal_source": "schedule", "energy": 0.9},
        {"group_id": "g1", "user_id": "u1", "signal_source": "irritation", "tension": 0.6},
    ]
    s = summarize_climate_events(rows)
    assert s["event_count"] == 3
    assert s["key_count"] == 2
    assert s["source_counts"]["irritation"] == 2
    assert abs(s["peak"]["tension"] - 0.6) < 1e-9


def test_summarize_empty():
    s = summarize_climate_events([])
    assert s["event_count"] == 0 and s["key_count"] == 0
