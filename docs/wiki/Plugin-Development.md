# 插件开发指南

本指南介绍如何为 omubot 编写插件：从最简结构到命令注册、工具注册、钩子系统。

## 插件形态

Omubot 现在只支持统一目录插件。根目录单文件插件已经取消运行时加载，只会在插件索引里被标为 `legacy_single_file_unsupported`。

标准结构：

```text
plugins/<name>/
  __init__.py
  plugin.py
  plugin.json
  config.default.json
  config.schema.json
```

`class` 名称建议以 `Plugin` 结尾，`name` 用于标识。需要兼容旧导入时，在 `__init__.py` re-export 关键类和函数。

## 最简插件

```python
from kernel.types import AmadeusPlugin, MessageContext, PluginContext

class HelloPlugin(AmadeusPlugin):
    name = "hello"
    description = "演示插件：回复所有消息 Hello World"
    version = "0.1.0"
    priority = 100

    async def on_startup(self, ctx: PluginContext) -> None:
        pass  # 初始化资源
```

### 属性一览

| 属性 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `name` | `str` | 是 | 插件标识，全局唯一 |
| `description` | `str` | 是 | 一行简介，显示在 `/plugins` 列表中 |
| `version` | `str` | 是 | 语义化版本号 |
| `priority` | `int` | 是 | 越小越先执行（见下方优先级表） |
| `author` | `str` | 否 | 开发者签名，默认 `"Omubot"` |
| `dependencies` | `dict` | 否 | 依赖声明，如 `{"web_search": ">=0.1.0"}` |
| `enabled` | `bool` | 否 | 是否启用，默认 `True` |

### 优先级规范

| 范围 | 角色 | 示例 |
|------|------|------|
| 0 | 核心（不可卸载） | ChatPlugin |
| 1-9 | 基础设施工具 | DateTimePlugin, WebSearchPlugin |
| 10-49 | 业务插件 | FoodPlugin, StickerPlugin, MemoPlugin |
| 50-99 | 辅助业务 | — |
| 100-199 | 后台任务 | HistoryLoaderPlugin, DreamPlugin |
| 200-299 | 管线拦截 | EchoPlugin, ElementDetectorPlugin |
| 300+ | 第三方/实验性 | DebugCommandPlugin |

## 钩子生命周期

```
on_startup → 加载配置，注册资源
     ↓
on_bot_connect → Bot 上线通知
     ↓
on_message → 每个消息（可拦截返回 True）
     ↓
on_thinker_decision → Thinker 决策后通知（只读）
     ↓
on_pre_prompt → 注入 system prompt 块
     ↓
LLM 工具循环 → 插件注册的工具可被 LLM 调用
     ↓
on_post_reply → 回复后的副作用
     ↓
on_tick → 定时触发（约每分钟一次）
     ↓
on_shutdown → 清理资源
```

### 钩子速查

| 钩子 | 签名 | 用途 |
|------|------|------|
| `on_startup(ctx)` | `PluginContext` | 初始化资源、加载配置 |
| `on_shutdown(ctx)` | `PluginContext` | 清理资源、持久化状态 |
| `on_bot_connect(ctx, bot)` | + `Bot` | Bot 上线后调用 |
| `on_message(ctx) -> bool` | `MessageContext` | 拦截消息，返回 `True` 消费 |
| `on_thinker_decision(ctx)` | `ThinkerContext` | 只读通知，不可修改 |
| `on_pre_prompt(ctx)` | `PromptContext` | 通过 `ctx.add_block()` 注入 |
| `on_post_reply(ctx)` | `ReplyContext` | 记录副作用 |
| `on_tick(ctx)` | `PluginContext` | 定时任务 |

只实现需要的钩子——每个钩子都有默认空实现。

## 命令注册

通过 `register_commands()` 声明式注册，框架自动处理权限门禁、参数校验、帮助文本。

### 基础命令

