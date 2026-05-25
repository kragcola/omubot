from __future__ import annotations

import pytest

from services.humanizer import EMOJI_BASE_DELAY, THINKING_FALLBACK, Humanizer


async def _capture_delay(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("services.humanizer.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("services.humanizer.random.uniform", lambda _a, _b: 1.0)
    return sleeps


@pytest.mark.asyncio
async def test_typing_plain_text_keeps_existing_formula(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = await _capture_delay(monkeypatch)
    humanizer = Humanizer(enabled=True, min_delay=1.0, max_delay=1.0, char_delay=0.1)

    await humanizer.delay("abcd")

    assert sleeps == [pytest.approx(1.4)]


@pytest.mark.asyncio
async def test_typing_emoji_has_base_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = await _capture_delay(monkeypatch)
    humanizer = Humanizer(enabled=True, min_delay=1.0, max_delay=1.0, char_delay=0.1)

    await humanizer.delay("🙂")

    assert sleeps == [pytest.approx(1.0 + EMOJI_BASE_DELAY)]


@pytest.mark.asyncio
async def test_typing_thinking_fallback_caps_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = await _capture_delay(monkeypatch)
    humanizer = Humanizer(enabled=True, min_delay=1.0, max_delay=1.0, char_delay=0.5)

    await humanizer.delay("abcdefgh", thinking_elapsed_s=THINKING_FALLBACK)

    assert sleeps == [pytest.approx(1.0)]


@pytest.mark.asyncio
async def test_typing_disabled_does_not_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = await _capture_delay(monkeypatch)
    humanizer = Humanizer(enabled=False)

    await humanizer.delay("🙂", thinking_elapsed_s=THINKING_FALLBACK)

    assert sleeps == []
