"""DebugCommandPlugin: admin debug commands — /plugins list.

Admin-only slash commands for inspecting bot state at runtime.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from kernel.types import AmadeusPlugin, PluginContext

_log = logger.bind(channel="command")


class DebugCommandPlugin(AmadeusPlugin):
    name = "debug_commands"
    description = "调试指令增强：/plugins 查看已加载插件列表，/version 版本检查"
    version = "1.0.0"
    priority = 300  # After all business plugins, before third-party
    author = "Omubot"

    def __init__(self) -> None:
        super().__init__()
        self._ctx: PluginContext | None = None

    async def on_startup(self, ctx: PluginContext) -> None:
        self._ctx = ctx

    def register_commands(self) -> list:
        from kernel.types import Command
        return [
            Command(
                name="plugins",
                handler=self._handle_plugins,
                description="列出所有已加载插件（名称、版本、开发者、简介）",
                usage="/plugins",
            ),
            Command(
                name="version",
                handler=self._handle_version,
                description="查看 Omubot 版本并检查 GitHub 更新",
                usage="/version",
            ),
        ]

    async def _handle_plugins(self, cmd_ctx: Any) -> None:
        from nonebot.adapters.onebot.v11 import Message

        ctx = self._ctx
        if ctx is None:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("系统未就绪"))
            return

        if cmd_ctx.user_id not in ctx.admins:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("无权限"))
            return

        bus = ctx.bus
        if bus is None:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("PluginBus 不可用"))
            return

        plugins = bus.plugins
        if not plugins:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("（无已加载插件）"))
            return

        lines: list[str] = [f"已加载 {len(plugins)} 个插件：", ""]
        for p in plugins:
            status = "启用" if p.enabled else "禁用"
            author = p.author if p.author else "—"
            desc = p.description if p.description else "—"
            lines.append(
                f"[{status}] {p.name} v{p.version}"
            )
            lines.append(f"  开发者: {author}")
            lines.append(f"  简介: {desc}")

        reply = "\n".join(lines)
        if len(reply) > 2000:
            reply = reply[:2000] + "\n…(截断)"

        await cmd_ctx.bot.send(cmd_ctx.event, Message(reply))
        _log.info("plugins listed | by={} count={}", cmd_ctx.user_id, len(plugins))

    async def _handle_version(self, cmd_ctx: Any) -> None:
        from nonebot.adapters.onebot.v11 import Message

        from services.version import GITHUB_REPO, VERSION, fetch_latest_release, parse_semver

        local = parse_semver(VERSION)
        lines = [f"Omubot v{VERSION}", f"GitHub: https://github.com/{GITHUB_REPO}"]

        release = await fetch_latest_release()
        if release is None:
            lines.append("")
            lines.append("（无法连接 GitHub，未检查更新）")
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
        await cmd_ctx.bot.send(cmd_ctx.event, Message(reply))
        _log.info("version checked | by={} local={}", cmd_ctx.user_id, VERSION)
