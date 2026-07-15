from __future__ import annotations

from typing import Any

import pytest

from kernel.bus import PluginBus
from kernel.types import AmadeusPlugin, PluginContext, ReplyContext
from services.health import _build_health_alerts, _check_plugin_bus


class _ReplyPlugin(AmadeusPlugin):
    name = "reply_plugin"

    def __init__(self) -> None:
        super().__init__()
        self.permissions = ["prompt"]

    async def on_post_reply(self, ctx: ReplyContext) -> None:
        del ctx


def _reply_context() -> ReplyContext:
    return ReplyContext(
        session_id="group_100",
        group_id="100",
        user_id="u1",
        reply_content="ok",
    )


async def _service_after_denials(*, reply_denials: int, noop_tick_denials: int) -> dict[str, Any]:
    bus = PluginBus()
    bus.register(_ReplyPlugin())
    for _ in range(reply_denials):
        await bus.fire_on_post_reply(_reply_context())
    for _ in range(noop_tick_denials):
        await bus.fire_on_tick(PluginContext())
    return _check_plugin_bus(ctx=type("Ctx", (), {"bus": bus})())


@pytest.mark.asyncio
async def test_plugin_bus_actual_reply_denials_at_threshold_raise_health_warning() -> None:
    service = await _service_after_denials(reply_denials=5, noop_tick_denials=7)
    alerts, _policy = _build_health_alerts([service])

    assert service["status"] == "warning"
    assert "5" in service["detail"]
    assert service["meta"]["permission_denials"] == 12
    assert service["meta"]["permission_contract_denials"] == 5
    assert service["meta"]["permission_skips"] == 7
    assert [alert["source"] for alert in alerts] == ["plugin_bus"]


@pytest.mark.asyncio
async def test_plugin_bus_other_noop_denials_do_not_amplify_actual_reply_count() -> None:
    service = await _service_after_denials(reply_denials=4, noop_tick_denials=7)

    assert service["status"] == "ok"
    assert service["meta"]["permission_denials"] == 11
    assert service["meta"]["permission_contract_denials"] == 4
    assert service["meta"]["permission_skips"] == 7


@pytest.mark.asyncio
async def test_plugin_bus_expected_noop_permission_skips_do_not_raise_warning() -> None:
    noop = AmadeusPlugin()
    noop.name = "noop"
    noop.permissions = ["prompt"]
    bus = PluginBus()
    bus.register(noop)
    for _ in range(5):
        await bus.fire_on_tick(PluginContext())

    service = _check_plugin_bus(ctx=type("Ctx", (), {"bus": bus})())

    assert service["status"] == "ok"
    assert service["meta"]["permission_denials"] == 5
    assert service["meta"]["permission_contract_denials"] == 0
    assert service["meta"]["permission_skips"] == 5
