# 05 — 系统服务

系统服务层封装外部资源，向上暴露接口。内核不 import 服务，服务之间允许相互引用。

## 服务列表

| 服务 | 模块（规划） | 职责 |
|------|-------------|------|
| LLMClient | `services.llm.client` | Anthropic-compatible API 调用、SSE 流、工具循环 |
| PromptBuilder | `services.llm.prompt_builder` | System prompt 构建与缓存管理 |
| Thinker | `services.llm.thinker` | 轻量决策：reply/wait/search |
| DreamAgent | `plugins.dream.agent` | 后台整合记忆、清理过期数据 |
| CardStore | `services.memory.card_store` | 长期记忆卡片 CRUD + 搜索 |
| RetrievalGate | `services.memory.retrieval` | 检索门控：按需注入卡片 |
| MessageLog | `services.memory.message_log` | 消息 SQLite 持久化 |
| GroupTimeline | `services.memory.timeline` | 群聊时间线、调度触发判定 |
| ShortTermMemory | `services.memory.short_term` | 会话内短期记忆（deque） |
| StateBoard | `services.memory.state_board` | 群聊状态面板（活跃用户、话题） |
| ImageCache | `services.media.image_cache` | 图片下载/缩放/缓存 |
| StickerStore | `services.media.sticker_store` | 表情包库、SHA256 去重 |
| ToolRegistry | `services.tools.registry` | 工具注册表、按名分发执行 |
| GroupChatScheduler | `services.scheduler` | 群聊调度：@触发/debounce/batch |
| UsageTracker | `services.llm.usage` | LLM 用量记录与统计 |
| Humanizer | `services.humanizer` | 回复长度/延迟人性化 |
| IdentityManager | `services.identity` | 从 config/soul/ 加载人格配置 |

## 服务接口约定

服务通过 `PluginContext` 暴露给插件：

```python
class MyPlugin(AmadeusPlugin):
    async def on_startup(self, ctx: PluginContext) -> None:
        self._cards = ctx.card_store        # CardStore
        self._images = ctx.image_cache      # ImageCache
        self._llm = ctx.llm_client          # LLMClient
```

### LLMClient

```python
# 核心方法（规划）
async def chat(
    session_id: str,
    messages: list[dict],
    tools: list[dict],
    system: str,
    ...
) -> str: ...
```

### CardStore

```python
# 核心方法（规划）
async def add_card(scope: str, scope_id: str, content: str, **kw) -> str: ...
async def search_cards(keyword: str, limit: int = 5) -> list[dict]: ...
async def list_cards(scope: str, scope_id: str) -> list[dict]: ...
async def update_card(card_id: str, content: str) -> bool: ...
async def delete_card(card_id: str) -> bool: ...
```

### ImageCache

```python
# 核心方法（规划）
async def download(url: str) -> Path: ...
async def downscale(path: Path, max_size: tuple) -> Path: ...
def get_cached(url: str) -> Path | None: ...
```

### StickerStore

```python
# 核心方法（规划）
async def save_sticker(path: Path, tags: list[str]) -> str: ...
async def search_by_tag(tag: str, limit: int = 10) -> list[dict]: ...
async def get_random() -> dict | None: ...
```

## 服务实现规范

1. **所有服务类放在 `services/` 下**
2. **构造函数接受配置对象，不接受隐式全局状态**
3. **异步方法使用 `async/await`**
4. **内部异常不向上传播** — 打日志后返回 safe fallback
5. **日志绑定服务频道** — `logger.bind(channel="cards")`

## 服务迁移状态

所有服务逻辑已于 2026-05-01 迁移完成。代码统一位于 `services/`、`kernel/`、`plugins/` 三层目录下，`src/` 垫片目录已清理移除。
