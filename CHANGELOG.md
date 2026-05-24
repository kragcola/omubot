# Changelog

All notable changes to Omubot are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.5.0] — 2026-05-24

聚合 v1.2.5 至今（2026-05-05 → 2026-05-24）维护日志，覆盖此前未发版的 v1.3 / v1.4 区间。版本号每条「涉及插件修改」的维护日志条目对应 +0.0.1。

### Added

#### 三层架构（kernel / services / admin）

- **Plugin 目录化**：插件从单文件 `.py` 全面迁移到 `plugins/<name>/` 目录 + `plugin.json`（manifest_version=3）+ `config.default.json` + `config.schema.json`，统一 `manifest / display_name / capabilities / permissions / restart_required_fields` 契约；新增 `plugin.sig` 签名预留与 marketplace_id 槽位
- **PluginBus 生命周期 hooks**：`on_startup` / `on_shutdown` / `on_bot_connect` / `on_message` / `on_pre_prompt` / `on_post_reply` / `on_tick`，钩子级耗时与异常聚合到 admin 健康面板
- **Kahn 拓扑排序 + 循环依赖回退**：插件加载顺序按 `provides → consumes` 拓扑展开，循环依赖时回退到 manifest priority
- **PromptBlock**：`static / stable / dynamic` 三段式 prompt 注入；插件可注册 block 而非自己拼字符串
- **LLMRequest spine**（Phase A→D）：所有 LLM 调用收敛到统一 `LLMRequest` 契约，含 per-task cache profile / 多 provider 路由 / SSE 流解析；阶段 D-now 把 main / compact / thinker / slang_review / style / episode 全部迁过去
- **多层学习记忆 Phase C-E**：`MemoryConsolidator` dry-run + admin 队列；Episode 候选 → approved → enabled_for_prompt promote 桥；reflection 路径（style_feedback / expressions / slang_drift 三源）；EpisodeProvider 召回路径双写 BlockTraceBus；5 条 graph edge 双写（`term_used_in_group` / `style_applies_to_situation` / `user_corrected_bot_about` / `doc_supports_fact` / `episode_supports_profile`）
- **Knowledge 系统治本 PR1-PR6**：RRF 跨源融合 + ContextRetriever Protocol + 软指令兜底；token 多桶预算替换字符截断；Thinker `retrieve_mode` 四档分路（删 search action）；启动顺序 / admin 链路 / skip metrics 三处漏点；query 重写 / decontextualization；prompt-injection guard（`<context_data>` 包裹 + 安全前导 + 人设强化）；graph 链路可观测性（pack/extractor INFO + reject 节流 + health API）
- **Knowledge 图谱抽取治本 PR1-PR2**：关闭 regex 自动抽取 + 清 75 条垃圾候选；LLM 抽取器 + 拆 0.85 快车道 + 重开 graph_auto_extract
- **DeepSeek 静态前缀加固**（方案 D + E）：thinker / slang_review / slang / slang_drift / slang_semantic 五条链路前缀加固跨过 1024-token 缓存门槛
- **黑话系统 v3**：群内黑话 v1/v2/v2.5 → v3 质量治理；黑话 alias key 与缓冲 correctness；全局词封闭群选项；语义漂移误报门控；ConversationArchive 黑话/表达 cursor 迁移
- **多层学习记忆 Phase D 候选 admin UX**：5 域筛选 + episode payload 编辑 + 修订审计
- **`/learning` 学习管线**（v1 → v2.1 → v3 fold-in）：
  - PR1-PR6：只读 pipeline + 执行追踪、BudgetManager 改吃 candidate、style/episode accepted-only observations、items 列表 + LearningTable、extract-all + 轻量审核抽屉、style runner + 入口深链
  - L1-L4 收口：slang accepted-only prompt observation、memory card_id 深链定位、trimmed prompt block 计入 hits、extract-all run_id 进度查询
  - v2.1：F1-F5 修订 + L1-L4 折入 + 7 PR 收口上线
  - v3 fold-in PR-A/B/C/D：三槽骨架（NounToolbar / SidePanel / DrawerHost）、slang / style / episode / memory 折入新管道、5 路由 redirect + SideMenu 收敛 + 旧页面退场
  - 词条主切换轴 NounSwitcher v0 → v1（Header Tabs，76px 双段式 tab，与 Stage eyebrow 同语汇）
