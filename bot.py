from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path

import loguru
import nonebot
from loguru import logger
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

parser = argparse.ArgumentParser(description="QQ Bot")
parser.add_argument("--config", default=None, help="配置文件路径")
parser.add_argument("--llm-base-url", default=None)
parser.add_argument("--llm-api-key", default=None)
parser.add_argument("--llm-model", default=None)
args = parser.parse_args()

if args.config:
    os.environ["BOT_CONFIG_PATH"] = args.config
if args.llm_base_url:
    os.environ["_CLI_LLM_BASE_URL"] = args.llm_base_url
if args.llm_api_key:
    os.environ["_CLI_LLM_API_KEY"] = args.llm_api_key
if args.llm_model:
    os.environ["_CLI_LLM_MODEL"] = args.llm_model

from kernel.config import load_config as _load_config  # noqa: E402

_bot_config = _load_config(config_path=args.config)
log_dir = Path(_bot_config.log.dir)
log_dir.mkdir(parents=True, exist_ok=True)
logger.add(
    log_dir / "bot_{time:YYYY-MM-DD}.log",
    rotation="10 MB",
    retention="30 days",
    encoding="utf-8",
    level="DEBUG",
    filter=lambda record: not record["extra"].get("dream", False),
)

_c = _bot_config
logger.info("========== Bot 启动 ({}) ==========", os.environ.get("GIT_COMMIT", "dev"))
logger.info(
    "[LLM] model={} base_url={} max_tokens={}",
    _c.llm.model, _c.llm.base_url, _c.llm.max_tokens,
)
logger.info(
    "[Context] max_tokens={} compact_ratio={} compress_ratio={}",
    _c.llm.context.max_context_tokens, _c.compact.ratio, _c.compact.compress_ratio,
)
logger.info(
    "[Group] debounce={:.1f}s batch_size={}",
    _c.group.debounce_seconds, _c.group.batch_size,
)
logger.info(
    "[Group] history_load={} presence_default={} legacy_allowed={}",
    _c.group.history_load_count,
    _c.group.presence.default_mode,
    _c.group.allowed_groups or "无限制",
)
logger.info(
    "[GroupAccess] mode={} whitelist={} blacklist={} log_dropped={}",
    _c.group.access.mode,
    _c.group.access.whitelist or [],
    _c.group.access.blacklist or [],
    _c.group.access.log_dropped,
)
logger.info(
    "[Access] admins={} private_whitelist={}",
    _c.admins or "无", _c.allowed_private_users or "无限制",
)
logger.info(
    "[Dirs] soul={} log={}",
    _c.soul.dir, _c.log.dir,
)
logger.info("[NapCat] api_url={}", _c.napcat.api_url)
logger.info("==================================")

nonebot.init()

# Suppress NoneBot per-message matcher noise while keeping our own INFO logs.
# Replace NoneBot's stderr handler with a channel-aware filter.
import nonebot.log as _nlog  # noqa: E402

_MATCHER_NOISE = ("Event will be handled by", "running complete")

