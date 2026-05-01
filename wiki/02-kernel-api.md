# 02 — 内核 API

## PluginBus

插件总线是内核的唯一调度器。一个进程通常只有一个实例。

### 导入

```python
from kernel.bus import PluginBus
```

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `plugins` | `list[AmadeusPlugin]` | 已注册插件列表（只读副本） |
| `started` | `bool` | 是否已调用 `fire_on_startup` |

### 注册与卸载

#### `register(plugin: AmadeusPlugin) -> None`

注册一个插件。按 `priority` 升序插入，同优先级保持注册顺序。

**限制**：必须在 `fire_on_startup()` 之前调用，否则抛 `RuntimeError`。

```python
bus = PluginBus()
bus.register(MyPlugin())          # priority=50
bus.register(CorePlugin())        # priority=0 → 排到最前
bus.register(AnotherPlugin())     # priority=50 → 排在 MyPlugin 之后
```

#### `unregister(name: str) -> bool`

按名称移除插件。返回 `True` 表示成功移除。

```python
bus.unregister("echo")  # → True
bus.unregister("nonexistent")  # → False
```

#### `get_plugin(name: str) -> AmadeusPlugin | None`

按名称查找插件。

```python
plugin = bus.get_plugin("chat")
if plugin:
    print(plugin.version)
```

### 生命周期调度

#### `fire_on_startup(ctx: PluginContext) -> None`

按优先级顺序调用所有插件的 `on_startup`。调用后将 `started` 设为 `True`。

#### `fire_on_shutdown(ctx: PluginContext) -> None`

按优先级**倒序**调用所有插件的 `on_shutdown`（先启动的后关闭）。

### 消息管线调度

#### `fire_on_message(ctx: MessageContext) -> bool`

按优先级顺序调用 `on_message`，直到有插件返回 `True`。返回 `True` 表示消息已被消费，调用方应停止后续处理。

```python
consumed = await bus.fire_on_message(msg_ctx)
if consumed:
    return  # 消息已被插件消费，不再处理
# 继续正常的 thinker → LLM → reply 流程
```

#### `fire_on_pre_prompt(ctx: PromptContext) -> None`

按优先级调用 `on_pre_prompt`，各插件通过 `ctx.add_block()` 追加内容。调用后读取 `ctx.blocks` 获取所有追加的 PromptBlock。

```python
prompt_ctx = PromptContext(session_id="...", ...)
await bus.fire_on_pre_prompt(prompt_ctx)
for block in prompt_ctx.blocks:
    print(block.label, block.position, len(block.text))
```

#### `fire_on_post_reply(ctx: ReplyContext) -> None`

按优先级调用 `on_post_reply`。用于副作用（记录互动、提取记忆等），不可修改已发送的回复。

#### `fire_on_thinker_decision(ctx: ThinkerContext) -> None`

通知所有插件 thinker 决策结果。纯通知，不可修改决策。

### 工具收集

#### `collect_tools() -> list[Tool]`

收集所有插件注册的工具。在 `on_startup` 之后调用，结果传给 ToolRegistry。

```python
await bus.fire_on_startup(ctx)
tools = bus.collect_tools()
registry = ToolRegistry(tools)
```

### 定时调度

#### `fire_on_tick(ctx: PluginContext) -> None`

按优先级调用 `on_tick`。通常由定时器每分钟触发一次。

### 插件发现

#### `discover_plugins(directory: str | Path) -> int`

扫描目录，自动发现并注册插件。返回新注册的插件数量。

**发现规则**：每个子目录若包含 `plugin.py`，且其中有 `AmadeusPlugin` 子类，则自动实例化并注册。

```python
count = bus.discover_plugins("plugins")
print(f"Discovered {count} plugins")
```

目录结构示例：
```
plugins/
├── chat/
│   └── plugin.py       # class ChatPlugin(AmadeusPlugin): ...
├── affection/
│   └── plugin.py       # class AffectionPlugin(AmadeusPlugin): ...
└── echo/
    └── plugin.py       # class EchoPlugin(AmadeusPlugin): ...
```

## 异常隔离 (`_safe_call`)

每个钩子调用都经过 `_safe_call` 包装：

- 单个插件异常 → 打 warning 日志，继续执行后续插件
- 超过 100ms → debug 日志
- 超过 5s → warning 日志

插件开发者无需在钩子内部做 try/except——框架已处理。

## 日志频道

```python
from loguru import logger
_L = logger.bind(channel="bus")
```

所有 PluginBus 日志都带 `channel=bus`，便于按频道过滤。

## 配置模块

内核配置位于 `kernel.config`，提供 23 个 Pydantic 模型 + `load_config()`。

### 导入

```python
from kernel.config import BotConfig, KernelConfig, GroupConfig, LLMConfig, VisionConfig, load_config
```

### KernelConfig

```python
class KernelConfig(BaseModel):
    plugin_dirs: list[str] = ["plugins"]
    disabled_plugins: list[str] = []
    max_hook_time_ms: int = 5000
```

### BotConfig

全局配置根模型，包含所有子系统配置作为子字段：

```python
config = load_config()
print(config.llm.model)          # LLM 模型名
print(config.group.at_only)      # 是否仅 @ 回复
print(config.kernel.plugin_dirs) # 插件搜索目录
group_cfg = config.group.resolve(123456)  # 合并群覆盖
```

### load_config()

```python
def load_config(
    config_path: str | None = None,
    cli_overrides: dict[str, str] | None = None,
) -> BotConfig: ...
```

三层合并：Pydantic 默认值 → TOML 文件 → 环境变量 → CLI 参数。

### 向后兼容

旧 `src/` 路径已于 2026-05-01 清理移除。所有配置模型和加载器统一从 `kernel.config` 导入。
