# Omubot 功能分类

> 对 amadeus-in-shell 项目全部 57 个 .py 文件的审计结果，按重写后的职责分类。
> 审计日期：2026-05-01

---

## 分类体系

| 分类 | 含义 | 判断标准 |
|------|------|---------|
| **Framework API** | 框架层基础设施 | 所有插件通过 PluginContext 可访问的共享服务；定义了跨插件的类型和数据流契约 |
| **Core** | 核心（不可卸载） | 没有它 bot 无法运行。消息路由、LLM 调用、上下文管理、调度 |
| **Plugin** | 可插拔功能 | `config.toml` 中有独立的 `enabled` 开关；移除后 bot 正常运行 |
| **Plugin → Tool** | 插件提供的 LLM 工具 | 插件通过 `register_tools()` 暴露给 LLM 的工具 |
| **Admin** | Web 管理面板 | FastAPI 路由 + Jinja2 模板 |
| **Config** | 配置系统 | Pydantic 模型 + TOML 加载 |
| **Support** | 辅助/一次性 | CLI 工具、迁移脚本、旧代码 |

---

## 一、Framework API（框架基础设施）

这些是 PluginBus 通过 PluginContext 暴露给所有插件的共享服务。它们是稳定的 API 契约，修改需谨慎。

### 1.1 类型系统

| 模块 | 位置 | 提供 | 说明 |
|------|------|------|------|
| `src/memory/types.py` | → `omubot/types.py` | `TextBlock`, `ImageRefBlock`, `ContentBlock`, `Content` | 多模态消息内容的统一类型。`Content = str \| list[ContentBlock]`。所有消息处理的基础类型。 |

### 1.2 消息存储

| 模块 | 位置 | 提供 | 说明 |
|------|------|------|------|
| `src/memory/message_log.py` | → `omubot/services/message_log.py` | `MessageLog` | SQLite 消息持久化。`record()`、`query_recent()`、`query_for_compact()`。群聊和私聊共用。 |
| `src/memory/short_term.py` | → `omubot/services/short_term.py` | `ShortTermMemory` | 私聊短期记忆（内存 deque），token 感知的压缩触发。 |
| `src/memory/group_timeline.py` | → `omubot/services/group_timeline.py` | `GroupTimeline` | 群聊追加式时间线，用户消息合并、轮次管理、压缩、摘要。 |

### 1.3 记忆卡片

| 模块 | 位置 | 提供 | 说明 |
|------|------|------|------|
| `src/memory/card_store.py` | → `omubot/services/card_store.py` | `CardStore`, `Card`, `NewCard` | SQLite 类型化记忆卡片（7 类别 × 3 作用域）。CRUD + supersedes 取代边 + 搜索 + 实体查询。 |
| `src/memory/retrieval.py` | → `omubot/services/retrieval.py` | `RetrievalGate` | 4 级检索门控：全量 → 周期刷新 → 关键词匹配 → 最小提示。 |

### 1.4 图片与表情

| 模块 | 位置 | 提供 | 说明 |
|------|------|------|------|
| `src/memory/image_cache.py` | → `omubot/services/image_cache.py` | `ImageCache` | 磁盘图片缓存，pyvips 缩放，base64 加载。两级哈希目录，启动时过期清理。 |
| `src/sticker/store.py` | → `omubot/services/sticker_store.py` | `StickerStore` | 表情包库。SHA-256 去重，index.json + 图片文件，200 上限，格式检测。 |

### 1.5 LLM 基础设施

| 模块 | 位置 | 提供 | 说明 |
|------|------|------|------|
| `src/llm/client.py` | → `omubot/core/llm_client.py` | `LLMClient` | Anthropic API 调用（SSE 流式）、工具循环（最多 5 轮）、上下文压缩、缓存断点管理、消息分割、Markdown 清理。**核心中最复杂的模块。** |
| `src/llm/usage.py` | → `omubot/services/usage_tracker.py` | `UsageTracker` | API 调用追踪到 SQLite。记录 tokens、缓存命中率、延迟、错误。 |

### 1.6 工具框架

