# QQ 群 Bot 生态代码审计与 Omubot 对比报告

审计日期：2026-05-07  
Omubot 基线：`/Volumes/我的电脑/omubot`，commit `d401432`  
外部审计工作区：`/private/tmp/qqbot-audit/2026-05-07/`

> 原计划工作区 `/Volumes/我的电脑/qqbot-audit/2026-05-07/` 在当前沙箱中不可写，因此外部仓库浅克隆和 PyPI sdist 下载落在 `/private/tmp/qqbot-audit/2026-05-07/`。本轮未运行外部 bot、未安装外部依赖、未执行外部安装脚本；Omubot 仓库只新增本审计报告、项目索引和评分矩阵。

## 交付物

- 主报告：`docs/audits/qqbot-comparison-2026-05-07.md`
- 机器索引：`docs/audits/qqbot-project-index.json`
- 评分矩阵：`docs/audits/qqbot-audit-matrix.csv`
- 下载记录：`/private/tmp/qqbot-audit/2026-05-07/manifests/clone-manifest.md`
- 本地证据笔记：`/private/tmp/qqbot-audit/2026-05-07/notes/*.evidence.txt`

## 审计方法

本轮按三层分组比较，避免把“协议端”和“成品 bot”混在一起打分。

| 分层 | 项目 | 比较重点 |
| --- | --- | --- |
| AI / LLM QQ bot 成品 | Omubot、MaiBot、MoFox-Core、AstrBot、LangBot、kirara-ai | 自然对话、人设、记忆、Prompt、插件、Web 管理、依赖重量 |
| QQ bot / 插件生态 | zhenxun_bot、Yunzai、Miao-Yunzai、HoshinoBot、NcatBot | 插件体系、命令生态、群管理、运维方式、扩展成本 |
| 框架与协议层 | NoneBot2、adapter-onebot、Koishi、NapCatQQ、Lagrange.Core、LLOneBot、go-cqhttp、Ariadne | 协议稳定性、适配边界、生态成熟度、集成风险 |

采样规则：

- 所有可访问 GitHub 项目均浅克隆到本地；NcatBot GitHub clone 失败，改用 PyPI `ncatbot==4.4.1.post1` sdist 薄审计。
- 每个深审项目至少落到 3 个代码证据点；证据来自依赖清单、入口、插件管理、消息管线、存储、LLM/Prompt、协议适配或运维代码。
- Stars/forks/license/push 时间通过 GitHub API 或 PyPI 元数据补充，代码判断以本地 clone 为准。

## 总体结论

Omubot 当前不是生态最大、插件最多、平台最全的项目，但它在“QQ 群陪伴型 LLM bot”这个窄目标上已经形成清晰优势：`NoneBot2 + NapCat` 承担协议和基础事件，`kernel/services/plugins` 三层承载 LLM 生命周期，Admin Web 面向实际运营，Slang v3 把群内黑话做成可审核、可回滚、可漂移治理的语境知识，而不是直接自动污染记忆或人设。

外部项目给出的方向很明确：

- MaiBot 在拟人表达、表达学习、黑话推断上更激进，更像“生命体模拟”；Omubot 更适合走“可治理、可解释、可运营”的路线。
- AstrBot / LangBot 在平台化、插件生命周期、Provider 抽象、Web 管理上领先；Omubot 可以借鉴局部机制，但不应把平台复杂度整体搬进来。
- MoFox / LangBot / AstrBot 的向量和图记忆能力更强，但默认引入 FAISS、向量库、embedding 后会显著增加镜像和维护成本；Omubot 应把语义检索做成 optional，而不是默认依赖。
- Koishi / NoneBot2 的框架生态强，但它们不替代 Omubot 的 PluginBus。NoneBot Matcher 更擅长命令/事件，Omubot PluginBus 更贴合 LLM 的 `on_message -> on_pre_prompt -> tools -> on_post_reply -> on_tick` 生命周期。
- NapCatQQ 仍是 Omubot 当前最合理默认协议端；LLOneBot 可以列入 fallback 检查表，Lagrange.Core 因 archived 状态不适合做首选。

## Omubot 基线

Omubot 结构证据：

