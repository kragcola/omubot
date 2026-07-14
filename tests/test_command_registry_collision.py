"""RED contracts for fail-fast, atomic command registry collision checks."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kernel.bus import PluginBus
from kernel.types import AmadeusPlugin, Command
from services.command import CommandDispatcher
from services.health import collect_service_health
from services.plugin_state import PluginStateStore
from services.plugin_toggle import PluginToggleService
from services.tools.registry import ToolRegistry


class _CommandOwner(AmadeusPlugin):
    """Mutable command owner exercising only the public registry API."""

    def __init__(self, owner: str, commands: list[Command]) -> None:
        super().__init__()
        self.name = owner
        self.enabled = True
        self.commands = commands

    def register_commands(self) -> list[Command]:
        return list(self.commands)


class _RollbackFailingCommandRegistry:
    """Transactional registry that fails primary prepare and state restore."""

    def __init__(self) -> None:
        self.prepare_calls = 0
        self.commit_calls = 0
        self.restore_calls = 0
        self._active_state: object = object()

    def prepare_refresh(self) -> object:
        self.prepare_calls += 1
        if self.prepare_calls == 2:
            raise ValueError("primary prepare failure")
        return object()

    def commit_refresh(self, prepared: object) -> None:
        self.commit_calls += 1
        self._active_state = prepared

    def snapshot_state(self) -> object:
        return self._active_state

    def restore_state(self, snapshot: object) -> None:
        del snapshot
        self.restore_calls += 1
        raise RuntimeError("rollback restore failure")


class _UnregisterFailingCommandRegistry:
    """Transactional registry that fails one unregister prepare only."""

    def __init__(self) -> None:
        self.prepare_calls = 0
        self.commit_calls = 0
        self.restore_calls = 0
        self._active_state: object = object()

    def prepare_refresh(self) -> object:
        self.prepare_calls += 1
        if self.prepare_calls == 2:
            raise ValueError("unregister prepare failure")
        return object()

    def commit_refresh(self, prepared: object) -> None:
        self.commit_calls += 1
        self._active_state = prepared

    def snapshot_state(self) -> object:
        return self._active_state

    def restore_state(self, snapshot: object) -> None:
        self.restore_calls += 1
        self._active_state = snapshot


def _command(
    name: str,
    *,
    aliases: list[str] | None = None,
    pattern: str = "",
) -> Command:
    return Command(
        name=name,
        aliases=list(aliases or []),
        pattern=pattern,
        handler=AsyncMock(),
    )


def _dispatcher(*owners: _CommandOwner) -> CommandDispatcher:
    bus = PluginBus()
    for owner in owners:
        bus.register(owner)
    return CommandDispatcher(bus)


def test_refresh_rejects_root_name_collision_with_both_owners_in_error() -> None:
    first = _CommandOwner("owner_a", [_command("shared")])
    second = _CommandOwner("owner_b", [_command("shared")])

    with pytest.raises(ValueError) as exc_info:
        _dispatcher(first, second)

    message = str(exc_info.value)
    assert "shared" in message
    assert "owner_a" in message
    assert "owner_b" in message


def test_refresh_rejects_root_name_alias_collision_with_both_owners_in_error() -> None:
    first = _CommandOwner("owner_a", [_command("shared")])
    second = _CommandOwner("owner_b", [_command("other", aliases=["shared"])])

    with pytest.raises(ValueError) as exc_info:
        _dispatcher(first, second)

    message = str(exc_info.value)
    assert "shared" in message
    assert "owner_a" in message
    assert "owner_b" in message


def test_refresh_rejects_case_normalized_alias_collision_with_both_owners_in_error() -> None:
    first = _CommandOwner("owner_a", [_command("first", aliases=["Shared"])])
    second = _CommandOwner("owner_b", [_command("second", aliases=["sHaReD"])])

    with pytest.raises(ValueError) as exc_info:
        _dispatcher(first, second)

    message = str(exc_info.value)
    assert "shared" in message.lower()
    assert "owner_a" in message
    assert "owner_b" in message


@pytest.mark.parametrize(
    ("candidate_name", "candidate_aliases"),
    [
        pytest.param("foo。", [], id="root-cjk-punctuation"),
        pytest.param("foo!", [], id="root-ascii-punctuation"),
        pytest.param("other", ["foo！"], id="alias-cjk-punctuation"),
        pytest.param("other", ["foo?"], id="alias-ascii-punctuation"),
    ],
)
def test_refresh_rejects_trailing_punctuation_canonical_token_collision(
    candidate_name: str,
    candidate_aliases: list[str],
) -> None:
    existing = _CommandOwner("existing_owner", [_command("foo")])
    candidate = _CommandOwner(
        "candidate_owner",
        [_command(candidate_name, aliases=candidate_aliases)],
    )

    with pytest.raises(ValueError) as exc_info:
        _dispatcher(existing, candidate)

    message = str(exc_info.value).lower()
    assert "token='foo'" in message
    assert existing.name in message
    assert candidate.name in message


def test_refresh_rejects_identical_regex_pattern_with_both_owners_in_error() -> None:
    pattern = r"^shared(?:\s|$)"
    first = _CommandOwner("owner_a", [_command("first", pattern=pattern)])
    second = _CommandOwner("owner_b", [_command("second", pattern=pattern)])

    with pytest.raises(ValueError) as exc_info:
        _dispatcher(first, second)

    message = str(exc_info.value)
    assert pattern in message
    assert "owner_a" in message
    assert "owner_b" in message


def test_refresh_rejects_case_only_regex_collision_under_ignorecase() -> None:
    lower_pattern = r"^foo(?:\s|$)"
    upper_pattern = r"^FOO(?:\s|$)"
    lower_owner = _CommandOwner(
        "lower_owner",
        [_command("lower", pattern=lower_pattern)],
    )
    upper_owner = _CommandOwner(
        "upper_owner",
        [_command("upper", pattern=upper_pattern)],
    )

    with pytest.raises(ValueError) as exc_info:
        _dispatcher(lower_owner, upper_owner)

    message = str(exc_info.value)
    assert lower_pattern in message
    assert upper_pattern in message
    assert lower_owner.name in message
    assert upper_owner.name in message


@pytest.mark.parametrize("pattern_first", [True, False], ids=["pattern-first", "literal-first"])
def test_refresh_rejects_regex_matching_other_owner_literal_root_in_either_order(
    pattern_first: bool,
) -> None:
    pattern = r"^shared(?:\s|$)"
    pattern_owner = _CommandOwner("pattern_owner", [_command("patterned", pattern=pattern)])
    literal_owner = _CommandOwner("literal_owner", [_command("shared")])
    owners = (
        (pattern_owner, literal_owner)
        if pattern_first
        else (literal_owner, pattern_owner)
    )

    with pytest.raises(ValueError) as exc_info:
        _dispatcher(*owners)

    message = str(exc_info.value)
    assert pattern in message
    assert "shared" in message
    assert "pattern_owner" in message
    assert "literal_owner" in message


def test_failed_refresh_preserves_previous_command_snapshot_atomically() -> None:
    first = _CommandOwner("owner_a", [_command("first")])
    second = _CommandOwner("owner_b", [_command("second")])
    dispatcher = _dispatcher(first, second)
    before = dispatcher.commands

    second.commands = [_command("first")]

    with pytest.raises(ValueError):
        dispatcher.refresh()

    after = dispatcher.commands
    assert after.keys() == before.keys()
    assert all(after[token] is command for token, command in before.items())
    assert dispatcher.is_known("/first") is True
    assert dispatcher.is_known("/second") is True


def test_runtime_enable_command_collision_returns_error_and_rolls_back(
    tmp_path: Path,
) -> None:
    existing_command = _command("shared")
    existing = _CommandOwner("existing_owner", [existing_command])
    candidate = _CommandOwner("candidate_owner", [_command("shared")])
    candidate.enabled = False
    bus = PluginBus()
    bus.register(existing)
    bus.register(candidate)
    dispatcher = CommandDispatcher(bus)
    before = dispatcher.commands
    state_store = PluginStateStore(tmp_path / "plugin-state.json")
    state_store.set_enabled(candidate.name, False)
    service = PluginToggleService(
        bus=bus,
        tool_registry=ToolRegistry(),
        plugin_state_store=state_store,
        is_locked=PluginBus.is_plugin_locked,
        serialize_plugin=lambda plugin: {
            "name": plugin.name,
            "enabled": plugin.enabled,
        },
    )

    payload = service.toggle(candidate.name, True)

    assert payload["ok"] is False
    error = str(payload["error"])
    assert "shared" in error
    assert existing.name in error
    assert candidate.name in error
    assert candidate.enabled is False
    assert state_store.get(candidate.name) is False
    after = dispatcher.commands
    assert after.keys() == before.keys()
    assert all(after[token] is command for token, command in before.items())


def test_toggle_does_not_repeat_bus_rollback_after_restore_failure(
    tmp_path: Path,
) -> None:
    candidate = _CommandOwner("candidate_owner", [])
    candidate.enabled = False
    bus = PluginBus()
    bus.register(candidate)
    registry = _RollbackFailingCommandRegistry()
    bus.bind_command_registry(registry)
    state_store = PluginStateStore(tmp_path / "plugin-state.json")
    state_store.set_enabled(candidate.name, False)
    service = PluginToggleService(
        bus=bus,
        tool_registry=ToolRegistry(),
        plugin_state_store=state_store,
        is_locked=PluginBus.is_plugin_locked,
        serialize_plugin=lambda plugin: {
            "name": plugin.name,
            "enabled": plugin.enabled,
        },
    )

    payload = service.toggle(candidate.name, True)

    assert payload["ok"] is False
    error = str(payload["error"])
    assert "primary prepare failure" in error
    assert "运行态回滚失败" not in error
    assert registry.prepare_calls == 2
    assert registry.commit_calls == 1
    assert registry.restore_calls == 2
    assert candidate.enabled is False
    assert state_store.get(candidate.name) is False


def test_direct_bus_enable_command_collision_rolls_back_atomically() -> None:
    existing = _CommandOwner("existing_owner", [_command("shared")])
    candidate = _CommandOwner("candidate_owner", [_command("shared")])
    candidate.enabled = False
    bus = PluginBus()
    bus.register(existing)
    bus.register(candidate)
    dispatcher = CommandDispatcher(bus)
    before = dispatcher.commands

    with pytest.raises(ValueError) as exc_info:
        bus.set_plugin_enabled(candidate.name, True)

    message = str(exc_info.value)
    assert "shared" in message
    assert existing.name in message
    assert candidate.name in message
    assert candidate.enabled is False
    health = {item["name"]: item for item in bus.plugin_health()}
    assert health[candidate.name]["enabled"] is False
    assert health[candidate.name]["state"] == "disabled"
    after = dispatcher.commands
    assert after.keys() == before.keys()
    assert all(after[token] is command for token, command in before.items())


def test_direct_bus_enable_preserves_primary_prepare_error_when_restore_fails() -> None:
    candidate = _CommandOwner("candidate_owner", [])
    candidate.enabled = False
    bus = PluginBus()
    bus.register(candidate)
    registry = _RollbackFailingCommandRegistry()
    bus.bind_command_registry(registry)

    with pytest.raises(ValueError, match="primary prepare failure") as exc_info:
        bus.set_plugin_enabled(candidate.name, True)

    assert type(exc_info.value) is ValueError
    assert registry.prepare_calls == 2
    assert registry.commit_calls == 1
    assert registry.restore_calls == 2
    assert candidate.enabled is False
    health = {item["name"]: item for item in bus.plugin_health()}
    assert health[candidate.name]["enabled"] is False
    assert health[candidate.name]["state"] == "disabled"


def test_register_command_collision_rolls_back_bus_and_dispatcher_atomically() -> None:
    existing_command = _command("shared")
    existing = _CommandOwner("existing_owner", [existing_command])
    candidate = _CommandOwner("candidate_owner", [_command("shared")])
    bus = PluginBus()
    bus.register(existing)
    dispatcher = CommandDispatcher(bus)
    before = dispatcher.commands

    with pytest.raises(ValueError):
        bus.register(candidate)

    assert candidate not in bus.plugins
    assert bus.get_plugin(candidate.name) is None
    health_names = {item["name"] for item in bus.plugin_health()}
    assert candidate.name not in health_names
    after = dispatcher.commands
    assert after.keys() == before.keys()
    assert after["shared"] is existing_command


def test_unregister_prepare_failure_restores_bus_and_dispatcher_atomically() -> None:
    alpha_command = _command("alpha")
    owner = _CommandOwner("alpha", [alpha_command])
    bus = PluginBus()
    bus.register(owner)
    dispatcher = CommandDispatcher(bus)
    before = dispatcher.commands["alpha"]
    registry = _UnregisterFailingCommandRegistry()
    bus.bind_command_registry(registry)
    registry_before = registry.snapshot_state()

    with pytest.raises(ValueError, match="unregister prepare failure") as exc_info:
        bus.unregister(owner.name)

    assert type(exc_info.value) is ValueError
    assert registry.prepare_calls == 2
    assert registry.commit_calls == 1
    assert registry.restore_calls == 1
    assert registry.snapshot_state() is registry_before
    assert owner in bus.plugins
    assert bus.get_plugin(owner.name) is owner
    health = {item["name"]: item for item in bus.plugin_health()}
    assert owner.name in health
    assert dispatcher.is_known("/alpha") is True
    assert dispatcher.commands["alpha"] is before
    assert before is alpha_command


def test_dispatcher_health_snapshot_tracks_refresh_failure_and_recovery() -> None:
    first = _CommandOwner("owner_a", [_command("first")])
    second = _CommandOwner("owner_b", [_command("second")])
    dispatcher = _dispatcher(first, second)
    health_snapshot = getattr(dispatcher, "health_snapshot", None)

    assert callable(health_snapshot)
    healthy = health_snapshot()
    assert healthy["status"] == "ok"
    assert healthy["command_count"] == 2

    second.commands = [_command("first")]
    with pytest.raises(ValueError):
        dispatcher.refresh()

    failed = health_snapshot()
    assert failed["status"] == "error"
    assert failed["failed_refreshes"] == 1
    assert "first" in failed["last_error"]
    assert first.name in failed["last_error"]
    assert second.name in failed["last_error"]
    assert failed["command_count"] == 2

    second.commands = [_command("second")]
    dispatcher.refresh()

    recovered = health_snapshot()
    assert recovered["status"] == "ok"
    assert recovered["command_count"] == 2


@pytest.mark.asyncio
async def test_service_health_exposes_active_command_registry_error() -> None:
    first = _CommandOwner("owner_a", [_command("first")])
    second = _CommandOwner("owner_b", [_command("second")])
    bus = PluginBus()
    bus.register(first)
    bus.register(second)
    dispatcher = CommandDispatcher(bus)

    second.commands = [_command("first")]
    with pytest.raises(ValueError):
        dispatcher.refresh()

    payload = await collect_service_health(ctx=SimpleNamespace(bus=bus))
    plugin_bus = next(
        item for item in payload["services"] if item["id"] == "plugin_bus"
    )

    assert plugin_bus["status"] == "error"
    detail = str(plugin_bus["detail"]).lower()
    assert "command" in detail
    assert "registry" in detail
    command_health = plugin_bus["meta"]["command_registry"]
    assert command_health["status"] == "error"
    assert command_health["failed_refreshes"] == 1
    assert first.name in command_health["last_error"]
    assert second.name in command_health["last_error"]
