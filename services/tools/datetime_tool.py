"""时间工具：查询当前日期时间。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from plugins.schedule.calendar import get_day_context
from services.tools.base import Tool
from services.tools.context import ToolContext

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class DateTimeTool(Tool):
    def __init__(self, schedule_store: object | None = None) -> None:
        self._schedule_store = schedule_store

    @property
    def name(self) -> str:
        return "get_datetime"

    @property
    def description(self) -> str:
        return "获取当前的日期和时间。"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        now = datetime.now(tz=ZoneInfo("Asia/Shanghai"))
        weekday = _WEEKDAYS[now.weekday()]
        result = f"{now.strftime('%Y-%m-%d %H:%M:%S')} {weekday}"

        # Calendar context — holidays, special days, birthdays
        day_ctx = get_day_context(now)
        if day_ctx.holiday_name:
            result += f"\n今天正在放{day_ctx.holiday_name}假。"
        elif day_ctx.is_makeup_day:
            result += "\n今天是调休日，虽然是周末但要上课。"
        if day_ctx.special_day:
            result += f"\n今天是{day_ctx.special_day}。"
        for b in day_ctx.birthdays:
            result += f"\n今天是{b.name_cn}（{b.group}）的生日！"

        if self._schedule_store is not None:
            schedule = getattr(self._schedule_store, "current", None)
            if schedule is not None:
                slot = schedule.current_slot(now)
                if slot is not None:
                    result += f"\n你正在：{slot.activity}"
        return result
