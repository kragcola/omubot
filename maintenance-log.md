# 维护日志

> 按时间倒序记录部署、配置变更、故障处理等运维事件。

---

## 2026-05-21 slang.db 反复损坏全栈治本 Phase 2 — DELETE journal + 完整性巡检 + admin 接线

**变更类型**：infra-soft / storage + admin（PRAGMA 调整 + 运行时巡检 + 全套 admin UI）

**背景**：

承接 Phase 1（fc7e591 之前的 3 个 commit：`close_with_checkpoint`、主机脚本守卫、UTF-8 修复），按 plan（`/Users/kragcola/.claude/plans/modular-forging-allen.md`）治本路径推进 Phase 2。

Phase 1 关闭路径已加 `wal_checkpoint(TRUNCATE)`，但 macOS Docker bind mount 上的 fsync 排序漏洞依然存在——只要 WAL 文件还在，崩溃就有机会重放乱序帧。Phase 2 用两条线把这个攻击面收掉：

1. **从根本上规避 WAL** — slang.db 是写少读多（每天数百次写、数千次读），切到 `journal_mode=DELETE` + `synchronous=FULL` 没有 WAL 文件就没有这个 fsync 排序问题
2. **巡检+紧急备份** — 每小时 `PRAGMA quick_check` 巡检所有关键 SQLite 库，发现 `quick_check != "ok"` 立即触发 `pre-change` profile 紧急备份（留下损坏前最后一份干净状态）+ 通过 loguru `channel="backup"` 自动落到 admin SSE 红条

**Phase 2 改动**：

**\#2 slang.db 切换 journal_mode=DELETE**（已于 fc7e591 单独入库）：

- [services/slang/store.py](services/slang/store.py) `init()`：`connect_sqlite` 之后立即 `PRAGMA journal_mode=DELETE` + `PRAGMA synchronous=FULL`，再 commit
- 不动 [services/storage/sqlite.py](services/storage/sqlite.py) 全局默认（其他 store 仍是 WAL+NORMAL）——slang 是已知反复损坏者，定向治理；其他 store 由 Phase 1 的 `close_with_checkpoint` 兜底，未来视情况推到 Phase 3 named volume
- 19 个 slang 测试全绿；DELETE 模式下产生的 db 完全兼容 WAL 模式，可随时回滚

**\#4 BackupConfig + BackupScheduler quick_check 回路 + admin 接线**：

- [kernel/config.py](kernel/config.py) 新增 `BackupConfig`（Pydantic）：`enabled` / `daily_time` / `keep_days` / `default_profile` / `quick_check_enabled` / `quick_check_interval_minutes`（15–1440 min）；`@model_validator` 校验 `daily_time` 为合法 `HH:MM`。挂到 `BotConfig.backup`
- [services/storage/backup.py](services/storage/backup.py) stdlib `logging` → loguru `_L = logger.bind(channel="backup")`；备份失败、紧急触发都进 admin SSE
- [services/storage/backup_scheduler.py](services/storage/backup_scheduler.py) 重写：
  - 新增 `QuickCheckResult` dataclass（db_id / path / ok / quick_check / journal_mode / error）
  - 两条并发 asyncio loop——`_daily_loop()`（沿用）+ `_quick_check_loop()`（新增）
  - quick_check 失败：`_L.error(...)` 直入 admin 红条 + 立即跑 `pre-change` profile 紧急备份；备份本身也损坏时记 `emergency pre-change backup rejected`
  - 新方法：`run_now(profile)` / `run_quick_check_now()` / `last_quick_check` / `settings` / `reload(quick_check_*)`
- [bot.py](bot.py) 第一次接线：从 `_bot_config.backup` 实例化 `BackupScheduler` 挂到 `_plugin_ctx.backup_scheduler`，[kernel/router.py](kernel/router.py) `on_startup`/`on_shutdown` 启停。这是 BackupScheduler 自上线以来第一次真的进 lifespan
- [admin/routes/api/backup.py](admin/routes/api/backup.py)（新增）+ [admin/routes/api/__init__.py](admin/routes/api/__init__.py)：6 条 `/api/admin/backup/*` 路由——`GET/POST /settings`、`GET /list?profile=`、`POST /create`、`GET/POST /quick-check`。`POST /settings` Pydantic 校验后 hot-reload scheduler + patch `config.json` 的 `backup` 块
- [admin/frontend/src/views/system/components/SystemBackup.vue](admin/frontend/src/views/system/components/SystemBackup.vue) + [admin/frontend/src/views/config/components/ConfigSystemBackup.vue](admin/frontend/src/views/config/components/ConfigSystemBackup.vue) 重写：
  - 切到新 `/api/admin/backup/create`（旧版调的是不存在的 `?profile=` form-style 接口，stub 状态）
  - 新增「SQLite 完整性巡检」面板：实时显示每个 db 的 `quick_check` / `journal_mode` 状态、上次巡检时间、立即巡检按钮
  - settings 表单加 `quick_check_enabled` / `quick_check_interval_minutes`
  - 全部 TypeScript 化（BackupListItem / BackupSettings / QuickCheckSnapshot 接口）

**测试覆盖**：

- [tests/test_backup_service.py](tests/test_backup_service.py) 23 个测试全绿（16 旧 + 4 修正过时断言 + 3 新 quick_check：`probe_passes_for_clean_db` / `detects_corruption` / `handles_missing_db`）
- 修正过时测试：`test_health_check_reads_backup_registry` 期望 8 个 db 但 `_check_sqlite` 只跑 3 个 → 改成 3；`test_health_check_warns_stale_backup` / `test_disk_usage_warning_threshold` 引用从未实现的 `_check_backup_freshness` / `_check_backup_disk_usage` → 改为校验 manifest mtime / `_free_disk_bytes(path)`
- Phase 1+2 影响范围全测：`test_backup_service.py` + `test_storage_sqlite.py` + `test_slang_store.py` + `test_message_log.py` + `test_card_store.py` + `test_knowledge.py` + `test_knowledge_graph.py` 共 115 通过 0 失败
- vue-tsc：backup 相关 0 错误（block-trace 1 个无关错误是 untracked 文件导入 `useSSE.onBlockTrace`）；npm run build 4.92s 通过，SystemView/ConfigView bundle 含本次 backup 改动
- 注意：`uv run pytest` 整体跑有 4 个 collection error（`test_graph_writer` / `test_reply_workflow` / `test_segmentation` / `test_slang_collision`）+ 48 个 fail，都是其他未提交工作的 untracked 测试，与 Phase 2 无关

**部署影响**：

- 重启 bot：`docker compose restart bot`（**napcat 全程不动**——CLAUDE.md 铁律 + 设备指纹反风控）。重启后第一条 backup 日志会输出 `backup scheduler started, daily_time=04:30:00` + `quick_check loop started, interval=3600s`
- slang.db journal_mode 切换是首次 startup 时自动完成的（`PRAGMA journal_mode=DELETE` 会把现有 WAL 文件 checkpoint+删除）。验证：`sqlite3 storage/slang.db "PRAGMA journal_mode"` 应返回 `delete`，`storage/slang.db-wal` 应不再存在
- 前端 `./admin/static` 是 bind mount，`npm run build` 产物已即时生效，无需 rebuild docker 镜像
- 第一次 quick_check 默认 60 分钟后触发；想立即验证可在 admin 备份面板按「立即巡检」

**回滚路径**：

- \#2 slang DELETE：fc7e591 单独 commit，`git revert fc7e591` 即回 WAL 模式；下次 init 时 `PRAGMA journal_mode=WAL` 自动迁回
- \#4 quick_check：`backup.quick_check_enabled = false`（admin UI 或 config.json）即关掉巡检 loop；reload 立即生效不需重启
- 新增的 `/api/admin/backup/*` 路由不调用就不触发，向后兼容旧 `POST /api/admin/backup`（system.py 的原 tar.gz 接口保留）

**与 Phase 1 / Phase 3 的衔接**：

- Phase 1 的 `close_with_checkpoint` 仍然是其他 5 个 WAL store 的兜底——slang 切 DELETE 后 WAL 文件不存在，对它来说 checkpoint 是 no-op
- Phase 2 的 quick_check 是早期预警；它**不能**预防损坏，只是把 RPO（最大可丢窗口）压到 1 小时。彻底消除 fsync 排序漏洞还得靠 Phase 3 named volume——但 Phase 3 风险更高需要 5 分钟服务停机，本次先跑 Phase 1+2 观察 24h+ 再决定是否推 Phase 3

---

## 2026-05-21 slang.db 反复损坏全栈治本 Phase 1 — close_with_checkpoint + 主机脚本守卫

**变更类型**：fix / storage + scripts（纯代码层、零 infra）

**背景**：

`storage/slang.db` 在 2026-05-11（3 次）、2026-05-17（1 次）、2026-05-20（最近一次）反复物理损坏，每 5–10 天一次。根因分析（plan：`/Users/kragcola/.claude/plans/modular-forging-allen.md`）：

- macOS Docker bind mount + SQLite WAL + `synchronous=NORMAL` 在重启 / checkpoint 时存在 fsync 排序漏洞——崩溃可能在 close 与 next-open 之间重放乱序 WAL frame，叠加在 main db 上，导致多棵 b-tree 页号失效（典型形态：`invalid page number 7xxx`）
- `scripts/dev/slang_*.py` 共 6 个写路径脚本默认 `--db storage/slang.db` 直指 live DB，其中只有 `slang_db_repair.py` 在 `default/recover --apply` 路径有 `_is_bot_running()` 守卫；其余 5 个脚本绕过守卫——主机 `sqlite3.connect` 与容器 bot WAL 锁的跨进程锁定域不互见，是损坏的另一来源
- `slang_db_repair.py` 的 `_sqlite_recover` 用 `text=True` 把 sqlite3 `.recover` stdout 当 UTF-8 解码，corrupt b-tree 页可能含非 UTF-8 字节，修复脚本本身会 `UnicodeDecodeError` 拒绝运行

用户决定：**3 阶段全治本**——同时修代码、PRAGMA、运维三层。本次仅 Phase 1（纯 .py 改动，零 infra）。

**Phase 1 改动**：

**\#3 优雅关闭时 `wal_checkpoint(TRUNCATE)`**：

- 新增 [services/storage/sqlite.py](services/storage/sqlite.py) 工具函数 `close_with_checkpoint` / `close_with_checkpoint_sync`：在 `await db.close()` 前 best-effort 执行 `PRAGMA wal_checkpoint(TRUNCATE)`，把 WAL 内容压回 main db 文件。失败仅记 warn 日志、close 仍继续。两个版本都 None-guard，cancel-path 安全。
- 6 个 store close 路径全部接入：
  - [services/slang/store.py](services/slang/store.py) `SlangStore.close`
  - [services/memory/message_log.py](services/memory/message_log.py) `MessageLog.close`
  - [services/memory/card_store.py](services/memory/card_store.py) `CardStore.close`
  - [services/knowledge_graph/store.py](services/knowledge_graph/store.py) `KnowledgeGraphStore.close`
  - [services/knowledge/store.py](services/knowledge/store.py) `KnowledgeIndexStore.close`（sync 版）
  - [services/block_trace/store.py](services/block_trace/store.py) `BlockTraceStore.close`（已 untracked，本次随 commit-1 不动；后续会随 block_trace 整体落地）
- 补齐缺失的 close 调用：
  - [plugins/chat/plugin.py](plugins/chat/plugin.py) `on_shutdown` 新增 `await ctx.card_store.close()`（之前漏关 → CardStore WAL 留在 fsync 不确定状态）
  - [plugins/knowledge/plugin.py](plugins/knowledge/plugin.py) **没有 on_shutdown** —— 新增一个，负责关 `KnowledgeIndexStore`（sync close）

**\#5 主机 slang 脚本统一 bot-running 守卫**：

- 新建 [scripts/dev/_bot_guard.py](scripts/dev/_bot_guard.py)：`assert_bot_stopped(action, force)` 共享模块。`docker compose ps --format json` 检测 bot 容器 `State == "running"` 时退出码 2；`--force` 路径打印警告继续。`docker` CLI 不可用时回退到"当作 stopped"——开发机操作员自负责。
- 5 个写路径脚本接入守卫：
  - `slang_batch_merge_collisions.py` / `slang_collision_auto_merge.py` / `slang_meta_migration_p02.py` / `style_seed_approved.py` / `slang_db_repair.py` 的 rebuild + recover --apply 路径
- 3 个只读脚本（`slang_acceptance_check.py` / `slang_alias_collision_report.py` / `slang_semantic_smoke.py`）不加守卫，与写路径区分

**\#6 修 `_sqlite_recover` UTF-8 解码 bug**：

- [scripts/dev/slang_db_repair.py](scripts/dev/slang_db_repair.py) `_sqlite_recover` 两处 `subprocess.run` 都改 `text=False`，stdout/stdin 在两个 sqlite3 进程之间走 raw bytes，Python 层不解码。仅在错误路径用 `errors="replace"` 把 stderr 拼成可读消息。

**验证**：

- 新增 [tests/test_storage_sqlite.py](tests/test_storage_sqlite.py)：5 个 case，包含 D2 cancel-path 回归（`asyncio.wait_for(close_with_checkpoint, timeout=0.0001)` + `pytest.raises(asyncio.TimeoutError)`），断言外部状态干净；happy + None guard + sync 全覆盖。`pytest tests/test_storage_sqlite.py`：5 passed in 0.05s。
- 新增 [tests/test_slang_db_integrity.py](tests/test_slang_db_integrity.py)：6 case，corrupt-DB 整体性合约——断言 `SlangDatabaseCorruptError` 容错 init 路径在 admin API 层兼容（不会让 admin 整站 500）。6 passed。
- 全 store close 类测试套件复跑：98 passed in 0.92s。
- `ruff check` 触及文件零错误；`pyright` 没有引入新错误（116 个全部 pre-existing，与 stash 前对比一致）。

**Commit 拆分**：

- `40656a0` fix(storage): wal_checkpoint(TRUNCATE) on graceful close — 9 modified tracked + 2 新测试
- `227bc7f` fix(scripts): host slang dev scripts — bot-running guard + .recover UTF-8 fix — `_bot_guard.py` + `slang_db_repair.py` UTF-8 + 4 脚本守卫 + 3 只读脚本（同批落地）

**影响范围**：

- 重启 bot 时每个 store 多一次 `wal_checkpoint(TRUNCATE)` SQL（WAL 大小决定耗时；本机典型 < 50ms），可承受
- 主机侧任何写路径脚本必须先 `docker compose stop bot` 才能跑，否则被守卫拒绝（这是预期行为）
- 修复后 `slang_db_repair.py recover` 在 corrupt 文件含非 UTF-8 字节时不再误报

**与 2026-05-17 BackupScheduler 上线的关系**：

那次是 RPO（"我们最多丢多少时间窗"），本次是 prevention（"少损坏一次")。两者互补——backup 是兜底，close_with_checkpoint 是消除 WAL 漂移源；Phase 2 会加 hourly quick_check 让 BackupScheduler 在损坏发生时立即报警 + 紧急备份干净状态。

**后续动作**：

- 部署窗口：今天/明天选低峰时段 `dot_clean . && docker compose up bot -d --build`（**铁律：napcat 不动**）；shutdown 日志预期 6 个 store 都打 `wal_checkpoint truncate ok`，重启后 `storage/*.db-wal` 都应是 0 字节
- 24h 观察期后启动 Phase 2：slang journal_mode=DELETE + BackupScheduler hourly quick_check + admin 告警
- 30 天观察窗口：判断 close_with_checkpoint + DELETE 模式（Phase 2 后）能否完全消除 corruption；如能，Phase 3 storage → named volume 仍按计划做（彻底消除根因），但优先级可降为 nice-to-have

**回滚**：

纯 `git revert 227bc7f 40656a0` 即可，不涉及数据迁移；DB 文件格式向前兼容。

---

## 2026-05-19 仪表盘 24h 调用曲线 → 管线命中率视图

**变更类型**：feat / admin-frontend + backend

**变更内容**：

LLMRequest spine 上线后 (commit `53cb7fa`) `LLMClient` 已能逐 task 记录 cache 命中率，但 admin Dashboard 还在显示 spine 之前那套 "24 小时调用曲线"——只画 calls 计数，看不出哪个管线在掉链子。本次替换为按管线分组的命中率视图。

按 LLMTask 的运维职责分 4 个管线：

- `core_chat` 主聊天链路 — `main` / `thinker` / `compact` / `reply_gate`
- `slang` 黑话治理 — `slang` / `slang_review` / `slang_drift` / `slang_semantic`
- `learning` 学习与工具 — `style` / `memo` / `chat_private` / `bilibili_intent` / `element_detect` / `vision`
- `memory_graph` 多层记忆 (预留) — `graph_review` / `graph_edge_classifier` / `reflection_consolidator` / `episode_summarizer`

**后端改动**：

- 新建 [services/llm/llm_pipelines.py](services/llm/llm_pipelines.py)：`LLMPipeline` dataclass + `LLM_PIPELINES` tuple + `pipeline_for_task` / `all_pipeline_tasks` / `resolve_call_type` 工具。`_CALL_TYPE_ALIASES = {"chat": "main", "proactive": "main"}` 处理 spine 之前主链路写入的历史行（标注 "过渡期，spine 全量切换后移除"）。
- [services/llm/usage.py](services/llm/usage.py)：新增 `cache_hit_by_call_type(*, period, date, tz_offset_hours)`，`GROUP BY call_type` 返回 `calls / hit_tokens / miss_tokens`。
- [admin/routes/api/dashboard.py](admin/routes/api/dashboard.py)：
  - 新增 `GET /api/admin/dashboard/cache-pipelines?period=day|week|month`，按管线返回加权命中率
  - 现有 `/dashboard` 响应里 `usage` 字段加 `cache_hit_pct: float | None`，由后端用 `prompt_cache_hit_tokens / (hit + miss)` 算好直接给——hero "Cache 命中" 与 panel 同分母同口径，两个数字不可能漂移

**前端改动**：

- 新建 [admin/frontend/src/views/dashboard/components/CachePipelinePanel.vue](admin/frontend/src/views/dashboard/components/CachePipelinePanel.vue)：基于 AppPanelSection / NProgress / NTag 不引图表库；每行显示管线名 + 加权命中率 + 颜色分段进度条 + 前 5 名 task chip（按 `hit_pct DESC` 排序，`calls<3` 加 `*` 提示样本不足，`calls=0` 不入 chip 改写"X 个未触发任务"）。整 panel 数据空时 `EmptyState`；`overall.calls < 10` 时顶部加灰字"今日样本数较少…"。
- [admin/frontend/src/views/system/helpers/formatters.ts](admin/frontend/src/views/system/helpers/formatters.ts)：加 `cacheHitColor(pct)`（与 `meterColor` 反极性，高=好绿、低=差红、null=灰）+ `formatHitPct(pct)`（0..1 → "92%" / "--"）。
- [admin/frontend/src/views/dashboard/DashboardView.vue](admin/frontend/src/views/dashboard/DashboardView.vue)：
  - 替换 L677-L692 整段 `<AppPanelSection eyebrow="USAGE" title="24 小时调用曲线">` 为 `<CachePipelinePanel :data="cachePipelines" @navigate="goTo" />`
  - 删除 `usageHourlyBuckets` computed 与 `SparklineChart` import（dashboard 仅此一处用）
  - `todayCacheHitRate` 改为读 `data.value?.usage?.cache_hit_pct`（hero 同口径）
  - `Promise.allSettled` 数组加第 8 项：`api<CachePipelineData>('/api/admin/dashboard/cache-pipelines?period=day')`
  - **保留** `usageData` fetch + `usageTopGroups` computed（hero 还在用 `total_calls` 等其它字段）
  - `DashboardUsage` interface 加 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` / `cache_hit_pct` 字段

**测试**：

- 新建 [tests/test_llm_pipelines.py](tests/test_llm_pipelines.py) — 5 个守门：
  - `test_all_llm_tasks_are_covered_by_pipelines` 强制 `set(all_llm_tasks()) == all_pipeline_tasks()`，新加 LLMTask 没分类就红
  - `test_pipelines_have_no_overlap`、`test_pipeline_keys_are_unique_and_stable` 保护前端 4 个 key Literal 硬编码
  - `test_pipeline_for_task_returns_owner` + `test_resolve_call_type_folds_legacy_aliases`
- 新建 [tests/test_dashboard_cache_pipelines.py](tests/test_dashboard_cache_pipelines.py) — 5 个 endpoint smoke：
  - 跨 task 多行混合（含 dream 未分类、chat/proactive 别名）断言 overall + per-pipeline + per_task 数字与手算一致
  - 全 0 数据 `hit_pct is None`；非法 period 返回 error；`/dashboard.usage.cache_hit_pct` 与手算一致

**验证**：

- 全量 `pytest -q`：1077 passed, 8 skipped（基线 1066，本次 +11：5 pipeline guard + 5 endpoint smoke + 1 dashboard 同口径）
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit`：clean
- `ruff check`：本次新增代码零错误，仅剩历史 pre-existing 错误

**口径选择（用户决策）**：

旧 hero "Cache 命中" 用 `cache_read / total_input`（DeepSeek 风格），新 panel 用 `prompt_cache_hit / (hit + miss)`（spine 统一字段）。两套分母在某些 provider 下数值会差几个点。本次用户选择"本期合并 hero 到同口径"——`/dashboard` 直接吐 `cache_hit_pct` 让前端单读，避免双数字共存的混淆。

**影响范围**：

- Dashboard 首屏可视化彻底换风格——不再有按时间的折线，而是按管线的命中率行
- hero "Cache 命中" 数值会变化（口径切换）；外部脚本若读 `/dashboard.usage.cache_hit_pct` 是新增字段、不破坏旧字段
- `memory_graph` 管线今天必然显示一片灰（4 个 task 全是预留），文案 `多层记忆 (预留)` 已说明
- 旧 `SparklineChart.vue` 暂未删除——其它视图可能在用，本次只解除 dashboard 引用

**后续动作**：

- staging 灰度后用真实 per-task 数据再推 P1（profile 各自 `cache_hit_pct` 隔离监控的目标线）
- 一旦 spine 之前的 `chat`/`proactive` call_type 历史行被 pruning 清除、所有新行只走 `main`/`compact`，删 `_CALL_TYPE_ALIASES`

---

## 2026-05-19（夜）CPA fixup sidecar 上线 — DeepSeek thinking 多轮 400 修复

**变更类型**：local ops / Codex workflow

**问题来源**：

VS Code Codex 第二轮请求必 400：`{"error":{"message":"The reasoning_content in the thinking mode must be passed back to the API."}}`。第一轮工作（"你好" → "你好！"），第二轮就炸。CPA 错误日志：[local/cpa/logs/error-v1-responses-2026-05-19T071355-01473c00.log](local/cpa/logs/error-v1-responses-2026-05-19T071355-01473c00.log)。

**根因**：

CPA 7.1.11 在 OpenAI Responses ⇄ DeepSeek thinking 翻译时丢字段。完整 bug 链：

1. 第一轮 DeepSeek thinking 模式吐出 `reasoning_content`；CPA 转 Responses 协议时把它写成空 stub：`{"type":"reasoning","summary":[{"text":""}],"content":null,"encrypted_content":""}`。
2. Codex 把这个空 reasoning 存进 history。
3. 第二轮 Codex 把 history 原样回传给 CPA；CPA 翻译回 DeepSeek chat 时 `reasoning_content` 字段是空的。
4. DeepSeek thinking 模式硬性要求 history 里上一轮的 `reasoning_content` 必须非空回传 → 400。

上游 CPA 没有针对 thinking 模式 reasoning 翻译的可配置开关；DeepSeek `extra_body.thinking={"type":"disabled"}` CPA 也不支持注入。

**修复**：

新增本机 fixup sidecar（stdlib 单文件 Python，无新增项目依赖）：

- [local/cpa/cpa-fixup-sidecar.py](local/cpa/cpa-fixup-sidecar.py)：监听 `127.0.0.1:8318`，转发到 CPA `127.0.0.1:8317`。仅对 `POST /v1/responses` 且 `model` 以 `ds/` 开头（DeepSeek via CPA `openai-compatibility` prefix）的请求，遍历 `input` 数组剥掉所有 `{"type":"reasoning",...}` item。其它请求和模型透传。SSE 流式响应逐 chunk 转发，不缓冲。
- [local/cpa/cpa-fixup-sidecar-ctl.sh](local/cpa/cpa-fixup-sidecar-ctl.sh)：start/stop/status/restart 控制脚本，PID 写入 `run/cpa-fixup.pid`，日志 `logs/cpa-fixup.log`。
- [~/.codex/config.toml](~/.codex/config.toml)：`model_providers.custom.base_url` 从 `:8317/v1` 改到 `:8318/v1`。
- [local/cpa/run-codex-local.sh](local/cpa/run-codex-local.sh)：CLI launcher 同步切到 `:8318`。
- [local/cpa/README.md](local/cpa/README.md)：补 sidecar 说明。

**为什么"剥 reasoning history"是正确选项**（用户决策）：

DeepSeek 每轮 thinking 都是从头跑，历史里的 reasoning_content 对当轮回答帮助有限，主要服务于"模型保持思路连贯"。剥掉历史 reasoning 等于让 DeepSeek 每轮独立思考，能力损失可接受；当轮 thinking 仍然完整生成给用户看。

**验证**：

- `python3 -m py_compile local/cpa/cpa-fixup-sidecar.py`：通过。
- `bash -n local/cpa/cpa-fixup-sidecar-ctl.sh`：通过。
- 单轮透传：`curl /v1/models` via 8318，看到所有 DeepSeek 模型。
- 多轮 stale reasoning 烟测（带 1 个 stale reasoning item）：flash + pro 都 200 OK，sidecar 日志 `stripped 1 stale reasoning item(s)`。
- 极端烟测（2 个连续 stale reasoning items）：pro 仍 200 OK，DeepSeek thinking summary 正常生成，FINAL TEXT `DeepSeek`，sidecar 日志 `stripped 2 stale reasoning item(s)`。

**生效需要重启 VS Code Codex 会话** — Codex 启动时一次性读 `model_providers.custom.base_url`。

**回滚路径**：

```bash
./local/cpa/cpa-fixup-sidecar-ctl.sh stop
# 编辑 ~/.codex/config.toml 把 base_url 改回 8317
# 编辑 local/cpa/run-codex-local.sh 把 base_url 改回 8317
```

**已知边界**：

- sidecar 不处理 SSE response 内容修复，只处理 request 入站。如果以后 CPA 在 SSE 流里产生 thinking 字段错位（目前还没看到），需要扩展。
- sidecar 单线程 socket I/O 透传，不限速、不重试。CPA 自身的 `request-retry: 3` 仍然生效。

---

## 2026-05-19（晚）Codex profile 锁着 gpt-5.5 残留修复 + 1M 上下文利用率提升

**变更类型**：local ops / Codex workflow

**问题来源**：

用户报告 ccswitch + CPA 反代接 DeepSeek 后，VS Code Codex 新会话仍显示 / 路由到 `gpt-5.5`。

**根因**：

[~/.codex/config.toml](~/.codex/config.toml) 顶层 `profile = "auto-max"` 已激活，但 `[profiles.auto-max].model = "gpt-5.5"` 没改。VS Code Codex wrapper（`codex-vscode-no-proxy.sh`）虽然把命令行 `-m/-p` 过滤了，但 codex 启动后会读 active profile 的 `model` 字段——profile 内部的覆盖优先级高于顶层默认，wrapper 的 `-m ds/deepseek-v4-pro` 在 profile 解析阶段被覆盖回 `gpt-5.5`。CPA 因此仍能收到 `/v1/responses` 200 OK（被 wrapper 的 `-c model="..."` 强制覆盖了一部分），但模型显示 / 部分代码路径仍按 gpt-5.5 走。维护日志 2026-05-19 早班记录的"wrapper 强制 ds/deepseek-v4-pro"只解决了命令行入口，没解决 profile 自身锁着旧模型的问题。

**变更内容**：

- [~/.codex/config.toml](~/.codex/config.toml)：
  - `[profiles.auto-max].model` 从 `gpt-5.5` 改为 `ds/deepseek-v4-pro`。
  - profile 内新增 `model_context_window = 1000000`、`model_auto_compact_token_limit = 950000`，与顶层一致；避免 wrapper 失效场景下 profile 退回默认值。
  - 顶层同步加 `model_context_window` / `model_auto_compact_token_limit`，让裸 codex 调用也能拿到 1M 窗口。
- [local/cpa/codex-vscode-no-proxy.sh](local/cpa/codex-vscode-no-proxy.sh)：`CODEX_AUTO_COMPACT_TOKEN_LIMIT` 默认值从 900000 抬到 950000。
- [local/cpa/run-codex-local.sh](local/cpa/run-codex-local.sh)：同步抬到 950000。

**为什么是 950K 不是 1M**：

DeepSeek V4 默认 thinking，每轮要预留几千 reasoning token；auto-compact 必须留出足够余量给"最后一次 LLM 触发 compact 的那轮 prompt + completion"，否则会撞 max_tokens。950K 给最后一轮留 50K 余量，等于把可用窗口从 90% 抬到 95%，又不冒撑爆风险。

**验证**：

- `bash -n` 两个 shell 脚本：通过。
- `bash -x codex-vscode-no-proxy.sh -m gpt-5.5 -p auto-max --version`：实际 exec 参数为 `-m ds/deepseek-v4-pro -c model="ds/deepseek-v4-pro" -c model_context_window=1000000 -c model_auto_compact_token_limit=950000`。传入的 GPT-5.5 / auto-max profile 都被过滤；codex 启动后 profile 自身也指向 ds/deepseek-v4-pro，双保险。
- CPA 进程在跑、`127.0.0.1:8317` 持续收 `/v1/responses` 200 OK。

**影响范围**：

- 已开启的 VS Code Codex 会话需重启才能生效（profile 在进程启动时读一次）。
- 新会话默认 950K 才触发 auto-compact，比之前多约 50K 可用上下文。如果观察到 max_tokens 撞顶，回退到 920K。

**为什么没动 `profile = "auto-max"` 顶层声明**：

profile 内部除了 model/context 现在也带着 `sandbox_mode = "workspace-write"` 等差异化设置，是用户为 VS Code 工作区刻意保留的，不能直接删；顶层的 `profile =` 也保留，避免显式禁用 profile 后某些代码路径 fallback 到完全的默认。

---

## 2026-05-19 DeepSeek V4 接入本机 CPA/Codex

**变更类型**：local ops / Codex workflow

**变更内容**：

- `local/cpa/apply-deepseek-provider.py`：新增本机同步脚本，从已有 DeepSeek 配置读取 API key（不打印值），写入 ignored 的 CPA native/docker 配置。
- `local/cpa/config.native.yaml`、`local/cpa/config.yaml`：新增 CPA `openai-compatibility` provider `deepseek`，前缀 `ds`，模型为 `deepseek-v4-flash` / `deepseek-v4-pro`。
- `local/cpa/run-codex-local.sh`：新增 `--deepseek-flash`、`--deepseek-pro` 快捷参数，默认路由改为 `ds/deepseek-v4-pro`，并显式设置 1M context / 900K auto-compact。
- `local/cpa/codex-vscode-no-proxy.sh`：VS Code Codex wrapper 过滤传入的 `-m/--model` 与 `-p/--profile`，强制使用 `ds/deepseek-v4-pro`，避免 `~/.codex/config.toml` 的 `auto-max` profile 把新会话带回 `gpt-5.5`。
- `local/cpa/README.md`：补充 DeepSeek via CPA 的本机使用说明。

**验证**：

- CPA 热重载成功：日志显示 `1 OpenAI-compat`。
- `/v1/models`：`ds/deepseek-v4-flash`、`ds/deepseek-v4-pro` 均可见。
- `/v1/responses` 探针：两个模型均返回 `completed`，输出结构包含 `reasoning, message`，最终文本为 `ok`。
- 确认未配置旧别名：`deepseek-chat` / `deepseek-reasoner` 未出现在本机 CPA 配置与说明中。
- `bash -n local/cpa/codex-vscode-no-proxy.sh local/cpa/run-codex-local.sh`：通过。
- `bash -x local/cpa/codex-vscode-no-proxy.sh -m gpt-5.5 -p auto-max --version`：实际 exec 参数为 `-m ds/deepseek-v4-pro`，传入的 GPT/profile 参数已被过滤。

**交接说明**：

DeepSeek V4 默认 thinking，会消耗输出 token；小探针需给足 `max_output_tokens`，否则可能只有 reasoning 没有 final message。当前接入只使用 Flash/Pro 两个正式模型名，不额外伪造 chat/reasoner 别名。

---

## 2026-05-19 全量 pytest 退出挂住修复（aiosqlite 资源收尾）

**变更类型**：test infra / backend reliability

**问题现象**：

Claude Code 运行 `uv run pytest` 时看似“卡住”。实际复现时 pytest 已打印 `1077 passed, 8 skipped`，测试本体约 11 秒完成，但 Python 进程没有退出，直到外层超时。

**根因**：

退出阶段仍残留非 daemon 的 `aiosqlite` `_connection_worker_thread`。faulthandler 现场显示 Python 卡在 `threading._shutdown` 等待这些连接线程结束。逐文件扫描定位到：

- `tests/test_card_store.py`：fixture 和手工 re-init 的 `CardStore` 未完整 close。
- `tests/test_usage_routes.py`：`UsageTracker` async fixture 未 close，`TestClient` 未用 context 收尾。
- `tests/test_slang_db_integrity.py`：腐坏 SQLite 触发 `connect_sqlite()` 的 PRAGMA 初始化失败时，底层 aiosqlite 连接已打开但没有关闭。

**变更内容**：

- `services/storage/sqlite.py`：`connect_sqlite()` 在 PRAGMA/初始化失败时 `await db.close()` 后重新抛错，避免失败路径泄漏 worker thread。
- `tests/test_card_store.py`：`store` fixture 改为 yield-finally close；backfill 测试中 `s/s2/s3` 全部 finally close。
- `tests/test_usage_routes.py`：`UsageTracker` fixture 改为 yield-finally close；`TestClient` 改为 context fixture。

**验证**：

- 残留线程扫描：`tests/test_card_store.py`、`tests/test_slang_db_integrity.py`、`tests/test_usage_routes.py` 均为 `THREADS 0`。
- `source ./scripts/dev/env.sh && uv run pytest tests/test_card_store.py tests/test_slang_db_integrity.py tests/test_usage_routes.py -q --tb=short`：56 passed。
- `source ./scripts/dev/env.sh && uv run ruff check services/storage/sqlite.py tests/test_card_store.py tests/test_usage_routes.py tests/test_slang_db_integrity.py`：通过。
- `source ./scripts/dev/env.sh && uv run pytest -q --tb=short`：1077 passed, 8 skipped in 10.71s，命令自然退出。

**交接说明**：

本次不是测试断言失败，而是 pytest 通过后的进程退出泄漏。若后续再出现“总结已打印但 Claude Code 不返回”，优先用 `threading.enumerate()` / `faulthandler.dump_traceback_later()` 查未关闭线程。

---

## 2026-05-19 多层学习记忆基石补丁 P1/P2/P3

**变更类型**：docs / backend / frontend

**变更内容**：

按 [docs/audits/multilayer-memory-learning-report-2026-05-17.md](docs/audits/multilayer-memory-learning-report-2026-05-17.md) 顶部"基石达标补丁"执行 Phase A0 前置三项：

- P1：在报告新增 §10 "关键接口契约草案"，补 `ContextProvider` / `BlockTraceBus` / `GraphWriter` 三段 Protocol / dataclass 雏形，并附决议追溯表。
- P2：`SlangSettings.max_indirect_inject_terms` 后端默认值改为 `0`，前端 `DEFAULT_SLANG_SETTINGS.max_indirect_inject_terms` 同步改为 `0`，恢复"默认只注入当前上下文直命中 approved 黑话"的承诺；新增默认 direct-only 回归测试。
- P3：grep 与代码阅读确认 `services/slang/` 运行时尚未接入 `LearningNormalizerStore.attach_candidate(domain="slang")`；报告已把 A1.3 从"复核/如果缺失"改为"已确认缺失，本轮补齐 attach 路径"。
- 同步 [docs/audits/slang-collision-thinker-audit-2026-05-18.md](docs/audits/slang-collision-thinker-audit-2026-05-18.md)，关闭 indirect 默认值"决议待定"旧状态。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py -q`：22 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit`：通过

**影响范围**：

新建或缺省的黑话设置会默认 `max_indirect_inject_terms=0`；已有 DB 设置如果没有该字段，会经 Pydantic 默认值回落到 0。Admin 仍可手动把该值调到 1-30 做实验。A1.3 的 normalizer 实际补链路仍是后续 Phase A1 数据治理任务。

---

## 2026-05-19 旧追踪与研讨文档清理

**变更类型**：docs / cleanup

**变更内容**：

阅读当前高层文档、wiki、CHANGELOG 和维护日志后，删除一批已经被正式文档、代码实现或维护日志吸收的过程文档，避免后续会话继续把旧路线当作当前待办：

- 删除 2026-05-17 备份体系方案与实施 tracker；当前权威状态已在本日志的“备份体系服务层实施（Phase 1-4）”、`services/storage/backup.py`、`services/storage/backup_scheduler.py` 和 `tests/test_backup_service.py` 中体现。
- 删除 2026-05-07 生态路线与 QQ bot 对比审计产物；对应能力已收敛到当前 wiki、`docs/project-info.md`、插件/Provider/Admin 实现和维护日志。
- 删除 2026-05-08 知识库/Context Knowledge System 的旧审计、路线图和进度表；当前权威说明已收敛到 `docs/wiki/Knowledge-System.md`、`docs/knowledge/omubot/*.md` 和相关实现。
- 删除 2026-05-03 Thinker 多阶段流水线旧方案、2026-05-07 三层架构旧审计，以及已经停在 2026-05-06 的 `docs/session-handoff.md`。

**保留原则**：

保留仍有明确未完成项或人工验收卡点的追踪文档：`docs/tracking/web-refactor.md`、`docs/slang-module-implementation-tracker.md`、`docs/style-learning-implementation-tracker.md`、`docs/reply-workflow-implementation-tracker.md`、`docs/conversation-archive-implementation-tracker.md`、`docs/group-concurrency-implementation-tracker.md` 以及近期黑话/多层记忆/LLMRequest spine 相关审计与迁移文档。

**影响范围**：

仅删除过期文档和旧审计产物，不改变运行时代码、配置、API 或管理端行为。

**交接说明**：

后续理解项目时继续优先读 `docs/project-info.md`、`docs/wiki/`、`wiki/`、`CHANGELOG.md` 和本维护日志；不要再依赖已删除的旧计划文档恢复当前状态。

---

## 2026-05-18 LLMRequest spine 迁移阶段 D-later 完成（聚合契约重谈 + main/compact 迁移）

**变更类型**：refactor / backend

**变更内容**：

接续同日的 D-now，今天补齐了 D-later 阻塞项——main `chat()` 和 `_compact_with_tools` 的 LLMRequest 迁移。完成后 `_call_thinker` / `_call_compact` / `_call_slang` / `_call_reply_gate` 四个 wrapper **全部清空**，`_call` 成为 `LLMClient` 唯一的 LLM 调用入口。

**契约重谈方案**：选定方案 A（在 LLMRequest 加 `auto_record_usage` 开关）。

- DL.1 `services/llm/llm_request.py`：`LLMRequest` 加 `auto_record_usage: bool = True` 字段。
- DL.1 `services/llm/client.py:_dispatch_call`：当 `request.auto_record_usage=False` 时跳过自动 `_record_usage`，但仍执行 `_record_cache_diagnostic`。聚合 caller 自己负责 `_record_usage`，per-round break 轴可见性零损失。
- DL.2 `_compact_with_tools` 两处调用点 → `_call(LLMRequest(task="compact", auto_record_usage=False))`。
- DL.3 main `chat()` tool_loop + final 两处调用点 → `_call(LLMRequest(task="main", auto_record_usage=False))`。
- DL.4 删除 `_call_compact` wrapper。
- DL.5 加 2 个回归测试：`test_compact_aggregated_usage_with_per_round_diagnostic` 和 `test_main_chat_aggregated_usage_with_per_round_diagnostic`，断言 `len(rows)==1` 聚合契约不变 + `cache_diagnostic_history(task)` 仍逐 round 记录。

**为什么不选方案 B / C**：

- 方案 B（接受多行 usage）：需要改 `test_chat_records_usage` / `test_compact_records_usage` 断言，admin/usage 视图也要重写"1 chat = 1 row"的预期。侵入面太大。
- 方案 C（spine 内部聚合 context manager）：要引入会话状态，spine 复杂度上升一档，但当前只有 chat / compact 两处需要。等以后 reflection consolidator / episode summarizer 真要多 round 时再加。
- 方案 A：LLMRequest 加一个 bool 字段，2 处 caller 加 `auto_record_usage=False`，1 处 spine 加 if-skip。改动 **最小**，向后兼容（默认 True）。

**额外发现**：DL.2 第一次跑 compact 测试时 4 个失败，原因是我在 `compact_request` 写了 `requires_capabilities=("chat", "tools") if tools else ("chat",)`，而测试 fixture 的 main profile 默认 capabilities 是 `["chat"]`。`tools` 是 provider-side 的支持事项（Anthropic / OpenAI / DeepSeek 都默认支持 function calling），并非要在 capability 列声明的能力。改回 `("chat",)` 后 11 个 compact 测试全部通过。

**验证**：

- 全量 `pytest -q`：1066 passed, 8 skipped（D-now 上线后 1064，+2 来自 DL.5 新增聚合契约回归测试）
- `grep -rn "_call_thinker\|_call_compact\|_call_slang\b\|_call_reply_gate" services/ plugins/ kernel/ tests/ admin/` → 无匹配
- `ruff check` 仅剩历史 pre-existing 错误，本次改动无新增

**影响范围**：

- `usage.db` 行为对外不变：1 chat / 1 compact 仍是 1 行，`call_type="main"` / `"compact"`
- `cache_diagnostic_history(task)` 现在能看到 main / compact 的逐 round 快照（之前 main / compact 不入 spine 路径，diagnostic 是空的）
- `last_cache_hit_pct_by_task["main"]` / `["compact"]` 字段从今天起开始有值（之前因为不走 spine 都是 None）
- 4 个 wrapper 删除是纯结构清理，无运行时变化

**后续动作**：

- D-later 已结束，spine 迁移整体收尾
- staging 灰度 3-7 天后对比 DeepSeek 后台 vs `usage.db` 的 per-task 分布，写灰度报告进 `docs/migrations/spine-2026-05-18.md` 新章节"灰度结果"
- 真实 per-task 数据出来再用来推 P1（profile 各自 cache_hit_pct 隔离监控）的目标线

---

## 2026-05-18 LLMRequest spine 迁移阶段 C + D-now 完成

**变更类型**：refactor / backend + admin-frontend

**变更内容**：

按 [docs/audits/prompt-cache-research-2026-05-18.md](docs/audits/prompt-cache-research-2026-05-18.md) §12.5 路线推进。共两个阶段：

**阶段 C — 11 处调用点迁移到 LLMRequest spine**：

- C.1 `services/style/extractor.py` → `task="style"`
- C.2 `plugins/bilibili/plugin.py` → `task="bilibili_intent"`
- C.3 `plugins/element_detector/plugin.py` → `task="element_detect"`
- C.4 `plugins/memo/plugin.py` → `task="memo"`
- C.5 slang ×4：extractor / review_utils / drift / semantic → `task="slang"` / `slang_review` / `slang_drift` / `slang_semantic`，删掉所有 `getattr(client, "_call_slang_*", ...)` 三级 fallback chain
- C.6 `services/llm/thinker.py` → `task="thinker"` 并实施 **P0-A**：mood/affection 从 system 前缀拼接移到 `dynamic_blocks` 末尾，static prefix 不再每次被污染
- C.7 / C.9 `compact` / main `chat()` → 推到 D-later。Omubot 的 compact 输入已是预压平的 user message（无历史 tool_result 块），§11.3 P2 audit 描述的 sentinel 替换在 Omubot 上无对象；compact 与 main `chat()` 共享"多 round 聚合为 1 行 usage.db"契约，spine 自动 record 路径会破坏现有断言，需先重谈聚合契约
- C.8 `plugins/chat/plugin.py /debug` ×2 → `task="chat_private"`；MemoExtractor 注入处由 C.4 已经覆盖

**阶段 D-now — wrapper 清理 + admin 视图 + 守门测试**：

- D.1a 删除 `_call_thinker` wrapper（无调用者）
- D.1b 迁移 `tests/test_client.py:186/192` 速率限制测试到 `_call(LLMRequest(task="slang"))`，删除 `_call_slang` wrapper
- D.1c 迁移 `kernel/router.py:884` semantic gate 调用 → 直接传 `ctx.llm_client._call`；`evaluate_semantic_gate` 内部构造 `LLMRequest(task="reply_gate")`；删除 `_call_reply_gate` wrapper；同步更新 `tests/test_reply_workflow.py` 两个 fake
- D.2 cancel-path 回归测试已存在于阶段 B（`test_spine_call_cancel_path_no_partial_record`），跑通验证
- D.3 写 [docs/migrations/spine-2026-05-18.md](docs/migrations/spine-2026-05-18.md) 迁移清单（全部调用点 / 旧 wrapper / 新 task / 新代码位置 / D-later 阻塞原因）
- D.4 admin/system 页 per-task 命中率：`SystemProviders.vue` 每个 task 行加 `命中 X.X%`，数据来自 `provider_rate_limit_payload().profiles[].last_cache_hit_pct_by_task`；后端 `admin/routes/api/providers.py` 在 profile payload 加 `last_cache_hit_pct_by_task` 字段
- D.5 cache diagnostic 后端 endpoint `GET /api/admin/providers/cache-diagnostic/{task}?limit=20`，返回 N 条 `{snapshot, diff}`；前端 UI 暂未独立面板，运维 curl 即可定位 break 轴
- D.6 admin task-profile 选择器旁加 capability 兼容性提示：所选 profile 缺少 task 所需 capability 时显示 `缺 chat/tools` warning tag
- D.7 新增 `tests/test_llm_task_admin_sync.py` 守门测试 ×3：保证 `services/llm/llm_request.py` 的 `LLMTask` Literal、`admin/.../types.ts` 的 `ProviderTaskKey` Literal、`SystemProviders.vue` 的 `providerTaskOrder` 数组与 `providerTaskLabels` 对象四方一致

**验证**：

- 全量 `pytest -q`：1064 passed, 8 skipped（阶段 C 上线前 1061 passed，+3 来自 D.7 守门）
- `vue-tsc --noEmit` 无错误
- `ruff check` 仅剩历史 pre-existing 错误，本次改动无新增

**配置面**：

- `last_cache_hit_pct_by_task` 字段对前端无侵入（旧前端会忽略），可灰度上线
- `/api/admin/providers/cache-diagnostic/{task}` 是新 endpoint，旧客户端不会调用，零风险

**影响范围**：

- usage.db 的 `call_type` 列从今天起开始出现 11 个新 task 名（style / bilibili_intent / element_detect / memo / slang / slang_review / slang_drift / slang_semantic / thinker（P0-A 后 prefix 才稳定）/ chat_private / reply_gate）
- thinker 调用的 cache prefix 命中率应从今天起改善（P0-A 修复了 mood/affection 拼接污染）
- `main` / `compact` / `proactive` 仍走 legacy 路径（D-later）

**后续动作**：

- D-later：重谈 `_record_usage` 聚合契约（方案 A: 加 `_omu_skip_auto_record`；方案 B: 接受多行 usage；方案 C: spine 内部聚合），完成 `_call_compact` 删除 + main `chat()` 迁移
- staging 灰度 3-7 天后对比 DeepSeek 后台 vs `usage.db` 的 per-task 分布，写灰度报告
- 待 P1-E 后再用真实 per-task 数据给出 cache 命中率验收阈值（不预设 55% / 60%）

---

## 2026-05-18 reply_gate 隐藏 bug 修复

**变更类型**：fix / backend

**变更内容**：

`kernel/router.py:884` 的 semantic gate 调用 `ctx.llm_client._call_reply_gate(...)`，但 `LLMClient` 从未定义此方法。配置 `reply_workflow.mode="semantic"` 已启用，每次实际调用都抛 `AttributeError`，被 `services/reply_workflow.py:394` 的 `except Exception` 静默吞掉，semantic gate 自上线起一直 fail-closed 返回 None，从未真正生效过。

修复（[services/llm/client.py](services/llm/client.py)）：

- 新增 `LLMClient._call_reply_gate` 薄 wrapper，与现有 `_call_thinker` / `_call_compact` / `_call_slang` 同模式（task=`"reply_gate"`，max_tokens 默认 96）
- `_call` 内部 deepseek thinking 自动禁用列表加入 `"reply_gate"`（避免 96-token 轻量决策被 reasoning 拖慢）

**配置面**：`config/config.json` 的 `task_profiles.reply_gate = "main"` 早就声明过，本次只补足代码侧的 method 实现。

**发现来源**：[docs/audits/prompt-cache-research-2026-05-18.md](docs/audits/prompt-cache-research-2026-05-18.md) §12.1 LLM 调用点盘点过程中发现。

**验证**：

- `.venv/bin/pytest tests/test_reply_workflow.py -q` 通过（20 passed）
- `.venv/bin/pytest tests/test_client.py -q` 通过（49 passed）

**影响范围**：semantic gate 终于能真正运作；group 路径的 reply 决策从"全部 fail-closed 放行"变为"按 LLM 判定"。运行时行为变化，需观察对群活跃度和误回复率的影响。

**后续动作**：本修复是 [prompt-cache-research-2026-05-18.md](docs/audits/prompt-cache-research-2026-05-18.md) §12.5 Step 0；接下来按 §12.5 路线落地 LLMRequest spine refactor。

---

## 2026-05-17 多层学习记忆研讨报告

**变更类型**：架构研究 / 审计文档

**变更内容**：

新增 [docs/audits/multilayer-memory-learning-report-2026-05-17.md](docs/audits/multilayer-memory-learning-report-2026-05-17.md)，对 Omubot 黑话、表达方式、知识库/记忆/图谱三层学习结构做外部研究与本地实现对照：

- 参考 Generative Agents、MemGPT/Letta、Reflexion、LangMem、Zep、Mem0、SillyTavern 等论文和成熟项目。
- 明确当前 Omubot 是“多层资料晚融合 + 局部治理耦合”，尚未达到真人式多层记忆。
- 汇总本地数据快照：knowledge index、slang、style、learning_normalizer、knowledge_graph 的当前沉淀情况。
- 提出后续路线：修通 style 反馈闭环、PromptBudgetManager、MemoryConsolidator、Episodic Reflection、图谱骨架化。
- 自审后已补充修复方案：采样说明、逐段来源链接、slang normalizer 接入待复核、style 反馈闭环修复、privacy/scope 硬门槛和 P0/P1 整改清单。

**影响范围**：仅文档，不改运行时代码。

**后续建议**：下一步优先处理 `StylePlugin` 缺 `reply` 权限和 style approved/profile 为空的问题，再讨论统一 prompt 预算层。

---

## 2026-05-17 备份体系服务层实施（Phase 1-4）

**变更类型**：基础设施 / 新功能

**变更内容**：

将外挂 cron 脚本备份方案升级为服务层集成的完整备份体系：

1. **Phase 1 — 最小可交付**：
   - `services/storage/backup.py`：BackupItem registry（24 项）+ BackupService（create/list/prune/inspect/restore）
   - `services/storage/backup_scheduler.py`：asyncio 定时调度，bot 启动即自动备份
   - `kernel/config.py`：新增 BackupConfig（enabled/daily_time/keep_days/default_profile）
   - `bot.py`：startup hook 启动 scheduler + 60s smoke test
   - `admin/routes/api/system.py`：替换 shutil.copytree 为 BackupService + 新增 settings/list API
   - `services/health.py`：registry-based SQLite 检查（8 DB）+ 备份新鲜度 + 磁盘占用
   - `scripts/backup-databases.sh`：改为 BackupService 薄包装
   - `tests/test_backup_service.py`：17 个单元测试全通过

2. **Phase 2 — Profile 体系 + Admin 配置面板**：
   - 4 个 profile：daily / pre-change / migration / diagnostic
   - Admin 系统页 SystemBackup.vue：profile 选择 + 备份历史 + 调度配置表单
   - GET/POST `/api/admin/backup/settings` 端点 + scheduler 热加载

3. **Phase 3 — 安全恢复流程**：
   - CLI `inspect`：漂亮打印 manifest
   - CLI `restore-plan`：只读恢复计划
   - CLI `restore --apply`：WAL checkpoint → pre-restore 备份 → 替换 → 清理 WAL/SHM → quick_check

4. **Phase 4 — Slang 专项恢复**：
   - `scripts/dev/slang_db_repair.py` 扩展：`rebuild-terms-from-revisions` + `validate` 子命令

**关键安全机制**：

- `.backup` API 失败即失败，不降级为 cp
- fcntl.flock 文件锁防并发
- 磁盘空间预检（1.5x 安全裕度）
- 原子 rename（同文件系统校验）
- WAL pre-checkpoint
- manifest sha256 + trusted 标记

**影响范围**：备份、健康检查、Admin 系统页、bot 启动流程

**回滚路径**：revert commit + 恢复旧 backup-databases.sh + 移除 bot.py scheduler hook

---

## 2026-05-17 slang.db 损坏修复 + 全库每日备份方案上线

**变更类型**：故障修复 / 运维基础设施

**事件经过**：

1. Phase 13 代码实施完成后，准备在备份 DB 上执行迁移脚本 dry-run。
2. `cp storage/slang.db /tmp/slang_backup.db` 后执行脚本报 `database disk image is malformed`。
3. 对源 DB 执行 `PRAGMA integrity_check` 发现大量 B-tree 页损坏（Tree 4/5/8/9/10/11/12/13/14/15/19 等数十个页面 `btreeInitPage() returns error code 11`）。
4. Docker 容器内 DB 为同一 bind mount，同样损坏。
5. `sqlite3 .recover` 可恢复 `slang_term_revisions`（2552 行）、`slang_observations`（3015 行）、`slang_settings`（10 行）、`slang_pending_candidate_keys`（275 行），但 `slang_terms` 表数据页全部不可读（0 行恢复）。
6. 检查 `storage/backups/` 下历史备份：均为 2026-05-11 标记为 corrupt 的旧快照（仅 130 条 term，远少于生产 ~998 条）。
7. 确认 `strings slang.db | grep term_id` 可见 5034 个片段 — 原始数据仍在磁盘页面上，但 B-tree 索引结构损坏导致 SQLite 无法遍历。

**修复方案**：

从 `slang_term_revisions` 表重建 `slang_terms`：
- 每个 `term_id` 取 `MAX(created_at)` 对应的 `after_json`（完整 term 快照）
- 解析 JSON 重建所有字段（term_id, term_key, term, meaning, aliases, scope, group_id, confidence, status, usage_count, unique_users, timestamps, source, repeat_policy, notes, meta）
- 结果：**747 条 term 重建成功**（approved 71 / candidate 536 / muted 138 / expired 2）
- 丢失约 250 条：无 revision 记录的早期数据（主要是 Phase 10 之前、revision 机制上线前创建的 term）

**执行步骤**：

```bash
# 1. recover 可读表到新 DB
sqlite3 storage/slang.db ".recover" | sqlite3 /tmp/slang_recovered.db

# 2. 从 revisions 重建 slang_terms（自定义 Python 脚本）
python3 rebuild_from_revisions.py  # → /tmp/slang_rebuilt.db

# 3. 在重建 DB 上执行 Phase 13 迁移
uv run python scripts/dev/slang_meta_migration_p02.py --db /tmp/slang_rebuilt.db --dry-run
# Migration 1 (backlog): approved=133, rejected=0, kept=34, total=167
# Migration 2 (daily): approved=48
# Migration 3 (human_reviewed): to mark=23

uv run python scripts/dev/slang_meta_migration_p02.py --db /tmp/slang_rebuilt.db
# Migration 1 (backlog): 167 rows updated
# Migration 2 (daily):   48 rows updated
# Migration 3 (human):   22 rows updated

# 4. 替换生产 DB
docker stop qq-bot
mv storage/slang.db storage/slang.db.corrupt-20260517
cp /tmp/slang_rebuilt.db storage/slang.db
docker start qq-bot
```

**备份方案上线**：

为防止再次发生不可恢复的数据丢失，新增每日自动备份：

| 项目 | 内容 |
|------|------|
| 脚本 | `scripts/backup-databases.sh` |
| 机制 | SQLite `.backup` API 热备份（不中断 bot、不锁表） |
| 调度 | crontab 每天 04:30 执行 |
| 存放 | `storage/backups/daily/YYYY-MM-DD/` |
| 保留 | 最近 7 天，超期自动清理 |
| 验证 | 备份后自动 `PRAGMA integrity_check` 验证 slang.db |
| 日志 | `storage/backups/backup.log` |
| 覆盖 | slang.db / messages.db / usage.db / style.db / memory_cards.db / knowledge_graph.db / knowledge_index.db / learning_normalizer.db（共 8 个，总计 ~36MB/天） |

恢复方式：
```bash
cp storage/backups/daily/2026-05-17/slang.db storage/slang.db
docker restart qq-bot
```

**影响**：

- slang_terms 从 ~998 条降至 747 条（丢失 ~250 条无 revision 的早期 term）
- Phase 13 迁移已在重建 DB 上执行完毕（ai_reviewed_at / ai_review_source / ai_review_decision / human_reviewed 字段已补写）
- bot 重启后正常运行，slang API 响应 200，消息收发正常
- 每日备份已生成首份快照并验证通过

**根因分析**：

- slang.db 此前已有多次损坏记录（2026-05-11 三次 corrupt 备份），说明存在持续性的写入异常
- 可能原因：① Docker bind mount + WAL 模式在 macOS 上的 fsync 语义差异；② bot 异常退出时 WAL checkpoint 未完成；③ 磁盘 I/O 错误
- 备份方案使用 `.backup` API 而非文件拷贝，可避免拷贝到 WAL 未 checkpoint 的不一致状态

**回滚**：

- 损坏的原始 DB 保留在 `storage/slang.db.corrupt-20260517`
- 如需回退备份方案：`crontab -r` 删除定时任务，`rm -rf storage/backups/daily/` 清理备份文件

**验证**：

- `sqlite3 storage/slang.db "PRAGMA integrity_check"` → ok
- `sqlite3 storage/slang.db "SELECT COUNT(*) FROM slang_terms"` → 747
- `docker logs qq-bot --tail 5` → Bot 就绪，消息正常接收
- `storage/backups/daily/2026-05-17/slang.db` 存在且 integrity_check=ok
- `crontab -l` → `30 4 * * * .../scripts/backup-databases.sh >> .../backup.log 2>&1`

---

## 2026-05-16（深夜）黑话治理 Phase 13 方案落入实施追踪

**变更类型**：文档 / 实施追踪同步

**内容**：

- 把六轮审计后定稿的方案写入实施追踪文档 `docs/slang-module-implementation-tracker.md`，开新阶段 Phase 13。
- 当前状态段更新：Phase 12 已完成；Phase 13 方案已收口（六轮审计完成），实施待 PR；当前阶段 = backlog reviewer 治理与 AI review 契约重构。
- 实施清单新增 Phase 13 共 16 行：P0-1（频次门槛）/ P0-2（AI review 契约 + SQL helper 拆分 + 历史 meta 迁移 + N7 全仓 grep）/ P0-3（kept streak 自动降级）/ P0-4（前端 tab 重排 + 砍作用域 + human_reviewed 迁移 + 互斥证明）/ O1-O6 第六轮审计实施级条目 / P1-1 / P1-2 / P2-1 / P2-2。
- 决策记录追加 6 条 2026-05-16 决策：① "AI 是否审过"与"AI 结论"用两个独立字段表达；② 迁移 CASE 直读 `backlog_review.approved` 不依赖最终 status；③ 第二段 daily 迁移 WHERE 用 `NOT LIKE ai_review_source`；④ LIKE 双格式纪律明文化；⑤ backlog mute 与人工 mute 用 revision 表 EXISTS 子查询区分；⑥ 大规模 meta 契约迁移按"自审 2 + 外部审计 2 + 修订后再审计 2 = 六轮"工序。
- 风险跟踪追加 9 条 Phase 13 风险（高 3 / 中 5 / 低 1），覆盖历史迁移误标、LIKE 单格式漏过滤、43 条 backlog approved 误打 human_reviewed、用户手动 mute 混入 AI 否决、N5 死代码、P1-1 死引用、反向重申误判、默认关 web search、kept streak 误 mute 真黑话。
- 更新日志追加 2026-05-16 收口条目，列出六轮审计的累计 28 项缺陷数量分布（gpt 5 / deepseek 8 / claude N1-N7 7 / claude O1-O8 8）和 P0 落地顺序（Day 1 = P0-2 契约 + 迁移；Day 2 = P0-1/3/4）。
- 顺手修了一个既有 lint 警告：line 75 `str | None` 含未转义的 `|` 把表格行变成 5 列，改为 `str \| None`。

**影响**：

- 下次会话或新 agent 接手 Phase 13 实施时，可以直接从 `slang-module-implementation-tracker.md` 看到完整待办、决策依据、风险清单，不需要再读 `slang-governance-research-2026-05-16.md` 的 670+ 行全文。
- 本次零代码改动，不动数据库、不动配置、不动 Docker；仅文档同步。

**回滚**：直接 `git checkout` 这两个文件即可，无运行时影响。

**验证**：

- 文档变更不需要运行时测试；lint 警告已就地修复。
- Phase 13 实施前的最低验证清单已落档在追踪文档"当前目标"段。

---

## 2026-05-16（晚）黑话治理方案外部审计落档

**变更类型**：文档 / 架构审计

**内容**：
- 在 `docs/slang-governance-research-2026-05-16.md` 追加“外部审计记录（2026-05-16）”，审计人标注为 `gpt`。
- 审计结论覆盖 P0 SQL 契约、AI 否决分桶、tab 计数验收、频次门槛验收指标和新增设置项前端触及范围。
- 补充建议 P0 顺序：先定义 AI review 契约和历史 meta 迁移，再做频次门槛、kept streak 和前端分桶。

**影响**：
- 后续实施黑话治理 P0 时，应先处理审计记录里的 High 项，避免 Day 1 验收 SQL 与生产分桶条件不一致。
- 本次只改 Markdown 文档，不改变运行时代码、配置或数据库。

**验证**：
- 文档已写入审计人 `gpt`。
- 未运行 pytest/前端构建，文档变更不需要运行时测试。

---

## 2026-05-16（晚）Wiki 覆盖最新项目状态

**变更类型**：文档 / Wiki / 交接信息更新

**内容**：
- 更新 `docs/wiki/Home.md`、`Architecture.md`、`Plugins.md`、`Configuration.md`、`Deployment.md`、`Slang.md`、`Knowledge-System.md`、`Commands.md` 与 `_Sidebar.md`，把当前版本、插件清单、配置主路径、Admin 能力和黑话 backlog reviewer 修复写入主 Wiki。
- 新增 `docs/wiki/Style-Learning.md`，说明表达学习的职责边界、数据模型、Admin/API、配置和验收重点。
- 新增 `docs/wiki/Conversation-Archive.md`，说明本地对话归档底座、scanner cursor、证据引用和留存 dry-run 策略。
- 同步刷新根目录 `wiki/` 的框架开发文档，移除 Phase 7 待实施、14 个插件、TOML 优先、单文件插件等旧表述。

**影响**：
- 后续会话可直接从 Wiki 了解 `v1.4.0` 当前状态：23 个本地包/能力包、manifest v3、JSON 配置契约、ContextPlugin system/locked、Style/ConversationArchive、Slang backlog slot 幂等修复。
- 本次只改 Markdown 文档，不改变运行时代码、配置或数据库。

**验证**：
- 已用 `rg` 扫描 Wiki 中的旧版本号、旧插件数和旧 Phase 表述；保留项仅为 legacy/说明性上下文。
- 未运行 pytest/前端构建，文档变更不需要运行时测试。

---

## 2026-05-16（晚）AI 清池死循环紧急修复 — backlog reviewer 缺 slot 幂等闸门

**故障现象**：
- 用户截图：清池跑完一轮后进度条立刻从 0/N 重新开始，token 持续消耗不停
- DB 验证：`backlog_review_state.active=true, processed=128/1020, started_at == last_done_at`
- 推算单轮成本：1020 条 × 1 LLM + 可选 1 web_search ≈ 70 万 tokens／轮，无任何冷却期

**根因**：
昨天首次实现 backlog_reviewer 时把 plan 中的"每天定点 2 次"自作主张升级成了"每 60s tick 都跑一批"。后续又在 `run_backlog_review_one_batch_if_due` 里加了"600s 内连跑直到清完"的循环。两个 bug 叠加：
- 第 N 个 tick 跑完一轮置 `active=False` + `last_done_at`
- 第 N+1 个 tick 没有"本 slot 已跑过"的闸门 → 看到 `active=False` + count > 0 → 重启新一轮
- daily_reviewer 同位置有 `last_daily_ai_review_slot` slot_key 幂等检查，backlog_reviewer 复制粘贴时漏掉了这层

**修复**（D1 同模式扫描 — 跟 daily_reviewer slot_key 模式对齐）：
- `plugins/slang/plugin.py` 的 `run_backlog_review_one_batch_if_due` 加 slot_key 幂等闸门：
  - 复用 `daily_ai_review_times` 时段配置（用户已有的"每天几点跑"设置）
  - slot_key = `f"{today}:{current_slot}"`，存到 `meta:last_backlog_review_slot`
  - 本 slot 已跑过 → `skipped: already_ran`
  - **关键不变量**：只有 `completed_in_session=True`（backlog 真的清空）才 mark slot；tick 超时半途而退不锁 slot，下个 tick 同 slot 续跑
- `reset_backlog_review` 同步清 `last_backlog_review_slot`，保证用户点"重置/重新开始"能立刻重跑

**节奏对比**：
- 修复前：每 60s tick 触发一轮，跑完立即重启 → token 无上限
- 修复后：每天 2 次（默认 04:00 / 16:00，用户可在 settings 调），单 slot 内连跑批直到清空或 tick 超时

**测试**：
- 新增 3 个回归测试到 `tests/test_slang_backlog_reviewer.py`：
  - `test_backlog_if_due_drains_pool_and_locks_slot` — 验证 slot 内能跑多批清空，跑完锁 slot
  - `test_backlog_if_due_skips_when_slot_already_ran` — 验证本 slot 重复 tick 跳过，新加候选也不重启
  - `test_backlog_if_due_does_not_lock_slot_on_partial_completion` — 验证半途而退不锁 slot
- 全部 26/26 backlog + plugin 测试通过
- 全量 `uv run pytest` 通过

**应急止血动作**（已执行）：
- 19:30 `docker compose stop bot` 物理切断 token 消耗
- 19:30 `UPDATE slang_settings SET value_json = json_set(value_json, '$.backlog_review_enabled', json('false'))` 兜底关开关

**部署**：
- 修复涉及 `plugins/slang/plugin.py`（.py 改动）→ 必须 rebuild bot
- `dot_clean . && docker compose up bot -d --build`
- 重启后把 `backlog_review_enabled` 改回 true（前端 settings 表单或直接改 DB）

**回滚**：关 `backlog_review_enabled` 即可

---

## 2026-05-16 AI 复核覆盖存量候选池 — 修复设计漏洞 1075 条永远积压

**背景**：
- daily_reviewer 只复核今天新抽词，不读 status='candidate' 存量；池子从 5/14 起累计 1075 条不收敛
- 包括 confidence=1.0 的"超舟"等高质量词，AI 复核日跑 2 次也碰不到

**改动**：
- 新增 `services/slang/backlog_reviewer.py`（SlangBacklogReviewer）：每批 50 条扫存量 candidate → 搜索 + LLM 判定
- approved → 升级为 approved；否决 → muted；模糊 → 保留待下轮
- `meta:backlog_review_state` 持久化游标，崩溃可续跑
- `on_tick` 接入新分支，每 60s 跑一批，约 22 分钟清完当前 1075 条
- admin 加 `backlog-review/status` / `run` / `reset` 三个 API
- 前端新增 `SlangBacklogProgress.vue` 进度条（5s 轮询）
- settings 加 `backlog_review_enabled` / `batch_size` / `min_confidence` 三项
- `daily_reviewer.py` 重构：`_assess` / `_search` / `_build_search_queries` 提到模块级 helper，两个 reviewer 共用

**验证**：
- `tests/test_slang_backlog_reviewer.py` 9/9 passed
- `tests/test_slang_plugin.py` 14/14 passed（回归无破坏）
- `vue-tsc --noEmit` 无错误
- `npm run build` 成功（SlangView-D_6tq28F.js 68.33 kB / gzip 19.10 kB）

**回滚**：关 `backlog_review_enabled` 即可（settings 默认开，但开关受用户控制）

**部署**：前端 bind mount 已生效；后端需 `dot_clean . && docker compose up bot -d --build`

---

## 2026-05-16 项目架构与 Wiki 梳理

**背景**：
通过阅读工作区 Wiki（`README.md`、`01-architecture.md`、`02-kernel-api.md`等）了解项目当前的架构设计与组件规范。

**核心梳理**：
1. **三层模型设计**：基于鸿蒙 OS 设计灵感，分层明确（内核层、系统服务层、插件层），内核保持零 I/O 且无外部依赖以维持架构隔离。
2. **PluginBus 调度机制**：通过统一的 Context 和 8 个钩子（如 `on_message`, `on_pre_prompt`）串联逻辑，内置 `_safe_call` 防崩溃/超时隔离。
3. **业务优先级控制**：遵循严格的降级执行流程（0为核心级别，向下递流至独立/实验性插件层），确保基础能力与扩展能力的隔离。

**后续**：
已对齐项目状态，将在后续开发中遵循此设计契约完成各模块的开发和运维工作。

---

## 2026-05-15（深夜 +5）SlangView PR C — panel-head 视觉收敛到 AppPanelSection

继 PR B-3 之后落 **PR C**：把仅剩的两块 `slang-panel-head` markup（SlangTermList 列表面板 + 主视图设置面板）迁到公共 `AppPanelSection`，B-3 多出来的 bundle 开销全部回吐 + 微净降。

**SlangTermList.vue**：339 → **308 行**（-31）

- 外层 `<AppCard bordered elevated>` + 手写 `<div class="slang-panel-head">` 改成 `<AppPanelSection eyebrow="Review Queue" title="黑话候选与词表">`
- 顶部分页 `<NPagination>` 通过 `<template #aside>` 槽位接到右上角，行为不变
- 删 4 块 scoped style（`.slang-list-panel / .slang-panel-head / .slang-eyebrow / .slang-title`，共 ~31 行）
- `AppCard` import 保留（term card / settings cards 仍在用）

**SlangView.vue（主视图）**：845 → **814 行**（-31，累计 2662 → 814，**-69.4%**）

- `<AppCard bordered elevated class="slang-settings-panel">` + 手写 panel-head 改成 `<AppPanelSection eyebrow="Advanced Settings" title="学习与注入">`
- 折叠按钮通过 `<template #aside>` 接入；折叠态/展开态业务逻辑（`showAdvancedSettings`）完全保留
- 删 `AppCard` import（已无引用），新增 `AppPanelSection` import
- 删 4 块 scoped style（`.slang-settings-panel / .slang-panel-head / .slang-eyebrow / .slang-title`，共 ~31 行），保留 `.slang-cache-revision / .slang-layout(--compact) / .slang-settings-collapsed-note` 与 1180px 媒体查询

**外部可观察证据**：

- `vue-tsc --noEmit` → exit 0
- `npm run build` → 4.93s
- `SlangView-*.js`：60.89 KB / gzip 17.34 KB → **60.63 KB / gzip 17.26 KB**（-0.26 / -0.08 gzip，B-3 +5.05 / +1.38 的开销首次出现回吐迹象，且对比起点 53.06 / 14.73 仍是 +7.57 / +2.53——这部分是 9 个子组件 scoped style 的固定成本，不再继续收敛）
- grep 验证：`slang-panel-head / slang-eyebrow / slang-title / .slang-list-panel / .slang-settings-panel` 五个 class 名在 admin/frontend/src/views/slang/ 下已全部消失

**累计四个 PR 的全景**：

| 阶段 | 主视图行数 | 减量 | 子组件数 | bundle KB / gzip |
| --- | --- | --- | --- | --- |
| 起点 | 2662 | 0 | 0 | 53.06 / 14.73 |
| B-1 helpers | 2320 | -342 (-12.8%) | 0（3 helpers） | 53.24 / 14.86 |
| B-2 只读 | 1864 | -456 (-19.7%) | 4 | 55.84 / 15.96 |
| B-3 交互 | 845 | -1019 (-54.7%) | 9（4+5） | 60.89 / 17.34 |
| **C 视觉收敛** | **814** | **-31 (-3.7%)** | 9 | **60.63 / 17.26** |
| 累计 | 814 | -1848 (**-69.4%**) | 9 | +7.57 / +2.53（固定成本，B-3 后已止涨） |

**回滚**：

- 仅 PR C 回滚：`git checkout HEAD -- admin/frontend/src/views/slang/SlangView.vue admin/frontend/src/views/slang/components/SlangTermList.vue` + `npm run build`，不动 9 个子组件结构。

**下一步**：SlangView 重构收尾。SystemView 同模板的 4 阶段（B-1 / B-2 / B-3 / C）走完，主视图剩下的 814 行 ≈ 11 API loader + 17 ref + 6 computed + 26 handler 的业务状态机，符合"不可拆的业务复杂度"边界。下一个候选可以挑表达方式 / 知识库 / Memo / 群管理里仍是单文件的视图。

---

## 2026-05-15（深夜 +4）SlangView PR B-3 — 5 个交互子组件抽离

继 [docs/tracking/web-refactor.md](docs/tracking/web-refactor.md) B-2 之后落 **PR B-3**：5 个交互子组件全部归位 `admin/frontend/src/views/slang/components/`，主视图再降 54.7%（累计 -68.3%）。

**新增 5 个 .vue**（行数为含 scoped style 的实际值）：

| 文件 | 角色 | 行数 |
| --- | --- | --- |
| `SlangTermList.vue` | 列表面板：drift mode / term list / bulk bar / 双 pagination；嵌 `SlangDriftCard`，emit `open-detail / quick-status / review-ai / drift-action / bulk-action` | 339 |
| `SlangGovernanceSection.vue` | 漂移治理 + 观察中候选两段 side-section；emit `switch-queue-mode` 跳漂移队列 | 144 |
| `SlangSettingsForm.vue` | 13 开关 + 14 数字 + 2 select + 2 textarea + 保存按钮；v-model:settings / allowlistText / stoplistText | 237 |
| `SlangCreateDrawer.vue` | 创建抽屉，词条信息 + 示例与备注两段 AppPanelSection；v-model:visible / draft | 144 |
| `SlangDetailDrawer.vue` | 详情抽屉五段：Editor / AI Review / Quality / History / Observations；v-model:visible / detailTerm / editAliases / mergeTargetId / mergeSearchText | 435 |

**主视图 SlangView**：1864 → **845 行**（**-1019 / -54.7%**），累计 2662 → 845（**-1817 / -68.3%**）。

- imports：删 `AlertCircleOutline / SearchOutline / TimeOutline` 图标（迁子组件）+ `AppDrawerHeader / AppDrawerLayout / AppPanelSection / EmptyState`（迁抽屉子组件）+ `isAiApproved / isHumanReviewed / needsHumanReview / revisionActionLabel / statusType / formatSearchQueries / formatTime / confidenceText`（迁子组件）+ `STATUS_OPTIONS / REPEAT_POLICY_OPTIONS`（仅子组件用），保留 `statusLabel`（merge options label 用）+ `DEFAULT_SLANG_SETTINGS / mergeSettings`（settings 状态机用）；新增 5 个子组件 import
- script：删 `selectedCount / pageSelectionChecked / pageSelectionIndeterminate` 三个 computed + `setPageSelection / toggleTermSelection / handleTermSelectionUpdate / termSelectionHandler` 四个函数（全部迁到 SlangTermList，主视图改用 `v-model:selected-term-ids` 直接同步）
- template：列表面板（含 bulk bar + drift list + term list + 双 pagination）+ 创建抽屉 + 详情抽屉（5 段 AppPanelSection）+ 治理段 + 设置表单 五块旧 markup 全部替换为子组件调用
- style：删 ~28 块 scoped class，主视图只保留 `.slang-cache-revision` + `.slang-layout(--compact)` + `.slang-settings-panel` + `.slang-panel-head` + `.slang-eyebrow` + `.slang-title` + `.slang-settings-collapsed-note` 七块共 ~70 行（PR C 把 panel-head 迁 `AppPanelSection` 后能再删一半）

**v-model 流向**（主视图 = 单一状态源）：

- `<SlangTermList v-model:page v-model:selectedTermIds>` ← 翻页 + 多选状态
- `<SlangAdvancedOverview v-model:expanded>` ← 折叠
- `<SlangQueueToolbar v-model:searchText / groupFilter / scopeFilter / queueMode / minConfidence>`
- `<SlangCreateDrawer v-model:visible v-model:draft>`
- `<SlangDetailDrawer v-model:visible / detailTerm / editAliases / mergeTargetId / mergeSearchText>`
- `<SlangSettingsForm v-model:settings / allowlistText / stoplistText>`

**外部可观察证据**：

- `vue-tsc --noEmit` → exit 0
- `npm run build` → 4.91s
- `SlangView-*.js`：55.84 KB / gzip 15.96 KB → **60.89 KB / gzip 17.34 KB**（+5.05 / +1.38 gzip，5 子组件 scoped style 复制造成的预期开销，与 SystemView B-3 +2.41 / +0.87 同量级；PR C 收敛 AppPanelSection 后会回吐部分）
- 行数对比与 SystemView B-3（1649 → 590，-1059 / -64%）按比例完全一致——SystemView 累计 3326 → 590 / -82%，SlangView 累计 2662 → 845 / -68%；剩余 845 行里 ~470 行是 11 个 API + 17 ref + 6 computed 业务状态机，这是不可拆的业务复杂度

**累计三个 PR 的全景**：

| 阶段 | 主视图行数 | 减量 | 子组件数 | bundle KB / gzip |
| --- | --- | --- | --- | --- |
| 起点 | 2662 | 0 | 0 | 53.06 / 14.73 |
| B-1 helpers | 2320 | -342 (-12.8%) | 0（3 helpers） | 53.24 / 14.86 |
| B-2 只读 | 1864 | -456 (-19.7%) | 4 | 55.84 / 15.96 |
| B-3 交互 | 845 | -1019 (-54.7%) | 9（4+5） | 60.89 / 17.34 |
| 累计 | 845 | -1817 (-68.3%) | 9 | +7.83 / +2.61（PR C 后回吐） |

**回滚**：

- B-3 完整回滚：`git checkout HEAD -- admin/frontend/src/views/slang/SlangView.vue`，再删除 5 个 B-3 子组件 `rm admin/frontend/src/views/slang/components/{SlangTermList,SlangGovernanceSection,SlangSettingsForm,SlangCreateDrawer,SlangDetailDrawer}.vue`，最后 `npm run build` 即可恢复（不动 B-2 的 4 个只读子组件）。

**下一步**：PR C 视觉收敛——把 4 块 `slang-list-panel` / `slang-settings-panel` 的 panel-head（eyebrow + title + 操作）迁到 `AppPanelSection`，删主视图剩余 7 块样式，bundle 预计回吐 1-2 KB / gzip 0.3-0.5 KB；目标主视图 ~750 行。

---

## 2026-05-15（深夜 +3）SlangView PR B-2 — 4 个只读子组件抽离

继 [docs/tracking/web-refactor.md](docs/tracking/web-refactor.md) B-1 helpers 之后落 **PR B-2**：4 个只读子组件全部归位 `admin/frontend/src/views/slang/components/`，主视图再降 19.7%。

**新增 4 个 .vue**（行数为含 scoped style 的实际值）：

| 文件 | 角色 | 行数 |
| --- | --- | --- |
| `SlangMetrics.vue` | 5 张 KPI grid（auto-fit minmax 156px，三段断点） | 83 |
| `SlangAdvancedOverview.vue` | 高级概览条（折叠开关）+ 3 张 stat 卡（热门/群活跃/抽取） | 200 |
| `SlangQueueToolbar.vue` | 队列 segment + 4 filter + 跨群扫描/重置/总数 tag，control-strip 装饰带完整保留 | 272 |
| `SlangDriftCard.vue` | drift 单卡（dual-use 预留：队列 drift 模式现已用，B-3 治理段会复用） | 149 |

**主视图 SlangView**：2320 行 → **1864 行（-456 / -19.7%）**

- imports：删 `MetricCard / CheckmarkCircleOutline / FlashOutline / CONFIDENCE_OPTIONS / SCOPE_OPTIONS / driftStatusLabel / runKindLabel`，新增 4 个子组件 import
- script：删 3 个 computed（`groupOptions / totalQueueCount / queueOptions`）；setQueueMode 保留（治理段"查看漂移队列"按钮还在用）
- template：metric grid / advanced strip / advanced cards / queue toolbar / 队列 drift 卡五块旧 markup 全部替换为子组件调用，drift 卡 emit `action(drift, accept|reject|alias|mute)` 给主视图调 `handleDriftAction`
- style：删 13 块 scoped class，保留治理段共享的 `pending-list / pending-row` 子集（B-3 还要用）

**外部可观察证据**：

- `vue-tsc --noEmit` → exit 0
- `npm run build` → 4.76s
- `SlangView-*.js`：53.24 KB / gzip 14.86 KB → **55.84 KB / gzip 15.96 KB**（+2.60 / +1.10 gzip，4 子组件 scoped style 复制造成的预期开销，与 SystemView B-2 +3.13 / +0.96 同量级；PR C 收敛 AppPanelSection 后会回吐部分）
- 行数对比与 SystemView B-2（2842 → 1649，-1193 / -42%）按"主视图占比"看在同量级，SlangView 因表单 + 抽屉两块体量大留在 B-3

**回滚**：

- B-2 完整回滚：`git checkout HEAD -- admin/frontend/src/views/slang/SlangView.vue`，再 `rm -rf admin/frontend/src/views/slang/components/`，最后 `npm run build` 即可恢复。

**下一步**：B-3 5 个交互子组件（SlangTermList / SlangGovernanceSection / SlangSettingsForm / SlangCreateDrawer / SlangDetailDrawer），主视图目标 ~600-700 行。

---

## 2026-05-15（深夜 +2）SlangView 拆分启动 — PR B-1 helpers 抽取

按 [docs/tracking/web-refactor.md](docs/tracking/web-refactor.md) SystemView 同模板（B-1 helpers / B-2 只读子组件 / B-3 交互子组件 / C 视觉收敛）启动 SlangView 拆分。本轮交付 **B-1**。

**B-2 / B-3 拆分定稿**（用户确认）：

- B-2 4 个：SlangMetrics / SlangAdvancedOverview / SlangQueueToolbar / SlangDriftCard（drift 单卡，列表 + 治理段双处复用）。
- B-3 5 个：SlangTermList / SlangGovernanceSection / SlangSettingsForm（治理段和表单单抽）/ SlangCreateDrawer / SlangDetailDrawer。

**B-1 改动**（admin/frontend/src/views/slang/）：

- 新增 `helpers/types.ts`（200 行）：11 interface + 3 type 全部从 SlangView.vue 抽出。
- 新增 `helpers/formatters.ts`（98 行）：formatTime / confidenceText / numberSetting / formatSearchQueries / DEFAULT_SLANG_SETTINGS / mergeSettings。其中 `mergeSettings` 改成 pure 函数，把 fallback 显式参数化（原版闭包 `settings.value`），主视图两处调用点改为 `mergeSlangSettings(payload, settings.value)`。
- 新增 `helpers/badges.ts`（114 行）：8 个标签函数（statusLabel/Type / driftStatusLabel / revisionActionLabel / policyLabel / runKindLabel / isAiApproved/isHumanReviewed/needsHumanReview）+ 4 options 常量（大写命名：STATUS_OPTIONS / CONFIDENCE_OPTIONS / SCOPE_OPTIONS / REPEAT_POLICY_OPTIONS）。
- `SlangView.vue` 主文件：删除 11 interface + 3 type + 13 函数 + 4 options 常量 + defaultSlangSettings；template 里 7 处 options 引用换大写 import 名。**2662 行 → 2320 行（-342 / -12.8%）**。

**外部可观察证据**：

- `vue-tsc --noEmit` → exit 0。
- `npm run build` → 4.91s。
- `SlangView-*.js`：53.06 KB / gzip 14.73 KB → **53.24 KB / gzip 14.86 KB**（+0.18 / +0.13 gzip，与 SystemView B-1 同等级的 helpers split 预期开销）。
- 行数对比与 SystemView B-1（3326 → 2842，-484 / -14.5%）在同量级。

**回滚**：

- B-1 完整回滚：`git checkout HEAD -- admin/frontend/src/views/slang/`，再 `rm -rf admin/frontend/src/views/slang/helpers/`，最后 `npm run build` 即可恢复。

**下一步**：B-2 4 个只读子组件（SlangMetrics / SlangAdvancedOverview / SlangQueueToolbar / SlangDriftCard）。

---

## 2026-05-15（深夜 +1）admin 系统页布局重构 — 删 dashboard 重合内容 + 资源上移 + Policies 加指引 + 低频页面整理

**用户反馈**：

1. 系统页有和 Dashboard 重复的"Bot 状态 / NapCat / 运行时长 / 活跃会话"卡，看着冗余。
2. "系统资源（CPU / 内存 / 磁盘）"位置过低，要滚一段才能看到，运维快速判断不便。
3. "运行策略"卡只展示数字，没说"防检测策略在哪里开 / 它的作用是什么"——新手看不懂。
4. SystemView 里 5 条"低频工具"链接（独立日程页 / 用量统计 / 沙盒 / 调度器 / 插件）要整理：日程已被 Dashboard 右栏完全覆盖、调度器已失效、沙盒和插件应该在一级导航。

**确认**（不可逆动作）：

- /schedule、/scheduler、/usage：**软下线**（只删导航入口和 SystemView 高级工具入口，保留路由 + .vue，便于回滚）。
- 沙盒：进 SideMenu 「设置与维护」组成为一级导航。
- Dashboard 同步补 cache 命中率 / 平均延迟 / 错误数 三个 /usage 独有指标（数据源 `usage_tracker.summary_today()` 已包含，无需改后端）。

**改动**（admin/frontend/src/，9 文件）：

- `views/system/components/SystemHero.vue`：删除 NapCat / 运行时长 chips（已在 Dashboard）；aside 两张卡改为「PID + 内存 + 线程」「活跃会话 + 转 Dashboard 提示」。仅保留版本 + 升级提示作为系统页独有信息。
- `views/system/components/SystemMetrics.vue`：**保留文件**（用户拒绝删除），但从 SystemView 移除 import 和使用——4 张 KPI 已被 Dashboard `statusBadges + dash-hero__kpi` 完整覆盖。
- `views/system/components/SystemPolicies.vue`：每个子卡增加 `system-stack__hint` 一行说明（版本号来源 / 防检测策略含义 / 发言倍率 config 路径）。防检测卡片新增「去配置·拟人延迟」按钮，跳 `/config?task=rhythm`。
- `views/system/components/SystemAdvancedEntry.vue`：description 从"这些页面和观测能力仍然保留"改为更准确的"LLM Provider 切换 / 协议探测 / 一键备份"；tools 网格在空数组时不渲染（之前会渲染空网格）。
- `views/system/SystemView.vue`：
  - 主区段顺序改为：Hero → **Resources（独占首屏）** → Maintenance → ServiceHealth → RuntimeErrors → **Policies（下沉）** → AdvancedEntry。删除 `.system-main-grid` 双列样式（资源现在独占整宽，Policies 在维护类信息之后单独成段）。
  - `advancedToolLinks` 从 5 条降为空数组（schedule/scheduler/usage 入口删除，sandbox/plugins 进侧栏一级导航）。
- `views/config/ConfigView.vue`：onMounted 新增 `applyRouteQueryNav()`，监听 `route.query.task` 变化；接收 `?task=rhythm` 等参数后自动选中对应 NavId。
- `layouts/components/SideMenu.vue`：「设置与维护」组里加「沙盒 /sandbox」入口（icon=TerminalOutline）；activeKey fallback 中分离 `/sandbox`（高亮自身）和 `/usage,/schedule,/scheduler`（fallback 到 /system）。
- `views/dashboard/DashboardView.vue`：
  - `DashboardUsage` interface 加 `cache_read_tokens / avg_elapsed_s / error_count`。
  - 加 3 个 computed：`todayCacheHitRate / todayAvgLatency / todayErrorCount`。
  - hero KPI 三联下加一条 `dash-hero__runtime` 紧凑行，展示 Cache 命中 / 平均延迟 / 今日错误。

**外部可观察证据**：

- `vue-tsc --noEmit` → exit 0。
- `npm run build` → 4.87s 通过。
- `SystemView-*.js`：53.35 KB / gzip 16.84 KB → **52.52 KB / gzip 16.68 KB**（删 SystemMetrics 引用 + 重排）。
- `DashboardView-*.js`：26.79 KB / gzip 9.55 KB（含三指标补充）。
- 路由 `/usage /schedule /scheduler /sandbox` 全部仍可手动访问；只是 schedule/scheduler/usage 不再出现在导航栏，sandbox 进入主导航。

**回滚**：

- 全部前端变更：`git checkout HEAD -- admin/frontend/src/{views/system,views/dashboard,views/config/ConfigView.vue,layouts/components/SideMenu.vue}`。
- 因为是 bind mount，回滚后 `npm run build` 即可恢复，无需 docker 重建。

**待用户验收**（dev 实测）：

1. /system 首屏：Hero（版本+进程基线）→ 系统资源（CPU/内存/磁盘进度条）→ 运维建议 → 服务健康 → 关键错误 → 运行策略（含防检测跳转按钮）→ 高级工具 toggle。
2. 防检测卡点「去配置·拟人延迟」：跳 `/config?task=rhythm` 自动选中"拟人延迟"任务。
3. /：Dashboard hero 下方多一条 Cache 命中 / 平均延迟 / 今日错误。
4. SideMenu「设置与维护」里有「沙盒」一项（紧跟插件）。
5. /schedule、/scheduler、/usage 仍能手动访问（软下线，保险起见）。

---

## 2026-05-15（深夜）admin Config 页整页重做 — 卡片错位 + 卡套卡 + list/kv 行错位

**背景**：用户反馈 `/admin/config` 页面"卡片错位、看着乱"。逐项核对后定位到三处实锤：

1. **卡片高度/留白参差**。`.config-section-grid` 用 `repeat(auto-fit, minmax(280px, 1fr))` 等分列，但 `.config-field__control { max-width: 520px }` 锁死控件宽度——同一行 switch 卡左下大片空白、input 卡满宽，观感不齐。
2. **object 字段卡套卡**。`field.kind === 'object'` 时递归调用 `ConfigFieldEditor.vue`，每层都自带 padding + border + radius + background，二层嵌套就有"盒中盒中盒"。
3. **list / kv 内部行错位**。`.config-field__list-item` 是 `1fr / auto`、`.config-field__kv-row` 是 `180px / 1fr / auto`，当 item_kind / value_kind 是 `switch` 时 1fr 列里全是空白，删除按钮和上一行控件不在同一垂直线。

附带顺手修的"新手够用度"短板：字段错误只走小红字，缺整张卡描红的强提示；没有"恢复字段到加载值"的动作；推荐值 chip 和当前控件视觉太贴。

**调研**：admin 项目早就有完整的 form-card 语言（`FieldGroup` + `AppPanelSection` + `AppCard` + `PageToolbar`），sibling 视图 GroupsView / MemoryView / PluginsView / SystemView 全部在用，**只有 Config 页自己造了一套**。重做的本质是让它"靠齐站点其它视图"，而不是发明新组件。

**改动**（admin/frontend/src/views/config/，9 文件，1 删 / 7 新建 / 1 重写）：

- `section-labels.ts`（**新建**）：`CONFIG_SECTION_LABELS` 字典 + `bucketForPath` + `bucketFields`。把扁平的 `task.paths` 按 namespace 分桶（llm / group_access / group / anti_detect / thinker / reply_segmentation / scheduler_concurrency / napcat / vision / access），分桶后每桶在视觉上对应一个 `AppPanelSection`。
- `ConfigField.vue`（**新建**）：基于 `FieldGroup` 的字段 dispatcher。switch/select/number 走 `inline` 模式（label 居左、控件居右），text/list/kv/json 走 stacked 模式。承载错误态（左红边 + helper 红字）、未保存态（左黄边 + 「已修改」标签 + 「撤销」action）、风险标签、重启提示标签。
- `ConfigListField.vue`（**新建**）：flex 行布局，switch item 不撑满（避免 1fr 列空白），input/number/select 撑满。删除按钮固定贴右。
- `ConfigKvField.vue`（**新建**）：grid `200px / 1fr / auto`；当 value_kind=switch 切到 `200px / auto / auto`，避免 switch 半行白。
- `ConfigObjectGroup.vue`（**新建**）：递归对象，**不画卡**，仅左 2px border + inline subhead。深度 ≥ 2 折成 `<details>`，顶层不再卡套卡。
- `ConfigSecretInput.vue` / `ConfigJsonInput.vue`（**新建**）：原 ConfigFieldEditor 内联的 secret 编辑切换、JSON parse-on-blur 行为抽出，便于单测和复用。
- `ConfigStatusStrip.vue`（**新建**）：顶部 4 联状态条独立组件。
- `ConfigView.vue`（**重写**）：rail + stage 布局保留；任务区 `task.paths → bucketFields() → AppPanelSection × N → ConfigField × N`。toolbar 改 `PageToolbar`。diff/backup/audit 都收纳进 `AppPanelSection` 统一 eyebrow/title。新增 `handleFieldRevert` 处理字段级撤销。
- `ConfigFieldEditor.vue`（**改成空壳**，加 `@deprecated` 注释，保留文件以防 stale import；用户后续可手删）。

**外部可观察证据**：

- `npx vue-tsc --noEmit` → exit 0，无类型错误。
- `npm run build` → 5.43s 通过；`ConfigView-*.js` 52 KB / gzip 17.5 KB，与原产物持平。
- 复用的共享组件全部走 `unplugin-vue-components` 自动注册（已确认 `components.d.ts` 包含 `ConfigField` / `ConfigListField` / 等新条目会在下次 dev 启动时刷新）。
- 关键设计参考的 admin 视觉锚点：FieldGroup（admin/frontend/src/components/common/FieldGroup.vue）的 inline=140px label / control / helper 三段；AppPanelSection 的 eyebrow + title + description + aside 槽。

**影响范围**：

- 仅前端 `/admin/config` 页面渲染层。后端 `/api/admin/config*` 全部不变（schema、values、preview、save、restore、history、backups 全沿用）。
- `types.ts` 不变，不影响其它使用 ConfigFieldSchema 的位置。
- `ConfigFieldEditor.vue` 留空壳，外部若仍有手写 import 会拿到一个 display:none 的占位，不会编译失败。

**验证清单**（待用户在 dev 环境复测）：

- "群聊回复"任务里 switch / number / text 三种字段 不再共享一行 grid，AppPanelSection 内单列堆叠，无半张空白。
- "完整配置"页 `vision.qwen.*` 这种 ≥2 层 object 改为左 border + 折叠，无卡中卡。
- "权限与私聊" admins kv 的 key 输入、value 输入、删除按钮三列对齐；当 value_kind=switch 时 switch 不再独占一整列。
- 修改某字段：右上角出现「已修改」+「撤销」，点撤销恢复加载时的值。
- 故意把 `llm.api_key` 留空预览/保存：FieldGroup 整张卡红左边、helper 红字。
- 响应式：1440 → 960 → 760，rail 折顶部、status 4→2→1 列、字段不溢出。

**回滚**：`git checkout HEAD -- admin/frontend/src/views/config/`。

---

## 2026-05-15（夜）SessionStart hook 重构 — 外置脚本 + 维护日志索引 + 修 cwd 路径 bug

**背景**：用户反馈"每次更新维护日志要很长时间"，怀疑日志过长。审计后发现：

1. 日志体量：3780 行 / 232 KB / 112 条。
2. SessionStart hook 已在做"只读最新一条 + 60 行上限"，**会话启动这一头不读全文**，不是瓶颈。
3. 真正卡的是 `Edit` 工具协议要求先 `Read` 整个文件再 diff——每次追加新条目都要过 3780 行，**改 hook 改不掉**，是工具协议层。
4. **顺手发现既存 bug**：原 inline hook 路径写的是 `omubot/maintenance-log.md`，但 cwd 实际是 `omubot/` 而不是 `OmubotWorkspace/`，**SessionStart 一直在静默失败**（错误信息 `[Errno 2] No such file or directory: 'omubot/maintenance-log.md'` 注入到上下文，但被旁边的 bot 日志稀释看不出来）。

用户选择"做目录索引"——给 agent 注入"最近 N 条标题 + 行号"清单，让 agent 在需要回顾时按 `Read offset=L` 精准定位，不用 `Read` 全文。

**改动**（2 文件，1 新建 + 1 编辑）：

- `.claude/hooks/session_start_status.py`（**新建**，128 行）：
  - 把 inline 长 Python 拆成独立脚本（settings.json 里 50 行转义字符串删掉）。
  - 路径改用 `Path(__file__).resolve().parents[2]` 锚定项目根，**和 cwd 解耦**——修了原 inline 的 cwd bug。
  - 新增 `_format_index()`：扫描所有 `## 20…` 标题输出 `L<行号>  <标题>` 索引（最近 15 条 / 共 N 条）。
  - 索引提示：`Read maintenance-log.md offset=N` 查看（路径相对当前 cwd 写）。
  - 标题超过 110 字符截断，每行宽度自动对齐。
- `.claude/settings.json` SessionStart 第一段：50 行 inline Python → 1 行 `python3 .claude/hooks/session_start_status.py`，timeout 保持 5s。

**外部可观察证据**：

- 脚本启动耗时：`time python3 .claude/hooks/session_start_status.py > /dev/null` → **0.029s**。
- 输出体量：9802 字节 / 121 行（含最新条目 75 行 + 索引 16 行 + bot log tail 40 行）。
- 索引样例：`L7  2026-05-15（晚）三起回溯事件复盘 …` / `L112 2026-05-15 黑话抽取 run 永远卡 running …` ……共 15 条。
- `python3 -c "import json; json.load(open('.claude/settings.json'))"` → 通过，hooks 键 `['SessionStart', 'PostToolUse']`。

**影响范围**：仅本仓库会话启动行为；运行时代码、构建产物、Docker 镜像不受影响。下一次新 session 启动即生效。

**回滚**：`git checkout .claude/settings.json && git rm .claude/hooks/session_start_status.py`。

**Lessons Learned**：

- 用户说"慢"时先量化瓶颈再动手——这次发现真正的慢在 Edit 工具协议层（强制 Read 全文），不在 SessionStart。
- 长 inline 脚本（多层转义的 Python in JSON）天然脆弱——拆外置文件时**顺带发现了 cwd 路径 bug**，否则可能再过半年才被注意到。索引功能是用户需求，cwd 修复是顺手收益。
- 后续如果维护日志继续膨胀（>500 条），再考虑按月归档拆分（方向 A）。

---

## 2026-05-15（晚）三起回溯事件复盘 — 同模式漏修 + SPA 迁移漏接 + 测试环境死锁

**背景**：当天发布了"slang run 卡 running"专项修复后不到 24 小时，
连续遇到 3 类不同性质的回溯，统一在此复盘。详细工作纪律已沉淀进
[docs/agent-discipline.md](docs/agent-discipline.md)。

### 事件 A — slang daily AI review 锁全天（同模式第二刀，最严重）

**现象**：00:08 配置的 daily AI review 启动了（sqlite 里有 run row），
但 status=abandoned、counters 全 0、`finished_at` 是 01:12 重启时被
stale-sweep 清的。`last_daily_ai_review_date` meta 已被写成 `2026-05-15`，
导致全天剩余 tick 都撞到 `if last_date == today: skipped="already_ran"`，
**当天没有第二次重试**。

**5-Why 根因**：

```text
Why1 status=abandoned + counters 0 → 任务半路死
Why2 任务半路死           → 旧镜像 _TICK_JOB_TIMEOUT_S=50s 杀掉它
Why3 50s 杀掉为什么锁全天 → set_meta(last_daily_ai_review_date) 写在 await
                            run_daily_ai_review() 之前——cancel 一次后 meta
                            已脏，下次 tick 看到 date==today 就跳过
Why4 为什么没在专项修复里发现 → 当天只盯"run 卡 running"修，没扫同模式
                                "await store.set_*(...) 写在长跑 await 之前"
Why5 为什么没扫           → 缺乏"同模式扫描"纪律，盯报错修表象
```

**改动**（2 文件）：

- `plugins/slang/plugin.py` `run_daily_ai_review_if_due`：
  - `set_meta(last_daily_ai_review_date)` 从"调用前"挪到"`result.ok==True` 之后"
  - 新增 `_daily_review_in_flight` 旗标，防 tick 间并发重入
  - cancel 路径让 `CancelledError` 自然冒泡（`finally` 释放 in-flight、不写 date）
- `tests/test_slang_plugin.py`：
  - `test_run_daily_ai_review_if_due_does_not_lock_day_when_cancelled`
    （wait_for 0.05s 强制 cancel，断言 meta 未被污染）
  - `test_run_daily_ai_review_if_due_does_not_lock_day_on_failure`
    （`_RaisingMessageLog` 模拟上游 raise，断言 ok=False 路径同样不锁全天）

**同模式扫描**（D1 纪律）：

```bash
rg -n 'await\s+self\.store\.set_meta' plugins/ services/
# 命中 4 处全部审过：
# - plugin.py:303 (本次修复点) — 已挪到 ok==True 之后
# - plugin.py:391 last_extracted_at — 在 try 内 success 路径里，cancel 不污染
# - daily_reviewer.py:241 last_daily_ai_review_at — finally 之外、success 路径，安全
# - 其它命中均为白名单（短跑 await，cancel 不影响语义）
```

**外部可观察证据**：

- pytest 13/13 (slang_plugin) ✅、32/32 (slang 4 文件) ✅
- 容器内代码校验：`docker compose exec bot grep -c _daily_review_in_flight plugins/slang/plugin.py` → 4 ✅
- 镜像 ID `omubot-bot:latest @ 3bfa861bb4d7`，bot Up + slang store init 成功

**回滚**：`git checkout plugins/slang/plugin.py tests/test_slang_plugin.py && docker compose up bot -d --build`。

**今天 daily review 不会再跑**：旧代码已经把 `last_daily_ai_review_date='2026-05-15'`
写进 meta 了，新代码不会重写。下一次自然触发是 **5/16 00:08**。
如果想立刻验证，需手动抹掉 meta 行（用户已选择不抹）。

### 事件 B — admin 表达方式页面消失（SPA 迁移漏接）

**现象**：用户报告 `/admin/style` 在侧边栏看不到了。

**根因**：`v1.4.0 release: ... admin SPA` 重构（commit `653b7b3`）时，
`StyleView.vue` 文件被复制到 `admin/frontend/src/views/style/`，但
`admin/frontend/src/router/index.ts` 没注册 `/style` 路由、
`admin/frontend/src/layouts/components/SideMenu.vue` 没加菜单项。
后端 `admin/routes/api/style.py` 一直健在并已 mount。
**前端文件存在 ≠ 用户能访问**——三件事缺二，三个月没被发现。

**改动**（2 文件）：

- `admin/frontend/src/router/index.ts`：在 `/slang` 之后追加 `/style` 路由。
- `admin/frontend/src/layouts/components/SideMenu.vue`：在「日常」组里
  「群内黑话」和「知识库」之间插入「表达方式」（`ChatbubbleEllipsesOutline` 图标）。

**外部可观察证据**：

- `cd admin/frontend && npm run build` 4.88s 通过，`StyleView-sDYX62D5.js (17.32 kB / gz 5.56 kB)` 输出到 `admin/static/assets/`
- HTTP 验证：`/admin/style` → 200、`/admin/assets/StyleView-sDYX62D5.js` → 200
- bind mount `./admin/static:/app/admin/static:ro` 让容器内立即生效（无需 rebuild）

**回滚**：`git checkout admin/frontend/src/router/index.ts admin/frontend/src/layouts/components/SideMenu.vue && cd admin/frontend && npm run build`。

### 事件 C — 全量 pytest 卡 5 分钟（环境性，非代码问题）

**现象**：连续两次 `uv run pytest` 卡 5 分钟无输出。

**根因**：`ps -ef | grep pytest | grep -v grep` 显示 11 个 PPID=1 的孤儿
pytest 进程（最早从凌晨 12:01 起就在内存里），跟 IDE 测试 explorer 启的新
pytest 抢同一个真实 sqlite 文件锁（`tests/test_slang_db_integrity.py` 用真实
路径 `Path("storage/slang.db")` 而非 `tmp_path`）导致互锁。

**处理**：`pkill -9 -f pytest` 清干净后，slang 4 文件 32 测试 1.57s 全过。

**Lessons Learned**：

- 跑全量 pytest 前先 `pkill -9 -f pytest`（已沉淀进 D5）。
- 优先跑 `tmp_path`-only 的测试集（如 slang_plugin/store/drift/semantic）规避真实 DB 锁。

---

## 2026-05-15 黑话抽取 run 永远卡 running、计数全 0 — 修复 CancelledError 收尾漏洞

**现象**：
admin 控制台 `/api/admin/slang/extract/runs` 看到最近 7 条 `slang_extraction_runs` 全部 `status=running`、`scanned/extracted/promoted` 都是 0，`finished_at` 为 NULL。但 sqlite 总表里历史上还有 488 条 success / 2 条 failed / 93 条 abandoned，说明不是从来没跑过 —— 是某个时间点起开始一律卡住。

**5-Why 根因**：

```text
Why1  status 卡 running        → finish_extraction_run() 从未调用
Why2  finish 没调用            → run_manual_extract / SlangDailyReviewer.run 半路退出
Why3  半路退出                  → asyncio.wait_for(timeout=50s) 触发 CancelledError
Why4  CancelledError 没被收尾   → 业务层 except Exception 不抓 BaseException 子类
Why5  50s 超时根本不够          → 12 个群 × LLM 抽取 + 复核根本跑不完
```

旁证：`storage/logs/bot_2026-05-14*.log` 反复出现 `slang tick job timeout | timeout=50s`，每 30 分钟一次，跟卡死的 7 条 run 时间戳完全吻合。

**改动文件**（5 个改 + 2 个测试新增）：

- `plugins/slang/plugin.py`
  - `_TICK_JOB_TIMEOUT_S` 50.0s → 600.0s（12 群 × LLM 调用现实预算）
  - `run_manual_extract`：`except` 拆出 `asyncio.CancelledError` 分支，把 `finish_extraction_run` 移到 `finally` 并用 `asyncio.shield` 保护，防止超时取消 finish 任务本身
  - `on_startup`：调用新的 `store.mark_stale_running_runs()` 清扫上一次进程崩溃留下的 orphan run
  - `_run_tick_jobs` 里 `asyncio.TimeoutError` → `TimeoutError`（builtin 别名，UP041）
- `services/slang/daily_reviewer.py`：同样的 CancelledError 分支 + finally + shield 重写
- `services/slang/store.py`：新增 `mark_stale_running_runs(status='abandoned')`，给定状态把所有 running 行收尾、写 finished_at 和 duration_ms
- `admin/routes/api/slang.py`：fallback 路径（plugin 不在线时）也复刻同样的 finally 兜底；顶部 import asyncio
- `tests/test_slang_store.py`：新增 `test_mark_stale_running_runs_closes_orphan_runs`
- `tests/test_slang_plugin.py`：新增 `test_run_manual_extract_finishes_run_when_cancelled`（用 `_SlowLLM` + `asyncio.wait_for(timeout=0.05)` 触发 CancelledError，断言 status='cancelled' 且 finished_at 非空）+ `test_on_startup_marks_orphan_running_runs_abandoned`（模拟两次 boot 之间的 leak）

**数据回填**：

```sql
-- sqlite3 storage/slang.db
UPDATE slang_extraction_runs
   SET status='abandoned',
       finished_at=datetime('now','localtime'),
       error='process restart while running (backfilled)'
 WHERE status='running';
-- 7 rows updated → 100 abandoned / 2 failed / 488 success
```

旁注：回填后还会出现 1 条 running，那是当前正在运行的旧版 bot 进程的 tick；它要等 bot 重启加载新代码才会被新逻辑收尾。

**验证证据**：

```text
pytest scoped (slang 全套):  36 passed / 0 failed
pytest full:                 978 passed / 8 skipped / 0 failed in 9.63s
ruff (改动文件):              All checks passed
pyright (改动文件):           0 错误（store.py 剩余 reportOptionalSubscript 全为 pre-existing）
sqlite 状态:                 abandoned=100 (含 7 条回填) / success=488 / failed=2
```

**回滚**：

- 代码：`git revert <commit-hash>`
- 数据：回填只是把 status 从 running → abandoned，无破坏；要还原直接 `UPDATE ... SET status='running', finished_at=NULL, error='' WHERE status='abandoned' AND error LIKE '%backfilled%'`

**部署后行为变化**：

- bot 重启后，旧版进程留下的 1 条 running 会被 `on_startup` 清扫为 abandoned
- 下一次 tick（默认 30 分钟）抽取超时上限从 50s 提到 600s，12 个群完整跑完一轮的预算够了
- 即使将来再因为别的原因被 cancel，run 行也会带 `status=cancelled / error='extraction cancelled (timeout or shutdown)' / finished_at` 完整收尾，admin 页面再也不会看到「永远 running、计数全 0」

---

## 2026-05-15 清理 7 项预存测试失败 → 全量绿（975 passed / 0 failed）

**触发**：上一会话 silent_learn 修复完成时遗留 7 个测试失败，全部为本任务范围外但影响 CI 信号可信度，授权一次性平掉。

**根因（按测试分组）**：

1. **`tests/test_slang_db_integrity.py`（6 测试 ImportError）**
   - 测试期望完整的「数据库损坏容灾」契约：损坏库 → store 抛 `SlangDatabaseCorruptError` → plugin 禁用但不 crash → API 返回 503 + `error_code/repair_script/db_path` 结构化错误。
   - 缺失：错误类、`SlangStore.init` 的损坏抓取、Plugin 容灾路径、API 503 转换。
   - 修复：
     - 新增 `services/slang/errors.py` 定义 `SlangDatabaseCorruptError(db_path, original)`。
     - `SlangStore.init` 用 `try/except aiosqlite.DatabaseError`，损坏时抛特定异常并保证 `_db=None`（`initialized=False`）。
     - `SlangPlugin.on_startup` 捕获该异常，置 `store=None`、记录 `_slang_disabled_reason`，`register_tools()` 返回空，bot 不 crash。
     - `admin/routes/api/slang.py` 用 `APIRoute` 子类（`_SlangCorruptGuardRoute`）拦截端点 raise 的 `SlangDatabaseCorruptError`，返回 503 JSON。

2. **`tests/test_image_cache.py`（4 测试 image processing error）**
   - 根因：本机 macOS 没装 libvips（`brew install vips` 未执行），`pyvips` import 时 `OSError: cannot load library 'libvips.42.dylib'`。Docker 镜像里有 libvips，CI 也应有；本地缺失是开发环境差异，**不是代码 bug**。
   - 修复：测试模块顶部 `try/except (ImportError, OSError)` 探测 libvips，加 `_requires_libvips` skipif marker 给 4 个真正需要 pyvips 的测试。其余 7 个测试不动，依旧能在缺 libvips 时跑。

3. **`tests/test_segmentation.py::test_debug_split_uses_new_segmenter_and_reports_reasons`**
   - 根因：`/debug split` handler 还在用旧函数 `_split_naturally`（只输出"分段数:"），但测试期望新切分器 `segment_reply` 的输出（"策略:"、"切分原因:"）。
   - 修复：`plugins/chat/plugin.py::_handle_debug_split` 改为调 `services.llm.segmentation.segment_reply`，输出策略与各段原因。

4. **`tests/test_sticker_tools.py::test_save_sticker_bot_steal`**
   - 根因：`SaveStickerTool.execute` 把所有「非管理员发起」一刀切拒绝，但产品语义需要区分三种情况：
     - admin 自己调 → `source="admin"`
     - 群聊（无 user_id）但 `requested_by` 是 admin → `source="admin"`
     - 用户消息触发 bot 主动收（user_id 是普通用户但 `requested_by=admin`）→ `source="stolen"`
   - 修复：execute 重写授权逻辑，按上述三态分流。

5. **`tests/test_mood.py::test_no_anomaly_has_empty_reason`（全量跑才挂）**
   - 现象：单独跑 mood 文件通过，全文件按顺序跑则挂。前序测试 `test_anomaly_can_flip_label`(anomaly_chance=1.0) 设了 `profile.anomaly_reason="虽然日程..."`，下一个 `anomaly_chance=0.0` 测试居然继承到了。
   - **根因（5-Why）**：
     1. 为什么 anomaly_reason 没清空？→ 因为 `_compute` 拿到的是 `_DEFAULT_MOOD` 的同一引用。
     2. 为什么是同一引用？→ `if/else: profile = _DEFAULT_MOOD`（`mood.py:162/164`）直接赋值无 copy。
     3. 为什么 `_lookup_base` 路径就没问题？→ 它末尾 `return MoodProfile(...)` 显式 copy。
     4. 为什么 fallback 路径没 copy？→ 早期实现遗漏，`_MOOD_BASE` 字典查询路径也是同一引用问题。
     5. 为什么测试今天才发现？→ 测试运行顺序+当前 hour=0 触发 fallback 分支，恰好 mutate 了模块级单例 `_DEFAULT_MOOD.anomaly_reason`，污染了下一个测试。
   - 修复：抽 `_copy_base()` static method，所有 `_compute` 取 base 都走 copy。`_lookup_base` 同时改用此方法保持一致。

**改动文件**：

- 新增：`services/slang/errors.py`
- 修改：`services/slang/__init__.py` `services/slang/store.py` `plugins/slang/plugin.py` `admin/routes/api/slang.py` `services/tools/sticker_tools.py` `plugins/chat/plugin.py` `plugins/schedule/mood.py` `tests/test_image_cache.py`

**验证证据**：

- 修复前：1 errored（collection）+ 6 failed → 修复后 0 failed
- 全量 pytest：`975 passed, 8 skipped in 9.47s`（8 skipped = 4 image_cache pyvips 缺失 + 4 sticker 预存 skip）
- ruff：仅 1 个预存 `UP041`（`asyncio.TimeoutError`，本次未引入），其他 86 个预存错误未引入新增

**回滚**：所有修改保留向后兼容（`SlangPlugin` 损坏时静默禁用而非 raise；`SaveStickerTool` 老调用方仍可用）。需要回滚时 `git revert` 单 commit。

---

## 2026-05-14 silent_learn 模式被 element_detector 击穿（紧急修复）

**变更类型**：incident / fix

**故障现象**：群 717096900「烬染无夜」配置 `presence_mode = silent_learn`（仅学习不发言），但 14:57:31 element_detector 命中规则后触发 `element+llm` 路径并实际发送消息：

```text
2026-05-14 14:57:31 INFO plugins:on_message:142 - element+llm |
group=717096900 轩(3057089539) reply='阴间死二是这样的，而我们崩坏玩的可就多了'
```

**根因（5-Why）**：

1. silent_learn 群下 element_detector 仍然主动发了消息 → 它没检查 `ctx.allow_speaking`
2. 为什么没检查？没有任何插件检查这个字段（echo / food / element_detector 三家全部漏检）
3. 为什么三家都漏？没有强制约束——`AmadeusPlugin.on_message` 契约里没声明"silent_learn 是否能跑"
4. 为什么这样设计？bus 不知道哪个 interceptor 会主动 send / 改 trigger
5. **真根因**：`router.py` 把 `bus.fire_on_message` 放在 `if not allow_speaking: return` **之前**，而 `fire_on_message` 内部对所有 `on_message` 一视同仁——silent_learn 模式下整条 interceptor 链路照样被全量触发

**修复**：内核统一门控（不让插件自查，因为新插件容易再忘）

- [kernel/types.py](kernel/types.py) `AmadeusPlugin` 加类属性 `silent_safe: bool = False` —— 默认 False 是关键，新增 interceptor 默认不被信任，写注释说明：「on_message 仅当满足 *只读、不发消息、不改 ctx.trigger、不触 scheduler.notify* 时才能设 True」
- [kernel/bus.py](kernel/bus.py) `fire_on_message(ctx, *, silent_mode=False)` 加 `silent_mode` 参数；`silent_mode=True` 时只调用 `silent_safe=True` 的插件
- [kernel/router.py](kernel/router.py) 把 silent_learn 的早返回路径调整为：先调 `bus.fire_on_message(msg_ctx, silent_mode=True)`（让 slang 之类纯学习插件继续记录），再写日志/timeline，然后 return；active 群仍走原 `fire_on_message(msg_ctx)` 全量链路
- [plugins/slang/plugin.py](plugins/slang/plugin.py) `SlangPlugin.silent_safe = True` 显式声明（它的 on_message 只 `record_hit` 写库，无副作用）
- 其他 4 个 interceptor（echo / element_detector / food / bilibili）保留默认 False —— bilibili 虽然只 "return False" 但会改 `ctx.trigger` 触发回复，所以 silent_learn 下也必须跳过

**为什么不是让插件自查**：用户决策走"内核统一门控"。如果让插件自查 `if not ctx.allow_speaking: return False`，每个新写的 interceptor 都得记得加，第六个插件再忘一次就再炸一次。把约束放到契约层，新插件默认 `silent_safe=False` 等于默认安全。

**验证**：

- `uv run pytest --ignore=tests/test_slang_db_integrity.py` 967 通过 / 6 失败（全部是预存与本次无关：image_cache / segmentation / sticker_tools）
- `tests/test_plugin_bus.py` + `tests/test_slang_plugin.py` 全部 55 通过
- `docker compose up bot -d --build` 后 bot 正常启动，element detection 规则正常装载（`rules=4`），后续 5 分钟日志中无任何 `element+llm` 或 `element |` 触发记录，silent_learn 群（717096900 / 625618470 / 805836168 / 1092460228 / 477640404 / 963085812）的所有消息均走"收消息 silent_learn"路径

**回滚**：单 PR git revert 即可，无数据库迁移。

**遗留**：`tests/test_slang_db_integrity.py` 预存 ImportError（`SlangDatabaseCorruptError` 未导出）和 6 个失败用例不在本次 scope，独立排查。

---

## 2026-05-14 Admin 前端重构 阶段 3 — LoginView 重构

**变更类型**：refactor / ux / security

LoginView 是阶段 3 的第 4 个视图（计划 §6.1 顺位 7，实际作为剩余视图里"最小先跑通模板"提前到本轮）。完成动作：

**美观（视觉一致性）**：

- 模板继续走"双层构图 + 雾青渐变 + 玻璃磨砂特征卡"骨架，内容沿用 [TheLogo](admin/frontend/src/components/common/TheLogo.vue) + [AppCard](admin/frontend/src/components/common/AppCard.vue) 公共组件
- 间距全部对齐 token 体系（4 / 8 / 12 / 16 / 24 / 32），删除原来的 14 / 18 / 28 / 34 等异常值；圆角对齐 12 / 16 / 24（24 仅卡片例外）
- chip 与 feature 卡片背景从 `rgba(255, 255, 255, 0.28/0.34)` 改为 `color-mix(in srgb, var(--om-surface) 70%, transparent)`，浅深主题自适应（旧值在深色下偏白发灰）
- 删除原 `.dark .login-card` 复式渐变背景兜底（依赖 themeOverrides 自动派生 cardColor）

**易用（交互细节）**：

- 自动 focus 输入框（`onMounted` + `nextTick`），键盘党直接打字
- Caps Lock 实时检测（`KeyboardEvent.getModifierState('CapsLock')`）→ 输入框下方提示
- 显示"上次登录时间"（`localStorage.getItem('admin:lastLoginAt')`），登录成功时回写
- 失败时卡片左右抖动动画（`@keyframes login-card-shake`，0.36s），尊重 `prefers-reduced-motion`
- 提交按钮 label 三态切换：默认 / 验证中 / 锁定倒计时
- input `autocomplete="current-password" spellcheck="false"`，配合密码管理器但不提示拼写

**安全（防滥用 + 可见警告）**：

- `auth.login` store 改为返回 `{ ok, error: 'invalid_token' \| 'network_error', status }`，前端区分"Token 无效"和"后端不可达"两类错误（旧版混在 catch 里给一句"网络错误"，掩盖真实原因）
- 失败计数 → 5 次连续失败锁定 30 秒（`COOLDOWN_THRESHOLD = 5`、`COOLDOWN_SECONDS = 30`），按钮 disabled + label 显示倒计时
- 非 HTTPS 检测：当 `location.protocol === 'http:'` 且 hostname 不在 localhost / 127.0.0.1 / ::1 白名单内，卡片头部显示警告条"Token 将以明文传输"。本机运维不打扰，外网暴露时立刻可见
- 错误提示带尝试计数（"Token 无效（已尝试 N/5）"），让用户知道距离锁定还有几次

**规模**：431 行 → 423 行（功能 +5、私写 CSS 节奏对齐 token 后微缩）。`vue-tsc` 0 error，`vite build` 4.83s 通过，新 LoginView 在 entry chunk 内（`AppCard` + `TheLogo` 跟主 bundle 一起加载，无额外 split）。

`docker compose up bot -d --build` 后 `/admin/` HTTP 200、`/api/admin/me` HTTP 401（无 cookie 时正确）。napcat 容器未触碰。

后端 auth 链路（[admin/auth.py](admin/auth.py) 中间件 + [admin/routes/api/auth.py](admin/routes/api/auth.py) `/login` `/logout` `/me`）未改动 — 锁定逻辑只在前端，避免新增服务端状态。这是一个权衡：前端锁定能被绕过（开新标签页就能重置 `failureCount`），但后端 ADMIN_TOKEN 不变下被穷举的风险靠的是 token 本身的熵，前端冷却只是"善意提示"，不是真正的限流。如果未来要做服务端限流，应在 `AdminAuthMiddleware` 加滑动窗口计数（不在本次重构 scope）。

跟踪表 [docs/tracking/web-refactor.md](docs/tracking/web-refactor.md) 同步：阶段 3 LoginView 标记 ✅，剩余 ConfigView / SystemView / SlangView 三项。

---

## 2026-05-14 Admin 前端重构 阶段 1 / 阶段 3 部分收尾

**变更类型**：refactor / chore

用户视觉验收三件套通过：

1. `/admin/design-playground` 浅 / 深主题 — 公共组件（StateBadge / LogPanel / DataToolbar / FieldGroup）渲染正常
2. DashboardView — 重写后的 Hero + KPI + Sparkline + LogPanel 视觉验收通过
3. GroupsView — 三 Tab 抽屉 + 概览条 + 门禁分流视觉验收通过

收尾动作：

- **删除 [admin/frontend/src/styles/global.css](admin/frontend/src/styles/global.css) 7 个冗余 `!important` 块**（41 行，含 `.dark .n-card / .n-input / .n-select / .n-tag / .n-drawer / .n-modal / .n-data-table`）。这些规则在 [stores/app.ts](admin/frontend/src/stores/app.ts) 的 `buildThemeOverrides()` common token（cardColor / inputColor / modalColor / tableColor / borderColor / textColor1-3 / placeholderColor）已经全部覆盖，是阶段 1 验收完成前的临时兜底。`!important` 计数 51 → 31，剩余 31 处全部在 keep 区（`.dark .n-button:not(...)` + `.dark .n-menu` 系列 deep 选择器，themeOverrides API 不能表达）
- `vue-tsc` 0 error，`vite build` 4.70s 通过，bundle 体积无变化

跟踪表 [docs/tracking/web-refactor.md](docs/tracking/web-refactor.md) 同步更新：阶段 1 任务 1.2 标记 ✅、`!important` 审计表所有 redundant 行改"已删除"、阶段 3 三个完成视图（DashboardView / LogsView / GroupsView）的"等用户视觉验收"标记改为"用户视觉验收通过"、补回缺失的 LogsView 完成段、剩余视图编号 3-6 修正为 4-7。下一步进入阶段 3 剩余视图，建议顺序 LoginView → ConfigView → SystemView / SlangView。

---

## 2026-05-14 Admin 静态资源缓存策略分流（修复刷新慢）

**变更类型**：performance

之前 `admin/__init__.py:admin_static_file` 给所有 `/admin/static/*` 一律 `Cache-Control: no-store, max-age=0`，包括 Vite 输出的 `assets/<name>-<hash>.js|css`。这些文件名本身带 8 字符内容 hash，内容变 hash 就变，本来就是 immutable 的资产。结果浏览器每次刷新都重下 ~75 个 chunk / ~1.6MB——慢的不是带宽是 RTT × 请求数。

修复：[admin/\_\_init\_\_.py](admin/__init__.py) 拆头分流——

- `_immutable_asset_headers()` → `public, max-age=31536000, immutable`
- `_headers_for_asset(path)` → `assets/` 前缀走 immutable，其余（favicon.svg 等根级文件）维持 `no-store`
- `index.html` 通过 `_spa_index_response()` 仍走 `_spa_headers()`（`no-store` + `clear-site-data: "cache"`），entry 每次都新鲜，所以 hash 变了的 chunk 永远能被新 index.html 引用到

curl 验证：`/admin/` 返回 `no-store, max-age=0`；`/admin/static/assets/*.js` 返回 `public, max-age=31536000, immutable`；`/admin/static/favicon.svg` 仍 `no-store`。第一次访问后刷新只下 index.html（< 1KB）+ 任何变了 hash 的 chunk，其余走 disk cache。

**坑点回填**：第一次 rebuild 后用户反馈"还是慢"。再排查发现 `_spa_headers()` 给 index.html 还挂着 `Clear-Site-Data: "cache"`——浏览器每次访问 `/admin/` 都会把整个 origin 的缓存（包括刚标 immutable 的 hash bundle）一并清空，immutable 头形同虚设。删掉这个指令后才真正生效。Hash 文件名本身就保证 entry 引用变化时旧 chunk 不会被复用，根本不需要 `Clear-Site-Data` 强清。

---

## 2026-05-14 Admin SSE 群活动实时推送 + group access refactor 收尾 + rapidfuzz 依赖补齐

**变更类型**：feature / refactor / dependency / infra

### Admin SSE 群活动实时推送（Q2）

之前 Groups 页 last_message_at / 24h 计数只在 onMounted 拉一次，刷新靠手动。这次把现有 `/api/admin/events` SSE 通道扩成事件驱动：

- 新增 `services/admin_events.py` — 进程内 broker（`publish_group_message` + `subscribe`/`unsubscribe`），bounded queue 防 stall，publisher 永不阻塞。kernel 不能 import admin，所以 broker 落在 services 这个共同依赖层。
- `kernel/router.py:_collect_group_context` — 自环过滤后立即 `publish_group_message`，覆盖 silent_learn / blocked_users 拦截的群消息也能进 admin。
- `admin/routes/api/events.py` — 重写：1s tick + drain group queue → `event: group_message`；30s 推一次 `event: group_activity` snapshot 做对账（`MessageLog.group_activity_summary`）。心跳/scheduler 仍 10s 节流。顺手修了 `log_sink_queue` 的 None-guard pyright 报错。
- `admin/frontend/src/composables/useSSE.ts` — 新增 `onGroupMessage` / `onGroupActivity` 订阅 API（EventTarget 转发，避免 logs 数组膨胀模式被复制）。
- `admin/frontend/src/views/groups/GroupsView.vue` — `useSSE()` 保活 + `onGroupMessage` 增量更新 last_message_at / 24h 计数 / 用户消息数；`onGroupActivity` 用 server-authoritative snapshot 覆盖防漂移。

**端到端验证**：35s SSE 窗口抓到 `group_message` × 10、`log` × 10、`heartbeat` × 4，活体群消息进入 admin 通道。`event: group_activity` 未在窗口出现是因为 30s 边界未踩上，逻辑上必然推送。

### group access / presence refactor 收尾

启动事故根因：工作区里有未提交的 `kernel/router.py`（652 行 diff）已经按"GroupConfig 加 access/presence 字段、ResolvedGroupConfig 加 presence_mode/access_allowed、MessageContext 加 allow_speaking/group_presence_mode/group_access_allowed"重写，但配套的 `kernel/config.py` + `kernel/types.py` 未同步进工作区——导致 `--build` 烤进半成品镜像，每条群消息都 `AttributeError: 'ResolvedGroupConfig' has no attribute 'presence_mode'` / `'GroupConfig' has no attribute 'allows_learning_group'`。

排查路径：先怀疑是 SSE hook 的 `presence_mode=resolved.presence_mode` 引入的，删掉后第二轮报错指向 `MessageContext` 的 kwarg，再删后第三轮报错指向 `allows_learning_group`——警觉这是一整套未完成的 refactor 而不是单点 bug，停手。在 `git stash@{0}` 里找到了配套的 `kernel/config.py`（含 `GroupAccessConfig` / `GroupPresenceConfig` / `presence_mode` / `access_allowed` / `allows_learning_group` / `allows_active_group` / `presence_mode_for` / `active_access_allowed`）和 `kernel/types.py`（`MessageContext` 加三字段），用 `git checkout stash@{0} -- kernel/config.py kernel/types.py kernel/router.py` 完整恢复 refactor 三件套。容器启动干净，群消息正常处理。

### rapidfuzz 依赖补齐

未提交的 `services/learning_normalizer/normalize.py` 引入 `from rapidfuzz import fuzz`，但 `pyproject.toml` 没声明，导致 build 出来的镜像启动时 `ModuleNotFoundError`。在 `pyproject.toml dependencies` 里加 `rapidfuzz>=3.0.0`，`uv lock` 解到 v3.14.5。

### 上一会话遗留：Q1 set Bot 群名片只改 web 显示

Q1 在前次会话（之前还没 compact）里完成的代码改动这次确认硬盘里都在：

- `admin/routes/api/groups.py` — `_verify_bot_card` / `_verify_group_remark`（`get_group_member_info(no_cache=True)` 回查 Napcat 真值）+ `_build_full_group_payload(inventory_override=...)`（防止 `_discover_groups()` 用 stale `get_group_list()` 覆盖 verified value）+ `set_bot_card` / `set_group_remark_endpoint` 重写为 verify-then-warn。
- `admin/frontend/src/views/groups/GroupsView.vue` — 收到 `data.warning` 时用 `message.warning(..., {duration: 6000})` 而非 success。

这次重 build 之后 Q1 改动也随容器生效。

### 影响范围

- 所有群消息处理路径（router refactor 收尾）—— `kernel/router.py:_collect_group_context` 现在依赖 `GroupConfig.allows_learning_group/allows_active_group` 两个新方法，`ResolvedGroupConfig` 多了 `presence_mode`/`access_allowed`，`MessageContext` 多了 `allow_speaking`/`group_presence_mode`/`group_access_allowed`。
- Admin Groups 页：last_message_at / 24h 计数在 1s 内随群消息更新，30s 全量对账。
- 依赖链：新增 rapidfuzz>=3.0.0（learning_normalizer 模糊匹配）。

### 回滚方案

如果 group access refactor 的逻辑分歧太大要重审：`git stash` + `git checkout HEAD -- kernel/router.py kernel/types.py kernel/config.py`，回到 v1.4.0 baseline。SSE 6 行（`publish_group_message` + `import` + `services/admin_events.py` + `events.py` 重写 + `useSSE.ts` 扩展）独立于 refactor，可单独保留——回滚后只需把 `_collect_group_context` 里的 `publish_group_message` 调用搬到旧版本对应位置即可。

### 事故复盘

- 未提交工作区改动 + `docker compose --build` 是雷区：build 把工作区当时的"瞬时半成品"烤进镜像，跟 git tag 含义脱节。下次 release 应当 `git add` 后 commit 再 build。
- `git stash` 用于"隔离对照"是反模式——pop 失败一次就把所有改动卷走。要做 baseline 对比，应该用 `git diff HEAD` / `git show` 而不是 stash。

---

## 2026-05-14 LogsView 二轮重设计 + Docker 磁盘事故恢复

**变更类型**：frontend / UX / infra

### LogsView v2 重设计

上一版 LogsView（commit 8197e60）完成了组件层清理，但用户反馈视觉仍有三处问题：

1. 工具栏"全部等级 + 搜索"上下折行，不整齐
2. 侧栏 26 个日志文件扁平铺开，dream 噪音盖过 bot
3. 文件模式进入黑底终端风格，和浅色实时流视觉割裂

本轮重写 [admin/frontend/src/views/logs/LogsView.vue](admin/frontend/src/views/logs/LogsView.vue) 完整解决：

**工具栏单行化**：

- 等级筛选改自研 Segment 段式按钮组（`默认 / ERROR / WARNING / INFO / DEBUG` 5 个 chip），高度 30px、与搜索框对齐
- 搜索框自研轻量实现，左内嵌 SearchOutline 图标 + 可点清除按钮，focus 时主色边框 + 2px 光晕
- "默认"模式自动隐藏 DEBUG（降噪决议），其他等级精确匹配
- 筛选变更时显示「重置筛选」按钮，一键回归默认
- 右侧「暂停流 / 清屏」两个 `size="small"` 按钮紧凑排列

**侧栏折叠分组**：

- 按文件名前缀自动分 `bot` / `dream` / `other` 三组
- Bot 默认展开，Dream 默认折叠，点击组标题切换
- 每行压缩到 32px 高，只显示相对日期（今天 / 昨天 / 前天 / N 天前 / MM-DD）
- 今天产生的 bot 日志自动带 `活跃` 绿色 tag
- Chevron 旋转动画指示折叠状态

**文件模式视觉归一**：

- 去掉 macOS 三点装饰 + 黑底终端
- 改用 `--om-surface-2` 浅面板（深色主题 `--om-surface-3`），与 LogPanel 对齐
- 结构化解析文件行（兼容两种格式：`MM-DD HH:MM:SS 系统 | msg` 和 `MM-DD HH:MM:SS [INFO] kernel | msg`），按 time / level / channel / msg 四列 grid 对齐
- 等级色标：ERROR 红、WARNING 橙、SUCCESS 绿、INFO 蓝、DEBUG 65% 透明度
- hover 时行背景微亮
- 无法解析的续行用 continuation 样式缩进显示

LogsView 从 583 → **1175 行**（净增约 600 行，主要是文件行解析 + 结构化 CSS + Segment/Search 自研）。功能与视觉质量都大幅提升。

### Docker 磁盘事故恢复

重构 build 中遇到 `rpc error: ... input/output error`，最初以为是 containerd bug。系统诊断后真相是：

- 宿主机磁盘 228GB 用了 205GB，**仅剩 886MB（100% 满）**
- Docker.raw sparse 文件实际占 32GB
- build 写产物时磁盘满 → 部分 blob 写入失败但 metadata 已创建 → 后续读操作 IO error
- **这不是 Docker bug，是磁盘满导致**

恢复流程（不同阶段用户批准）：

1. 用户手动清理 `~/.cache` 腾 2.9GB 应急空间
2. `osascript -e 'quit app "Docker"'` 优雅退出 Docker Desktop
3. `kill -TERM` 处理 60s 不响应的 backend 进程（vmnetd 系统助手保留）
4. `open -a Docker` 重启，daemon 10s 响应
5. containerd 重启后 metadata 一致性自愈，`docker images` 恢复正常
6. `docker image prune -f` 清悬空镜像，**回收 12.85GB**
7. 宿主机可用空间：2.9GB → **79GB**
8. `docker compose up bot -d --build` 成功，bot 6s 内就绪

### 验证

- `vue-tsc --noEmit` → 0 error
- `vite build` → 4.80s
- `docker compose up bot -d --build` → 成功
- Bot 就绪 `[INFO] kernel | Bot 就绪，开始接收消息 ✓`
- 浏览器侧验收：用户确认新 LogsView 排版与视觉符合期望

### 教训

- 宿主机磁盘监控应作为运维前置检查（可纳入 system/health 端点）
- `docker compose up --build` 失败时应检查宿主机磁盘，不要直接推论 Docker 本身损坏
- Docker Desktop backend 不响应 quit 信号时再用 `kill -TERM` 而非 `-KILL`，保留 vmnetd 等系统级助手

**影响范围**：

- LogsView.vue 一个文件变动；无其他前端文件受影响
- Docker 状态从"悬空镜像 + 磁盘 100% 满"清理到健康，可用 79GB
- Bot 中断约 10 分钟（Docker 重启期间容器下线）后恢复

**下一步**：

- 用户视觉验收 LogsView v2 通过后，进入 GroupsView 拆分
- 考虑把宿主机磁盘 +  Docker.raw 实际占用纳入 `/api/admin/system/health` 监控项

---

## 2026-05-14 阶段 3 第二个视图：LogsView 重构

**变更类型**：frontend / UX

**内容**：

阶段 3 清单里剩 LogsView（606 行）/ LoginView（431 行）/ GroupsView（1833 行）。今天做 LogsView，跳过 LoginView（已用 AppCard + TheLogo 自带设计稿，改动空间小），GroupsView 量大需子组件拆分留到下一轮。

### LogsView 重构要点

[admin/frontend/src/views/logs/LogsView.vue](admin/frontend/src/views/logs/LogsView.vue)：606 → **583 行**（净减 23 行，但组件复用度大幅提升）。

- 实时流渲染改用公共组件 [LogPanel](admin/frontend/src/components/common/LogPanel.vue) —— 删掉 60 行手写 `<div v-for>` / autoscroll / stick-to-bottom 实现；LogPanel 负责渲染+自动滚+暂停；视图层只负责筛选+快照冻结
- 文件模式保留 `<pre>` 渲染（LogPanel 针对结构化 entry，不适合纯文本尾部查看）
- 左右栏物理顺序改为「主栏在前 → 侧栏在后」，删 CSS `order: 1/2` 反转 hack
- 状态徽章统一换为 [StateBadge](admin/frontend/src/components/common/StateBadge.vue)（SSE 在线 / 文件模式 / 实时流模式）
- 按钮改 `size="small"`，与 PageToolbar 节奏一致
- LogsView 从"自给自足"变成"消费公共组件"，后续 LogPanel 有任何增强（高亮、过滤、虚拟滚）只改一处

### 行为零回归

- 实时流 SSE 继续消费 `useSSE()`
- paused 逻辑：`paused=true` 时把当前 sseLogs 冻结到 pausedSnapshot（LogPanel 只负责停止 autoscroll，不冻结数据，快照由视图层管理）
- 等级筛选 + channel/message 搜索沿用原实现
- 清屏 / 切换文件 / 重新读取 / 返回实时流 四个按钮行为一致
- 文件模式最近 500 行读取逻辑不动

### 验证

- `vue-tsc --noEmit` → 0 error
- `vite build` → 4.80s
- `docker compose up bot -d --build` 成功，bot 正常就绪
- 浏览器手动验证留给用户：SSE 流是否正常滚、暂停后能否看到冻结、文件模式 pre 渲染正常、浅深主题无塌陷

### 不做的

- **LoginView 不动** — 它已经用 AppCard + TheLogo，设计稿完成度高，没有冗余组件需要替换，改了收益低
- **GroupsView（1833 行）** — 需子组件拆分（GroupsToolbar / GroupsList / GroupsDetailDrawer / GroupsActions），单次改动风险大。留到下一轮用 codex 协同 spec 批量推

**影响范围**：

- 仅 LogsView.vue 一个文件
- 前端零 API 改动、零后端影响
- LogPanel 组件本身未改

**下一步**：

- 用户视觉验收 LogsView
- 通过后启动 GroupsView 子组件拆分，采用「先写 spec → 子组件分片做」的方式
- LoginView 如果后续要改，单独立项

---

## 2026-05-14 codex 协同流程干跑验证 + 修三个 spec 漏洞

**变更类型**：process / docs

**内容**：

在 codex 协同流程（见上条）真正派单之前，Claude 做了一轮干跑，发现并修复 3 个会让 spec 失败的漏洞。

### 发现的问题

1. **验收命令用 `git diff main` 不成立**：当前工作区相对 main 有 253 处未提交改动（整轮 admin 重构 + codex handoff 文件本身）。任何基于 `git diff main` 的"只动某某文件"校验都会永远失败。
2. **spec 预期标注数字不对**：spec 假设"6-7 块 `@audit redundant`"，实际只有 **1 个**标注注释（line 199），下面覆盖 7 个选择器。
3. **grep 误匹配说明性文字**：`global.css` 文件头 line 134 的文档注释里**字面出现** `@audit redundant` 字符串（解释用途），朴素 `grep -c '@audit redundant'` 会把它算成标注。

### 模拟验证

Claude 本地执行 `sed -i '' '199,244d'` 模拟 codex 删除：

- `!important` 从 51 → **31**（`@audit keep` 的 NButton + NMenu 两块占了约 30 处，这是下限）
- `vue-tsc --noEmit` 0 error
- `vite build` 4.72s 通过
- 文件功能上无损

改完立刻 `cp /tmp/global.css.bak admin/frontend/src/styles/global.css` 回滚，`!important` 恢复 51。工作区零残留。

### Spec 修补

修改三份文件：

- [.claude/handoff/TASK-20260514-01-remove-redundant-important.md](.claude/handoff/TASK-20260514-01-remove-redundant-important.md)
  - 精确指定 line 199-244 为删除范围
  - 期望值 `!important` 改为精确等于 **31**（不是 ≤ 20）
  - 验收用 `grep -c '^/\* @audit redundant'` 排除文档注释
  - 验收用 `git diff HEAD` 不用 `git diff main`
  - 用户复制命令段加入 `git stash push -u` 保护 dirty worktree
  - 合并段改为不 merge 回 main（main 严重落后），留作分支
  - 备注段记录干跑结果
- [.claude/handoff/TEMPLATE.md](.claude/handoff/TEMPLATE.md)
  - 模板 7 步流程统一改为 `git diff HEAD`、`git stash push/pop`、不合并回 main
- [.claude/handoff/README.md](.claude/handoff/README.md)
  - 解释为什么用 `git diff HEAD` 不用 `git diff main`

### 现状

- TASK-20260514-01 已可直接用 codex 执行，spec 里 6 条验收命令全部能在命令行 0/非 0 判断
- TEMPLATE 适配当前 dirty-worktree 实况，不需要用户每次起 task 前先 commit 一大批
- 用户仍未实际派单 codex —— 流程校验本身不消耗 codex 配额

**影响范围**：

- 仅 `.claude/handoff/` 三份文档与本 log 条目
- 零代码改动（干跑时模拟删除已完全回滚）
- 不影响构建、运行时、其他任务

**下一步**：

- 用户可随时按 TASK-01 "用户复制命令段" 派 codex 试跑
- 如果第一次跑通，后续 spec 按 TEMPLATE 批量产出

---

## 2026-05-14 引入 codex 协同工作流 + 第一个 handoff spec

**变更类型**：process / docs

**内容**：

建立 Claude（决策 + 审查）+ codex（机械执行）的协同机制，目标是把规则明确、判断密度低的改动分流到 codex，节省成本。

新增目录与文件：

- [.claude/handoff/](.claude/handoff/) — 存放交给外部 AI 执行的任务规范
  - [README.md](.claude/handoff/README.md) — 目录用途、命名规范、生命周期、审查流程
  - [TEMPLATE.md](.claude/handoff/TEMPLATE.md) — spec 模板，含「用户复制命令段」7 步流程（建分支 / codex 执行 / 验证 / 贴 diff 给 Claude / 合并 / 丢弃重来）
  - [TASK-20260514-01-remove-redundant-important.md](.claude/handoff/TASK-20260514-01-remove-redundant-important.md) — 第一个实战 spec：删除 `global.css` 里 6 块标注 `@audit redundant` 的 CSS 规则，期望 `!important` 从 51 降到 ≤ 20

协同工作流的核心设计：

- spec 必须满足：动的文件精确到路径、不准动的明确列出、验收命令可在终端 0/非 0 判断、不含"优雅地处理"这种需要判断的词
- 每个 task 走独立 git 分支，便于失控时一把丢弃
- 用户操作全部浓缩成「复制到终端」的命令块，不需要改字符
- codex 交付后用户把 `git diff main` 贴给 Claude 审查，审查要点放在 spec 底部
- 审查清单自动核对："动的文件"之外未改、"不准动"列表 0 diff、验收命令全 OK、无残留 TODO/console.log

适合给 codex 的活儿定义（基于当前 repo）：

- `!important` 审计清理（已成为 TASK-01）
- 照 Dashboard 样板迁移 Logs / Login 骨架
- SlangView / SystemView 子组件拆分（接口明确时）
- 给新端点补 pytest
- 内联样式 → UnoCSS shortcut 批量替换
- `<p class="help">` → `<FieldGroup helper="…">` 机械替换
- 后端响应 schema → 前端 `interface` 类型同步

不给 codex 做：视觉设计 / 信息架构 / 跨层贯穿改动 / 调试 / 鉴权相关。

**影响范围**：

- 纯新增目录与文档，零代码变更
- 不影响构建与运行时
- 第一个 task（TASK-01）待用户决定何时用 codex 试跑

**下一步**：

- 用户按照 TASK-01 的「用户复制命令段」试一次 codex，验证工作流是否顺手
- 跑完后根据体验调整 TEMPLATE.md（比如验证命令的粒度、审查要点清单项）

---

## 2026-05-14 Dashboard 新增「今日学习收录」模块 + 右侧竖版日程时间线

**变更类型**：frontend / backend / UX

**内容**：

### 右侧竖版日程（之前两栏布局落地）

- 主布局改两栏：左主栏 + 右 320px sticky 长条
- 日程改为垂直时间线（圆点 + 连接线 + 当前段主色光晕）
- 心情由 4 条横向 progress 改 4 行竖向 label+bar+%
- "今日主题 / 心情标签 / 下一段"整合到右栏顶部卡片
- <1200px 塌成单栏

### 新增后端 [admin/routes/api/learning.py](admin/routes/api/learning.py)

- 路由 `GET /api/admin/learning/today` 聚合今日学习活动
- 数据源：
  - **slang**：直查 `slang.db`，统计 `approved_today / reviewed_today / pending / today_hits`，返回今日新入库 Top 5（term + meaning + time）
  - **style**：直查 `style.db`，统计 `approved_today / reviewed_today / pending`，返回今日新入库 Top 5（style + situation + scope）
  - **stickers**：读 `storage/stickers/index.json`，按 `created_at` 过滤今日新入库，返回 Top 5（title + usage_hint + HH:MM）
- 时区处理：slang/style 的 `updated_at` 用 Asia/Shanghai 存，LIKE 前缀匹配安全；stickers 的 `created_at` 是 UTC，需先解析 datetime 再转 UTC+8 判断，避免漏早 8 点前的数据
- 容错：任一源异常只返回该源的 `error`，不 500 整个端点
- 注册于 [admin/routes/api/__init__.py](admin/routes/api/__init__.py)

### 新增前端 Dashboard「今日学习收录」模块

插在"Top Groups + 待处理"行下方、关键日志上方。三栏等分：

- **黑话卡**：大号数字"新入库"+ `今审 / 命中 / 待审`三个次级指标（待审用 warning 色）+ 今日新入库 Top 5 时间线（时间 · 词条 · 含义），点击跳 `/slang`
- **表达风格卡**：同构，数据来自 style，点击跳 `/style`
- **表情包卡**：大号数字"新入库"+ `总库 N` + 今日新入库 Top 5（时间 + 24px 缩略图 + 描述），点击跳 `/stickers`

每张卡 hover 高亮边框 + 微上浮 1px。今日无入库时显示"今天还没有新入库"占位，不让卡片崩塌。

### 冒烟测试

用真实 storage 直接调 collectors：

- slang 今日 `approved_today=6 reviewed_today=65 pending=127 today_hits=19`
- style 今日 `approved_today=0 reviewed_today=0 pending=20`（今天尚未审风格）
- stickers 有今日新入库

**验证**：

- `vue-tsc --noEmit` → 0 error
- `vite build` → 4.75s
- Python collector 冒烟测试通过，返回真实数据
- docker compose up bot -d --build 成功，`/api/admin/learning/today` 响应 401 说明已挂载到鉴权路由（浏览器侧带 session 即可访问）

**影响范围**：

- 新增 1 个后端路由 + 1 个 Dashboard 模块，其他页面零影响
- 直查 sqlite 而非调 store，避免干扰 store 状态
- sticker index.json 只读，不产生写入

---

## 2026-05-14 DashboardView 信息密度与视觉重构

**变更类型**：frontend / UX

**内容**：

按 [docs/web-refactor-plan.md](docs/web-refactor-plan.md) 阶段 3 推进，DashboardView 是第一个重做的视图。目标：信息密度提升、视觉层级更清晰，易用性明显改善。

新增组件：

- [admin/frontend/src/components/common/SparklineChart.vue](admin/frontend/src/components/common/SparklineChart.vue) — 纯 SVG 微型面积图，零第三方依赖（不引 ECharts / Chart.js）。接受 values + labels，自动画出渐变填充的趋势曲线，底部 2 行累计与峰值统计。24h 调用曲线用它渲染。

重写 [DashboardView.vue](admin/frontend/src/views/dashboard/DashboardView.vue)（1043 → 约 1070 行，信息量翻倍，代码行略增）：

1. **Hero 瘦身**：顶部合并状态徽章（Bot / NapCat / SSE / 更新时间）+ 大号标题 + 副说明 + 行内"今日主题 / 心情 / 下一段"一行三态，取代原来两张副卡重复陈列。
2. **Hero 右侧挂 3 张 KPI**：今日调用 / 活跃群 / 待处理，取代原来下方满屏 6 张 KPI 平铺。
3. **新增"24 小时调用曲线"面板**：`/admin/usage/data?period=day` 拉取时序，SparklineChart 渲染，带累计+峰值小字。
4. **新增"近 7 天活跃群 Top 5"**：`/admin/usage/data` 的 `top_groups`，显示群号、调用次数、相对占比条，点击跳转 `/groups`。
5. **待处理 + 学习信号合并**：原单列 todo 改成 todo + 黑话/风格信号双层面板。学习信号新增今日触达 / 已入库 / 启用画像等指标。
6. **新增 Style 待审入口**：原来只接 slang，现在也带 style pending。
7. **日程 + 心情合并到第 3 行**：心情从占半屏的 4 条 progress meter 压缩为 4 枚 pill chip，腾出空间让日程用 auto-fill 网格铺开。
8. **关键日志用新 LogPanel**：替代手写日志列表，自动暂停/清屏/自动滚、等宽字体、level 上色。
9. **视觉层级**：Hero 用 `--om-hero-gradient` 建立锚点；KPI / 面板 / 时段用 token 阴影与圆角统一（12/18）；浅深主题下 hero-kpi 背景自适配。
10. **交互升级**：Top Groups 行可点（跳 /groups）、待处理行可点（跳对应详情）、"去日志页"按钮下沉到 LogPanel 动作槽。

API 依赖（全部沿用已有接口，零后端改动）：

- `/api/admin/dashboard` — uptime / usage / mood / schedule
- `/api/admin/health` — bot / napcat 运行状态
- `/api/admin/services/health` — alerts / maintenance_window
- `/api/admin/slang/summary` — 黑话统计
- `/api/admin/style/summary` — 风格统计
- `/admin/usage/data?period=day` — 24h timeseries + top_groups

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` → 0 error
- `./node_modules/.bin/vite build` → 4.69s，DashboardView chunk 20.85 KB（gzip 8.21 KB）。相比改前 +30%，由引入的公共组件贡献，总 index 未变。
- `docker compose up bot -d --build` → bot 容器正常重建启动，napcat 未动。日志显示 `group inventory refreshed | total=4 learning=4`、`Bot 就绪，开始接收消息 ✓`，顺带生成 5/14 日程。
- 访问 `http://localhost:8081/admin/` 即可看到新仪表盘。

**影响范围**：

- 仅 DashboardView 视觉重构 + 新增 SparklineChart 公共组件。
- 运行时 / 后端 / 数据库 / API 零改动。
- 不影响其他页面。

**下一步**：

- 等用户对新仪表盘视觉验收。通过后继续阶段 3 其他视图（Logs / Login / Groups）。
- 未做的易用性项（Cmd+K 跳转、统一 toast、抽屉 sticky 底部、未保存警告）留待后续阶段处理。

---

## 2026-05-13 admin 前端重构阶段 0-2 自主执行完成 · 等待人工视觉验收

**变更类型**：feature / frontend / tooling / docs

**内容**：

阶段 0（环境清理）：

- 新增 [admin/frontend/.nvmrc](admin/frontend/.nvmrc)，锁定 Node 20。
- [admin/frontend/package.json](admin/frontend/package.json) 补 `engines.node ">=20.0.0 <21"`。
- [.gitignore](.gitignore) 补 `admin/static/assets/` 与 `.claude/skills/omubot-design-system/`。
- 审计：`admin/templates/*.html` 在 git 中仍有 9 个追踪记录但 Python 零引用；`admin/static/assets/` 仍追踪 95 个构建产物。两者都需要 `git rm --cached` 清索引，但**未自主执行**，待人工确认后清理。

阶段 1（基础设施固化）：

- [admin/frontend/src/stores/app.ts](admin/frontend/src/stores/app.ts) `buildThemeOverrides()` 扩展：补 `common.placeholderColor / iconColor / closeIconColor`、新增 `Tag` 与 `DataTable` 配置块，浅深两套同时覆盖。
- [admin/frontend/src/styles/global.css](admin/frontend/src/styles/global.css) 在 `!important` 块上方加审计注释：2 块标 `@audit keep`（`.dark .n-button:not(...)` / `.dark .n-menu` 深度选择器），6 块标 `@audit redundant`（themeOverrides 已覆盖）。**规则未删**，等 playground 验收后由人工拍板删除，预计可从 51 降至 ≤ 18。
- [admin/frontend/uno.config.ts](admin/frontend/uno.config.ts) 新增 6 个语义 shortcut：`section-title / section-hint / metric-num / chip / panel / toolbar-row`。
- 新增 [docs/admin-ui-tokens.md](docs/admin-ui-tokens.md) token 速查表。

阶段 2（公共组件补齐）：

- 新增 4 个公共组件：
  - `StateBadge.vue` — 5 档状态徽章（success / warning / error / info / default），带 icon 或圆点，可紧凑模式。
  - `LogPanel.vue` — 终端面板外壳：等宽字体、level 上色、暂停/清屏槽、自动滚动、暂停态徽章。
  - `DataToolbar.vue` — 列表工具条：摘要 / 筛选 / 操作三槽 + dense 模式，窄屏自动纵向。
  - `FieldGroup.vue` — 表单字段分组：标题 / 必填 / 帮助文字 / 右侧辅助 / inline 模式。
- 决策：不新建 `SectionCard` —— [AppPanelSection.vue](admin/frontend/src/components/common/AppPanelSection.vue) 已覆盖 style guide §7 全部描述。
- 新增 `/admin/design-playground` 路由和 [DesignPlaygroundView.vue](admin/frontend/src/views/playground/DesignPlaygroundView.vue)，集成全部公共组件 + Naive UI 基础控件演示，供人工浅 / 深主题视觉验收。后端 `@router.get("/admin/{rest:path}")` catch-all 已覆盖，仅需更新 [vite.config.ts](admin/frontend/vite.config.ts) `SPA_ROUTES`。

新增跟踪文档：

- [docs/tracking/web-refactor.md](docs/tracking/web-refactor.md) — 逐项勾选跟踪，含 `!important` 审计表、阶段 3 视图改造顺位、验收门径。

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` → 0 error
- `./node_modules/.bin/vite build` → 4.40s 通过，产物 1.7 MB（未恶化），`DesignPlaygroundView` 独立 chunk 16.62 kB / gzip 6.64 kB
- 新增组件 + playground 视图 `grep -c '!important'` 全部 0

**影响范围**：

- 运行时：零改动。仅新增组件和路由，不影响已有页面、API、数据结构、依赖版本。
- 构建：产物大小与构建时间无明显变化。
- 开发流程：开发者首次 clone 后 `nvm use` 能自动锁定到 Node 20。

**待人工验收**：

1. 启动前端（`cd admin/frontend && ./node_modules/.bin/vite`）或重建 bot 容器后访问 `/admin/design-playground`
2. 浅 / 深主题各看一遍，逐项核对 KPI 卡 / StateBadge / DataToolbar / DataTable / LogPanel / FieldGroup / EmptyState / Naive 基础控件
3. 通过 → 允许删除 `global.css` 里 `@audit redundant` 6 块冗余规则 → 进入阶段 3（Dashboard / Logs / Login 骨架迁移）
4. 不通过 → 指出具体问题项

**未自主执行的动作（待人工确认）**：

- `git rm --cached -r admin/static/assets/` — 让 git 忘掉 95 个构建产物，下次构建不再产生 diff 噪音
- `git rm admin/templates/*.html` — 清理 git 索引里 9 个已不存在的 Jinja 模板

---

## 2026-05-13 admin 前端重构启动 + 设计系统 skill 落地

**变更类型**：docs / tooling / process

**内容**：

- 新增 [docs/web-refactor-plan.md](docs/web-refactor-plan.md)，把 [admin-ui-style-guide.md](docs/admin-ui-style-guide.md) 转成可执行工程计划：阶段 0 清理 → 阶段 1 themeOverrides 固化 → 阶段 2 公共组件补齐（补 SectionCard/StateBadge/LogPanel/DataToolbar/FieldGroup）→ 阶段 3 高流量页面 Dashboard/Logs/Groups/System/Slang/Config/Login → 阶段 4 长尾渐进 → 阶段 5 可选 pnpm/chunk 拆分。给出每视图 3 段式 PR 模板（骨架迁移/子组件拆分/视觉精修）和 7 项视觉验收清单。
- 新增 `omubot-design-system` skill，作为 Calm Ops 设计系统执行器，独立于 `omubot-admin-console`。包含：token 速查表（light/dark 色板 + 阴影 + UnoCSS shortcut）、公共组件真实 API（AppPage/AppCard/AppPanelSection/MetricCard/PageToolbar/EmptyState 的 props 和 slot 清单）、Naive UI themeOverrides 单一来源原则、12 条反面样例、新视图骨架模板、大视图重构 3 段式 PR 流程。明确拒绝 bold/maximalist 默认，避免和官方 `frontend-design` skill 的创意取向冲突。
- skill 三处同步：`.claude/skills/omubot-design-system/`、`~/.claude/skills/omubot-design-system/`、`~/.codex/skills/omubot-design-system/`，339 行一致。
- `~/.claude/skills/omubot-admin-console/` 从 `~/.codex/skills/` 的旧版（5040b）升级为项目版（5987b，含 Maintenance Log Policy 一节），三处内容统一。
- 调整 `~/.claude/settings.json` 权限策略：`Bash(*)/Read(*)/Edit(*)/Write(*)` 全通配 + `deny` 规则拦删除类命令（rm/rmdir/unlink/shred/git rm/git clean -f/find -delete/sudo rm/trash/xargs rm），减少许可弹窗同时保底防误删。

**影响范围**：

- 文档：新增两份设计系统参考。
- agent 行为：两个 skill 对 admin/frontend 任务会自动匹配；`omubot-design-system` 描述里明确列了触发文件（`.vue`、`uno.config.ts`、`global.css`、`stores/app.ts`）。
- 运行时代码、构建产物、测试、API：无变更。

**后续**：

- 待确认阶段 0 的两个删除决策：清空 `admin/templates/`、`admin/static/assets/` 从 git 移除。
- 阶段 1-2 人工执行前建议先创建 `docs/tracking/web-refactor.md` 跟踪表。

---

## 2026-05-13 记录页统一补充默认排序 / 时间排序

**变更类型**：feature / backend / frontend / tests

**内容**：

- 为管理端记录型页面补充两档排序模式：`默认排序` 与 `按时间排序`。
- 表情包页接入真实排序与时间字段：默认按发送热度，时间模式按最近发送 / 收录时间。
- 记忆管理页与记忆浏览页接入排序切换；浏览页补实体聚合更新时间，避免“时间排序”只在实体内生效。
- 黑话页改为后端真实排序参数，不再前端对分页结果做本页“最新重排”；默认保留审核队列优先级，时间模式按最近更新/出现。
- 表达学习页接入表达样本、档案、反馈的排序参数；默认保留待审/置信/计数优先，时间模式按最近记录时间。
- 知识库页为文档源、图谱关系、候选队列补排序模式；文档源补 `updated_at` 字段贯通到前端展示。
- 新增前端共用排序选项模块 `admin/frontend/src/views/shared/sort.ts`，统一按钮文案和取值。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_admin_api.py tests/test_style_store.py tests/test_style_api.py -q` 通过，`91 passed`
- `source ./scripts/dev/env.sh && uv run ruff check admin/routes/api/stickers.py admin/routes/api/memory.py admin/routes/api/memos.py admin/routes/api/slang.py admin/routes/api/style.py admin/routes/api/knowledge.py services/media/sticker_store.py services/memory/card_store.py services/slang/store.py services/style/store.py services/knowledge/types.py services/knowledge/store.py services/knowledge/service.py tests/test_admin_api.py tests/test_style_store.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过，Vite 仅输出第三方 `#__PURE__` 注释提示

**交接说明**：

- 有分页或 limit 的列表都走后端 `sort` 参数，避免前端只排当前页造成直觉错位。
- “默认排序”保留各页面原有业务语义，不统一强行改成纯时间流。

## 2026-05-13 LearningNormalizer 统一归一化系统层落地

**变更类型**：feature / backend / frontend / tests

**内容**：

- 新增 `services/learning_normalizer`，提供统一 `normalize_key / fingerprint_key / score_similarity` 与 SQLite 聚类、成员、修订表。
- 引入 `rapidfuzz` 做候选相似度评分；首期不引入拼音/字形纠错重依赖。
- 黑话与表达存储接入统一归一化层，候选入库时记录 `normalization_cluster_id / normalization_item_id / normalized_key / normalization_features / auto_merged`。
- 黑话短词 fuzzy 守卫收紧：中文 3 字以内、ASCII 4 字以内只允许 exact/fingerprint 合并，避免“猫饼/猫猫饼”一类短词被误吞。
- 新增 Admin LearningNormalizer API，并在黑话详情、表达样本卡片内嵌展示归一化簇、代表写法、自动归并痕迹。
- 页面内补充锁定代表写法、拆出当前变体、撤销最近自动归并入口；不新增独立归一化控制台。
- 原始聊天记录和 evidence 不改写；归一化只作为派生系统层视图。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_learning_normalizer.py tests/test_similarity.py tests/test_style_store.py tests/test_style_api.py tests/test_style_plugin.py tests/test_style_extractor.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_slang_semantic_reviewer.py tests/test_admin_api.py tests/test_client.py tests/test_chat_plugin.py tests/test_plugin_bus.py -q` 通过，`305 passed`
- `source ./scripts/dev/env.sh && uv run ruff check services/learning_normalizer services/similarity.py services/slang services/style admin/routes/api/learning_normalizer.py admin/routes/api/slang.py admin/routes/api/style.py tests/test_learning_normalizer.py tests/test_style_store.py tests/test_slang_store.py tests/test_style_api.py tests/test_slang_plugin.py tests/test_admin_api.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过，Vite 仅输出第三方 `#__PURE__` 注释提示

**交接说明**：

- 激进自动合并只影响候选归并、别名/变体记录和 count/evidence 累计，不会自动批准黑话或表达。
- 历史数据尚未批量 backfill；新簇会随后续抽取和人工操作逐步生成。

## 2026-05-13 表达抽取 backlog 与 Web 展示修正

**变更类型**：fix / backend / frontend / tests

**内容**：

- 修复表达手动抽取在大群 backlog 下每次只消费单个小 batch 的问题：Archive cursor 模式下默认每群连续消费最多 5 个 batch，目标有效文本 200 条。
- 保留旧 MessageLog / legacy fallback 的单批行为，避免无 cursor 场景重复扫描同一批最近消息。
- 表达抽取 API 返回 `raw_scanned / text_scanned / backlog_raw / backlog_text / has_more / batches`，区分原始消息行和有效文本消息。
- Admin 表达学习页将“扫描”改为更直观的“有效文本 / 原始行 / 待扫文本”，群级结果显示“仍有待扫”。
- ConversationArchive 增加 `count_messages_after_pk()`，用于估算当前 scanner cursor 后的剩余待扫量。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_style_api.py tests/test_style_store.py tests/test_style_plugin.py tests/test_conversation_archive_store.py tests/test_admin_api.py -q` 通过，`104 passed`
- `source ./scripts/dev/env.sh && uv run ruff check admin/routes/api/style.py services/conversation_archive/store.py tests/test_style_api.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过，Vite 仅输出第三方 `#__PURE__` 注释提示

---

## 2026-05-13 高频表情冷却修正

**变更类型**：fix / backend / prompt / tests

**内容**：

- 新增 `storage/stickers/usage.json` scoped 使用记录，保留现有 `index.json` 的 `send_count/last_sent` 长期统计。
- `send_sticker` 增加硬冷却：同群短窗口重复、全局过热、长期占比过高时不发送、不计数，并返回替代表情 ID 让模型改选。
- 表情包 prompt 改为动态推荐候选视图，优先展示低频、久未发送、非冷却表情，并提示少量冷却中的 ID。
- 颜文字强制配图规则保留，但文案调整为“合适且近期未重复”，避免把单张表情当默认万能图。
- 小表情库或替代候选不足时不启用硬拦截，避免表情功能不可用。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_sticker_store.py tests/test_sticker_tools.py tests/test_chat_plugin.py tests/test_client.py tests/test_config_loader.py tests/test_plugin_bus.py -q` 通过，`228 passed`
- `source ./scripts/dev/env.sh && uv run ruff check plugins/sticker services/media/sticker_store.py services/tools/sticker_tools.py services/llm/client.py plugins/chat/plugin.py tests/test_sticker_store.py tests/test_sticker_tools.py tests/test_chat_plugin.py` 通过

---

## 2026-05-13 静默群表情收录 live 验收通过

**变更类型**：acceptance

**内容**：

- 用户在静默学习群 `477640404` 发送 QQ 动画表情后，运行日志出现 `silent sticker learned`。
- 新增表情 `stk_08c3d35b`，来源为 `stolen_silent_learn`，文件保存为 `storage/stickers/stk_08c3d35b.gif`。
- 常驻群表情仍走原聊天视觉路径；静默群表情现在走轻量 `on_message` 收录路径，不触发回复。

**验证**：

- 日志：`silent sticker learned | group=477640404 sticker_id=stk_08c3d35b file=DC937A0B68A506D77814153F251AED81.jpg`
- 表情库：`storage/stickers/index.json` 总数从 78 增至 79，新增项 `source=stolen_silent_learn`

---

## 2026-05-13 静默群表情收录权限误拦截修复

**变更类型**：fix / backend / tests / deployment

**内容**：

- Live 验收发现：静默学习群 `477640404` 收到 `sub_type=1`、`summary=[动画表情]` 的 QQ 动画表情，但未出现 `silent sticker learned`，表情库也未新增。
- 根因：静默群不在主动发言白名单中，`GroupConfig.resolve()` 会把 `tools_enabled` 派生为 `False`；StickerPlugin 误把这个派生值当成显式关闭工具，从而拦截了静默收录。
- 修复：静默偷表情只尊重全局或群 override 中显式设置的 `tools_enabled=false`，以及 `sticker_mode="off"`；不再被“不能主动发言”派生出的 `tools_enabled=false` 误伤。
- 已重建并重启 `bot` 服务；NapCat 保持运行，未重建。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_chat_plugin.py tests/test_sticker_tools.py tests/test_sticker_store.py tests/test_client.py tests/test_config_loader.py tests/test_plugin_bus.py -q` 通过，`215 passed`
- `source ./scripts/dev/env.sh && uv run ruff check plugins/sticker services/tools/sticker_tools.py tests/test_chat_plugin.py tests/test_sticker_tools.py tests/test_client.py tests/test_config_loader.py tests/test_plugin_bus.py` 通过
- 重建后容器内确认 `StickerPlugin` 已包含 `_silent_sticker_learning_disabled` 修复入口，Bot 已连接 OneBot 并进入接收消息状态

---

## 2026-05-13 静默学习群表情偷取重建验收

**变更类型**：deployment / acceptance / security-note

**内容**：

- 已执行 `docker compose up -d --build bot` 重建并重启 `bot` 服务。
- NapCat 容器保持运行，未重建。
- 启动后确认 OneBot WebSocket 已连接，Bot 已进入“开始接收消息”状态。
- 容器内确认 `plugins/sticker/plugin.json` 已包含 `message` 权限，`StickerPlugin` 已具备 `on_message` 静默学习入口。
- 重建后观测到静默学习群 `477640404` 的一条普通图片消息：`sub_type=0` 且无表情摘要，未被收录，符合“只偷表情、不偷普通图片”的边界。
- 审查发现当前运行环境未配置 `ADMIN_TOKEN`，Admin API 会回退默认 token `admin`；需在后续配置中补上强 token 后重启。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_sticker_tools.py tests/test_sticker_store.py tests/test_chat_plugin.py tests/test_client.py tests/test_config_loader.py tests/test_plugin_bus.py -q` 通过，`214 passed`
- `source ./scripts/dev/env.sh && uv run ruff check plugins/sticker services/tools/sticker_tools.py tests/test_sticker_tools.py tests/test_chat_plugin.py tests/test_client.py tests/test_config_loader.py tests/test_plugin_bus.py` 通过
- `docker compose ps` 显示 `qq-bot` 与 `napcat` 均为 `Up`
- Admin API smoke 通过：默认 token 登录后 `/api/admin/health` 返回 `bot=running`、`napcat=connected`、`connected_bots=1`；`/api/admin/plugins` 显示 `sticker` 已启用；`/api/admin/stickers` 返回 200
- 最近日志未发现 traceback、fatal、database locked、corrupt、abandoned、timeout 等异常

---

## 2026-05-13 静默学习群表情偷取修复

**变更类型**：fix / backend / docs / tests

**内容**：

- 修复 `silent_learn` 群无法偷表情的问题：群消息路由会在静默学习模式下提前返回，导致原先依赖 LLM 识图后调用 `save_sticker` 的路径不会执行。
- StickerPlugin 新增 `on_message` 轻量收录路径：只在静默学习群、不允许发言时识别 QQ 表情图片，最多每条消息收录 2 张，始终返回 `False`，不消费消息、不触发回复。
- `save_sticker` 工具调整为支持 bot 主动偷表情：管理员要求时仍需 `requested_by`，主动收录时可留空；显式传入非管理员仍会拒绝。
- `send_sticker` 保持群策略保护，未开放主动发言或关闭工具的群不会发送表情。
- 表情包 wiki 补充静默学习群收录规则与关闭条件。
- 本轮未重启 bot，需下次重建/重启后生效；NapCat 未触碰。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_sticker_tools.py tests/test_sticker_store.py tests/test_chat_plugin.py tests/test_client.py tests/test_config_loader.py tests/test_plugin_bus.py -q` 通过，`214 passed`
- `source ./scripts/dev/env.sh && uv run ruff check plugins/sticker services/tools/sticker_tools.py tests/test_sticker_tools.py tests/test_chat_plugin.py tests/test_client.py tests/test_config_loader.py tests/test_plugin_bus.py` 通过

---

## 2026-05-13 黑话复核失败回待审

**变更类型**：fix / backend / frontend / docs

**内容**：

- candidate AI 复核超时、解析失败或 LLM 不可用时，不再把词条移入独立“复核失败”队列。
- 失败项保留 `candidate_review_failed` 等诊断 meta，但 `candidate_reviewed=false`，继续归入“待 AI 复核”，下一轮普通复核会自动重试。
- Admin 黑话页删除“复核失败”队列、指标卡和“重试失败”按钮。
- 黑话追踪文档同步更新 Phase 9/Phase 12 口径。
- 已执行 `docker compose up -d --build bot` 重建并重启 bot；NapCat 保持运行，未重建。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_slang_store.py::test_slang_store_summary_splits_candidate_review_state tests/test_slang_plugin.py::test_slang_plugin_candidate_review_failure_returns_to_unreviewed_queue -q` 通过，`2 passed`
- `source ./scripts/dev/env.sh && uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py tests/test_slang_semantic_reviewer.py -q` 通过，`143 passed`
- `source ./scripts/dev/env.sh && uv run ruff check services/slang/store.py services/slang/daily_reviewer.py plugins/slang/plugin.py admin/routes/api/slang.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- 重启后 Admin API smoke 通过：`/api/admin/health` 返回 `bot=running`、`napcat=connected`、`connected_bots=1`；`/api/admin/slang/summary` 返回 200，历史失败项已计入待 AI 复核口径

---

## 2026-05-13 黑话 AI 复核性能修复重建验收

**变更类型**：deployment / acceptance / backend / frontend

**内容**：

- 重新构建 Admin 前端静态资源。
- 执行 `docker compose up -d --build bot` 重建并重启 `bot` 服务；NapCat 容器保持运行，未重建。
- 启动后黑话自动抽取正常完成：`run_4ec4615cbd5efb64`，4 个群扫描 33 条消息，耗时约 12.9 秒，提取 6 条，提升 4 条。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py tests/test_slang_semantic_reviewer.py -q` 通过，`143 passed`
- `source ./scripts/dev/env.sh && uv run ruff check services/slang plugins/slang admin/routes/api/slang.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py tests/test_slang_semantic_reviewer.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `source ./scripts/dev/env.sh && uv run python scripts/dev/slang_acceptance_check.py --skip-live` 通过，`149 passed`，ruff 通过，`slang.db` integrity/quick check 均为 `ok`
- 受保护 Admin API smoke 通过：`/api/admin/health` 返回 `bot=running`、`napcat=connected`、`connected_bots=1`；黑话 settings/summary/stats/runs 与表达 summary 均返回 200
- `storage/slang.db`、`storage/messages.db`、`storage/style.db` 的 `PRAGMA integrity_check` 与 `quick_check` 均为 `ok`
- 重启后 5 分钟日志未发现 traceback、fatal、database locked、corrupt、abandoned、timeout 等异常信号

**交接说明**：

- 当前剩余人工验收点：在 Web 黑话页手动点“全量 AI 复核”，观察 `review_all_pending` 行为是否符合预期；真实 LLM 耗时需以 live 操作为准。
- 未触碰 NapCat，未启用任何物理清理。

---

## 2026-05-13 黑话 AI 复核性能收口

**变更类型**：fix / performance / backend / frontend / tests / docs

**内容**：

- 定时 `daily_ai_review` 不再被 90 秒 `wait_for` 硬取消，避免长复核被下一轮 tick 反复重开并产生大量 `abandoned` run。
- `review_candidates` 与 `review_all_pending` 解耦：日常复核只处理达到语义阈值的 pending；Admin 手动“全量 AI 复核”才穿透 pending 阈值。
- pending 三段语义复核改为 3 并发执行，落库仍按结果顺序串行，降低 DB 写竞争风险。
- pending 语义复核跳过不参与判定的 web search，公网搜索仍保留在新抽取候选的辅助准入路径。
- Admin API 与黑话页面补充 `review_all_pending` 参数，失败队列重试不再顺带全扫 pending。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_slang_plugin.py tests/test_admin_api.py tests/test_slang_semantic_reviewer.py -q` 通过，`108 passed`
- `source ./scripts/dev/env.sh && uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py tests/test_slang_semantic_reviewer.py -q` 通过，`143 passed`
- `source ./scripts/dev/env.sh && uv run ruff check services/slang plugins/slang admin/routes/api/slang.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py tests/test_slang_semantic_reviewer.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

---

## 2026-05-13 ConversationArchive Phase 4a 审计修正收口

**变更类型**：fix / backend / storage / health / tests / docs

**内容**：

- `needs_rescan` / 非 active cursor 退回旧最近窗口时，真实 `ConversationArchive` 会写入 `status=legacy_fallback` 的 `conversation_scan_runs`，便于追查 fallback 原因；该路径仍不推进 cursor。
- 新增 archive evidence ref 写入能力：按 `message_pk` 或 `chat_id + platform_message_id` 将黑话/表达抽取证据挂到 `conversation_message_refs`。
- 新增业务 refs 回填 helper，可从 `slang_observations` / `style_evidence` 通过 `group_id + message_id` 回填 archive refs；真实清理前必须先跑 refs 同步或等价校验。
- System health 的 SQLite 卡片新增 messages archive 差异指标：`legacy_count / archive_count / missing_archive_count / archive_extra_count`；只有 legacy 缺 archive 回填时降级为 warning。
- 未启用真实物理清理，未重启 bot，未触碰 NapCat。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_conversation_archive_store.py tests/test_admin_api.py -q` 通过，`77 passed`
- `source ./scripts/dev/env.sh && uv run pytest tests/test_conversation_archive_store.py tests/test_message_log.py tests/test_style_api.py tests/test_slang_plugin.py tests/test_admin_api.py -q` 通过，`127 passed`
- `source ./scripts/dev/env.sh && uv run ruff check services/conversation_archive services/memory/message_log.py services/health.py plugins/slang/plugin.py services/slang/daily_reviewer.py admin/routes/api/style.py admin/routes/api/slang.py tests/test_conversation_archive_store.py tests/test_style_api.py tests/test_slang_plugin.py tests/test_admin_api.py` 通过

**交接说明**：

- `needs_rescan` 仍是人工介入状态，不会自动全量重扫或自动恢复增量 cursor。
- 当前 `dry_run_cleanup()` 仍只报告候选，不删除 raw rows。

---

## 2026-05-13 ConversationArchive 实机验收热修

**变更类型**：fix / backend / storage / deployment / tests

**内容**：

- 重建并重启 `bot` 服务，未重启 NapCat。
- 修复 `conversation_messages.message_pk` 是全局稀疏序列时，scanner 用 `last_pk + limit` 可能卡住的问题：
  - cursor 读取改为按当前 chat 的下一批 N 条消息查询。
  - 首次 bootstrap 改为取当前 chat 最近 N 条，而不是按全局 pk 做粗略范围。
- `ConversationArchive.backfill_legacy_messages()` 先检查 `legacy_row_id` 是否已存在，避免重复 init 时消耗 AUTOINCREMENT 序列。
- `messages.db` 切到 `journal_mode=DELETE` / `synchronous=FULL`，避免 Docker 容器持有 deleted WAL 后宿主 sqlite 看不到 bot 写入。
- daily/manual 抽取被 timeout/cancel 时会把 active `conversation_scan_runs` 标记为 `abandoned`，不再长期悬挂。
- 运行中发现 `messages.db` 索引条目不一致，已停 bot、备份、`REINDEX` 修复；备份：`storage/backups/messages.pre-reindex-20260513-003304.db`。
- 清理本轮自动验收产生的临时表达样本。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_conversation_archive_store.py tests/test_style_api.py tests/test_slang_plugin.py -q` 通过，`55 passed`
- `source ./scripts/dev/env.sh && uv run pytest tests/test_conversation_archive_store.py tests/test_message_log.py -q` 通过，`13 passed`
- `source ./scripts/dev/env.sh && uv run ruff check services/conversation_archive services/memory/message_log.py tests/test_conversation_archive_store.py` 通过
- `source ./scripts/dev/env.sh && uv run ruff check services/conversation_archive plugins/slang/plugin.py services/slang/daily_reviewer.py tests/test_conversation_archive_store.py tests/test_slang_plugin.py` 通过
- 实机：`PRAGMA integrity_check` 返回 `ok`；容器内 `messages.db` 为 `journal_mode=delete`，没有 `messages.db-wal (deleted)` fd
- 实机：`/style/extract/run` 对 `426727294` 第一轮 `scan_source=archive, scanned=1, from=17281, to=17367`，第二轮 `scan_source=archive, scanned=0, from=17367, to=17367`
- 实机：`slang_manual_extract` 自动抽取先消费同一增量并推进 cursor，之后手动两轮均 `scanned=0`，符合“不重复扫旧消息”

**交接说明**：

- 当前仍未启用真实物理清理。
- daily review 仍可能因公网搜索/LLM 耗时而 timeout，但对应 archive scan run 已能在取消时标记为 `abandoned`。

---

## 2026-05-12 ConversationArchive 黑话/表达 cursor 迁移

**变更类型**：backend / storage / tests / docs

**内容**：

- 新增 archive scan batch 兼容 helper：
  - 真实 `ConversationArchive` 优先读取 `conversation_messages` + `conversation_scan_cursors`。
  - 测试 fake、旧 MessageLog-shaped 对象、archive 读取失败、cursor `needs_rescan` 时自动退回旧 `query_recent()` 最近窗口。
  - 首次启用 cursor 只 bootstrap 最近 `limit` 条消息，不全量重扫历史。
- 黑话手动抽取改用 `slang_manual_extract` cursor。
- 黑话 daily review 改用独立 `slang_daily_review` cursor，避免手动抽取推进 daily review 进度；pending 复核无新消息时仍保留最近上下文 fallback。
- 表达手动抽取改用 `style_manual_extract` cursor，并继续保留黑话边界过滤、global 表达池和人工审核语义。
- 普通聊天、状态板、Admin 最近消息、client 压缩仍走兼容 `MessageLog` 接口；未启用真实清理，未重启 bot / NapCat。
- 更新 ConversationArchive、黑话、表达追踪文档。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_conversation_archive_store.py tests/test_style_api.py tests/test_slang_plugin.py -q` 通过，`54 passed`
- `source ./scripts/dev/env.sh && uv run pytest tests/test_admin_api.py tests/test_slang_store.py tests/test_style_plugin.py -q` 通过，`97 passed`
- `source ./scripts/dev/env.sh && uv run pytest tests/test_message_log.py tests/test_group_timeline.py tests/test_state_board.py tests/test_client.py -q` 通过，`121 passed`
- `source ./scripts/dev/env.sh && uv run pytest tests/test_style_store.py tests/test_style_extractor.py tests/test_slang_semantic_reviewer.py tests/test_slang_drift_reviewer.py -q` 通过，`29 passed`
- `source ./scripts/dev/env.sh && uv run ruff check services/conversation_archive services/memory/message_log.py admin/routes/api/style.py admin/routes/api/slang.py plugins/slang/plugin.py services/slang/daily_reviewer.py tests/test_conversation_archive_store.py tests/test_style_api.py tests/test_slang_plugin.py` 通过

**交接说明**：

- 下一步是人工验收 `/admin/style` 和 `/admin/slang` 手动抽取：重复触发时应只扫新消息，archive 不可用时仍能退回旧最近窗口。
- 真实删除 raw rows 仍禁止；dry-run 只报告候选和阻塞原因。

---

## 2026-05-12 ConversationArchive 后端原语落地

**变更类型**：backend / storage / tests / docs

**内容**：

- 新增 `services/conversation_archive`：
  - 创建首期 5 张核心表：`conversation_messages`、`conversation_scan_cursors`、`conversation_scan_runs`、`conversation_retention_policies`、`conversation_message_refs`。
  - 保留并维护旧 `group_messages` 表，现有 `MessageLog` 接口行为不变。
  - 兼容读取首期仍读旧 `group_messages`，避免 Admin 旧表调试删除临时消息后，新 `conversation_messages` 把消息“复活”。
  - `init()` 会幂等 backfill 旧 `group_messages` 到 `conversation_messages`。
  - `record()` 旧表写入优先；archive-side 写失败只记录错误，后续 backfill 可补齐。
  - 新增扫描 cursor、scan run 审计、retention policy、message ref 和 dry-run cleanup 原语。
- `services/memory/message_log.py` 改为 `ConversationArchive` 兼容包装，现有消费者继续使用 `MessageLog`。
- 当前没有迁移黑话/表达扫描路径，没有接 Admin，没有启用真实物理清理，没有重启 bot / NapCat。
- 更新 `docs/conversation-archive-implementation-tracker.md`，将 Phase 1、Phase 2 backfill 原语、Phase 4 dry-run 原语标记为已实现；Phase 3 黑话/表达 cursor 迁移仍待人工确认。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_conversation_archive_store.py tests/test_message_log.py tests/test_admin_api.py -q` 通过，`73 passed`
- `source ./scripts/dev/env.sh && uv run pytest tests/test_group_timeline.py tests/test_state_board.py tests/test_client.py -q` 通过，`117 passed`
- `source ./scripts/dev/env.sh && uv run pytest tests/test_style_api.py tests/test_style_plugin.py tests/test_slang_plugin.py tests/test_slang_store.py -q` 通过，`78 passed`
- `source ./scripts/dev/env.sh && uv run ruff check services/conversation_archive services/memory/message_log.py tests/test_conversation_archive_store.py` 通过

**交接说明**：

- 下一步是人工审计是否允许进入 Phase 3：把黑话 daily/manual 与表达 manual 从 `query_recent()` 迁到 cursor 范围扫描。
- 真实删除 raw rows 仍禁止；当前 dry-run 只报告候选和阻塞原因。

---

## 2026-05-12 ConversationArchive 本地对话归档方案归档

**变更类型**：docs / architecture-plan

**内容**：

- 新增 `docs/conversation-archive-implementation-tracker.md`：
  - 记录当前 `MessageLog.group_messages` 单表现状和主要消费者。
  - 固化首期 5 张核心表方案：`conversation_messages`、`conversation_scan_cursors`、`conversation_scan_runs`、`conversation_retention_policies`、`conversation_message_refs`。
  - 明确 `created_at` 继续使用 REAL epoch，主游标使用 `message_pk`，辅以 `last_created_at` 和小窗口回看。
  - 明确清理首期只做 dry-run；缺 required scanner cursor 时阻塞；`message_refs` 不作为唯一安全来源。
  - 将 `conversation_segments`、词频统计、私聊备忘录业务表延后，不混入归档底座首期 schema。
- `docs/style-learning-implementation-tracker.md` 补充表达学习与 ConversationArchive 的关系：后续迁到 `style_extract` scanner，动态风格档案仍由 `StyleStore` 管理。
- `docs/slang-module-implementation-tracker.md` 补充黑话模块与 ConversationArchive 的关系：后续迁到 `slang_extract` scanner，黑话业务语义仍由 `SlangStore` 管理。
- 本轮仅文档归档，不改运行时代码、不迁移 DB、不重启 bot、不碰 NapCat。

**验证**：

- `rg -n "ConversationArchive|conversation_messages|conversation_scan_cursors|dry-run|message_refs" docs/conversation-archive-implementation-tracker.md`
- `rg -n "ConversationArchive" docs/style-learning-implementation-tracker.md docs/slang-module-implementation-tracker.md maintenance-log.md`

---

## 2026-05-12 表达学习与黑话边界过滤

**变更类型**：fix / admin-api / frontend / tests / data

**内容**：

- 表达学习手动抽取保存前新增“黑话优先”过滤：
  - 读取当前群和 global 的黑话 term / aliases。
  - 如果表达候选的 `situation` 或 `style` 直接命中已知黑话 token，则不保存为表达习惯。
  - 证据文本里出现黑话不直接拦截，因为证据只是来源上下文。
- `/admin/style` 最近抽取面板新增 `filtered` 数量，方便区分“LLM 抽到了，但因为黑话边界被挡掉”。
- 修正现有数据：将 `993065015` 中把 `emu/ymy` 归纳成“无意义重复短词”的表达样本标记为 `rejected`，保留 revision。
- 本轮不改变黑话模块本身，不改 soul 文件。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_style_api.py -q` 通过
- `source ./scripts/dev/env.sh && uv run ruff check admin/routes/api/style.py tests/test_style_api.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过

**交接说明**：

- `emu=凤笑梦`、`ymy=有没有` 这类内容仍由黑话模块负责；表达学习只学习“怎么说/怎么接话”，不解释 token 含义。

---

## 2026-05-12 表达学习手动抽取可观测性

**变更类型**：admin-api / frontend / tests / docs

**内容**：

- `POST /api/admin/style/extract/run` 新增 `per_group` 明细，逐群返回：
  - `scanned`：参与抽取的人类消息数
  - `extracted`：LLM 返回的表达候选数
  - `saved`：实际写入/合并的表达数
  - `approved` / `pending` / `expression_ids`
- `/admin/style` 新增“最近抽取”面板，手动抽取后显示每个群的扫描、候选和保存结果。
- 0 候选群现在会显示为“无候选”，避免大群被扫描但没有样本时看起来像“没参与”。
- 本轮不改变抽取策略，不自动批准，不改 soul 文件。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_style_store.py tests/test_style_extractor.py tests/test_style_api.py tests/test_style_plugin.py tests/test_admin_api.py -q` 通过
- `source ./scripts/dev/env.sh && uv run ruff check services/style plugins/style admin/routes/api/style.py admin/routes/api/__init__.py tests/test_style_store.py tests/test_style_extractor.py tests/test_style_api.py tests/test_style_plugin.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- Web 人工验收时，点击“手动抽取”后看右侧“最近抽取”。若 `477640404` 显示“扫描 > 0 / 候选 0 / 保存 0”，说明该群参与了抽取，但当前窗口没有可保存表达。

---

## 2026-05-12 表达学习二次审计 P1 收口

**变更类型**：fix / backend / frontend / tests / docs

**内容**：

- `StyleExtractor` 新加入库前低信号质量过滤：
  - 拦截“有人说话 / 可以接话”这类泛化候选，避免污染 pending 队列。
  - 继续保留骂人、阴阳怪气、过度幼态等真实表达样本，通过 `risk_tags` 和 `output_policy` 交给输出层转译。
- `/admin/style` 动态风格档案补齐审计缺口：
  - 非启用档案可直接“启用”。
  - 当前启用档案可“回滚”到上一版，也可禁用。
- 补充 extractor 异常/低信号路径测试和 source row fallback 测试。
- 本轮不改 soul 文件、不重启 bot / NapCat。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_style_store.py tests/test_style_extractor.py tests/test_style_api.py tests/test_style_plugin.py tests/test_admin_api.py -q` 通过，`91 passed`
- `source ./scripts/dev/env.sh && uv run ruff check services/style plugins/style admin/routes/api/style.py admin/routes/api/__init__.py tests/test_style_store.py tests/test_style_extractor.py tests/test_style_api.py tests/test_style_plugin.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过；仅保留 VueUse 第三方 `#__PURE__` 注释提示

**交接说明**：

- 当前仍停在人工端到端验收：进入 `/admin/style` 手动抽取/审核表达，生成档案后测试启用旧版、回滚、禁用和实际回复风格。

---

## 2026-05-12 表达学习 Phase 4-6 反馈、档案与控制台

**变更类型**：backend / plugin / admin-api / frontend / tests / docs

**内容**：

- `StyleStore` 新增 `style_feedback` 与 `style_profiles`：
  - feedback 记录人工好/坏反馈、profile 操作审计和 bot 回复中性弱信号。
  - profile 保存动态风格档案版本、启用状态、来源表达和风险说明。
- `StylePlugin.on_post_reply()` 记录 bot 回复弱信号，但只作为 neutral feedback，不自动学习、不自动改权重。
- Admin API 新增表达状态、反馈、档案生成、当前档案、启用、禁用、回滚接口。
- 动态风格档案从 approved 表达生成，可启用/禁用/回滚；Prompt 明确不得改变核心人设、身份、价值观或禁区。
- 新增 `/admin/style` 轻量控制台：展示指标、表达样本、动态档案、反馈记录，支持手动抽取、审核、好/坏反馈和生成档案。
- 本轮不做模型微调、不改 soul 文件、不重启 bot / NapCat。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_style_store.py tests/test_style_extractor.py tests/test_style_api.py tests/test_style_plugin.py tests/test_admin_api.py -q` 通过，`88 passed`
- `source ./scripts/dev/env.sh && uv run ruff check services/style plugins/style admin/routes/api/style.py admin/routes/api/__init__.py tests/test_style_store.py tests/test_style_extractor.py tests/test_style_api.py tests/test_style_plugin.py` 通过
- `source ./scripts/dev/env.sh && uv run pytest tests/test_plugin_bus.py -q` 通过，`46 passed`
- `source ./scripts/dev/env.sh && uv run pytest tests/test_config_loader.py::test_plugin_config_json_default_and_override -q` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过；仅保留 VueUse 第三方 `#__PURE__` 注释提示

**交接说明**：

- 当前停在人工端到端验收：进入 `/admin/style`，先手动抽取/审核表达，再生成动态档案，在测试群确认回复风格。
- 默认不串群；需要全局表达时配置 `plugins/style` 的 `global_enabled_group_ids`。
- 如果回复变味，先在 `/admin/style` 禁用档案或静音表达；无需改 soul。

---

## 2026-05-12 表达学习 Phase 3 Prompt 注入初版

**变更类型**：backend / plugin / tests / docs

**内容**：

- 新增 `plugins/style` 目录插件：运行时只读取 `StyleStore` 中 `approved` 的表达习惯，构建 `表达习惯参考` 动态 PromptBlock。
- 存储层新增 `build_prompt_block()` / `get_prompt_expressions()`：按当前群、当前对话文本、置信度和作用域筛选相关表达；不相关时不注入。
- 默认不串群：只读取本群 `scope=group` 表达；只有配置 `global_enabled_group_ids` 中的群会额外读取 `scope=global` 表达池。
- `observe_only` 表达不注入；带 `risk_tags` 的表达即使被人工标为 `allow_use`，Prompt 中也会强制提示“按凤笑梦人设和当前心情转译，不要原样复刻”。
- 本轮不新增自动抽取、不后台采集 bot 回复质量、不改 soul 文件、不重启 bot / NapCat。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_style_store.py tests/test_style_extractor.py tests/test_style_api.py tests/test_style_plugin.py tests/test_admin_api.py -q` 通过，`83 passed`
- `source ./scripts/dev/env.sh && uv run ruff check services/style plugins/style admin/routes/api/style.py admin/routes/api/__init__.py tests/test_style_store.py tests/test_style_extractor.py tests/test_style_api.py tests/test_style_plugin.py` 通过
- `source ./scripts/dev/env.sh && uv run pytest tests/test_plugin_bus.py -q` 通过，`46 passed`
- `source ./scripts/dev/env.sh && uv run pytest tests/test_config_loader.py::test_plugin_config_json_default_and_override -q` 通过

**交接说明**：

- 当前停在 Phase 3 人工回复风格验收点：需要在测试群确认 approved 表达注入后，回复更贴近群节奏但仍像凤笑梦。
- 进入 Phase 4 前，不会学习 bot 自己的回复，也不会根据正负反馈自动强化或降权表达。

---

## 2026-05-12 表达学习 Phase 2 手动抽取初版

**变更类型**：backend / admin-api / tests / docs

**内容**：

- 新增 `services/style/extractor.py`：从群聊窗口抽取可复用表达习惯候选，输出 `situation/style/evidence/confidence/risk_tags/output_policy/persona_fit/mood_fit`。
- 风险表达不拒学：骂人、阴阳怪气、过度幼态、客服腔等会保留为候选，但必须打风险标签，并通过 `output_policy` 标注未来输出时应转译或只观察。
- 新增手动 Admin API `POST /api/admin/style/extract/run`：从 `MessageLog` 读取近期人类消息，调用 LLM 抽取并写入 `StyleStore`；默认写入 pending，只有显式 `auto_approve=true` 且高置信、非 `observe_only` 时才 approved。
- 保持默认群隔离；手动传 `scope=global` 时写入全局表达池，证据仍记录真实来源群。
- 不注册插件钩子、不后台采集消息、不注入 Prompt、不修改 soul 文件。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_style_store.py tests/test_style_extractor.py tests/test_style_api.py tests/test_admin_api.py -q` 通过，`77 passed`
- `source ./scripts/dev/env.sh && uv run ruff check services/style admin/routes/api/style.py admin/routes/api/__init__.py tests/test_style_store.py tests/test_style_extractor.py tests/test_style_api.py` 通过

**交接说明**：

- 当前停在 Phase 2 人工候选验收点：需要人工查看抽出的表达候选是否像表达习惯，而不是黑话词条、事实记忆或人设改写命令。
- 进入 Phase 3 前，不会影响 bot 回复；表达样本即使 approved，也尚未注入运行时 Prompt。

---

## 2026-05-12 黑话 Web 队列最新优先显示

**变更类型**：backend / frontend / tests

**内容**：

- 调整黑话 Admin 列表排序口径：词条队列、观察中 pending、语义漂移队列均改为最新时间优先，再按状态、置信度、次数做并列排序。
- 黑话页前端增加列表兜底排序：主队列、观察中候选、漂移治理、最近 run、详情修订记录、观察记录在接收数据后都会按对应时间字段倒序显示。
- 保持现有页面结构、筛选项、分页和操作按钮不变，只调整“最新信息条在最前面”的显示逻辑。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_slang_store.py::test_slang_store_lists_review_items_newest_first tests/test_admin_api.py::test_slang_api_lifecycle -q` 通过，`2 passed`
- `source ./scripts/dev/env.sh && uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py tests/test_slang_semantic_reviewer.py -q` 通过，`133 passed`
- `source ./scripts/dev/env.sh && uv run ruff check services/slang/store.py tests/test_slang_store.py admin/routes/api/slang.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过；仅保留 VueUse 第三方 `#__PURE__` 注释提示

**交接说明**：

- 前端静态文件已刷新到 `admin/static`；后端排序变更需要 bot 容器使用新代码后才会体现在 API 顺序上。
- 页面刷新后，待审、观察不足、复核失败、AI 审核、观察中候选、漂移治理和详情抽屉中的记录都应呈现最新在前。

---

## 2026-05-12 黑话模块重建与自动化验收

**变更类型**：deploy / smoke / tests / docs

**内容**：

- 执行 `docker compose up -d --build bot` 重建并重启 bot 容器；NapCat 容器保持运行，未重建。
- 自动验收覆盖工作区 doctor、黑话 SQLite 完整性、黑话 pytest/ruff、Admin 登录、健康检查、黑话设置/summary/stats/runs API、live semantic smoke。
- 修正 `scripts/dev/slang_semantic_smoke.py` 的 live 验收口径：
  - 强制复核时传 `review_candidates=true`，确保 pending semantic review 真正执行。
  - 默认 smoke 群优先使用 `/api/admin/slang/groups` 返回的真实群，避免硬编码 `100` 被运行时群过滤后 `groups=0`。
  - 临时上下文消息不再复用 pending term，避免 daily review 的候选抽取先合并/清掉待复核样本。
  - Docker 日志计数改为取窗口内最后一个 `semantic_reviewed`，避免旧 run 的 `0` 误导输出。
- 清理早前失败 smoke 留下的一条孤儿 observation；最终确认 `pending_smoke`、`term_smoke`、`obs_smoke` 均为 0。

**验证**：

- `source ./scripts/dev/env.sh && ./scripts/dev/doctor.sh` 通过，`0 fail, 0 warn`
- `source ./scripts/dev/env.sh && uv run python scripts/dev/slang_acceptance_check.py --skip-live` 通过，`4 passed, 0 failed`
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `docker compose up -d --build bot` 成功；`qq-bot` 与 `napcat` 均为 `Up`
- `source ./scripts/dev/env.sh && ADMIN_TOKEN=admin uv run python scripts/dev/slang_acceptance_check.py` 通过，`5 passed, 0 failed`
- `source ./scripts/dev/env.sh && ADMIN_TOKEN=admin uv run python scripts/dev/slang_semantic_smoke.py` 通过，`0 fail, 0 warn`，latest smoke run `semantic_reviewed=1`
- Admin API 自动检查：`/api/admin/health` 返回 `bot=running`、`napcat=connected`、`connected_bots=1`；`/api/admin/slang/settings`、`summary`、`stats`、`extract/runs` 均返回 200
- SQLite 残留检查：`pending_smoke=0`、`term_smoke=0`、`obs_smoke=0`

**交接说明**：

- 当前已完成自动化验收，可以进入人工页面/群聊验收。
- 启动日志里仍能看到早前 LLM `API 402 Insufficient Balance` 记录；后续人工验收如触发真实 LLM 任务失败，优先检查供应商余额/额度，而不是黑话存储。
- 本轮没有重建 NapCat，也没有改动生产群配置；bot 已保持运行。

---

## 2026-05-12 黑话 alias key 与缓冲 correctness 收口

**变更类型**：backend / tests / docs

**内容**：

- 修复黑话命中缓冲在 `message_id=None` 时的覆盖边界：有消息 ID 时继续按同消息同词去重，无消息 ID 时使用内部 event key，连续多条同词消息不会在缓冲或 flush 分组中互相压成 1 次。
- 新增 `slang_pending_candidate_keys` 辅助索引表，记录 pending 主 term 与 aliases 的 normalized keys；`SlangStore.init()` 会 backfill 既有 pending，pending 写入、更新、删除、晋升和合并路径同步维护索引。
- `_merge_pending_candidates_into_existing()` 改为按 `(group_id, normalized_key)` 从 pending key 索引预过滤，再用 `_normalized_term_keys()` 做 Python 二次确认，修复 `P J S K` / `pjsk` 这类 alias 归一化合并漏项。
- stoplist 语义收口为 term + aliases 彻底停用：extractor、candidate upsert、AI approved upsert、manual create 都会拒绝 alias 命中 stoplist 的新入库；既有词条不删除，但 match、Prompt 注入、lookup 继续隐藏。

**验证**：

- `python -m py_compile services/slang/store.py services/slang/extractor.py plugins/slang/plugin.py admin/routes/api/slang.py tests/test_slang_store.py tests/test_slang_plugin.py` 通过
- `source ./scripts/dev/env.sh && uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py tests/test_slang_semantic_reviewer.py -q` 通过，`132 passed`
- `source ./scripts/dev/env.sh && uv run pytest tests/test_slang_plugin.py::test_slang_plugin_buffers_hits_without_message_id_as_distinct_events tests/test_slang_store.py::test_slang_store_pending_merge_uses_normalized_alias_key_index tests/test_slang_store.py::test_slang_store_rebuilds_pending_key_index_for_legacy_rows tests/test_slang_store.py::test_slang_store_stoplist_alias_blocks_existing_terms_and_intake -q` 通过，`4 passed`
- `source ./scripts/dev/env.sh && uv run ruff check services/slang plugins/slang admin/routes/api/slang.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py` 通过

**交接说明**：

- 本轮只做黑话 correctness 修复，不部署、不重启、不碰 NapCat，也不改 Admin UI 样式。
- 新增的是辅助索引表，不改 `slang_terms` / `slang_pending_candidates` 既有列；旧 pending 会在 store 初始化时自动回填 key 索引。
- stoplist 现在会拦 alias。人工确实要恢复某个词或别名时，先从 stoplist 移除。

---

## 2026-05-12 黑话全局词封闭群选项

**变更类型**：backend / frontend / tests / docs

**内容**：

- 新增黑话设置 `global_excluded_group_ids`：默认所有群可使用 `scope=global` 的已批准黑话；列入该列表的群只使用本群 `scope=group` 词条。
- `find_matching_terms()`、Prompt 注入和 `slang_lookup` 工具统一遵守该封闭群设置，避免封闭群被全局黑话命中、注入或查询返回。
- `/admin/slang` 高级设置新增“封闭全局黑话的群”多行输入，留空即保持默认全局开启。
- Wiki 配置页同步说明全局黑话默认开启与封闭群语义。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py -k 'global_terms_can_be_closed_per_group or slang_lookup_tool_uses_current_group_and_global_terms' -q` 通过，`2 passed`
- `source ./scripts/dev/env.sh && uv run pytest tests/test_admin_api.py::test_slang_api_lifecycle -q` 通过
- `source ./scripts/dev/env.sh && uv run ruff check services/slang/types.py services/slang/store.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过；仅保留 VueUse 第三方 `#__PURE__` 注释提示

**交接说明**：

- 这不是关闭全局候选生成；`auto_promote_global_enabled` 仍单独控制是否扫描跨群 global 候选。
- 若只想让某个群不吃全局黑话，把群号写入 `/admin/slang` 的“封闭全局黑话的群”即可。

---

## 2026-05-12 群聊发言白名单与黑话学习拆分

**变更类型**：backend / admin-api / frontend / tests / docs

**内容**：

- 修正群门禁语义：`config/group-policy.json` 的白名单/黑名单只控制“能否主动发言、调用工具”，不再代表黑话学习许可。
- 未列入发言白名单的群默认 `off`，不会回复、不会调工具；单群 Profile 显式开启黑话后进入 `silent_learn`，仍然 `allows_active_group=false`。
- 当前真人大群 `426727294` 已从发言白名单移出，并在 `config/config.toml` 写入 `presence_mode="silent_learn"`、`slang_enabled=true`；`blacklist` 保持空数组未改。
- 群管理页文案改为“发言开放/发言关闭”，并说明黑话学习可在单群 Profile 单独开启。
- 群管理工具与贴纸发送工具补充 `tools_enabled` / 发言门禁校验，闭群不会通过工具外发消息。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_config_loader.py tests/test_admin_api.py tests/test_slang_plugin.py tests/test_scheduler.py -q` 通过，`168 passed`
- `source ./scripts/dev/env.sh && uv run ruff check kernel/config.py plugins/slang/plugin.py admin/routes/api/groups.py services/tools/group_admin.py services/tools/sticker_tools.py tests/test_config_loader.py tests/test_slang_plugin.py tests/test_admin_api.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `docker compose up -d --build bot` 后，容器内校验 `426727294 access=false presence=silent_learn slang=true tools=false learn=true speak=false`

**交接说明**：

- 要允许某群发言/工具调用，才把群号加入 `config/group-policy.json` 白名单。
- 要让某个非白名单群只学习黑话，在 `/admin/groups` 的单群 Profile 打开黑话系统即可；它不会进入回复、调度或工具外发链路。

---

## 2026-05-11 黑话语义漂移误报门控

**变更类型**：backend / admin-api / frontend / tests / docs

**内容**：

- 新增 `SlangDriftReviewer` 专用语义门控，drift 判定输出 `same_meaning / alias_candidate / real_drift / unclear`。
- `SlangStore._maybe_create_drift_review()` 不再靠 n-gram 低相似度直接开漂移；只有高置信 `real_drift` 才创建或刷新 open drift。
- `same_meaning` / `unclear` fail closed，不改 approved 释义；`alias_candidate` 只允许合并 alias，不进入 drift。
- 新增 Admin API `/api/admin/slang/drift/replay`，支持 dry-run / apply 回放历史 open drift，用于关闭 `没米` 这类同义改写误报。
- Admin 黑话 drift 卡片展示语义门控 verdict / reason，修订记录支持 `drift_suppressed` / `drift_alias_candidate`。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_slang_store.py tests/test_slang_drift_reviewer.py tests/test_slang_plugin.py tests/test_admin_api.py tests/test_client.py tests/test_config_loader.py tests/test_slang_semantic_reviewer.py -q` 通过，`208 passed`
- `source ./scripts/dev/env.sh && uv run ruff check services/slang/drift_reviewer.py services/slang/store.py services/slang/__init__.py services/llm/client.py kernel/config.py plugins/slang/plugin.py admin/routes/api/slang.py admin/routes/api/providers.py tests/test_slang_store.py tests/test_slang_drift_reviewer.py tests/test_admin_api.py tests/test_client.py tests/test_config_loader.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- 漂移队列现在是保守语义门控：模型不可用、超时、解析失败或低置信都会不开 drift。
- 若需要清理历史 open drift，可先调用 replay dry-run 对账，再 apply；不需要改表结构或清空数据。

---

## 2026-05-11 黑话恢复候选回归口径校正

**变更类型**：tests / docs

**内容**：

- 校正黑话恢复候选的回归断言：`return_ai_reviewed_term_to_candidate()` 会清空 AI 复核痕迹并让词条重新进入 `candidate_ai_unreviewed` 口径。
- 补充 store / admin API 回归，确认恢复后的词条不再保留旧 `ai_rejected` 计数，也不会继续出现在 `candidate_ai_rejected` 队列。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_slang_semantic_reviewer.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py -q` 通过
- `source ./scripts/dev/env.sh && uv run ruff check tests/test_slang_store.py tests/test_admin_api.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过

**交接说明**：

- 恢复候选后的词条会重新回到“未审候选”队列，后续人工复查应从该队列继续，不要再把它视为已处理项。

---

## 2026-05-10 维护日志归档与 Docker 可见日志瘦身

**变更类型**：ops / docs / observability

**内容**：

- 将 `maintenance-log.md` 调整为“活跃维护日志”，保留 2026-05-07 之后仍常用于交接的记录。
- 新增归档文件 [docs/audits/maintenance-log-archive-2026-04-29-to-2026-05-06.md](docs/audits/maintenance-log-archive-2026-04-29-to-2026-05-06.md)，收纳 2026-04-29 至 2026-05-06 的早期实施期维护条目。
- 调整 `bot.py` 的 stderr 格式化层：
  - 为 `send_queue` 增加独立中文频道标签。
  - 将 `scheduler`、`reply_workflow`、`send_queue`、长 `message_out` 日志在 Docker 可见输出中收敛为更短的中文观测摘要。
  - 保留原始结构化消息在文件日志中的细节能力，不改变运行时行为。

**验证**：

- `source ./scripts/dev/env.sh && uv run ruff check bot.py` 通过

**交接说明**：

- 之后查看日常交接先读主 `maintenance-log.md`，追早期演进再去归档文件。
- 本轮日志瘦身只作用于 stderr / `docker compose logs` 可见层；若需要完整原始字段，继续查看 `storage/logs/` 文件日志。

## 归档索引

- 早期实施期维护记录已归档至 [docs/audits/maintenance-log-archive-2026-04-29-to-2026-05-06.md](docs/audits/maintenance-log-archive-2026-04-29-to-2026-05-06.md)
- 当前主日志保留 2026-05-07 起仍频繁交接的活跃维护记录

---

## 2026-05-10 配置页补齐分段与并发入口

**变更类型**：backend / frontend / tests / docs

**内容**：

- 后端配置模型为 `reply_segmentation` 与 `scheduler.concurrency` 补充结构化编辑元数据：
  - 可读标签、帮助说明、推荐值、风险等级与重启提示。
  - `first_segment_humanize` / `later_segment_humanize` 收窄为 `skip | normal` 枚举，管理端会渲染为下拉选择。
- 管理端配置页新增两个日常任务入口：
  - `回复分段`：集中编辑分段开关、目标长度、软/硬段数上限、收尾文案、断点策略与段间延迟。
  - `群聊并发`：集中编辑全局 LLM 并发、队列预留参数与实验性的首段释放开关。
- 配置 API 保存/预览/审计逻辑保持原路径，仅修正字段错误路径序列化中的无效三元表达式。
- 修正 `.gitignore` 中 `config/` 规则误伤 `admin/frontend/src/views/config/` 的问题，改为只忽略仓库根目录 `/config/`。

**验证**：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_admin_api.py -k 'config_endpoint or config_preview or config_backups' tests/test_config_loader.py -q` 通过，`4 passed, 73 deselected`
- `source ./scripts/dev/env.sh && uv run ruff check kernel/config.py admin/routes/api/config.py tests/test_admin_api.py tests/test_config_loader.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过；仅保留 VueUse 第三方 `#__PURE__` 注释提示

**交接说明**：

- 本轮不改变运行时分段/并发语义，只补管理端可编辑性与 schema 说明。
- `admin/frontend/src/views/config/` 在本轮前被 `.gitignore` 误忽略，修正规则后会作为未跟踪源码目录显示；若提交本轮改动，需要一并纳入。
- `npm run build` 已刷新 `admin/static` 哈希产物，生产静态包已对应最新配置页。

---

## 2026-05-08 Context Knowledge System 评测闸门推进

**变更类型**：backend / tests / docs

**内容**：

- 按追踪文档未完成清单继续推进 Phase 5，先补 ContextPlugin 接管前的自动评测地基。
- 新增 `services/context/eval.py`：
  - `ContextEvalCase / ContextHitExpectation / ContextEvalResult / ContextEvalSummary`
  - 支持从 JSON fixture 加载 query 用例
  - 输出命中率、漏召、禁入误召、重复注入和 Prompt pack 长度
- 新增 `tests/fixtures/context_eval/basic.json`：
  - 覆盖 `memory_card / doc_chunk / graph_fact` 三类上下文命中
  - 标注不应命中的禁入内容，作为 Prompt 接管前安全闸门
- 新增 `tests/test_context_eval.py`：
  - 验证正常 memory/doc/graph 召回通过
  - 验证漏召、禁入内容、重复注入会被评测结果标红
- 新增 DeepSeek native stable prefix 回归测试：
  - 动态上下文变化只进入 tail metadata
  - system stable prefix 不随本轮上下文变化

**验证**：

- `.venv/bin/pytest tests/test_context_service.py tests/test_context_eval.py tests/test_prompt.py -q` 通过，`17 passed`
- `.venv/bin/ruff check services/context/eval.py services/context/__init__.py tests/test_context_eval.py tests/test_prompt.py` 通过

**交接说明**：

- `ContextPlugin` 仍保持默认关闭；本轮只是补“可证明不回归”的评测闸门。
- 下一步应把真实群聊、私聊、知识库问题继续沉淀到 `tests/fixtures/context_eval/`，再做旧 Memo/Knowledge 注入与新 ContextPlugin 注入的正式对比。

---

## 2026-05-08 ContextPlugin 接管前评测守卫

**变更类型**：backend / tests / docs

**内容**：

- 继续按 Context Knowledge System 未完成清单推进 P0 接管前评测。
- 扩充 `tests/fixtures/context_eval/basic.json`：
  - 新增无关问题案例，验证无关 query 不应误召 memory/doc/graph，也不应注入禁入内容。
- 新增 `tests/test_context_plugin.py`：
  - 使用真实 `PluginBus.fire_on_pre_prompt()` 路径，覆盖 manifest 权限检查与插件优先级。
  - 验证 ContextPlugin 接管时只出现一个“上下文资料”动态块。
  - 验证接管时旧 `KnowledgePlugin` 的“知识库”动态块和 `MemoPlugin` 的“记忆卡片”动态块不会重复注入。
  - 验证关闭接管后旧 Memo/Knowledge 动态注入路径可恢复，保留回滚能力。

**验证**：

- `.venv/bin/pytest tests/test_context_eval.py tests/test_context_plugin.py tests/test_prompt.py -q` 通过，`16 passed`
- `.venv/bin/ruff check services/context/eval.py services/context/__init__.py tests/test_context_eval.py tests/test_context_plugin.py tests/test_prompt.py` 通过

**交接说明**：

- 当前已经有“接管不重复、关闭可回滚”的自动守卫。
- `ContextPlugin` 仍不建议默认开启；下一步应继续补真实群聊、私聊、知识库 query fixture，并观察 Prompt pack 长度与漏召情况。

---

## 2026-05-08 主人场景上下文评测扩充

**变更类型**：tests / docs

**内容**：

- 继续扩充 ContextPlugin 接管前评测覆盖。
- 新增 `tests/fixtures/context_eval/owner_scenarios.json`：
  - 私聊用户记忆：验证私聊可召回用户记忆。
  - 群聊记忆：验证群聊可召回当前群记忆。
  - 作用域隔离：同关键词同时存在于用户、当前群、其他群时，不允许串 scope。
  - 文档知识：验证知识库文档 chunk 可独立召回。
  - 图谱事实：验证派生 graph fact 可召回。
  - 无关问题：验证无关 query 不为了填充 Prompt 误召旧资料。
- 扩展 `tests/test_context_eval.py`，新增主人场景评测测试和隔离断言。

**验证**：

- `.venv/bin/pytest tests/test_context_eval.py -q` 通过，`3 passed`
- `.venv/bin/ruff check tests/test_context_eval.py services/context/eval.py` 通过

**交接说明**：

- 当前 fixture 是可公开的脱敏/合成主人场景，不包含真实聊天记录。
- 下一步如果要进一步提高上线信心，应从实际群聊/私聊中提取脱敏 query，并记录 pack 长度、漏召和误召趋势。

---

## 2026-05-08 Context Knowledge System P2 执行

**变更类型**：backend / frontend / plugins / docs / tests

**内容**：

- 知识库索引持久化：
  - 新增 `services/knowledge/store.py`
  - 新增 `KnowledgeIndexStore`
  - SQLite 表：`knowledge_sources`、`knowledge_chunks`
  - `KnowledgeService` 支持 `index_db_path`
  - `KnowledgePlugin` 默认使用 `storage/knowledge_index.db`
  - 重启后可从 SQLite 恢复 chunk/source 索引
  - `reindex()` 按 source hash 复用未变化文件，只重切变化 source
- 知识库配置补齐：
  - `plugins/knowledge/config.default.json` 新增 `index_db_path`
  - `plugins/knowledge/config.schema.json` 增加 Web 可配置说明
- 上下文指标 API：
  - `ContextService` 最近检索快照新增 `hit_type_counts`、`hit_source_counts`、`duplicate_count`、`pack_chars`、`omitted_count`
  - 新增 `ContextService.metrics()`
  - 新增 `/api/admin/context/metrics`
- Admin Web：
  - `/admin/knowledge` 新增 `评测指标` 页签
  - 展示最近查询数、Miss 率、平均/最大 Prompt Pack、重复率、省略命中、命中来源、命中类型和最近查询列表
  - 知识库页头新增 `SQLite 索引 / 内存索引` 状态标签
- 文档：
  - 更新 `docs/wiki/Knowledge-System.md`
  - 更新 Context Knowledge System 追踪文档，标记 P2 完成

**验证**：

- `.venv/bin/pytest tests/test_knowledge.py tests/test_context_service.py tests/test_admin_api.py -k "knowledge or context" -q` 通过，`21 passed, 45 deselected`
- `.venv/bin/ruff check services/knowledge services/context tests/test_knowledge.py tests/test_context_service.py admin/routes/api/context.py admin/routes/api/knowledge.py plugins/knowledge/plugin.py` 通过
- `.venv/bin/python -m py_compile services/knowledge/*.py services/context/*.py admin/routes/api/context.py plugins/knowledge/plugin.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过，Vite 仅输出第三方 `#__PURE__` 注释提示

**交接说明**：

- 当前仍是“运行时内存 BM25/ngram 检索 + SQLite 持久索引”的轻量方案，不引入向量库。
- SQLite 持久索引用于更快恢复和增量 reindex，不改变知识库检索排序算法。
- 真实脱敏 query 仍需要继续沉淀到 fixture，指标面板会随着真实对话积累更有价值。

---

## 2026-05-08 Context Knowledge System P1 全量执行

**变更类型**：backend / frontend / plugins / docs / tests

**内容**：

- 正式启用 `ContextPlugin` 动态上下文接管：
  - `plugins/context/config.default.json` 默认 `enabled=true`
  - 默认 `takeover_dynamic_prompt=true`
  - `MemoPlugin` 保留稳定全局索引、提取和工具职责，不再重复注入实体记忆动态块
  - `KnowledgePlugin` 保留知识库服务和工具入口，不再重复注入文档 chunk 动态块
- 接入图谱自动候选抽取：
  - `services/knowledge_graph/extractor.py` 从占位边界升级为轻量确定性抽取器
  - `KnowledgeGraphService.extract_from_context_hits()` 从 `memory_card/doc_chunk` 中抽取 subject/predicate/object
  - `ContextPlugin` 在本轮 Prompt pack 完成后提交抽取结果，避免影响同一轮 Prompt
  - 高置信自动 active，中置信 pending，低置信忽略
  - 新增 active fact / pending candidate 去重，避免每轮重复写入
- 增强图谱证据链与回滚治理：
  - Graph fact 返回 evidence 列表
  - 新增 relationship detail / rollback / supersede API
  - rollback 会撤销当前 fact；如果当前 fact 取代了旧 fact，会恢复旧 fact
  - supersede 会创建新 active fact，并把旧 fact 标记为 superseded
- Web 知识库图谱页增强：
  - active fact 卡片显示证据、来源、fact_id、取代关系
  - 支持填写备注回滚事实
  - 支持用新的 subject/predicate/object 取代事实
- 更新 `docs/wiki/Knowledge-System.md` 和 Context Knowledge System 追踪文档，说明默认接管、自动抽取、证据链与回滚规则。

**验证**：

- `.venv/bin/pytest tests/test_knowledge_graph.py tests/test_context_plugin.py tests/test_admin_api.py -k "knowledge_graph or context_plugin or graph" -q` 通过，`11 passed, 47 deselected`
- `.venv/bin/pytest tests/test_context_service.py tests/test_context_eval.py tests/test_context_plugin.py tests/test_prompt.py tests/test_knowledge_graph.py tests/test_admin_api.py -k "context or knowledge_graph or graph or prompt" -q` 通过，`32 passed, 45 deselected`
- `.venv/bin/ruff check services/knowledge_graph tests/test_knowledge_graph.py plugins/context/plugin.py tests/test_context_plugin.py admin/routes/api/knowledge.py plugins/chat/plugin.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过，Vite 仅输出第三方 `#__PURE__` 注释提示

**交接说明**：

- 如果生产环境需要临时回滚动态上下文接管，可在插件配置中关闭 `ContextPlugin.enabled` 或 `takeover_dynamic_prompt`。
- 当前抽取器是轻量确定性规则，不是 LLM 抽取器；复杂事实仍需要后续增强或人工治理。
- 本轮没有迁移 `CardStore`，知识图谱仍是派生层，不替代记忆卡片或文档知识库。

---

## 2026-05-08 Context Eval 增加 Prompt Pack 长度预算

**变更类型**：backend / tests / docs

**内容**：

- 为 ContextPlugin 接管前评测补充“上下文长度预算”。
- `ContextEvalCase` 新增 `max_pack_chars`，用于描述某个案例期望实际注入的上下文长度上限。
- `ContextEvalResult` 新增：
  - `max_pack_chars`
  - `pack_budget_exceeded`
- `ContextEvalSummary` 新增 `pack_budget_violations`。
- `tests/fixtures/context_eval/basic.json` 与 `owner_scenarios.json` 为每个案例增加 pack 长度基线。
- `tests/test_context_eval.py` 新增失败路径：当 pack 实际长度超过 `max_pack_chars` 时，评测结果必须失败。

**验证**：

- `.venv/bin/pytest tests/test_context_eval.py -q` 通过，`4 passed`
- `.venv/bin/ruff check services/context/eval.py tests/test_context_eval.py` 通过

**交接说明**：

- `max_chars` 仍是打包硬截断预算；`max_pack_chars` 是评测期望预算，用来发现“没超系统上限但已经比预期膨胀”的回归。
- 后续真实脱敏 query 进入 fixture 时，应同步设定合理的 `max_pack_chars`，避免 ContextPlugin 接管后 Prompt 成本悄悄上涨。

---

## 2026-05-08 知识库导入指导文档

**变更类型**：docs

**内容**：

- 新增 `docs/wiki/Knowledge-System.md`，作为当前知识库使用与导入指南。
- 文档说明：
  - 文档知识库、记忆卡片、知识图谱三者的关系
  - 当前知识库配置结构与默认行为
  - Markdown 索引规则：只扫描 `.md`，按 `##` 二级标题切 chunk
  - BM25/ngram 轻量检索规则
  - 推荐导入目录、写作模板、不推荐写法
  - Web 中重建索引、搜索核对、上下文调试的操作流程
  - 常见问题排查清单
- 更新 `docs/wiki/_Sidebar.md`，增加“知识库”入口。

**验证**：

- 本轮为文档更新，已检查 wiki 侧栏链接和文档关键章节。

**交接说明**：

- 推荐把日常知识资料放到 `docs/knowledge/`，并将知识库插件 `dir` 配为 `docs/knowledge`，避免默认递归 `docs` 时把审计报告、开发计划等内部文档混入日常聊天知识库。

---

## 2026-05-08 知识库图谱加载失败审计与兼容修复

**变更类型**：frontend / tests / ops-audit

**问题**：

- `/admin/knowledge` 显示“图谱信息加载失败”。
- 审计 `docker logs qq-bot` 确认当前运行容器对新版接口返回 404：
  - `GET /api/admin/knowledge/stats`
  - `GET /api/admin/knowledge/sources`
  - `GET /api/admin/knowledge/graph/entities`
  - `GET /api/admin/knowledge/graph/relationships`
  - `GET /api/admin/knowledge/graph/candidates`
- 结论：运行容器后端仍是旧版本，新前端已经请求新版知识/图谱 API，属于前后端版本错位，不是图谱数据库或 SQL 损坏。

**修复**：

- `/admin/knowledge` 增加旧后端兼容降级：
  - `/knowledge/stats` 404 时降级到旧 `/knowledge` 统计
  - `/knowledge/search` 404 时降级到旧 `/knowledge?q=...`
  - `/context/search`、`/knowledge/graph/*` 404 时不再弹“加载失败”，改为展示“当前后端还没有新版接口，请重建/重启 Bot”
- 后端测试补充：
  - 图谱服务缺失时 graph API 返回 `available=false`，不应 500
  - 图谱服务存在时能返回 entities / relationships / candidates

**验证**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（Vite 仅输出第三方 `#__PURE__` 注释提示）
- `.venv/bin/pytest tests/test_admin_api.py -k "knowledge_graph or knowledge_api" -q` 通过，`3 passed, 46 deselected`

**交接说明**：

- 兼容修复只避免误报和白屏，不会让旧容器凭空拥有图谱 API。
- 要启用完整图谱、上下文调试和新版知识库来源管理，仍需重建/重启 `qq-bot` 容器。

---

## 2026-05-08 知识库 Web 治理台落地

**变更类型**：frontend / docs / tests

**内容**：

- `/admin/knowledge` 从单一关键词搜索页升级为知识系统治理台：
  - `文档源`：展示来源文件、路径、索引状态、chunk 数、source hash、跳过原因，并支持重建索引
  - `搜索核对`：调用结构化搜索接口，展示 title/source/chunk_id/score/content
  - `上下文调试`：输入本轮消息、用户 ID、群 ID，展示 memory/doc/graph 命中和最终 Prompt pack
  - `图谱关系`：展示 active graph facts 与实体列表
  - `候选队列`：展示 pending graph candidates，支持通过和拒绝
- 页面视觉保持 `Calm Ops / 雾青控制台` 风格：
  - 顶部改为紧凑状态总览，不再堆大 KPI 卡
  - 主内容按治理任务分页，减少重复信息和首屏噪声
  - 空状态补充下一步说明，避免只显示“暂无数据”
- 本轮只改 Web 展示和操作入口，不改变后端知识库、ContextService、GraphService 的数据语义。

**影响范围**：`admin/frontend/src/views/knowledge/KnowledgeView.vue`、`admin/static/assets/*`、`docs/superpowers/plans/2026-05-08-omubot-context-knowledge-system.md`

**验证**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（Vite 仅输出第三方 `#__PURE__` 注释提示）
- `.venv/bin/pytest tests/test_knowledge.py tests/test_context_service.py tests/test_knowledge_graph.py tests/test_admin_api.py -k "knowledge or context" -q` 通过，`18 passed, 45 deselected`

**交接说明**：

- 现在可以先用 `/admin/knowledge` 的“上下文调试”页做 ContextPlugin 开启前人工评测。
- 图谱自动抽取和标准化检索评测集仍未实现；候选队列目前依赖后端已有 candidate 数据。
- 如果页面仍显示旧版，需要重启 Vite dev server 或刷新已构建静态资源缓存。

---

## 2026-05-08 Context Knowledge System 后端落地

**变更类型**：backend / plugins / docs / tests

**内容**：

- 新增进度追踪文档 `docs/superpowers/plans/2026-05-08-omubot-context-knowledge-system.md`。
- 修复知识库 Admin 断链：
  - `KnowledgeBase` 升级为结构化 `KnowledgeService`，保留 `retrieve()` 兼容旧调用
  - 新增 `KnowledgeHit`，返回 `content/source/title/score/chunk_id`
  - Admin API 懒解析运行时知识库实例，不再依赖启动快照
- 新增 `services/context`：
  - `ContextHit / ContextService / MemoryContextSource / KnowledgeContextSource`
  - 新增 `/api/admin/context/search` 与 `/api/admin/context/recent`
  - 调试出口可解释 memory/doc/graph 三类命中
- 新增 opt-in 系统插件 `plugins/context`：
  - 默认关闭，避免立即替换生产 Prompt 注入
  - 开启后可由 ContextPlugin 接管动态上下文；Memo/Knowledge 保留旧路径回滚
- 新增轻量 SQLite 知识图谱底座：
  - `services/knowledge_graph`
  - 支持高置信事实自动 active、中置信候选审核、低置信忽略
  - 新增知识图谱实体、关系、候选队列 Admin API

**验证**：

- `.venv/bin/pytest tests/test_card_store.py tests/test_retrieval.py tests/test_memo_tools.py tests/test_knowledge.py tests/test_context_service.py tests/test_knowledge_graph.py tests/test_prompt.py -q` 通过，`111 passed`
- `.venv/bin/pytest tests/test_admin_api.py -k "memory or knowledge or context" -q` 通过，`3 passed, 44 deselected`
- `.venv/bin/pytest tests/test_plugin_bus.py -q` 通过，`45 passed`
- `.venv/bin/python -m py_compile services/knowledge/*.py services/context/*.py services/knowledge_graph/*.py plugins/context/*.py plugins/knowledge/plugin.py plugins/memo/plugin.py plugins/chat/plugin.py admin/routes/api/context.py admin/routes/api/knowledge.py` 通过

**交接说明**：

- 本轮没有迁移 `memory_cards`，也没有改变 `CardStore` 作为生产记忆权威存储的地位。
- `ContextPlugin` 默认关闭，当前生产聊天仍沿用 MemoPlugin/KnowledgePlugin 的旧注入路径；后续开启前建议先用 `/api/admin/context/search` 对典型群聊查询做命中评测。
- 图谱服务已建库和 API，但尚未接入自动抽取与前端治理页。

---

## 2026-05-08 知识库模块工作流审计

**变更类型**：docs / audit

**内容**：

- 新增 [知识库模块审计表](docs/audits/knowledge-module-audit-2026-05-08.md)，按阶段拆解当前知识库工作流：
  - 插件发现、配置读取、索引构建、分词、聊天触发、检索、Prompt 注入、DeepSeek V4 tail metadata、Admin API、前端页面、测试覆盖
  - 明确区分“服务层检索可用”和“Admin 搜索链路断裂”
- 审计确认：
  - `KnowledgeBase` 只有 `retrieve()`，没有 Admin API 当前期待的 `search()`
  - `KnowledgePlugin` 当前未把内部 `_kb` 挂到 `ctx.knowledge_base`
  - Admin 知识库页多数情况下只能看到空统计或空结果
  - 当前索引只扫描 `docs` 一级 Markdown，不递归索引 `docs/wiki`、`docs/audits`
- 给出 P0/P1/P2 风险表与后续修复路线。

**验证**：

- `.venv/bin/pytest tests/test_knowledge.py -q` 通过，`10 passed`
- Python 探针确认 `KnowledgeBase`：`has_reload=True`、`has_retrieve=True`、`has_search=False`
- Python 探针确认当前 `docs` 一级索引为 `93` 个 chunk

**交接说明**：

- 本轮只做审计留档，未修改知识库运行代码。
- 后续建议优先修复 Admin API 与运行时 `KnowledgeBase` 实例断链。

## 2026-05-08 配置页新手友好改版

**变更类型**：frontend / backend / docs / tests

**内容**：

- `/admin/config` 从“全量配置编辑器”改为“主人设置向导 + 高级维护区”：
  - 顶部 4 个大指标卡收口为紧凑状态条，显示保存状态、配置路径、解析模式和重启提示
  - 首屏新增 5 个任务卡：模型与 API、群聊回复、回复节奏、连接与视觉、权限与私聊
  - 点击任务卡后进入对应设置面板，顶部说明“适合谁改 / 改了影响什么 / 生效建议”
- 配置字段展示增强：
  - `ConfigFieldEditor` 支持中文展示名、帮助说明、示例、推荐值、风险等级和重启提示
  - API Key、Admin Token、NapCat 地址、白名单等高风险字段会显示明确提示
  - 前端先用本地字段文案映射兜底，后续可逐步迁移到后端 schema
- 保存体验调整：
  - “预览变更”从高级区前移到保存按钮旁
  - 保存确认会提示变更数量、涉及模块、高风险字段和重启建议
  - 保存成功后如涉及建议/必须重启字段，会提示在线重启 Bot
- 高级维护区收口：
  - 默认折叠
  - 拆为完整配置、高级 JSON、备份恢复、保存审计四块
  - 低频字段会递归保留在完整配置中，不会因为同模块有常用字段就被整体隐藏
- 后端配置 schema 增加可选展示字段透传能力：
  - `display_label / help / example / recommended / risk_level / restart_hint`
  - 当前接口保持兼容，旧前端和旧字段不受影响

**影响范围**：`admin/frontend/src/views/config/ConfigView.vue`、`admin/frontend/src/views/config/ConfigFieldEditor.vue`、`admin/frontend/src/views/config/types.ts`、`admin/routes/api/config.py`

**验证**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（Vite 仅输出第三方 PURE 注释提示）
- `.venv/bin/python -m py_compile admin/routes/api/config.py` 通过
- `.venv/bin/pytest tests/test_config_loader.py tests/test_admin_api.py -k config -q` 通过，`29 passed, 41 deselected`

**交接说明**：

- 本轮未改变配置文件格式、保存 API、预览 API、备份恢复 API 的业务语义。
- Raw JSON 仍可用于兜底，但默认不再作为新手主流程。
- 构建时 `scripts/cleanup-appledouble.sh` 清理了 AppleDouble 伴生文件；如开发服务器仍显示旧页面，请重启 Vite dev server。

## 2026-05-08 插件中心视觉重设计与权限状态文案修复

**变更类型**：frontend / backend / tests

**内容**：

- 修复 Vite 开发环境插件详情深链刷新白屏：
  - `admin/frontend/vite.config.ts` 增加 `/admin/plugins/` 前缀 SPA fallback
  - `/admin/plugins/element_detector?tab=settings` 这类动态路由刷新时会返回前端 `index.html`，不再被代理到后端
  - 移除 Vue Router 中“浏览器刷新时强制回仪表盘”的旧逻辑，插件配置深链刷新后会留在原页面
- 二次压实插件中心视觉：
  - 插件卡片由固定 2 列大卡改为自适应紧凑网格，卡片最小高度从大面板收口为高密度操作卡
  - 卡片按钮改为成组布局并加宽，启停开关固定在右侧，避免按钮在大卡中显得过小
  - 插件描述改为单行截断，版本、工具数、命令数保留为紧凑信息行
- 二次整改配置页：
  - 对象数组配置（如 `element_detector.rules`）改为全宽设置区，不再让左侧说明占据半屏
  - 规则卡片内字段使用高密度网格，移动端自动回退为单列
- 插件中心首页从大指标卡改为紧凑摘要条，默认聚焦用户插件、可配置数量、需关注项和系统锁定项。
- 插件卡片改为两列“书架卡”布局，统一按钮尺寸、底部操作区、状态标签和描述截断，减少空白与按钮不协调问题。
- 插件详情页重排为返回按钮、详情头、分段标签和内容面板；配置页增加设置状态条、结构化规则编辑器和固定保存栏。
- `permission_limited` 不再作为用户可见错误状态：
  - `PluginBus.plugin_health()` 增加 `display_state / display_label / display_type`
  - Admin 插件 API 对健康 payload 做展示字段归一化
  - 前端将权限门控显示为“按权限运行”，高级健康页保留原始状态与权限跳过次数

**影响范围**：`kernel/bus.py`、`admin/routes/api/plugins.py`、`admin/frontend/src/views/plugins/PluginsView.vue`、`tests/test_plugin_bus.py`、`tests/test_admin_api.py`

**验证**：

- `.venv/bin/python -m py_compile kernel/bus.py admin/routes/api/plugins.py` 通过
- `.venv/bin/pytest tests/test_plugin_bus.py tests/test_admin_api.py -k plugin -q` 通过，`58 passed`
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（最近一次构建通过，Vite 仅输出第三方 PURE 注释提示）
- `curl http://localhost:5173/admin/plugins/element_detector?tab=settings` 本地探测超时；如使用 Vite dev server，需要重启 dev server 让 `vite.config.ts` 生效

**交接说明**：

- `health.state` 仍保留原始诊断值；Web 展示请优先使用 `display_label / display_type`。
- `permission_denials` 和 `last_permission_denied` 只作为详情健康页诊断信息，不应在插件卡片中作为异常主状态展示。

## 2026-05-08 插件全目录化与插件中心配置体验修复

**变更类型**：feature / backend / frontend / plugins / docs / tests

**内容**：

- 取消根目录单文件插件：
  - `chat / datetime / debug_commands / echo / group_admin / history_loader / http_api / web_fetch / web_search` 全部迁移为 `plugins/<name>/plugin.py`
  - 所有运行时插件补齐 `__init__.py / plugin.json / config.default.json / config.schema.json`
  - `vision` 改为 `plugins/vision/plugin.json` 系统能力包，只读展示，不作为可启停插件
  - `PluginBus.discover_plugins()` 不再加载根目录 `.py` 单文件
  - `PluginIndexService` 将旧根目录单文件标记为 `legacy_single_file_unsupported` blocked
- 插件配置标准化补齐：
  - 新增时间、调试指令、复读、群管理、HTTP API、网页抓取、网页搜索的 Web 配置 schema
  - 要素察觉 `rules` 改为结构化对象数组 schema，Web 不再只能编辑裸 JSON
  - 插件配置统一保存到 `storage/plugins/config/<name>.json`
- 插件中心体验修复：
  - `/admin/plugins` 默认只展示用户插件
  - “显示系统插件”作为弱化高级入口；系统卡片固定显示“系统级 / 锁定 / 不可关闭”，无关闭开关
  - 用户插件卡片增加 `详情` 与 `配置` 两个清晰入口
  - `/admin/plugins/:name?tab=settings` 可直达配置页
  - 详情页左上角新增“返回插件中心”，并拆为 `概览 / 配置 / 命令工具 / 健康 / 包来源`
- Admin API 行为同步：
  - `GET /api/admin/plugins` 默认隐藏系统插件，`include_system=true` 才返回系统级和系统能力包
  - 系统级或 `read_only` 插件的 settings API 返回空 schema，保存请求会拒绝

**影响范围**：`plugins/*`、`kernel/bus.py`、`services/plugin_index.py`、`services/tools/*`、`admin/routes/api/plugins.py`、`admin/frontend/src/views/plugins/PluginsView.vue`、`docs/wiki/Plugins.md`、`docs/wiki/Plugin-Development.md`、`docs/architecture.md`、`docs/project-info.md`

**验证**：

- `.venv/bin/python -m py_compile ...` 通过
- `.venv/bin/python` 对真实 `plugins/` 执行发现：运行插件 `19` 个，`vision` 为系统 capability
- `.venv/bin/pytest tests/test_plugin_bus.py tests/test_admin_api.py tests/test_config_loader.py -k plugin -q` 通过，`55 passed`
- `.venv/bin/pytest tests/test_echo.py tests/test_history_self_messages.py tests/test_history_sticker.py -q` 通过，`33 passed`
- `.venv/bin/pytest tests/test_bilibili.py tests/test_element_detector.py tests/test_echo.py tests/test_sticker_store.py tests/test_slang_plugin.py tests/test_history_self_messages.py tests/test_history_sticker.py -q` 通过，`142 passed`
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `scripts/cleanup-appledouble.sh` 已在构建后清理伴生文件

**交接说明**：

- 旧根目录 `plugins/<name>.py` 不再是迁移目标；后续新增插件必须使用目录插件格式。
- 系统插件仍由后端强锁定，前端隐藏只是体验层收口，不是安全边界。
- 本轮尚未开放远程插件安装，插件商店仍是本地只读治理入口。

## 2026-05-08 插件规范化 Phase 0 与插件中心

**变更类型**：feature / backend / frontend / plugins / docs / tests

**内容**：

- 插件规范化地基落地：
  - `bilibili / dream / element_detector / food / knowledge / memo / sticker` 迁移为目录插件
  - `affection / schedule / slang` 补齐 `config.default.json` 与 `config.schema.json`
  - 所有有配置插件统一使用 `config.default.json` + `storage/plugins/config/<name>.json`
  - 旧插件 TOML 不再作为主配置路径读取
- Manifest v3 接入：
  - `plugin.json` 增加中文/英文名、系统/用户级、启停策略、分类、权限、能力、配置规格、商店元数据
  - `PluginBus` 会为显式注册插件和自动发现插件统一应用 manifest
  - 系统级或 `toggle_policy=locked` 插件无法被运行时关闭
- 后端插件治理扩展：
  - `PluginConfigStore` 改为 per-plugin JSON 覆盖文件，并保留旧 `plugin-config.json` 只读迁移 fallback
  - Admin 插件 API 返回 `effective_values / locked / tier / toggle_policy / config_spec / store`
  - 新增只读 `GET /api/admin/plugins/store`
  - 关闭系统级插件时 API 返回 `系统级插件无法关闭`，且不写入 `plugin-state.json`
- Web 插件中心上线：
  - 主侧栏恢复 `插件` 入口
  - `/admin/plugins` 拆为 `用户插件 / 系统插件 / 插件商店 / 治理队列`
  - 插件卡片显示中文名、英文名、插件 ID、版本、健康、工具数、命令数和配置状态
  - `/admin/plugins/:name` 提供插件详情与 JSON Schema 自动配置表单
  - 插件商店首版只读展示本地包与未来市场字段，不提供远程安装
- 文档同步：
  - `docs/wiki/Plugins.md` 更新为 manifest v3、JSON 配置、插件中心与只读商店现状

**影响范围**：`kernel/types.py`、`kernel/bus.py`、`kernel/config.py`、`services/plugin_config.py`、`services/plugin_index.py`、`admin/routes/api/plugins.py`、`admin/frontend/src/views/plugins/PluginsView.vue`、`admin/frontend/src/layouts/components/SideMenu.vue`、`admin/frontend/src/router/index.ts`、`plugins/*`、`docs/wiki/Plugins.md`

**验证**：

- `.venv/bin/pytest tests/test_config_loader.py tests/test_bilibili.py tests/test_sticker_store.py tests/test_slang_plugin.py tests/test_plugin_bus.py tests/test_admin_api.py -q` 通过，`209 passed`
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `.venv/bin/python` 对真实 `plugins/` 目录执行发现与索引：发现运行插件 `19` 个，本地索引 `20` 个，含 `vision` 系统能力卡

**交接说明**：

- `vision` 继续是系统服务，不进入普通插件启停流；插件中心只作为系统能力卡展示。
- Web 保存插件配置只写 `storage/plugins/config/<name>.json`，不会修改仓库内默认配置。
- 当前没有开放远程插件安装；插件商店接口是本地只读索引，为未来生态预留。

## 2026-05-08 DeepSeek V4 原生模式接入

**变更类型**：feature / backend / frontend / llm / tests / ops

**内容**：

- LLM Provider 体系新增原生 `deepseek` 路径：
  - `kernel/config.py` 扩展 `llm.api_format`，支持 `deepseek`
  - `services/llm/providers/deepseek.py` 新增 DeepSeek 原生 `/chat/completions` provider
  - 默认请求启用 `stream=true` 与 `stream_options.include_usage=true`
  - 解析 `prompt_cache_hit_tokens / prompt_cache_miss_tokens / completion_tokens_details.reasoning_tokens`
  - 兼容回退读取 `prompt_tokens_details.cached_tokens`
- DeepSeek V4 reasoning / replay 链路修正：
  - 原生 provider 会在 tool-call assistant 历史上回放 `reasoning_content`
  - 缺失 reasoning 时会自动补占位值，避免 DeepSeek thinking/tool loop 因非法 payload 报错
  - 最近一轮 replay token 规模与 sanitizer 介入状态会被记录到运行时观测
- Prompt 结构为 V4 前缀缓存做了专项重排：
  - `KnowledgePlugin` 的检索结果从 `static` 改为 `dynamic`
  - DeepSeek native 模式下，`state_board` 与所有动态 PromptBlock 不再进入稳定 system 前缀
  - 这些高频变化信息会被拼到当前 user turn 尾部的 `<turn_meta>` 块中
  - `plugin_static / plugin_stable` 继续留在 system prompt 中，保护稳定前缀缓存
- 压缩与 user scope 调整：
  - DeepSeek V4 主聊天路径使用更晚的 compact 阈值 `0.88`
  - 请求级 `user_id` 改为稳定哈希：群聊 `grp_*`，私聊 `dm_*`，后台任务 `sys_*`
  - 不再把原始 QQ 号或群号直接发给 DeepSeek 原生接口
- Usage 与系统观测扩展：
  - `services/llm/usage.py` 为 `llm_calls` 增加 `provider_kind / prompt_cache_hit_tokens / prompt_cache_miss_tokens / reasoning_replay_tokens`
  - 旧库会在 `init()` 时自动 `ALTER TABLE` 补齐字段
  - `admin/routes/api/providers.py` 与 `SystemView.vue` 现在可显示：
    - 当前 provider mode：`native / native-beta / anthropic-compat / openai-compat`
    - 最近一轮 cache hit%
    - 最近一轮 reasoning replay tokens
    - 最近一次 payload sanitizer 是否介入
  - Provider 测试接口现在会回传 usage 摘要与当前运行模式

**影响范围**：`kernel/config.py`、`services/llm/provider.py`、`services/llm/providers/{anthropic,openai,deepseek}.py`、`services/llm/client.py`、`services/llm/prompt_builder.py`、`services/llm/usage.py`、`plugins/knowledge.py`、`admin/routes/api/providers.py`、`admin/frontend/src/views/system/SystemView.vue`、`tests/test_call_api.py`、`tests/test_client.py`、`tests/test_config_loader.py`、`tests/test_usage.py`、`tests/test_admin_api.py`

**验证**：

- `UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest -q tests/test_call_api.py tests/test_prompt.py tests/test_usage.py tests/test_client.py tests/test_config_loader.py tests/test_admin_api.py` 通过，`140 passed`
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- 这轮只新增了 DeepSeek V4 原生路径，没有删除现有 `anthropic` 与 `openai` provider；兼容端点仍可继续使用。
- 运行时是否真正走 `native`，取决于对应 profile 的 `api_format=deepseek` 与 base URL；旧的 `api.deepseek.com/anthropic` 仍会在系统页显示为 `anthropic-compat`。

## 2026-05-07 Web 端“主人优先”精简改版

**变更类型**：feature / frontend / ux / docs

**内容**：

- 主侧栏收口为高频入口：
  - 保留 `仪表盘 / 人设编辑 / 群管理 / 记忆 / 表情包 / 群内黑话 / 知识库 / 配置 / 系统 / 日志`
  - 移出主导航：`日程心情 / 用量统计 / 好感度 / 调度器 / 插件 / 沙盒`
  - 隐藏页能力未删除，改由 `系统` 页中的“高级工具”入口进入
- 仪表盘改为唯一日常首页：
  - 合并原 `日程心情` 的主要职责
  - 增加待处理事项区，聚合黑话待审核、AI 待人工复核、NapCat 异常、重启建议和关键服务告警
  - 首屏收口为运行状态、下一段节奏、当前心情、待处理事项、关键日志和完整当日日程
- 配置页收口：
  - 常用模块默认直出，低频模块转入“高级设置”
  - JSON 兜底、变更预览、快照恢复、保存审计和保存说明默认折叠
- 系统页收口：
  - 首屏继续聚焦健康、连接、异常、资源和运维建议
  - Provider 深度管理、协议探测、备份与隐藏页面深链统一下沉到高级区
- 黑话页收口：
  - 首屏仅保留核心审核队列和筛选主流程
  - 热门排行、抽取运行记录、学习设置、漂移治理和观察中候选默认折叠
- 记忆体系调整：
  - `MemoryConsoleView` 默认进入浏览视图
  - 用户实体详情直接补充关系画像摘要，替代单独进入好感度页
  - `/affection` 路由改为兼容跳转到 `/memory?view=browse`
- 日志页调整：
  - 强化“实时流优先”视图
  - 历史文件列表降为次级入口，不再让用户先做来源选择

**影响范围**：`admin/frontend/src/layouts/components/SideMenu.vue`、`admin/frontend/src/router/index.ts`、`admin/frontend/src/views/dashboard/DashboardView.vue`、`admin/frontend/src/views/config/ConfigView.vue`、`admin/frontend/src/views/system/SystemView.vue`、`admin/frontend/src/views/slang/SlangView.vue`、`admin/frontend/src/views/memory/*`、`admin/frontend/src/views/groups/GroupsView.vue`、`admin/frontend/src/views/logs/LogsView.vue`

**验证**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- 前端构建前已自动执行 `bash ../../scripts/cleanup-appledouble.sh`

**交接说明**：

- 这轮没有删除低频页面功能，而是把它们从主人主流程中移走，并统一改为高级入口。
- 当前仓库的 `admin/frontend/src/*` 在 git 状态中表现为未跟踪文件；本次改动已实际写入工作区并完成构建验证，但后续若要提交，需要先确认该仓库的前端源码跟踪策略。

## 2026-05-07 三层架构审计报告改写为最新版并留档

**变更类型**：docs / audit

**内容**：

- 新增归档文档：`docs/audits/omubot-three-layer-architecture-audit-2026-05-07.md`
- 将旧版《Omubot 三层架构审计报告》按当前仓库真实状态重写为 2026-05-07 更新版
- 保留仍然成立的底层判断：
  - `kernel / services / plugins` 三层边界
  - `PluginBus` 中心地位
  - 8 个主钩子
  - 类继承式 `AmadeusPlugin`
- 修正已经过时的结论：
  - Provider 治理已升级为 profile / task profile / 热切换 / 后台编辑
  - Admin Web 已不再只是基础面板
  - 轻量语义检索、知识库、黑话治理、群 Profile、协议健康与插件治理已形成新能力层
- 明确仍未改变的短板：
  - 仍无 IoC、YAML Workflow、进程级插件隔离、重型向量 RAG、图谱记忆、标准迁移账本

**影响范围**：`docs/audits/omubot-three-layer-architecture-audit-2026-05-07.md`

**交接说明**：

- 后续如果再引用“三层架构审计”，应优先引用这份 2026-05-07 更新版，而不是沿用旧结论直接判断当前项目状态。

## 2026-05-07 Phase 2 Provider 多样性：profile 定义编辑器与细粒度管理收口

**变更类型**：feature / backend / frontend / tests / docs / ops

**内容**：

- Providers API 扩展：
  - `admin/routes/api/providers.py` 新增 `POST /api/admin/providers/definitions`
  - 支持结构化保存 `llm.profiles`，并处理 `api_key_mode = keep / replace / clear`
  - 保存时会同步修正 `default_profile / task_profiles`，删除旧 profile 后自动把失效任务映射回退到当前默认 profile
  - `main` profile 会继续同步 legacy `llm.api_format / base_url / api_key / model / max_tokens` 根字段，保持旧配置兼容
- 运行时热生效：
  - Provider 定义保存后会立即刷新运行中的 `LLMClient` 任务 profile 映射，不需要额外重启
  - 原有 `POST /api/admin/providers/selection` 也改为统一走持久化后配置模型，减少运行态和落盘态漂移
- 系统页 Provider 面板收口：
  - `SystemView.vue` 在原有“默认 profile 热切换 + 连通性测试”基础上新增“定义管理”抽屉
  - 抽屉支持新增 / 删除 / 编辑 profile，配置 API 格式、Base URL、Model、Max Tokens、能力声明和 API Key 处理方式
  - 保存后自动刷新当前 Provider 面板，不再需要手动回到配置文件里改 JSON
- 文档与路线图同步：
  - `docs/wiki/Configuration.md` 新增 `LLM Profiles` 说明，解释 `llm.profiles / default_profile / task_profiles` 的关系
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 2 尾项标记为完成，并把剩余内容归类为真实运行反馈微调或 optional extra

**影响范围**：`admin/routes/api/providers.py`、`admin/frontend/src/views/system/SystemView.vue`、`tests/test_admin_api.py`、`docs/wiki/Configuration.md`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "provider or protocol"` 通过，8 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh` 已由前端构建前自动执行

**交接说明**：

- 现在系统页已经同时具备“任务映射热切换”和“profile 定义编辑”，本轮 Phase 2 的具体尾项已收口。
- 后续若还要继续做 Provider 相关工作，优先级应转向真实模型运营反馈，例如默认 profile 选择策略、文案优化和更细的可观测性，而不是再补基础编辑能力。

## 2026-05-07 Phase 6 群 Profile：工具矩阵、屏蔽用户编辑与策略审计历史

**变更类型**：feature / backend / frontend / tests / docs / ops

**内容**：

- 群配置模型扩展：
  - `kernel/config.py` 为群配置新增 `allowed_tools / blocked_tools`
  - `ResolvedGroupConfig` 现在会解析群级工具 allow/block 结果，并继续保留 `tools_enabled / sticker_mode / slang_enabled` 的原有语义
- 运行时工具过滤接线：
  - `services/llm/client.py` 在原有“工具总开关 + 贴纸/黑话特殊过滤”之外，新增按群工具名单过滤
  - 当某群配置了允许名单时，只保留名单内工具；屏蔽名单始终优先
- Groups API 升级：
  - `admin/routes/api/groups.py` 新增 `GET /api/admin/groups/{group_id}/profile`
  - 返回当前群策略、工具目录和最近审计记录
  - 保存/恢复群策略时会写入 `storage/groups/group-profile-audit.json`
  - 审计记录包含动作类型、变更字段和 before/after 摘要
- Groups 页面重构收口：
  - `GroupsView.vue` 现在把群详情拆成基础配置、额外屏蔽用户、工具矩阵、实时状态、最近消息、策略审计历史
  - `blocked_users` 改为可视化标签编辑器，并明确区分“当前群额外屏蔽”和“全局屏蔽”
  - 工具矩阵按插件分组，支持 `继承 / 允许 / 屏蔽`
  - 策略历史支持直接回看最近变更
- 路线图同步：
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 6 尾项标记为完成
  - 当前焦点切到 Phase 2 尾项：profile 定义编辑器与更细粒度 Provider 管理

**影响范围**：`kernel/config.py`、`services/llm/client.py`、`services/group_profile_audit.py`、`admin/routes/api/groups.py`、`admin/routes/api/__init__.py`、`admin/frontend/src/views/groups/GroupsView.vue`、`tests/test_config_loader.py`、`tests/test_client.py`、`tests/test_admin_api.py`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_config_loader.py tests/test_admin_api.py tests/test_client.py -k "group"` 通过，16 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh` 已执行
- `find admin/static -name '._*' -print | wc -l` 结果为 `0`

**交接说明**：

- 这一步完成的是“群级工具治理”和“可回看审计”，不是全局 Provider 编辑器；下一步可以转去做 Phase 2 尾项。
- `blocked_users` 仍保持“全局 + 群级额外名单”的并集语义，没有改成可在群级反向移除全局屏蔽。

## 2026-05-07 Phase 3 插件治理：插件软隔离/限流收口

**变更类型**：feature / backend / frontend / tests / docs / ops

**内容**：

- `PluginBus` 增加软隔离冷却：
  - `kernel/bus.py` 在高频 Hook 链路上新增轻量软隔离策略
  - 同一插件在短窗口内连续报错或连续超出 Hook budget 时，会进入短时冷却
  - 冷却期间会临时跳过 `on_message / on_pre_prompt / on_post_reply / on_thinker_decision / on_tick`
  - 不做进程级卸载，不改插件 ABI；目标是先降低异常插件对总线的连带拖累
- 插件健康快照细化：
  - 新增 `suppressed_calls`、`cooldown_reason`、`cooldown_remaining_seconds`、`error_burst_count`、`slow_burst_count`
  - 插件页与 API 可以直接看到“正在冷却”“最近被抑制的 Hook”“慢调用/异常爆发来源”
- 系统健康接线：
  - `services/health.py` 的 PluginBus 检查新增 `throttled_plugins` 与 `suppressed_calls`
  - 顶层阈值告警会把“已有插件进入软隔离”视为需要关注的运行事件
- 插件页治理状态补强：
  - `PluginsView.vue` 的健康标签与治理统计区新增冷却/抑制可视化
  - Hook 明细里新增每个 Hook 的抑制次数，方便区分“插件慢”还是“插件已被总线临时降载”
- 路线图同步：
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 3 尾项标记为完成
  - 当前焦点切换到 Phase 6 尾项：群级工具矩阵、blocked users 编辑器和群策略审计历史

**影响范围**：`kernel/bus.py`、`services/health.py`、`admin/frontend/src/views/plugins/PluginsView.vue`、`tests/test_plugin_bus.py`、`tests/test_admin_api.py`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_plugin_bus.py tests/test_admin_api.py -k "plugin or services_health"` 通过，51 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `find admin/static -name '._*' -print | wc -l` 结果为 `0`

**交接说明**：

- 这一步做的是“软隔离”而不是热卸载/进程沙箱，优先目标是控制异常插件的爆发面和排障可见性。
- 如果后续真实运行中仍遇到单插件长期拖垮总线，再评估更重的进程级隔离，但当前默认路线仍保持轻量。

## 2026-05-07 Phase 1 稳定性地基：健康告警降噪与策略化门槛

**变更类型**：feature / backend / frontend / tests / docs / ops

**内容**：

- 顶层健康告警阈值化：
  - `services/health.py` 为 `LLM / PluginBus / Runtime Errors / NapCat / Protocol Trace / SQLite / Memory / Slang` 增加顶部告警判定门槛
  - 顶层 `alerts` 不再机械镜像所有 warning/error，而是只保留达到阈值的高优先级异常
  - 新增 `policy` 摘要，说明当前采用 thresholded 模式，并统计被折叠的轻量提醒数量
- 维护窗口建议同步降噪：
  - `maintenance_window` 不再直接根据所有服务 warning/error 触发
  - 现在只根据阈值后的高优先级告警判断是否建议进入维护窗口或是否建议重启验证
- 系统页说明增强：
  - `SystemView.vue` 新增“折叠轻量提醒”提示和阈值说明文案
  - 顶部告警区更安静，但下方“服务级健康”仍保留完整 warning/error 细节，方便人工审查回退链路
- 文档与路线图同步：
  - `docs/project-info.md` 说明系统页采用“两层健康口径”
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 1 告警策略尾项标记为完成，并把焦点切到 Phase 3 插件限流/隔离策略

**影响范围**：`services/health.py`、`admin/frontend/src/views/system/SystemView.vue`、`tests/test_admin_api.py`、`docs/project-info.md`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "system"` 通过，6 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `find admin/static -name '._*' -print | wc -l` 结果为 `0`

**交接说明**：

- 这一步完成的是“顶层告警降噪”，不是隐藏细节；完整 warning/error 仍保留在服务级健康卡中。
- 下一步建议继续 Phase 3 尾项，补插件限流/隔离策略，减少异常插件对总线的连带影响。

## 2026-05-07 Phase 8 运维体验：维护窗口提示、健康告警与重启影响说明

**变更类型**：feature / backend / frontend / tests / docs / ops

**内容**：

- 服务级健康聚合升级：
  - `services/health.py` 在原有 `services` 列表与 summary 之外，新增 `alerts` 与 `maintenance_window`
  - 告警会按服务状态自动生成高优先级摘要，并给出下一步处理动作
  - 维护窗口摘要会给出“是否建议进入维护窗口”、原因、处理顺序和重启建议
- 系统页收口：
  - `SystemView.vue` 新增“运维建议”区，统一展示维护窗口判断、当前健康告警和重启影响说明
  - 保留原有服务级健康、关键错误、资源、协议探测和备份区，不重做整体信息架构
- 全局重启提示同步：
  - `RestartBotButton.vue` 改为多段式确认文案，不再只提示“短暂中断”
  - 会明确说明连接中断、配置/插件/协议改动生效边界，以及 Docker 自动拉起/手工启动的注意点
- 路线图与说明同步：
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 8 第三刀标记为完成
  - `docs/project-info.md` 同步系统页职责说明

**影响范围**：`services/health.py`、`admin/routes/api/system.py`、`admin/frontend/src/views/system/SystemView.vue`、`admin/frontend/src/components/common/RestartBotButton.vue`、`tests/test_admin_api.py`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`docs/project-info.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "system"` 通过，6 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- 这一步已经把 Phase 8 的“维护窗口/告警/重启影响”补齐，但告警阈值仍偏静态。
- 下一步建议回到 Phase 1 尾项，继续做健康告警降噪、分级门槛和长期运行误报控制。

## 2026-05-07 Phase 8 运维体验：配置回滚向导与基础备份

**变更类型**：feature / backend / frontend / tests / docs / ops

**内容**：

- 配置快照与恢复链路：
  - 新增 `services/config_backup.py`，用 `storage/config/config-backups.json` 管理最近可恢复快照元数据
  - 实际快照内容写入 `storage/config/backups/`，用于真实恢复，不在 Web 端直接暴露原始配置值
  - `admin/routes/api/config.py` 新增 `/api/admin/config/backups` 与 `/api/admin/config/restore`
  - 保存配置后会自动生成“保存快照”；执行恢复时会先生成“恢复前备份”，再写入“恢复结果”快照
- Admin 配置页：
  - `ConfigView.vue` 新增“可恢复配置快照”面板，展示时间、来源、模块命中、快照大小和恢复按钮
  - 恢复动作会先确认当前草稿、再确认覆盖当前 `config/config.json`，成功后同步刷新变更预览与最近审计
  - 页面只展示快照摘要，不会把 secret 明文渲染到前端
- 类型、测试与计划同步：
  - `admin/frontend/src/views/config/types.ts` 扩展 backup / restore 类型
  - `tests/test_admin_api.py` 新增配置快照保存、恢复与 secret 不泄露回归
  - `docs/wiki/Configuration.md` 与 `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 同步更新，Phase 8 第二刀收口

**影响范围**：`services/config_backup.py`、`admin/routes/api/config.py`、`admin/frontend/src/views/config/ConfigView.vue`、`admin/frontend/src/views/config/types.ts`、`tests/test_admin_api.py`、`docs/wiki/Configuration.md`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "config"` 通过，4 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `find admin/static -name '._*' -print | wc -l` 结果为 `0`

**交接说明**：

- 现在配置页已经具备“预览 diff + 审计记录 + 可恢复快照”三段运维链路。
- 下一步可继续 Phase 8 第三刀，补维护窗口提示、健康告警和更清晰的重启影响说明。

## 2026-05-07 Phase 8 运维体验：配置变更审计与保存前 diff 预览

**变更类型**：feature / backend / frontend / docs / ops

**内容**：

- 配置预览与审计链路：
  - 新增 `services/config_audit.py`，以 `storage/config/config-audit.json` 保存最近配置落盘摘要
  - `admin/routes/api/config.py` 新增 `/api/admin/config/preview` 与 `/api/admin/config/history`
  - 保存前会基于服务端校验后的 `BotConfig` 规范化结构计算 diff；保存成功后写入审计记录
  - 审计记录只保留字段路径、变更类型和遮罩后的 before/after 展示，不把 secret 明文写进历史
- Admin 配置页：
  - `ConfigView.vue` 新增“查看变更”按钮
  - 新增“保存前变更预览”面板，展示新增/移除/修改统计、涉及模块和逐字段差异
  - 新增“最近保存审计”面板，回看最近几次配置落盘摘要
  - 保存操作现在会先走一次预览校验，再让用户确认写入条目数，减少误改直接落盘
- 类型与文档同步：
  - `admin/frontend/src/views/config/types.ts` 扩展 diff / audit 类型
  - `docs/wiki/Configuration.md` 增补配置页预览和审计说明
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 8 第一刀标记为已完成，并把下一焦点切到回滚向导与基础备份

**影响范围**：`services/config_audit.py`、`admin/routes/api/config.py`、`admin/frontend/src/views/config/ConfigView.vue`、`admin/frontend/src/views/config/types.ts`、`tests/test_admin_api.py`、`docs/wiki/Configuration.md`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "config"` 通过，3 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- 当前审计记录是轻量摘要，不等价于完整配置备份；下一步仍需补 Phase 8 的回滚向导和保存快照。
- Secret 字段在预览和审计里默认遮罩展示，但原始 `config/config.json` 仍按实际值正常落盘。

## 2026-05-07 Phase 7 本地插件生态：plugin.sig 签名与来源校验预留

**变更类型**：feature / backend / frontend / docs / governance

**内容**：

- 本地插件 detached attestation 预留：
  - `services/plugin_index.py` 新增可选 `plugin.sig` / `xxx.sig` 识别
  - 当前支持轻量 JSON 校验结构：`scheme=sha256`、`entry_sha256`、`manifest_sha256`、`signer`、`key_id`、`signed_at`、`source.origin`、`source.entry_path`
  - 索引会校验入口文件、`plugin.json` 指纹以及来源声明是否和当前本地路径一致
- 插件索引与治理增强：
  - 每个本地插件包新增 `signature_status`、`source_attestation_status`、`signature_signer`、`relative_signature` 等字段
  - `summary` 新增 `signature_verified_count`、`signature_issue_count`、`unsigned_external_count`
  - 签名或来源声明异常时，已加载插件会标记为 `attention`，未加载插件会直接进入 `blocked`
- Admin 插件页：
  - 本地插件索引横条新增“已校验 / 签名问题”计数
  - 治理队列与插件卡片支持显示签名状态
  - 插件详情“本地包索引”区新增签名路径、签名方案、签名人、来源声明、声明入口、签名时间等信息
- 文档同步：
  - `docs/wiki/Plugins.md` 补充 `plugin.sig` 和索引校验说明
  - `docs/architecture.md` 补充目录插件中的 `plugin.sig` 预留说明
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 7 标记为“基本收口”，并把下一焦点切到 Phase 8 运维体验

**影响范围**：`services/plugin_index.py`、`admin/routes/api/plugins.py`、`admin/frontend/src/views/plugins/PluginsView.vue`、`tests/test_admin_api.py`、`docs/wiki/Plugins.md`、`docs/architecture.md`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "plugin"` 通过，4 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- 这不是远程安装或自动执行机制，只是本地插件包的轻量签名/来源校验预留。
- 当前默认仍坚持 `local_only` 策略；后续若继续增强签名格式，也必须保持“不从 Web 直接下载并执行未知代码”的边界。

## 2026-05-07 Phase 7 本地插件生态：未加载包治理队列

**变更类型**：feature / backend / frontend / docs / governance

**内容**：

- 本地插件索引治理语义补强：
  - `services/plugin_index.py` 为每个本地插件包新增 `governance_status / governance_label / action_hint`
  - 状态固定为 `healthy / attention / ready / review / blocked`
  - `summary` 新增 `not_loaded_count / ready_to_load_count / review_required_count / blocked_count / attention_count`
  - 已加载但缺清单、来源待确认或版本不兼容的插件不再只给 warning，而是明确标记为“需关注”
  - 未加载插件会区分“可接入运行时”“来源待确认”“已阻塞”，减少目录里藏问题的情况
- Admin 插件页治理队列：
  - `/admin/plugins` 顶部本地插件索引横条补充未加载、阻塞、待确认计数
  - 新增“本地包治理队列”卡片，集中展示未加载、来源待确认、版本不兼容以及已加载但仍需治理的插件包
  - 每个条目显示形态、入口路径、来源状态、清单状态、兼容状态和行动建议；已加载项可直接跳转到插件详情继续治理
- 文档与路线图同步：
  - `docs/wiki/Plugins.md` 增补本地插件索引、治理状态和 `GET /api/admin/plugins/index` 的说明
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 7 的“未加载本地包治理”和“兼容告警优化”标记为已完成，并把下一焦点切到签名/来源校验预留

**影响范围**：`services/plugin_index.py`、`admin/routes/api/plugins.py`、`admin/frontend/src/views/plugins/PluginsView.vue`、`tests/test_admin_api.py`、`docs/wiki/Plugins.md`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "plugin"` 通过，4 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- 本轮仍坚持 Phase 7 的安全边界：只识别本地插件包，不开放 Web 端远程下载安装与执行未知代码。
- 当前已具备“发现本地包存在但未加载”与“给出治理建议”的能力；真正的签名格式与来源校验策略仍留在下一刀。

## 2026-05-07 Phase 5 轻量语义增强：质量守卫与系统可视化收口

**变更类型**：feature / backend / frontend / quality / observability

**内容**：

- 黑话质量守卫收口：
  - 新增 `services/slang/quality.py`，统一沉淀噪声 term、泛化释义、alias 清洗等轻量质量判断
  - `SlangExtractor` 改为复用共享质量守卫，继续过滤低信号候选，并额外挡掉“一个梗 / 一种说法”这类无效释义
  - `SlangDailyReviewer` 在 AI 复核写库前也走同一套判断；如果 AI 把释义改坏，会自动回退到 extractor 原始释义，避免把泛化结果写进 approved 词条
- 记忆语义指标可视化：
  - `services/health.py` 的 `Memory` 服务项补充 `queries / hits / hit_rate / fallbacks / errors / last_error`
  - Admin 系统页 `Memory` 服务卡新增紧凑指标标签，直接展示 semantic backend、命中数、回退数与最近错误
- 文档与计划同步：
  - `docs/wiki/Semantic-Retrieval.md` 更新系统页可视指标说明
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 5 调整为“基本收口”，并把当前焦点切到 Phase 7 本地插件生态起步

**影响范围**：`services/slang/quality.py`、`services/slang/extractor.py`、`services/slang/daily_reviewer.py`、`services/health.py`、`admin/frontend/src/views/system/SystemView.vue`、`tests/test_slang_plugin.py`、`tests/test_admin_api.py`、`docs/wiki/Semantic-Retrieval.md`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_slang_plugin.py tests/test_admin_api.py tests/test_retrieval.py tests/test_client.py tests/test_config_loader.py tests/test_similarity.py` 通过，136 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh && find admin/static -name '._*' -print | wc -l` 返回 `0`

**交接说明**：

- 本轮没有引入 embedding、FAISS 或新重依赖；`embedding` 仍保持 optional extra 安全 stub。
- Phase 5 现已完成默认轻量路线的主要能力，后续只剩可选 embedding 真正实现，不作为默认栈阻塞项。
- 下一步按路线图进入 Phase 7：本地插件包索引、来源校验与兼容版本检查。

## 2026-05-07 Phase 4 协议韧性：mock 协议测试

**变更类型**：test / protocol / observability

**内容**：

- 补强协议 mock 回归测试：
  - 无 Bot 场景：`/api/admin/protocol/health` 与 `/protocol/probe` 稳定返回 disconnected / failed，不抛异常
  - 缺方法场景：Bot 只有 `get_login_info` 时，`group_list` capability 标为 failed，并记录 `method_missing`
  - 协议失败场景：`get_login_info` 与 `get_group_list` 抛错时，probe 保持 200 响应，连接历史记录 `protocol_probe` 错误事件
  - trace 场景：`ProtocolTraceStore` 记录成功/失败调用、最小容量钳制、失败摘要与敏感参数脱敏
- 计划表同步：
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 4 mock 协议测试标记为已完成
  - 下一步 Now 队列切换到 Phase 6 群 Profile

**影响范围**：`tests/test_admin_api.py`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check tests/test_admin_api.py admin/routes/api/protocol.py services/protocol_trace.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "protocol_mock or protocol_trace_mock or protocol_trace_store or provider_and_protocol"` 通过，5 passed
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py tests/test_config_loader.py tests/test_call_api.py tests/test_client.py tests/test_similarity.py tests/test_plugin_bus.py` 通过，142 passed
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh && find admin/static -name '._*' -print | wc -l` 返回 `0`

**交接说明**：

- 本轮只补协议契约测试，未改生产协议路由代码；当前实现已满足 mock 失败降级契约。
- Phase 4 已基本收口，后续只在实际协议端差异出现时补兼容项。
- 下一步按计划进入 Phase 6 群 Profile，建立每群工具/风格/主动插话/表情/黑话策略配置。

---

## 2026-05-07 Phase 2 Provider 深化：profile 热切换

**变更类型**：feature / backend / frontend / runtime-config

**内容**：

- Provider 选择 API：
  - 新增 `POST /api/admin/providers/selection`
  - 支持保存默认 profile 与 `main / thinker / compact / slang / vision` 任务映射
  - 请求会校验 profile 是否存在，避免写入无效映射
  - 保存时只补丁式更新 `llm.default_profile` 与 `llm.task_profiles`，并写入 JSON 配置
- 运行时热切换：
  - `LLMClient` 新增 `set_task_profiles()`
  - 保存成功后立即更新运行中的任务 profile，不需要重建 aiohttp session，也不清空会话
  - `main` 任务跟随默认 profile，并同步更新 LLMClient 的主模型连接参数
- Admin 系统页：
  - LLM Provider 卡新增默认 profile 选择器和“应用热切换”按钮
  - 任务 profile 从只读小卡升级为紧凑选择器
  - 显示“运行中 / 待应用”状态，保存后刷新 Provider 概览和限流状态
- 计划表同步：
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 2 profile 热切换标记为已完成
  - 下一步 Now 队列切换到 Phase 4 mock 协议测试

**影响范围**：`admin/routes/api/providers.py`、`admin/routes/api/__init__.py`、`services/llm/client.py`、`admin/frontend/src/views/system/SystemView.vue`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`tests/test_admin_api.py`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check admin/routes/api/providers.py admin/routes/api/__init__.py services/llm/client.py tests/test_admin_api.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "provider_selection or provider_and_protocol"` 通过，2 passed
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py tests/test_config_loader.py tests/test_call_api.py tests/test_client.py tests/test_similarity.py tests/test_plugin_bus.py` 通过，139 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh && find admin/static -name '._*' -print | wc -l` 返回 `0`

**交接说明**：

- 本轮只做 profile 选择和任务映射热切换，不做 profile 定义编辑器；新增/修改 base_url、api_key、model 仍建议走配置页。
- 保存会写入 `config/config.json`；如果当前只存在 legacy TOML，会生成 JSON 主配置，不删除 TOML。
- 下一步按计划补 Phase 4 mock 协议测试，确保协议健康、probe、trace 和兼容清单契约可回归。

---

## 2026-05-07 Phase 1 稳定性补强：关键错误聚合

**变更类型**：feature / backend / frontend / observability

**内容**：

- 新增运行期关键错误聚合：
  - `RuntimeErrorStore` 在内存中滚动记录 `WARNING / ERROR / CRITICAL`
  - 按 level、channel、message 生成 signature，聚合同类问题的次数、首次出现和最近出现时间
  - loguru SSE sink 在推送实时日志的同时写入错误聚合，且增加重复安装保护
- Admin API / 服务健康：
  - 新增 `/api/admin/system/errors`
  - `/api/admin/services/health` 新增 `Runtime Errors` 服务项
  - 系统健康能区分无错误、warning、error/critical 等状态
- Admin 系统页：
  - 新增“关键错误”面板
  - 展示 error/warning 数、唯一问题数、滚动容量和最近错误分组
  - 无关键错误时显示紧凑空状态，保留日志页作为深度排查入口
- 计划表同步：
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 1 关键错误聚合标记为已完成
  - 下一步 Now 队列切换到 Phase 2 profile 热切换

**影响范围**：`services/errors.py`、`admin/routes/api/events.py`、`admin/routes/api/system.py`、`services/health.py`、`kernel/types.py`、`bot.py`、`admin/frontend/src/views/system/SystemView.vue`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`tests/test_admin_api.py`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/errors.py admin/routes/api/events.py admin/routes/api/system.py services/health.py kernel/types.py bot.py tests/test_admin_api.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "system_runtime_errors or system_services_health"` 通过，2 passed
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py tests/test_config_loader.py tests/test_call_api.py tests/test_client.py tests/test_similarity.py tests/test_plugin_bus.py` 通过，138 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh && find admin/static -name '._*' -print | wc -l` 返回 `0`

**交接说明**：

- 关键错误聚合是内存滚动诊断，不持久化到数据库；长期追溯仍以普通日志文件为准。
- loguru sink 现在也会接收未打 channel 的 warning/error，用于关键错误面板和 SSE 摘要；普通 DEBUG/INFO 仍只推送带 channel 的运行日志。
- 下一步按计划进入 Phase 2 profile 热切换，需要评估保存结构化配置后是热重载还是提示硬重启。

---

## 2026-05-07 Phase 2 Provider 深化：分 profile rate limit

**变更类型**：feature / backend / frontend / observability

**内容**：

- LLMClient 新增 profile 维度限流状态：
  - 每个 resolved profile 独立记录调用数、成功数、失败数、限流数、快失败数、最近任务、最近错误、最近成功时间、最近限流时间和冷却剩余时间
  - 某个 profile 被 429 后只给该 profile 设置冷却，不污染其他 profile
  - 冷却期内同 profile 请求快失败，避免低优先级任务长时间占用流程
  - thinker/slang 等辅助任务沿用原有降级逻辑；main profile 仍由现有私聊/群聊外层重试兜底
- ChatPlugin：
  - 启动时把 `task -> profile name` 映射传给 LLMClient，用于诊断和限流隔离
- Admin API / Web：
  - `/api/admin/providers` 返回 `rate_limits`
  - Provider profile 行新增限流状态 tag，可见 ready、冷却和历史限流次数
- 计划表同步：
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 2 分 profile rate limit 标记为已完成
  - 下一步 Now 队列切换到 Phase 1 关键错误聚合

**影响范围**：`services/llm/client.py`、`plugins/chat.py`、`admin/routes/api/providers.py`、`admin/routes/api/__init__.py`、`admin/frontend/src/views/system/SystemView.vue`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`tests/test_client.py`、`tests/test_admin_api.py`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/llm/client.py plugins/chat.py admin/routes/api/providers.py admin/routes/api/__init__.py tests/test_client.py tests/test_admin_api.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py tests/test_config_loader.py tests/test_call_api.py tests/test_client.py tests/test_similarity.py tests/test_plugin_bus.py` 通过，137 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh && find admin/static -name '._*' -print | wc -l` 返回 `0`

**交接说明**：

- 本轮不改配置文件语义，不新增 rate limit 配置项；策略使用现有 `RATE_LIMIT_BASE_DELAY` 与最大 60 秒冷却。
- profile 热切换仍未实现，下一步按计划先做 Phase 1 关键错误聚合。

---

## 2026-05-07 Phase 4 协议韧性：历史连接记录

**变更类型**：feature / backend / frontend / observability

**内容**：

- 新增协议连接历史：
  - `ProtocolConnectionHistory` 记录当前状态、连接 Bot 数、`self_id`、最近变化时间、最近确认时间、断连起点、恢复耗时和最近错误
  - `on_bot_connect` 自动记录连接恢复
  - 如果当前 NoneBot Driver 支持 `on_bot_disconnect`，断开时自动记录断连事件
  - `/api/admin/protocol/health` 与服务健康聚合会做安全快照校准，避免只依赖生命周期钩子
- Admin API：
  - 新增 `/api/admin/protocol/connections`
  - `/api/admin/protocol/health` 和 `/api/admin/protocol/probe` 返回 `connection` 摘要
  - 协议探测中登录信息/群列表失败会记录为连接历史错误事件
- Admin 系统页：
  - 协议卡新增“连接历史”面板
  - 展示连接/断开状态、Bot 数、最近变化、最近确认、上次恢复耗时、最近错误和最近事件列表
- 计划表同步：
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md` 将 Phase 4 历史连接记录标记为已完成
  - 下一步 Now 队列切换到 Phase 2 分 profile rate limit

**影响范围**：`services/protocol_trace.py`、`kernel/types.py`、`kernel/router.py`、`bot.py`、`admin/routes/api/protocol.py`、`services/health.py`、`admin/frontend/src/views/system/SystemView.vue`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`tests/test_admin_api.py`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/protocol_trace.py services/health.py kernel/types.py kernel/router.py bot.py admin/routes/api/protocol.py tests/test_admin_api.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py tests/test_config_loader.py tests/test_call_api.py tests/test_client.py tests/test_similarity.py tests/test_plugin_bus.py` 通过，136 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh && find admin/static -name '._*' -print | wc -l` 返回 `0`

**交接说明**：

- 连接历史是内存滚动记录，用于运行期诊断；Bot 进程重启后历史会清空，但维护日志和普通日志仍保留长期信息。
- 本轮不做自动重连、不做协议切换、不替换 NapCat。

---

## 2026-05-07 Phase 2 Provider 测试 + Phase 4 协议兼容清单

**变更类型**：feature / backend / frontend / observability

**内容**：

- Provider 连通性诊断：
  - 新增 `POST /api/admin/providers/{name}/test`
  - 系统页 LLM Provider 卡支持逐个 profile 手动测试
  - 测试请求为显式点击触发，不在页面加载时自动调用外部模型
  - 结果显示耗时、成功/失败和短文本预览/错误摘要
- 协议韧性补强：
  - `/api/admin/protocol/health` 与 `/api/admin/protocol/probe` 返回 NapCat / LLOneBot 兼容清单
  - 新增 `/api/admin/protocol/compatibility`
  - 系统页协议卡新增只读兼容检查表，区分支持、兼容、条件支持、手动确认和未探测
  - 继续保持安全探测策略，不主动发群消息、不测试禁言/踢人/戳一戳等会污染群聊的动作

**影响范围**：`admin/routes/api/providers.py`、`admin/routes/api/protocol.py`、`admin/frontend/src/views/system/SystemView.vue`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`tests/test_admin_api.py`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check admin/routes/api/providers.py admin/routes/api/protocol.py tests/test_admin_api.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py tests/test_config_loader.py tests/test_call_api.py tests/test_client.py tests/test_similarity.py tests/test_plugin_bus.py` 通过，135 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh && find admin/static -name '._*' -print | wc -l` 返回 `0`

**交接说明**：

- Provider 测试按钮用于维护窗口内手动确认 profile 可用性；后续 Phase 2 仍可继续做 profile 热切换与分 profile rate limit。
- 协议兼容清单是排查指引，不代表自动切换到 LLOneBot；NapCat 仍是默认协议端。

---

## 2026-05-07 Phase 1 请求追踪 + Phase 2 分任务 Provider Profile

**变更类型**：feature / backend / frontend / observability

**内容**：

- Phase 1：OneBot 请求 echo/追踪
  - 新增 `services/protocol_trace.py`
  - Bot 连接后自动包装 `bot.call_api`
  - 每次 OneBot API 调用生成本地 `ob_*` 追踪号，记录 action、耗时、成功/失败、错误摘要和脱敏参数
  - 新增 `/api/admin/protocol/traces`
  - `/api/admin/protocol/health` 返回 `trace_summary`
  - 服务健康聚合新增 `Protocol Trace` 服务项
  - 系统页协议卡新增“请求 Echo 追踪”摘要与最近请求列表
- Phase 2：Provider 分任务 profile
  - `LLMConfig` 新增 `task_profiles`
  - 新增 `profile_name_for_task()` 与 `resolve_task_profile()`
  - 默认支持 `main / thinker / compact / slang / vision` 任务映射
  - `ChatPlugin` 启动时把任务 profile 传入 `LLMClient`
  - `LLMClient` 新增 `_call_thinker / _call_compact / _call_slang`
  - thinker 决策、上下文 compact、黑话抽取分别走对应任务 profile
  - `/api/admin/providers` 返回任务到 profile 的映射，系统页 LLM Provider 卡展示任务矩阵

**影响范围**：`services/protocol_trace.py`、`services/health.py`、`kernel/config.py`、`kernel/types.py`、`kernel/router.py`、`bot.py`、`plugins/chat.py`、`services/llm/client.py`、`services/slang/extractor.py`、`admin/routes/api/protocol.py`、`admin/routes/api/providers.py`、`admin/frontend/src/views/system/SystemView.vue`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`tests/test_config_loader.py`、`tests/test_admin_api.py`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/protocol_trace.py services/health.py kernel/types.py kernel/router.py kernel/config.py bot.py plugins/chat.py services/llm/client.py services/slang/extractor.py admin/routes/api/protocol.py admin/routes/api/providers.py tests/test_admin_api.py tests/test_config_loader.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_config_loader.py tests/test_admin_api.py tests/test_plugin_bus.py tests/test_call_api.py tests/test_client.py tests/test_similarity.py` 通过，135 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- 追踪号是 Omubot 本地 echo，不修改 OneBot 协议 payload；用于排查 API 调用链路，不影响协议端兼容性。
- `vision` 任务映射已在配置/API 中预留，但当前视觉仍使用既有 Qwen VL 配置，不强行切到聊天 LLM profile。
- Phase 2 后续可继续做 profile 测试按钮、热切换和分 profile rate limit 策略。

---

## 2026-05-07 Phase 1 稳定性补强：服务级健康聚合

**变更类型**：feature / backend / frontend / observability

**内容**：

- 新增 `services/health.py`：
  - 聚合 LLM、PluginBus、NapCat、SQLite、Memory、Slang 六类服务状态
  - SQLite 使用只读式 `quick_check` 思路检查 `messages.db / memory_cards.db / slang.db`
  - Memory 汇总记忆卡片、消息日志、短期会话可用性
  - Slang 汇总候选、已批准、观察中数量；未初始化时给出 warning 而不阻塞页面
  - PluginBus 汇总启用数、异常数、慢调用数和权限拦截数
- Admin API：
  - `/api/admin/services/health` 返回统一服务健康快照
  - `create_system_router()` 接收 `config`，用于 LLM 与 NapCat 配置判定
- Admin 系统页：
  - 新增“服务级健康”面板
  - 展示整体状态、需关注数量，以及每个服务的状态、指标和诊断说明
  - 保留原资源、Provider、协议探测与备份卡片结构，不重做系统页信息架构

**影响范围**：`services/health.py`、`admin/routes/api/system.py`、`admin/routes/api/__init__.py`、`admin/frontend/src/views/system/SystemView.vue`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`tests/test_admin_api.py`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/health.py admin/routes/api/system.py admin/routes/api/__init__.py tests/test_admin_api.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py tests/test_plugin_bus.py` 通过，66 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- 本轮只做安全只读健康聚合，不做主动发消息、协议破坏性探测或远程模型连通性测试。
- Phase 1 后续还剩请求 echo/追踪与关键错误聚合，可继续接在协议韧性与日志聚合上。

---

## 2026-05-07 插件治理 Phase 3 继续收口：配置持久化与 Hook 预算

**变更类型**：feature / architecture / frontend

**内容**：

- 新增插件私有配置持久化：
  - 新增 `services/plugin_config.py`
  - Bot 启动时创建 `storage/plugins/plugin-config.json` 对应的 `PluginConfigStore`
  - `PluginContext` 暴露 `plugin_config_store`，供插件后续按需读取 Admin 配置
- Admin API 扩展：
  - `/api/admin/plugins/{name}` 返回 `settings`，包含 schema、保存值、合并默认值后的 effective values、保存路径和更新时间
  - 新增 `GET /api/admin/plugins/{name}/settings`
  - 新增 `POST /api/admin/plugins/{name}/settings`
- Admin 插件页：
  - 插件详情抽屉的 `settings_schema` 从只读 JSON 升级为结构化编辑区
  - 支持开关、文本、数字、枚举、字符串列表和 JSON 兜底字段
  - 支持未保存提示、撤销和保存配置
- Hook 耗时预算：
  - `AmadeusPlugin` 新增 `hook_budget_ms`
  - `PluginBus` 支持从 class / manifest 读取预算
  - 超预算 Hook 记录 `slow_calls`、`last_slow_hook` 和 per-hook 慢调用统计，Admin 插件页可见

**影响范围**：`services/plugin_config.py`、`kernel/types.py`、`kernel/bus.py`、`bot.py`、`admin/__init__.py`、`admin/routes/api/__init__.py`、`admin/routes/api/plugins.py`、`admin/frontend/src/views/plugins/PluginsView.vue`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`、`tests/test_plugin_bus.py`、`tests/test_admin_api.py`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/plugin_config.py kernel/types.py kernel/bus.py bot.py admin/__init__.py admin/routes/api/__init__.py admin/routes/api/plugins.py tests/test_admin_api.py tests/test_plugin_bus.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_plugin_bus.py tests/test_admin_api.py` 通过，65 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- 已保存的插件配置不会自动热重载所有插件内部状态；插件需要在启动或自己的控制逻辑中读取 `ctx.plugin_config_store.get(plugin.name)`。
- Phase 3 还剩“插件限流/隔离策略”作为后续增强；按路线表下一步更建议转入 Phase 1 服务级健康聚合。

---

## 2026-05-07 生态路线图固化与插件治理 Phase 3 收口

**变更类型**：feature / architecture / frontend / docs

**内容**：

- 新增阶段计划表：
  - `docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`
  - 按 Phase 1-8 记录已完成、进行中、待做和后置阶段
  - 将 Phase 3 拆为启停状态持久化、权限门禁、配置 schema 展示、插件配置保存、hook 预算和限流/隔离
- 插件启停状态持久化：
  - 新增 `services/plugin_state.py`
  - Admin 切换插件启停后写入 `storage/plugins/plugin-state.json`
  - Bot 启动时先回放持久状态，再应用 `kernel.disabled_plugins` 静态禁用兜底
- 插件权限门禁：
  - `PluginBus` 对 manifest v2 `permissions` 做兼容式门禁
  - 旧插件未声明 permissions 时继续放行
  - 显式声明 permissions 的插件只允许对应 `message / prompt / reply / tick / tool / command / admin` 能力
  - 健康快照新增 `permission_denials`
- Admin API / Web：
  - `/api/admin/plugins/state` 返回持久化状态文件视图
  - `/api/admin/plugins/{name}/state` 切换状态时同步持久化
  - 插件详情抽屉展示持久状态、权限拦截次数和 `settings_schema`
  - 插件工具/命令列表遵守 manifest v2 权限声明

**影响范围**：`docs/superpowers/plans/*`、`services/plugin_state.py`、`kernel/bus.py`、`bot.py`、`admin/__init__.py`、`admin/routes/api/__init__.py`、`admin/routes/api/plugins.py`、`admin/frontend/src/views/plugins/PluginsView.vue`、`admin/static`、`tests/test_plugin_bus.py`、`tests/test_admin_api.py`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check ...` 本轮相关文件通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_config_loader.py tests/test_plugin_bus.py tests/test_call_api.py tests/test_client.py tests/test_admin_api.py tests/test_similarity.py` 通过，130 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh` 构建后清理 99 个 AppleDouble 文件，`admin/static` 已无 `._*`

**交接说明**：

- Phase 3 仍剩“插件配置保存”和“hook 预算/限流策略”未做；下一步建议转入 Phase 1 服务级健康聚合，或继续把插件配置 schema 做成可编辑控件。
- 运行时启停不强制卸载插件内部已启动的后台任务；这类插件仍需要硬重启收口。

---

## 2026-05-07 Omubot 生态借鉴式扩展地基落地

**变更类型**：feature / architecture / backend / frontend

**内容**：

- 稳定性地基：
  - 新增 `services/storage/sqlite.py`，统一 SQLite 连接 PRAGMA：WAL、NORMAL synchronous、foreign_keys、busy_timeout
  - `SlangStore`、`CardStore`、`MessageLog` 接入共享 SQLite helper，降低长期运行时写入锁和连接策略不一致风险
- Provider 多样性：
  - `LLMConfig` 新增 `api_format`、`default_profile`、`profiles`
  - 新增 `LLMProfile` / `LLMCapability`，旧 `llm.base_url/api_key/model/max_tokens` 自动映射为 `main`
  - `LLMClient.call_api` 改为走 `services/llm/provider.py` provider registry，支持 Anthropic 与 OpenAI SSE profile
  - `ChatPlugin` 按 `config.llm.default_profile` 初始化主 LLM client
- 插件治理：
  - `AmadeusPlugin` manifest v2 元数据扩展：`category`、`permissions`、`settings_schema`、`capabilities`、`min_omubot_version`
  - `PluginBus` 新增运行时启停、hook 调用/耗时/异常健康快照
  - `ToolRegistry` 增加 `clear()`，插件启停后可刷新工具注册表
  - Admin API 新增 `/api/admin/plugins/health` 与 `/api/admin/plugins/{name}/state`
- 协议与 Provider 可观测：
  - Admin API 新增 `/api/admin/providers`
  - Admin API 新增 `/api/admin/protocol/health` 与 `/api/admin/protocol/probe`
  - 协议探测仅做安全只读能力检查，不发送消息、不执行群管理动作
- 轻量语义增强：
  - 新增 `services/similarity.py`，提供默认 ngram similarity 与 embedding 安全 stub
  - 黑话 store 的 normalize/ngram 相似度改为复用统一 provider
  - `BotConfig.memory.semantic` 预留默认关闭的语义增强配置
- Admin Web：
  - 系统页新增 LLM Provider 与协议能力概览/探测卡片
  - 插件页显示 category、permissions、health、hook 统计，并支持运行时启停

**影响范围**：`kernel/config.py`、`kernel/bus.py`、`kernel/types.py`、`services/llm/*`、`services/storage/*`、`services/similarity.py`、`services/slang/store.py`、`services/memory/*`、`admin/routes/api/*`、`admin/frontend/src/views/system/SystemView.vue`、`admin/frontend/src/views/plugins/PluginsView.vue`、`admin/static`

**验证**：

- `python -m py_compile ...` 核心后端文件通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check ...` 本轮相关后端/测试文件通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_config_loader.py tests/test_plugin_bus.py tests/test_call_api.py tests/test_client.py tests/test_admin_api.py tests/test_similarity.py` 通过，129 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

**交接说明**：

- 插件启停目前是运行时治理入口；`kernel.disabled_plugins` 仍用于启动时禁用。
- Provider profiles 默认不改变旧配置行为；需要多模型时在 `llm.profiles` 中显式增加 profile。
- embedding/FAISS 仍未引入，`memory.semantic.enabled=false` 与 `backend="ngram"` 是默认轻量路径。

---

## 2026-05-07 项目 Wiki 同步黑话 v3 与配置/部署口径

**变更类型**：docs / wiki

**内容**：

- 新增 `docs/wiki/Slang.md`：
  - 记录群内黑话的服务层/插件层/Admin Web 分层
  - 补充状态生命周期、SQLite 表、每日 AI 复核、v3 修订历史、语义漂移、`slang_lookup` 工具、API 与关键设置
  - 明确 v3 默认保持轻依赖，embedding/FAISS 放在 v3.5 可选增强
- 更新 Wiki 入口和旧口径：
  - `_Sidebar.md` 增加“群内黑话”
  - `Home.md` 更新核心特性、插件数量、Admin 面板能力和版本号
  - `Architecture.md` 增加 `services/slang`、`SlangPlugin` 消息路径、动态 Prompt 与工具查询说明
  - `Plugins.md` 更新 19 个插件列表，并补充常见工具与 `slang_lookup`
  - `Configuration.md` 同步 `config/config.json` 主配置、TOML 兼容读取、Admin 配置页保存口径和黑话设置
  - `Deployment.md` 同步 `--no-deps bot` 重建规则、NapCat WS 端口、`storage/slang.db`
  - `Stickers.md` 同步 JSON 主配置路径

**影响范围**：`docs/wiki/*` 与后续项目说明文档阅读口径

**验证**：

- `rg` 检查 Wiki 中已无旧插件数量、旧 WS 端口、旧版本号等主要过期口径
- `bash scripts/cleanup-appledouble.sh` 清理文档编辑产生的 AppleDouble 文件

---

## 2026-05-07 bot 定向重建部署黑话 v3

**变更类型**：deployment

**内容**：

- 按用户要求定向重建并替换 `bot` 服务：
  - 使用 `DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker compose up -d --build --no-deps bot`
  - `qq-bot` 已重新创建并启动
  - `napcat` 未重建、未重启，保持原运行实例
- 容器内确认已包含黑话 v3 前端资源：
  - `admin/static/assets/SlangView-BEjC26cy.js`
  - `admin/static/assets/SlangView-FgExca7P.css`
  - `admin/static/assets/index-CZtMB5rv.js`

**影响范围**：运行中的 `qq-bot` 容器；`napcat` 不受影响

**验证**：

- `docker compose ps`：`qq-bot` 新实例 Up，`napcat` 仍为原实例 Up 22 hours
- `docker logs --tail 120 qq-bot` 显示服务启动完成、OneBot 已连接、`Bot 就绪`
- `docker exec qq-bot find admin/static -name '._*'` 未发现静态目录 AppleDouble 文件
- `GET /admin/slang` 返回 SPA HTML，入口指向 `assets/index-CZtMB5rv.js?v=1778110341`
- `bash scripts/cleanup-appledouble.sh` 清理日志写入后产生的 AppleDouble 文件

---

## 2026-05-07 黑话系统 v3 质量治理与工具化查询

**变更类型**：feature / backend / frontend

**内容**：

- 黑话服务层新增 v3 质量治理能力：
  - `slang_term_revisions` 记录词条创建、编辑、AI 通过、人工复核、合并和漂移治理的前后快照
  - `slang_drift_reviews` 承接已批准词条的冲突新释义，处理前不覆盖主词条、不进入 Prompt
  - `SlangSettings` 增加漂移检测、漂移最低置信度、查询工具、注入最低置信度和 `semantic_backend` 预留项
- Admin API 新增：
  - `GET /api/admin/slang/terms/{id}/revisions`
  - `GET /api/admin/slang/drift`
  - `POST /api/admin/slang/drift/{id}/accept|reject|alias|mute`
- `plugins/slang` 新增 `slang_lookup` 工具：
  - 只查询当前群与全局的已批准词条
  - 无群上下文时只返回全局词条
  - 工具关闭时执行会返回已关闭提示
- `/admin/slang` 增加“语义漂移”治理队列、质量治理侧栏、修订记录/证据链详情区和 v3 结构化设置。
- v3 仍保持轻依赖策略；未引入 embedding、FAISS、BM25、jieba、numpy。

**影响范围**：`services/slang`、`plugins/slang`、`admin/routes/api/slang.py`、`admin/frontend/src/views/slang/SlangView.vue`、`tests/test_slang_*`、`tests/test_admin_api.py`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py -k slang` 通过，19 passed
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/slang plugins/slang admin/routes/api/slang.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh` 构建后追加清理 98 个 AppleDouble 文件；测试/日志写入后复查再清理 3 个和 1 个

**交接说明**：

- 运行中容器若需要立即使用 v3 前端与 API，需要后续定向重建 `bot`；不要触碰 `napcat`。
- v3.5 的 embedding/FAISS 仍只是 `semantic_backend` 预留，默认 Docker 不安装也不加载。

---

## 2026-05-07 Phase 5 轻量语义增强首轮收口

**变更类型**：feature / backend / docs

**内容**：

- `RetrievalGate` 正式接入 `SimilarityProvider`：
  - 保留原有“全量 / 周期刷新 / 关键词匹配 / 最小提示”四层 gate
  - 当关键词未命中且 `memory.semantic.enabled=true` 时，追加“轻量语义匹配”兜底
  - 支持 `memory.semantic.backend = ngram | embedding`
- 语义后端安全降级：
  - 默认 `ngram` 后端可直接使用，无额外依赖
  - 若配置 `embedding` 但未安装实现，运行时会自动回退到 `ngram`
  - 记录 queries / hits / fallbacks / errors / last_error，避免静默失败
- 系统健康补充：
  - `services/health.py` 的 `Memory` 服务项新增语义检索状态摘要
  - 能看出是否启用、当前生效后端以及是否发生降级
- 黑话质量增强补第一刀：
  - `SlangExtractor` 过滤“释义基本等于原词”的低信号候选
  - 减少 LLM 输出空泛定义时污染候选池
- Wiki 补充 optional extra 口径：
  - `docs/wiki/Configuration.md` 增加 `memory.semantic` 配置说明
  - 新增 `docs/wiki/Semantic-Retrieval.md`
  - `_Sidebar.md` 加入“轻量语义检索”入口

**影响范围**：`services/memory/retrieval.py`、`services/health.py`、`plugins/chat.py`、`services/slang/extractor.py`、`tests/test_retrieval.py`、`tests/test_slang_plugin.py`、`docs/wiki/Configuration.md`、`docs/wiki/Semantic-Retrieval.md`、`docs/wiki/_Sidebar.md`、`docs/superpowers/plans/2026-05-07-omubot-ecosystem-roadmap.md`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_retrieval.py tests/test_slang_plugin.py tests/test_admin_api.py tests/test_config_loader.py` 通过，88 passed

**交接说明**：

- 这轮完成的是 Phase 5 的第一段主链，重点是“让语义增强真正进入记忆检索运行路径，并且可降级、可观察”。
- Phase 5 仍未完全结束；后续继续收口黑话常识对比增强和更细的 semantic metrics Web 可视化。

---

## 2026-05-07 Phase 6 群 Profile 落地

**变更类型**：feature / backend / frontend

**内容**：

- 为 `group` 配置链路新增每群 Profile 字段：
  - `reply_style`
  - `custom_prompt`
  - `tools_enabled`
  - `sticker_mode`
  - `slang_enabled`
- 新增群 Profile 持久化接口：
  - `POST /api/admin/groups/{group_id}/profile`
  - `DELETE /api/admin/groups/{group_id}/profile`
  - 保存目标统一写入 `config/config.json`，兼容从 legacy TOML 读取
  - 与全局默认相同的值会自动回退为继承，避免把群覆盖配置写死
- 运行时立即生效：
  - `LLMClient` 读取每群 Profile，向 prompt 注入群聊回复偏好，并按群过滤工具
  - `StickerPlugin` 按群贴纸策略决定是否注入贴纸规则
  - `SlangPlugin` 按群黑话开关决定是否学习、抽取、每日 AI 复核和注入
- `/admin/groups` 抽屉升级为模块化群策略编辑台：
  - 保留群列表、实时状态、最近消息
  - 新增每群风格、主动节奏、工具、贴纸、黑话、附加提示词的结构化控件
  - 支持“恢复全局默认”“重置草稿”“保存群策略”

**影响范围**：`kernel/config.py`、`admin/routes/api/groups.py`、`admin/routes/api/__init__.py`、`services/llm/client.py`、`plugins/chat.py`、`plugins/sticker.py`、`plugins/slang/plugin.py`、`services/slang/daily_reviewer.py`、`admin/frontend/src/views/groups/GroupsView.vue`、`tests/test_config_loader.py`、`tests/test_admin_api.py`、`tests/test_client.py`、`tests/test_slang_plugin.py`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_config_loader.py tests/test_admin_api.py tests/test_client.py tests/test_slang_plugin.py` 通过，104 passed
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过
- `bash scripts/cleanup-appledouble.sh && find admin/static -name '._*' -print | wc -l` 输出 `0`

**交接说明**：

- 这轮完成的是群 Profile 第一版闭环，已经覆盖“保存配置 -> 立即生效 -> Web 编辑”主路径。
- 更细粒度的工具权限矩阵、`blocked_users` 编辑器和群策略审计历史仍留在后续阶段。

---

## 2026-05-07 Vite dev /admin/slang 刷新白屏修复

**变更类型**：fix / frontend-dev

**内容**：

- 修复 `http://localhost:5173/admin/slang` 刷新白屏且未回仪表盘的问题：
  - 原因是 `admin/frontend/vite.config.ts` 的 `SPA_ROUTES` 漏掉 `/admin/slang`
  - Vite dev server 刷新该路径时没有返回开发模式 SPA 入口，客户端路由守卫无法执行
- 同步修复带 query 页面刷新匹配问题：
  - dev proxy bypass 从完整 `req.url` 改为按 `pathname` 判断
  - `/admin/memory?view=browse` 这类地址刷新也能返回 SPA 入口

**影响范围**：`admin/frontend/vite.config.ts`

**验证**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（prebuild 清理 3 个 AppleDouble 文件）
- `bash scripts/cleanup-appledouble.sh` 构建后追加清理 96 个 AppleDouble 文件
- `GET http://localhost:5173/admin/slang` 返回 Vite dev SPA HTML，入口为 `/admin/static/src/main.ts`
- `GET http://localhost:5173/admin/memory?view=browse` 返回 Vite dev SPA HTML
- `GET http://localhost:5173/admin/static/src/main.ts` 返回 `200 text/javascript`

---

## 2026-05-07 bot 定向重建部署黑话筛选栏与刷新回仪表盘

**变更类型**：deployment

**内容**：

- 按用户要求自动定向重建并替换 `bot` 服务：
  - 使用 `DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker compose up -d --build --no-deps bot`
  - `qq-bot` 已重新创建并启动
  - `napcat` 未重建、未重启，保持原运行实例
- 容器内确认已包含最新前端资源：
  - `admin/static/assets/SlangView-DlRSHrNr.js`
  - `admin/static/assets/SlangView-gq1ISRBZ.css`
  - `admin/static/assets/index-BctfOx0m.js`

**影响范围**：运行中的 `qq-bot` 容器；`napcat` 不受影响

**验证**：

- `docker compose ps`：`qq-bot` 新实例已 Up，`napcat` 仍为原实例 Up 22 hours
- `docker logs --tail 90 qq-bot` 显示服务启动完成、OneBot 已连接、`Bot 就绪`
- `docker exec qq-bot find admin/static -name '._*'` 未发现静态目录 AppleDouble 文件
- `GET /admin/static/assets/SlangView-DlRSHrNr.js` 返回 `200 text/javascript`
- `GET /admin/static/assets/index-BctfOx0m.js` 返回 `200 text/javascript`
- `GET /admin/slang` 入口指向 `assets/index-BctfOx0m.js?v=1778108807`

---

## 2026-05-07 黑话筛选栏一体化与刷新回仪表盘

**变更类型**：fix / frontend

**内容**：

- `/admin/slang` 筛选栏从分散工具条重做为一体化 `slang-control-strip`：
  - 审核队列改为连续分段按钮，保留 `待审核 / AI 审核 / 已批准 / 全部` 与数量徽标
  - 搜索、群、作用域、置信度和操作按钮统一收进同一片简约控制条
  - 移除队列按钮副标题，降低视觉噪音
- 前端路由增加一次性刷新判断：
  - 浏览器刷新非仪表盘页面时自动回到 `/admin/`
  - 站内侧栏切换和普通路由跳转不受影响

**影响范围**：`admin/frontend/src/views/slang/SlangView.vue`、`admin/frontend/src/router/index.ts`、`admin/static`

**验证**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（最终构建前自动清理 2 个 AppleDouble 文件）
- `bash scripts/cleanup-appledouble.sh` 构建后追加清理 96 个 AppleDouble 文件

---

## 2026-05-07 bot 定向重建部署黑话队列与记忆导航修复

**变更类型**：deployment

**内容**：

- 按用户要求定向重建并替换 `bot` 服务：
  - 使用 `DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker compose up -d --build --no-deps bot`
  - `qq-bot` 已重新创建并启动
  - `napcat` 未重建、未重启，保持原运行实例
- 容器内确认已包含最新前端资源：
  - `admin/static/assets/SlangView-BPxFxJma.js`
  - `admin/static/assets/SlangView-CWHmGDOq.css`
  - `admin/static/assets/MemoryConsoleView-B3Pok4gv.js`
  - `admin/static/assets/MemoryConsoleView-Cf5mUD0F.css`

**影响范围**：运行中的 `qq-bot` 容器；`napcat` 不受影响

**验证**：

- `docker compose ps`：`qq-bot` 新实例已 Up，`napcat` 仍为原实例 Up 22 hours
- `docker logs --tail 80 qq-bot` 显示服务启动完成、OneBot 已连接、`Bot 就绪`
- `docker exec qq-bot find admin/static -name '._*'` 未发现静态目录 AppleDouble 文件
- `GET /admin/static/assets/SlangView-BPxFxJma.js` 返回 `200 text/javascript`
- `GET /admin/static/assets/MemoryConsoleView-B3Pok4gv.js` 返回 `200 text/javascript`

---

## 2026-05-07 黑话审核栏队列化与记忆页同组导航修复

**变更类型**：fix / frontend

**内容**：

- `/admin/slang` 筛选栏从“状态下拉 + AI 来源下拉”改为审核队列按钮组：
  - `待审核` 对应候选词条
  - `AI 审核` 对应 AI 已通过但待人工复核的 approved 词条
  - `已批准` 对应可注入词表
  - `全部` 用于总览完整词表
- 黑话列表请求参数统一由 `queueMode` 派生，删除状态与复核来源互相改值的 watcher，避免筛选条件打架。
- 修复 `MemoryConsoleView` 在 KeepAlive 后仍监听全局路由的问题：
  - 只在当前路由为 `memory` 时补齐 `view=manage`
  - 离开记忆页后不再把同“数据”分组的页面切换抢回 `/memory`
- 侧栏菜单增加防御性跳转处理，避免重复点击当前完整路由触发无意义导航。

**影响范围**：`admin/frontend/src/views/slang/SlangView.vue`、`admin/frontend/src/views/memory/MemoryConsoleView.vue`、`admin/frontend/src/layouts/components/SideMenu.vue`

**验证**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（最终构建前自动清理 98 个 AppleDouble 文件）
- `bash scripts/cleanup-appledouble.sh` 构建后追加清理 97 个 AppleDouble 文件

---

## 2026-05-07 bot 定向重建部署 Slang 设置修复

**变更类型**：deployment

**内容**：

- 按用户要求定向重建并替换 `bot` 服务：
  - 使用 `DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker compose up -d --build --no-deps bot`
  - `qq-bot` 已重新创建并启动
  - `napcat` 未重建、未重启，保持原运行实例
- 容器内确认已包含最新 Slang 前端资源：
  - `admin/static/assets/SlangView-Des9ufmn.js`
  - `admin/static/assets/SlangView-D-neoPjG.css`

**影响范围**：运行中的 `qq-bot` 容器；`napcat` 不受影响

**验证**：

- `docker compose ps`：`qq-bot` 新实例已 Up，`napcat` 仍为原实例 Up 21 hours
- `docker exec qq-bot ls admin/static/assets | grep SlangView` 可见最新 Slang chunk
- `docker logs --tail 80 qq-bot` 显示服务启动完成、OneBot 已连接、`slang store initialized`

---

## 2026-05-07 Slang 设置保存后新字段清空修复

**变更类型**：fix / frontend

**内容**：

- 修复 `/admin/slang` 保存设置后“每日 AI 识别”模块新字段被清空的问题：
  - 原因是保存成功后前端直接用接口返回的 `data.settings` 整体替换本地 `settings`
  - 当运行中后端暂未返回新字段，或返回体缺少新字段时，开关会变成 `undefined`，数值输入框会显示空
- 新增前端 `defaultSlangSettings` 与 `mergeSettings()`：
  - 加载设置与保存设置后都按“默认值 + 当前本地值 + 接口返回值”合并
  - 保留 `daily_ai_review_enabled`、`daily_ai_review_search_enabled`、`daily_ai_auto_approve_enabled` 等开关状态
  - 对数值字段做兜底转换，避免空值导致输入框被清空

**影响范围**：`admin/frontend/src/views/slang/SlangView.vue`、`admin/static`

**验证**：

- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（prebuild 清理 2 个 AppleDouble 文件；构建后再次清理 96 个 AppleDouble 文件）

---

## 2026-05-07 黑话每日 AI 搜索识别与 AI 通过

**变更类型**：feature / backend / frontend / tests

**内容**：

- `services/slang` 新增每日 AI 复核能力：
  - 新增 `SlangDailyReviewer`，先用现有 LLM 抽取候选，再复用 `web_search` 搜索“是什么梗 / 梗含义”，最后由 LLM 二次复核
  - 新增设置：每日识别开关、执行时间、搜索辅助开关、AI 自动通过开关、自动通过最低置信度、每日每群入库上限、每日扫描消息数
  - AI 通过词条不新增状态，仍写为 `status="approved"`，同时标记 `source="ai_auto_review"` 与 `meta.ai_approved=true`
  - 搜索失败时只降级为候选 / 观察中，不会自动通过
- `plugins/slang` 的 `on_tick` 增加每日定点任务：
  - 使用 `meta:last_daily_ai_review_date` 保证同一天只跑一次
  - 每日任务与现有间隔抽取并存，不替代原 v2/v2.5 抽取链路
- `/api/admin/slang/*` 增加 AI 复核筛选与动作：
  - 列表支持 `review_filter=ai_approved / needs_human_review / human_reviewed`
  - 新增 `human-approve`、`deny`、`return-candidate` 操作
  - “真实通过”只改人工复核元数据；“否决”会静音词条，避免反复学回
- `/admin/slang` 增加 AI 通过管理体验：
  - 指标卡显示 AI 通过数与待人工复核数
  - 列表与抽屉显示 AI 通过、待复核、人工确认标签
  - 抽屉展示 AI 理由、群内证据、搜索查询和搜索证据
  - 设置区新增“每日 AI 识别”模块，保持结构化控件，不使用 raw JSON

**影响范围**：`services/slang/*`、`plugins/slang/plugin.py`、`admin/routes/api/slang.py`、`admin/frontend/src/views/slang/SlangView.vue`、`tests/test_slang_store.py`、`tests/test_slang_plugin.py`、`tests/test_admin_api.py`、`admin/static`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/slang plugins/slang admin/routes/api/slang.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py -k slang` 通过（15 passed）
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（prebuild 清理 2 个 AppleDouble 文件；构建后再次清理 96 个 AppleDouble 文件）

---

## 2026-05-07 Slang Web 主动构建黑话入口

**变更类型**：feature / backend / frontend / tests / deployment

**内容**：

- `services/slang` 新增 `create_term()`，用于 Admin Web 手动创建黑话词条：
  - 支持群内 / 全局作用域、状态、置信度、别名、复述策略、备注和示例证据
  - 手动创建来源标记为 `source="manual"`，`meta.manual=true`
  - 直接批准的词条置信度下限保持为 `0.8`
  - 群内词条要求填写 `group_id`，重复 term / alias 会返回明确错误
- `/api/admin/slang/terms/create` 新增结构化创建接口，不复用抽取候选缓冲逻辑
- `/admin/slang` 页头新增“新建黑话”按钮：
  - 打开同风格抽屉填写术语、释义、别名、作用域、群号、状态、置信度、复述策略、示例与备注
  - 创建成功后自动刷新摘要、统计、群列表和当前词条列表，并切换到对应筛选
- 已重新构建前端静态资源并定向重建 `bot`，`napcat` 未重建、未重启

**影响范围**：`services/slang/store.py`、`admin/routes/api/slang.py`、`admin/frontend/src/views/slang/SlangView.vue`、`tests/test_slang_store.py`、`tests/test_admin_api.py`、`admin/static`、运行中的 `qq-bot` 容器

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/slang admin/routes/api/slang.py tests/test_slang_store.py tests/test_admin_api.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py -k slang` 通过（11 passed）
- `cd admin/frontend && npm run build` 通过（构建前自动清理 104 个 AppleDouble 文件）
- 容器内 API router 已包含 `/api/admin/slang/terms/create`
- 登录后空 payload 调用 `/api/admin/slang/terms/create` 返回 `{"ok":false,"error":"term cannot be empty"}`，确认路由可达且未写入数据
- `docker compose ps`：`qq-bot` 已重新创建并启动，`napcat` 保持原运行实例

---

## 2026-05-07 Admin 静态资源路由修复，Slang 白屏恢复

**变更类型**：fix / backend / deployment

**内容**：

- 排查 `/admin/slang` 白屏：
  - `GET /admin/static/assets/index-*.js` 与 `GET /admin/static/assets/SlangView-*.js` 返回 `200 text/html`，内容是 SPA `index.html`
  - 浏览器因此把 HTML 当作 JavaScript module 加载，导致页面直接白屏
- 修复 `admin/__init__.py` 静态资源路由：
  - 在 SPA history fallback 前新增显式 `GET /admin/static/{asset_path:path}` 文件响应
  - 静态文件存在时返回 `FileResponse`，缺失时返回真正 `404`
  - 保留原 `StaticFiles` mount，但不再依赖其在 `APIRouter.include_router` 下的行为
- 追加缓存恢复措施：
  - SPA HTML 与静态资源响应增加 `Cache-Control: no-store`
  - SPA HTML 增加 `Clear-Site-Data: "cache"`
  - SPA HTML 为入口 JS/CSS 自动追加 `?v=<index mtime>`
  - 重新构建前端，入口文件变为 `index-CLzXCTQg.js`，Slang 页面 chunk 变为 `SlangView-D0HzJKfr.js`，避开浏览器此前缓存的坏模块
- 已仅重建并替换 `bot` 服务，`napcat` 未重建、未重启

**影响范围**：`admin/__init__.py`、运行中的 `qq-bot` 容器；`napcat` 不受影响

**验证**：

- 本地 TestClient：`/admin/static/assets/SlangView-bLHe8fEH.js` 返回 `200 text/javascript`，缺失资源返回 `404`
- 部署后 curl：
  - `/admin/slang` 返回 `Cache-Control: no-store`、`Clear-Site-Data: "cache"`，入口指向 `/admin/static/assets/index-CLzXCTQg.js?v=1778102840`
  - `/admin/static/assets/index-CLzXCTQg.js?v=1778102840` 返回 `200 text/javascript`
  - `/admin/static/assets/SlangView-D0HzJKfr.js` 返回 `200 text/javascript`
  - `/admin/static/assets/not-found.js` 返回 `404`
- `docker compose ps`：`qq-bot` 已重新创建并启动，`napcat` 保持原运行实例

---

## 2026-05-07 Slang API 404 导致列表加载失败排查与 bot 定向重建

**变更类型**：fix / deployment

**内容**：

- 排查 `/admin/slang` 显示“黑话列表加载失败”：
  - `docker logs qq-bot` 显示 `/api/admin/slang/summary`、`/groups`、`/terms`、`/settings`、`/stats`、`/pending`、`/extract/runs` 全部返回 `404`
  - 容器内确认旧运行镜像缺少 `admin.routes.api.slang` 与 `services.slang`，原因是前端静态资源已更新，但 `qq-bot` 后端容器尚未重建
- 已仅重建并替换 `bot` 服务：
  - 首次 `docker compose up -d --build --no-deps bot` 被 Docker buildx 本机权限文件阻塞
  - 改用 `DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker compose up -d --build --no-deps bot` 成功
  - `napcat` 未重建、未重启，保持原运行实例
- 重建后容器内确认 slang 路由已注册，浏览器接口不再 404

**影响范围**：运行中的 `qq-bot` 容器；`napcat` 不受影响

**验证**：

- `docker compose ps`：`qq-bot` 已重新创建并启动，`napcat` 仍 Up 20 hours
- 容器内 `admin.routes.api.slang` 与 `services.slang` 均可导入
- 容器内 API router 已包含 `/api/admin/slang/terms`、`/summary`、`/stats`、`/pending` 等 slang 路由
- 登录后请求 `GET /api/admin/slang/terms?page=1&page_size=50&status=candidate` 返回 `200`，响应 `{"terms":[],"total":0,...}`

---

## 2026-05-07 群内黑话系统 v2 / v2.5 增强落地

**变更类型**：feature / backend / frontend / tests

**内容**：

- `services/slang` 增强为 v2/v2.5 能力层：
  - 新增低频候选缓冲、抽取运行日志、批量状态操作、观察记录批量删除、词条合并、跨群 global 候选扫描、统计汇总与置信度重算
  - `candidate_min_count` 现在真正控制候选进入审核列表；未达阈值的候选进入“观察中”
  - `normalize_term` 扩展全半角、Markdown 标点和常见符号归一；新增 stoplist 与 muted 二次过滤
  - Prompt 注入增加 `max_prompt_chars` 限制，并优先当前对话命中、群内高置信词，再补全局词
- `plugins/slang` 保持薄插件定位：
  - 手动/定时抽取会记录 `slang_extraction_runs`
  - 抽取时按批次消息估算出现次数，并传入 `candidate_min_count`
  - 可选自动跨群提升仍只生成 global candidate，不自动批准
- `/api/admin/slang/*` 新增 v2/v2.5 管理接口：
  - `POST /terms/bulk`、`POST /terms/merge`、`POST /global/scan`、`GET /stats`
  - `GET /extract/runs`、`GET /pending`、`POST /terms/{id}/recompute-confidence`
- 管理端 `/admin/slang` 升级为审核控制台：
  - 新增统计卡、群活跃排行、最近抽取记录、观察中候选、跨群扫描、作用域筛选
  - 列表支持多选与批量批准/静音/过期/删除观察记录
  - 抽屉增加置信度来源摘要、重算入口和“合并到主词条”
  - 设置区新增跨群提升、批量页大小、统计窗口、stoplist、Prompt 字符上限等结构化控件

**影响范围**：`services/slang/*`、`plugins/slang/plugin.py`、`admin/routes/api/slang.py`、`admin/frontend/src/views/slang/SlangView.vue`、`tests/test_slang_store.py`、`tests/test_slang_plugin.py`、`tests/test_admin_api.py`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py -k slang` 通过（9 passed）
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/slang plugins/slang admin/routes/api/slang.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过（最终构建前自动清理 97 个 AppleDouble 文件）

---

## 2026-05-07 群内黑话系统 v1 落地

**变更类型**：feature / backend / frontend / tests

**内容**：

- 新增 `services/slang` 系统服务层，提供黑话类型、SQLite 存储、候选生命周期、Prompt 注入文本生成与轻量 LLM 抽取器
- 新增 `plugins/slang` 薄插件接入消息管线：
  - `on_message` 记录已知黑话命中
  - `on_tick` 按设置批量抽取候选
  - `on_pre_prompt` 只注入当前群已批准黑话
- 新增 `/api/admin/slang/*` 管理接口，支持摘要、分页列表、详情、审核状态切换、结构化设置和手动抽取
- 新增管理端 `/admin/slang` 页面与侧栏“群内黑话”入口，提供指标、筛选、候选审核、抽屉编辑和学习/注入设置
- 默认保持审核优先，不引入 `jieba / sentence-transformers / faiss / rank-bm25` 等重依赖，不修改内核钩子和人设文件

**影响范围**：`services/slang/*`、`plugins/slang/*`、`admin/routes/api/slang.py`、`admin/frontend/src/views/slang/SlangView.vue`、`admin/frontend/src/router/index.ts`、`admin/frontend/src/layouts/components/SideMenu.vue`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py -k slang` 通过（4 passed）
- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run ruff check services/slang plugins/slang admin/routes/api/slang.py tests/test_slang_store.py tests/test_slang_plugin.py` 通过
- `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit` 通过
- `cd admin/frontend && npm run build` 通过

---

## 2026-05-07 重启按钮失败排查与接口缺失提示增强

**变更类型**：fix / frontend / deployment

**内容**：

- 排查“点击重启 Bot 显示失败”：
  - 线上 `qq-bot` 容器运行的是旧代码，`/api/admin/system/restart` 路由不存在，实测返回 `404`
  - 已执行 `docker compose up -d --build bot` 重建并重启 `bot`（`napcat` 未重建、持续运行）
  - 重建后实测 `POST /api/admin/system/restart` 返回 `200`，并可触发容器自动拉起
- 前端重启按钮错误提示增强：
  - `404` 时明确提示“运行中的 Bot 不支持重启接口，请先重建并重启容器”
  - `401` 时提示登录状态失效
  - 其他异常仍显示通用失败

**影响范围**：`admin/frontend/src/components/common/RestartBotButton.vue`、运行中 `qq-bot` 容器

**验证**：

- `curl -X POST /api/admin/system/restart`：重建前 `404`，重建后 `200`
- `docker compose ps`：`qq-bot` 已重建并自动拉起，`napcat` 保持原运行实例

---

## 2026-05-07 配置 JSON 空白兜底与页面级重启入口统一

**变更类型**：fix / frontend

**内容**：

- 撤销顶栏全局重启入口，恢复为页面内操作：`header` 不再放“重启 Bot”按钮
- 新增可复用组件 `RestartBotButton` 并按配置页样式统一接入以下页面 action 区：
  - `仪表盘`、`用量统计`、`日志`、`系统`、`人设编辑`
- 配置页增强“JSON 空内容”兜底：
  - 当接口 `schema` 为空时，自动切换到高级 JSON 模式并显示明确提示
  - 增加“顶层配置”分组，保证非 object 根字段不被隐藏
  - 当 `editor.values` 为空但 `advanced.raw_json` 有内容时，自动回填可编辑值
  - 结构化 schema 缺失时禁止关闭高级模式，避免再次出现“页面看起来无内容”

**影响范围**：`admin/frontend/src/layouts/normal/header/index.vue`、`admin/frontend/src/components/common/RestartBotButton.vue`、`admin/frontend/src/views/config/ConfigView.vue`、`admin/frontend/src/views/dashboard/DashboardView.vue`、`admin/frontend/src/views/usage/UsageView.vue`、`admin/frontend/src/views/logs/LogsView.vue`、`admin/frontend/src/views/system/SystemView.vue`、`admin/frontend/src/views/soul/SoulView.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过（含 AppleDouble 自动清理钩子）

---

## 2026-05-07 Config 路径显示统一与全局重启入口

**变更类型**：fix / frontend

**内容**：

- 配置页展示路径增加前端兜底规范化：即使接口仍返回 `.toml` 路径，页面统一显示为对应 `.json` 目标路径
- 顶栏新增全局“重启 Bot”按钮（带二次确认与状态提示），所有页面都可直接触发 `/api/admin/system/restart`

**影响范围**：`admin/frontend/src/views/config/ConfigView.vue`、`admin/frontend/src/layouts/normal/header/index.vue`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过

---

## 2026-05-07 Admin 顶栏去标签 + Config 结构化编辑 + 一键重启

**变更类型**：refactor / frontend / backend / docs / tests / deployment

**内容**：

- 管理端顶部多标签体系下线：
  - 顶栏移除 `AppTab` 区域，正文可视面积增大
  - 删除 tabs 运行链路（`stores/tabs.ts`、`AppTab.vue`、`TabContextMenu.vue`）
  - 路由切换缓存改为基于 `route.meta.keepAlive` 的单路由视图模式
- 配置中心改造为结构化编辑器：
  - `GET /api/admin/config` 升级为结构化模型返回（`format_mode`、`migration_pending`、`editor.schema`、`editor.values`、`advanced.raw_json`）
  - `POST /api/admin/config` 支持 `structured` 与 `advanced` 两种保存模式，统一 `BotConfig` 校验并返回字段级错误
  - 前端 `ConfigView` 改为“分组模块 + 字段控件（switch/input/number/list/kv/json）”，不再默认原文直出
  - Secret 字段默认部分遮罩展示，可按需进入编辑
- 配置格式迁移口径调整：
  - 运行时配置默认路径切到 `config/config.json`，并保留 `config.toml` 兼容读取
  - legacy TOML 首次保存后写出 JSON 主文件，不删除原 TOML
- 新增配置页一键重启：
  - `POST /api/admin/system/restart` 新增，确认后延迟退出进程，适配 Docker 自动拉起
  - `/admin/config` 页右上角新增“重启 Bot”按钮（带二次确认）
- Docker 与文档口径同步：
  - `docker-compose.yml` 调整为 `./config:/app/config:rw`，确保 Web 保存配置可持久化
  - `README.md`、`docs/setup-guide.md`、`docs/project-info.md` 同步补充 JSON 主格式与 legacy 兼容说明

**影响范围**：`admin/frontend/src/App.vue`、`admin/frontend/src/layouts/normal/header/index.vue`、`admin/frontend/src/router/index.ts`、`admin/frontend/src/views/config/*`、`admin/routes/api/config.py`、`admin/routes/api/system.py`、`kernel/config.py`、`docker-compose.yml`、`README.md`、`docs/setup-guide.md`、`docs/project-info.md`、`tests/test_admin_api.py`、`tests/test_config_loader.py`

**验证**：

- `./node_modules/.bin/vue-tsc --noEmit`
- `npm run build`
- `pytest tests/test_config_loader.py`
- `pytest tests/test_admin_api.py -k "config or system"`

---

## 2026-05-07 Sticker 分页、System 状态修复、Dashboard 时序重排

**变更类型**：fix / frontend / backend / tests / deployment

**内容**：

- `Stickers` 页面新增分页机制：当素材数量超过阈值时自动分页，并在列表顶部与底部同时显示页码按钮（含快速跳转）
- `System` API 修复 NapCat 连通状态误报：
  - `/api/admin/health` 改为动态检查已连接 bot，不再使用路由初始化时的静态引用
  - 返回 `connected_bots` 便于排查连接态
- `System` API 增加资源统计 fallback：
  - 无 `psutil` 依赖时使用标准库与 `/proc` 提供 CPU/内存/磁盘/进程信息
  - 活跃会话统计优先读取 `ShortTermMemory._store`，兼容旧 `_messages` 结构
- `Dashboard` 修复“下一段节奏不刷新”：
  - 改为基于当前时间实时重排全量日程，不再只看前 4 段固定切片
  - 已过时段自动灰显，未到达时段优先置顶，日程列表展示完整全天条目
- `Dashboard` 实时日志收口：
  - 过滤卡片相关日志项，避免干扰主监控视图
  - 展示总条数限制为最近 10 条
- 新增系统接口回归测试，覆盖动态健康判定与会话统计

**影响范围**：`admin/frontend/src/views/stickers/StickersView.vue`、`admin/frontend/src/views/dashboard/DashboardView.vue`、`admin/routes/api/system.py`、`admin/routes/api/__init__.py`、`tests/test_admin_api.py`

**验证**：

- `UV_CACHE_DIR=/private/tmp/omubot-uv-cache uv run pytest tests/test_admin_api.py -k "system or soul"` 通过
- `./node_modules/.bin/vue-tsc --noEmit` 通过
- `npm run build` 通过（已刷新 `admin/static` 产物）

---

## 2026-05-10 黑话 daily review pending 复核闭环

**变更类型**：fix / backend / tests / deployment

**内容**：

- daily review 不再只复核最近消息抽取结果，也会按群限量复核 `slang_pending_candidates`
- web search 从自动通过的唯一门槛降级为辅助证据；群内重复证据足够时也可 AI 通过
- AI 明确判定“不通过”的 pending 会转成 `muted` 词条并清出待处理队列
- 日志补充 `pending_reviewed`、`pending_approved`、`pending_rejected`、`pending_kept`，便于 Docker 日志对账

**验证**：

- `uv run pytest tests/test_slang_plugin.py -q` 通过
- `uv run pytest tests/test_slang_store.py tests/test_admin_api.py -q` 通过
- `uv run ruff check services/slang/daily_reviewer.py services/slang/store.py plugins/slang/plugin.py tests/test_slang_plugin.py tests/test_slang_store.py tests/test_admin_api.py` 通过
- `docker compose up bot -d --build --no-deps` 已重建并启动 bot

---

## 归档索引

- 早期实施期维护记录已归档至 [docs/audits/maintenance-log-archive-2026-04-29-to-2026-05-06.md](docs/audits/maintenance-log-archive-2026-04-29-to-2026-05-06.md)
- 当前主日志保留 2026-05-07 起仍频繁交接的活跃维护记录