- `docs/project-info.md:34-45` 记录 `QQ ←→ NapCat ←→ NoneBot2` 以及 Kernel/Services/Plugins 三层。
- `docs/architecture.md:118-142` 描述 `PluginBus` 生命周期、依赖拓扑排序、PromptBlock、Tool 和 AdminRoute 收集。
- `kernel/bus.py:37-210` 实现插件注册、生命周期、`fire_on_message`、`fire_on_pre_prompt`、`collect_tools`、`collect_admin_routes`、`on_tick`。
- `kernel/types.py:44-66` 定义 `Tool`，`kernel/types.py:118-250` 定义 `PromptBlock` 与 `PromptContext.add_block()`，`kernel/types.py:411-442` 定义插件钩子和工具注册契约。
- `services/slang/store.py:36-157` 建表包括 `slang_terms`、`slang_observations`、`slang_pending_candidates`、`slang_extraction_runs`、`slang_term_revisions`、`slang_drift_reviews`。
- `plugins/slang/plugin.py:42-77` 定义 `SlangLookupTool`，`plugins/slang/plugin.py:141-249` 接入 `on_message`、`on_tick`、每日 AI 复核、`on_pre_prompt` 注入。

Omubot 基线评价：

- 功能：群聊自然对话、主动插话、记忆、人设、情感、表情、日程、日志、配置、黑话治理、Admin Web 都已在本仓库内闭环。
- 性能：默认依赖中没有 FAISS、向量数据库、Pandas/PyArrow 这类重量项；SQLite + async 服务足以支撑当前规模。
- 架构：分层清楚，适合迭代；短板是 Provider 抽象、插件热重载/市场、跨平台能力和大规模插件隔离不如成熟平台。

## 分层对比

### AI / LLM 成品

| 项目 | 强项 | 相比 Omubot 的弱项 | 借鉴建议 |
| --- | --- | --- | --- |
| MaiBot | 拟人目标明确，表达学习和 jargon miner 激进 | 依赖更重，自动学习治理弱，核心与行为更耦合 | 借鉴黑话“含义推断 + 常识对比”思路，但保留 Omubot 审核/修订/漂移 |
| MoFox-Core | 图记忆、向量/embedding 方向强 | 依赖重，项目规模小，默认上图记忆会推高运维成本 | 语义记忆只做 optional extra |
| AstrBot | Provider 管理、插件热 reload、管线化、SQLite PRAGMA 调优强 | 平台面大，默认复杂度高 | 借鉴 Provider/session 分离、插件生命周期、DB 调优 |
| LangBot | 生产级平台、插件隔离、迁移、RAG/vector 后端强 | 对 Omubot 当前目标过重 | 只在第三方插件不可信时借鉴进程隔离 |
| kirara-ai | 工作流、IOC、多适配器、插件安装成熟 | 更像通用自动化平台，不是 QQ 陪伴定制 | 借鉴 workflow/block 可视化思路，不要重构主线 |

### QQ bot / 插件生态

| 项目 | 强项 | 相比 Omubot 的弱项 | 借鉴建议 |
| --- | --- | --- | --- |
| zhenxun_bot | NoneBot2 插件管理、权限、限流、调度、LLM manager | LLM 陪伴、人设/黑话治理不是核心 | 借鉴插件权限、限流、商店/更新管理 |
| Yunzai / Miao-Yunzai | JS 插件生态大，规则优先级清晰，社区内容多 | Redis/Puppeteer 重，LLM-native 能力弱 | 借鉴插件优先级和热刷新，不照搬运行栈 |
| HoshinoBot | 简洁、历史稳定、服务开关清楚 | nonebot old/aiocqhttp 路线老 | 仅作“简单服务开关”历史参考 |
| NcatBot | NapCat SDK 直接，事件总线和插件模板完整 | 不是 AI 成品，GitHub 仓库不可用 | 可参考 SDK 事件处理，不替代 NoneBot2 基座 |

### 框架与协议层

| 项目 | 定位 | 对 Omubot 的意义 |
| --- | --- | --- |
| NoneBot2 | Python 异步 bot 框架 | 保留作为底座；Omubot PluginBus 不应被 Matcher 完全取代 |
| adapter-onebot | NoneBot OneBot 适配器 | 当前路线适配良好，继续依赖 |
| Koishi | TypeScript 多平台框架 | 借鉴插件生态、配置/数据库抽象，不切栈 |
| NapCatQQ | 当前主流 NTQQ 协议端 | 继续作为默认协议端 |
| Lagrange.Core | C# NTQQ 实现 | archived，放入 fallback 研究，不做首选 |
| LLOneBot | NTQQ plugin-side bridge | 可作为 NapCat fallback 候选 |
| go-cqhttp | legacy OneBot 实现 | API 覆盖可查，但不是现代默认 |
| Ariadne | Graia/Mirai framework | 历史框架参考，当前 Omubot 不建议迁移 |

