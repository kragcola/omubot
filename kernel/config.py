"""Omubot 内核配置系统。

包含：
- KernelConfig: 内核层专属配置（插件目录、禁用列表、钩子超时）
- BotConfig: 全局配置根模型（含所有子系统配置）
- load_config(): 配置文件（JSON/TOML）→ 环境变量 → CLI 参数三层合并

这是框架的配置中枢。所有层都依赖此模块。
"""

from __future__ import annotations

import json
import os
import tomllib
from copy import deepcopy
from pathlib import Path
from types import UnionType
from typing import Any, Literal, Self, TypeVar, Union, get_args, get_origin

from pydantic import BaseModel, Field, model_validator

T = TypeVar("T")

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


LLMCapability = Literal["chat", "tools", "thinking", "vision", "json", "compact"]
LLMApiFormat = Literal["anthropic", "openai", "deepseek"]


class LLMProfile(BaseModel):
    """一个可被任务选择的 LLM Provider 配置。

    为空的字段会在 resolve_profile() 中回退到 legacy llm.* 字段，
    让新增 profiles 不需要一次性复制所有密钥和地址。
    """

    api_format: LLMApiFormat | None = None
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    capabilities: list[LLMCapability] = Field(default_factory=lambda: ["chat"])


class LLMConfig(BaseModel):
    """LLM 接入配置。"""

    base_url: str = "http://127.0.0.1:34567/v1"
    api_key: str = "sk-placeholder"
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 1024
    api_format: LLMApiFormat = "anthropic"
    default_profile: str = "main"
    profiles: dict[str, LLMProfile] = Field(default_factory=dict)
    task_profiles: dict[str, str] = Field(default_factory=dict)
    context: ContextConfig = ContextConfig()
    usage: UsageConfig = UsageConfig()

    def _legacy_profile(self) -> LLMProfile:
        return LLMProfile(
            api_format=self.api_format,
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model,
            max_tokens=self.max_tokens,
            capabilities=["chat", "tools", "thinking", "compact"],
        )

    def resolve_profile(self, name: str | None = None) -> LLMProfile:
        """Resolve a named profile with backward-compatible legacy fallback."""
        profile_name = name or self.default_profile or "main"
        profile = self.profiles.get(profile_name)
        if profile is None and profile_name != "main":
            profile = self.profiles.get("main")
        if profile is None:
            return self._legacy_profile()

        return LLMProfile(
            api_format=profile.api_format or self.api_format,
            base_url=profile.base_url or self.base_url,
            api_key=profile.api_key or self.api_key,
            model=profile.model or self.model,
            max_tokens=profile.max_tokens or self.max_tokens,
            reasoning_effort=profile.reasoning_effort,
            capabilities=profile.capabilities or ["chat"],
        )

    def profile_name_for_task(self, task: str) -> str:
        """Return the configured profile name for a logical LLM task."""
        task_key = str(task or "main")
        if self.task_profiles.get(task_key):
            return self.task_profiles[task_key]
        if task_key == "main":
            return self.default_profile or "main"
        if task_key in self.profiles:
            return task_key
        return self.default_profile or "main"

    def resolve_task_profile(self, task: str) -> LLMProfile:
        """Resolve the profile selected for a logical LLM task."""
        return self.resolve_profile(self.profile_name_for_task(task))

    @model_validator(mode="after")
    def _ensure_main_profile(self) -> Self:
        if "main" not in self.profiles:
            self.profiles["main"] = self._legacy_profile()
        defaults = {
            "main": self.default_profile or "main",
            "thinker": "thinker" if "thinker" in self.profiles else (self.default_profile or "main"),
            "compact": "compact" if "compact" in self.profiles else (self.default_profile or "main"),
            "slang": "slang" if "slang" in self.profiles else (self.default_profile or "main"),
            "vision": "vision" if "vision" in self.profiles else (self.default_profile or "main"),
        }
        for task, profile_name in defaults.items():
            self.task_profiles.setdefault(task, profile_name)
        return self


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
    bilibili: bool = True


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


GroupReplyStyle = Literal["default", "gentle", "playful", "concise", "energetic", "steady"]
GroupStickerMode = Literal["inherit", "off", "rarely", "normal", "frequently"]


class ResolvedGroupConfig(BaseModel):
    """resolve() 返回的扁平群配置。"""

    blocked_users: set[int] = set()
    allowed_tools: set[str] = set()
    blocked_tools: set[str] = set()
    at_only: bool = False
    talk_value: float = 0.3
    planner_smooth: float = 3.0
    debounce_seconds: float = 5.0
    batch_size: int = 10
    history_load_count: int = 30
    privacy_mask: bool = True
    reply_style: GroupReplyStyle = "default"
    custom_prompt: str = ""
    tools_enabled: bool = True
    sticker_mode: GroupStickerMode = "inherit"
    slang_enabled: bool = True


