"""CalendarContextPlugin: unified holiday/special-day/birthday provider."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel

from kernel.config import load_plugin_config
from kernel.types import AmadeusPlugin, PluginContext
from plugins.calendar_context.birthday_greeter import BirthdayGreeter
from plugins.calendar_context.runtime import (
    publish_calendar_service,
    set_calendar_self_names,
    unpublish_calendar_service,
)
from plugins.calendar_context.service import CalendarContextService


class CalendarContextConfig(BaseModel):
    enabled: bool = True
    timezone: str = "Asia/Shanghai"
    match_identity_aliases: bool = True
    builtin_birthdays_file: str = "birthdays.json"
    builtin_special_days_file: str = "special_days.json"
    builtin_years_dir: str = "years"
    extra_birthdays_files: list[str] = []
    extra_special_days_files: list[str] = []
    extra_year_files: list[str] = []
    auto_fetch_missing_year: bool = True
    auto_refresh_future_years: bool = False
    official_source_enabled: bool = True
    fallback_local_holiday_lib: bool = True
    cache_dir: str = "storage/plugins/calendar_context"
    write_back_plugin_data: bool = False
    official_fetch_timeout_s: float = 10.0
    official_url_template: str = "https://www.gov.cn/zhengce/zhengceku/{year}-11/12/content_holidays.htm"


class CalendarContextPlugin(AmadeusPlugin):
    name = "calendar_context"
    description = "统一管理节假日、调休日、特殊日与生日上下文"
    version = "1.0.0"
    priority = -5

    def __init__(self) -> None:
        self._service: CalendarContextService | None = None
        self._greeter: BirthdayGreeter | None = None
        self._bot: Any | None = None

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = load_plugin_config("plugins/calendar_context/config.default.json", CalendarContextConfig)
        if not cfg.enabled:
            if self._service is not None:
                unpublish_calendar_service(self._service)
            self._service = None
            self._greeter = None
            self._bot = None
            ctx.calendar_service = None
            ctx.birthday_greeter = None
            return

        service = CalendarContextService(
            timezone=cfg.timezone,
            match_identity_aliases=cfg.match_identity_aliases,
            cache_dir=Path(cfg.cache_dir).expanduser(),
            plugin_data_dir=Path(__file__).resolve().parent / "data",
            auto_fetch_missing_year=cfg.auto_fetch_missing_year,
            auto_refresh_future_years=cfg.auto_refresh_future_years,
            official_source_enabled=cfg.official_source_enabled,
            fallback_local_holiday_lib=cfg.fallback_local_holiday_lib,
            write_back_plugin_data=cfg.write_back_plugin_data,
            official_fetch_timeout_s=cfg.official_fetch_timeout_s,
            official_url_template=cfg.official_url_template,
        )
        base_dir = Path(__file__).resolve().parent
        birthdays_path = base_dir / "data" / cfg.builtin_birthdays_file
        special_days_path = base_dir / "data" / cfg.builtin_special_days_file
        builtin_years_dir = base_dir / "data" / cfg.builtin_years_dir
        extra_birthdays_paths = [Path(path).expanduser() for path in cfg.extra_birthdays_files]
        extra_special_days_paths = [Path(path).expanduser() for path in cfg.extra_special_days_files]
        extra_year_paths = [Path(path).expanduser() for path in cfg.extra_year_files]
        service.load_dataset(
            birthdays_path=birthdays_path,
            builtin_years_dir=builtin_years_dir,
            extra_birthdays_paths=extra_birthdays_paths,
            special_days_path=special_days_path,
            extra_special_days_paths=extra_special_days_paths,
            extra_year_paths=extra_year_paths,
        )
        await service.ensure_year_loaded(datetime.now().year)
        self._service = service
        publish_calendar_service(service)
        if getattr(ctx, "identity", None) is not None and getattr(ctx.identity, "name", ""):
            set_calendar_self_names(ctx.identity.name)
        ctx.calendar_service = service

        greeter_path = Path(cfg.cache_dir).expanduser() / "member_birthdays.json"
        self._greeter = BirthdayGreeter(greeter_path)
        ctx.birthday_greeter = self._greeter

    async def on_bot_connect(self, ctx: PluginContext, bot: Any) -> None:
        del ctx
        self._bot = bot

    async def on_tick(self, ctx: PluginContext) -> None:
        if self._greeter is None or self._bot is None:
            return
        scheduler = getattr(ctx, "scheduler", None)
        is_muted = getattr(scheduler, "is_muted", None)

        def group_allowed(group_id: str) -> bool:
            return not bool(callable(is_muted) and is_muted(str(group_id)))

        try:
            greeted = await self._greeter.check_and_greet(
                self._bot,
                llm_client=getattr(ctx, "llm_client", None),
                group_allowed=group_allowed,
            )
            if greeted:
                logger.bind(channel="calendar_context").info(
                    "birthday_greeter sent wishes | qq={}",
                    greeted,
                )
        except Exception as exc:
            logger.bind(channel="calendar_context").warning(
                "birthday_greeter failed | err={}",
                exc,
            )

    async def on_shutdown(self, ctx: PluginContext) -> None:
        if self._service is not None:
            unpublish_calendar_service(self._service)
        if getattr(ctx, "calendar_service", None) is self._service:
            ctx.calendar_service = None
        if getattr(ctx, "birthday_greeter", None) is self._greeter:
            ctx.birthday_greeter = None
        self._service = None
        self._greeter = None
        self._bot = None

    def runtime_meta(self) -> dict[str, object]:
        return self._service.runtime_meta if self._service is not None else {}
