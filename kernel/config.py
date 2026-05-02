"""Omubot 内核配置系统。

包含：
- KernelConfig: 内核层专属配置（插件目录、禁用列表、钩子超时）
- BotConfig: 全局配置根模型（含所有子系统配置）
- load_config(): TOML → 环境变量 → CLI 参数三层合并

这是框架的配置中枢。所有层都依赖此模块。
"""

from __future__ import annotations

import os
import tomllib
from typing import Any, Self

from pydantic import BaseModel, model_validator

# ============================================================================
# 内核配置
# ============================================================================


class KernelConfig(BaseModel):
    """内核层专属配置。"""

    plugin_dirs: list[str] = ["plugins"]
    disabled_plugins: list[str] = []
    max_hook_time_ms: int = 5000


# ============================================================================
# LLM 配置
# ============================================================================


class ContextConfig(BaseModel):
    """上下文窗口配置。"""

    max_context_tokens: int = 1_000_000


class UsageConfig(BaseModel):
    """LLM usage tracking configuration."""

    enabled: bool = True
    slow_threshold_s: float = 60.0


class LLMConfig(BaseModel):
    """LLM 接入配置。"""

    base_url: str = "http://127.0.0.1:34567/v1"
    api_key: str = "sk-placeholder"
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 1024
    context: ContextConfig = ContextConfig()
    usage: UsageConfig = UsageConfig()


# ============================================================================
# 日志配置
# ============================================================================


class LogChannelConfig(BaseModel):
    """stderr 日志频道开关。"""

    message_in: bool = True
    message_out: bool = True
    thinking: bool = True
    mood: bool = True
    affection: bool = True
    schedule: bool = True
    scheduler: bool = False
    usage: bool = False
    compact: bool = False
    system: bool = True
    debug: bool = False
    dream: bool = False


class LogConfig(BaseModel):
    """日志配置。"""

    dir: str = "storage/logs"
    channels: LogChannelConfig = LogChannelConfig()


# ============================================================================
# 人设配置
# ============================================================================


class SoulConfig(BaseModel):
    """人设与指令配置目录。"""

    dir: str = "config/soul"


# ============================================================================
# 群聊配置
# ============================================================================


class ResolvedGroupConfig(BaseModel):
    """resolve() 返回的扁平群配置。"""

    blocked_users: set[int] = set()
    at_only: bool = False
    debounce_seconds: float = 5.0
    batch_size: int = 10
    history_load_count: int = 30
    privacy_mask: bool = True


class GroupOverride(BaseModel):
    """单个群的覆盖配置，None 表示使用全局值。"""

    blocked_users: list[int] = []
    at_only: bool | None = None
    debounce_seconds: float | None = None
    batch_size: int | None = None
    history_load_count: int | None = None


class GroupConfig(BaseModel):
    """群聊上下文配置。"""

    history_load_count: int = 30
    allowed_groups: list[int] = []
    debounce_seconds: float = 5.0
    batch_size: int = 10
    at_only: bool = False
    blocked_users: list[int] = []
    privacy_mask: bool = True
    overrides: dict[int, GroupOverride] = {}

    def resolve(self, group_id: int) -> ResolvedGroupConfig:
        base_blocked = set(self.blocked_users)
        override = self.overrides.get(group_id)
        if override is None:
            return ResolvedGroupConfig(
                blocked_users=base_blocked,
                at_only=self.at_only,
                debounce_seconds=self.debounce_seconds,
                batch_size=self.batch_size,
                history_load_count=self.history_load_count,
                privacy_mask=self.privacy_mask,
            )
        o = override
        return ResolvedGroupConfig(
            blocked_users=base_blocked | set(o.blocked_users),
            at_only=o.at_only if o.at_only is not None else self.at_only,
            debounce_seconds=o.debounce_seconds if o.debounce_seconds is not None else self.debounce_seconds,
            batch_size=o.batch_size if o.batch_size is not None else self.batch_size,
            history_load_count=o.history_load_count if o.history_load_count is not None else self.history_load_count,
            privacy_mask=self.privacy_mask,
        )


# ============================================================================
# NapCat 配置
# ============================================================================


class NapcatConfig(BaseModel):
    """NapCat HTTP API 配置。"""

    api_url: str = "http://localhost:29300"


# ============================================================================
# 记忆 / 压缩 / Dream 配置
# ============================================================================


class MemoConfig(BaseModel):
    """备忘录系统配置。"""

    dir: str = "storage/memories"
    user_max_chars: int = 300
    group_max_chars: int = 500
    index_max_lines: int = 200
    history_enabled: bool = True


class CompactConfig(BaseModel):
    """上下文压缩配置。"""

    ratio: float = 0.7
    compress_ratio: float = 0.5
    max_failures: int = 3
    cache_hit_warn: float = 90.0
    cache_alert_window_m: float = 30.0
    cache_alert_cooldown_m: float = 10.0

    @model_validator(mode="after")
    def _check_ratios(self) -> Self:
        if not (0.0 < self.ratio < 1.0):
            raise ValueError("ratio must be between 0 and 1")
        if not (0.0 < self.compress_ratio < 1.0):
            raise ValueError("compress_ratio must be between 0 and 1")
        return self


class DreamConfig(BaseModel):
    """Dream 整理配置。"""

    enabled: bool = False
    interval_hours: int = 24
    max_rounds: int = 15


# ============================================================================
# 视觉 / 表情包配置
# ============================================================================


class QwenVLConfig(BaseModel):
    """Qwen VL 小模型配置。api_key 非空即启用，无需额外 enabled 开关。"""

    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: str = ""
    model: str = "qwen2.5-vl-7b-instruct"


