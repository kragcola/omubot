"""Health and storage-catalog contracts for Social Narrative."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from services.health import collect_service_health
from services.storage.catalog import DEFAULT_DATABASE_CATALOG


class _StatsStore:
    def __init__(
        self,
        *,
        entities: int = 2,
        active_experiences: int = 3,
        invalidated_experiences: int = 1,
    ) -> None:
        self.calls = 0
        self._stats = {
            "entities": entities,
            "active_experiences": active_experiences,
            "invalidated_experiences": invalidated_experiences,
        }

    async def stats(self) -> dict[str, int]:
        self.calls += 1
        return dict(self._stats)


class _Plugin:
    name = "social_narrative"
    enabled = True

    def __init__(
        self,
        *,
        configured_enabled: bool,
        allowed_group_ids: set[str],
        store: Any = None,
    ) -> None:
        self._enabled = configured_enabled
        self._allowed_group_ids = allowed_group_ids
        self._store = store


class _Bus:
    def __init__(self, plugin: Any = None) -> None:
        self._plugin = plugin
        self.plugins = [plugin] if plugin is not None else []

    def get_plugin(self, name: str) -> Any:
        return self._plugin if name == "social_narrative" else None

    def plugin_health(self) -> list[dict[str, Any]]:
        return []


def _social_item(payload: dict[str, Any]) -> dict[str, Any]:
    matches = [
        item
        for item in payload["services"]
        if item["id"] == "social_narrative"
    ]
    assert len(matches) == 1, (
        "collect_service_health must expose one generic social_narrative service card"
    )
    return matches[0]


def test_memory_cards_catalog_declares_social_narrative_as_shared_client() -> None:
    spec = DEFAULT_DATABASE_CATALOG.get("memory_cards")

    assert spec.owner == "services.memory.card_store"
    assert spec.clients == (
        "services.memory.card_store",
        "services.social_narrative",
    )


@pytest.mark.asyncio
async def test_health_default_disabled_is_clear_and_not_a_top_alert(tmp_path) -> None:
    plugin = _Plugin(configured_enabled=False, allowed_group_ids=set())
    payload = await collect_service_health(
        ctx=SimpleNamespace(storage_dir=tmp_path, bus=_Bus(plugin)),
    )

    service = _social_item(payload)
    assert service["status"] == "ok"
    assert service["meta"] == {
        "enabled": False,
        "allowed_group_ids": [],
        "entities": 0,
        "active_experiences": 0,
        "invalidated_experiences": 0,
    }
    assert all(alert.get("source") != "social_narrative" for alert in payload["alerts"])


@pytest.mark.asyncio
async def test_health_enabled_with_empty_allowlist_stays_fail_closed(tmp_path) -> None:
    plugin = _Plugin(configured_enabled=True, allowed_group_ids=set())
    payload = await collect_service_health(
        ctx=SimpleNamespace(storage_dir=tmp_path, bus=_Bus(plugin)),
    )

    service = _social_item(payload)
    assert service["status"] == "ok"
    assert service["meta"]["enabled"] is True
    assert service["meta"]["allowed_group_ids"] == []
    assert all(alert.get("source") != "social_narrative" for alert in payload["alerts"])


@pytest.mark.asyncio
async def test_health_enabled_allowlist_without_store_is_warning(tmp_path) -> None:
    plugin = _Plugin(configured_enabled=True, allowed_group_ids={"200"})
    payload = await collect_service_health(
        ctx=SimpleNamespace(storage_dir=tmp_path, bus=_Bus(plugin)),
    )

    service = _social_item(payload)
    assert service["status"] == "warning"
    assert service["meta"]["enabled"] is True
    assert service["meta"]["allowed_group_ids"] == ["200"]
    assert "store" in service["detail"].lower()


@pytest.mark.asyncio
async def test_health_uses_plugin_store_stats_without_opening_database(tmp_path) -> None:
    store = _StatsStore()
    plugin = _Plugin(
        configured_enabled=True,
        allowed_group_ids={"200", "201"},
        store=store,
    )
    payload = await collect_service_health(
        ctx=SimpleNamespace(storage_dir=tmp_path, bus=_Bus(plugin)),
    )

    service = _social_item(payload)
    assert service["status"] == "ok"
    assert service["meta"] == {
        "enabled": True,
        "allowed_group_ids": ["200", "201"],
        "entities": 2,
        "active_experiences": 3,
        "invalidated_experiences": 1,
    }
    assert store.calls == 1


@pytest.mark.asyncio
async def test_health_can_read_stats_from_context_store_without_plugin(tmp_path) -> None:
    store = _StatsStore(entities=4, active_experiences=6, invalidated_experiences=2)
    payload = await collect_service_health(
        ctx=SimpleNamespace(
            storage_dir=tmp_path,
            bus=_Bus(),
            social_narrative_store=store,
        ),
    )

    service = _social_item(payload)
    assert service["status"] == "ok"
    assert service["meta"] == {
        "enabled": False,
        "allowed_group_ids": [],
        "entities": 4,
        "active_experiences": 6,
        "invalidated_experiences": 2,
    }
    assert store.calls == 1
