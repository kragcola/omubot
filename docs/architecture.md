# Architecture Details

## Omubot 三层架构

重构后的项目采用三层模型：

```
┌─────────────────────────────────────────────────────────┐
│                    插件层 (Plugins)                       │
│  好感度 · 日程 · 记忆 · 表情包 · 梦境 · 视觉 · 复读 ……  │
│  可开关，可安装卸载，通过钩子接入                         │
├─────────────────────────────────────────────────────────┤
│                 系统服务层 (System Services)               │
│  LLM调用 · 时间线 · 记忆卡片 · 图片缓存 · 表情库 · 用量  │
│  内置且始终运行，提供能力，插件通过 PluginContext 调用    │
├─────────────────────────────────────────────────────────┤
│                   内核层 (Kernel)                         │
│  PluginBus · 类型契约 · 配置系统 · NoneBot路由            │
│  极小且稳定，不做 I/O，不调 LLM，不改 API                 │
└─────────────────────────────────────────────────────────┘
```

**判断归属的三问**：

| 问题 | 是 → 内核 | 是 → 系统服务 | 是 → 插件 |
|------|----------|-------------|----------|
| 只做调度/类型定义/配置解析？ | ✓ | | |
| 提供可复用的 I/O 或计算能力？ | | ✓ | |
| 可以单独开关，可有可无？ | | | ✓ |
| 改动会导致所有插件都要改？ | ✓ | | |

**关键约束**：
- **内核不 import 任何系统服务或插件**——它只定义接口和调度规则
- **系统服务可以互相 import 同类**——CardStore 可以 import MessageLog，但它们不 import 插件
- **插件只 import 内核类型 + 系统服务**——插件从不 import 其他插件
- **管理员面板是一个特殊的"系统服务"**——它挂载 FastAPI 路由，但插件可以声明自己的管理页面

### 目录结构

```
omubot/
├── kernel/                 # 内核层
│   ├── types.py            #   AmadeusPlugin, PluginContext, MessageContext, Tool, Command, AdminRoute ...
│   ├── bus.py              #   PluginBus: 注册、生命周期、钩子调度、发现
│   ├── router.py           #   NoneBot 消息路由（群聊 + 私聊入口）
│   └── manifest.py         #   PluginManifest, SemVer 解析
├── services/               # 系统服务层
│   ├── llm/                #   LLMClient, PromptBuilder, Thinker, UsageTracker
│   ├── memory/             #   CardStore, MessageLog, Timeline, ShortTerm, Retrieval, StateBoard
│   ├── media/              #   ImageCache, StickerStore
│   ├── tools/              #   Tool 基类, ToolContext, ToolRegistry
│   ├── scheduler.py        #   GroupChatScheduler
│   ├── identity.py         #   IdentityManager
│   └── humanizer.py        #   消息人性化处理
├── plugins/                # 插件层（14 个）
│   ├── chat/               #   核心聊天（不可卸载）
│   ├── affection/          #   好感度系统
│   ├── datetime.py         #   时间查询（单文件插件）
│   ├── dream/              #   梦境整合
│   ├── echo/               #   复读检测
│   ├── element_detector/   #   消息元素检测（特殊消息类型识别）
│   ├── group_admin.py      #   群管理（单文件插件）
│   ├── history_loader/     #   历史消息加载
│   ├── http_api.py         #   HTTP API 工具（单文件插件）
│   ├── memo/               #   记忆卡片
│   ├── schedule/           #   模拟日程
│   ├── sticker/            #   表情包
│   ├── vision/             #   多模态视觉
│   ├── web_fetch.py        #   网页抓取（单文件插件）
│   └── web_search.py       #   网页搜索（单文件插件）
└── admin/                  # 管理面板（系统服务）
    ├── auth.py             #   HMAC-signed cookie 认证
    ├── templates.py        #   Jinja2 模板渲染
    ├── routes/             #   各子页面路由
    └── static/             #   静态资源
```

### 插件形态

**形态 A：目录插件**（功能复杂、多文件）

```
plugins/memo/
├── plugin.py              # 入口（必须）
├── plugin.json            # (可选) 元数据清单，覆盖类属性
└── ...
```

