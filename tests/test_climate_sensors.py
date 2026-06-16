"""Tests for Dialogue Climate M3 — Sensor adapter layer (Wave M3-1)."""

from __future__ import annotations

from services.dialogue_climate.dynamics import ClimateEngine
from services.dialogue_climate.sensors import (
    CalendarSensor,
    CircadianSensor,
    InteractionSensor,
    IrritationSensor,
    MessageSensor,
    ScheduleSensor,
    SensorHub,
    SensorInput,
    default_sensors,
)

# -- ScheduleSensor ----------------------------------------------------------


def test_schedule_sensor_emits_delta_from_neutral():
    sig = {s.dim: s for s in ScheduleSensor().sense(SensorInput(mood_energy=0.8, mood_valence=0.5))}
    assert abs(sig["energy"].delta - 0.3) < 1e-9  # 0.8 - 0.5 neutral
    assert "valence" not in sig  # 0.5 == neutral → no signal
    assert sig["energy"].source == "schedule"


def test_schedule_sensor_tension_target_is_zero():
    sig = {s.dim: s for s in ScheduleSensor().sense(SensorInput(mood_tension=0.3))}
    assert abs(sig["tension"].delta - 0.3) < 1e-9  # tension target 0, not neutral


def test_schedule_sensor_none_dims_skipped():
    assert ScheduleSensor().sense(SensorInput()) == []


# -- IrritationSensor --------------------------------------------------------


def test_irritation_sensor_bounded_tension():
    sigs = IrritationSensor().sense(SensorInput(mention_count=2, poke_count=1))
    assert len(sigs) == 1
    s = sigs[0]
    assert s.dim == "tension"
    # 2*0.03 + 1*0.04 + (3-1)*0.01 = 0.12
    assert abs(s.delta - 0.12) < 1e-9


def test_irritation_sensor_capped():
    sigs = IrritationSensor().sense(SensorInput(mention_count=100))
    assert abs(sigs[0].delta - 0.2) < 1e-9  # cap


def test_irritation_sensor_no_burst_no_signal():
    assert IrritationSensor().sense(SensorInput()) == []


# -- CircadianSensor ---------------------------------------------------------


def test_circadian_late_night_energy_penalty():
    sigs = {s.dim: s for s in CircadianSensor().sense(SensorInput(hour=2))}
    assert abs(sigs["energy"].delta - (-0.2)) < 1e-9


def test_circadian_post_lunch_dip():
    sigs = {s.dim: s for s in CircadianSensor().sense(SensorInput(hour=13))}
    assert abs(sigs["energy"].delta - (-0.05)) < 1e-9
    assert abs(sigs["tension"].delta - (-0.05)) < 1e-9


def test_circadian_daytime_no_signal():
    assert CircadianSensor().sense(SensorInput(hour=10)) == []
    assert CircadianSensor().sense(SensorInput(hour=None)) == []


# -- SensorHub ---------------------------------------------------------------


def test_hub_disabled_is_noop():
    eng = ClimateEngine(m2_enabled=True)
    hub = SensorHub(eng, m3_sensors_enabled=False)
    n = hub.collect(SensorInput(group_id="g1", user_id="u1", mood_energy=0.9, hour=2))
    assert n == 0
    assert eng.state_count() == 0


def test_hub_enabled_feeds_engine_per_user():
    eng = ClimateEngine(m2_enabled=True)
    hub = SensorHub(eng, m3_sensors_enabled=True)
    n = hub.collect(
        SensorInput(group_id="g1", user_id="u1", mood_energy=0.9, mention_count=3, hour=2),
        now_ts=0.0,
    )
    assert n >= 2  # energy (schedule) + tension (irritation) + energy (circadian)
    assert eng.state_count() == 1
    state = eng.resolve(group_id="g1", user_id="u1", now_ts=0.0)
    assert state.tension > 0.0  # irritation fed in


def test_hub_engine_disabled_register_returns_zero():
    eng = ClimateEngine(m2_enabled=False)  # engine off → register no-ops
    hub = SensorHub(eng, m3_sensors_enabled=True)
    n = hub.collect(SensorInput(group_id="g1", user_id="u1", mood_energy=0.9), now_ts=0.0)
    assert n == 0
    assert eng.state_count() == 0


def test_default_sensors_set():
    names = {s.name for s in default_sensors()}
    assert names == {"schedule", "irritation", "circadian", "interaction", "calendar", "message"}


# -- InteractionSensor (M3-2) ------------------------------------------------


def test_interaction_sensor_familiarity_and_trust():
    sigs = {s.dim: s for s in InteractionSensor().sense(SensorInput(familiarity=0.8))}
    assert abs(sigs["familiarity"].delta - 0.8) < 1e-9
    assert abs(sigs["trust"].delta - 0.4) < 1e-9  # half weight
    assert sigs["familiarity"].source == "interaction"


def test_interaction_sensor_none_skipped():
    assert InteractionSensor().sense(SensorInput()) == []
    assert InteractionSensor().sense(SensorInput(familiarity=0.0)) == []


# -- CalendarSensor (M3-2, rich calendar_context) ----------------------------


def test_calendar_sensor_self_birthday():
    sigs = {s.dim: s for s in CalendarSensor().sense(SensorInput(has_self_birthday=True))}
    assert sigs["valence"].delta > 0
    assert sigs["energy"].delta > 0


def test_calendar_sensor_holiday_only_valence():
    sigs = {s.dim: s for s in CalendarSensor().sense(SensorInput(is_holiday=True))}
    assert "valence" in sigs
    assert "energy" not in sigs


def test_calendar_sensor_ordinary_day_no_signal():
    assert CalendarSensor().sense(SensorInput()) == []


# -- MessageSensor (M3-2, revived classifier) --------------------------------


def test_message_sensor_cold_label():
    sigs = {s.dim: s for s in MessageSensor().sense(SensorInput(message_label="cold", message_confidence=1.0))}
    assert sigs["tension"].delta > 0
    assert sigs["valence"].delta < 0


def test_message_sensor_confidence_scales():
    full = MessageSensor().sense(SensorInput(message_label="playful", message_confidence=1.0))
    half = MessageSensor().sense(SensorInput(message_label="playful", message_confidence=0.5))
    assert abs(full[0].delta - 2 * half[0].delta) < 1e-9


def test_message_sensor_unknown_or_zero_conf_skipped():
    assert MessageSensor().sense(SensorInput(message_label="neutral", message_confidence=1.0)) == []
    assert MessageSensor().sense(SensorInput(message_label="cold", message_confidence=0.0)) == []