## 项目代码证据

### MaiBot

- `pyproject.toml:10-23` 引入 `faiss-cpu`、`jieba`、`peewee`、`pyarrow` 等重依赖，说明其语义/数据层默认更重。
- `src/plugin_system/base/base_plugin.py:15-76` 定义 Action、Command、EventHandler、Tool 类组件式插件模型。
- `src/plugin_system/core/plugin_manager.py:19-150` 负责插件 manifest、版本兼容、模块加载和组件注册。
- `src/chat/message_receive/bot.py:225-335` 体现消息预处理、禁用检查、chat stream、命令处理、事件 hook、heartflow 的主路径。
- `src/bw_learner/jargon_miner.py:76-139` 通过 LLM prompt 推断黑话含义并做常识对比。
- `src/bw_learner/jargon_miner.py:142-178` 使用出现次数阈值触发推断；`src/bw_learner/jargon_miner.py:451-614` 合并上下文、群/全局策略与异步推断。

判断：MaiBot 比 Omubot 更偏“自动成长的拟人生命体”。Omubot 若照搬自动黑话学习，会破坏现在 Slang v3 的治理优势；更适合借鉴“群语境与常识语义差异对比”的抽取思路。

### MaiBot-Napcat-Adapter

- `main.py:16-51` 使用 `asyncio.Queue` 对 NapCat 消息入队并分发 message/meta/notice。
- `src/response_pool.py:26-43` 使用 `echo` 映射响应并清理超时响应。
- `src/mmc_com_layer.py:1-19` 接入 `maim_message.Router` 和发送 handler。
- `README.md:29` 展示 NapCat -> Adapter -> Queue -> Handler -> MaiBot service 的消息流，`README.md:74` 明确用 echo/uuid 保证顺序。

判断：这是专用 adapter，不应与 Omubot 成品比功能；但其 echo/uuid 和队列拆分可作为 Omubot 协议故障排查参考。

### MoFox-Core

- `requirements.lock` 包含 `faiss-cpu`、`rjieba`、`apscheduler` 等，默认栈比 Omubot 重。
- `src/memory_graph/manager.py` 管理图记忆、向量存储、embedding 生成。
- `src/memory_graph/plugin_tools/memory_plugin_tools.py` 暴露记忆创建/查询工具，但存在部分工具未完全暴露给 LLM 的实现痕迹。

判断：MoFox 在图记忆方向领先，但 Omubot 当前更需要“轻量可治理”。建议把图记忆/embedding 作为 v3.5+ optional，不进入默认 Docker。

### AstrBot

- `pyproject.toml` 包含 `faiss-cpu`、`jieba`、`aiosqlite`、`apscheduler`，AI 平台能力强但依赖更重。
- `astrbot/core/pipeline/scheduler.py:17-96` 使用 ordered pipeline stage 与 async generator onion model。
- `astrbot/core/star/star_manager.py:177-220` 支持插件管理、watchfiles 热 reload、依赖安装/恢复。
- `astrbot/core/provider/manager.py:31-220` 管理 LLM/STT/TTS/embedding/rerank provider，并支持 session 级选择。
- `astrbot/core/db/sqlite.py:43-65` 设置 SQLite WAL/PRAGMA，值得 Omubot 借鉴。

判断：AstrBot 是最值得借鉴的“平台化但仍偏 bot”的项目。Omubot 下一阶段可借鉴 Provider 抽象、插件生命周期和 SQLite 调优，而不是整体重构为 AstrBot 风格。

### LangBot

- `AGENTS.md:7-21` 明确模块分为 platform/provider/pipeline/api/plugin/frontend。
- `AGENTS.md:53-61` 描述插件 runtime 独立进程，走 stdio/websocket。
- `AGENTS.md:73` 说明使用 Alembic 迁移，支持 SQLite/PostgreSQL。
- `src/langbot/pkg/vector/mgr.py` 与 `src/langbot/pkg/vector/vdbs/` 支持 Chroma、Qdrant、SeekDB、Milvus、pgvector 等后端。
- `src/langbot/pkg/platform/sources/` 覆盖 Satori、aiocqhttp、Telegram、Slack、WeCom 等多平台来源。

判断：LangBot 在“生产平台”维度强过 Omubot，但也明显超出 Omubot 当前定位。适合借鉴插件隔离、迁移和 Provider API，不适合默认引入多向量后端。

