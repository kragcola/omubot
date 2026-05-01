# Omubot

基于 NoneBot2 的三层可扩展 QQ 机器人框架。

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Ruff](https://img.shields.io/badge/lint-ruff-orange.svg)](https://github.com/astral-sh/ruff)

## 架构

```
QQ ←→ NapCat (WS) ←→ NoneBot2
                        └── Omubot 三层框架
                             ├── Kernel     PluginBus · 类型契约 · 插件发现
                             ├── Services   LLM · 记忆 · 时间线 · 工具 · 调度
                             └── Plugins    14 个可开关、可插拔的功能插件
```

- **内核层** — 零 I/O，零外部依赖。定义调度规则和类型契约，不改 API
- **系统服务层** — LLM 客户端、记忆卡片、群聊时间线、图片缓存、表情库、工具注册表
- **插件层** — 好感度、日程、记忆、表情包、梦境、视觉、复读等，通过钩子接入

## 快速开始

### 前置

- Python 3.12+ / [uv](https://github.com/astral-sh/uv)
- Docker + Docker Compose
- 一个 QQ 号（建议用小号）

### 1. 安装

```bash
git clone https://github.com/your-username/omubot.git
cd omubot
uv sync
```

### 2. 配置

```bash
cp .env.example config/.env              # 编辑：QQ 号、API Key
cp config.example.toml config/config.toml # 编辑：LLM 模型、群聊开关
cp soul/identity.example.md config/soul/identity.md       # 编辑：Bot 人设
cp soul/instruction.example.md config/soul/instruction.md # 编辑：行为指令
```

### 3. 启动

```bash
# Docker（推荐）
docker compose up napcat -d           # 启动 NapCat
# 浏览器打开 http://localhost:6099 → 扫码登录
docker compose up bot -d --build      # 启动 Bot

# 或本地运行
docker compose up napcat -d
uv run python bot.py
```

### 4. 验证

- 在群里 @bot 发消息
- 访问 Admin 面板：`http://localhost:8081/admin/`

## 插件

| 插件 | 优先级 | 功能 |
|------|--------|------|
| ChatPlugin | 0 | 核心聊天：消息路由、LLM 调用、tool loop |
| DateTimePlugin | 1 | 时间日期查询 |
| WebSearchPlugin | 1 | DuckDuckGo 网页搜索 |
| WebFetchPlugin | 1 | 网页内容抓取 |
| HttpApiPlugin | 1 | NapCat HTTP API 调用 |
| GroupAdminPlugin | 1 | 群管理（禁言、头衔、发消息） |
| VisionPlugin | 5 | 多模态图像理解 |
| StickerPlugin | 10 | 表情包库：收藏、检索、发送 |
| MemoPlugin | 20 | 记忆卡片：7 类 3 作用域，检索门控 |
| AffectionPlugin | 30 | 好感度系统：分数、昵称、态度调节 |
| SchedulePlugin | 35 | 模拟日程：每日 LLM 生成，结合真实日期 |
| HistoryLoaderPlugin | 40 | 启动时加载群历史消息 |
| DreamPlugin | 100 | 梦境整合：定期整理记忆、清理表情包 |
| EchoPlugin | 200 | 复读检测 |
| ElementDetectorPlugin | 200 | 特殊消息元素检测 |

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
kernel/         # 内核层（PluginBus、类型、配置）
services/       # 系统服务层（LLM、记忆、媒体、工具）
plugins/        # 插件层（14 个可开关插件）
admin/          # 管理面板
docs/           # 项目文档
wiki/           # 框架开发文档
soul/           # 人设模板（identity.example.md + instruction.example.md）
storage/        # 运行时数据（volume 挂载，不进入版本控制）
tests/          # 测试
```

## 许可

MIT License — 详见 [LICENSE](LICENSE)。

## 致谢

从 [amadeus-in-shell](https://github.com/RoggeOhta/amadeus-in-shell) 重构而来。构建在 [NoneBot2](https://github.com/nonebot/nonebot2) 和 [NapCat](https://github.com/NapNeko/NapCatQQ) 之上。
