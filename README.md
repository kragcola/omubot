# Omubot

基于 NoneBot2 的三层可扩展 QQ 机器人框架。

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-1.1.0-blue.svg)](CHANGELOG.md)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Ruff](https://img.shields.io/badge/lint-ruff-orange.svg)](https://github.com/astral-sh/ruff)

## 架构

```
QQ ←→ NapCat (WS) ←→ NoneBot2
                        └── Omubot 三层框架
                             ├── Kernel     PluginBus · 类型契约 · 插件发现 · 指令调度
                             ├── Services   LLM · 记忆 · 时间线 · 版本 · 调度
                             └── Plugins    15 个可开关、可插拔的功能插件
```

- **内核层** — 零 I/O，零外部依赖。定义调度规则和类型契约，不改 API
- **系统服务层** — LLM 客户端、记忆卡片、群聊时间线、图片缓存、指令分发、版本管理
- **插件层** — 好感度、日程、记忆、表情包、梦境、视觉、复读等，通过钩子接入

## 快速开始

### 前置

- Python 3.12+ / [uv](https://github.com/astral-sh/uv)
- Docker + Docker Compose
- 一个 QQ 号（建议用小号）

### 1. 安装

```bash
git clone https://github.com/kragcola/omubot.git
cd omubot
uv sync
```

### 2. 配置

```bash
cp config.example.toml config/config.toml    # 编辑：LLM 模型、群聊开关
# 创建 config/.env，填写 SUPERUSERS 和 LLM_API_KEY
# 创建 config/soul/identity.md 和 config/soul/instruction.md
```

配置模板见 [config.example.toml](config.example.toml)，完整文档见 [wiki/](wiki/)。

### 3. 启动

```bash
# Docker（推荐）
docker compose up -d --build

# 或本地运行
docker compose up napcat -d
uv run python bot.py
```

### 4. 验证

- 浏览器打开 `http://localhost:6099` → 扫码登录 QQ
- 在群里 @bot 发消息
- 访问 Admin 面板：`http://localhost:8081/admin/`
- 私聊发送 `/version` 检查版本

## 插件

| 插件 | 优先级 | 功能 |
|------|--------|------|
| ChatPlugin | 0 | 核心聊天：消息路由、LLM 调用、tool loop、/debug 及其子命令 save/send |
| DateTimePlugin | 1 | 时间日期查询 |
| WebSearchPlugin | 1 | DuckDuckGo 网页搜索 |
| WebFetchPlugin | 1 | 网页内容抓取 |
| HttpApiPlugin | 1 | NapCat HTTP API 调用 |
| GroupAdminPlugin | 1 | 群管理（禁言、头衔、发消息） |
| StickerPlugin | 10 | 表情包库：收藏、检索、发送（依赖系统层 vision） |
| MemoPlugin | 20 | 记忆卡片：7 类 3 作用域，检索门控 |
| AffectionPlugin | 30 | 好感度系统：分数、昵称、态度调节 |
| SchedulePlugin | 35 | 模拟日程：每日 LLM 生成，结合真实日期 |
| HistoryLoaderPlugin | 5 | 启动时加载群历史消息 |
| DreamPlugin | 150 | 梦境整合：定期整理记忆、清理表情包 |
| EchoPlugin | 200 | 复读检测：5 分钟内同消息 3 次触发 |
| ElementDetectorPlugin | 210 | 特殊消息元素检测 |
| DebugCommandPlugin | 300 | /plugins 查看插件列表、/version 版本检查 |

## 斜杠指令

| 指令 | 权限 | 说明 |
| --- | --- | --- |
| `/debug [问题]` | 管理员 | 进入调试模式，注入系统状态后单轮 LLM 回答 |
| `/debug save [描述]` | 管理员 | 保存最近图片到表情包库（别名: 保存/收录/添加表情） |
| `/debug send [stk_id\|gif]` | 管理员 | 发送表情包：指定ID或随机（别名: 发/发送） |
| `/plugins` | 管理员 | 列出所有已加载插件（名称、版本、开发者、简介） |
| `/version` | 公开 | 查看本地版本并检查 GitHub 是否有更新 |

插件可通过 `register_commands()` 注册更多指令。

## 写一个插件

**单文件插件**（纯工具，零依赖）：

```python
# plugins/my_tool.py
from kernel.types import AmadeusPlugin
from services.tools.base import Tool

class MyTool(Tool):
    name = "my_tool"
    description = "我的工具"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def execute(self, ctx, text: str) -> str:
        return f"处理结果: {text}"

class MyToolPlugin(AmadeusPlugin):
    name = "my_tool"
    description = "我的工具插件"
    version = "1.0.0"
    priority = 10

    def register_tools(self):
        return [MyTool()]
```

放到 `plugins/` 目录下，重启后自动发现。

**可用钩子**：`on_startup` `on_shutdown` `on_bot_connect` `on_message` `on_pre_prompt` `on_post_reply` `on_tick` `register_tools` `register_commands` `register_admin_routes`

详见 [wiki/](wiki/) 和 [docs/architecture.md](docs/architecture.md)。

## 配置

三层优先级：`config.toml` < 环境变量 < CLI 参数

| 环境变量 | 覆盖字段 |
|----------|---------|
| `LLM_BASE_URL` | `llm.base_url` |
| `LLM_API_KEY` | `llm.api_key` |
| `LLM_MODEL` | `llm.model` |
| `NAPCAT_API_URL` | `napcat.api_url` |
| `ADMIN_TOKEN` | `admin_token` |

完整配置项见 [config.example.toml](config.example.toml) 和 [wiki/06-config.md](wiki/06-config.md)。

## 开发

```bash
uv run ruff check    # Lint
uv run pytest        # 测试
uv run pyright       # 类型检查
```

## 项目结构

```
kernel/         # 内核层（PluginBus、类型、配置、路由）
services/       # 系统服务层（LLM、记忆、媒体、工具、指令、版本）
plugins/        # 插件层（15 个可开关插件）
admin/          # 管理面板（用量、配置、Soul 编辑、日志）
docs/           # 项目文档
wiki/           # 框架开发文档
config/         # 运行时配置（gitignored，Docker volume 挂载）
storage/        # 运行时数据（volume 挂载，不进入版本控制）
tests/          # 测试
```

## 变更日志

详见 [CHANGELOG.md](CHANGELOG.md)。

## 许可

MIT License — 详见 [LICENSE](LICENSE)。

## 致谢

从 [amadeus-in-shell](https://github.com/RoggeOhta/amadeus-in-shell) 重构而来。构建在 [NoneBot2](https://github.com/nonebot/nonebot2) 和 [NapCat](https://github.com/NapNeko/NapCatQQ) 之上。