### kirara-ai

- `kirara_ai/entry.py` 组装 IOC container、memory manager、workflow registry、LLM manager、WebServer。
- `kirara_ai/im/manager.py:19-197` 管理多 IM adapter 的注册、启动和停止。
- `kirara_ai/plugin_manager/plugin_loader.py:18-220` 支持内部/外部插件发现、entry points、pip 安装插件。
- `kirara_ai/memory/memory_manager.py` 支持 memory scope 与 File/Redis 持久化。
- `kirara_ai/workflow/core/` 与 `kirara_ai/workflow/implementations/` 提供 workflow/block 扩展体系。

判断：kirara-ai 更像“可编排 AI 工作流机器人”。Omubot 可借鉴 Web 端可视配置/工作流思想，但当前不应把聊天主路径改成通用工作流引擎。

### zhenxun_bot

- `zhenxun/cli.py:94-112` 初始化 NoneBot、加载 OneBot V11 adapter、加载内置/外部插件。
- `zhenxun/configs/utils/models.py` 定义插件限制、冷却、次数、调度元数据。
- `zhenxun/services/scheduler/` 提供独立调度服务，`zhenxun/services/memory_governor.py` 管理内存/缓存清理。
- `zhenxun/services/llm/manager.py` 与 `zhenxun/builtin_plugins/llm_manager/` 说明 LLM 能力被作为服务/插件管理。
- `tests/builtin_plugins/plugin_store/` 有插件商店相关测试。

判断：zhenxun_bot 在 QQ 命令 bot 的运维治理、插件商店、权限限流上比 Omubot成熟。Omubot 可借鉴权限/限流/插件商店，不需要照搬其命令 bot 形态。

### Yunzai

- `lib/bot.js` 负责 adapter、消息事件、插件加载、Redis 退出清理。
- `lib/plugins/loader.js` 体现 priority 排序的插件执行管线。
- `plugins/system/add.js` 支持群/全局自定义回复。
- 项目常见运行依赖包括 Redis、Puppeteer，社区插件生态成熟但运维更重。

判断：Yunzai 系生态的长处是插件内容和命令生态，不是 LLM 陪伴主路径。Omubot 可借鉴规则优先级与插件刷新，但不要引入 Redis/Puppeteer 作为默认必要项。

### Miao-Yunzai

- `lib/plugins/plugin.js` 定义 plugin class，包含事件、优先级、规则和 handler。
- `lib/plugins/loader.js` 处理 priority、事件过滤、Redis 统计和热刷新。
- 与 Yunzai 一样，运行栈偏命令/内容插件，LLM 不是系统主轴。

判断：适合作为“规则插件管线”参考，不适合作为 Omubot 架构目标。

### HoshinoBot

- `hoshino/__init__.py` 使用旧 NoneBot / aiocqhttp 路线。
- `hoshino/service.py` 定义 Service 抽象、触发器、群启停和 scheduler。
- `hoshino/service.py` 的 group enable/disable 思路简单直接，适合作为 Omubot 插件开关 UI 的参考。

判断：HoshinoBot 简单稳定，但协议和框架路线偏旧；只保留历史参考价值。

### NcatBot

- PyPI `PKG-INFO:2-3` 标记 `Name: ncatbot`、`Version: 4.4.1.post1`，`PKG-INFO:8-14` 标记 MIT license。
- `ncatbot/core/client.py:81-100` 创建 `BotClient`、Adapter、event handlers、BotAPI。
- `ncatbot/core/client.py:137-151` 将 NapCat 上报事件转为 handler task。
- `ncatbot/core/adapter/adapter.py:50-83` 通过 WebSocket 发送 OneBot action 并用 echo 等待响应。
- `ncatbot/plugin_system/base_plugin.py:29-79` 定义插件元数据、生命周期、工作目录和 RBAC。
- `ncatbot/plugin_system/event/event_bus.py:33-80` 定义 EventBus 订阅、优先级和 timeout。

判断：NcatBot 是 NapCat SDK/框架，不是完整 LLM bot。若 Omubot 未来考虑脱离 NoneBot，可参考它的轻 SDK 方式；现阶段不建议替换 NoneBot2。

### NoneBot2

- `nonebot/plugin/load.py:29-158` 支持从模块、路径、JSON/TOML 加载插件。
- `nonebot/internal/matcher/matcher.py:138-220` 定义 Matcher 的 priority、block、temp、dependency injection 参数。
- 框架提供 Driver/Adapter/Matcher 成熟抽象，Omubot 已合理复用。

