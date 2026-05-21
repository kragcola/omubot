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

from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

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
        fallback = self.default_profile or "main"
        defaults = {
            "main": fallback,
            "thinker": "thinker" if "thinker" in self.profiles else fallback,
            "compact": "compact" if "compact" in self.profiles else fallback,
            "slang": "slang" if "slang" in self.profiles else fallback,
            "slang_review": (
                "slang_review"
                if "slang_review" in self.profiles
                else self.task_profiles.get("slang", fallback)
            ),
            "slang_drift": (
                "slang_drift"
                if "slang_drift" in self.profiles
                else self.task_profiles.get(
                    "slang_review",
                    self.task_profiles.get("slang", fallback),
                )
            ),
            "slang_semantic": (
                "slang_semantic"
                if "slang_semantic" in self.profiles
                else self.task_profiles.get("slang", fallback)
            ),
            "style": "style" if "style" in self.profiles else fallback,
            "memo": "memo" if "memo" in self.profiles else fallback,
            "vision": "vision" if "vision" in self.profiles else fallback,
        }
        for task, profile_name in defaults.items():
            self.task_profiles.setdefault(task, profile_name)
        self.task_profiles.setdefault(
            "reply_gate",
            "reply_gate" if "reply_gate" in self.profiles else self.task_profiles.get("thinker", "main"),
        )
        # Plugin-direct call sites and Phase A.5+ memory framework call
        # sites — every task in services/llm/llm_request.LLMTask must
        # land somewhere routable, defaulting to ``main`` so the
        # behavior matches today's "everything goes to main" reality.
        for task in (
            "chat_private",
            "bilibili_intent",
            "element_detect",
            "graph_review",
            "graph_edge_classifier",
            "reflection_consolidator",
            "episode_summarizer",
        ):
            self.task_profiles.setdefault(task, fallback)
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
    reply_workflow: bool = False


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
GroupAccessMode = Literal["whitelist", "blacklist"]
GroupPresenceMode = Literal["active", "silent_learn", "off"]


def _normalize_group_ids(value: Any) -> list[int]:
    if value is None or value == "":
        return []
    items = value if isinstance(value, list) else [value]
    normalized: list[int] = []
    for item in items:
        raw = str(item or "").strip()
        if not raw:
            continue
        normalized.append(int(raw))
    return sorted(set(normalized))


class GroupAccessConfig(BaseModel):
    """群聊发言门禁：控制哪些群可以主动发言/调用工具。"""

    mode: GroupAccessMode = Field(
        default="blacklist",
        json_schema_extra={
            "display_label": "群聊访问模式",
            "help": "blacklist=黑名单群关闭，其余群默认开启；whitelist=白名单群开启，其余群默认关闭。",
            "restart_hint": "recommended",
        },
    )
    whitelist: list[int] = Field(
        default_factory=list,
        json_schema_extra={
            "display_label": "群聊白名单",
            "help": "访问模式为 whitelist 时，只有这些群可以主动发言/调用工具，其余群默认关闭。",
            "restart_hint": "recommended",
        },
    )
    blacklist: list[int] = Field(
        default_factory=list,
        json_schema_extra={
            "display_label": "群聊黑名单",
            "help": "访问模式为 blacklist 时，这些群完全关闭，其余群默认开启。",
            "restart_hint": "recommended",
        },
    )
    log_dropped: bool = Field(
        default=True,
        json_schema_extra={
            "display_label": "记录拦截日志",
            "help": "群被访问策略拦截时，写一条固定格式日志，便于排查新群状态。",
        },
    )

    @field_validator("whitelist", "blacklist", mode="before")
    @classmethod
    def _normalize_lists(cls, value: Any) -> list[int]:
        return _normalize_group_ids(value)

    def allows_group(self, group_id: int | str | None) -> bool:
        if group_id is None:
            return False
        gid = int(group_id)
        if self.mode == "whitelist":
            return gid in set(self.whitelist)
        return gid not in set(self.blacklist)

    def allows_active(self, group_id: int | str | None) -> bool:
        return self.allows_group(group_id)


class GroupPresenceConfig(BaseModel):
    """群聊在未单独配置时的参与模式。"""

    default_mode: GroupPresenceMode = Field(
        default="active",
        json_schema_extra={
            "display_label": "默认群参与模式",
            "help": "active=可回复；silent_learn=仅对显式开启黑话的群学习；off=完全忽略群聊。",
            "restart_hint": "recommended",
        },
    )


