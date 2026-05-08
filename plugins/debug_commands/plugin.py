"""DebugCommandPlugin: admin debug commands — /plugins list.

Admin-only slash commands for inspecting bot state at runtime.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from pydantic import BaseModel

from kernel.config import load_plugin_config
from kernel.types import AmadeusPlugin, PluginContext

_log = logger.bind(channel="command")


class DebugCommandConfig(BaseModel):
    """聊天内调试指令配置。"""

    plugins_command_enabled: bool = True
    version_command_enabled: bool = True
    max_reply_chars: int = 2000
    check_github_updates: bool = True


class DebugCommandPlugin(AmadeusPlugin):
    name = "debug_commands"
    description = "调试指令增强：/plugins 查看已加载插件列表，/version 版本检查"
    version = "1.3.1"
    priority = 300  # After all business plugins, before third-party

    def __init__(self) -> None:
        super().__init__()
        self._ctx: PluginContext | None = None
        self._config = DebugCommandConfig()

    async def on_startup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        self._config = load_plugin_config("plugins/debug_commands/config.default.json", DebugCommandConfig)

    def register_commands(self) -> list:
        from kernel.types import Command
        commands: list[Command] = []
        if self._config.plugins_command_enabled:
            commands.append(Command(
                name="plugins",
                handler=self._handle_plugins,
                description="列出所有已加载插件（名称、版本、开发者、简介）",
                usage="/plugins",
                aliases=["p", "plg", "插件"],
                admin_only=True,
            ))
        if self._config.version_command_enabled:
            commands.append(Command(
                name="version",
                handler=self._handle_version,
                description="查看 Omubot 版本并检查 GitHub 更新",
                usage="/version",
            ))
        return commands

    async def _handle_plugins(self, cmd_ctx: Any) -> None:
        from nonebot.adapters.onebot.v11 import Message

        bus = cmd_ctx.plugin_ctx.bus
        if bus is None:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("PluginBus 不可用"))
            return

        plugins = sorted(bus.plugins, key=lambda p: (not p.enabled, p.priority))
        if not plugins:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("（无已加载插件）"))
            return

        enabled_count = sum(1 for p in plugins if p.enabled)
        disabled_count = len(plugins) - enabled_count
        lines: list[str] = [f"插件列表（启用 {enabled_count} / 禁用 {disabled_count}）：", ""]
        for p in plugins:
            status = "启用" if p.enabled else "禁用"
            author = p.author if p.author else "—"
            desc = p.description if p.description else "—"
            lines.append(f"[{status}] [{p.name} v{p.version}] 开发者：{author}")
            lines.append(f"  简介：{desc}")

        reply = "\n".join(lines)
        if len(reply) > self._config.max_reply_chars:
            reply = reply[:self._config.max_reply_chars] + "\n…(截断)"

        await cmd_ctx.bot.send(cmd_ctx.event, Message(reply))
        _log.info("plugins listed | by={} count={}", cmd_ctx.user_id, len(plugins))

    async def _handle_version(self, cmd_ctx: Any) -> None:
        from nonebot.adapters.onebot.v11 import Message

        from services.version import GITHUB_REPO, VERSION, fetch_latest_release, parse_semver

        local = parse_semver(VERSION)
        lines = [f"Omubot v{VERSION}", f"GitHub: https://github.com/{GITHUB_REPO}"]

        release = await fetch_latest_release() if self._config.check_github_updates else None
        if release is None:
            lines.append("")
            if self._config.check_github_updates:
                lines.append("（无法连接 GitHub，未检查更新）")
            else:
                lines.append("（已关闭 GitHub 更新检查）")
        else:
            remote_tag: str = release.get("tag_name", "unknown")
            remote_ver = parse_semver(remote_tag)
            published: str = release.get("published_at", "")[:10]
            body: str = release.get("body", "") or ""

            if remote_ver > local:
                lines.append(f"最新版本: {remote_tag}（发布于 {published}）")
                lines.append("**有可用更新！**")
                if body:
                    first_line = body.strip().split("\n")[0]
                    lines.append(f"更新摘要: {first_line}")
            elif remote_ver == local:
                lines.append(f"最新版本: {remote_tag}（发布于 {published}）")
                lines.append("已是最新版本")
            else:
                lines.append(f"GitHub 最新: {remote_tag}（发布于 {published}）")
                lines.append("本地版本领先（开发版）")

        reply = "\n".join(lines)
        if len(reply) > self._config.max_reply_chars:
            reply = reply[:self._config.max_reply_chars] + "\n…(截断)"
        await cmd_ctx.bot.send(cmd_ctx.event, Message(reply))
        _log.info("version checked | by={} local={}", cmd_ctx.user_id, VERSION)