| 模块 | 位置 | 提供 | 说明 |
|------|------|------|------|
| `src/tools/base.py` | → `omubot/tools/base.py` | `Tool` (ABC) | 工具抽象基类：`name`、`description`、`parameters`、`execute()`、`to_openai_tool()`。 |
| `src/tools/registry.py` | → `omubot/tools/registry.py` | `ToolRegistry` | 工具注册表：`register()`、`get()`、`call()`、`to_openai_tools()`。 |
| `src/tools/context.py` | → `omubot/tools/context.py` | `ToolContext` | 工具执行上下文：`bot`、`user_id`、`group_id`、`session_id`、`extra`。 |

### 1.7 PluginBus 相关类型

| 类型 | 位置 | 说明 |
|------|------|------|
| `PluginContext` | `omubot/plugin_bus.py` | 暴露给插件的共享服务容器（见设计草案） |
| `MessageContext` | `omubot/plugin_bus.py` | 消息到达时的上下文 |
| `PromptContext` | `omubot/plugin_bus.py` | 构建 system prompt 时的上下文，提供 `add_block()` |
| `ReplyContext` | `omubot/plugin_bus.py` | 回复发送后的上下文（副作用用） |
| `ThinkerContext` | `omubot/plugin_bus.py` | Thinker 决策后的上下文 |
| `PromptBlock` | `omubot/plugin_bus.py` | 插件贡献的 system prompt block（text + label + position） |

---

## 二、Core（核心，不可卸载）

bot 没有这些模块无法运行。不是插件，是框架的一部分。

### 2.1 入口

| 模块 | 位置 | 说明 |
|------|------|------|
| `bot.py` | `omubot/bot.py` | 启动入口。CLI 参数解析 → 配置加载 → 日志初始化 → NoneBot 启动 → 插件加载 → Admin 中间件。 |

### 2.2 核心聊天

| 模块 | 位置 | 说明 |
|------|------|------|
| `src/plugins/chat/__init__.py` | → `omubot/core/chat_plugin.py` | **主 NoneBot 事件处理器。** @on_message 路由、私聊/群聊分发、`_render_message()` 消息段转换。启动/关闭生命周期编排（通过 PluginBus）。 |
| `src/llm/scheduler.py` | → `omubot/core/scheduler.py` | 群聊调度器：debounce + batch + @触发。`_GroupSlot` 状态管理，速率限制重试，自动静音检测。 |

### 2.3 Prompt 构建

| 模块 | 位置 | 说明 |
|------|------|------|
| `src/llm/prompt.py` | → `omubot/core/prompt_builder.py` | System prompt 构建器。管理 6 个缓存断点的块组装。插件通过 `on_pre_prompt` 追加块后，由它合并为最终 system prompt。 |

### 2.4 Thinker（预回复决策）

| 模块 | 位置 | 说明 |
|------|------|------|
| `src/llm/thinker.py` | → `omubot/core/thinker.py` | 轻量 LLM 调用决定 reply/wait/search。受旧 MaiBot Planner 启发，防止消息轰炸。 |

### 2.5 消息处理工具（核心内置）

| 模块 | 位置 | 说明 |
|------|------|------|
| `src/plugins/echo.py` | → `omubot/plugins/echo/plugin.py` | 复读检测：5 分钟内同消息 3 次触发，5% 打断概率。**通过 `on_message` 钩子。** |
| `src/plugins/element_detector.py` | → `omubot/plugins/element_detector/plugin.py` | 触发短语匹配：正则规则 → 预设回复或 LLM 生成。**通过 `on_message` 钩子。** |

> echo 和 element_detector 虽然是可选功能，但属于核心消息管线的一部分。设为 priority=200-210，默认启用。

### 2.6 共享工具（核心内置）

