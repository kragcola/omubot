"""Command dispatcher — matches /slash commands and routes them to plugin handlers.

Registered as a system service so any plugin can register commands via
register_commands() and they will be dispatched before LLM processing.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from kernel.types import Command, RichCommandContext

_log = logger.bind(channel="command")
_TRAILING_COMMAND_PUNCT = "。，！？、；：.,!?;:"
_TRAILING_COMMAND_RE = re.compile(rf"[{re.escape(_TRAILING_COMMAND_PUNCT)}]+$")


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
        plugin_ctx: Any,
    ) -> bool:
        """Parse and execute a command from message text.

        Returns True if a command matched and was executed (caller should stop).
        Returns False if no command matched (caller should continue to LLM).
        """
        stripped_text = text.strip()
        if not stripped_text.startswith("/"):
            return False

        stripped = stripped_text[1:]
        if not stripped.strip():
            return False

        parts = stripped.split(maxsplit=1)
        name = _normalize_command_token(parts[0]).lower()
        args = parts[1] if len(parts) > 1 else ""

        if not name:
            return False

        cmd = self._commands.get(name)
        if cmd is None:
            return False

        return await self._dispatch_cmd(
            cmd, args, bot, event, is_private, user_id, group_id, plugin_ctx,
        )

    async def _dispatch_cmd(
        self,
        cmd: Command,
        args: str,
        bot: Any,
        event: Any,
        is_private: bool,
        user_id: str,
        group_id: str | None,
        plugin_ctx: Any,
        root_cmd: Command | None = None,
    ) -> bool:
        """Recursively dispatch a command, trying sub-commands first.

        Guard checks (admin_only, private_only, require_args) run before the
        handler.  Unknown sub-commands produce a helpful error listing available
        sub-commands.
        """
        if root_cmd is None:
            root_cmd = cmd

        args = args.strip()

        # ---- sub-command matching ----
        if cmd.sub_commands and args:
            first_word = _normalize_command_token(args.split(maxsplit=1)[0]).lower()
            sub = _find_sub(cmd.sub_commands, first_word)
            if sub is not None:
                sub_args = args.split(maxsplit=1)[1] if " " in args else ""
                return await self._dispatch_cmd(
                    sub, sub_args, bot, event, is_private,
                    user_id, group_id, plugin_ctx, root_cmd=root_cmd,
                )

            # Unknown sub-command
            if not cmd.passthrough_unknown:
                visible = [s for s in cmd.sub_commands if not s.hidden]
                names = ", ".join(f"/{root_cmd.name} {s.name}" for s in visible)
                await _send(bot, event, f"未知子命令。可用：{names}")
                return True
            # else: fall through to call the parent handler with original args

        # ---- guard checks (inherit admin/private from root) ----
        admins: dict = getattr(plugin_ctx.config, "admins", {})

        if (cmd.admin_only or root_cmd.admin_only) and user_id not in admins:
            await _send(bot, event, "无权限")
            return True
        if (cmd.private_only or root_cmd.private_only) and not is_private:
            await _send(bot, event, "请在私聊中使用此指令")
            return True
        if cmd.require_args and not args:
            await _send(bot, event, f"用法：{cmd.usage}")
            return True

        # ---- execute handler ----
        ctx = RichCommandContext(
            bot=bot,
            event=event,
            args=args,
            is_private=is_private,
            user_id=user_id,
            group_id=group_id,
            command=cmd,
            root_command=root_cmd,
            plugin_ctx=plugin_ctx,
        )

        try:
            await cmd.handler(ctx)
        except Exception:
            _log.warning("command_dispatch_failed | name={}", cmd.name, exc_info=True)
            await _send(bot, event, "指令执行失败，请稍后再试")

        return True


def _normalize_command_token(token: str) -> str:
    """Trim trailing accidental punctuation from a command token only."""
    cleaned = (token or "").strip()
    if not cleaned:
        return ""
    return _TRAILING_COMMAND_RE.sub("", cleaned)


def _find_sub(sub_commands: list[Command], word: str) -> Command | None:
    """Find a sub-command by name or alias. Returns None if not found."""
    for sub in sub_commands:
        if sub.name == word or word in sub.aliases:
            return sub
    return None


async def _send(bot: Any, event: Any, text: str) -> None:
    """Send a plain-text reply."""
    from nonebot.adapters.onebot.v11 import Message
    await bot.send(event, Message(text))