_CHANNEL_LABELS = {
    "message_in": "收消息  ",
    "message_out": "回复    ",
    "thinking": "思考    ",
    "mood": "心情    ",
    "affection": "好感    ",
    "schedule": "日程    ",
    "scheduler": "调度    ",
    "usage": "用量    ",
    "compact": "压缩    ",
    "system": "系统    ",
    "debug": "调试    ",
    "dream": "梦境    ",
    "bilibili": "B站     ",
    "reply_workflow": "回复流  ",
    "send_queue": "发送队列",
}


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _trim_text(value: object, limit: int = 72) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _humanize_scheduler_message(message: str) -> str:
    if " at_mention -> fire" in message:
        return message.replace("at_mention -> fire", "@触发，立即回复")
    if " directed_followup -> fire" in message:
        return message.replace("directed_followup -> fire", "承接追问，立即回复")
    if " video_always -> fire" in message:
        return message.replace("video_always -> fire", "视频触发，立即回复")

    match = re.search(
        r"scheduler \| group=(?P<group>\d+) prob (?P<action>fire|skip) "
        r"\(threshold=(?P<threshold>[0-9.]+) mood=(?P<mood>[0-9.]+) "
        r"time=(?P<time>[0-9.]+) msgs=(?P<msgs>\d+) skips=(?P<skips>\d+) mode=(?P<mode>[^)]+)\)",
        message,
    )
    if match:
        action = "命中回复" if match.group("action") == "fire" else "本轮跳过"
        return (
            f"group={match.group('group')} 概率调度：{action} "
            f"(阈值={match.group('threshold')} 心情={match.group('mood')} "
            f"时段={match.group('time')} 攒消息={match.group('msgs')} "
            f"连续跳过={match.group('skips')} 模式={match.group('mode')})"
        )

    match = re.search(
        r"scheduler llm slow \| group=(?P<group>\d+) elapsed=(?P<elapsed>[0-9.]+)s trigger=(?P<trigger>\S+)",
        message,
    )
    if match:
        return (
            f"group={match.group('group')} LLM 偏慢 "
            f"(耗时={match.group('elapsed')}s 触发={match.group('trigger')})"
        )

    match = re.search(
        r"scheduler reply batch metrics \| group=(?P<group>\d+) segments=(?P<segments>\d+) "
        r"reply_batch_queue_wait_s=(?P<queue>[0-9.]+) first_segment_elapsed_s=(?P<first>[0-9.]+) "
        r"tail_send_elapsed_s=(?P<tail>[0-9.]+) total_send_elapsed_s=(?P<total>[0-9.]+) "
        r"interleave_count=(?P<interleave>\d+)",
        message,
    )
    if match:
        return (
            f"group={match.group('group')} 回复批次：{match.group('segments')} 段 "
            f"(排队={match.group('queue')}s 首段={match.group('first')}s "
            f"尾段={match.group('tail')}s 总发送={match.group('total')}s "
            f"插队={match.group('interleave')})"
        )

    match = re.search(
        r"scheduler reply batch send complete \| group=(?P<group>\d+) segments=(?P<segments>\d+) "
        r"send_total=(?P<total>[0-9.]+)s first_release=(?P<release>\S+)",
        message,
    )
    if match:
        release = "首段即放行" if match.group("release") == "True" else "完整批次后放行"
        return (
            f"group={match.group('group')} 回复发送完成 "
            f"({match.group('segments')} 段，总耗时={match.group('total')}s，{release})"
        )

    match = re.search(
        r"scheduler reply batch released after first segment \| group=(?P<group>\d+) "
        r"segments_sent=(?P<segments>\d+) send_elapsed=(?P<elapsed>[0-9.]+)s",
        message,
    )
    if match:
        return (
            f"group={match.group('group')} 首段已发出并释放后续生成 "
            f"(已发={match.group('segments')} 段，耗时={match.group('elapsed')}s)"
        )

    match = re.search(
        r"scheduler reply send complete \| group=(?P<group>\d+) "
        r"segments=(?P<segments>\d+) send_total=(?P<total>[0-9.]+)s",
        message,
    )
    if match:
        return (
            f"group={match.group('group')} 回复发送完成 "
            f"({match.group('segments')} 段，总耗时={match.group('total')}s)"
        )

    match = re.search(r"scheduler \| group=(?P<group>\d+) (?P<rest>.+)", message)
    if match:
        return f"group={match.group('group')} {match.group('rest')}"
    return message


