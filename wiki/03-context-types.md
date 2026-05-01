# 03 — Context 类型

每个钩子接收特定的 Context 作为入参。这些类型定义在 `kernel.types`。

## PluginContext

生命周期钩子和 `on_tick` 的上下文。暴露全部系统服务引用。

```python
@dataclass
class PluginContext:
    config: Any = None           # BotConfig
    storage_dir: Path            # 存储根目录
    msg_log: Any = None          # MessageLog
    timeline: Any = None         # GroupTimeline
    short_term: Any = None       # ShortTermMemory
    card_store: Any = None       # CardStore
    retrieval: Any = None        # RetrievalGate
    state_board: Any = None      # StateBoard
    image_cache: Any = None      # ImageCache
    sticker_store: Any = None    # StickerStore
    llm_client: Any = None       # LLMClient
    prompt_builder: Any = None   # PromptBuilder
    thinker: Any = None          # Thinker
    tool_registry: Any = None    # ToolRegistry
    scheduler: Any = None        # GroupChatScheduler
    usage_tracker: Any = None    # UsageTracker
    humanizer: Any = None        # Humanizer
    identity: Any = None         # Identity 实例
```

字段类型为 `Any` 是因为内核不 import 服务模块。运行时由 `bot.py` 注入实际对象。

**使用方式**：在 `on_startup` 中保存所需服务引用：

```python
class MyPlugin(AmadeusPlugin):
    async def on_startup(self, ctx: PluginContext) -> None:
        self._card_store = ctx.card_store
        self._storage = ctx.storage_dir / "my_plugin"
```

## MessageContext

`on_message` 的上下文。包含原始消息的所有信息。

```python
@dataclass
class MessageContext:
    session_id: str              # "group_123456" 或 "private_123456"
    group_id: str | None         # 群聊为群号，私聊为 None
    user_id: str                 # 发送者 QQ 号
    content: Content             # 解析后的消息内容
    raw_message: dict[str, Any]  # 原始 OneBot message 字典
    is_at: bool = False          # 是否 @ 了 bot
    is_private: bool = False     # 是否为私聊
    message_id: int | None = None

    @property
    def is_group(self) -> bool:  # → group_id is not None
```

## PromptContext

`on_pre_prompt` 的上下文。核心方法是 `add_block()`。

```python
@dataclass
class PromptContext:
    session_id: str
    group_id: str | None
    user_id: str
    identity: Identity
    conversation_text: str = ""   # 最近对话文本
    force_reply: bool = False
    privacy_mask: bool = True

    blocks: list[PromptBlock]     # 内部可变列表

    def add_block(
        self,
        text: str,
        *,
        label: str = "",
        position: Literal["static", "stable", "dynamic"] = "dynamic",
    ) -> None:
```

### PromptBlock

```python
@dataclass
class PromptBlock:
    text: str                     # 注入的文本内容
    label: str = ""               # 日志/调试用标签
    position: Literal["static", "stable", "dynamic"] = "dynamic"
```

position 语义：

| position | 含义 | 缓存行为 | 示例 |
|----------|------|----------|------|
| `static` | 永不变化 | cache breakpoint 1 之前 | 人格、行为指令 |
| `stable` | 罕变 | cache breakpoint 2 之前 | 全局索引、工具列表 |
| `dynamic` | 每轮可变 | cache breakpoint 2 之后 | 实体记忆、好感度 |

## ReplyContext

`on_post_reply` 的上下文。只读。

```python
@dataclass
class ReplyContext:
    session_id: str
    group_id: str | None
    user_id: str
    reply_content: str            # 实际发送的回复文本
    tool_calls: list[dict[str, Any]]  # 本轮 LLM 工具调用记录
    elapsed_ms: float = 0.0       # 本轮 LLM 调用耗时
    thinker_action: str = ""      # "reply" / "wait" / "search"
    thinker_thought: str = ""     # thinker 内心想法
```

## ThinkerContext

`on_thinker_decision` 的上下文。只读，纯通知。

```python
@dataclass
class ThinkerContext:
    session_id: str
    group_id: str | None
    user_id: str
    action: str                   # "reply" / "wait" / "search"
    thought: str                  # thinker 内心想法
    elapsed_ms: float = 0.0
```

## ToolContext

工具执行上下文，传入 `Tool.execute()`。

```python
@dataclass
class ToolContext:
    bot: Any = None               # nonebot Bot 实例
    user_id: str = ""
    group_id: str | None = None
    session_id: str = ""
    extra: dict[str, Any]         # 扩展字段
```

## Identity

轻量人格模型。

```python
@dataclass
class Identity:
    id: str = ""                  # 人格 ID
    name: str = ""                # Bot 名字
    personality: str = ""         # 人格描述
    proactive: str = ""           # "## 插话方式" 内容
```

## Content 类型

多模态消息内容。

```python
class TextBlock(Protocol):
    type: Literal["text"]
    text: str

class ImageRefBlock(Protocol):
    type: Literal["image_ref"]
    path: str                     # 磁盘路径
    media_type: str               # e.g. "image/jpeg"

ContentBlock = TextBlock | ImageRefBlock
Content = str | list[ContentBlock]
```
