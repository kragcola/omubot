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
from dataclasses import dataclass
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
HumanizationProfile = Literal["custom", "economy", "balanced", "performance"]


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
            "style_review": (
                "style_review"
                if "style_review" in self.profiles
                else self.task_profiles.get("style", fallback)
            ),
            "memo": "memo" if "memo" in self.profiles else fallback,
            "persona_import": "persona_import" if "persona_import" in self.profiles else fallback,
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
            "episode_review",
            "fact_review",
            "scheduler_eot",
            "scheduler_replay_judge",
            "birthday_wish",
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
    planner_smooth: float = 2.0
    consecutive_skip_force_threshold: int = 5
    consecutive_skip_double_threshold: int = 3
    debounce_seconds: float = 5.0
    batch_size: int = 10
    history_load_count: int = 30
    privacy_mask: bool = True
    reply_style: GroupReplyStyle = "default"
    custom_prompt: str = ""
    tools_enabled: bool = True
    sticker_mode: GroupStickerMode = "inherit"
    slang_enabled: bool = True
    humanization_profile: HumanizationProfile | None = None
    qq_interactions_profile_override: bool | None = None


class GroupOverride(BaseModel):
    """单个群的覆盖配置，None 表示使用全局值。"""

    blocked_users: list[int] = []
    allowed_tools: list[str] | None = None
    blocked_tools: list[str] | None = None
    at_only: bool | None = None
    talk_value: float | None = None
    planner_smooth: float | None = None
    consecutive_skip_force_threshold: int | None = None
    consecutive_skip_double_threshold: int | None = None
    debounce_seconds: float | None = None
    batch_size: int | None = None
    history_load_count: int | None = None
    reply_style: GroupReplyStyle | None = None
    custom_prompt: str | None = None
    tools_enabled: bool | None = None
    sticker_mode: GroupStickerMode | None = None
    slang_enabled: bool | None = None
    presence_mode: GroupPresenceMode | None = None
    humanization_profile: HumanizationProfile | None = None
    qq_interactions_profile_override: bool | None = None


class GroupConfig(BaseModel):
    """群聊上下文配置。"""

    _legacy_allowed_groups_as_active: bool = PrivateAttr(default=True)

    access: GroupAccessConfig = Field(default_factory=GroupAccessConfig)
    presence: GroupPresenceConfig = Field(default_factory=GroupPresenceConfig)
    history_load_count: int = 30
    allowed_groups: list[int] = []
    talk_value: float = 0.3
    planner_smooth: float = 2.0
    consecutive_skip_force_threshold: int = 5
    consecutive_skip_double_threshold: int = 3
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
                    planner_smooth=2.0,
                    consecutive_skip_force_threshold=5,
                    consecutive_skip_double_threshold=3,
                    debounce_seconds=5.0,
                    batch_size=10,
                    history_load_count=self.history_load_count,
                    privacy_mask=self.privacy_mask,
                    reply_style=self.reply_style,
                    custom_prompt=self.custom_prompt,
                    tools_enabled=False,
                    sticker_mode="inherit",
                    slang_enabled=False,
                    humanization_profile=None,
                    qq_interactions_profile_override=None,
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
                consecutive_skip_force_threshold=self.consecutive_skip_force_threshold,
                consecutive_skip_double_threshold=self.consecutive_skip_double_threshold,
                debounce_seconds=self.debounce_seconds,
                batch_size=self.batch_size,
                history_load_count=self.history_load_count,
                privacy_mask=self.privacy_mask,
                reply_style=self.reply_style,
                custom_prompt=self.custom_prompt,
                tools_enabled=self.tools_enabled,
                sticker_mode=self.sticker_mode,
                slang_enabled=self.slang_enabled,
                humanization_profile=None,
                qq_interactions_profile_override=None,
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
            consecutive_skip_force_threshold=(
                o.consecutive_skip_force_threshold
                if o.consecutive_skip_force_threshold is not None
                else self.consecutive_skip_force_threshold
            ),
            consecutive_skip_double_threshold=(
                o.consecutive_skip_double_threshold
                if o.consecutive_skip_double_threshold is not None
                else self.consecutive_skip_double_threshold
            ),
            debounce_seconds=o.debounce_seconds if o.debounce_seconds is not None else self.debounce_seconds,
            batch_size=o.batch_size if o.batch_size is not None else self.batch_size,
            history_load_count=o.history_load_count if o.history_load_count is not None else self.history_load_count,
            privacy_mask=self.privacy_mask,
            reply_style=o.reply_style if o.reply_style is not None else self.reply_style,
            custom_prompt=o.custom_prompt if o.custom_prompt is not None else self.custom_prompt,
            tools_enabled=tools_enabled,
            sticker_mode=o.sticker_mode if o.sticker_mode is not None else self.sticker_mode,
            slang_enabled=slang_enabled,
            humanization_profile=o.humanization_profile,
            qq_interactions_profile_override=o.qq_interactions_profile_override,
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


class CharacterRecognitionConfig(BaseModel):
    """角色识别 sidecar 配置。"""

    enabled: bool = False
    sidecar_url: str = "http://host.docker.internal:8620"
    packs_dir: str = "config/character_packs"
    timeout_seconds: float = 5.0


class VisionConfig(BaseModel):
    """多模态视觉配置。"""

    enabled: bool = True
    max_images_per_message: int = 5
    max_dimension: int = 768
    cache_dir: str = "storage/image_cache"
    cache_max_age_hours: int = 24
    describe_max_tokens: int = 200  # 描述输出上限；留出 OCR 文字空间（原硬编码 128）
    qwen: QwenVLConfig = QwenVLConfig()
    character_recognition: CharacterRecognitionConfig = Field(default_factory=CharacterRecognitionConfig)



# ============================================================================
# 日程 / 好感度 / Thinker 配置
# ============================================================================




