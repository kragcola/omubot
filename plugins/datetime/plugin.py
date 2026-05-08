"""DateTimePlugin: 时间查询工具。"""

from __future__ import annotations

from pydantic import BaseModel

from kernel.config import load_plugin_config
from kernel.types import AmadeusPlugin, PluginContext
from services.tools.base import Tool
from services.tools.datetime_tool import DateTimeTool


class DateTimeConfig(BaseModel):
    """时间工具配置。"""

    timezone: str = "Asia/Shanghai"
    include_calendar_context: bool = True
    include_schedule: bool = True


class DateTimePlugin(AmadeusPlugin):
    name = "datetime"
    description = "时间查询工具：获取当前日期、时间、节假日、日程信息"
    version = "1.1.1"
    priority = 1

    async def on_startup(self, ctx: PluginContext) -> None:
        self._config = load_plugin_config("plugins/datetime/config.default.json", DateTimeConfig)
        self._schedule_store = ctx.schedule_store

    def register_tools(self) -> list[Tool]:
        return [
            DateTimeTool(
                schedule_store=self._schedule_store,
                timezone=self._config.timezone,
                include_calendar_context=self._config.include_calendar_context,
                include_schedule=self._config.include_schedule,
            )
        ]