**形态 B：单文件插件**（纯工具插件，一文件搞定）

```
plugins/datetime.py       # 单文件，内含 AmadeusPlugin 子类
```

- 目录名（或单文件名去掉 `.py`）= `plugin.name`
- 单文件中只放一个 `AmadeusPlugin` 子类
- 同名时目录优先（单文件被忽略）

### plugin.json 清单

可选的 `plugin.json`，覆盖类属性，方便 CI/CD 和打包工具读取：

```json
{
    "name": "memo",
    "version": "1.0.0",
    "description": "记忆卡片系统",
    "priority": 20,
    "enabled": true,
    "dependencies": {
        "chat": ">=1.0.0"
    }
}
```

加载优先级：`plugin.json` > 类属性默认值。`PluginBus` 在 `discover_plugins()` 时解析。

## PluginBus 核心机制

### 生命周期钩子

```
fire_on_startup(ctx) → 按依赖拓扑顺序调用所有插件 on_startup
fire_on_shutdown(ctx) → 按依赖倒序调用 on_shutdown
fire_on_bot_connect(ctx, bot) → bot 连接通知
```

### 消息管线钩子

```
fire_on_message(ctx) → 按优先级调用 on_message，直到有插件返回 True 消费消息
fire_on_thinker_decision(ctx) → thinker 决策后通知
fire_on_pre_prompt(ctx) → 收集插件追加的 PromptBlock
fire_on_post_reply(ctx) → 回复后副作用
```

### 工具/命令/路由收集

```
collect_tools() → 所有插件的 Tool 列表 → ToolRegistry
collect_commands() → 所有插件的文本命令
collect_admin_routes() → 所有插件的 Admin Panel 路由
```

### 定时调度

```
fire_on_tick(ctx) → 按优先级调用 on_tick（~60s 间隔）
start_tick_loop(ctx, interval) → 启动后台 asyncio tick 循环
stop_tick_loop() → 停止 tick 循环
```

### 插件发现

```python
bus.discover_plugins("plugins")
```

两轮扫描：
1. Pass 1: 子目录 + `plugin.py`（优先）
2. Pass 2: 独立 `.py` 文件（跳过 `__init__`，同名时目录优先）

已注册的插件自动跳过。`plugin.json` 在发现时解析并覆盖实例属性。

### 依赖解析

用 Kahn 算法拓扑排序插件依赖图：
- 缺失依赖 → warning，跳过该依赖边
- 版本不兼容 → warning，跳过该依赖边
- 循环依赖 → warning，回退到 priority 排序
- 被禁用的插件 → 跳过 on_startup

### 优先级规则

| 范围 | 用途 |
|------|------|
| 0 | 核心（ChatPlugin，不可卸载） |
| 1-9 | 基础设施工具（几乎总是需要） |
| 10-49 | 业务插件（好感度、日程、记忆、表情包等） |
| 50-99 | 辅助业务 |
| 100-199 | 后台任务 |
| 200-299 | 管线拦截（echo、元素检测） |
| 300+ | 第三方/实验性 |

### 异常隔离

`_safe_call()` 包装每个钩子调用：
- 单个插件异常不影响其他插件
- 超过 100ms → debug 日志
- 超过 5s → warning 日志
- 被禁用的插件静默跳过

## 已注册插件一览

| 插件 | 优先级 | 形态 | 主要钩子 |
|------|--------|------|----------|
| ChatPlugin | 0 | 目录 | `on_startup`, `on_shutdown`, `on_bot_connect`, `register_tools` |
| DateTimePlugin | 1 | 单文件 | `register_tools` |
| WebSearchPlugin | 1 | 单文件 | `register_tools` |
| WebFetchPlugin | 1 | 单文件 | `register_tools` |
| HttpApiPlugin | 1 | 单文件 | `register_tools` |
| GroupAdminPlugin | 1 | 单文件 | `register_tools` |
| VisionPlugin | 5 | 目录 | `on_startup`, `on_pre_prompt` |
| StickerPlugin | 10 | 目录 | `register_tools` |
| MemoPlugin | 20 | 目录 | `on_startup`, `register_tools` |
| AffectionPlugin | 30 | 目录 | `on_startup`, `on_pre_prompt`, `on_post_reply` |
| SchedulePlugin | 35 | 目录 | `on_bot_connect`, `on_shutdown`, `on_pre_prompt` |
| HistoryLoaderPlugin | 40 | 目录 | `on_bot_connect` |
| DreamPlugin | 100 | 目录 | `on_startup`, `on_bot_connect`, `on_shutdown`, `on_tick` |
| EchoPlugin | 200 | 目录 | `on_message` |
| ElementDetectorPlugin | 200 | 目录 | `on_message` |