| 模块 | 位置 | 说明 |
|------|------|------|
| `src/tools/datetime_tool.py` | → `omubot/plugins/datetime/plugin.py` | `get_datetime` 工具：当前时间 + 日程上下文。**通过 `register_tools()`。** |
| `src/tools/web_search.py` | → `omubot/plugins/web_search/plugin.py` | `web_search` 工具：DuckDuckGo 搜索。**通过 `register_tools()`。** |
| `src/tools/web_fetch.py` | → `omubot/plugins/web_fetch/plugin.py` | `web_fetch` 工具：URL 抓取 + SSRF 防护。**通过 `register_tools()`。** |
| `src/tools/http_api.py` | → `omubot/plugins/http_api/plugin.py` | `http_api` 工具：通用 REST API 调用。**通过 `register_tools()`。** |

### 2.7 群管理工具（核心内置）

| 模块 | 位置 | 说明 |
|------|------|------|
| `src/tools/group_admin.py` | → `omubot/plugins/group_admin/plugin.py` | `mute_user`、`set_title`、`send_group_msg`。仅 SUPERUSER。**通过 `register_tools()`。** |

### 2.8 身份系统

| 模块 | 位置 | 说明 |
|------|------|------|
| `src/identity/models.py` | → `omubot/core/identity.py` | `Identity` Pydantic 模型：id、name、personality、proactive 规则。 |
| `src/identity/manager.py` | → `omubot/core/identity.py` | `IdentityManager`：解析 `soul/identity.md`。 |

### 2.9 防检测

| 模块 | 位置 | 说明 |
|------|------|------|
| `src/anti_detect.py` | → `omubot/core/humanizer.py` | `Humanizer`：随机延迟模拟人类打字节奏。 |

---

## 三、Plugin（可插拔功能）

每个插件是一个目录，包含 `plugin.py`（继承 `AmadeusPlugin` 或实现钩子）。通过 `config.toml` 的 `enabled` 开关控制。

### 3.1 AffectionPlugin（好感度系统）

| 原位置 | 新位置 | 说明 |
|--------|--------|------|
| `src/affection/engine.py` | `omubot/plugins/affection/engine.py` | 好感度引擎：记录互动、等级计算、态度短语 |
| `src/affection/store.py` | `omubot/plugins/affection/store.py` | JSON 持久化（每用户一个 JSON 文件） |
| `src/affection/models.py` | `omubot/plugins/affection/models.py` | `AffectionProfile` 数据模型 |
| `src/tools/affection_tools.py` | `omubot/plugins/affection/tools.py` | `set_nickname` 工具 |

**实现的钩子：**
- `on_pre_prompt`：追加好感度 block（群聊隐私遮掩时跳过）
- `on_post_reply`：记录互动
- `register_tools`：`set_nickname`

**配置：** `[affection]` section，`enabled` 开关，`daily_limit`，`per_interaction` 增量

### 3.2 SchedulePlugin（日程系统）

| 原位置 | 新位置 | 说明 |
|--------|--------|------|
| `src/schedule/generator.py` | `omubot/plugins/schedule/generator.py` | LLM 日程生成（午夜触发） |
| `src/schedule/store.py` | `omubot/plugins/schedule/store.py` | JSON 日程持久化 |
| `src/schedule/mood.py` | `omubot/plugins/schedule/mood.py` | 实时心情计算引擎 |
| `src/schedule/calendar.py` | `omubot/plugins/schedule/calendar.py` | 节假日/调休/角色生日日历 |
| `src/schedule/types.py` | `omubot/plugins/schedule/types.py` | `TimeSlot`、`Schedule`、`MoodProfile` |

**实现的钩子：**
- `on_pre_prompt`：追加日程 block + 心情 block
- `on_tick`：每日凌晨重新生成日程
- （不注册 LLM 工具）

**配置：** `[schedule]` section，`enabled` 开关

### 3.3 StickerPlugin（表情包系统）

| 原位置 | 新位置 | 说明 |
|--------|--------|------|
| `src/sticker/store.py` | → `omubot/services/sticker_store.py` | 表情包库（提升为 Framework API，因为 ImageCache 和 LLMClient 也依赖它） |
| `src/tools/sticker_tools.py` | `omubot/plugins/sticker/tools.py` | `save_sticker`、`manage_sticker`、`send_sticker`、`describe_image` |

