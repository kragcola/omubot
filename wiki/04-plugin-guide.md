# 04 — 插件开发指南

## 最小插件

单文件插件（推荐）：

```python
# plugins/my_plugin.py
from kernel.types import AmadeusPlugin, PluginContext

class MyPlugin(AmadeusPlugin):
    name = "my_plugin"
    description = "一个示例插件"
    version = "0.1.0"
    priority = 50

    async def on_startup(self, ctx: PluginContext) -> None:
        self._store = ctx.card_store

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        ctx.add_block("当前天气：晴", label="weather", position="dynamic")
```

可选侧车清单文件 `plugins/my_plugin.json`：

```json
{
  "name": "my_plugin",
  "version": "0.1.0",
  "priority": 50,
  "dependencies": {
    "vision": ">=1.0.0"
  }
}
```

目录插件（多文件或需要子模块时）：

```
plugins/my_plugin/
├── plugin.py          # 必须有此文件 + AmadeusPlugin 子类
├── plugin.json        # 可选：覆盖元数据、声明依赖
└── helper.py          # 额外的子模块
```

两种形态完全等价，PluginBus 在发现时统一处理。

## 选择优先级

```
0         — 核心（ChatPlugin）
1-9       — 基础设施工具（几乎总是需要）
10-49     — 业务插件（好感度、日程、记忆、表情包）
50-99     — 辅助业务
100-199   — 后台任务
200-299   — 管线拦截（echo、元素检测）
300+      — 第三方/实验性
```

选择原则：
- 应该**最早执行**的（如消息预处理）用较低数字
- 应该**最晚执行**的（如日志记录）用较高数字
- 不确定时从 100 开始，后续调整

## 常用模式

### 消息拦截器

```python
class SpamFilter(AmadeusPlugin):
    name = "spam_filter"
    priority = 200  # 管线拦截

    async def on_message(self, ctx: MessageContext) -> bool:
        if "广告" in str(ctx.content):
            return True  # 消费消息，阻止后续处理
        return False
```

### Prompt 追加器

```python
class AffectionPlugin(AmadeusPlugin):
    name = "affection"
    priority = 10

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        level = self.get_affection(ctx.user_id)
        ctx.add_block(
            f"[好感度] {ctx.identity.name} 对 {ctx.user_id} 的好感度为 {level}/10",
            label="affection",
            position="stable",
        )
```

### 工具注册器

```python
class MemoTools(AmadeusPlugin):
    name = "memo_tools"
    priority = 5

    def register_tools(self) -> list[Tool]:
        return [LookupCardsTool(), AppendMemoTool()]
```

### 回复后副作用

```python
class InteractionLogger(AmadeusPlugin):
    name = "interaction_logger"
    priority = 300

    async def on_post_reply(self, ctx: ReplyContext) -> None:
        await self.log_to_db(
            user=ctx.user_id,
            reply=ctx.reply_content,
            elapsed=ctx.elapsed_ms,
        )
```

### 定时任务

```python
class HealthCheck(AmadeusPlugin):
    name = "health_check"
    priority = 100

    async def on_tick(self, ctx: PluginContext) -> None:
        await self.ping_external_services()
```

## 最佳实践

1. **不要在 `__init__` 中做 I/O** — 所有初始化放在 `on_startup`
2. **保存服务引用** — 在 `on_startup` 中从 `PluginContext` 获取并保存
3. **不需要的钩子不要覆写** — 基类已有空实现
4. **单个钩子只做一件事** — 复杂功能拆成多个小插件
5. **用 position 控制缓存** — static/stable/dynamic 选对，避免不必要的缓存失效
6. **handle errors gracefully** — 框架已做异常隔离，但你仍可在内部处理预期错误
7. **插件间不直接引用** — 通过共享服务或 Context 通信
8. **插件数据放 `plugin_data_dir`** — 通过 `ctx.plugin_data_dir / "your_plugin"` 创建专属子目录存放日志、缓存等，该目录在 `storage/plugins/` 下且已被 gitignore

## 插件发现

`PluginBus.discover_plugins("plugins")` 自动扫描，支持两种形态：

```
plugins/
├── chat.py              → 单文件插件，注册 ChatPlugin
├── chat.json            → 侧车清单（可选）
├── echo.py              → 单文件插件，注册 EchoPlugin
├── affection/           → 目录插件（多文件）
│   ├── plugin.py        → 注册 AffectionPlugin
│   ├── plugin.json      → 可选：覆盖元数据、声明依赖
│   └── engine.py        → 子模块
├── utils/               → 跳过（无 plugin.py）
└── old_module.py        → 跳过（无 AmadeusPlugin 子类）
```

发现规则：
1. 子目录包含 `plugin.py` → 目录插件（优先于同名 .py 文件）
2. 独立 `.py` 文件 → 单文件插件，检查是否有 `AmadeusPlugin` 子类
3. `.py` 文件的侧车 `.json` → 自动拾取并覆盖实例属性
4. 子目录的 `plugin.json` → 自动拾取并覆盖实例属性
5. `__init__.py` 跳过

## 测试插件

```python
import pytest
from kernel.bus import PluginBus
from kernel.types import PluginContext, MessageContext

@pytest.mark.asyncio
async def test_my_plugin():
    bus = PluginBus()
    bus.register(MyPlugin())
    await bus.fire_on_startup(PluginContext())

    msg = MessageContext(
        session_id="group_123",
        group_id="123",
        user_id="456",
        content="hello",
        raw_message={},
    )
    consumed = await bus.fire_on_message(msg)
    assert not consumed  # 插件不应消费普通消息
```