class GroupOverride(BaseModel):
    """单个群的覆盖配置，None 表示使用全局值。"""

    blocked_users: list[int] = []
    allowed_tools: list[str] | None = None
    blocked_tools: list[str] | None = None
    at_only: bool | None = None
    talk_value: float | None = None
    planner_smooth: float | None = None
    debounce_seconds: float | None = None
    batch_size: int | None = None
    history_load_count: int | None = None
    reply_style: GroupReplyStyle | None = None
    custom_prompt: str | None = None
    tools_enabled: bool | None = None
    sticker_mode: GroupStickerMode | None = None
    slang_enabled: bool | None = None


class GroupConfig(BaseModel):
    """群聊上下文配置。"""

    history_load_count: int = 30
    allowed_groups: list[int] = []
    talk_value: float = 0.3
    planner_smooth: float = 3.0
    debounce_seconds: float = 5.0
    batch_size: int = 10
    at_only: bool = False
    blocked_users: list[int] = []
    allowed_tools: list[str] = []
    blocked_tools: list[str] = []
    privacy_mask: bool = True
    reply_style: GroupReplyStyle = "default"
    custom_prompt: str = ""
    tools_enabled: bool = True
    sticker_mode: GroupStickerMode = "inherit"
    slang_enabled: bool = True
    overrides: dict[int, GroupOverride] = {}

    def resolve(self, group_id: int) -> ResolvedGroupConfig:
        base_blocked = set(self.blocked_users)
        base_allowed_tools = {str(name).strip() for name in self.allowed_tools if str(name).strip()}
        base_blocked_tools = {str(name).strip() for name in self.blocked_tools if str(name).strip()}
        override = self.overrides.get(group_id)
        if override is None:
            return ResolvedGroupConfig(
                blocked_users=base_blocked,
                allowed_tools=base_allowed_tools - base_blocked_tools,
                blocked_tools=base_blocked_tools,
                at_only=self.at_only,
                talk_value=self.talk_value,
                planner_smooth=self.planner_smooth,
                debounce_seconds=self.debounce_seconds,
                batch_size=self.batch_size,
                history_load_count=self.history_load_count,
                privacy_mask=self.privacy_mask,
                reply_style=self.reply_style,
                custom_prompt=self.custom_prompt,
                tools_enabled=self.tools_enabled,
                sticker_mode=self.sticker_mode,
                slang_enabled=self.slang_enabled,
            )
        o = override
        allowed_tools = (
            base_allowed_tools
            if o.allowed_tools is None
            else {str(name).strip() for name in o.allowed_tools if str(name).strip()}
        )
        blocked_tools = (
            base_blocked_tools
            if o.blocked_tools is None
            else {str(name).strip() for name in o.blocked_tools if str(name).strip()}
        )
        return ResolvedGroupConfig(
            blocked_users=base_blocked | set(o.blocked_users),
            allowed_tools=allowed_tools - blocked_tools,
            blocked_tools=blocked_tools,
            at_only=o.at_only if o.at_only is not None else self.at_only,
            talk_value=o.talk_value if o.talk_value is not None else self.talk_value,
            planner_smooth=o.planner_smooth if o.planner_smooth is not None else self.planner_smooth,
            debounce_seconds=o.debounce_seconds if o.debounce_seconds is not None else self.debounce_seconds,
            batch_size=o.batch_size if o.batch_size is not None else self.batch_size,
            history_load_count=o.history_load_count if o.history_load_count is not None else self.history_load_count,
            privacy_mask=self.privacy_mask,
            reply_style=o.reply_style if o.reply_style is not None else self.reply_style,
            custom_prompt=o.custom_prompt if o.custom_prompt is not None else self.custom_prompt,
            tools_enabled=o.tools_enabled if o.tools_enabled is not None else self.tools_enabled,
            sticker_mode=o.sticker_mode if o.sticker_mode is not None else self.sticker_mode,
            slang_enabled=o.slang_enabled if o.slang_enabled is not None else self.slang_enabled,
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


# ============================================================================
# 群聊记忆池配置
# ============================================================================


class PoolConfig(BaseModel):
    """单个记忆池定义。"""

    name: str = ""
    groups: list[str] = []
    shared_categories: list[str] = []  # 空 = 全部共享


class MemoryModeConfig(BaseModel):
    """记忆模式配置。mode: global | per_group | pool"""

    mode: Literal["global", "per_group", "pool"] = "per_group"
    pools: dict[str, PoolConfig] = {}


class NicknameModeConfig(BaseModel):
    """昵称模式配置。"""

    mode: Literal["global", "per_group", "pool"] = "per_group"
    per_group: dict[str, str] = {}


class CardTTLConfig(BaseModel):
    """卡片 TTL 配置。"""

    default: int = 0  # 0 = 永不过期
    per_category: dict[str, int] = {}


class GroupMemoryConfig(BaseModel):
    """群聊记忆池配置。从 config/group-memory.json 加载。"""

    version: int = 1
    memory: MemoryModeConfig = MemoryModeConfig()
    nickname: NicknameModeConfig = NicknameModeConfig()
    card_ttl_days: CardTTLConfig = CardTTLConfig()

    @classmethod
    def load(cls, path: str) -> GroupMemoryConfig:
        """从 JSON 文件加载配置，不存在时返回默认值。"""
        p = Path(path)
        if not p.exists():
            return cls()
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls.model_validate(data)

    def resolve_group_pools(self, group_id: str) -> list[str]:
        """根据 memory.mode 将 group_id 映射为 scope_id 列表。

        - global:    所有群共享 → ["__global__"]
        - per_group: 每个群独立 → [group_id]
        - pool:      按 pools 配置 → [pool_id, ...]（未归属则 fallback 到 group_id）
        """
        mode = self.memory.mode
        if mode == "global":
            return ["__global__"]
        if mode == "per_group":
            return [group_id]
        # mode == "pool"
        pool_ids = [pid for pid, p in self.memory.pools.items() if group_id in p.groups]
        return pool_ids if pool_ids else [group_id]


class SemanticConfig(BaseModel):
    """轻量语义增强配置。默认只启用 ngram，不加载重依赖。"""

    enabled: bool = False
    backend: Literal["ngram", "embedding"] = "ngram"


class MemoryConfig(BaseModel):
    """全局记忆增强配置。群聊记忆池仍由 GroupMemoryConfig 管理。"""

    semantic: SemanticConfig = SemanticConfig()



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



# ============================================================================
# 日程 / 好感度 / Thinker 配置
# ============================================================================




class ThinkerConfig(BaseModel):
    """Pre-reply thinking phase configuration."""

    enabled: bool = False
    max_tokens: int = 256


# ============================================================================
# 要素察觉 / 防检测配置
# ============================================================================



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
    compact: CompactConfig = CompactConfig()
    soul: SoulConfig = SoulConfig()
    memory: MemoryConfig = MemoryConfig()
    group: GroupConfig = GroupConfig()
    napcat: NapcatConfig = NapcatConfig()
    vision: VisionConfig = VisionConfig()
    thinker: ThinkerConfig = ThinkerConfig()

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
    "LLM_API_FORMAT": "llm.api_format",
    "NAPCAT_API_URL": "napcat.api_url",
    "ADMIN_TOKEN": "admin_token",
    "QWEN_VL_API_KEY": "vision.qwen.api_key",
    "QWEN_VL_BASE_URL": "vision.qwen.base_url",
    "QWEN_VL_MODEL": "vision.qwen.model",
    "SEARCH_API_KEY": "search.api_key",
}