def _humanize_reply_workflow_message(message: str) -> str:
    if "semantic gate failed closed" in message:
        match = re.search(
            r"semantic gate failed closed \| error_type=(?P<etype>\w+) timeout_ms=(?P<timeout>\d+) error=(?P<error>.*)",
            message,
        )
        if match:
            detail = _trim_text(match.group("error"), 48)
            suffix = f" 详情={detail}" if detail else ""
            return (
                f"语义 gate 失败，按静默回退 "
                f"(类型={match.group('etype')} 超时={match.group('timeout')}ms{suffix})"
            )
        return "语义 gate 失败，按静默回退"

    match = re.search(
        r"reply_workflow \| conversation=(?P<conversation>\S+) event_id=(?P<event>\S+) "
        r"mode=(?P<mode>\S+) action=(?P<action>\S+) source=(?P<source>\S+) "
        r"confidence=(?P<confidence>[0-9.]+) latency_ms=(?P<latency>[0-9.]+) "
        r"reason=(?P<reason>\S+) fields=(?P<fields>.*)",
        message,
    )
    if not match:
        return message

    fields_text = match.group("fields")
    preview_match = re.search(r"'text_preview': '([^']*)'", fields_text)
    candidate_match = re.search(r"'candidate_reason': '([^']*)'", fields_text)
    consumed_match = re.search(r"'consumed': (True|False)", fields_text)
    intent_match = re.search(r"'intent': '([^']*)'", fields_text)

    extra_parts = []
    if preview_match:
        extra_parts.append(f"文本={_trim_text(preview_match.group(1), 24)}")
    if candidate_match:
        extra_parts.append(f"候选={candidate_match.group(1)}")
    if intent_match:
        extra_parts.append(f"意图={intent_match.group(1)}")
    if consumed_match:
        extra_parts.append(f"消费={consumed_match.group(1)}")
    extra = " ".join(extra_parts)
    if extra:
        extra = f" {extra}"

    return (
        f"{match.group('conversation')} 回复流：{match.group('mode')} -> {match.group('action')} "
        f"(来源={match.group('source')} 置信度={match.group('confidence')} "
        f"耗时={match.group('latency')}ms 原因={match.group('reason')}{extra})"
    )


def _humanize_send_queue_message(message: str) -> str:
    match = re.search(
        r"reply batch yielded between segments \| group=(?P<group>\d+) desc=(?P<desc>.+) interleaved=(?P<item>.+)",
        message,
    )
    if match:
        return (
            f"group={match.group('group')} 回复批次段间让位 "
            f"(批次={_trim_text(match.group('desc'), 18)} 插入={_trim_text(match.group('item'), 18)})"
        )
    match = re.search(
        r"send queue slow \| group=(?P<group>\d+) kind=(?P<kind>\S+) "
        r"desc=(?P<desc>.*?) humanize=(?P<humanize>\S+) "
        r"len=(?P<len>\S+) elapsed=(?P<elapsed>[0-9.]+)s",
        message,
    )
    if match:
        return (
            f"group={match.group('group')} 发送偏慢 "
            f"(类型={match.group('kind')} 描述={_trim_text(match.group('desc'), 18)} "
            f"长度={match.group('len')} 耗时={match.group('elapsed')}s)"
        )
    return message


def _humanize_message_out(message: str) -> str:
    match = re.search(
        r"(?P<preview>'.*?') \| sticker=(?P<sticker>\S+) len=(?P<len>\d+) segments=(?P<segments>\d+) "
        r"segmentation_raw_count=(?P<raw>\d+) segmentation_capped_count=(?P<capped>\d+) "
        r"segmentation_limit=(?P<limit>\S+) segmentation_strategy=(?P<strategy>\S+) "
        r"segmentation_break_reasons=(?P<reasons>\S+) llm=(?P<llm>[0-9.]+)s "
        r"send_partial_elapsed=(?P<send>[0-9.]+)s total=(?P<total>[0-9.]+)s",
        message,
    )
    if match:
        preview = _trim_text(match.group("preview").strip("'"), 36)
        return (
            f"{preview} | 表情={match.group('sticker')} 长度={match.group('len')} "
            f"分段={match.group('segments')} 原始={match.group('raw')} "
            f"实际={match.group('capped')} 限制={match.group('limit')} "
            f"切分={match.group('strategy')} 断点={match.group('reasons')} "
            f"LLM={match.group('llm')}s 发送={match.group('send')}s 总计={match.group('total')}s"
        )
    return message


