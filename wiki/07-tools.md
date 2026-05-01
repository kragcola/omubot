# 07 — 工具系统

## Tool ABC

工具是 LLM 可调用的函数。所有工具必须继承 `Tool` 并实现 4 个抽象成员：

```python
from omubot import Tool, ToolContext

class MyTool(Tool):
    @property
    def name(self) -> str:
        return "my_tool"

    @property
    def description(self) -> str:
        return "描述这个工具做什么，LLM 据此决定何时调用。"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "arg1": {"type": "string", "description": "参数说明"},
            },
            "required": ["arg1"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        result = await self.do_something(kwargs["arg1"])
        return result  # 文本结果返回给 LLM
```

## ToolContext

每次工具调用时传入：

```python
@dataclass
class ToolContext:
    bot: Any = None          # Bot 实例（可调用 API）
    user_id: str = ""        # 触发用户
    group_id: str | None     # 群号（私聊为 None）
    session_id: str = ""     # 会话标识
    extra: dict[str, Any]    # 扩展字段
```

用途：
- `ctx.bot.call_api("send_group_msg", ...)` — 发送消息
- `ctx.bot.call_api("get_group_member_info", ...)` — 查成员信息
- `ctx.user_id` — 确定操作目标
- `ctx.group_id` — 确定操作 scope

## to_openai_tool()

内置方法，生成 Anthropic-compatible 的 tool 定义：

```python
tool = MyTool()
tool.to_openai_tool()
# → {
#     "type": "function",
#     "function": {
#         "name": "my_tool",
#         "description": "描述...",
#         "parameters": { ... }
#     }
# }
```

## 注册工具

插件在 `register_tools()` 方法中返回工具列表：

```python
class SearchPlugin(AmadeusPlugin):
    name = "search"
    priority = 5

    def register_tools(self) -> list[Tool]:
        return [
            WebSearchTool(),
            ImageSearchTool(),
            LocalSearchTool(),
        ]
```

PluginBus 在 `fire_on_startup` 之后调用 `collect_tools()` 收集全部工具：

```python
await bus.fire_on_startup(ctx)
tools = bus.collect_tools()           # 所有插件的工具
registry = ToolRegistry(tools)        # 传入 ToolRegistry
```

## 工具调用流程

```
LLM 返回 tool_use block
  │
  ▼
ToolRegistry.dispatch(tool_name, args)
  │
  ▼
Tool.execute(ctx, **args)
  │
  ▼
返回文本结果 → 追加到 messages → 继续 LLM 调用
  │
  ▼
最多 5 轮工具循环，或 LLM 返回纯文本
```

## 工具设计原则

1. **description 要具体** — LLM 据此判断何时调用，模糊描述导致误调用
2. **parameters 要完整** — JSON Schema 必须准确描述所有参数
3. **execute 返回纯文本** — 不要返回 markdown 或结构化数据（除非 prompt 中另有指令）
4. **execute 必须幂等安全** — 同一个工具可能被 LLM 重复调用
5. **execute 内部处理异常** — 返回错误描述文本给 LLM，不要抛异常
6. **name 使用 snake_case** — 与 Anthropic API 惯例一致

## 常用工具类型

| 类型 | 示例 | 说明 |
|------|------|------|
| 查询 | `lookup_cards` | 搜索记忆卡片 |
| 写入 | `append_memo` | 追加观察记录 |
| 操作 | `send_sticker` | 发送表情包 |
| 外部 | `web_search` | 网络搜索 |
| 管理 | `delete_card` | 删除卡片 |
