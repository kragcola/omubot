from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.humanizer import Humanizer


async def _capture_delay(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("services.humanizer.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("services.humanizer.random.uniform", lambda _a, _b: 1.0)
    return sleeps


@pytest.mark.asyncio
async def test_delay_old_signature_keeps_default_multiplier(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = await _capture_delay(monkeypatch)
    humanizer = Humanizer(enabled=True, min_delay=1.0, max_delay=1.0, char_delay=0.1)

    await humanizer.delay("abcd")

    assert sleeps == [pytest.approx(1.4)]


@pytest.mark.asyncio
async def test_delay_quiet_low_energy_slows_down(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = await _capture_delay(monkeypatch)
    humanizer = Humanizer(enabled=True, min_delay=1.0, max_delay=1.0, char_delay=0.1)

    await humanizer.delay(
        "abcd",
        group_id="g1",
        register="quiet",
        slot={"energy": 0.2},
        mood=SimpleNamespace(energy=0.3),
    )

    assert sleeps == [pytest.approx(2.1)]


@pytest.mark.asyncio
async def test_delay_quiet_requires_slot_and_mood_low_energy(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = await _capture_delay(monkeypatch)
    humanizer = Humanizer(enabled=True, min_delay=1.0, max_delay=1.0, char_delay=0.1)

    await humanizer.delay("abcd", register="quiet", slot={"energy": 0.2}, mood={"energy": 0.8})

    assert sleeps == [pytest.approx(1.4)]


@pytest.mark.asyncio
async def test_delay_playful_speeds_up_from_dict_register(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = await _capture_delay(monkeypatch)
    humanizer = Humanizer(enabled=True, min_delay=1.0, max_delay=1.0, char_delay=0.1)

    await humanizer.delay("abcd", register={"label": "playful"}, slot={"energy": 0.1}, mood={"energy": 0.1})

    assert sleeps == [pytest.approx(0.98)]


@pytest.mark.asyncio
async def test_disabled_humanizer_does_not_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = await _capture_delay(monkeypatch)
    humanizer = Humanizer(enabled=False)

    await humanizer.delay("abcd", register="playful")

    assert sleeps == []