def _humanize_console_message(channel: str | None, message: str) -> str:
    if channel == "scheduler":
        return _humanize_scheduler_message(message)
    if channel == "reply_workflow":
        return _humanize_reply_workflow_message(message)
    if channel == "send_queue":
        return _humanize_send_queue_message(message)
    if channel == "message_out":
        return _humanize_message_out(message)
    return message


def _channel_format(record: loguru.Record) -> str:
    """Human-readable log format with Chinese channel labels for tagged records."""
    time_str = record["time"].strftime("%m-%d %H:%M:%S")
    channel = record["extra"].get("channel")
    # Escape curly braces so that loguru's stderr colorizer doesn't parse
    # JSON-like content (e.g. [json:data={...}]) in messages as format fields.
    msg = _humanize_console_message(channel, record["message"])
    msg = msg.replace("{", "{{").replace("}", "}}")

    if channel and channel in _CHANNEL_LABELS:
        label = _CHANNEL_LABELS[channel]
        return f"{time_str} {label} | {msg}\n"

    # Untagged records: traditional format for system/NoneBot/uvicorn messages
    level = record["level"].name
    name = record["name"]
    return f"{time_str} [{level}] {name} | {msg}\n"


def _make_channel_filter():
    """Return a channel-aware log filter that reads LogChannelConfig lazily.

    - ERROR and above always pass.
    - Untagged records pass at INFO and above (startup banner, key events).
    - Tagged records check the corresponding LogChannelConfig boolean.
    - NoneBot matcher noise is always suppressed.
    """
    def _channel_filter(record: loguru.Record) -> bool:
        # Always pass ERROR / CRITICAL
        if record["level"].no >= 40:
            return True

        # Respect NoneBot's own level filtering
        if not _nlog.default_filter(record):  # type: ignore[arg-type]
            return False

        # Suppress NoneBot matcher handled / complete spam
        name = record.get("name") or ""
        if name.startswith("nonebot") and any(s in record["message"] for s in _MATCHER_NOISE):
            return False

        # Untagged records: allow INFO+ (block DEBUG / TRACE)
        channel = record["extra"].get("channel")
        if channel is None:
            return record["level"].no >= 20

        # Tagged records: consult LogChannelConfig
        return getattr(_bot_config.log.channels, channel, False)

    return _channel_filter


if hasattr(_nlog, "logger_id"):
    logger.remove(_nlog.logger_id)
    _nlog.logger_id = logger.add(  # type: ignore[assignment]
        __import__("sys").stderr,
        level=0,
        diagnose=False,
        filter=_make_channel_filter(),
        format=_channel_format,
    )

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

nonebot.load_from_toml("pyproject.toml")

# Register admin auth middleware before app starts
from admin.auth import AdminAuthMiddleware  # noqa: E402

_nb_app = nonebot.get_app()
_nb_app.add_middleware(AdminAuthMiddleware)

# ---- Omubot PluginBus setup ----
from pathlib import Path as _Path  # noqa: E402

from kernel.bus import PluginBus  # noqa: E402
from kernel.router import setup_routers  # noqa: E402
from kernel.types import PluginContext  # noqa: E402
from plugins.affection.plugin import AffectionPlugin  # noqa: E402
from plugins.chat.plugin import ChatPlugin  # noqa: E402
from plugins.dream.plugin import DreamPlugin  # noqa: E402
from plugins.echo.plugin import EchoPlugin  # noqa: E402
from plugins.element_detector.plugin import ElementDetectorPlugin  # noqa: E402
from plugins.history_loader.plugin import HistoryLoaderPlugin  # noqa: E402
from plugins.memo.plugin import MemoPlugin  # noqa: E402
from plugins.schedule.plugin import SchedulePlugin  # noqa: E402
from plugins.sticker.plugin import StickerPlugin  # noqa: E402
from services.command import CommandDispatcher  # noqa: E402
from services.errors import RuntimeErrorStore  # noqa: E402
from services.media.vision import VisionClient  # noqa: E402
from services.protocol_trace import ProtocolConnectionHistory, ProtocolTraceStore  # noqa: E402