- **Persona Source Importer Part A** — `services/persona/` parser/builder/writer/CLI/LLM extractor；source.md 一份 → 15 个 `.draft/*.yaml` partial skeleton + `_import_report.json`；Pending Freeze 复制到 `_pending_freeze/`；新增 `/api/admin/persona/import` / `/draft/{id}` / `/freeze/{id}` / `/source/{id}`；`persona_import` LLMTask + cache profile + admin provider task 同步
- **Persona Part B dry-run 闭环**：`services/system_module/` 27 + 7 模块 catalog / models / DAG validator / RuntimeStateBus skeleton；`config/persona/_defaults/v2/` 9 份默认模板；`services/persona/system_validation.py` 接入 `_import_report.json`；`services/persona/compiler.py` `--compile-dry-run` 输出 core prompt block 草案 + module order
- **Admin SPA 阶段 0 → 5.2**：Vue 3 + TypeScript + Naive UI + Vite manualChunks（vendor-vue / vendor-icons 拆分）；Jinja 模板退役（一刀切）；阶段 3 长尾 6 视图（BlockTrace / Episodes / MemoryConsolidator / Style / Scheduler / Sandbox / Schedule / MemosView / CrossGroup）AppPanelSection 收敛；阶段 4 月度合规脚本 `scripts/check-ui-compliance.sh`；DashboardView 信息密度 + 「今日学习收录」 + 右侧竖版日程时间线；LogsView 二轮重设计 + LogPanel；admin 静态资源缓存策略分流；admin SSE 群活动实时推送；hero 改随主体滚动；全局滚动 reset.css 修复
- **Admin Persona Importer SPA 首版**：4 MetricCard + PageToolbar + 双栏 source/issues 视图 + sourceDirtySinceImport gate + Pending Freeze NPopconfirm 二次确认
- **storage 切 docker named volume**（Phase 3 A/B）：`storage/` 从 bind mount 切到 docker named volume，避开 macOS 共享盘 sqlite 写入异常
- **slang.db 损坏全栈治本 Phase 1-3**：`close_with_checkpoint` + 主机脚本守卫；DELETE journal + 完整性巡检 + admin 接线；storage 切 docker named volume；附 `external: true` 中途修复
- **备份体系 Phase 1-4**：`services/backup/` 全库每日备份 + 服务层接线 + admin 入口
- **Codex 协同工作流**：`.claude/handoff/*.md` 流程；codex 切回原生 DeepSeek（弃用 CPA / codeseeq 路径）；CPA fixup sidecar 处理 DeepSeek thinking 多轮 400
- **DeepSeek V4 原生模式接入** + 本机 CPA / Codex profile 锁定修复 + 1M 上下文利用率提升
- **Phase 1-8 治理 / 生态 / 韧性**：服务级健康聚合、关键错误聚合、健康告警降噪、维护窗口提示、配置回滚向导 + 基础备份、配置变更审计 + 保存前 diff、profile 热切换、分 profile rate limit、群 Profile 工具矩阵 + 屏蔽用户编辑 + 策略审计历史、未加载包治理队列、`plugin.sig` 签名 + 来源校验预留、mock 协议测试、历史连接记录、Phase 5 质量守卫 + 系统可视化、Phase 7 本地插件生态
- **Context Knowledge System P1/P2** + 评测闸门 + ContextPlugin 接管前评测守卫 + 主人场景上下文评测扩充 + Prompt Pack 长度预算
- **知识库 Web 治理台**（admin/routes/knowledge.py） + 导入指导文档 + 图谱加载失败审计

### Changed