class ThinkerConfig(BaseModel):
    """Pre-reply thinking phase configuration."""

    enabled: bool = False
    max_tokens: int = 256
    necessity_gate_enabled: bool = Field(
        default=False,
        description="B3: downgrade a low-necessity proactive reply to wait (suppress 'showing off').",
    )
    necessity_gate_addressed_exempt: bool = Field(
        default=True,
        description="When True, addressed/triggered turns are never suppressed by the necessity gate.",
    )
    wait_deferral_seconds: float = Field(
        default=8.0,
        description="When an addressed (@) turn's thinker chooses wait, re-fire after this quiet window so "
        "the @ obligation is honored if the user said nothing more. 0 disables (wait drops silently).",
    )
    wait_max_deferrals: int = Field(
        default=1,
        description="Max times an addressed wait may defer before forcing a reply (prevents waiting forever).",
    )


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
    natural_split_enabled: bool = Field(
        default=True,
        description="是否启用 Part 5 自然分段算法。",
        json_schema_extra={
            "display_label": "自然分段",
            "help": "默认开启 Part 5 自然分段算法 + 自适应段间延迟；关闭会让回复整段一次性发出。",
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


class PersonaV2Config(BaseModel):
    """Persona v2 runtime configuration.

    The bot loads persona artifacts from ``config/persona/<persona_id>/`` at
    connect time. There is no v1 path; ``persona_id`` selects which compiled
    persona bundle to load.
    """

    persona_id: str = "default"


StateBoardLayout = Literal["head", "tail"]
StateBoardGranularity = Literal["fine", "coarse"]


class StateBoardConfig(BaseModel):
    """State board prompt rendering controls.

    Defaults preserve the legacy prompt layout. Part 6 can move the volatile
    block to the tail for DeepSeek byte-exact prefix stability.
    """

    layout: StateBoardLayout = Field(
        default="head",
        description="Where the volatile group state board is inserted in prompt blocks.",
        json_schema_extra={
            "display_label": "群状态块位置",
            "help": "head=保持旧顺序；tail=把 state_board 放到 plugin stable/dynamic 之后，减少 DeepSeek prefix 抖动。",
            "recommended": "head / tail",
            "risk_level": "careful",
            "restart_hint": "required",
        },
    )
    granularity: StateBoardGranularity = Field(
        default="fine",
        description="How much time/count detail the state board renders.",
        json_schema_extra={
            "display_label": "群状态块粒度",
            "help": "fine=旧输出，保留分钟和计数；coarse=粗粒度时间/频率，减少 prompt 字符漂移。",
            "recommended": "fine / coarse",
            "risk_level": "careful",
            "restart_hint": "required",
        },
    )


class StreamingSegmentConfig(BaseModel):
    """Streaming-as-segment profile gate."""

    enabled: bool = Field(
        default=False,
        description="Enable Part 6 streaming-as-segment generation.",
        json_schema_extra={
            "display_label": "Streaming 分段",
            "help": "默认关闭；balanced/performance profile 可在决议层启用。",
            "risk_level": "careful",
            "restart_hint": "required",
        },
    )


class PauseThenExtendConfig(BaseModel):
    """Pause-then-extend profile gate."""

    enabled: bool = Field(
        default=True,
        description="Enable Part 6 pause-then-extend follow-up generation.",
        json_schema_extra={
            "display_label": "暂停后追发",
            "help": "默认开启；可用 profile=economy 或本开关关闭快速回滚。",
            "risk_level": "careful",
            "restart_hint": "required",
        },
    )


class PlanThenUtterConfig(BaseModel):
    """Plan-then-utter profile gate."""

    enabled: bool = Field(
        default=False,
        description="Enable Part 6 plan-then-utter generation.",
        json_schema_extra={
            "display_label": "计划后发言",
            "help": "默认关闭；performance profile 可在决议层启用。",
            "risk_level": "danger",
            "restart_hint": "required",
        },
    )
    group_whitelist: list[str] = Field(
        default_factory=list,
        description="Optional group whitelist for plan-then-utter.",
        json_schema_extra={
            "display_label": "计划后发言灰度群",
            "help": "非空时仅这些群允许 plan_then_utter 生效。",
            "risk_level": "careful",
            "restart_hint": "required",
        },
    )

    @field_validator("group_whitelist", mode="before")
    @classmethod
    def _coerce_group_whitelist(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("group_whitelist must be a list")
        normalized: list[str] = []
        for raw in value:
            text = str(raw).strip()
            if text:
                normalized.append(text)
        return normalized


class QQInteractionsConfig(BaseModel):
    """QQ special interaction feature flags."""

    poke_inbound_response_enabled: bool = Field(
        default=False,
        description="Enable reply triggers for inbound poke notices.",
        json_schema_extra={
            "display_label": "戳一戳入站响应",
            "help": "开启后被戳一戳时可投递调度 trigger；默认关闭。",
            "risk_level": "careful",
            "restart_hint": "required",
        },
    )
    reaction_inbound_response_enabled: bool = Field(
        default=False,
        description="Enable reply triggers for inbound message reactions.",
        json_schema_extra={
            "display_label": "表情回应入站响应",
            "help": "开启后消息被表情回应时可投递调度 trigger；默认关闭。",
            "risk_level": "careful",
            "restart_hint": "required",
        },
    )
    poke_outbound_enabled: bool = Field(
        default=False,
        description="Enable outbound poke_user tool registration.",
        json_schema_extra={
            "display_label": "主动戳一戳",
            "help": "开启后可低频调用 poke_user；默认关闭。",
            "risk_level": "careful",
            "restart_hint": "required",
        },
    )
    reaction_outbound_enabled: bool = Field(
        default=False,
        description="Enable outbound react_to_message tool registration.",
        json_schema_extra={
            "display_label": "主动表情回应",
            "help": "开启后可低频调用 react_to_message；默认关闭。",
            "risk_level": "careful",
            "restart_hint": "required",
        },
    )
    quote_reply_enabled: bool = Field(
        default=False,
        description="Enable quote reply anchor conversion.",
        json_schema_extra={
            "display_label": "引用回复锚点",
            "help": "开启后 <quote msg_id=\"...\"/> 可转为 OneBot 引用回复；默认关闭。",
            "risk_level": "careful",
            "restart_hint": "required",
        },
    )


@dataclass(frozen=True)
class ResolvedHumanization:
    """Profile decisions consumed by Part 6 runtime hooks."""

    state_board_layout: StateBoardLayout = "head"
    state_board_granularity: StateBoardGranularity = "fine"
    streaming_segment_enabled: bool = False
    pause_then_extend_enabled: bool = False
    plan_then_utter_enabled: bool = False
    disable_natural_split: bool = False
    qq_interactions_poke_inbound_response_enabled: bool = False
    qq_interactions_reaction_inbound_response_enabled: bool = False
    qq_interactions_poke_outbound_enabled: bool = False
    qq_interactions_reaction_outbound_enabled: bool = False
    qq_interactions_quote_reply_enabled: bool = False


class HumanizationConfig(BaseModel):
    """Part 1 humanization rollout flags.

    All flags default off so adding the config section is behavior-neutral.
    """

    context_providers: bool = Field(
        default=False,
        description="Enable humanization PromptBlock providers.",
        json_schema_extra={
            "display_label": "启用拟人上下文 Provider",
            "help": "开启后才允许拟人 register/catchphrase/sticker/thinker Provider 进入 PromptBlock 管线。",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    register_classifier: bool = Field(
        default=False,
        description="Enable register classification worker.",
        json_schema_extra={
            "display_label": "启用语域分类",
            "help": "开启后按最近对话窗口判断当前回复语域，并写入 humanization runtime state。",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    sticker_register_provider: bool = Field(
        default=False,
        description="Enable sticker register PromptBlock provider.",
        json_schema_extra={
            "display_label": "启用表情包语域 Provider",
            "help": "开启后按近期表情包使用记录为回复提供表情包语域提示。",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    thinker_provider: bool = Field(
        default=False,
        description="Enable thinker decision PromptBlock provider.",
        json_schema_extra={
            "display_label": "启用 Thinker Provider",
            "help": "开启后 thinker 决策可通过 PromptBlock 管线注入；关闭时保留旧旁路行为。",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    rewrite_threshold: float = Field(
        default=-1.0,
        description="Humanization rewrite score threshold; negative disables rewrite loop.",
        json_schema_extra={
            "display_label": "拟人重写阈值",
            "help": "小于 0 表示关闭二次重写；灰度时再设为 0.0 - 1.0。",
            "recommended": "-1.0 / 0.4",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    semantic_gate_dynamic: bool = Field(
        default=False,
        description="Enable dynamic semantic gate threshold.",
        json_schema_extra={
            "display_label": "启用动态语义 Gate",
            "help": "开启后 semantic gate 阈值可按好感度和 mood 动态调整；关闭时使用 reply_workflow 固定阈值。",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    kaomoji_enforce_strict: bool = Field(
        default=False,
        description="Restrict kaomoji sticker enforcement to playful/high runtime state.",
        json_schema_extra={
            "display_label": "收紧颜文字强制表情包",
            "help": "开启后仅 playful 语域且 mood 为 playful/high 时，颜文字回复才强制补一轮 send_sticker。",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    profile: HumanizationProfile = Field(
        default="custom",
        description="Part 6 humanization generation profile.",
        json_schema_extra={
            "display_label": "拟人化生成档位",
            "help": (
                "custom=保留显式 flag；economy=cache 稳定优先；"
                "balanced=流式分段+追发；performance=额外启用 plan-then-utter。"
            ),
            "recommended": "custom / economy / balanced / performance",
            "risk_level": "careful",
            "restart_hint": "required",
        },
    )
    runtime_groups: list[str] = Field(
        default_factory=list,
        description="Group ids where humanization runtime features may run; empty means all groups when flags are on.",
        json_schema_extra={
            "display_label": "拟人化灰度群",
            "help": "非空时仅这些群允许拟人化 Provider、语域分类和 rewrite 生效；空列表表示开关开启后不限制群。",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    state_board: StateBoardConfig = Field(
        default_factory=StateBoardConfig,
        description="Part 6 state_board prompt stability controls.",
        json_schema_extra={
            "display_label": "群状态提示稳定化",
            "help": "控制 state_board 在 prompt 中的位置；默认保持旧行为。",
            "risk_level": "careful",
            "restart_hint": "required",
        },
    )
    streaming_segment: StreamingSegmentConfig = Field(
        default_factory=StreamingSegmentConfig,
        description="Part 6 streaming-as-segment controls.",
        json_schema_extra={
            "display_label": "Streaming 分段配置",
            "help": "默认关闭；custom profile 下显式读取。",
            "risk_level": "careful",
            "restart_hint": "required",
        },
    )
    pause_then_extend: PauseThenExtendConfig = Field(
        default_factory=PauseThenExtendConfig,
        description="Part 6 pause-then-extend controls.",
        json_schema_extra={
            "display_label": "暂停后追发配置",
            "help": "默认开启；custom profile 下显式读取，可通过本开关关闭。",
            "risk_level": "careful",
            "restart_hint": "required",
        },
    )
    plan_then_utter: PlanThenUtterConfig = Field(
        default_factory=PlanThenUtterConfig,
        description="Part 6 plan-then-utter controls.",
        json_schema_extra={
            "display_label": "计划后发言配置",
            "help": "默认关闭；custom profile 下显式读取。",
            "risk_level": "danger",
            "restart_hint": "required",
        },
    )
    qq_interactions: QQInteractionsConfig = Field(
        default_factory=QQInteractionsConfig,
        description="Part 6 QQ special interaction controls.",
        json_schema_extra={
            "display_label": "QQ 特殊交互配置",
            "help": "默认全关；custom profile 下显式读取，预设档位按三档策略决议。",
            "risk_level": "careful",
            "restart_hint": "required",
        },
    )
    rws_shadow: bool = Field(
        default=False,
        description="Run Reply Worthiness Score beside the legacy scheduler without changing decisions.",
        json_schema_extra={
            "display_label": "RWS 影子决策",
            "help": "开启后调度器同时计算 RWS 并记录解释，但仍使用旧概率路径。",
            "risk_level": "safe",
            "restart_hint": "recommended",
        },
    )
    rws_primary: bool = Field(
        default=False,
        description="Let Reply Worthiness Score make probability-path reply decisions.",
        json_schema_extra={
            "display_label": "RWS 接管调度",
            "help": "实验开关；开启后非强制回复路径由 RWS score 与阈值决定。",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    rws_threshold: float = Field(
        default=0.5,
        description="Decision threshold used when RWS primary mode is enabled.",
        json_schema_extra={
            "display_label": "RWS 阈值",
            "help": "仅 RWS 接管时生效；默认 0.5。",
            "recommended": "0.5",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    rws_hawkes: bool = Field(
        default=False,
        description="Inject group heat rho from the Hawkes cache into RWS.",
        json_schema_extra={
            "display_label": "RWS 群热度项",
            "help": "开启后 RWS 读取 storage/hawkes_cache.db；miss 时使用近期消息率轻量回退。",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    rws_eot: bool = Field(
        default=False,
        description="Inject the end-of-turn probability classifier into RWS.",
        json_schema_extra={
            "display_label": "RWS EOT 项",
            "help": "开启后按群限频调用 scheduler_eot LLM task，超限或失败回退 0.5。",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    rws_bandit: bool = Field(
        default=False,
        description="Enable epsilon-greedy theta adaptation for RWS.",
        json_schema_extra={
            "display_label": "RWS Bandit",
            "help": "仅调整 RWS 阈值 theta；默认关闭，标注数据接入前不建议打开。",
            "risk_level": "danger",
            "restart_hint": "recommended",
        },
    )
    rws_bandit_freeze: bool = Field(
        default=True,
        description="Freeze the RWS bandit theta adapter.",
        json_schema_extra={
            "display_label": "冻结 RWS Bandit",
            "help": "紧急停用在线学习漂移；默认冻结。",
            "risk_level": "safe",
            "restart_hint": "recommended",
        },
    )
    rws_reward: bool = Field(
        default=False,
        description="P1: close the RWS reward loop — enqueue fire/skip, feed delayed reaction reward to bandit.",
        json_schema_extra={
            "display_label": "RWS Reward 回路",
            "help": "开启后 bot 发言/沉默的后续群反应会算成 reward 回灌 bandit；关闭则 bandit 空转（现状）。",
            "risk_level": "danger",
            "restart_hint": "recommended",
        },
    )
    rws_reward_window_s: float = Field(
        default=300.0,
        description="Settlement window (seconds) before a fire/skip decision's reaction is measured.",
    )
    rws_bandit_algo: str = Field(
        default="thompson",
        description="P4: RWS bandit learner — 'thompson' (Beta-Bernoulli, default) or 'epsilon' (legacy rollback).",
        json_schema_extra={
            "display_label": "RWS Bandit 算法",
            "help": "thompson=Beta-Bernoulli Thompson 采样（对稀疏/延迟反馈鲁棒）；epsilon=旧 epsilon-greedy 回退。",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    rws_bandit_min_obs: int = Field(
        default=50,
        description="P4: keep the bandit frozen (prior-only theta) until this many reward observations accrue.",
        json_schema_extra={
            "display_label": "RWS Bandit 观测门槛",
            "help": "观测数低于该值时只用先验 theta，避免早期噪声驱动漂移；默认 50。",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    rws_bandit_decay_per_obs: float = Field(
        default=0.99,
        description="P4: multiplicative decay of the bandit's Beta counts per observation (non-stationary groups).",
        json_schema_extra={
            "display_label": "RWS Bandit 衰减",
            "help": "每次观测对历史证据的乘性衰减；<1 让旧数据逐步让位于近期群氛围，默认 0.99。",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    rws_dual_threshold: bool = Field(
        default=False,
        description="P5: gate firing on BOTH the intent (im) and timing (interrupt) scores, Inner-Thoughts style.",
        json_schema_extra={
            "display_label": "RWS 双阈值",
            "help": "开启后只有「值得说」(im) 且「此刻合适」(interrupt) 同时达标才发言；主动插话的 interrupt 门更高。",
            "risk_level": "danger",
            "restart_hint": "recommended",
        },
    )
    rws_im_threshold: float = Field(
        default=0.5,
        description="P5: minimum intent score (worth speaking) required to fire under dual-threshold mode.",
        json_schema_extra={
            "display_label": "RWS im 阈值",
            "help": "「值得说」最低分；越高越话少，默认 0.5。仅在双阈值开启时生效。",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    rws_interrupt_threshold: float = Field(
        default=0.5,
        description="P5: base timing/interrupt threshold for addressed replies under dual-threshold mode.",
        json_schema_extra={
            "display_label": "RWS interrupt 阈值",
            "help": "被寻址回复的「此刻合适」最低分，默认 0.5。仅在双阈值开启时生效。",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    rws_interrupt_threshold_proactive: float = Field(
        default=0.65,
        description="P5: higher interrupt threshold for proactive (non-addressed) interjection — stay quieter.",
        json_schema_extra={
            "display_label": "RWS 主动插话 interrupt 阈值",
            "help": "未被寻址时主动插话需达到的更高「此刻合适」分，默认 0.65，让 bot 在进行中的话轮里更克制。",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    counterfactual_replay: bool = Field(
        default=False,
        description="Enable counterfactual scheduler replay collection and admin reporting.",
        json_schema_extra={
            "display_label": "反事实静默重放",
            "help": "开启后可写入/查看 scheduler replay 报表；默认只提供只读 API。",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    pass_turn_confidence_gate: bool = Field(
        default=False,
        description="Convert low-confidence pass_turn tool calls into a light acknowledgement.",
        json_schema_extra={
            "display_label": "pass_turn 信心门",
            "help": "开启后 pass_turn confidence 低于阈值时不静默，改发轻量确认。",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )
    pass_turn_confidence_threshold: float = Field(
        default=0.4,
        description="Minimum pass_turn confidence required to stay silent.",
        json_schema_extra={
            "display_label": "pass_turn 信心阈值",
            "help": "低于该值时触发 light_ack；默认 0.4。",
            "recommended": "0.4",
            "risk_level": "careful",
            "restart_hint": "recommended",
        },
    )

    @field_validator("runtime_groups", mode="before")
    @classmethod
    def _coerce_runtime_groups(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("runtime_groups must be a list")
        normalized: list[str] = []
        for raw in value:
            text = str(raw).strip()
            if text:
                normalized.append(text)
        return normalized

    @field_validator("rws_threshold", "pass_turn_confidence_threshold")
    @classmethod
    def _clamp_probability(cls, value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def resolve_profile(
        self,
        profile_value: HumanizationProfile | None = None,
        group_id: int | str | None = None,
        *,
        performance_degraded: bool | None = None,
    ) -> ResolvedHumanization:
        """Resolve Part 6 profile into concrete generation decisions."""
        profile = profile_value or self.profile
        if profile == "economy":
            return ResolvedHumanization(
                state_board_layout="tail",
                state_board_granularity="coarse",
            )
        if profile == "balanced":
            streaming_enabled = True
            plan_enabled = False
            return ResolvedHumanization(
                state_board_layout="tail",
                state_board_granularity="coarse",
                streaming_segment_enabled=streaming_enabled,
                pause_then_extend_enabled=True,
                plan_then_utter_enabled=plan_enabled,
                disable_natural_split=streaming_enabled or plan_enabled,
                qq_interactions_poke_inbound_response_enabled=True,
                qq_interactions_reaction_inbound_response_enabled=True,
                qq_interactions_quote_reply_enabled=True,
            )
        if profile == "performance":
            if self._performance_degraded(group_id, override=performance_degraded):
                streaming_enabled = True
                plan_enabled = False
                return ResolvedHumanization(
                    state_board_layout="tail",
                    state_board_granularity="coarse",
                    streaming_segment_enabled=streaming_enabled,
                    pause_then_extend_enabled=True,
                    plan_then_utter_enabled=plan_enabled,
                    disable_natural_split=streaming_enabled or plan_enabled,
                    qq_interactions_poke_inbound_response_enabled=True,
                    qq_interactions_reaction_inbound_response_enabled=True,
                    qq_interactions_quote_reply_enabled=True,
                )
            plan_enabled = self.plan_then_utter.enabled and self._plan_then_utter_allowed(group_id)
            streaming_enabled = True
            return ResolvedHumanization(
                state_board_layout="tail",
                state_board_granularity="coarse",
                streaming_segment_enabled=streaming_enabled,
                pause_then_extend_enabled=True,
                plan_then_utter_enabled=plan_enabled,
                disable_natural_split=streaming_enabled or plan_enabled,
                qq_interactions_poke_inbound_response_enabled=True,
                qq_interactions_reaction_inbound_response_enabled=True,
                qq_interactions_poke_outbound_enabled=True,
                qq_interactions_reaction_outbound_enabled=True,
                qq_interactions_quote_reply_enabled=True,
            )

        plan_enabled = self.plan_then_utter.enabled and self._plan_then_utter_allowed(group_id)
        streaming_enabled = self.streaming_segment.enabled
        return ResolvedHumanization(
            state_board_layout=self.state_board.layout,
            state_board_granularity=self.state_board.granularity,
            streaming_segment_enabled=streaming_enabled,
            pause_then_extend_enabled=self.pause_then_extend.enabled,
            plan_then_utter_enabled=plan_enabled,
            disable_natural_split=streaming_enabled or plan_enabled,
            qq_interactions_poke_inbound_response_enabled=self.qq_interactions.poke_inbound_response_enabled,
            qq_interactions_reaction_inbound_response_enabled=self.qq_interactions.reaction_inbound_response_enabled,
            qq_interactions_poke_outbound_enabled=self.qq_interactions.poke_outbound_enabled,
            qq_interactions_reaction_outbound_enabled=self.qq_interactions.reaction_outbound_enabled,
            qq_interactions_quote_reply_enabled=self.qq_interactions.quote_reply_enabled,
        )

    def _plan_then_utter_allowed(self, group_id: int | str | None) -> bool:
        whitelist = {str(gid).strip() for gid in self.plan_then_utter.group_whitelist if str(gid).strip()}
        if not whitelist:
            return True
        return str(group_id or "").strip() in whitelist

    def _performance_degraded(
        self,
        group_id: int | str | None,
        *,
        override: bool | None = None,
    ) -> bool:
        if override is not None:
            return override
        try:
            from services.humanization.health_guard import is_group_degraded

            return is_group_degraded(group_id)
        except Exception:
            return False


class SentinelGuardrailConfig(BaseModel):
    """A-cluster visible-reply guardrail config."""

    enabled: bool = Field(
        default=False,
        description="Enable sentinel / dedup / thinker-phrase guardrails before visible segmentation.",
    )
    dedup_ngram: int = 5
    dedup_threshold: float = 0.4
    dedup_action: Literal["warn", "rewrite", "block"] = "rewrite"
    thinker_phrase_ngram: int = 4
    thinker_phrase_threshold: float = 0.4
    thinker_phrase_action: Literal["warn", "rewrite", "block"] = "rewrite"

    @field_validator("dedup_threshold", "thinker_phrase_threshold")
    @classmethod
    def _clamp_guardrail_probability(cls, value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @field_validator("dedup_ngram", "thinker_phrase_ngram")
    @classmethod
    def _clamp_guardrail_ngram(cls, value: int) -> int:
        return max(1, int(value))


class ScheduleOvershareConfig(BaseModel):
    """D-cluster unsolicited schedule overshare guard config."""

    enabled: bool = Field(
        default=False,
        description="Dampen unsolicited schedule/time disclosures in visible replies.",
    )
    cumulative_threshold: int = 2
    bypass_patterns: list[str] = Field(default_factory=lambda: [
        "几点",
        "什么时候",
        "日程",
        "安排",
        "忙不忙",
        "在干嘛",
        "在做什么",
        "干啥呢",
    ])
    leak_patterns: list[str] = Field(default_factory=lambda: [
        r"\d{1,2}[：:]\d{2}",
        "上午",
        "下午",
        "晚上",
        "排练",
        "吃饭",
        "休息",
        "上课",
        "午饭",
        "晚饭",
    ])

    @field_validator("cumulative_threshold")
    @classmethod
    def _clamp_cumulative_threshold(cls, value: int) -> int:
        return max(0, int(value))


class PersonaDriftConfig(BaseModel):
    """D-cluster Layer 5 visible declaration stripper config."""

    enabled: bool = Field(
        default=False,
        description="Strip visible self-declaration / persona drift phrases before reply segmentation.",
    )
    lambda_ewma: float = 0.3
    theta_repair: float = 0.6
    theta_block: float = 0.85
    repair_max_retries: int = 1

    @field_validator("lambda_ewma", "theta_repair", "theta_block")
    @classmethod
    def _clamp_probability(cls, value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @field_validator("repair_max_retries")
    @classmethod
    def _clamp_repair_retries(cls, value: int) -> int:
        return max(0, int(value))


class AnchorReinjectionConfig(BaseModel):
    """D-cluster Layer 2 transient anchor reinjection config."""

    enabled: bool = Field(
        default=False,
        description="Inject transient persona anchor reminders at semantic boundaries.",
    )
    min_turns_between_anchors: int = 5
    max_turns_without_anchor: int = 7
    anchor_token_budget: int = 80

    @field_validator(
        "min_turns_between_anchors",
        "max_turns_without_anchor",
        "anchor_token_budget",
    )
    @classmethod
    def _clamp_positive_anchor_int(cls, value: int) -> int:
        return max(1, int(value))


class UpstreamCommandFilterConfig(BaseModel):
    """E-cluster upstream command filter config."""

    enabled: bool = Field(
        default=False,
        description="Drop peer-bot receipts and user commands aimed at other bots before timeline ingestion.",
    )
    command_patterns: list[str] = Field(default_factory=lambda: [
        "#napcat",
        "#NapCat",
        "/napcat",
    ])
    log_drops: bool = True


class AddresseeHintConfig(BaseModel):
    """E-cluster addressee request-side hint config."""

    enabled: bool = Field(
        default=False,
        description="Inject request-time addressee hint blocks for group replies.",
    )


class MentionPostProcessorConfig(BaseModel):
    """E-cluster mention literal to CQ:at conversion config."""

    enabled: bool = Field(
        default=False,
        description="Rewrite literal @昵称 tokens to CQ:at codes before outbound send.",
    )
    fallback_keep_literal: bool = True
    recent_speaker_limit: int = 20

    @field_validator("recent_speaker_limit")
    @classmethod
    def _clamp_recent_speaker_limit(cls, value: int) -> int:
        return max(1, int(value))


class SlangLookupConfig(BaseModel):
    """F-cluster OOV slang lookup config."""

    enabled: bool = Field(
        default=False,
        description="Enable OOV slang cascade with local slang DB lookup and optional TianAPI fallback.",
    )
    tianapi_key: str = ""
    timeout_ms: int = 500
    daily_limit: int = 100
    cache_size: int = 500
    circuit_breaker_threshold: int = 3
    circuit_breaker_cooldown_s: int = 300
    ask_user_fallback_enabled: bool = True

    @field_validator(
        "timeout_ms",
        "daily_limit",
        "cache_size",
        "circuit_breaker_threshold",
        "circuit_breaker_cooldown_s",
    )
    @classmethod
    def _clamp_positive_slang_lookup_ints(cls, value: int) -> int:
        return max(1, int(value))


class StickerPlacementConfig(BaseModel):
    """F-cluster post-reply sticker placement config."""

    enabled: bool = Field(
        default=False,
        description="Enable post-reply sticker decision provider and segment-aware placement.",
    )
    cooldown_ms: int = 45_000

    @field_validator("cooldown_ms")
    @classmethod
    def _clamp_positive_sticker_cooldown(cls, value: int) -> int:
        return max(1, int(value))


class TextPreflightConfig(BaseModel):
    """G-cluster low-signal text short-circuit config."""

    enabled: bool = Field(
        default=False,
        description="Skip thinker/main reply path for obviously low-signal group messages.",
    )
    skip_punctuation_only: bool = True
    skip_single_emoji: bool = True
    skip_single_char: bool = True
    skip_repetition: bool = True
    min_repetition_count: int = 3
    bypass_on_reply_to_bot: bool = True
    bypass_on_at_bot: bool = True

    @field_validator("min_repetition_count")
    @classmethod
    def _clamp_min_repetition_count(cls, value: int) -> int:
        return max(2, int(value))


class TopicBlockConfig(BaseModel):
    """B1 topic-block attribution config (parallel-topic understanding)."""

    enabled: bool = Field(
        default=False,
        description="Anchor probability-fire replies to the bot's topic block instead of the latest message.",
    )
    stale_seconds: float = Field(default=300.0, description="Inactive block archived after this many seconds.")
    attrib_recent_seconds: float = Field(
        default=120.0, description="Window for same-speaker / @-continuation attribution.",
    )
    sim_threshold: float = Field(default=0.4, description="Lexical-similarity floor for same-block fallback.")
    max_blocks: int = Field(default=6, description="Max blocks retained per group.")
    overhearer_mode: str = Field(
        default="shadow",
        description="B2 role gating: shadow (log only) / threshold (lower fire prob) / silent (no fire).",
    )
    overhearer_threshold_boost: float = Field(
        default=0.0,
        description="In threshold mode, subtract this from fire threshold when the bot is an overhearer.",
    )
    ratified_continuation_floor: float = Field(
        default=0.0,
        description="Min fire probability for a ratified continuation (user follows up in a block the bot is in). "
        "0 disables. A positive floor stops low time-of-day multipliers from crushing a live back-and-forth.",
    )

    @field_validator("overhearer_mode")
    @classmethod
    def _validate_overhearer_mode(cls, value: str) -> str:
        v = str(value or "shadow").strip().lower()
        return v if v in {"shadow", "threshold", "silent"} else "shadow"


class SelfMuteConfig(BaseModel):
    """P2 self-mute lifecycle config."""

    reconcile_enabled: bool = Field(
        default=False,
        description="Enable periodic reconcile against get_group_member_info shut_up_timestamp.",
    )
    reconcile_interval_seconds: int = 300
    action_failed_reverse_mark: bool = False
    action_failed_retcodes: list[int] = Field(default_factory=lambda: [1200, 1300])

    @field_validator("reconcile_interval_seconds")
    @classmethod
    def _clamp_reconcile_interval(cls, value: int) -> int:
        return max(1, int(value))

    @field_validator("action_failed_retcodes", mode="before")
    @classmethod
    def _normalize_action_failed_retcodes(cls, value: object) -> list[int]:
        normalized: list[int] = []
        items = value if isinstance(value, list) else [value]
        for item in items or []:
            if not isinstance(item, (int, str)):
                continue
            try:
                normalized.append(int(item))
            except (TypeError, ValueError):
                continue
        return normalized or [1200, 1300]


class BotPairGuardConfig(BaseModel):
    """B-cluster inbound loop guard config."""

    enabled: bool = Field(
        default=True,
        description="Suppress reply loops between this bot and any peer in a group via direction-alternation guard.",
    )
    max_per_minute: int = 3
    cooldown_seconds: int = 60
    loop_alt_threshold: int = Field(
        default=10,
        description="Suppress when a self↔peer back-and-forth shows ≥N direction flips within 60s (any peer). "
        "10 ≈ 5 full round-trips; a human burst (same direction) never trips it.",
    )
    known_peer_alt_threshold: int = Field(
        default=6,
        description="Stricter alternation threshold for peers listed in known_other_bots (faster suppression).",
    )
    known_other_bots: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("max_per_minute", "cooldown_seconds")
    @classmethod
    def _clamp_positive_int(cls, value: int) -> int:
        return max(1, int(value))

    @field_validator("loop_alt_threshold", "known_peer_alt_threshold")
    @classmethod
    def _clamp_alt_threshold(cls, value: int) -> int:
        return max(2, int(value))

    @field_validator("known_other_bots", mode="before")
    @classmethod
    def _normalize_known_other_bots(cls, value: object) -> dict[str, list[str]]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise TypeError("known_other_bots must be a mapping")
        normalized: dict[str, list[str]] = {}
        for raw_group_id, raw_ids in value.items():
            group_id = str(raw_group_id).strip()
            if not group_id:
                continue
            if not isinstance(raw_ids, list):
                raise TypeError("known_other_bots values must be lists")
            ids = [str(raw_id).strip() for raw_id in raw_ids if str(raw_id).strip()]
            if ids:
                normalized[group_id] = ids
        return normalized


class CoalesceConfig(BaseModel):
    """B-cluster inbound scheduling coalescer config."""

    enabled: bool = Field(
        default=False,
        description="Delay non-addressed scheduler notify calls so rapid user bursts collapse into one fire.",
    )
    idle_window_seconds: float = 5.0
    max_window_seconds: float = 12.0

    @field_validator("idle_window_seconds", "max_window_seconds")
    @classmethod
    def _clamp_positive_window(cls, value: float) -> float:
        return max(0.1, float(value))


class ArbiterConfig(BaseModel):
    """Concurrent LLM arbiter config for burst @mention handling."""

    enabled: bool = False
    api_base: str = ""
    api_key: str = ""
    model: str = ""
    timeout_ms: int = 500
    completeness_confidence_threshold: float = 0.8
    completeness_poll_interval_s: float = 0.3
    completeness_max_wait_s: float = 5.0
    interruption_enabled: bool = True
    correction_enabled: bool = True
    correction_window_s: float = 30.0
    runtime_groups: list[str] = Field(default_factory=list)

    resolved_api_base: str = Field(default="", exclude=True)
    resolved_api_key: str = Field(default="", exclude=True)
    resolved_model: str = Field(default="", exclude=True)

    @field_validator("timeout_ms")
    @classmethod
    def _clamp_timeout_ms(cls, value: int) -> int:
        return max(50, int(value))

    @field_validator(
        "completeness_confidence_threshold",
    )
    @classmethod
    def _clamp_probability(cls, value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @field_validator(
        "completeness_poll_interval_s",
        "completeness_max_wait_s",
        "correction_window_s",
    )
    @classmethod
    def _clamp_positive_float(cls, value: float) -> float:
        return max(0.0, float(value))

    @field_validator("runtime_groups", mode="before")
    @classmethod
    def _coerce_runtime_groups(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("runtime_groups must be a list")
        normalized: list[str] = []
        for raw in value:
            text = str(raw).strip()
            if text:
                normalized.append(text)
        return normalized


class InstructionGateConfig(BaseModel):
    """Issue 15 — instruction authority gate.

    Numeric authority levels (0-4) decide who can issue what kind of
    directive to the bot. Each severity class has a required level; a user
    passes only when their authority >= required. Admins (config.admins keys)
    map to the max level automatically. severity is detected by regex
    fast-path + an optional thinker `instruction_signal` field (most-restrictive
    merge). See docs/tracking/omubot-grayscale-issue15-instruction-gate-landing-design.md.
    """

    enabled: bool = Field(
        default=False,
        description="Master switch for the instruction authority gate.",
    )
    mode: str = Field(
        default="shadow",
        description="shadow = log gate decisions without enforcing; active = enforce.",
    )
    default_authority: int = Field(
        default=2,
        description="Authority level for users without an explicit override (0-4).",
    )
    authority_overrides: dict[str, int] = Field(
        default_factory=dict,
        description="Seed map qq_id -> level (0-4). Runtime changes via /authority "
        "are persisted to storage/instruction_authority.json and win over this seed.",
    )
    required_authority: dict[str, int] = Field(
        default_factory=lambda: {"low": 2, "medium": 3, "high": 4},
        description="severity -> minimum authority level required to pass. "
        "Set high=5 to deny persona-breaking directives even for admins.",
    )
    severity_patterns: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "high": [
                r"你是\s*(ai|AI|人工智能|机器人|程序|bot)",
                r"你(其实|到底|真的)?是不是\s*(ai|AI|机器人|程序)",
                r"从现在(开始|起)你(是|叫)",
                r"忘记?(你的)?(设定|人设|身份|角色)",
                r"(重置|清空|无视)(你的)?(设定|人设|指令|prompt|提示词)",
                r"你的?(系统)?(提示词|prompt|指令)是什么",
            ],
            "medium": [
                r"帮我\s*@",
                r"去\s*@",
                r"帮(我|忙)?(发|发送|转发)",
                r"帮(我|忙)?(骂|怼|喷|损)",
                r"替我(说|告诉|转告)",
                r"去(跟|和|对).{0,8}说",
            ],
            "low": [
                r"(撒|卖)个?(娇|萌)",
                r"说话?(带|加)个?\s*喵",
                r"夸(夸|一下)?我",
                r"哄(哄)?我",
                r"叫我?(一声)?(主人|哥哥|姐姐)",
            ],
        },
        description="severity -> regex patterns (fast-path). Matched against the "
        "current user message only.",
    )
    mood_threshold: dict[str, float] = Field(
        default_factory=lambda: {
            "openness_min": 0.6,
            "valence_min": 0.3,
            "energy_floor": 0.3,
            "tension_ceiling": 0.8,
        },
        description="Mood thresholds for low-severity comply/refuse modulation.",
    )
    deny_responses: list[str] = Field(
        default_factory=lambda: [
            "我又不是你的工具人……",
            "你谁啊，凭什么指使我",
            "不想，你自己来",
            "这个我可不接",
        ],
        description="Legacy hardcoded refusal lines for DENY (only used when deny_direct_emit=true).",
    )
    deny_direct_emit: bool = Field(
        default=False,
        description="P1 rollback switch. false (default) = DENY routes an in-persona refusal hint "
        "through the main LLM (in-character, deduped, timeline-written, usage-counted). "
        "true = legacy: emit a random hardcoded deny_responses line directly, bypassing the LLM.",
    )
    refuse_soft_responses: list[str] = Field(
        default_factory=lambda: [
            "现在没心情……",
            "累了，下次吧",
            "嗯……不太想动",
        ],
        description="Soft refusal hints injected when mood blocks a low-severity request.",
    )

    @field_validator("mode")
    @classmethod
    def _normalize_mode(cls, value: str) -> str:
        text = str(value or "shadow").strip().lower()
        return text if text in {"shadow", "active"} else "shadow"

    @field_validator("default_authority")
    @classmethod
    def _clamp_default_authority(cls, value: int) -> int:
        return max(0, min(4, int(value)))

    @field_validator("required_authority", mode="before")
    @classmethod
    def _coerce_required_authority(cls, value: object) -> dict[str, int]:
        base = {"low": 2, "medium": 3, "high": 4}
        if isinstance(value, dict):
            for key in ("low", "medium", "high"):
                if key in value:
                    # high may be 5 (= unreachable, denies all incl. admin); others 0-4
                    ceiling = 5 if key == "high" else 4
                    base[key] = max(0, min(ceiling, int(value[key])))
        return base


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
    llm: LLMConfig = LLMConfig()
    log: LogConfig = LogConfig()
    compact: CompactConfig = CompactConfig()
    memory: MemoryConfig = MemoryConfig()
    group: GroupConfig = GroupConfig()
    napcat: NapcatConfig = NapcatConfig()
    vision: VisionConfig = VisionConfig()
    thinker: ThinkerConfig = ThinkerConfig()
    backup: BackupConfig = Field(default_factory=BackupConfig)
    persona_v2: PersonaV2Config = Field(default_factory=PersonaV2Config)
    humanization: HumanizationConfig = Field(default_factory=HumanizationConfig)
    sentinel_guardrail: SentinelGuardrailConfig = Field(default_factory=SentinelGuardrailConfig)
    schedule_overshare: ScheduleOvershareConfig = Field(default_factory=ScheduleOvershareConfig)
    persona_drift: PersonaDriftConfig = Field(default_factory=PersonaDriftConfig)
    anchor_reinjection: AnchorReinjectionConfig = Field(default_factory=AnchorReinjectionConfig)
    upstream_command_filter: UpstreamCommandFilterConfig = Field(default_factory=UpstreamCommandFilterConfig)
    addressee_hint: AddresseeHintConfig = Field(default_factory=AddresseeHintConfig)
    mention_post_processor: MentionPostProcessorConfig = Field(default_factory=MentionPostProcessorConfig)
    slang_lookup: SlangLookupConfig = Field(default_factory=SlangLookupConfig)
    sticker_placement: StickerPlacementConfig = Field(default_factory=StickerPlacementConfig)
    text_preflight: TextPreflightConfig = Field(default_factory=TextPreflightConfig)
    topic_block: TopicBlockConfig = Field(default_factory=TopicBlockConfig)
    self_mute: SelfMuteConfig = Field(default_factory=SelfMuteConfig)
    bot_pair_guard: BotPairGuardConfig = Field(default_factory=BotPairGuardConfig)
    coalesce: CoalesceConfig = Field(default_factory=CoalesceConfig)
    arbiter: ArbiterConfig = Field(default_factory=ArbiterConfig)
    instruction_gate: InstructionGateConfig = Field(default_factory=InstructionGateConfig)

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
    "RWS_SHADOW": "humanization.rws_shadow",
    "RWS_PRIMARY": "humanization.rws_primary",
    "RWS_THRESHOLD": "humanization.rws_threshold",
    "RWS_HAWKES": "humanization.rws_hawkes",
    "RWS_EOT": "humanization.rws_eot",
    "RWS_BANDIT": "humanization.rws_bandit",
    "BANDIT_FREEZE": "humanization.rws_bandit_freeze",
    "COUNTERFACTUAL_REPLAY": "humanization.counterfactual_replay",
    "PASS_TURN_CONFIDENCE_GATE": "humanization.pass_turn_confidence_gate",
    "PASS_TURN_CONFIDENCE_THRESHOLD": "humanization.pass_turn_confidence_threshold",
    "HUMANIZATION_PROFILE": "humanization.profile",
    "STATE_BOARD_LAYOUT": "humanization.state_board.layout",
    "STATE_BOARD_GRANULARITY": "humanization.state_board.granularity",
    "STREAMING_SEGMENT_ENABLED": "humanization.streaming_segment.enabled",
    "PAUSE_THEN_EXTEND_ENABLED": "humanization.pause_then_extend.enabled",
    "PLAN_THEN_UTTER_ENABLED": "humanization.plan_then_utter.enabled",
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


def load_plugin_config[T: BaseModel](toml_path: str | Path, model_cls: type[T]) -> T:
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