```python
def register_commands(self) -> list:
    from kernel.types import Command
    return [
        Command(
            name="mycmd",
            handler=self._handle_mycmd,
            description="我的命令",
            usage="/mycmd <参数>",
            aliases=["mc"],
            require_args=True,   # 无参数时自动回复 usage
        ),
    ]

async def _handle_mycmd(self, cmd_ctx: RichCommandContext) -> None:
    from nonebot.adapters.onebot.v11 import Message
    text = cmd_ctx.args.strip()
    await cmd_ctx.bot.send(cmd_ctx.event, Message(f"收到: {text}"))
```

### 带子命令

```python
Command(
    name="mgr",
    handler=self._handle_mgr,
    description="管理工具",
    hidden=True,                    # 父命令不显示在自身 help 中
    sub_commands=[
        Command(
            name="list",
            handler=self._handle_list,
            description="列出所有项目",
        ),
        Command(
            name="delete",
            handler=self._handle_delete,
            description="删除项目",
            usage="/mgr delete <id>",
            require_args=True,
            admin_only=True,
        ),
    ],
)
```

### 门禁字段

所有门禁由 `CommandDispatcher` 在调用 handler 前统一检查，handler 不需要写任何权限代码：

| 字段 | 类型 | 默认值 | 效果 |
|------|------|--------|------|
| `admin_only` | `bool` | `False` | 非管理员自动回复"无权限" |
| `private_only` | `bool` | `False` | 群聊自动回复"请在私聊中使用此指令" |
| `require_args` | `bool` | `False` | 无参数时自动回复 `usage` |
| `hidden` | `bool` | `False` | 在 `format_help()` 中隐藏（占位父命令） |
| `passthrough_unknown` | `bool` | `False` | 未知子命令不报错，透传给父 handler |

门禁从父命令继承：父命令设 `admin_only=True`，所有子命令自动受保护。

### RichCommandContext

Handler 接收 `RichCommandContext`，包含消息信息 + 全部系统服务：

```python
async def _handle_mycmd(self, cmd_ctx: RichCommandContext) -> None:
    # 消息上下文
    user_id = cmd_ctx.user_id      # str
    group_id = cmd_ctx.group_id    # str | None
    is_private = cmd_ctx.is_private  # bool
    args = cmd_ctx.args            # str（命令名之后的文本）
    bot = cmd_ctx.bot              # NoneBot Bot 实例
    event = cmd_ctx.event          # 原始 OneBot event

    # 命令元数据
    cmd_ctx.command      # 当前匹配的 Command
    cmd_ctx.root_command # 顶层父 Command（用于 format_help()）

    # 系统服务（不需要 self._ctx 模式）
    plugin_ctx = cmd_ctx.plugin_ctx
    plugin_ctx.card_store    # CardStore 记忆系统
    plugin_ctx.llm_client    # LLMClient
    plugin_ctx.sticker_store # StickerStore 表情包
    plugin_ctx.scheduler     # GroupChatScheduler
    plugin_ctx.tool_registry # ToolRegistry
    plugin_ctx.bus           # PluginBus
    # ... 还有 image_cache, msg_log, timeline 等
```

### 自动帮助

`format_help()` 从 `Command` 元数据递归生成帮助文本，自动标注门禁：

```python
help_text = cmd_ctx.root_command.format_help()
# 输出：
# 管理食物偏好配置：
# /food help — 显示全部食物偏好管理指令
# /food search <参数> — 开关 Web 搜索功能（仅管理员）
# /food like <参数> — 添加喜欢的食物偏好
# /food info — 查看你当前的食物偏好和地区（仅私聊）
```

## 工具注册

通过 `register_tools()` 返回 `Tool` 列表，LLM 可调用这些工具：