- **CHANGELOG 收敛策略**：v1.2.5 之后未发布的 v1.3 / v1.4 中间版本合并到本版本，`pyproject.toml` 直跳 1.4.0 → 1.5.0
- **维护日志治理纪律**：D1 同模式扫描 / D2 cancel-path 测试 / D3 重构带迁移清单 / D4 完成声明含证据 / D5 pytest 防孤儿 / D6 admin SPA 同步路径 / D7 部署前 git hygiene 七条入 CLAUDE.md
- **Skill 自动触发**：`omubot-admin-console` skill 接管 admin/frontend / admin/routes / docs/tracking / maintenance-log 任意改动
- **Docker 镜像/缓存治理**：dev 主机 65.99 GB → 2.241 GB（含 4.477 GB volume）；`docker compose up bot --build` 反复触发后的 146 张 dangling 镜像清理；compose 给 bot 服务加 `mem_limit: 2g` / `mem_reservation: 512m` 防御红线（napcat 不动 — D6 反风控）
- **bot 配置中心化**：6 个插件配置从中央 `config.toml` 迁移至 `plugins/<name>.toml`；Config 模型从 `kernel/config.py` 搬至各插件
- **session-start hook 重构**：外置脚本 + 维护日志索引 + 修 cwd 路径 bug
- Bot 版本 1.2.5 → 1.5.0
- 插件总数：v1.2.5 18 → v1.5.0 23（新增 calendar_context / context / knowledge / slang / sticker / style 等目录化产物）

### Plugin version bumps（每条维护日志条目 +0.0.1）

| Plugin           | v1.2.5 → v1.5.0       | 涉及条目数 |
| ---------------- | --------------------- | ---------- |
| chat             | 1.1.7 → 1.1.25        | 18         |
| slang            | 0.1.0 → 0.1.17        | 17         |
| context          | 0.1.0 → 0.1.9         | 9          |
| knowledge        | 0.1.1 → 0.1.5         | 4          |
| schedule         | 1.1.2 → 1.1.5         | 3          |
| memo             | 1.1.3 → 1.1.5         | 2          |
| sticker          | 1.1.4 → 1.1.6         | 2          |
| style            | (新增 5-21) → 1.0.0   | 2          |
| bilibili         | 1.1.3 → 1.1.4         | 1          |
| calendar_context | (新增 5-21) → 1.0.0   | 1          |
| element_detector | 1.1.2 → 1.1.3         | 1          |
| history_loader   | 1.1.1 → 1.1.2         | 1          |
| vision           | 1.1.1 → 1.1.2         | 1          |

未触及的插件保持原版本：`affection 1.1.2` / `datetime 1.1.1` / `debug_commands 1.3.1` / `dream 1.1.3` / `echo 1.1.2` / `food 0.1.6` / `group_admin 1.1.1` / `http_api 1.1.1` / `web_fetch 1.1.1` / `web_search 1.1.1`

### Fixed

- **表情包静默学习回路 5-21 之后失活**：2026-05-21 恢复提交 3477163 把 `services/media/sticker_capture.py` helpers 装回 main，但 `StickerPlugin.on_message` + `HistoryLoaderPlugin.learn_new_stickers` 两条调用路径没恢复，全仓 grep helper 名 → 0 调用方；从 `wip/stash-1-pre-erase` 把 silent_learn 实捕（`stolen_silent_learn`）和历史回放（`history_loader_sticker_learn`）两条路径恢复到 main，加 `silent_safe = True` 旁路新护栏
- **silent_learn 模式被 element_detector 击穿**：emergency hotfix
- **黑话抽取 run 永远卡 running、计数全 0**：CancelledError 收尾漏洞
- **AI 清池死循环**：backlog reviewer 缺 slot 幂等闸门，1075 条永远积压修复
- **黑话复核失败回待审** + AI 复核性能修复 + alias key 与缓冲 correctness
- **B站 JSON 卡片多 URL 遍历** + 无 scheme URL 归一化（v1.2.4 已修；本版本未触发新 bug）
- **stage→noun-mode 跨 4 noun 全栈映射对齐**（5 处 mismatch）：slang review→`pending_human_review` + archived→`archived_only`；episode candidate / review / approved 三处错位修正
- **noun=memory 数据源修正** + inventory stage 默认 date=all
- **fold-in slang 槽双轴根除**：embedded 模式抑制 SummaryBar / QueueToolbar 子 tab
- **信息速递不再截断 title** + 6 处布局/视觉补强（pill chip / flex 布局 / 注解填空 / 群号去省略号 / .feed-tail 包裹 / min-width:0）
- **admin SPA 全局滚动**：reset.css `html, body, #app, .n-config-provider { height:100% }` 链路修复
- **stash 全量恢复**：5 天 in-progress 工作 3-way merge 回 Phase 1+2 主线
- **Docker 磁盘事故恢复** + 拉起修复（stale bind-mount 重建 + 前端 rebuild + napcat 重新扫码）
- **工作区恢复**：从网络共享盘 omubot-critical-backup 恢复 config / napcat / storage
- **deploy fix**：pyproject 补 rapidfuzz 依赖；admin SSE 群活动实时推送 + group access refactor 收尾
- **全量 pytest 退出挂住** （aiosqlite 资源收尾）+ 7 项预存测试失败清理（975 passed / 0 failed）+ ruff E501 26 条 pre-existing 清理

