"""GroupAdminPlugin: 群管理工具（禁言、头衔、发送群消息）。"""

from __future__ import annotations

import nonebot

from kernel.types import AmadeusPlugin, PluginContext
from services.tools.base import Tool
from services.tools.group_admin import MuteUserTool, SendGroupMsgTool, SetTitleTool


class GroupAdminPlugin(AmadeusPlugin):
    name = "group_admin"
    description = "群管理工具：禁言用户、设置头衔、发送群消息（需 SUPERUSER 权限）"
    version = "1.0.0"
    priority = 1

    async def on_startup(self, ctx: PluginContext) -> None:
        self._superusers = set(ctx.config.admins.keys()) | nonebot.get_driver().config.superusers

    def register_tools(self) -> list[Tool]:
        return [
            MuteUserTool(self._superusers),
            SendGroupMsgTool(self._superusers),
            SetTitleTool(self._superusers),
        ]