class ResolvedGroupConfig(BaseModel):
    """resolve() 返回的扁平群配置。"""

    access_allowed: bool = False
    presence_mode: GroupPresenceMode = "silent_learn"
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
    presence_mode: GroupPresenceMode | None = None


class GroupConfig(BaseModel):
    """群聊上下文配置。"""

    _legacy_allowed_groups_as_active: bool = PrivateAttr(default=True)

    access: GroupAccessConfig = Field(default_factory=GroupAccessConfig)
    presence: GroupPresenceConfig = Field(default_factory=GroupPresenceConfig)
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

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_group_allowlist(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        data = dict(value)
        legacy_groups = _normalize_group_ids(data.get("allowed_groups"))
        if "access" not in data and legacy_groups:
            data["access"] = {
                "mode": "whitelist",
                "whitelist": legacy_groups,
                "blacklist": [],
            }

            # Legacy allowed_groups meant these groups could already speak.
            # Preserve that behavior while keeping all newly seen groups silent.
            overrides = dict(data.get("overrides") or {})
            for gid in legacy_groups:
                existing = overrides.get(gid, overrides.get(str(gid), {}))
                if hasattr(existing, "model_dump"):
                    existing_payload = existing.model_dump(mode="python")
                elif isinstance(existing, dict):
                    existing_payload = dict(existing)
                else:
                    existing_payload = {}
                existing_payload.setdefault("presence_mode", "active")
                overrides[str(gid)] = existing_payload
            data["overrides"] = overrides
        return data

    @field_validator("allowed_groups", "blocked_users", mode="before")
    @classmethod
    def _normalize_group_id_lists(cls, value: Any) -> list[int]:
        return _normalize_group_ids(value)

    @model_validator(mode="after")
    def _sync_legacy_allowed_groups(self) -> Self:
        if self.access.mode == "whitelist" and not self.allowed_groups and self.access.whitelist:
            self.allowed_groups = list(self.access.whitelist)
        return self

    def active_access_allowed(self, group_id: int | str | None) -> bool:
        return self.access.allows_active(group_id)

    def presence_mode_for(self, group_id: int | str | None) -> GroupPresenceMode:
        if group_id is None:
            return "off"
        gid = int(group_id)
        access_allowed = self.active_access_allowed(gid)
        override = self.overrides.get(gid)
        if override is not None and override.presence_mode is not None:
            if access_allowed:
                return override.presence_mode
            if override.presence_mode == "silent_learn":
                return "silent_learn"
            if override.presence_mode == "off":
                return "off"
            return "silent_learn" if override.slang_enabled is True else "off"
        if not access_allowed:
            if override is not None and (override.slang_enabled is True):
                return "silent_learn"
            return "off"
        if self._legacy_allowed_groups_as_active and gid in set(self.allowed_groups):
            return "active"
        if self.access.mode == "whitelist":
            return "active"
        return self.presence.default_mode

    def allows_learning_group(self, group_id: int | str | None) -> bool:
        if group_id is None:
            return False
        gid = int(group_id)
        resolved = self.resolve(gid)
        if resolved.presence_mode == "off" or not resolved.slang_enabled:
            return False
        if self.active_access_allowed(gid):
            return True
        return gid in self.overrides

    def allows_active_group(self, group_id: int | str | None) -> bool:
        return self.presence_mode_for(group_id) == "active" and self.active_access_allowed(group_id)

    def resolve(self, group_id: int) -> ResolvedGroupConfig:
        base_blocked = set(self.blocked_users)
        base_allowed_tools = {str(name).strip() for name in self.allowed_tools if str(name).strip()}
        base_blocked_tools = {str(name).strip() for name in self.blocked_tools if str(name).strip()}
        override = self.overrides.get(group_id)
        access_allowed = self.active_access_allowed(group_id)
        if override is None:
            if not access_allowed:
                return ResolvedGroupConfig(
                    access_allowed=False,
                    presence_mode="off",
                    blocked_users=base_blocked,
                    allowed_tools=set(),
                    blocked_tools=base_blocked_tools,
                    at_only=False,
                    talk_value=0.3,
                    planner_smooth=3.0,
                    debounce_seconds=5.0,
                    batch_size=10,
                    history_load_count=self.history_load_count,
                    privacy_mask=self.privacy_mask,
                    reply_style=self.reply_style,
                    custom_prompt=self.custom_prompt,
                    tools_enabled=False,
                    sticker_mode="inherit",
                    slang_enabled=False,
                )
            return ResolvedGroupConfig(
                access_allowed=access_allowed,
                presence_mode=self.presence_mode_for(group_id),
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
        presence_mode = self.presence_mode_for(group_id)
        tools_enabled = (
            (o.tools_enabled if o.tools_enabled is not None else self.tools_enabled)
            if access_allowed
            else False
        )
        if o.slang_enabled is not None:
            slang_enabled = o.slang_enabled if access_allowed else (o.slang_enabled and presence_mode != "off")
        else:
            slang_enabled = self.slang_enabled if access_allowed else presence_mode != "off"
        return ResolvedGroupConfig(
            access_allowed=access_allowed,
            presence_mode=presence_mode,
            blocked_users=base_blocked | set(o.blocked_users or []),
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
            tools_enabled=tools_enabled,
            sticker_mode=o.sticker_mode if o.sticker_mode is not None else self.sticker_mode,
            slang_enabled=slang_enabled,
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


class ReplySegmentationConfig(BaseModel):
    """可见回复分段与发送节奏配置。"""

    enabled: bool = Field(
        default=True,
        description="是否启用可见回复分段发送。",
        json_schema_extra={
            "display_label": "启用回复分段",
            "help": "开启后长回复会按句子、短句和显式 ---cut--- 分段逐条发送。",
            "restart_hint": "recommended",
        },
    )
    max_segment_chars: int = Field(
        default=20,
        description="单段目标最大字符数。",
        json_schema_extra={
            "display_label": "单段目标长度",
            "help": "分段器会尽量在该长度附近寻找自然断点，不是硬性截断。",
            "recommended": "18 - 32",
            "restart_hint": "recommended",
        },
    )
    min_segment_chars: int = Field(
        default=6,
        description="过短尾段合并参考长度。",
        json_schema_extra={
            "display_label": "短尾合并阈值",
            "help": "尾段短于该值时，会优先并入前一段，减少一两个字单独刷屏。",
            "recommended": "4 - 8",
            "restart_hint": "recommended",
        },
    )
    max_send_segments: int = Field(
        default=0,
        description="硬性发送段数上限；0 表示不启用硬上限。",
        json_schema_extra={
            "display_label": "硬性段数上限",
            "help": "0 表示不限制正常动态长度；设为正数会把超出的内容并回最后一段。",
            "recommended": "保持 0，除非需要强制防刷屏",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    soft_max_send_segments: int = Field(
        default=0,
        description="软性发送段数上限；0 表示关闭软上限。",
        json_schema_extra={
            "display_label": "软性段数上限",
            "help": "0 表示默认不截断正常长回复；设为正数后，超过该段数会截断并追加自然收尾。",
            "recommended": "保持 0；需要临时防刷屏时再设为 10 - 16",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    soft_limit_notice: str = Field(
        default="先说到这里啦，不然我要刷屏了☆",
        description="软上限触发时追加的自然收尾。",
        json_schema_extra={
            "display_label": "软上限收尾文案",
            "help": "只在回复极长且触发软上限时使用。",
            "restart_hint": "recommended",
        },
    )
    boundary_backend: Literal["pysbd_hybrid", "local"] = Field(
        default="pysbd_hybrid",
        description="回复分段边界候选后端。",
        json_schema_extra={
            "display_label": "分段边界后端",
            "help": "pysbd_hybrid 使用 pySBD 作为句边界候选并保留 Omubot 聊天节奏规则；local 使用本地规则回退。",
            "recommended": "pysbd_hybrid / local",
            "restart_hint": "recommended",
        },
    )
    prefer_sentence_break: bool = Field(
        default=True,
        description="优先按句末标点切分。",
        json_schema_extra={
            "display_label": "优先句末断点",
            "help": "开启后会优先在句号、问号、感叹号等自然位置切段。",
            "restart_hint": "recommended",
        },
    )
    preserve_ascii_tokens: bool = Field(
        default=True,
        description="避免切开 URL、CQ 码和 ASCII token。",
        json_schema_extra={
            "display_label": "保护链接与 CQ 码",
            "help": "开启后尽量不切断 URL、CQ 码、英文 token 和编号。",
            "restart_hint": "recommended",
        },
    )
    merge_short_tail: bool = Field(
        default=True,
        description="合并过短尾段。",
        json_schema_extra={
            "display_label": "合并过短尾段",
            "help": "开启后避免最后一个很短的词或标点单独成为一条消息。",
            "restart_hint": "recommended",
        },
    )
    first_segment_humanize: Literal["skip", "normal"] = Field(
        default="skip",
        description="首段发送前的人性化延迟策略。",
        json_schema_extra={
            "display_label": "首段延迟策略",
            "help": "skip 表示首段尽快发出；normal 表示按拟人延迟等待。",
            "recommended": "skip / normal",
            "restart_hint": "recommended",
        },
    )
    later_segment_humanize: Literal["skip", "normal"] = Field(
        default="normal",
        description="后续分段发送前的人性化延迟策略。",
        json_schema_extra={
            "display_label": "后续段延迟策略",
            "help": "normal 表示后续段按拟人节奏发送；skip 会让所有段更快排出。",
            "recommended": "normal / skip",
            "restart_hint": "recommended",
        },
    )
    inter_segment_delay_s: float = Field(
        default=0.8,
        description="分段之间的固定间隔秒数。",
        json_schema_extra={
            "display_label": "分段固定间隔",
            "help": "每两段之间额外等待的秒数；过大可能让长回复显得拖沓。",
            "recommended": "0.5 - 1.2 秒",
            "restart_hint": "recommended",
        },
    )


class SchedulerConcurrencyConfig(BaseModel):
    """群聊调度器并发配置。"""

    global_llm_limit: int = Field(
        default=2,
        description="全局同时进行的群聊 LLM 调用数。",
        json_schema_extra={
            "display_label": "全局生成并发",
            "help": "限制所有群同时进行的 LLM 生成数量，避免多群同时触发把模型打满。",
            "recommended": "2 起步，稳定后再调高",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    max_group_queue: int = Field(
        default=8,
        description="单群队列软上限预留配置。",
        json_schema_extra={
            "display_label": "单群队列上限",
            "help": "预留给后续 actor 化调度使用；当前保留为运行参数，不建议频繁调整。",
            "recommended": "8",
            "restart_hint": "recommended",
        },
    )
    max_low_priority_queue: int = Field(
        default=3,
        description="低优先级队列软上限预留配置。",
        json_schema_extra={
            "display_label": "低优先级队列上限",
            "help": "预留给低优先级自然插话队列使用，强触发不会按这个值直接丢弃。",
            "recommended": "3",
            "restart_hint": "recommended",
        },
    )
    first_segment_release: bool = Field(
        default=False,
        description="首段发送后释放下一轮生成的实验开关。",
        json_schema_extra={
            "display_label": "首段后释放生成",
            "help": "实验项。开启后首段发出即可处理下一轮触发，但更容易让贴纸和尾段交错。",
            "risk_level": "danger",
            "restart_hint": "recommended",
        },
    )
    drop_stale_low_priority_after_s: float = Field(
        default=45.0,
        description="低优先级事件过期秒数预留配置。",
        json_schema_extra={
            "display_label": "低优先级过期秒数",
            "help": "预留给自然插话过期控制使用；时间过长会让旧话题更容易滞后触发。",
            "recommended": "30 - 60 秒",
            "restart_hint": "recommended",
        },
    )


class SchedulerConfig(BaseModel):
    """群聊调度器运行时配置。"""

    concurrency: SchedulerConcurrencyConfig = SchedulerConcurrencyConfig()


ReplyWorkflowMode = Literal["off", "shadow", "semantic", "rules"]


class ReplyWorkflowConfig(BaseModel):
    """回复工作流观测与后续 actor 化配置。"""

    mode: ReplyWorkflowMode = Field(
        default="shadow",
        description="回复工作流模式；shadow 只记录观测日志，semantic 使用语义 gate 消费高置信承接。",
        json_schema_extra={
            "display_label": "回复工作流模式",
            "help": (
                "shadow 只记录观测日志；semantic 会调用小模型判断承接意图；"
                "rules 为旧兼容值，运行时按 shadow 处理。"
            ),
            "recommended": "shadow",
            "risk_level": "careful",
            "restart_hint": "recommended",
            "options": ["off", "shadow", "semantic"],
        },
    )
    semantic_force_threshold: float = Field(
        default=0.78,
        description="语义 gate 判定 force_reply 的最低置信度。",
        json_schema_extra={
            "display_label": "语义强触发阈值",
            "help": "semantic 模式下，小模型返回 force_reply 且置信度不低于该值才会转成 directed_followup。",
            "recommended": "0.75 - 0.85",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    semantic_timeout_ms: int = Field(
        default=2200,
        description="语义 gate 单次调用超时毫秒数。",
        json_schema_extra={
            "display_label": "语义 gate 超时",
            "help": "超过该时间则 fail closed，不强触发，继续走原调度路径。",
            "recommended": "1800 - 3000",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    semantic_max_chars: int = Field(
        default=48,
        description="进入语义 gate 的当前消息最大字符数。",
        json_schema_extra={
            "display_label": "语义候选最大字数",
            "help": "较长消息不作为短承接 gate 候选，避免每条群消息都调用小模型。",
            "recommended": "40 - 64",
            "restart_hint": "recommended",
        },
    )
    directed_followup_window_s: float = Field(
        default=180.0,
        description="承接类短句判断最近 bot 回复的时间窗口。",
        json_schema_extra={
            "display_label": "承接判断窗口",
            "help": "用于 shadow 观测继续说、接着讲等短句是否可能指向 bot。当前不改变强触发行为。",
            "recommended": "120 - 240 秒",
            "restart_hint": "recommended",
        },
    )
    shadow_log_private: bool = Field(
        default=True,
        description="是否记录私聊当前直进 LLM 路径的 shadow 日志。",
        json_schema_extra={
            "display_label": "记录私聊观测",
            "help": "开启后会记录私聊目前会直接进入 LLM 的事实，为后续 wait actor 提供基线。",
            "restart_hint": "recommended",
        },
    )

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_legacy_rules_mode(cls, value: object) -> object:
        if str(value or "").strip().lower() == "rules":
            return "shadow"
        return value


# ============================================================================
# 备份
# ============================================================================


class BackupConfig(BaseModel):
    """备份调度配置。

    BackupScheduler 用这套字段决定何时跑日常备份、保留多少天、是否开启
    quick_check 巡检。`quick_check_*` 是 Phase 2 治理的核心：每小时巡检
    所有 SQLite 关键库，发现 quick_check != "ok" 立即打 admin 红条 + 触发
    `pre-change` profile 紧急备份，留下损坏前最后一份干净状态。
    """

    enabled: bool = True
    daily_time: str = Field(default="02:00", pattern=r"^\d{2}:\d{2}$")
    keep_days: int = Field(default=7, ge=1, le=90)
    default_profile: str = Field(default="daily")
    pre_change_enabled: bool = True
    pre_change_keep_count: int = Field(default=5, ge=1, le=20)

    # Phase 2 — hourly SQLite quick_check probe + emergency backup + admin alarm
    quick_check_enabled: bool = True
    quick_check_interval_minutes: int = Field(default=60, ge=15, le=1440)

    @model_validator(mode="after")
    def _check_daily_time(self) -> Self:
        parts = self.daily_time.split(":")
        if len(parts) != 2:
            raise ValueError(f"daily_time must be 'HH:MM', got {self.daily_time!r}")
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except ValueError as exc:
            raise ValueError(f"daily_time must be numeric HH:MM, got {self.daily_time!r}") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"daily_time out of range, got {self.daily_time!r}")
        return self


# ============================================================================
# 根配置
# ============================================================================


class BotConfig(BaseModel):
    """全局 Bot 配置。"""

    # 内核
    kernel: KernelConfig = KernelConfig()

    # 子系统
    anti_detect: AntiDetectConfig = AntiDetectConfig()
    reply_segmentation: ReplySegmentationConfig = ReplySegmentationConfig()
    reply_workflow: ReplyWorkflowConfig = ReplyWorkflowConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    llm: LLMConfig = LLMConfig()
    log: LogConfig = LogConfig()
    compact: CompactConfig = CompactConfig()
    soul: SoulConfig = SoulConfig()
    memory: MemoryConfig = MemoryConfig()
    group: GroupConfig = GroupConfig()
    napcat: NapcatConfig = NapcatConfig()
    vision: VisionConfig = VisionConfig()
    thinker: ThinkerConfig = ThinkerConfig()
    backup: BackupConfig = Field(default_factory=BackupConfig)

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


def _resolve_group_policy_file(config_file: Path) -> Path:
    return config_file.parent / "group-policy.json"


def _load_group_access_policy(policy_path: Path) -> GroupAccessConfig | None:
    if not policy_path.is_file():
        return None
    try:
        data = json.loads(policy_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    payload = data.get("access", data)
    if not isinstance(payload, dict):
        return None
    try:
        return GroupAccessConfig.model_validate(payload)
    except Exception:
        return None


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

    cfg = BotConfig.model_validate(data)

    group_policy = _load_group_access_policy(_resolve_group_policy_file(resolved_file))
    if group_policy is not None:
        cfg.group.access = group_policy
        cfg.group._legacy_allowed_groups_as_active = False
    else:
        cfg.group._legacy_allowed_groups_as_active = True

    return cfg


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
