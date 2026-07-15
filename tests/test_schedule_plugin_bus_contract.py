from __future__ import annotations

from typing import Any

import pytest

from kernel.bus import PluginBus
from kernel.types import ReplyContext
from plugins.schedule.plugin import SchedulePlugin


class _ClimateEngineSpy:
    def __init__(self) -> None:
        self.signals: list[dict[str, Any]] = []

    def register_signal(self, **kwargs: Any) -> bool:
        self.signals.append(kwargs)
        return True


@pytest.mark.asyncio
async def test_real_schedule_manifest_allows_plugin_bus_post_reply_hook() -> None:
    engine = _ClimateEngineSpy()
    plugin = SchedulePlugin()
    plugin._climate_sensor_hub = type(
        "Hub",
        (),
        {"enabled": True, "_engine": engine},
    )()
    bus = PluginBus()
    bus.register(plugin)

    await bus.fire_on_post_reply(ReplyContext(
        session_id="group_100",
        group_id="100",
        user_id="u1",
        reply_content="收到。",
    ))

    assert engine.signals == [{
        "dim": "openness",
        "delta": 0.05,
        "source": "post_reply",
        "group_id": "100",
        "user_id": "u1",
    }]
    [health] = bus.plugin_health()
    assert health["permission_denials"] == 0

