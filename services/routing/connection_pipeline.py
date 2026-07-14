"""Runtime ownership for OneBot connection and disconnection behavior."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Literal, cast

from loguru import logger

from services.name_registry import NameVariationRegistry

_L = logger.bind(channel="system")

HistoryBackfillState = Literal["idle", "running", "success", "failed"]
HistoryBackfillStage = Callable[[Any, Any], Awaitable[Any]]
DefaultHistoryBackfillStage = Callable[
    [Any, Any, tuple[str, ...] | None],
    Awaitable[Any],
]


@dataclass(frozen=True, slots=True)
class HistoryBackfillStatus:
    status: HistoryBackfillState
    runs: int = 0
    last_error: str = ""


async def _run_default_history_backfill(
    ctx: Any,
    bot: Any,
    group_ids: tuple[str, ...] | None,
) -> None:
    history_backfill = import_module("services.history_backfill")
    stage = getattr(history_backfill, "run_history_backfill", None)
    if not callable(stage):
        raise RuntimeError("services.history_backfill.run_history_backfill is unavailable")
    await cast(DefaultHistoryBackfillStage, stage)(ctx, bot, group_ids)


class RuntimeConnectionPipeline:
    """Bind a connected protocol bot to lazily assembled runtime services."""

    def __init__(
        self,
        ctx: Any,
        bus: Any,
        history_backfill_stage: HistoryBackfillStage | None = None,
    ) -> None:
        self._ctx = ctx
        self._bus = bus
        self._history_backfill_stage = history_backfill_stage
        self._history_backfill_status = HistoryBackfillStatus(status="idle")

    @property
    def history_backfill_status(self) -> dict[str, object]:
        """Return a serializable snapshot without exposing mutable stage state."""
        status = self._history_backfill_status
        return {
            "status": status.status,
            "runs": status.runs,
            "last_error": status.last_error,
        }

    async def _backfill_history(
        self,
        bot: Any,
        group_ids: tuple[str, ...] | None,
    ) -> None:
        previous_status = self._history_backfill_status
        next_run = previous_status.runs + 1
        self._history_backfill_status = HistoryBackfillStatus(
            status="running",
            runs=next_run,
        )
        try:
            stage = self._history_backfill_stage
            if stage is None:
                await _run_default_history_backfill(self._ctx, bot, group_ids)
            else:
                await stage(self._ctx, bot)
        except asyncio.CancelledError:
            self._history_backfill_status = previous_status
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._history_backfill_status = HistoryBackfillStatus(
                status="failed",
                runs=next_run,
                last_error=error,
            )
            _L.exception(
                "history backfill stage failed | self_id={} error={}",
                getattr(bot, "self_id", "unknown"),
                error,
            )
        else:
            self._history_backfill_status = HistoryBackfillStatus(
                status="success",
                runs=next_run,
            )

    async def on_connect(self, bot: Any) -> None:
        ctx = self._ctx
        ctx.bot = bot

        protocol_connections = getattr(ctx, "protocol_connections", None)
        if protocol_connections is not None and hasattr(protocol_connections, "record_connected"):
            protocol_connections.record_connected(bot)

        outbound_guard = getattr(ctx, "outbound_group_access_guard", None)
        if outbound_guard is not None and hasattr(outbound_guard, "wrap_bot") and outbound_guard.wrap_bot(bot):
            _L.info("group outbound access guard installed | self_id={}", bot.self_id)

        protocol_trace = getattr(ctx, "protocol_trace", None)
        if protocol_trace is not None and hasattr(protocol_trace, "wrap_bot") and protocol_trace.wrap_bot(bot):
            _L.info("protocol trace wrapper installed | self_id={}", bot.self_id)

        ctx.llm_client._bot_self_id = bot.self_id
        ctx.state_board.bot_self_id = bot.self_id
        ctx.persona_runtime.bind_bot_self_id(str(bot.self_id))
        ctx.scheduler.set_bot(bot)

        history_group_ids: tuple[str, ...] | None = None
        registry = getattr(ctx, "name_registry", None)
        if isinstance(registry, NameVariationRegistry):
            try:
                preload_groups = await bot.get_group_list()
            except Exception:
                pass
            else:
                resolved_group_ids: list[str] = []
                for item in preload_groups or ():
                    gid = (
                        str(item.get("group_id", "") or "").strip()
                        if isinstance(item, dict)
                        else ""
                    )
                    if not gid:
                        continue
                    resolved_group_ids.append(gid)
                    try:
                        await registry.refresh(bot, gid)
                    except Exception:
                        logger.bind(channel="debug").debug(
                            "name registry refresh skipped | group={}",
                            gid,
                        )
                history_group_ids = tuple(resolved_group_ids)

        await self._backfill_history(bot, history_group_ids)

        is_first_connect = not getattr(ctx, "startup_triggered", False)
        if is_first_connect:
            ctx.startup_triggered = True

        try:
            await self._bus.fire_on_bot_connect(ctx, bot)
        except BaseException:
            if is_first_connect:
                ctx.startup_triggered = False
            raise
        if is_first_connect:
            self._bus.start_tick_loop(ctx)

        admin_ids = list(ctx.admins.keys())
        if admin_ids and ctx.config.llm.usage.enabled:

            async def _alert_admins(msg: str) -> None:
                for admin_id in admin_ids:
                    try:
                        await bot.send_private_msg(user_id=int(admin_id), message=msg)
                    except Exception:
                        logger.bind(channel="usage").warning(
                            "failed to send usage alert to admin {}",
                            admin_id,
                        )

            ctx.usage_tracker.set_alert(
                alert_fn=_alert_admins,
                cache_hit_warn=ctx.config.compact.cache_hit_warn,
                slow_threshold_s=ctx.config.llm.usage.slow_threshold_s,
                cache_alert_window_m=ctx.config.compact.cache_alert_window_m,
                cache_alert_cooldown_m=ctx.config.compact.cache_alert_cooldown_m,
            )

        try:
            group_list: list[dict[str, object]] = await bot.get_group_list()
            group_inventory: dict[str, dict[str, object]] = {}
            for item in group_list:
                gid = str(item.get("group_id", "") or "").strip()
                if not gid:
                    continue
                group_inventory[gid] = dict(item)
            ctx.group_inventory = group_inventory
            group_ids = list(group_inventory)
            group_cfg = getattr(ctx.config, "group", None)
            if group_cfg is not None and hasattr(group_cfg, "allows_learning_group"):
                group_ids = [gid for gid in group_ids if group_cfg.allows_learning_group(gid)]
        except Exception:
            logger.exception("failed to get group list")
            return

        _L.info(
            "group inventory refreshed | total={} learning={}",
            len(getattr(ctx, "group_inventory", {}) or {}),
            len(group_ids),
        )
        if not is_first_connect:
            _L.info("reconnected, skipping first-connect setup")

        muted_count = 0
        for gid in group_ids:
            try:
                info: dict[str, object] = await bot.get_group_member_info(
                    group_id=int(gid),
                    user_id=int(bot.self_id),
                )
                raw = info.get("shut_up_timestamp") or 0
                shut_until = int(str(raw))
                if shut_until > time.time():
                    ctx.scheduler.mute(gid, source="reconcile", until_unix=float(shut_until))
                    muted_count += 1
            except Exception:
                logger.bind(channel="debug").debug("failed to query mute status | group={}", gid)
        if muted_count:
            logger.info("muted in {} group(s) at startup", muted_count)
        logger.info("Bot 就绪，开始接收消息 ✓")

    async def on_disconnect(self, bot: Any) -> None:
        ctx = self._ctx
        protocol_connections = getattr(ctx, "protocol_connections", None)
        if protocol_connections is not None and hasattr(
            protocol_connections,
            "record_disconnected",
        ):
            protocol_connections.record_disconnected(bot)
        current_bot = getattr(ctx, "bot", None)
        if current_bot is bot:
            ctx.bot = None
        _L.warning("bot disconnected | self_id={}", getattr(bot, "self_id", "unknown"))
