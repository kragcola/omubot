"""RED contract for moving debug command ownership out of ChatPlugin."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from kernel.types import Command
from plugins.chat.plugin import ChatPlugin
from plugins.debug_commands.plugin import DebugCommandPlugin

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CHAT_DEBUG_HANDLER_NAMES = (
    "_handle_debug",
    "_handle_authority",
    "_handle_debug_save",
    "_handle_debug_send",
    "_handle_debug_split",
)


def _manifest(plugin_name: str) -> dict[str, Any]:
    path = _REPO_ROOT / "plugins" / plugin_name / "plugin.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _walk_commands(commands: list[Command]) -> Iterator[Command]:
    for command in commands:
        yield command
        yield from _walk_commands(command.sub_commands)


def test_chat_plugin_no_longer_owns_commands_or_command_permission() -> None:
    assert ChatPlugin().register_commands() == []
    assert "command" not in _manifest("chat")["permissions"]


def test_debug_command_plugin_owns_the_complete_command_surface() -> None:
    roots = DebugCommandPlugin().register_commands()
    commands = {command.name: command for command in roots}

    assert len(roots) == 4
    assert set(commands) == {"debug", "authority", "plugins", "version"}

    authority = commands["authority"]
    assert authority.aliases == ["权限", "授权"]
    assert authority.require_args is False
    assert authority.passthrough_unknown is False
    assert authority.admin_only is True
    assert authority.hidden is True

    debug = commands["debug"]
    assert debug.aliases == []
    assert debug.require_args is False
    assert debug.passthrough_unknown is True
    assert debug.admin_only is True
    assert debug.hidden is True
    assert [command.name for command in debug.sub_commands] == ["save", "send", "split"]
    assert {
        command.name: (
            command.aliases,
            command.require_args,
            command.passthrough_unknown,
        )
        for command in debug.sub_commands
    } == {
        "save": (["保存", "收录", "添加表情"], False, False),
        "send": (["发", "发送"], False, False),
        "split": (["分段", "分割"], True, False),
    }


def test_debug_command_handlers_are_bound_to_debug_command_plugin() -> None:
    plugin = DebugCommandPlugin()
    commands = plugin.register_commands()

    for command in _walk_commands(commands):
        assert callable(command.handler), command.name
        assert getattr(command.handler, "__self__", None) is plugin, command.name

    for handler_name in _CHAT_DEBUG_HANDLER_NAMES:
        assert not hasattr(ChatPlugin, handler_name), handler_name


def test_debug_manifest_declares_its_expanded_command_responsibility() -> None:
    manifest = _manifest("debug_commands")

    assert "command" in manifest["permissions"]
    metadata = " ".join(
        [str(manifest.get("description", "")), *map(str, manifest.get("capabilities", []))]
    ).casefold()
    assert any(token in metadata for token in ("debug", "调试"))
    assert any(token in metadata for token in ("authority", "权限", "授权"))
