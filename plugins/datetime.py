"""DateTimePlugin: 时间查询工具。"""

from __future__ import annotations

from kernel.types import AmadeusPlugin, PluginContext
from services.tools.base import Tool
from services.tools.datetime_tool import DateTimeTool


class DateTimePlugin(AmadeusPlugin):
    name = "datetime"
    description = "时间查询工具：获取当前日期、时间、节假日、日程信息"
    version = "1.1.0"
    priority = 1

    async def on_startup(self, ctx: PluginContext) -> None:
        self._schedule_store = ctx.schedule_store

    def register_tools(self) -> list[Tool]:
        return [DateTimeTool(schedule_store=self._schedule_store)]