判断：NoneBot2 是 Omubot 正确的底座。Omubot 自研 PluginBus 不是重复造轮子，而是为 LLM Prompt/Tool/定时生命周期补了一层更贴合业务的插件契约。

### adapter-onebot

- `nonebot/adapters/onebot/v11/adapter.py:56-218` 支持 HTTP server、WebSocket server、reverse WebSocket client。
- 代码包含 API result store 与 timeout 逻辑，适合 Omubot 当前 NapCat/OneBot 连接方式。
- V11/V12 adapter 与 NoneBot2 生态绑定紧密，迁移成本低。

判断：继续使用。若协议端更换为 LLOneBot，只要 OneBot V11 行为兼容，Omubot 上层无需大改。

### Koishi

- `packages/core/src/context.ts` 是 Koishi Context / 插件能力核心。
- `packages/core/src/middleware.ts` 实现 middleware 处理链。
- `packages/core/src/bot.ts` 与 `packages/core/src/database.ts` 抽象 bot 和数据库能力。
- `packages/loader/src/shared.ts` 体现插件配置 reload 和 loader 共享逻辑。

判断：Koishi 的插件生态和控制台设计值得研究，但技术栈与 Omubot 完全不同。借鉴思想，不迁移平台。

### NapCatQQ

- `packages/napcat-onebot/event/OneBotEvent.ts` 定义 OneBot event 类型。
- `packages/napcat-onebot/config/config.ts` 管理多种网络模式与 `messagePostFormat` 默认行为。
- `packages/napcat-onebot/action/go-cqhttp/GetGroupMsgHistory.ts` 实现群历史消息动作，Omubot 历史加载依赖这一类能力。
- `packages/napcat-core/.../NodeIKernelMsgListener.ts` 连接 NTQQ 内核消息监听。

判断：NapCatQQ 是当前最合适默认协议端。Omubot 应继续围绕 NapCat 做健康检查和可观测性，而不是频繁切协议端。

### Lagrange.Core

- `Lagrange.Core/Event/EventInvoker.Events.cs` 提供 typed events。
- `Lagrange.OneBot/Extensions/HostApplicationBuilderExtension.cs:79-134` 注册 OneBot signer、正反向 WebSocket、message service。
- `Lagrange.Core/Message/MessageChain.cs` 与 `MessagePacker.cs` 展示 typed message chain 与 protobuf packing。
- GitHub metadata 显示 archived，长期维护风险高。

判断：工程质量和类型系统不错，但 archived 状态决定它只能作为 fallback 研究，不适合 Omubot 主线。

### LLOneBot

- `src/main/main.ts:66-94` 接入 NTQQ API，并提供 OneBot11、Satori、Milky adapters。
- `src/main/store.ts` 管理消息缓存与短 ID 映射。
- `src/ntqqapi/core.ts` 处理 `nt/message-created`、offline、deleted、sent 等事件。
- `src/satori/server.ts` 提供 Satori server 与 OneBot bridge。

判断：LLOneBot 是 NapCat 的现实 fallback 候选。Omubot 后续可做协议兼容 checklist，而不是现在直接切换。

### go-cqhttp

- `server/websocket.go` 支持正向/反向 WebSocket。
- `pkg/onebot/supported.go` 列出大量 OneBot action，包括群文件、群管理、转发消息、历史消息等。
- `coolq/api.go` 保留广泛 CQ API 兼容。

判断：API 覆盖是参考资料，但 QQ 协议路线老。Omubot 不建议回退到 go-cqhttp 作为默认。

### Ariadne

- `src/graia/ariadne/app.py:221-266` 将远端事件转入上下文、缓存消息，并 `broadcast.postEvent(event)`。
- `src/graia/ariadne/service.py:74-88` 构造 Broadcast 并设置 dispatcher。
- `src/graia/ariadne/app.py:1218-1231` 支持注册 command。
- `src/graia/ariadne/entry/scheduler.py` 接入 GraiaScheduler。

判断：Ariadne / Graia 体系优雅，但 Mirai API HTTP 路线对当前 Omubot 的 NapCat/NoneBot2 基座意义有限。

## Omubot 强项