**实现的钩子：**
- `on_post_reply`：检测颜文字 → 补发表情包
- `register_tools`：4 个 sticker 工具

**配置：** `[sticker]` section，`enabled` 开关，`frequency` (rarely/normal/frequently)，`max_count`

### 3.4 MemoPlugin（记忆系统）

| 原位置 | 新位置 | 说明 |
|--------|--------|------|
| `src/memory/memo_extractor.py` | `omubot/plugins/memo/extractor.py` | 对话后异步记忆提取（LLM 调用） |
| `src/tools/memo_tools.py` | `omubot/plugins/memo/tools.py` | `lookup_cards`、`update_cards` |

**实现的钩子：**
- `on_pre_prompt`：通过 RetrievalGate 检索记忆卡片
- `on_post_reply`：提取本轮对话中的记忆
- `register_tools`：2 个 memo 工具

**配置：** `[memo]` section

### 3.5 DreamPlugin（梦境/记忆整理）

| 原位置 | 新位置 | 说明 |
|--------|--------|------|
| `src/llm/dream.py` | `omubot/plugins/dream/agent.py` | 后台 LLM 记忆整理代理：合并重复卡片、重新分类、交叉验证 |

**实现的钩子：**
- `on_tick`：每 24h 运行一次整合循环
- （不注册 LLM 工具给对话——它有自己的内部工具）

**配置：** `[dream]` section，`enabled` 开关

### 3.6 VisionPlugin（图片理解）

| 原位置 | 新位置 | 说明 |
|--------|--------|------|
| `src/vision/client.py` | `omubot/plugins/vision/client.py` | Qwen VL 图片描述（OpenAI 兼容 API） |

**实现的钩子：**
- （不实现标准钩子——被 `_render_message()` 直接调用。需要提供 `describe_image(url) -> str` API 或注册为 Framework API）
- `register_tools`：`describe_image`（已合并到 sticker_tools）

**配置：** `[vision]` section，`[vision.qwen]` sub-section

### 3.7 HistoryLoaderPlugin（历史加载）

| 原位置 | 新位置 | 说明 |
|--------|--------|------|
| `src/memory/history_loader.py` | `omubot/plugins/history_loader/loader.py` | 启动时从 OneBot WebSocket 拉取群历史消息 |

**实现的钩子：**
- `on_startup`：拉取历史消息并填充 GroupTimeline
- （在 `_on_connect` 中触发，需提供 `on_bot_connect` 钩子或由核心调度）

---

## 四、Config（配置系统）

| 模块 | 新位置 | 说明 |
|------|--------|------|
| `src/config.py` | `omubot/config/models.py` | 全部 Pydantic 配置模型（~25 个类） |
| `src/config_loader.py` | `omubot/config/loader.py` | 分层加载：TOML < env < CLI |
| `src/constants/qq_face.py` | `omubot/config/qq_face.py` | QQ 表情 ID → 中文名映射（341 条目） |

**在新架构中的角色：**
- `BotConfig` 是唯一配置根。每个插件通过 `PluginContext.config` 访问全局配置。
- 插件可声明自己的配置 section（如 `[plugin.affection]`），放在 `config.py` 的 `BotConfig` 中或插件自带的 `Config` 类中。
- `config_loader.py` 保持不变。

---

## 五、Admin（Web 管理面板）

| 模块 | 新位置 | 说明 |
|------|--------|------|
| `src/admin/__init__.py` | `omubot/admin/__init__.py` | Admin 面板工厂，接受依赖注入 |
| `src/admin/auth.py` | `omubot/admin/auth.py` | HMAC 签名 cookie 认证中间件 |
| `src/admin/templates.py` | `omubot/admin/templates.py` | Jinja2 模板辅助 |
| `src/admin/routes/dashboard.py` | `omubot/admin/routes/dashboard.py` | 总览页 |
| `src/admin/routes/usage.py` | `omubot/admin/routes/usage.py` | 用量统计 + Chart.js 图表 |
| `src/admin/routes/groups.py` | `omubot/admin/routes/groups.py` | 群聊管理 |
| `src/admin/routes/config_viewer.py` | `omubot/admin/routes/config_viewer.py` | 配置只读查看 |
| `src/admin/routes/soul.py` | `omubot/admin/routes/soul.py` | Soul 在线编辑器 |
| `src/admin/routes/logs.py` | `omubot/admin/routes/logs.py` | 日志查看器 |
| `src/llm/usage_routes.py` | `omubot/admin/routes/usage_api.py` | REST API：/api/usage/today 等 |