class VisionConfig(BaseModel):
    """多模态视觉配置。"""

    enabled: bool = True
    max_images_per_message: int = 5
    max_dimension: int = 768
    cache_dir: str = "storage/image_cache"
    cache_max_age_hours: int = 24
    qwen: QwenVLConfig = QwenVLConfig()


class StickerConfig(BaseModel):
    """表情包系统配置。"""

    enabled: bool = True
    storage_dir: str = "storage/stickers"
    max_count: int = 200
    frequency: str = "normal"


# ============================================================================
# 日程 / 好感度 / Thinker 配置
# ============================================================================


class ScheduleConfig(BaseModel):
    """模拟日程系统配置。"""

    enabled: bool = True
    storage_dir: str = "storage/schedule"
    generate_at_hour: int = 2
    mood_anomaly_chance: float = 0.2
    mood_refresh_minutes: int = 15


class AffectionConfig(BaseModel):
    """好感度与称呼系统配置。"""

    enabled: bool = True
    storage_dir: str = "storage/affection"
    score_increment: float = 0.8
    daily_cap: float = 10.0


class ThinkerConfig(BaseModel):
    """Pre-reply thinking phase configuration."""

    enabled: bool = True
    max_tokens: int = 256


# ============================================================================
# 要素察觉 / 防检测配置
# ============================================================================


class ElementRule(BaseModel):
    """单条要素察觉规则：正则匹配 → 预设回复（或 LLM 生成）。"""

    pattern: str
    reply: str
    description: str = ""
    use_llm: bool = False


class ElementDetectionConfig(BaseModel):
    """要素察觉配置。"""

    enabled: bool = True
    rules: list[ElementRule] = []


class AntiDetectConfig(BaseModel):
    """防检测人性化配置。"""

    enabled: bool = True
    min_delay: float = 0.5
    max_delay: float = 3.0
    char_delay: float = 0.02


# ============================================================================
# 根配置
# ============================================================================


class BotConfig(BaseModel):
    """全局 Bot 配置。"""

    # 内核
    kernel: KernelConfig = KernelConfig()

    # 子系统
    anti_detect: AntiDetectConfig = AntiDetectConfig()
    llm: LLMConfig = LLMConfig()
    log: LogConfig = LogConfig()
    memo: MemoConfig = MemoConfig()
    compact: CompactConfig = CompactConfig()
    dream: DreamConfig = DreamConfig()
    soul: SoulConfig = SoulConfig()
    group: GroupConfig = GroupConfig()
    napcat: NapcatConfig = NapcatConfig()
    vision: VisionConfig = VisionConfig()
    sticker: StickerConfig = StickerConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    affection: AffectionConfig = AffectionConfig()
    thinker: ThinkerConfig = ThinkerConfig()
    element_detection: ElementDetectionConfig = ElementDetectionConfig()

    # 管理员 & 白名单
    admins: dict[str, str] = {}
    allowed_private_users: list[int] = []
    admin_token: str = ""


# ============================================================================
# 配置加载器
# ============================================================================

# 环境变量 → dotted key
_ENV_MAP: dict[str, str] = {
    "LLM_BASE_URL": "llm.base_url",
    "LLM_API_KEY": "llm.api_key",
    "LLM_MODEL": "llm.model",
    "NAPCAT_API_URL": "napcat.api_url",
    "ADMIN_TOKEN": "admin_token",
    "QWEN_VL_API_KEY": "vision.qwen.api_key",
    "QWEN_VL_BASE_URL": "vision.qwen.base_url",
    "QWEN_VL_MODEL": "vision.qwen.model",
}

# CLI 参数名 → dotted key
_CLI_MAP: dict[str, str] = {
    "llm_base_url": "llm.base_url",
    "llm_api_key": "llm.api_key",
    "llm_model": "llm.model",
}


def _deep_set(d: dict[str, Any], dotted_key: str, value: Any) -> None:
    """将 dotted_key 写入嵌套字典 d。"""
    keys = dotted_key.split(".")
    node = d
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value


def load_config(
    config_path: str | None = None,
    cli_overrides: dict[str, str] | None = None,
) -> BotConfig:
    """加载并合并配置。

    优先级（低 → 高）：
      1. Pydantic 默认值
      2. TOML 文件
      3. 环境变量（_ENV_MAP）
      4. _CLI_* 环境变量（由 bot.py argparse 设置）
      5. cli_overrides 参数
    """
    data: dict[str, Any] = {}

    # 1. 确定 TOML 文件路径
    resolved_path: str | None = config_path
    if resolved_path is None:
        resolved_path = os.environ.get("BOT_CONFIG_PATH")
    if resolved_path is None:
        default_toml = "config/config.toml"
        if os.path.isfile(default_toml):
            resolved_path = default_toml

    # 2. 加载 TOML
    if resolved_path is not None:
        with open(resolved_path, "rb") as fh:
            data = tomllib.load(fh)

    # 3. 环境变量覆盖
    for env_var, dotted_key in _ENV_MAP.items():
        value = os.environ.get(env_var)
        if value is not None:
            _deep_set(data, dotted_key, value)

    # 4. _CLI_* 环境变量覆盖
    for cli_key, dotted_key in _CLI_MAP.items():
        env_name = f"_CLI_{cli_key.upper()}"
        value = os.environ.get(env_name)
        if value is not None:
            _deep_set(data, dotted_key, value)

    # 5. cli_overrides 参数覆盖
    if cli_overrides:
        for cli_key, value in cli_overrides.items():
            dotted_key = _CLI_MAP.get(cli_key)
            if dotted_key is None:
                dotted_key = cli_key
            _deep_set(data, dotted_key, value)

    return BotConfig.model_validate(data)