## Key Design Decisions

- **Raw Anthropic API via aiohttp SSE** — no SDK. `services/llm/client.py` manually parses SSE `data:` lines to extract text deltas and tool_use blocks. Adding tool calls means touching the `_call_api` function.
- **Prompt Caching strategy** — 4 cache breakpoints: ① tools[-1] (global shared), ② system block 1: personality + instruction + admins + proactive rules (global shared, built once at startup), ③ system block 2: memo index + entity memo + sticker library (per-entity), ④ messages[near-end] (per-conversation). Group timeline summaries inserted before messages for cache stability. Tool definitions also cached (last tool gets `cache_control`).
- **Context window management** — When estimated input tokens exceed `max_context_tokens × compact_ratio`, the front half of history is compressed into a summary via a separate LLM call. During compaction, the LLM receives an `append_memo` tool to extract user traits/events into long-term memory (§ Compact Memo Extraction). A circuit breaker drops oldest messages after `max_failures` consecutive compact failures.
- **Segmented responses** — Bot replies can contain `---cut---` separators; each segment is sent as a separate QQ message with a 0.5s delay.
- **Tool framework** — Tools extend `services/tools/base.py:Tool` ABC (name, description, parameters as JSON Schema, async execute). Registered in `ToolRegistry`, converted to Anthropic format via OpenAI-style intermediate. `ToolContext` carries the Bot instance and event metadata. Tools are executed in parallel within each round.
- **Soul directory** — `config/soul/` holds personality & instruction configs. `identity.md` defines a single persona (Markdown: `# Name` heading for the persona name, body for personality, optional `## 插话方式` section for proactive chat rules — exact heading match required). `instruction.md` holds behavioral directives injected into the system prompt. Templates at `soul/*.example.md`.
- **Memory layers** — Short-term: in-memory deque per session. Long-term: typed cards in `storage/memory_cards.db` via CardStore (SQLite, 7 categories × 3 scopes, with supersedes edges). Group timeline: append-only turns + pending buffer per group (`GroupTimeline`), with summary from compaction and SQLite persistence via `MessageLog`. Max 200 groups in memory (LRU eviction).
- **Session IDs** — `group_{group_id}` for group chats, `private_{user_id}` for DMs.
- **History bootstrap** — On bot connect, `history_loader` pulls recent messages from NapCat HTTP API for all groups, populating the group timeline (with image caching and sticker recognition). After loading, the scheduler fires once per group to catch up on missed messages.

## Proactive Chat (GroupChatScheduler)

The bot can autonomously join group conversations when the identity has a `## 插话方式` section defined. The `GroupChatScheduler` (`services/scheduler.py`) manages this:

- **Debounce**: after each non-@ group message, a debounce timer starts (`debounce_seconds`). If the group goes quiet, the scheduler triggers an LLM call.
- **Batch**: if messages accumulate to `batch_size` before the debounce fires, the scheduler triggers immediately.
- **@bot interrupt**: when someone @s the bot, the scheduler cancels any pending debounce/running proactive task for that group, yielding to the direct @bot handler. After the @bot reply completes, the scheduler is re-enabled.
- **pass_turn tool**: when the scheduler fires, the LLM receives the `pass_turn` tool. If the model decides there's nothing worth saying, it calls `pass_turn` and no message is sent.
- **Startup catch-up**: on bot connect, the scheduler triggers once for each group that has history, so the bot can respond to messages it missed while offline.

## Config

Config flows through `kernel/config.py:BotConfig` (Pydantic model), loaded via `kernel/config.py`. Priority (low → high):

