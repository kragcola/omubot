# 项目信息

> 凤笑梦 (Emu Otori) QQ 机器人 — 基于 NoneBot2 + Omubot 三层框架。
> 快速搭建见 [setup-guide.md](setup-guide.md)，架构细节见 [architecture.md](architecture.md)。

## 身份标识

| 项目 | 值 |
| --- | --- |
| 项目名 | omubot |
| Bot 名称 | 凤笑梦 (Emu Otori) |
| Bot QQ 号 | 384801062 |
| Git 仓库 | [kragcola/omubot](https://github.com/kragcola/omubot) |
| 原 bot（被替换） | MaiBot（见底部"旧 maibot 详情"） |

## 管理人员

| QQ 号 | 昵称 | 角色 |
| --- | --- | --- |
| 1416930401 | 工丿囗 | 超级管理员 / 开发者 |

## 目标群聊

| 群号 | 群名 | 备注 |
| --- | --- | --- |
| 984198159 | 测试 | 旧 maibot 活跃群 |
| 993065015 | 烤 | 旧 maibot 活跃群 |

> 群聊访问门禁通过 `config/group-policy.json` 控制；`whitelist` 模式只开启白名单群，`blacklist` 模式只关闭黑名单群。Web 端在 `/admin/groups` 编辑。

## 技术架构

```
QQ ←→ NapCat (WS) ←→ NoneBot2 → DeepSeek API (Anthropic 兼容)
                      └── Omubot 三层框架
                           ├── 内核层: PluginBus · 类型契约 · 插件发现
                           ├── 服务层: LLMClient · Timeline · CardStore · Scheduler
                           └── 插件层: 15 个可开关插件
```

- **LLM 后端**：DeepSeek API（`api.deepseek.com/anthropic`），Anthropic Messages 兼容端点
- **模型**：`deepseek-v4-flash`（上下文窗口 1M tokens）
- **QQ 协议**：NapCat (NTQQ) via Docker
- **部署**：Docker Compose（napcat + bot 双容器）
- **插件框架**：Omubot PluginBus — 钩子驱动，依赖拓扑排序，异常隔离

### Omubot 三层模型

| 层 | 目录 | 职责 | 约束 |
|---|------|------|------|
| 内核 | `kernel/` | PluginBus 调度、类型定义、插件发现 | 不 import 任何服务/插件 |
| 系统服务 | `services/` | LLM 调用、记忆、时间线、调度器 | 可互相 import，不 import 插件 |
| 插件 | `plugins/` | 好感度、日程、表情包、梦境等 | 只 import 内核类型 + 系统服务 |

### 19 个插件一览

| 插件 | 优先级 | 形态 | 功能 |
|------|--------|------|------|
| ChatPlugin | 0 | 目录 / 系统级 | 核心聊天：消息路由、LLM 调用、tool loop |
| DateTimePlugin | 1 | 目录 | 时间日期查询工具 |
| WebSearchPlugin | 1 | 目录 | DuckDuckGo / Bing 网页搜索 |
| WebFetchPlugin | 1 | 目录 | 网页内容抓取 |
| HttpApiPlugin | 1 | 目录 | 通用 HTTP API 调用 |
| GroupAdminPlugin | 1 | 目录 | 群管理（禁言、头衔、发消息） |
| HistoryLoaderPlugin | 5 | 目录 / 系统级 | 启动时加载群历史消息 |
| KnowledgePlugin | 8 | 目录 | 知识库检索与对话上下文注入 |
| AffectionPlugin | 10 | 目录 | 好感度系统：分数、昵称、态度调节 |
| SchedulePlugin | 20 | 目录 | 模拟日程：每日 LLM 生成，结合真实日期 |
| FoodPlugin | 25 | 目录 | 饮食/点餐相关指令 |
| MemoPlugin | 30 | 目录 | 记忆卡片：7 类 3 作用域，检索门控 |
| StickerPlugin | 40 | 目录 | 表情包库：收藏、检索、发送（依赖系统层 vision） |
| SlangPlugin | 42 | 目录 | 群内黑话学习、审核、复核与注入 |
| DreamPlugin | 150 | 目录 | 梦境整合：定期整理记忆、清理表情包 |
| BilibiliPlugin | 190 | 目录 | B站视频链接识别：标题/封面/简介注入 |
| EchoPlugin | 200 | 目录 | 复读检测：5 分钟内同消息 3 次触发 |
| ElementDetectorPlugin | 210 | 目录 | 特殊消息元素检测 |
| DebugCommandPlugin | 300 | 目录 | 调试指令：/plugins、/version |

`vision` 是系统服务能力包（`plugins/vision/plugin.json`），只在插件中心系统视图中只读展示，不作为可启停运行时插件。

## 配置要点

### 双配置文件

| 文件 | 用途 | 谁读取 |
| --- | --- | --- |
| `config/.env` | NoneBot 框架层（SUPERUSERS, ONEBOT_WS_URLS） + LLM 环境变量 | `nonebot.init()` |
| `config/config.json` | Bot 业务层主配置（LLM、群聊、vision、compact 等） | `kernel/config.py` |
| `config/config.toml` | legacy 兼容配置源（首次管理端保存后会迁移为 JSON） | `kernel/config.py` |
| `config/group-policy.json` | 群聊访问门禁（白/黑名单） | `kernel/config.py` + `/admin/groups` |
| `plugins/<name>/config.default.json` | 插件默认配置（仅目录插件） | 插件通过 `load_plugin_config()` 读取 |
| `storage/plugins/config/<name>.json` | Admin Web 保存的插件运行时覆盖 | `PluginConfigStore` 合并 |

优先级：`config/config.json`（兼容 TOML）< 环境变量 < CLI 参数

### 人设文件

| 文件 | 内容 |
| --- | --- |
| `config/soul/identity.md` | 角色定义（从 `soul/identity.example.md` 复制） |
| `config/soul/instruction.md` | 行为指令（从 `soul/instruction.example.md` 复制） |

修改后 `docker compose restart bot` 即可生效。

## 存储路径

容器内挂载点：`/app/storage`。2026-05-21 起改为 Docker named volume `omubot-storage`（`external: true`），主机不再直接持有 `./storage`；如需检查或备份请走 `scripts/dev/storage_export.sh` 或 `docker exec qq-bot ...`，详见 [maintenance-log.md](../maintenance-log.md) Phase 3 条目。

```
/app/storage/         # named volume omubot-storage
├── usage.db          # LLM 用量追踪（SQLite）
├── messages.db       # 群消息持久化（SQLite）
├── memory_cards.db   # 类型化记忆卡片（CardStore，7 类 3 作用域）
├── logs/
│   ├── bot_*.log     # 主日志（10MB 切割，30 天保留）
│   └── dream_*.log   # Dream Agent 日志
├── memories/         # 旧 .md 备忘录（已迁移，源文件 → .md.migrated）
├── image_cache/      # 图片缓存（启动时自动清理过期）
├── stickers/         # 表情包库（SHA256 去重）
├── affection/        # 好感度数据
├── schedule/         # 模拟日程（每日 JSON）
└── plugins/          # 插件私有数据（日志、缓存等）
```

## 关键端口

| 端口 | 服务 | 用途 |
| --- | --- | --- |
| 6099 | NapCat WebUI | 扫码登录、QQ 管理 |
| 5173 | Vite Dev Server | 前端开发热更新（`npm run dev`） |
| 8081 | NoneBot FastAPI | Bot HTTP API + 用量查询 + Admin Dashboard（docker 映射至容器 :8080） |
| 29300 | NapCat HTTP | OneBot HTTP API |
| 3001 | NapCat WS | OneBot WebSocket（NoneBot 连接） |

## 模式与开关

| 功能 | 配置路径 | 当前值 |
| --- | --- | --- |
| 主动插话 | `config/soul/identity.md` → `## 插话方式` | 已启用 |
| @人后才回复 | `config/config.json` → `group.at_only` | `false` |
| Debounce 等待 | `[group].debounce_seconds` | `5.0` 秒 |
| Batch 触发 | `[group].batch_size` | `10` 条 |
| 多模态视觉 | `[vision.qwen]` → `api_key` 填写即启用 | 已启用（硅基流动 Qwen3-VL-30B） |
| 表情包系统 | `plugins/sticker/config.default.json` → `enabled` | `true` |
| 表情包发送频率 | `plugins/sticker/config.default.json` → `frequency` | `"frequently"` |
| Dream Agent | `plugins/dream/config.default.json` → `enabled` | `true`（每 24h） |
| 用量追踪 | `[llm.usage].enabled` | `true` |
| B站视频识别 | `plugins/bilibili/config.default.json` → `enabled` | `true` |
| B站回复模式 | `plugins/bilibili/config.default.json` → `reply_mode` | `autonomous` |
| B站回复概率 | `plugins/bilibili/config.default.json` → `bilibili_talk_value` | `0.8` |
| B站兴趣关键词 | `plugins/bilibili/config.default.json` → `high/medium/low_interest_keywords` | 高69/中29/低19 个 |
| B站兴趣 LLM 回退 | `plugins/bilibili/config.default.json` → `interest_llm_fallback` | `0.6` |
| 上下文压缩 | `[compact].ratio` | `0.7` |
| 模拟日程 | `plugins/schedule/config.default.json` → `enabled` | `true`（每日凌晨 2:00 生成） |
| 好感度系统 | `plugins/affection/config.default.json` → `enabled` | `true`（每次互动 +0.8，日上限 10.0） |
| 要素察觉 | `plugins/element_detector/config.default.json` → `enabled` | `true`（2 条规则） |
| 群聊隐私遮掩 | `[group].privacy_mask` | `true` |
| 预回复思考 | thinker（内置） | `true`（轻量 LLM 判断 reply/wait/search） |
| 日志频道 | `[log.channels]` | 6 个默认开启，其余关闭 |
| 插件发现 | 自动 | `bot.py` 调用 `bus.discover_plugins()` |
| plugin.json 覆盖 | 自动 | 目录插件可选，覆盖类属性元数据 |

## 单群覆盖

在 `config/config.json` 中按以下结构添加（legacy TOML 也兼容）：

```json
{
  "group": {
    "overrides": {
      "<群号>": {
        "at_only": true,
        "debounce_seconds": 10.0,
        "batch_size": 20,
        "blocked_users": [123],
        "privacy_mask": false
      }
    }
  }
}
```

## API 端点

### 用量 API

| 端点 | 说明 |
| --- | --- |
| `GET /api/usage/today` | 今日用量概况 |
| `GET /api/usage/month` | 月度用量概况 |
| `GET /api/usage/top-users` | 用户用量排行 |
| `GET /api/usage/top-groups` | 群用量排行 |
| `GET /api/usage/timeseries` | 按时段 token 消耗 |

### Admin 管理面板（Vue 3 SPA）

前端基于 Vue 3 + Naive UI + TypeScript，18 个页面，56 个 JSON API 端点。

| 页面 | 路由 | 说明 |
| --- | --- | --- |
| 仪表盘 | `/admin/` | uptime、今日统计（含 cache 命中率 / 平均延迟 / 错误数）、心情卡片、待办、近 7 天活跃群 |
| 用量统计 | `/admin/usage` | 时序图表 + 用户/群排行（**软下线**：核心指标已并入仪表盘，导航栏不再展示，路由仍可手动访问） |
| 沙盒 | `/admin/sandbox` | 本地 LLM 对话测试（在「设置与维护」一级导航） |
| 人设编辑 | `/admin/soul` | identity.md / instruction.md 在线编辑 |
| 日程心情 | `/admin/schedule` | MoodProfile 可视化 + 今日日程（**软下线**：仪表盘右栏已完整覆盖，导航栏不再展示） |
| 记忆管理 | `/admin/memory` | 卡片列表、池配置、CRUD |
| 好感度 | `/admin/affection` | 用户排行、详情编辑 |
| 表情包 | `/admin/stickers` | 网格浏览、描述编辑、删除 |
| 群内黑话 | `/admin/slang` | 候选词审核、AI 复核、注入设置 |
| 表达方式 | `/admin/style` | 群内表达风格抽取、profile 生成、反馈复核 |
| 知识库 | `/admin/knowledge` | 文档源 / 搜索 / 上下文调试 / 评测 / 图谱关系 / 图谱节点 / 候选队列 |
| Memo | `/admin/memos` | 按 user_id/kind 筛选 |
| 群管理 | `/admin/groups` | 群列表、实时状态、消息 |
| 插件 | `/admin/plugins` | 插件列表、工具/指令详情 |
| 调度器 | `/admin/scheduler` | 各群 slot 状态、静音控制（**软下线**：实时数据已失效，导航栏不再展示） |
| 配置 | `/admin/config` | 结构化配置编辑（JSON 主格式，支持 `?task=rhythm` 等 query 直达任务） |
| 系统 | `/admin/system` | 资源 + 维护建议 + 服务健康 + 关键错误 + 运行策略（含跳配置入口） + 备份 |
| 日志 | `/admin/logs` | 实时 SSE 推送 + 历史文件 |

访问 `http://localhost:8081/admin/`（生产）或 `http://localhost:5173/admin/`（开发），使用 `ADMIN_TOKEN` 环境变量登录。

系统页当前采用两层健康口径：

- 顶部“运维建议 / 健康告警”只展示达到阈值的高优先级异常，用于减少长期运行下的噪声。
- 下方“服务级健康”继续保留完整 warning / error 细节，便于人工排查轻量退化和回退链路。

#### 当前前端重构状态（2026-05-14）

**阶段 0-2 已完成，阶段 3 首个视图 Dashboard 重构完成并上线**。详细跟踪见 [docs/tracking/web-refactor.md](./tracking/web-refactor.md)，阶段方案见 [web-refactor-plan.md](./web-refactor-plan.md)。

- 阶段 0（环境清理）：`.nvmrc` Node 20 锁定 + `package.json engines` + `.gitignore admin/static/assets/`。`git rm --cached` 待人工确认。
- 阶段 1（基础设施固化）：`themeOverrides` 扩展（Tag / DataTable / placeholder / icon），`uno.config.ts` 加 6 个语义 shortcut，新增 [admin-ui-tokens.md](./admin-ui-tokens.md) 速查表。**2026-05-14 验收通过后删除 `global.css` 7 个冗余 `!important` 块**（41 行），计数 51 → 31，剩余 31 处全部在 keep 区（`.dark .n-button:not(...)` + `.dark .n-menu` 系列 deep 选择器，themeOverrides API 不能表达）。
- 阶段 2（公共组件补齐）：新增 `StateBadge / LogPanel / DataToolbar / FieldGroup / SparklineChart` 共 5 个公共组件；`SectionCard` 评估为重复造轮子，跳过。新增 `/admin/design-playground` 视觉验收路由。
- 阶段 3 进行中：
  - ✅ **DashboardView** — 2026-05-14 完成三轮迭代后**视觉验收通过**：
    1. 第一版重构：Hero 压缩 + 3 主 KPI + 24h 调用曲线 + 近 7 天活跃群 Top 5 + 待处理 + 日程+心情合并 + LogPanel。
    2. 布局调整：改两栏主布局，右侧 320px sticky 长条放竖版日程时间线 + 心情 + 下一段，左栏重新排布消除空白。
    3. 新增「今日学习收录」模块 + 后端 `/api/admin/learning/today` 聚合端点，3 栏展示黑话 / 表达风格 / 表情包的今日新入库数量、审核统计、最新 Top 5（表情包带缩略图）。
  - ✅ **LogsView** — 2026-05-14 两轮迭代后**视觉验收通过**：
    1. 组件层重构（commit 8197e60，606 → 583 行）：实时流改用公共 LogPanel 组件，删 60 行手写渲染；StateBadge 统一状态徽章；主 / 侧栏改物理顺序。
    2. 视觉重设计：工具栏单行化（自研 Segment 段式选等级 + 内嵌图标的搜索框 + 可点清除 + 重置筛选）；默认模式自动隐藏 DEBUG 降噪；侧栏分组折叠（Bot 展开、Dream 折叠、带今日活跃 tag、相对日期显示）；文件模式去黑底终端改用 `--om-surface-2` 浅面板 + 结构化解析 time/level/channel/msg 四列 grid + 等级色标。LogsView 583 → 1175 行，功能视觉完整度大幅提升。
  - ✅ **GroupsView** — 2026-05-14 完成全量重构后**视觉验收通过**：
    1. 主页面瘦身：4 张指标卡片 → 单行紧凑概览条（群 / 自定义 / 主动 / 静默 / 关闭 / 门禁），首屏 ~150px → ~52px；表格列数 7 → 4，点行打开抽屉。
    2. 抽屉重构：旧 Snapshot 块 + Profile 长滚 → `基础 / 节奏 / 高级` 三 Tab（基础含 FieldGroup + 段式按钮 + 6 开关、节奏含 5 个底层数字、高级含工具矩阵 + 实时状态 + 最近消息 + 策略审计）；门禁块从首屏移到独立抽屉，双入口（页头按钮 + 概览条 chip）。
    3. SSE 事件订阅 last_message_at / 24h 计数实时更新；`window.confirm` → NPopconfirm；连带修了 `config/config.json` 里 `blocked_users: null` 的预存配置 bug。
  - ✅ **LoginView** — 2026-05-14 完成 PR A→C：视觉沿用双层构图 + 雾青渐变骨架，间距/圆角全量对齐 token 体系，chip/feature 背景改 `color-mix(... var(--om-surface) ...)` 浅深自适应；接入易用增强（autofocus、Caps Lock 检测、上次登录时间、失败卡片抖动、提交按钮三态文案）和安全增强（错误分级 invalid_token vs network_error、连续失败 5 次锁定 30 秒、非 HTTPS 警告条、错误提示带尝试计数）。`auth.login` store 同步改返回结构。规模 431→423 行，`vue-tsc` 0 error，`vite build` 4.83s。**2026-05-14 用户视觉验收通过**。
  - ✅ **ConfigView** — 2026-05-15 整页重做完成：拆出 7 个子组件（ConfigField / ConfigListField / ConfigKvField / ConfigObjectGroup / ConfigSecretInput / ConfigJsonInput / ConfigStatusStrip）+ 1 个 helper（section-labels.ts），ConfigFieldEditor.vue 改成 deprecated 空壳。任务字段改"按 namespace 分桶 → AppPanelSection × N → ConfigField × N"，单字段基于公共 FieldGroup（switch/select/number 走 inline 模式、其它走 stacked），错误态加左红边 + helper 红字、未保存字段加左黄边 + 已修改 tag + 字段级"撤销"按钮；list / kv 行 flex 布局，switch 类不再撑满 1fr；object 改左 border 装饰 + ≥2 层折叠，去掉卡套卡。toolbar 接入 PageToolbar，diff/backup/audit 全部收纳进 AppPanelSection。`vue-tsc --noEmit` 0 error，`npm run build` 5.43s 通过，ConfigView bundle 52 KB / gzip 17.5 KB。
  - ✅ **SystemView** — 2026-05-15 完成 PR B-1 helpers / B-2 9 只读子组件 / B-3 3 交互子组件 / C 视觉收敛 4 轮拆分：3326 行 → 590 行（-82%）+ 12 个 < 500 行子组件，头部样式收敛到 [AppPanelSection](../admin/frontend/src/components/common/AppPanelSection.vue)。同日另做布局重构：删 SystemMetrics 引用、资源上移、Policies 加配置跳转、advancedTools 软下线 schedule/scheduler/usage（详见 [maintenance-log.md](../maintenance-log.md)）。
  - ✅ **SlangView** — 2026-05-15 完成 PR B-1 helpers / B-2 4 只读子组件 / B-3 5 交互子组件 / C 视觉收敛 4 轮拆分：2662 行 → **814 行（-69.4%）** + 9 个子组件 + 3 helper，头部样式全部迁到 [AppPanelSection](../admin/frontend/src/components/common/AppPanelSection.vue)。PR C 把 SlangTermList 的 `slang-list-panel` 与主视图的 `slang-settings-panel` 两块 panel-head 改 AppPanelSection（含 `#aside` slot 顶替分页/折叠按钮），删 7 块冗余样式（panel-head/eyebrow/title/list-panel/settings-panel padding，约 62 行）；`SlangView-*.js` 60.89→**60.63 KB** / gzip 17.34→**17.26 KB**（B-3 +overhead 全部回吐 + 微净降）。

历史已统一风格的页面（2026-05-06 第一轮手工统一）：

- `LoginView` / `DashboardView` / `SystemView` / `LogsView` / `GroupsView` / `MemoryView` / `PluginsView` / `KnowledgeView` / `UsageView`

这一批仍会按本次重构的 PR A 模板再过一遍骨架迁移，目标是消除内联样式、接入新公共组件。

统一风格文档：

- [admin-ui-style-guide.md](./admin-ui-style-guide.md)
- [admin-ui-tokens.md](./admin-ui-tokens.md)（**新**，token / shortcut 速查）
- [agent-ui-guidelines.md](./agent-ui-guidelines.md)
- [web-refactor-plan.md](./web-refactor-plan.md)（**新**，分阶段方案）
- [tracking/web-refactor.md](./tracking/web-refactor.md)（**新**，逐项勾选跟踪）

#### 项目内 Agent / Codex Skill

项目内两个 Skill 并行生效：

- `$omubot-admin-console` — 工作流 Skill：admin/frontend 重构、wiki/代码审计、增量修改的整体流程。包含 Maintenance Log Policy 条款。
- `$omubot-design-system` — 设计系统执行 Skill：Calm Ops 色板/圆角/间距锁定、公共组件 API 速查、Naive UI themeOverrides 单一来源、反面样例与 PR 视觉验收清单。触发于 `.vue`、`uno.config.ts`、`global.css`、`stores/app.ts` 改动。

安装位置：

- 项目内（Claude 版本）：`.claude/skills/omubot-admin-console/`、`.claude/skills/omubot-design-system/`
- Codex 仓库源包：`codex-skills/omubot-admin-console/`
- Codex 安装脚本：`scripts/install-codex-skill.sh`
- 本机已同步：
  - `~/.codex/skills/omubot-admin-console/`、`~/.codex/skills/omubot-design-system/`
  - `~/.claude/skills/omubot-admin-console/`、`~/.claude/skills/omubot-design-system/`

#### Claude × codex 协同工作流（2026-05-14 新增）

分工：Claude 负责决策 + 审查，codex 负责机械执行。目标是把规则明确、判断密度低的改动分流到 codex，节约成本。

- 规范目录：[.claude/handoff/](../.claude/handoff/)
  - [README.md](../.claude/handoff/README.md) — 命名规范、生命周期、审查流程
  - [TEMPLATE.md](../.claude/handoff/TEMPLATE.md) — spec 模板
  - `TASK-YYYYMMDD-NN-slug.md` — 具体任务
- 流程：Claude 写 spec → 用户 `git stash` + 建分支 `task-YYYYMMDD-NN` → codex 执行 → 用户把 `git diff HEAD` 贴给 Claude 审查 → 通过后 commit + `git stash pop`（不 merge 回 main，main 可能严重落后）
- spec 必含字段：目标 / 约束 / 动的文件 / 不准动 / 验收命令（可 0/非 0 判断）/ 用户复制命令段（6 步）/ 审查要点
- 2026-05-14 已做一轮干跑验证，修复三个初版 spec 漏洞（`git diff main` → `git diff HEAD`、grep 误匹配文档注释、期望数字与实际不一致）。详见 maintenance-log。
- 当前第一个 spec：[TASK-20260514-01](../.claude/handoff/TASK-20260514-01-remove-redundant-important.md) — 删除 `global.css` 冗余 `!important` 块（期望 `!important` 从 51 降到 31）。**2026-05-14 已由 Claude 在主线直接完成（视觉验收通过后），spec 未交给 codex 执行；保留作为干跑验证案例。**

适合给 codex 做的：机械转换 / 照表执行 / 规则明确的样板代码。
不给 codex 做：视觉设计 / 信息架构 / 跨层贯穿改动 / 调试 / 鉴权相关。

## 常用命令速查

```bash
# 本地开发
uv sync                          # 安装依赖
uv run python bot.py             # 直接运行（需 NapCat 先启动）
uv run ruff check                 # Lint
uv run pytest                    # 测试
uv run pyright                   # 类型检查

# Docker 运维
docker compose up -d             # 全部启动
docker compose up napcat -d      # 仅启动 NapCat
docker compose restart bot       # 重启 bot（config/ 变更）
docker compose up bot -d --build # 重建 bot（代码/依赖变更）
docker compose restart napcat    # 重启 NapCat（断线重连，不要 down+up）
docker compose logs bot --tail=50

# 用量查看
uv run python -m services.llm.usage_cli tui day
curl http://localhost:8081/api/usage/today
```

## 常见问题

### NapCat 断线/TX 风控

- 现象：Bot 无故停止回复，NapCat 日志显示连接断开
- 原因：通常是腾讯反欺诈（device fingerprint 变化或异常行为）
- 处理：`docker compose restart napcat`（不要 `down`+`up`），必要时重新扫码登录
- 预防：固定使用 `restart`，不频繁上下线

### Bot 不回复

1. `docker compose ps` — 两个容器都在运行
2. `docker compose logs bot --tail=50` — 看是否有 ERROR
3. NapCat WebUI (`:6099`) — QQ 是否在线
4. `config/.env` → `SUPERUSERS` JSON 格式正确（双引号，无尾逗号）
5. `config/config.json`（或 legacy `config.toml`）中的 `llm.api_key` 有效、余额充足
6. `config/group-policy.json` — 当前群是否被门禁关闭（也可在 `/admin/groups` 查看）
7. 在群里 @bot 或私聊测试

### 修改人设后不生效

```bash
docker compose restart bot    # Soul 文件通过 volume mount，restart 即可
```

### 升级代码

```bash
git pull
docker compose up bot -d --build
```

### 查看花了多少 token

```bash
uv run python -m services.llm.usage_cli tui day
# 或浏览器访问 http://localhost:8081/api/usage/today
```

---

## 旧 maibot 详情

> 以下信息提取自 `/Users/kragcola/MaiM-with-u/`，用于迁移参考和故障回溯。

### 旧 bot 身份

| 项目 | 值 |
| --- | --- |
| 框架 | MaiBot v7.3.5（MaiM-with-u 生态） |
| Bot QQ | 384801062 |
| 昵称 | emu不吃小杯面 |
| 别名 | 姆、emu、笑梦、凤同学、姆姆、凤笑梦 |
| 人设 | 凤笑梦 (Emu Otori) — 与新版相同角色 |
| 部署日期 | 2026-01-16 |
| 累计请求 | 28,769 次（截至 2026-04-29） |

### 新 bot vs 旧 bot 关键差异

| 维度 | 旧 maibot (MaiBot) | 新 bot (omubot) |
| --- | --- | --- |
| 框架 | MaiBot (自研) | NoneBot2 + Omubot 三层框架 |
| 协议适配 | MaiBot-Napcat-Adapter (自研) | nonebot-adapter-onebot |
| LLM API 格式 | OpenAI 兼容 (`/v1`) | Anthropic 兼容 (`/anthropic`) |
| Prompt 组织 | Planner + Replyer 分离 | Thinker → LLM + Tool loop |
| 记忆系统 | LPMM 知识图谱 + HippoMemorizer | CardStore（SQLite，7 类卡片 + supersedes 取代边） |
| 群聊触发 | Planner 决策模型 | Debounce/Batch 调度器 |
| 部署方式 | 本地 Python 进程 | Docker Compose |
| 插件系统 | 无 | PluginBus 钩子驱动，依赖拓扑排序 |
| 表情包 | `emoji` 目录 | `storage/stickers/` SHA256 去重 |
| LPMM 知识库 | 本地 RAG 图谱 | 无（依赖 memo 系统 + 上下文压缩） |

---
> 本文档应随项目演化持续更新。每次重大变更后同步更新上述表格和记录。