### Removed

- **Jinja2 模板**：admin/templates 全量退役，全部走 Vue 3 SPA + Vite 构建到 admin/static
- **5 条旧 admin 路由**：v3 fold-in 后 `/slang` / `/memory-consolidator` / `/style` / `/episodes` / `/cross-group` 全部 redirect 收敛到 `/learning?noun=...&stage=...`
- **CPA / codeseeq DeepSeek 中间层**：codex 切回原生 DeepSeek API
- **`MemoryConsolidator` 旧搜索 action**：被 PR5 retrieve_mode 四档分路替代

[1.5.0]: https://github.com/kragcola/omubot/releases/tag/v1.5.0

## [1.2.5] — 2026-05-05

### Added

- **FoodPlugin**：`/吃什么` 食物推荐命令，含 1094 条本地食物库（16 品类 × 56 品牌），支持时段匹配、品牌/口味排除、"不要麦当劳"等自然语言过滤。Web 搜索默认关闭，可通过 `/food search on|off` 切换。`/food like|dislike|location` 偏好管理
- **LLM Provider 抽象层**（`services/llm/provider.py`）：解耦 Anthropic / OpenAI Chat Completions API 格式差异，支持 build_request + parse_sse_stream 双方法。AnthropicProvider + OpenAIProvider 实现
- **Knowledge 检索服务**（`services/knowledge/`）：倒排索引全文检索，按 `##` 标题分块，支持中文二元组 + 英文词 tokenization
- **KnowledgePlugin**：加载 `docs/` 下 markdown 文档到倒排索引，`/knowledge <查询>` 命令检索
- **群记忆管理页**（`admin/routes/group_memory.py`）：Admin Dashboard 中浏览和编辑记忆卡片、群昵称配置
- **`thinking` 参数透传**：`call_api()` 和 `LLMClient._call()` 支持 `thinking` 参数，可禁用 DeepSeek 等模型的默认推理行为

### Changed

- Bot 版本 1.2.4 → 1.2.5
- FoodPlugin 0.1.0 → 0.1.4
- 插件数量 16 → 18

## [1.2.4] — 2026-05-04

### Fixed

- **B站 JSON 卡片多 URL 遍历**：QQ 小程序卡片同时包含 `url`（QQ 页面）和 `share_url`（b23.tv），旧代码只取第一个导致 BV 号解析失败。改为收集全部候选 URL 并逐个尝试解析
- **无 scheme URL 归一化**：`_resolve_urls_to_vid()` 对不含 `://` 的 URL 自动补 `https://` 前缀，确保 HTTP 重定向跟踪能够触发
- **JSON 原始数据日志截断**：从 500 字符扩展到 2000 字符，捕获完整字段

### Changed

- BilibiliPlugin 1.1.1 → 1.1.2

## [1.2.3] — 2026-05-03

### Fixed

- **B站搜索匹配**：`_title_match_score()` 新增前缀词身份匹配——关键词第一个有意义词不在候选标题中时得分 × 0.3，解决"萍儿→豹"误匹配
- **qqdocurl 跳转跟随**：`_resolve_urls_to_vid()` 新增通用 HTTP 重定向跟随，支持 qqdocurl 等非 b23.tv 的短链跳转
- **B站搜索置信度阈值**：搜索结果得分 < 0.3 时拒绝匹配，避免低质量结果

### Changed