1. Pydantic defaults
2. TOML file (`config.toml` or `BOT_CONFIG_PATH` env var)
3. Environment variables (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `NAPCAT_API_URL`)
4. CLI arguments (via `bot.py` argparse)

Key config sections:

| Section | Fields | Purpose |
|---------|--------|---------|
| `llm` | `base_url`, `api_key`, `model`, `max_tokens` | LLM API connection |
| `llm.context` | `max_context_tokens` | Context window size |
| `llm.usage` | `enabled`, `slow_threshold_s` | Usage tracking & slow call alerts |
| `compact` | `ratio`, `compress_ratio`, `max_failures`, `cache_hit_warn`, `cache_alert_window_m`, `cache_alert_cooldown_m` | Context compaction & cache alerting |
| `dream` | `enabled`, `interval_hours`, `max_rounds` | Dream agent (periodic memo consolidation) |
| `group` | `history_load_count`, `allowed_groups`, `debounce_seconds`, `batch_size`, `at_only`, `blocked_users`, `overrides` | Group chat behavior, scheduler & per-group overrides |
| `napcat` | `api_url` | NapCat HTTP API endpoint |
| `memo` | `dir`, `user_max_chars`, `group_max_chars`, `index_max_lines`, `history_enabled` | Long-term memo storage |
| `soul` | `dir` | Soul config directory |
| `log` | `dir` | Log directory |
| `vision` | `enabled`, `max_images_per_message`, `max_dimension`, `cache_dir`, `cache_max_age_hours` | Multimodal image understanding |
| `sticker` | `enabled`, `storage_dir`, `max_count` | Sticker library |
| `schedule` | `enabled` | Schedule generation |
| `affection` | `enabled`, `score_increment`, `daily_limit` | Affection system |
| top-level | `admins`, `allowed_private_users` | Access control & admin designation |
| top-level | `admin_token` | Admin dashboard login token |

`admins` is a `dict[str, str]` mapping QQ numbers to nicknames. Admins are injected into the system prompt as trusted sources and authorized for group admin tools.

NoneBot itself is configured in `pyproject.toml` under `[tool.nonebot]`.

### Per-Group Config

`group.overrides` maps group IDs to `GroupOverride`, allowing per-group tuning of `at_only`, `debounce_seconds`, `batch_size`, `history_load_count`, and `blocked_users`. Resolved via `GroupConfig.resolve(group_id) -> ResolvedGroupConfig`:

- `blocked_users`: union of global + per-group lists (additive, not override)
- All other fields: per-group value if set, else global default

## PluginContext

`PluginContext` 是插件访问所有系统服务的统一入口：

```python
@dataclass
class PluginContext:
    config: BotConfig              # 全局配置
    storage_dir: Path              # 存储根目录
    bus: PluginBus | None          # 插件总线（用于动态查询其他插件）
    bot_start_time: float          # Bot 启动时间戳
    # 以下由 ChatPlugin 在 on_startup 中注入
    llm_client: LLMClient | None
    usage_tracker: UsageTracker | None
    msg_log: MessageLog | None
    card_store: CardStore | None
    schedule_store: ScheduleStore | None
    schedule_enabled: bool
    # ...
```

## Available Tools

| Tool | Class | Description |
|------|-------|-------------|
| `recall_memo` | `RecallMemoTool` | Recall user/group memo by exact id or fuzzy query |
| `update_memo` | `UpdateMemoTool` | Overwrite user/group memo (async fire-and-forget) |
| `get_datetime` | `DateTimeTool` | Current date/time (Asia/Shanghai) |
| `web_fetch` | `WebFetchTool` | Fetch web page content (SSRF-protected) |
| `web_search` | `WebSearchTool` | DuckDuckGo web search (max 10 results) |
| `http_api` | `HttpApiTool` | Call NapCat HTTP API |
| `mute_user` | `MuteUserTool` | Mute group member (admin only; duration=0 unmutes) |
| `set_title` | `SetTitleTool` | Set member special title (admin only) |
| `send_group_msg` | `SendGroupMsgTool` | Send group message (admin only) |
| `save_sticker` | `SaveStickerTool` | Save image to sticker library (conditional on sticker enabled) |
| `manage_sticker` | `ManageStickerTool` | Update description/usage_hint or delete sticker (delete is admin only; conditional on sticker enabled) |
| `send_sticker` | `SendStickerTool` | Send sticker as image message (conditional on sticker enabled) |
| `pass_turn` | — | Skip this turn (injected by LLMClient for all chat calls, not a registered tool) |
| `append_memo` | — | Append observation to memo pending section (injected only during compaction, not a registered tool) |
| `list_stickers` / `delete_sticker` | — | Dream-only tools defined inline in `dream.py` for sticker library curation |
| `set_nickname` | `SetNicknameTool` | Set user nickname (affection system) |

