"""Omubot 内核类型系统。

所有层都依赖此模块。改动需谨慎——这是框架的 ABI。
内核不 import 任何项目内的其他模块。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

# ============================================================================
# 多模态消息类型（从旧 memory/types.py 提升至内核）
# ============================================================================


class TextBlock(Protocol):
    """文本块。"""
    type: Literal["text"]
    text: str


class ImageRefBlock(Protocol):
    """图片引用块——存磁盘路径而非 base64。"""
    type: Literal["image_ref"]
    path: str
    media_type: str  # e.g. "image/jpeg"


ContentBlock = TextBlock | ImageRefBlock
"""消息内容块：文本或图片引用。"""

Content = str | list[ContentBlock]
"""消息内容：纯文本（向后兼容）或多模态块列表。"""


# ============================================================================
# Tool 抽象（从旧 tools/base.py 提升至内核）
# ============================================================================


class Tool(ABC):
    """LLM 可调用工具的抽象基类。内核定义接口，系统服务层实现。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名（唯一标识，LLM 通过此名调用）。"""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述（注入 system prompt）。"""
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON Schema 格式的参数定义。"""
        ...

    @abstractmethod
    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        """执行工具，返回文本结果给 LLM。"""
        ...

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ============================================================================
# ToolContext（从旧 tools/context.py 提升至内核）
# ============================================================================


@dataclass
class ToolContext:
    """工具执行上下文。每次对话调用时构建，传入工具 execute()。"""

    bot: Any = None  # nonebot.adapters.onebot.v11.Bot（避免顶层导入耦合）
    user_id: str = ""
    group_id: str | None = None
    session_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# ============================================================================
# Identity（轻量人格模型，内核自有）
# ============================================================================


@dataclass
class Identity:
    """Bot 人格。由 IdentityManager（系统服务）从 soul/ 加载。"""

    id: str = ""
    name: str = ""
    personality: str = ""
    proactive: str = ""  # "## 插话方式" 内容


# ============================================================================
# PromptBlock —— 插件向 system prompt 追加的内容块
# ============================================================================


@dataclass
class PromptBlock:
    """插件贡献的 system prompt block。

    position 语义：
    - "static":  永不变化（放 cache breakpoint 1 之前，人格/指令之后）
    - "stable":  罕变（放 cache breakpoint 2 之前，与全局索引同层）
    - "dynamic": 每轮可变（放 cache breakpoint 2 之后，与实体记忆同层）
    """

    text: str
    label: str = ""  # 日志/调试用标签
    position: Literal["static", "stable", "dynamic"] = "dynamic"


# ============================================================================
# Context 类型 —— 各钩子的入参
# ============================================================================


@dataclass
class PluginContext:
    """生命周期钩子上下文。暴露全部系统服务引用。

    由 bot.py 在启动时构建，传入 fire_on_startup / fire_on_shutdown / on_tick。
    字段用 Any 是因为内核不 import 系统服务模块。实际类型见字段注释。
    """

    # 内核自有
    config: Any = None  # BotConfig (omubot.kernel.config)

    # 存储路径
    storage_dir: Path = field(default_factory=Path)
    plugin_data_dir: Path = field(default_factory=Path)  # storage/plugins/，插件私有数据

    # 消息存储 —— MessageLog / GroupTimeline / ShortTermMemory
    msg_log: Any = None
    timeline: Any = None
    short_term: Any = None

    # 记忆 —— CardStore / RetrievalGate / StateBoard / MemoExtractor
    card_store: Any = None
    retrieval: Any = None
    memo_extractor: Any = None
    state_board: Any = None

    # 媒体 —— ImageCache / StickerStore
    image_cache: Any = None
    sticker_store: Any = None

    # LLM —— LLMClient / PromptBuilder / Thinker
    llm_client: Any = None
    prompt_builder: Any = None
    thinker: Any = None

    # 工具与调度 —— ToolRegistry / GroupChatScheduler
    tool_registry: Any = None
    scheduler: Any = None

    # 其他 —— UsageTracker / Humanizer / Identity
    usage_tracker: Any = None
    humanizer: Any = None
    identity: Any = None  # Identity 实例

    # PluginBus 引用（供 LLMClient 等需要触发钩子的服务使用）
    bus: Any = None


@dataclass
class MessageContext:
    """消息到达钩子（on_message）的上下文。"""

    session_id: str  # "group_123456" 或 "private_123456"
    group_id: str | None  # 群聊为群号，私聊为 None
    user_id: str  # 发送者 QQ 号
    content: Content  # 解析后的消息内容
    raw_message: dict[str, Any]  # 原始 OneBot message 字典 + 扩展字段（echo_key, plain_text, segments）
    is_at: bool = False  # 是否 @ 了 bot
    is_private: bool = False  # 是否为私聊
    message_id: int | None = None
    bot: Any = None  # nonebot Bot 实例（供拦截器发送消息）
    nickname: str = ""  # 发送者昵称

    @property
    def is_group(self) -> bool:
        return self.group_id is not None


@dataclass
class PromptContext:
    """System prompt 构建钩子（on_pre_prompt）的上下文。

    插件通过 add_block() 向 system prompt 追加内容块。
    """

    session_id: str
    group_id: str | None
    user_id: str
    identity: Identity
    conversation_text: str = ""  # 最近对话文本（供关键词检索等使用）
    force_reply: bool = False
    privacy_mask: bool = True

    # 插件追加的 blocks（可变列表）
    blocks: list[PromptBlock] = field(default_factory=list)

    def add_block(
        self,
        text: str,
        *,
        label: str = "",
        position: Literal["static", "stable", "dynamic"] = "dynamic",
    ) -> None:
        """插件调用此方法向 system prompt 追加一个 block。"""
        self.blocks.append(PromptBlock(text=text, label=label, position=position))


@dataclass
class ReplyContext:
    """回复后副作用钩子（on_post_reply）的上下文。只读。"""

    session_id: str
    group_id: str | None
    user_id: str
    reply_content: str  # 实际发送的回复文本
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # 本轮 LLM 工具调用记录
    elapsed_ms: float = 0.0  # 本轮 LLM 调用耗时
    thinker_action: str = ""  # thinker 决策: "reply" / "wait" / "search"
    thinker_thought: str = ""  # thinker 内心想法


@dataclass
class ThinkerContext:
    """Thinker 决策后钩子（on_thinker_decision）的上下文。只读。"""

    session_id: str
    group_id: str | None
    user_id: str
    action: str  # "reply" / "wait" / "search"
    thought: str  # thinker 的内心想法
    elapsed_ms: float = 0.0


# ============================================================================
# AmadeusPlugin 基类
# ============================================================================


@dataclass
class CommandContext:
    """命令执行上下文，传给 Command.handler。"""

    bot: Any  # nonebot Bot 实例
    event: Any  # 原始 OneBot event
    args: str  # 命令名之后的文本（去除首尾空白）
    is_private: bool
    user_id: str
    group_id: str | None


@dataclass
class Command:
    """插件注册的文本命令。"""

    name: str  # 命令名，如 "memo"
    handler: Any  # async callable，签名为 (CommandContext) -> bool
    description: str = ""
    usage: str = ""  # 用法示例，如 "/memo list"
    pattern: str = ""  # 匹配模式，空字符串表示用 name 自动生成


@dataclass
class AdminRoute:
    """插件注册的 Admin Panel HTTP 路由。"""

    path: str  # 路由路径，如 "/api/admin/memo"
    router: Any  # FastAPI / Starlette APIRouter 实例


class AmadeusPlugin:
    """插件基类。

    只实现你需要的钩子——每个钩子都有默认空实现。
    不要在 __init__ 中做 I/O，所有初始化放在 on_startup 中。

    优先级规则：
    - 0:       核心（ChatPlugin，不可卸载）
    - 1-9:     基础设施工具（几乎总是需要）
    - 10-49:   业务插件（好感度、日程、记忆、表情包等）
    - 50-99:   辅助业务
    - 100-199: 后台任务
    - 200-299: 管线拦截（echo、元素检测）
    - 300+:    第三方/实验性
    - 数字越小，越先执行
    """

    name: str = ""
    description: str = ""
    version: str = "0.1.0"
    priority: int = 100
    enabled: bool = True
    dependencies: dict[str, str] = {}  # noqa: RUF012 — overridden per-plugin, never mutated
    author: str = ""  # 开发者签名，显示在 /plugins 列表中

    # ---- 生命周期 ----

    async def on_startup(self, ctx: PluginContext) -> None:
        """Bot 启动时调用。初始化资源、注册工具等。"""

    async def on_shutdown(self, ctx: PluginContext) -> None:
        """Bot 关闭时调用。清理资源、持久化状态等。"""

    async def on_bot_connect(self, ctx: PluginContext, bot: Any) -> None:
        """Bot 连接到 OneBot 后调用。可访问 Bot 实例发送消息/调用 API。"""

    # ---- 消息管线 ----

    async def on_message(self, ctx: MessageContext) -> bool:
        """消息到达时调用（在 thinker 之前）。

        返回 True 表示消费此消息，阻止后续插件和默认处理。
        返回 False（默认）表示不消费，继续正常流程。

        用途：复读检测、触发短语匹配、垃圾过滤等。
        """
        return False

    async def on_thinker_decision(self, ctx: ThinkerContext) -> None:
        """Thinker 决策后调用。仅通知，不可修改决策。

        用途：记录 thinker 统计数据、调试日志等。
        """

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        """System prompt 构建前调用。通过 ctx.add_block() 追加内容。

        用途：好感度等级、当前心情、日程、记忆卡片等。
        """

    async def on_post_reply(self, ctx: ReplyContext) -> None:
        """回复发送后调用。用于副作用，不可修改已发送的内容。

        用途：记录好感度互动、提取记忆、更新状态面板等。
        """

    # ---- 工具 ----

    def register_tools(self) -> list[Tool]:
        """返回本插件提供的 Tool 列表。在 on_startup 之后调用。"""
        return []

    # ---- 命令与 Admin 路由 ----

    def register_commands(self) -> list[Command]:
        """返回本插件注册的文本命令列表。"""
        return []

    def register_admin_routes(self) -> list[AdminRoute]:
        """返回本插件注册的 Admin Panel HTTP 路由列表。"""
        return []

    # ---- 定时 ----

    async def on_tick(self, ctx: PluginContext) -> None:
        """定时调用（约每分钟一次）。用于周期性任务。

        用途：日程生成、梦境整合、过期数据清理等。
        """
