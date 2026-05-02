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

> 群聊白名单通过 `config.toml` → `[group].allowed_groups` 控制，空 = 所有群。

## 技术架构

```
QQ ←→ NapCat (WS) ←→ NoneBot2 → DeepSeek API (Anthropic 兼容)
                      └── Omubot 三层框架
                           ├── 内核层: PluginBus · 类型契约 · 插件发现
                           ├── 服务层: LLMClient · Timeline · CardStore · Scheduler
                           └── 插件层: 14 个可开关插件
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

### 14 个插件一览

| 插件 | 优先级 | 形态 | 功能 |
|------|--------|------|------|
| ChatPlugin | 0 | 单文件 | 核心聊天：消息路由、LLM 调用、tool loop |
| DateTimePlugin | 1 | 单文件 | 时间日期查询工具 |
| WebSearchPlugin | 1 | 单文件 | DuckDuckGo 网页搜索 |
| WebFetchPlugin | 1 | 单文件 | 网页内容抓取 |
| HttpApiPlugin | 1 | 单文件 | NapCat HTTP API 调用 |
| GroupAdminPlugin | 1 | 单文件 | 群管理（禁言、头衔、发消息） |
| StickerPlugin | 10 | 单文件 | 表情包库：收藏、检索、发送（依赖系统层 vision） |
| MemoPlugin | 20 | 单文件 | 记忆卡片：7 类 3 作用域，检索门控 |
| AffectionPlugin | 30 | 目录 | 好感度系统：分数、昵称、态度调节 |
| SchedulePlugin | 35 | 目录 | 模拟日程：每日 LLM 生成，结合真实日期 |
| HistoryLoaderPlugin | 5 | 单文件 | 启动时加载群历史消息 |
| DreamPlugin | 150 | 单文件 | 梦境整合：定期整理记忆、清理表情包 |
| EchoPlugin | 200 | 单文件 | 复读检测：5 分钟内同消息 3 次触发 |
| ElementDetectorPlugin | 210 | 单文件 | 特殊消息元素检测 |

## 配置要点

### 双配置文件

| 文件 | 用途 | 谁读取 |
| --- | --- | --- |
| `config/.env` | NoneBot 框架层（SUPERUSERS, ONEBOT_WS_URLS） + LLM 环境变量 | `nonebot.init()` |
| `config/config.toml` | Bot 业务层（LLM、群聊、vision、dream、compact 等） | `kernel/config.py` |

优先级：TOML < 环境变量 < CLI 参数

### 人设文件

| 文件 | 内容 |
| --- | --- |
| `config/soul/identity.md` | 角色定义（从 `soul/identity.example.md` 复制） |
| `config/soul/instruction.md` | 行为指令（从 `soul/instruction.example.md` 复制） |

修改后 `docker compose restart bot` 即可生效。

## 存储路径

```
storage/
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
└── plugins/          # 插件私有数据（日志、缓存等，gitignored）
```

## 关键端口

| 端口 | 服务 | 用途 |
| --- | --- | --- |
| 6099 | NapCat WebUI | 扫码登录、QQ 管理 |
| 8081 | NoneBot FastAPI | Bot HTTP API + 用量查询 + Admin Dashboard（docker 映射至容器 :8080） |
| 29300 | NapCat HTTP | OneBot HTTP API |
| 3001 | NapCat WS | OneBot WebSocket（NoneBot 连接） |

## 模式与开关

| 功能 | 配置路径 | 当前值 |
| --- | --- | --- |
| 主动插话 | `config/soul/identity.md` → `## 插话方式` | 已启用 |
| @人后才回复 | `config/config.toml` → `[group].at_only` | `false` |
| Debounce 等待 | `[group].debounce_seconds` | `5.0` 秒 |
| Batch 触发 | `[group].batch_size` | `10` 条 |
| 多模态视觉 | `[vision.qwen]` → `api_key` 填写即启用 | 已启用（硅基流动 Qwen3-VL-30B） |
| 表情包系统 | `[sticker].enabled` | `true` |
| 表情包发送频率 | `[sticker].frequency` | `"frequently"` |
| Dream Agent | `[dream].enabled` | `true`（每 24h） |
| 用量追踪 | `[llm.usage].enabled` | `true` |
| 上下文压缩 | `[compact].ratio` | `0.7` |
| 模拟日程 | `[schedule].enabled` | `true`（每日凌晨 2:00 生成） |
| 好感度系统 | `[affection].enabled` | `true`（每次互动 +0.8，日上限 10.0） |
| 群聊隐私遮掩 | `[group].privacy_mask` | `true` |
| 预回复思考 | thinker（内置） | `true`（轻量 LLM 判断 reply/wait/search） |
| 日志频道 | `[log.channels]` | 6 个默认开启，其余关闭 |
| 插件发现 | 自动 | `bot.py` 调用 `bus.discover_plugins()` |
| plugin.json 覆盖 | 自动 | 目录插件可选，覆盖类属性元数据 |

## 单群覆盖

在 `config/config.toml` 中按以下格式添加：

```toml
[group.overrides."<群号>"]
at_only = true           # 仅被 @ 时回复
debounce_seconds = 10.0  # 更长等待
batch_size = 20          # 更大攒量
blocked_users = [123]    # 屏蔽用户（与全局取并集）
privacy_mask = false     # 关闭群聊隐私遮掩
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

### Admin 管理面板

| 端点 | 说明 |
| --- | --- |
| `/admin/` | 总览（uptime、今日用量卡片） |
| `/admin/usage` | 用量统计（Chart.js 趋势图 + 排行） |
| `/admin/groups` | 群聊管理（overrides 配置查看） |
| `/admin/config` | 配置查看（config/config.toml 只读） |
| `/admin/soul` | Soul 编辑（在线编辑 identity.md / instruction.md） |
| `/admin/logs` | 日志查看（tail 最近 N 行） |

访问 `http://localhost:8081/admin/`，使用 `ADMIN_TOKEN` 环境变量登录。

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
5. `config/config.toml` → `api_key` 有效、余额充足
6. `[group].allowed_groups` — 是否限制了目标群
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