## Group Timeline

Append-only conversation timeline per group (`services/memory/timeline.py`).

- **`_TurnLog`**: immutable `Sequence` of finalized Anthropic messages. Supports append and truncation (for compaction), but not arbitrary mutation.
- **`pending`**: mutable buffer of raw `TimelineMessage` dicts accumulating the current user turn. Flushed into `_TurnLog` when an assistant reply arrives.
- **`_GroupState`**: per-group state holding `turns`, `pending`, `turn_times`, `summary`, `last_input_tokens`, `last_cached_msg_index`.
- **Cache stability**: the turns range is byte-identical between calls; the prompt cache breakpoint is placed at `len(messages) - 2`, so only the newest pending merge invalidates the cache.
- **SQLite backing**: every `add()` call also records the raw message via `MessageLog.record()` (fire-and-forget), enabling `_compact_group` to query historical messages with speaker info.
- **Compaction**: `compact(split, new_summary)` truncates turns at a split point (turn count) and stores a new summary. `drop_oldest(count)` is the circuit-breaker fallback.
- **LRU eviction**: max 200 groups in memory; least-recently-used groups evicted on overflow.

## Vision System

Multimodal image understanding, enabled by default (`vision.enabled = true`).

**Pipeline**: QQ image segment → download URL concurrently → downscale via pyvips to `max_dimension` (768px default) → cache to disk as JPEG → send as base64 in Anthropic `image` content blocks.

- **Image cache** (`services/media/image_cache.py`): two-level hash directory layout (`ab/abc123def456.jpg`), 8 concurrent downloads max, auto-cleanup on startup for images older than `cache_max_age_hours`.
- **Per-message limit**: `max_images_per_message` (default 5), excess images rendered as `[图片]` text.
- **Fallback**: when vision is disabled or images fail to download, images are rendered as `[图片]` or `[summary]` text.
- **Sticker recognition**: during history loading, downloaded images are checked against the sticker library by content hash; matches use the sticker path instead of the image cache.

## Sticker System

Persistent sticker library for the bot to collect and send image stickers.

- **Storage** (`services/media/sticker_store.py`): images stored in `storage/stickers/` with `index.json` metadata. Content-hash dedup via SHA256 prefix (`stk_{hash}`). Supports JPG, PNG, WebP; rejects GIF.
- **Tools**: `SaveStickerTool` (requires image_ref, description, usage_hint) and `SendStickerTool` (sends by sticker_id). Both conditional on `sticker.enabled`.
- **Prompt integration**: sticker library summary injected into system block 2 via `StickerStore.format_prompt_view()`.
- **Dream curation**: Dream agent can list and delete stickers, pruning low-usage or inaccurately described entries. Max count enforced via `sticker.max_count`.

## Dream Agent

Background agent for periodic memory consolidation (`plugins/dream/plugin.py`).

- **Schedule**: runs on `dream.interval_hours` interval (default 24h, first run after one full interval). Disabled by default (`dream.enabled = false`).
- **Tasks**: (1) merge pending items into structured memo sections, (2) cross-file validation of references, (3) fix structural issues (dangling refs, oversized memos), (4) sticker library curation (if enabled).
- **Pre-check**: `dream_pre_check()` programmatically scans for structural issues before the LLM loop.
- **Tool loop**: up to `max_rounds` (default 15) rounds with `recall_memo`, `update_memo`, `list_stickers`, `delete_sticker` tools.
- **Logging**: dedicated log sink (`storage/logs/dream_*.log`), filtered from main bot log.
- **Lifecycle**: started on bot connect via `on_bot_connect` hook, stopped on shutdown via `on_shutdown` hook.