# CLI 参数名 → dotted key
_CLI_MAP: dict[str, str] = {
    "llm_base_url": "llm.base_url",
    "llm_api_key": "llm.api_key",
    "llm_model": "llm.model",
    "llm_api_format": "llm.api_format",
}


def _deep_set(d: dict[str, Any], dotted_key: str, value: Any) -> None:
    """将 dotted_key 写入嵌套字典 d。"""
    keys = dotted_key.split(".")
    node = d
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value


def _is_union_type(tp: Any) -> bool:
    origin = get_origin(tp)
    return origin is Union or origin is UnionType


def _coerce_cli_value(raw: str, annotation: Any) -> Any:
    """根据字段注解做轻量 CLI 值转换（仅处理高频类型）。"""
    ann = annotation
    if get_origin(ann) is not None and _is_union_type(ann):
        for sub in get_args(ann):
            if sub is type(None):
                continue
            try:
                return _coerce_cli_value(raw, sub)
            except Exception:
                continue
        return raw
    if ann is bool:
        lowered = raw.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        return raw
    if ann is int:
        try:
            return int(raw)
        except Exception:
            return raw
    if ann is float:
        try:
            return float(raw)
        except Exception:
            return raw
    return raw


def _read_config_file(path: Path) -> dict[str, Any]:
    """读取 JSON 或 TOML 配置文件。"""
    suffix = path.suffix.lower()
    if suffix == ".json":
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return {}
        loaded = json.loads(content)
        if isinstance(loaded, dict):
            return loaded
        raise ValueError(f"Config JSON must be an object: {path}")

    with open(path, "rb") as fh:
        loaded = tomllib.load(fh)
    if isinstance(loaded, dict):
        return loaded
    raise ValueError(f"Config TOML must decode to object: {path}")