**在新架构中的角色：**
- Admin 是一组 FastAPI 路由，不是插件。通过 PluginBus 获得共享服务引用，挂载到 NoneBot FastAPI app。
- 每个管理页面是可选的——如果某个服务（如 card_store）为 None，对应功能自动隐藏。

---

## 六、Support（辅助/一次性/旧代码）

| 模块 | 新位置 | 说明 |
|------|--------|------|
| `src/llm/usage_cli.py` | `omubot/support/usage_cli.py` | CLI 用量查询（独立于 bot 运行） |
| `src/llm/usage_tui.py` | `omubot/support/usage_tui.py` | Rich TUI 渲染（usage_cli 使用） |
| `src/memory/memo_store.py` | `omubot/support/legacy_memo_store.py` | 旧 .md 文件记忆系统（已被 CardStore 取代） |
| `src/memory/migrate.py` | `omubot/support/migrate.py` | 一次性迁移：.md → CardStore |
| `src/memory/history_loader.py` | → Plugin 或 Core | 见 3.7 |

---

## 七、重写后的目录结构

```
Omubot/                          # 新项目根（重写目标）
├── bot.py                       # 启动入口
├── pyproject.toml
├── config.example.toml
├── config/                       # 运行时配置（gitignore）
│   ├── config.toml
│   ├── .env
│   └── soul/
│       ├── identity.md
│       └── instruction.md
├── soul/                         # 人设模板
│   ├── identity.example.md
│   └── instruction.example.md
│
├── omubot/
│   ├── plugin_bus.py            # PluginBus + AmadeusPlugin + Context 类型
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   ├── models.py            # BotConfig + 所有子配置
│   │   ├── loader.py            # load_config()
│   │   └── qq_face.py           # QQ 表情映射
│   │
│   ├── core/                    # 核心（不可卸载）
│   │   ├── __init__.py
│   │   ├── chat_plugin.py       # NoneBot 事件处理 + 消息路由
│   │   ├── llm_client.py        # Anthropic API 调用
│   │   ├── prompt_builder.py    # System prompt 组装
│   │   ├── scheduler.py         # 群聊调度器
│   │   ├── thinker.py           # 预回复决策
│   │   ├── identity.py          # Identity + IdentityManager
│   │   └── humanizer.py         # 打字延迟模拟
│   │
│   ├── services/                # Framework API（共享服务）
│   │   ├── __init__.py
│   │   ├── message_log.py       # SQLite 消息持久化
│   │   ├── short_term.py        # 私聊短期记忆
│   │   ├── group_timeline.py    # 群聊时间线
│   │   ├── card_store.py        # 类型化记忆卡片
│   │   ├── retrieval.py         # 检索门控
│   │   ├── image_cache.py       # 图片缓存
│   │   ├── sticker_store.py     # 表情包库
│   │   ├── usage_tracker.py     # API 用量追踪
│   │   └── state_board.py       # 群状态摘要
│   │
│   ├── types/                   # 共享类型（零依赖）
│   │   ├── __init__.py
│   │   └── messages.py          # Content, ContentBlock, TextBlock, ImageRefBlock
│   │
│   ├── tools/                   # 工具框架
│   │   ├── __init__.py
│   │   ├── base.py              # Tool ABC
│   │   ├── registry.py          # ToolRegistry
│   │   └── context.py           # ToolContext
│   │
│   ├── plugins/                 # 可插拔功能
│   │   ├── echo/
│   │   │   └── plugin.py        # EchoPlugin (priority=200)
│   │   ├── element_detector/
│   │   │   └── plugin.py        # ElementDetectorPlugin (priority=210)
│   │   ├── affection/
│   │   │   ├── plugin.py        # AffectionPlugin (priority=10)
│   │   │   ├── engine.py
│   │   │   ├── store.py
│   │   │   ├── models.py
│   │   │   └── tools.py         # set_nickname
│   │   ├── schedule/
│   │   │   ├── plugin.py        # SchedulePlugin (priority=20)
│   │   │   ├── generator.py
│   │   │   ├── store.py
│   │   │   ├── mood.py
│   │   │   ├── calendar.py
│   │   │   └── types.py
│   │   ├── sticker/
│   │   │   ├── plugin.py        # StickerPlugin (priority=40)
│   │   │   └── tools.py         # save/send/manage/describe
│   │   ├── memo/
│   │   │   ├── plugin.py        # MemoPlugin (priority=30)
│   │   │   ├── extractor.py
│   │   │   └── tools.py         # lookup_cards, update_cards
│   │   ├── dream/
│   │   │   └── plugin.py        # DreamPlugin (priority=100)
│   │   ├── vision/
│   │   │   └── plugin.py        # VisionPlugin (priority=50)
│   │   ├── history_loader/
│   │   │   └── plugin.py        # HistoryLoaderPlugin (priority=5)
│   │   ├── datetime/
│   │   │   └── plugin.py        # DateTimePlugin (priority=1, register_tools only)
│   │   ├── web_search/
│   │   │   └── plugin.py        # WebSearchPlugin (priority=1)
│   │   ├── web_fetch/
│   │   │   └── plugin.py        # WebFetchPlugin (priority=1)
│   │   ├── http_api/
│   │   │   └── plugin.py        # HttpApiPlugin (priority=1)
│   │   └── group_admin/
│   │       └── plugin.py        # GroupAdminPlugin (priority=1)
│   │
│   ├── admin/                   # Web 管理面板
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── templates.py
│   │   ├── templates/           # Jinja2 模板（8 个 .html + admin.css）
│   │   └── routes/
│   │       ├── dashboard.py
│   │       ├── usage.py
│   │       ├── usage_api.py
│   │       ├── groups.py
│   │       ├── config_viewer.py
│   │       ├── soul.py
│   │       └── logs.py
│   │
│   └── support/                 # 辅助（非运行时）
│       ├── usage_cli.py
│       ├── usage_tui.py
│       ├── legacy_memo_store.py
│       └── migrate.py
│
├── storage/                     # 运行时数据（gitignore）
│   ├── usage.db
│   ├── messages.db
│   ├── memory_cards.db
│   ├── image_cache/
│   ├── stickers/
│   ├── affection/
│   ├── schedule/
│   ├── plugins/                 # 各插件的独立存储
│   └── logs/
│
├── tests/
│   ├── test_plugin_bus.py
│   ├── test_retrieval.py
│   ├── test_affection.py
│   ├── test_schedule.py
│   ├── test_sticker.py
│   └── ...
│
└── docs/
    ├── architecture.md
    ├── operations.md
    ├── maintenance-log.md
    └── project-info.md
```

