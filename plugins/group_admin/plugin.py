"""GroupAdminPlugin: 群管理工具（禁言、头衔、发送群消息）。"""

from __future__ import annotations

import nonebot
from pydantic import BaseModel

from kernel.config import load_plugin_config
from kernel.types import AmadeusPlugin, PluginContext
from services.tools.base import Tool
from services.tools.group_admin import MuteUserTool, SendGroupMsgTool, SetTitleTool


class GroupAdminConfig(BaseModel):
    """群管理工具开关配置。"""

    mute_enabled: bool = True
    set_title_enabled: bool = True
    send_group_msg_enabled: bool = True
    default_mute_seconds: int = 60
    max_mute_seconds: int = 2592000


class GroupAdminPlugin(AmadeusPlugin):
    name = "group_admin"
    description = "群管理工具：禁言用户、设置头衔、发送群消息（需 SUPERUSER 权限）"
    version = "1.1.1"
    priority = 1

    async def on_startup(self, ctx: PluginContext) -> None:
        self._config = load_plugin_config("plugins/group_admin/config.default.json", GroupAdminConfig)
        self._superusers = set(ctx.config.admins.keys()) | nonebot.get_driver().config.superusers

    def register_tools(self) -> list[Tool]:
        tools: list[Tool] = []
        if self._config.mute_enabled:
            tools.append(
                MuteUserTool(
                    self._superusers,
                    default_duration=self._config.default_mute_seconds,
                    max_duration=self._config.max_mute_seconds,
                )
            )
        if self._config.send_group_msg_enabled:
            tools.append(SendGroupMsgTool(self._superusers))
        if self._config.set_title_enabled:
            tools.append(SetTitleTool(self._superusers))
        return tools
