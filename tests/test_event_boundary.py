from __future__ import annotations

import time

import pytest

from kernel.types import PluginContext
from plugins.dream.plugin import DreamPlugin
from services.memory_consolidator.event_boundary import EventBoundaryDetector


class _Profile:
    def __init__(self, valence: float) -> None:
        self.valence = valence


class _MoodEngine:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def recent_profiles(self, **_kwargs):
        return [_Profile(value) for value in self._values]


class _MessageLog:
    def __init__(self, *, last_age_s: float, group_ids: list[str] | None = None) -> None:
        self._last_age_s = last_age_s
        self._group_ids = group_ids or ["g1"]

    async def query_recent(self, group_id: str, limit: int = 1):
        assert group_id == "g1"
        return [{"created_at": time.time() - self._last_age_s}][:limit]

    async def list_group_ids(self):
        return list(self._group_ids)


@pytest.mark.asyncio
async def test_event_boundary_silence_trigger() -> None:
    detector = EventBoundaryDetector(cooldown_s=0.0)
    triggered = await detector.check_silence("g1", _MessageLog(last_age_s=1900), threshold_min=30)
    assert triggered is True


@pytest.mark.asyncio
async def test_event_boundary_mood_reversal_trigger_and_cooldown() -> None:
    detector = EventBoundaryDetector(cooldown_s=1800.0)
    mood_engine = _MoodEngine([-0.6, 0.5])

    first, reason = await detector.detect(
        group_id="g1",
        message_log=_MessageLog(last_age_s=10),
        mood_engine=mood_engine,
    )
    second, _ = await detector.detect(
        group_id="g1",
        message_log=_MessageLog(last_age_s=10),
        mood_engine=mood_engine,
    )

    assert first is True
    assert reason == "mood_reversal"
    assert second is False


@pytest.mark.asyncio
async def test_dream_plugin_on_tick_runs_event_boundary_trigger(monkeypatch, tmp_path) -> None:
    class _Consolidator:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def run_once(self, **kwargs):
            self.calls.append(kwargs)

    from services import learning_settings

    monkeypatch.setattr(
        learning_settings,
        "load",
        lambda _storage_dir: {"consolidator": {"auto_enabled": True, "interval_minutes": 360}},
    )
    monkeypatch.setenv("EBR_ENABLED", "true")

    plugin = DreamPlugin()
    plugin._last_consolidator_monotonic = time.monotonic()
    consolidator = _Consolidator()
    ctx = PluginContext(
        storage_dir=tmp_path,
        memory_consolidator=consolidator,
        msg_log=_MessageLog(last_age_s=1900),
        mood_engine=_MoodEngine([0.0, 0.1]),
    )

    await plugin.on_tick(ctx)

    assert consolidator.calls
    assert consolidator.calls[0]["triggered_by"] == "event_boundary:silence"