- 分层清楚：协议基础交给 NoneBot2/NapCat，业务生命周期由 Omubot PluginBus 接管，避免直接把 LLM 业务塞进 NoneBot Matcher。
- 默认轻量：依赖没有默认 FAISS、向量数据库、Pandas/PyArrow；适合 Docker 常驻和小团队维护。
- 运营闭环好：Admin Web 已覆盖 dashboard、配置、日志、系统、记忆、人设、表情、黑话等实际管理面。
- 群语境治理强：Slang v3 的候选、AI 通过、人工确认、revision、drift review、lookup tool，比 MaiBot 的自动 jargon 学习更可审计。
- QQ 群陪伴目标聚焦：主动插话、日程、表情、情感、关系、人设等都围绕单一 QQ bot 产品，不被多平台平台化需求稀释。

## Omubot 短板

- Provider 抽象不如 AstrBot / LangBot：多模型、多 provider、STT/TTS/embedding/rerank 统一管理还不够系统。
- 插件生态不如 NoneBot / Koishi / zhenxun：缺插件市场、热加载、版本兼容、依赖恢复和权限限流体系。
- 语义记忆不如 MoFox / LangBot / AstrBot：当前记忆更偏 typed cards 和时间线，缺图记忆/向量检索的可选增强。
- 协议端可替换性不足：目前主路径绑定 NapCat + OneBot V11，缺 LLOneBot/Lagrange fallback 验收矩阵。
- 测试/CI/迁移成熟度不如大项目：Slang 已有较多结构，但整体迁移策略和端到端测试还可以增强。

## 可借鉴设计

- AstrBot：ProviderManager/session 级 provider 选择、插件热 reload、SQLite WAL/PRAGMA 调优。
- LangBot：Alembic 风格迁移、插件隔离 runtime、平台 API 分层；只在 Omubot 发展到第三方插件生态后引入。
- MaiBot：黑话含义推断 + 与普通语义对比的提示词策略；保留 Omubot 审核治理，不自动污染 Prompt。
- zhenxun_bot：插件权限、冷却、次数限制、插件商店和自动更新的治理经验。
- Koishi：插件配置、控制台体验、数据库抽象和 middleware 生态设计。
- NapCat / LLOneBot：整理协议兼容 checklist，包括历史消息、图片/语音、戳一戳、群管理、转发、message_id 映射、心跳和重连。

## 不建议照搬

- 不建议默认引入 FAISS / embedding / 多向量数据库。它们会直接增加镜像大小、冷启动、依赖冲突和维护成本。
- 不建议把 Omubot 改成通用多平台 IM 平台。当前产品力来自“QQ 群陪伴”的聚焦。
- 不建议让黑话自动通过后缺少人工治理。AI 通过可以执行等价，但必须继续保留来源、证据、否决、漂移与修订历史。
- 不建议切到 JS/Koishi/Yunzai 栈。生态强，但迁移成本和现有 Python 服务积累不匹配。
- 不建议将协议端与业务强耦合。协议层应该只提供 OneBot 能力，业务层继续通过服务和插件封装。

## 下一阶段路线建议

1. Provider 抽象小步增强：从 AstrBot/LangBot 借鉴 provider registry、session provider selection、能力声明，不一次性接入 STT/TTS/embedding 全家桶。
2. 插件治理增强：为 PluginBus 增加插件 manifest 校验、版本兼容、权限/限流、后台启停和健康状态；优先借鉴 zhenxun，不做进程隔离。
3. Slang 继续走治理路线：补充黑话导入/导出、漂移批量处理、证据对比 UI、Prompt 注入评估，不改成 MaiBot 式全自动。
4. 语义检索 optional 化：设计 `SimilarityProvider` 接口，默认 ngram；embedding/FAISS 作为可选 extra，默认 Docker 不安装。
5. 协议兼容矩阵：保持 NapCat 默认，同时为 LLOneBot 建最小验收脚本和文档清单，避免协议端故障时临时摸黑。
6. 运维性能：借鉴 AstrBot SQLite PRAGMA，审查 Omubot SQLite 连接、WAL、索引和 Admin 列表分页。

## 风险与不确定性

- 外部仓库是浅克隆快照，只代表 2026-05-07 附近代码状态；后续 upstream 可能变化。
- 本轮没有运行外部项目，也没有安装依赖，因此性能评价是静态结构推断，不是压测结论。
- NcatBot GitHub 仓库不可访问，本轮使用 PyPI sdist；它的仓库历史、issue 状态和最新开发分支未能审计。
- Stars/forks 只代表生态规模，不代表代码质量；本报告的架构判断优先看本地代码证据。