```python
from kernel.types import Tool
from services.tools.context import ToolContext

class MyTool(Tool):
    name = "my_tool"
    description = "我的工具：做一些事情"
    parameters = {
        "type": "object",
        "properties": {
            "input_text": {
                "type": "string",
                "description": "输入文本",
            },
        },
        "required": ["input_text"],
    }

    async def execute(self, ctx: ToolContext, **kwargs) -> str:
        text = kwargs.get("input_text", "")
        return f"处理结果: {text}"

class MyPlugin(AmadeusPlugin):
    def register_tools(self) -> list[Tool]:
        return [MyTool()]
```

## Prompt 注入

在 `on_pre_prompt` 中通过 `ctx.add_block()` 向 system prompt 注入内容：

```python
async def on_pre_prompt(self, ctx: PromptContext) -> None:
    if ctx.group_id:
        return  # 群聊不注入敏感信息

    ctx.add_block(
        text="[我的插件] 当前状态：活跃",
        label="my_plugin_status",
        position="stable",  # "static" / "stable" / "dynamic"
    )
```

`position` 控制缓存行为：
- `static`: 永不变化，放缓存断点 1 之前
- `stable`: 罕变，放缓存断点 2 之前
- `dynamic`: 每轮可变，放缓存断点 2 之后

## 消息拦截

`on_message` 返回 `True` 消费消息，阻止后续处理：

```python
async def on_message(self, ctx: MessageContext) -> bool:
    if ctx.is_private:
        return False

    text = ctx.raw_message.get("plain_text", "")
    if "关键词" in text:
        await ctx.bot.send(ctx.event, Message("检测到关键词"))
        return True

    return False
```

## 定时任务

`on_tick` 约每分钟触发一次：

```python
async def on_tick(self, ctx: PluginContext) -> None:
    now = time.time()
    if now - self._last_check > 3600:
        self._last_check = now
        await self._do_hourly_cleanup()
```

长周期任务使用自己的计时器，不要在 `on_tick` 中做阻塞操作。

## 系统服务访问

`PluginContext` 在 `on_startup` 时传入，暴露所有系统服务。不要在 `__init__` 中保存引用——此时服务还未初始化：

```python
# 正确：在 on_startup 中保存
async def on_startup(self, ctx: PluginContext) -> None:
    self._ctx = ctx
    self._sticker_store = ctx.sticker_store  # 按需提取

# 在命令 handler 中通过 RichCommandContext 访问
async def _handle_xxx(self, cmd_ctx: RichCommandContext) -> None:
    store = cmd_ctx.plugin_ctx.card_store  # 不需要 self._ctx
```

## 完整示例

参见以下生产级插件：

| 插件 | 特点 | 文件 |
|------|------|------|
| FoodPlugin | 命令注册、子命令、门禁字段、自动帮助、工具调用 | [plugins/food/plugin.py](../../plugins/food/plugin.py) |
| DebugCommandPlugin | 最小命令注册示例、admin_only | [plugins/debug_commands/plugin.py](../../plugins/debug_commands/plugin.py) |
| EchoPlugin | 消息拦截、on_message 返回 True | [plugins/echo/plugin.py](../../plugins/echo/plugin.py) |
| StickerPlugin | 工具注册、Prompt 注入 | [plugins/sticker/plugin.py](../../plugins/sticker/plugin.py) |

## 最佳实践

1. **不要写权限检查代码**：使用 `admin_only`、`private_only` 门禁字段，由 dispatcher 统一检查
2. **不要写参数校验代码**：使用 `require_args=True`，由 dispatcher 统一检查
3. **不要写帮助文本代码**：描述写在 `Command` 元数据中，用 `format_help()` 自动生成
4. **Handler 从 `cmd_ctx.plugin_ctx` 访问服务**：不需要 `self._ctx` 间接引用
5. **`on_startup` 中初始化，不在 `__init__` 中**：`__init__` 时系统服务尚未就绪
6. **不要 import 项目内部模块到内核层**：内核 (`kernel/`) 不依赖任何项目内模块
7. **用户字符串用中文，日志用英文**：`_L.info("food library loaded | entries={}", n)`