---

## 八、迁移映射速查

### 原模块 → 新位置

| 原位置 | 新位置 | 分类 |
|--------|--------|------|
| `src/plugins/chat/__init__.py` | `omubot/core/chat_plugin.py` | Core |
| `src/plugins/echo.py` | `omubot/plugins/echo/plugin.py` | Plugin |
| `src/plugins/element_detector.py` | `omubot/plugins/element_detector/plugin.py` | Plugin |
| `src/llm/client.py` | `omubot/core/llm_client.py` | Core |
| `src/llm/prompt.py` | `omubot/core/prompt_builder.py` | Core |
| `src/llm/scheduler.py` | `omubot/core/scheduler.py` | Core |
| `src/llm/thinker.py` | `omubot/core/thinker.py` | Core |
| `src/llm/dream.py` | `omubot/plugins/dream/plugin.py` | Plugin |
| `src/llm/usage.py` | `omubot/services/usage_tracker.py` | Service |
| `src/llm/usage_cli.py` | `omubot/support/usage_cli.py` | Support |
| `src/llm/usage_tui.py` | `omubot/support/usage_tui.py` | Support |
| `src/llm/usage_routes.py` | `omubot/admin/routes/usage_api.py` | Admin |
| `src/memory/types.py` | `omubot/types/messages.py` | Type |
| `src/memory/short_term.py` | `omubot/services/short_term.py` | Service |
| `src/memory/group_timeline.py` | `omubot/services/group_timeline.py` | Service |
| `src/memory/history_loader.py` | `omubot/plugins/history_loader/plugin.py` | Plugin |
| `src/memory/message_log.py` | `omubot/services/message_log.py` | Service |
| `src/memory/card_store.py` | `omubot/services/card_store.py` | Service |
| `src/memory/memo_extractor.py` | `omubot/plugins/memo/extractor.py` | Plugin |
| `src/memory/memo_store.py` | `omubot/support/legacy_memo_store.py` | Support |
| `src/memory/migrate.py` | `omubot/support/migrate.py` | Support |
| `src/memory/retrieval.py` | `omubot/services/retrieval.py` | Service |
| `src/memory/state_board.py` | `omubot/services/state_board.py` | Service |
| `src/memory/image_cache.py` | `omubot/services/image_cache.py` | Service |
| `src/tools/base.py` | `omubot/tools/base.py` | Framework |
| `src/tools/registry.py` | `omubot/tools/registry.py` | Framework |
| `src/tools/context.py` | `omubot/tools/context.py` | Framework |
| `src/tools/datetime_tool.py` | `omubot/plugins/datetime/plugin.py` | Plugin→Tool |
| `src/tools/web_search.py` | `omubot/plugins/web_search/plugin.py` | Plugin→Tool |
| `src/tools/web_fetch.py` | `omubot/plugins/web_fetch/plugin.py` | Plugin→Tool |
| `src/tools/http_api.py` | `omubot/plugins/http_api/plugin.py` | Plugin→Tool |
| `src/tools/memo_tools.py` | `omubot/plugins/memo/tools.py` | Plugin→Tool |
| `src/tools/group_admin.py` | `omubot/plugins/group_admin/plugin.py` | Plugin→Tool |
| `src/tools/sticker_tools.py` | `omubot/plugins/sticker/tools.py` | Plugin→Tool |
| `src/tools/affection_tools.py` | `omubot/plugins/affection/tools.py` | Plugin→Tool |
| `src/affection/` | `omubot/plugins/affection/` | Plugin |
| `src/schedule/` | `omubot/plugins/schedule/` | Plugin |
| `src/sticker/store.py` | `omubot/services/sticker_store.py` | Service |
| `src/vision/` | `omubot/plugins/vision/` | Plugin |
| `src/identity/` | `omubot/core/identity.py` | Core |
| `src/anti_detect.py` | `omubot/core/humanizer.py` | Core |
| `src/config.py` | `omubot/config/models.py` | Config |
| `src/config_loader.py` | `omubot/config/loader.py` | Config |
| `src/constants/qq_face.py` | `omubot/config/qq_face.py` | Config |
| `src/admin/` | `omubot/admin/` | Admin |

### 不再需要的文件/目录（重写后删除）

| 文件 | 原因 |
|------|------|
| `src/plugins/__init__.py` | 空文件，插件不再集中存放 |
| `src/llm/__init__.py` | 分散到 core/ + services/ + plugins/dream/ |
| `src/memory/__init__.py` | 分散到 services/ + plugins/ |
| `src/tools/__init__.py` | 工具分散到各插件目录 |
| `src/affection/__init__.py` | 整个目录移到 plugins/affection/ |
| `src/schedule/__init__.py` | 整个目录移到 plugins/schedule/ |
| `src/sticker/__init__.py` | store 移到 services/，tools 移到 plugins/sticker/ |
| `src/vision/__init__.py` | 整个目录移到 plugins/vision/ |
| `src/identity/__init__.py` | 合并到 core/identity.py |
| `src/constants/__init__.py` | 合并到 config/qq_face.py |