def _resolve_config_file(config_path: str | None = None) -> Path:
    """解析配置文件路径：JSON 优先，兼容 TOML。"""
    raw_path = config_path or os.environ.get("BOT_CONFIG_PATH")
    if raw_path:
        configured = Path(raw_path)
        if configured.suffix.lower() == ".json":
            if configured.is_file():
                return configured
            legacy_toml = configured.with_suffix(".toml")
            if legacy_toml.is_file():
                return legacy_toml
            return configured
        if configured.suffix.lower() == ".toml":
            if configured.is_file():
                return configured
            candidate_json = configured.with_suffix(".json")
            if candidate_json.is_file():
                return candidate_json
            return configured
        if configured.is_file():
            return configured
        candidate_json = configured.with_suffix(".json")
        if candidate_json.is_file():
            return candidate_json
        candidate_toml = configured.with_suffix(".toml")
        if candidate_toml.is_file():
            return candidate_toml
        return candidate_json

    default_json = Path("config/config.json")
    if default_json.is_file():
        return default_json
    default_toml = Path("config/config.toml")
    if default_toml.is_file():
        return default_toml
    return default_json


def load_config(
    config_path: str | None = None,
    cli_overrides: dict[str, str] | None = None,
) -> BotConfig:
    """加载并合并配置。

    优先级（低 → 高）：
      1. Pydantic 默认值
      2. 配置文件（JSON 优先，兼容 TOML）
      3. 环境变量（_ENV_MAP）
      4. _CLI_* 环境变量（由 bot.py argparse 设置）
      5. cli_overrides 参数
    """
    data: dict[str, Any] = {}

    # 1. 解析配置文件路径并读取内容（JSON 优先）
    resolved_file = _resolve_config_file(config_path)
    if resolved_file.is_file():
        data = _read_config_file(resolved_file)

    # 3. 环境变量覆盖
    for env_var, dotted_key in _ENV_MAP.items():
        value = os.environ.get(env_var)
        if value is not None:
            _deep_set(data, dotted_key, value)

    field_types: dict[str, Any] = {}
    for field_name, field_info in BotConfig.model_fields.items():
        field_types[field_name] = field_info.annotation

    # 4. _CLI_* 环境变量覆盖
    for cli_key, dotted_key in _CLI_MAP.items():
        env_name = f"_CLI_{cli_key.upper()}"
        value = os.environ.get(env_name)
        if value is not None:
            root_key = dotted_key.split(".", 1)[0]
            ann = field_types.get(root_key, str)
            _deep_set(data, dotted_key, _coerce_cli_value(value, ann))

    # 5. cli_overrides 参数覆盖
    if cli_overrides:
        for cli_key, value in cli_overrides.items():
            dotted_key = _CLI_MAP.get(cli_key)
            if dotted_key is None:
                dotted_key = cli_key
            root_key = dotted_key.split(".", 1)[0]
            ann = field_types.get(root_key, str)
            _deep_set(data, dotted_key, _coerce_cli_value(value, ann))

    return BotConfig.model_validate(data)


def _infer_plugin_name(path: Path) -> str:
    if path.name in {"plugin.toml", "plugin.json", "config.default.json", "config.schema.json"}:
        return path.parent.name
    return path.stem


def _merge_plugin_values(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_plugin_values(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _read_plugin_json_values(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    values = data.get("values")
    if isinstance(values, dict):
        return values
    return data


def load_plugin_config[T](toml_path: str | Path, model_cls: type[T]) -> T:
    """加载插件配置。

    新主路径为 ``plugins/<name>/config.default.json`` 加
    ``storage/plugins/config/<name>.json`` 运行时覆盖；旧 TOML 路径保留为
    兼容 fallback，方便插件逐步目录化。
    """
    legacy_path = Path(toml_path)
    plugin_name = _infer_plugin_name(legacy_path)
    default_path = Path("plugins") / plugin_name / "config.default.json"
    override_path = Path("storage/plugins/config") / f"{plugin_name}.json"

    data: dict[str, Any] = {}
    if default_path.is_file():
        data = _read_plugin_json_values(default_path)
    elif legacy_path.suffix == ".json" and legacy_path.is_file():
        data = _read_plugin_json_values(legacy_path)
    elif legacy_path.is_file():
        with open(legacy_path, "rb") as f:
            data = tomllib.load(f)

    override = _read_plugin_json_values(override_path)
    if override:
        data = _merge_plugin_values(data, override)
    return model_cls.model_validate(data)
