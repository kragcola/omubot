"""RED contracts for command parsing, permissions, and Admin metadata."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nonebot
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.routes.api.plugins import create_plugins_router
from kernel.bus import PluginBus
from kernel.types import AmadeusPlugin, Command
from services.command import CommandDispatcher


class _CommandBus:
    def __init__(self, commands: list[Command]) -> None:
        self._commands = commands

    def collect_commands(self) -> list[Command]:
        return list(self._commands)


def _command_context(*, admins: dict[str, str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(admins=admins or {}))


@pytest.mark.parametrize("separator", ["\t", "\n", "\r\n"])
def test_subcommand_arguments_preserve_arbitrary_whitespace(separator: str) -> None:
    root_handler = AsyncMock()
    sub_handler = AsyncMock()
    dispatcher = CommandDispatcher(
        _CommandBus(
            [
                Command(
                    name="food",
                    handler=root_handler,
                    sub_commands=[Command(name="like", handler=sub_handler)],
                )
            ]
        )
    )
    bot = SimpleNamespace(send=AsyncMock())

    matched = asyncio.run(
        dispatcher.dispatch(
            bot,
            SimpleNamespace(),
            f"/food{separator}like{separator}辣的",
            is_private=False,
            user_id="u1",
            group_id="g1",
            plugin_ctx=_command_context(),
        )
    )

    assert matched is True
    sub_handler.assert_awaited_once()
    assert sub_handler.await_args is not None
    assert sub_handler.await_args.args[0].args == "辣的"


def test_admin_guard_accepts_config_admins_and_nonebot_superusers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = AsyncMock()
    dispatcher = CommandDispatcher(
        _CommandBus([Command(name="ops", handler=handler, admin_only=True)])
    )
    monkeypatch.setattr(
        nonebot,
        "get_driver",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                superusers={"super-user"},
                SUPERUSERS={"super-user"},
            )
        ),
    )
    bot = SimpleNamespace(send=AsyncMock())

    for user_id in ("config-admin", "super-user"):
        matched = asyncio.run(
            dispatcher.dispatch(
                bot,
                SimpleNamespace(),
                "/ops",
                is_private=False,
                user_id=user_id,
                group_id="g1",
                plugin_ctx=_command_context(admins={"config-admin": "管理员"}),
            )
        )
        assert matched is True

    assert handler.await_count == 2


class _MetadataPlugin(AmadeusPlugin):
    name = "metadata_surface"
    description = "command metadata fixture"
    version = "1.0.0"
    priority = 10
    settings_schema = {  # noqa: RUF012 - test fixture metadata
        "type": "object",
        "properties": {"enabled": {"type": "boolean"}},
    }

    def register_commands(self) -> list[Command]:
        return [
            Command(
                name="ops",
                handler=AsyncMock(),
                description="operations",
                usage="/ops <subcommand>",
                pattern=r"^ops(?:\s|$)",
                aliases=["o"],
                admin_only=True,
                private_only=True,
                passthrough_unknown=True,
                sub_commands=[
                    Command(
                        name="status",
                        handler=AsyncMock(),
                        aliases=["s"],
                        description="status",
                    )
                ],
            )
        ]


def _metadata_payload() -> dict[str, object]:
    # The Admin detail endpoint resolves the owning module by name.  Pytest's
    # import mode can use a non-package module key, so keep this fixture
    # explicitly importable for the endpoint's existing discovery contract.
    sys.modules.setdefault(_MetadataPlugin.__module__, sys.modules[__name__])
    bus = PluginBus()
    bus.register(_MetadataPlugin())
    app = FastAPI()
    app.include_router(create_plugins_router(bus=bus), prefix="/api/admin")
    response = TestClient(app).get("/api/admin/plugins/metadata_surface")
    assert response.status_code == 200
    payload = response.json()
    assert "error" not in payload, payload
    settings = payload.get("settings", {})
    commands = settings.get("commands", []) if isinstance(settings, dict) else []
    if not commands:
        commands = payload.get("commands", [])
    assert commands, payload
    return commands[0]


def test_admin_command_metadata_includes_aliases_subcommands_and_inherited_flags() -> None:
    root = _metadata_payload()

    assert root["name"] == "ops"
    assert root["aliases"] == ["o"]
    assert root["pattern"] == r"^ops(?:\s|$)"
    assert root["passthrough_unknown"] is True
    subcommands = root.get("subcommands", root.get("sub_commands"))
    assert isinstance(subcommands, list)
    assert subcommands and subcommands[0]["name"] == "status"
    assert subcommands[0]["aliases"] == ["s"]
    # The child declares no gates, but inherits both root gates in the control
    # plane so Admin users can see the effective permission contract.
    assert subcommands[0]["admin_only"] is True
    assert subcommands[0]["private_only"] is True


def test_command_help_propagates_root_gates_to_subcommands() -> None:
    root = _MetadataPlugin().register_commands()[0]

    help_text = root.format_help()
    child_line = next(line for line in help_text.splitlines() if "/ops status" in line)

    assert "仅管理员" in child_line
    assert "仅私聊" in child_line


def test_declared_alias_bypasses_root_pattern_while_pattern_adds_entrypoint() -> None:
    """Aliases remain executable when a command also declares a pattern."""

    handler = AsyncMock()
    dispatcher = CommandDispatcher(
        _CommandBus(
            [
                Command(
                    name="ops",
                    handler=handler,
                    aliases=["o"],
                    pattern=r"^(?:ops|operate)(?:\s|$)",
                )
            ]
        )
    )
    bot = SimpleNamespace(send=AsyncMock())
    event = SimpleNamespace()

    alias_matched = asyncio.run(
        dispatcher.dispatch(
            bot,
            event,
            "/o alias-arg",
            is_private=False,
            user_id="u1",
            group_id="g1",
            plugin_ctx=_command_context(),
        )
    )
    pattern_matched = asyncio.run(
        dispatcher.dispatch(
            bot,
            event,
            "/operate pattern-arg",
            is_private=False,
            user_id="u1",
            group_id="g1",
            plugin_ctx=_command_context(),
        )
    )

    assert alias_matched is True
    assert pattern_matched is True
    assert handler.await_count == 2
    assert [call.args[0].args for call in handler.await_args_list] == [
        "alias-arg",
        "pattern-arg",
    ]
    bot.send.assert_not_awaited()