- BilibiliPlugin 1.1.0 → 1.1.1

## [1.2.2] — 2026-05-03

### Added

- **Thinker 预回复思考**：`config.toml` 新增 `[thinker]` 段，回复前 Thinker LLM 预判 action/thought/sticker/tone，主 LLM 收到指令后生成回复
- **B站回复模式**：4 种 `reply_mode`（mood/always/dedicated/autonomous）接入调度器，支持视频兴趣评估驱动自主回复
- **B站兴趣关键词配置化**：`plugins/bilibili.toml` 支持三级关键词（high/medium/low）和 LLM 回退阈值，修改无需 rebuild
- **`/debug split` 子命令**：实时测试文本分段效果

### Fixed

- **Thinker image_ref 过滤**：Thinker 调用前剥离非 text 内容块，避免 `image_ref` 内部类型发往 Anthropic API 导致 400 错误
- **句中断行合并**：`\n` 从硬分段边界降级为软提示，仅句末标点后切分
- **文本分段重写**：替换三层函数叠加为单一 `_smart_chunk` 回溯式标点优先级切分，解决孤儿碎片问题

### Changed

- Bot 版本 1.2.1 → 1.2.2
- ChatPlugin 1.1.4 → 1.1.5
- BilibiliPlugin 1.0.0 → 1.1.0

### Fixed

- **文本分段算法重写**：替换三层函数叠加（`_split_on_sentence_end` + `_split_long_on_comma` + hard slice）为单一 `_smart_chunk` 回溯式标点优先级切分。扫描窗口从右向左找最佳断点：句末标点（。！？～…）→ 从句标点（，；：、）→ 字符边界（保护英文单词完整性）→ 硬切。解决 "AI修复" 被撕成 "AI"+"修复" 的孤儿碎片问题
- **段首标点修复**：`_smart_chunk` 将标点留在前段末尾（`t[:best]`），不再推到下一段开头
- **句尾从句标点剥离**：独立 QQ 消息末尾无意义连接符（"虽然我主要玩烤和邦邦，"）剥离从句标点，句末标点保留
- **`～` 升级为句末标点**：从仅用于 `\n` 合并判断升级为一级切分点，与 `。！？` 同级
- **`/debug split` 误输入保护**：纯 ASCII 小写首词检测，非已知子命令时提示可用命令而非送 LLM

### Added

- **`/debug split <文本>`** 子命令：实时测试 `_split_naturally()` 分段效果，别名 `/debug 分段`/`/debug 分割`
- 新增 5 个测试：段首无标点、英文完整性、尾段合并、精确回归、用户 case v2（共 13 个 split 测试）

### Changed

- `_MIN_CHUNK` 3 → 6，避免短片段逃脱合并逻辑
- `_MAX_CHUNK` 45 → 20（配合新算法更精确的断点选择）
- Bot 版本 1.2.0 → 1.2.1
- ChatPlugin 1.1.3 → 1.1.4

## [1.2.0] — 2026-05-03

### Fixed

- **句中断行合并**：`\n` 从硬分段边界降级为软提示。仅当上一行末尾有句末标点（。！？～…）」』））时才切分，句内换行直接合并
- **超长句语义切分**：硬字符切分替换为逗号层级语义切分，避免合并后完整句子被重新撕碎
- **指令更新**：分段指导从"换行即分段"改为"一个完整想法写完后再换行"

### Added

- `_SENTENCE_ENDING` 字符集用于 `\n` 合并判断
- `TestSplitNaturally` 测试类：9 个测试覆盖句中断行合并、句末切分、`---cut---`、长句语义切分、`_MIN_CHUNK` 合并

### Changed

- Bot 版本 1.1.1 → 1.2.0

## [1.1.0] — 2026-05-03

### Added