_storage_dir = _Path("storage")
_plugin_data_dir = _storage_dir / "plugins"
_plugin_data_dir.mkdir(parents=True, exist_ok=True)

_plugin_ctx = PluginContext(
    config=_bot_config,
    storage_dir=_storage_dir,
    plugin_data_dir=_plugin_data_dir,
)
_plugin_ctx.protocol_trace = ProtocolTraceStore(max_items=120)
_plugin_ctx.protocol_connections = ProtocolConnectionHistory(max_items=80)
_plugin_ctx.runtime_errors = RuntimeErrorStore(max_events=300, max_groups=120)

# Set bot start time early — used by admin dashboard and ChatPlugin
_plugin_ctx.bot_start_time = time.time()

# SSE log sink (install early so logs from startup are pushed)
from admin.routes.api.events import install_loguru_sink  # noqa: E402

install_loguru_sink(_plugin_ctx.runtime_errors)

_bus = PluginBus()

# Directory-based plugins (loaded explicitly — these may need future migration
# to plugin.json discovery as well)
_bus.register(ChatPlugin())
_bus.register(AffectionPlugin())
_bus.register(DreamPlugin())
_bus.register(EchoPlugin())
_bus.register(ElementDetectorPlugin())
_bus.register(HistoryLoaderPlugin())
_bus.register(MemoPlugin())
_bus.register(SchedulePlugin())
_bus.register(StickerPlugin())
# VisionClient (system service, not a plugin)
if _bot_config.vision.qwen.api_key:
    _plugin_ctx.vision_client = VisionClient(
        base_url=_bot_config.vision.qwen.base_url,
        api_key=_bot_config.vision.qwen.api_key,
        model=_bot_config.vision.qwen.model,
        timeout_s=15.0,
    )
    logger.info(
        "Qwen VL vision enabled | model={} base_url={}",
        _bot_config.vision.qwen.model,
        _bot_config.vision.qwen.base_url,
    )
else:
    _plugin_ctx.vision_client = None
    logger.info("Qwen VL vision disabled (no api_key)")

# Single-file plugins + any directory plugins with plugin.json are auto-discovered
_bus.discover_plugins("plugins")
from services.plugin_config import PluginConfigStore  # noqa: E402
from services.plugin_state import PluginStateStore  # noqa: E402

_plugin_config_store = PluginConfigStore(_plugin_data_dir / "config")
_plugin_state_store = PluginStateStore(_plugin_data_dir / "plugin-state.json")
for _plugin_name, _enabled in _plugin_state_store.load().items():
    _plugin = _bus.get_plugin(_plugin_name)
    if _enabled is False and _bus.is_plugin_locked(_plugin):
        logger.warning("ignored persisted disabled state for locked plugin | name={}", _plugin_name)
        continue
    _bus.set_plugin_enabled(_plugin_name, _enabled)
for _disabled_name in _bot_config.kernel.disabled_plugins:
    _plugin = _bus.get_plugin(_disabled_name)
    if _bus.is_plugin_locked(_plugin):
        logger.warning("ignored config disabled state for locked plugin | name={}", _disabled_name)
        continue
    _bus.set_plugin_enabled(_disabled_name, False)
_plugin_ctx.plugin_state_store = _plugin_state_store
_plugin_ctx.plugin_config_store = _plugin_config_store

_plugin_ctx.bus = _bus
_plugin_ctx.command_dispatcher = CommandDispatcher(_bus)

setup_routers(_bus, _plugin_ctx)

# Mount admin dashboard AFTER all services are wired (bus, scheduler, etc.)
from admin import create_admin_router  # noqa: E402

_nb_app.include_router(create_admin_router(_plugin_ctx))

_logger = logger.bind(channel="system")
_logger.info(
    "Omubot PluginBus initialized | plugins={}",
    len(_bus.plugins),
)

if __name__ == "__main__":
    nonebot.run()
