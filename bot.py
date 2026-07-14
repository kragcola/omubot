from __future__ import annotations

import argparse
import os
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
    "[Group] history_load={} allowed={}",
    _c.group.history_load_count,
    _c.group.allowed_groups or "无限制",
)
logger.info(
    "[Access] admins={} private_whitelist={}",
    _c.admins or "无", _c.allowed_private_users or "无限制",
)
logger.info(
    "[Dirs] log={}",
    _c.log.dir,
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
    "echo": "复读    ",
    "bilibili": "B站     ",
}


def _channel_format(record: loguru.Record) -> str:
    """Human-readable log format with Chinese channel labels for tagged records."""
    time_str = record["time"].strftime("%m-%d %H:%M:%S")
    channel = record["extra"].get("channel")
    # Escape curly braces so that loguru's stderr colorizer doesn't parse
    # JSON-like content (e.g. [json:data={...}]) in messages as format fields.
    msg = record["message"].replace("{", "{{").replace("}", "}}")

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

# ---- Omubot process Composition Root ----
from bootstrap import (  # noqa: E402
    ApplicationPaths,
    build_application,
    install_application,
)

_storage_dir = Path("storage")
_plugin_data_dir = _storage_dir / "plugins"
_application = build_application(
    config=_bot_config,
    paths=ApplicationPaths(
        repo_root=Path(".").resolve(),
        storage_dir=_storage_dir,
        plugin_root=Path("plugins"),
        plugin_data_dir=_plugin_data_dir,
        config_path=(args.config or os.environ.get("BOT_CONFIG_PATH") or "config/config.json"),
    ),
    app=_nb_app,
)
_plugin_ctx = _application.context
_bus = _application.bus

# SSE log sink (install early so logs from startup are pushed)
from admin.routes.api.events import install_loguru_sink  # noqa: E402

install_loguru_sink(_plugin_ctx.runtime_errors)
install_application(_application)

_logger = logger.bind(channel="system")
_logger.info(
    "Omubot PluginBus initialized | plugins={}",
    len(_bus.plugins),
)

if __name__ == "__main__":
    nonebot.run()