- **多级命令支持**：`Command.sub_commands` 字段，`CommandDispatcher` 递归匹配子命令，未命中回退父 handler
- **`/debug` 子命令**：`save`（别名: 保存/收录/添加表情）、`send`（别名: 发/发送）
- **B站视频链接识别插件**：识别 BV号/av号/b23.tv/番剧链接，注入视频摘要（含 Qwen VL 封面描述），本地缓存去重
- **B站回复模式**：4 种模式（mood/always/dedicated/autonomous），兴趣评估函数根据视频标题匹配 bot 人设关键词计算 0-1 兴趣分
- **心情系统 × 概率调度联动**：心情三维度（valence/energy/openness）计算 talk_value 乘数 [0.25, 2.0]
- **群聊延迟优化**：概率调度替代固定 debounce、移除独立 Thinker（~55 行）、合并 Sticker 强制执行（~23 行），延迟从 17-22s 降至 ~3-5s
- **调度器日志可见性**：5 处 skip 决策日志从 DEBUG 提升至 INFO
- **心情缓存修复**：`mood_getter` lambda 改为主动调用 `mood_engine.evaluate()`，修复重启后首次聊天心情乘数始终 1.0 的 bug
- **要素察觉启用**：`[element_detection]` 配置补全，修复 `identity_mgr`→`identity` 引用错误
- **NoneBot NICKNAME 配置**：补全 `config/.env` 的 `NICKNAME`，修复适配器层昵称剥离和 `to_me` 标记

### Changed

- **插件配置迁移**：6 个插件配置从中央 `config.toml` 迁移至 `plugins/<name>.toml`，Config 模型从 kernel/config.py 搬至各插件 .py 文件
- Bot 版本 1.0.7 → 1.1.0
- ChatPlugin 1.1.1 → 1.1.2

## [1.0.6] — 2026-05-02

### Fixed

- **mface（QQ 商城表情）检测**：`/debug 保存表情` 无法识别第四张 QQ 商城表情（如 `[星星眼]`）。新增 `market_face` 类型检测 + 从 `event.raw_message` 解析 `[mface:...]` CQ 码兜底，NoneBot 将其转为纯文本时仍可捕获
- **Qwen VL 连续调用限流**：多图保存时连续 vision API 调用触发硅基流动限流。图片间增加 1.5s 延迟，timeout 10s → 15s
- **历史加载触发正常回复**：Bot 重启后历史加载回填 `/debug` 消息进入 timeline，重新触发 thinker/chat 流程。`HistoryLoaderPlugin` 现跳过含 `/debug` 的消息

### Changed

- ChatPlugin 1.0.3 → 1.0.4
- HistoryLoaderPlugin 1.0.0 → 1.0.1
- VisionClient 错误日志增加异常类型

## [1.0.5] — 2026-05-02

### Changed

- **图像描述提升至系统层**：`VisionPlugin` 删除，`VisionClient` 移至 `services/media/vision.py`。由 `bot.py` 根据 `api_key` 是否填写自动启用，无需额外 `enabled` 开关
- **接入硅基流动 VLM**：配置 `Qwen/Qwen3-VL-30B-A3B-Instruct` 通过 SiliconFlow API 提供多模态描述，DeepSeek V4 不支持多模态
- **`/debug 保存表情` 图像描述**：保存时调用 Qwen VL 生成中文描述，替代硬编码占位符。支持用户自定义描述前缀
- **表情包评估日志**：回复日志新增 `sticker=sent`/`sticker=none` 字段
- **指令调度兼容前置文本**：`CommandDispatcher` 匹配 `/` 命令不再要求以斜杠开头，支持 `[表情] /debug` 等前置内容

### Added

- `config.example.toml` 新增 `[vision.qwen]` 示例段落

### Removed

- `QwenVLConfig.enabled` 字段：api_key 非空即启用，留空即关闭

### Changed (plugins)

- StickerPlugin 1.0.1 → 1.0.2

## [1.0.4] — 2026-05-02

### Fixed

- **表情包发送全线修复（critical）**：`SendStickerTool` 中 `subType`（驼峰）→ `sub_type`（蛇形），OneBot v11 协议要求蛇形命名，NapCat 静默忽略驼峰 key 导致所有表情包以普通图片发送
- **Docker 容器文件隔离**：bot 与 napcat 在不同容器，改为 base64 编码内联传输图片
- **`/debug` 指令触发 thinker**：命令匹配后调用 `scheduler.cancel_debounce()` 取消待处理的 thinker 触发
- **指令被复读插件检测**：`EchoPlugin` 跳过以 `/` 开头的消息

### Changed

