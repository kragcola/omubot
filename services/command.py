"""Command dispatcher — matches /slash commands and routes them to plugin handlers.

Registered as a system service so any plugin can register commands via
register_commands() and they will be dispatched before LLM processing.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from kernel.types import Command, CommandContext

_log = logger.bind(channel="command")


class CommandDispatcher:
    """Collects commands from PluginBus and dispatches incoming messages."""

    def __init__(self, bus: Any) -> None:
        self._commands: dict[str, Command] = {}
        self._load(bus)

    def _load(self, bus: Any) -> None:
        for cmd in bus.collect_commands():
            if cmd.name in self._commands:
                _log.warning("duplicate command name, overwriting | name={}", cmd.name)
            self._commands[cmd.name] = cmd
            for alias in cmd.aliases:
                if alias in self._commands:
                    _log.warning("duplicate command alias, overwriting | alias={}", alias)
                self._commands[alias] = cmd
        _log.info("commands loaded | count={}", len(self._commands))

    @property
    def commands(self) -> dict[str, Command]:
        return dict(self._commands)

    async def dispatch(
        self,
        bot: Any,
        event: Any,
        text: str,
        *,
        is_private: bool,
        user_id: str,
        group_id: str | None,
    ) -> bool:
        """Parse and execute a command from message text.

        Returns True if a command matched and was executed (caller should stop).
        Returns False if no command matched (caller should continue to LLM).
        """
        # Find first "/" — command may be preceded by noise (e.g. emoji, stickers)
        slash_idx = text.find("/")
        if slash_idx == -1:
            return False

        stripped = text[slash_idx + 1:]
        if not stripped:
            return False

        # Split into command name and args: "/debug foo bar" → ("debug", "foo bar")
        parts = stripped.split(maxsplit=1)
        name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        cmd = self._commands.get(name)
        if cmd is None:
            return False

        cmd_ctx = CommandContext(
            bot=bot,
            event=event,
            args=args,
            is_private=is_private,
            user_id=user_id,
            group_id=group_id,
        )

        try:
            await cmd.handler(cmd_ctx)
        except Exception:
            _log.warning("command error | name={}", name, exc_info=True)

        return True
