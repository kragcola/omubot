"""Omubot 内核层。

提供框架基础设施：
- PluginBus: 插件注册/调度
- 类型契约: Context 类型、Plugin 基类、Tool ABC
- 配置系统: BotConfig + TOML 加载器
- 消息路由: NoneBot 事件处理器
"""

from kernel.bus import PluginBus
from kernel.config import (
    AntiDetectConfig,
    BotConfig,
    CompactConfig,
    ContextConfig,
    GroupConfig,
    GroupOverride,
    KernelConfig,
    LLMConfig,
    LogChannelConfig,
    LogConfig,
    NapcatConfig,
    QwenVLConfig,
    ResolvedGroupConfig,
    SoulConfig,
    ThinkerConfig,
    UsageConfig,
    VisionConfig,
    load_config,
    load_plugin_config,
)
from kernel.manifest import (
    PluginManifest,
    check_version,
    parse_semver,
)
from kernel.types import (
    AdminRoute,
    AmadeusPlugin,
    Command,
    CommandContext,
    Content,
    ContentBlock,
    Identity,
    ImageRefBlock,
    MessageContext,
    PluginContext,
    PromptBlock,
    PromptContext,
    ReplyContext,
    TextBlock,
    ThinkerContext,
    Tool,
    ToolContext,
)

__all__ = [
    "AdminRoute",
    "AmadeusPlugin",
    "AntiDetectConfig",
    "BotConfig",
    "Command",
    "CommandContext",
    "CompactConfig",
    "Content",
    "ContentBlock",
    "ContextConfig",
    "GroupConfig",
    "GroupOverride",
    "Identity",
    "ImageRefBlock",
    "KernelConfig",
    "LLMConfig",
    "LogChannelConfig",
    "LogConfig",
    "MessageContext",
    "NapcatConfig",
    "PluginBus",
    "PluginContext",
    "PluginManifest",
    "PromptBlock",
    "PromptContext",
    "QwenVLConfig",
    "ReplyContext",
    "ResolvedGroupConfig",
    "SoulConfig",
    "TextBlock",
    "ThinkerConfig",
    "ThinkerContext",
    "Tool",
    "ToolContext",
    "UsageConfig",
    "VisionConfig",
    "check_version",
    "load_config",
    "load_plugin_config",
    "parse_semver",
]