- **`/debug` 直接调度**：新增 `_debug_direct_dispatch()` 绕过 LLM 直接执行已知命令（发表情等），避免 DeepSeek V4 幻觉不调用工具
- **表情包发送**：新增 `summary=[动画表情]` 字段使 QQ 正确渲染贴图样式
- ChatPlugin 1.0.2 → 1.0.3

## [1.0.3] — 2026-05-02

### Added

- **`/debug` 工具循环**：从单轮 `_call()` 改为完整 5 轮工具循环，LLM 可调用所有已注册工具
- **好感度插件 INFO 日志**：on_pre_prompt 记录好感度层级和分数，on_post_reply 记录互动后变化

### Fixed

- **分段逻辑根本修复**：`force_reply=True` 不再绕过 `_split_naturally()`，所有回复统一分段
- **消息分段增强**：硬字符上限强制切分、`---cut---` 逐行精确匹配、尾段合并仅对纯标点生效
- **日志频道默认值**：`LogChannelConfig.system` 默认改为 `True`
- **记忆卡片提取日志**：从 DEBUG 提升到 INFO

### Changed

- ChatPlugin 1.0.1 → 1.0.2
- AffectionPlugin 1.0.0 → 1.0.1
- MemoPlugin 1.0.0 → 1.0.1
- SchedulePlugin 1.0.0 → 1.0.1
- StickerPlugin 1.0.0 → 1.0.1

## [1.0.2] — 2026-05-02

### Added

- Command alias system: `Command.aliases` field, `CommandDispatcher` indexes by alias
- `/plugins` now accessible via `/p`, `/plg`, `/插件` (Chinese alias)
- All plugins signed with author "kragcola" (default in `AmadeusPlugin` base class)

### Fixed

- Scheduler: `proactive is None` no longer blocks @-mention responses — @ mentions always trigger a reply
- `force_reply` no longer strips mood/affection blocks for @ mentions (was conflating debug mode with mention-reply)
- Thinker now correctly fires for non-@ group messages (was blocked by scheduler proactive guard)

### Changed

- DebugCommands plugin upgraded to v1.1.0

## [1.0.0] — 2026-05-01

### Added

- Omubot 三层框架：Kernel（PluginBus）→ Services（LLC/Scheduler）→ Plugins（14 个）
- 核心聊天：消息路由、LLM 调用、Tool loop、Thinker 决策
- 14 个插件：Chat、DateTime、WebSearch、WebFetch、HttpApi、GroupAdmin、Vision、Sticker、Memo、Affection、Schedule、HistoryLoader、Dream、Echo、ElementDetector
- CommandDispatcher 服务层指令系统：/debug、/plugins、/version
- PluginBus 钩子驱动架构：on_startup、on_message、on_pre_prompt、on_post_reply、on_tick
- 插件自动发现：单文件 + 目录插件，plugin.json 侧车清单
- plugin_data_dir：插件私有数据目录（storage/plugins/），gitignored
- 上下文压缩（compact）：LLM 摘要 + 熔断器
- 记忆卡片系统（CardStore）：7 类 3 作用域，SQLite 持久化
- 多模态视觉：图片下载 → pyvips 缩放 → Anthropic API
- 表情包系统：SHA256 去重、LLM 可发送/保存、Dream Agent 整理
- 好感度系统：分数累加、昵称系统、Prompt 态度调节
- 模拟日程：每日 LLM 生成，结合真实日期与季节
- Dream Agent：定期记忆整理 + 表情包清理
- Admin 面板：用量统计、配置查看、Soul 在线编辑、日志查看
- LLM 用量追踪：SQLite 记录、TUI 查看、API 端点
- 群聊调度器：debounce + batch 双模式、@消息优先
- 单群配置覆盖：at_only、debounce、batch_size、blocked_users
- 群聊隐私遮掩：QQ 号脱敏
- 复读检测：5 分钟内同消息 3 次触发
- 管理员系统：超级管理员 + 群管理工具
- Docker Compose 部署：NapCat + Bot 双容器
- config/ gitignore 隔离：API key 不进入版本控制

[1.0.0]: https://github.com/kragcola/omubot/releases/tag/v1.0.0
