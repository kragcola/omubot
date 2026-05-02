# 插件

## 已加载插件（14+2 个）

| 插件 | 版本 | 优先级 | 形态 | 功能 |
|------|------|--------|------|------|
| ChatPlugin | 1.0.3 | 0 | 单文件 | 核心聊天：消息路由、LLM 调用、tool loop |
| DateTimePlugin | 1.0.0 | 1 | 单文件 | 时间日期查询工具 |
| WebSearchPlugin | 1.0.0 | 1 | 单文件 | DuckDuckGo 网页搜索 |
| WebFetchPlugin | 1.0.0 | 1 | 单文件 | 网页内容抓取 |
| HttpApiPlugin | 1.0.0 | 1 | 单文件 | NapCat HTTP API 调用 |
| GroupAdminPlugin | 1.0.0 | 1 | 单文件 | 群管理（禁言、头衔、发消息） |
| VisionPlugin | 1.0.0 | 8 | 单文件 | 多模态图像理解 (Qwen VL) |
| StickerPlugin | 1.0.1 | 10 | 单文件 | 表情包：保存、发送、管理 |
| MemoPlugin | 1.0.1 | 20 | 单文件 | 记忆卡片：7 类 3 作用域，检索门控 |
| AffectionPlugin | 1.0.1 | 30 | 目录 | 好感度系统：分数、昵称、态度调节 |
| SchedulePlugin | 1.0.1 | 35 | 目录 | 模拟日程：每日 LLM 生成 |
| HistoryLoaderPlugin | 1.0.0 | 5 | 单文件 | 启动时加载群历史消息 |
| DreamPlugin | 1.0.0 | 150 | 单文件 | 梦境整合：定期整理记忆、清理表情包 |
| EchoPlugin | 1.0.0 | 200 | 单文件 | 复读检测：5 分钟内同消息 3 次触发 |
| ElementDetectorPlugin | 1.0.0 | 210 | 单文件 | 特殊消息元素检测 |
| DebugCommandPlugin | 1.1.0 | 300 | 单文件 | 调试指令：/plugins、/version |

## 钩子生命周期

```
on_startup → 加载配置，注册工具
     ↓
on_message → 每个消息（可拦截返回 True）
     ↓
on_pre_prompt → 注入 system prompt 块（dynamic / stable / static）
     ↓
LLM 工具循环 → 插件注册的工具可被 LLM 调用
     ↓
on_post_reply → 回复后的副作用（记录好感度等）
     ↓
on_tick → 定时触发（Dream Agent 等）
```

## 工具注册

插件通过 `register_tools()` 返回 Tool 列表：

```python
class MyPlugin(AmadeusPlugin):
    def register_tools(self) -> list[Tool]:
        return [MyTool(), AnotherTool()]
```

## 命令注册

插件通过 `register_commands()` 注册斜杠指令：

```python
def register_commands(self) -> list:
    from kernel.types import Command
    return [
        Command(name="mycmd", handler=self._handle, description="...",
                usage="/mycmd [args]", aliases=["mc"]),
    ]
```