## Usage Tracking

SQLite-backed recording of all LLM API calls (`services/llm/usage.py`).

- **Database**: `storage/usage.db` with `llm_calls` table. Records: timestamp, call_type (`chat`/`proactive`/`compact`/`dream`), user_id, group_id, model, input/output/cache_read/cache_create tokens, tool_rounds, elapsed_s, error.
- **Alerting**: PMs admin users when (1) average cache hit rate drops below `cache_hit_warn` % over `cache_alert_window_m` minutes, or (2) a single call exceeds `slow_threshold_s` seconds. Alert cooldown prevents spam.
- **API**: FastAPI routes mounted on the NoneBot app: `/api/usage/today`, `/api/usage/month`, `/api/usage/top-users`, `/api/usage/top-groups`, `/api/usage/timeseries`.
- **TUI** (`services/llm/usage_tui.py`): Rich-based interactive dashboard via `uv run python -m services.llm.usage_cli tui day|week|month [date]`.

## Compact Memo Extraction

During context compaction, the LLM receives an `append_memo` tool that allows it to extract new observations into long-term memory:

- **Private chat**: extracts user traits/preferences into user memo cards. Source tagged as `compact:private:{session_id}`.
- **Group chat**: extracts user traits and group dynamics into user and group memo cards. Collects all user IDs seen in compacted messages. Source tagged as `compact:group:{group_id}`.
- **Circuit breaker**: after `max_failures` (default 3) consecutive compact failures, compaction falls back to dropping oldest messages instead of calling the LLM.

## Message Rendering

- **Reply quotes**: when a message replies to another, the text includes `[回复 昵称(QQ号): 原文摘要]` prefix (50 char cap, 200 for bot's own messages)
- **@mentions**: `[CQ:at,qq=123]` segments are rendered as `@123` in the text sent to the model; self-@ rendered as `@我`
- **Face emoji**: `[CQ:face,id=X]` converted to text representation via `face_to_text()` from `kernel/qq_face.py`
- **Images**: downloaded concurrently, cached, and sent as `image_ref` content blocks (resolved to base64 before API call); excess images or failures rendered as `[图片]`
- **Bot self-ID**: injected into system prompt so the model knows which messages are its own
- **CQ code normalization**: malformed CQ codes (`[CQ:reply,id:123]`) auto-fixed to `[CQ:reply,id=123]`

## Access Control

- `allowed_groups`: group whitelist (empty = allow all)
- `allowed_private_users`: private chat whitelist (empty = allow all)
- `admins`: QQ→nickname dict injected into system prompt as trusted sources; authorized for admin tools (MuteUser, SetTitle, SendGroupMsg, SaveSticker with `admin` source tag)

## Admin Dashboard

Admin panel at `/admin/` is a system service mounted directly in `bot.py` (not via a plugin):

- **Authentication**: HMAC-signed cookie via `AdminAuthMiddleware`. Token from `ADMIN_TOKEN` env var or `config.toml` → `admin_token`
- **Pages**: Dashboard (overview + uptime), Usage (Chart.js trends), Groups (overrides viewer), Config (read-only viewer), Soul (online editor for identity.md/instruction.md), Logs (tail viewer)
- **Plugin routes**: plugins can declare `register_admin_routes()` → `AdminRoute` to add custom pages
- **Location**: `admin/` (17 files, migrated from `admin/`)

## Plugin Discovery Flow

```
bot.py
  ├── PluginBus.register(ChatPlugin())          # directory-based, manual
  ├── PluginBus.register(AffectionPlugin())     # directory-based, manual
  ├── ... (10 directory plugins total)
  └── PluginBus.discover_plugins("plugins")
        ├── Pass 1: scan subdirectories
        │     ├── chat/plugin.py → already registered, skip
        │     ├── affection/plugin.py → already registered, skip
        │     └── ...
        └── Pass 2: scan standalone .py files
              ├── datetime.py → DateTimePlugin (priority=1)
              ├── group_admin.py → GroupAdminPlugin
              ├── http_api.py → HttpApiPlugin
              ├── web_fetch.py → WebFetchPlugin
              └── web_search.py → WebSearchPlugin
```
