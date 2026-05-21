# Slang DB 反复损坏全治本计划 — 实施跟踪

跟踪 `~/.claude/plans/modular-forging-allen.md`（计划原文）的三阶段落地状态。每完成一项打勾，并记录验证证据（命令输出、commit、observable artifact）。

## 元信息

- **计划提出日期**：2026-05-20
- **计划批准**：2026-05-20，用户选定"全治本 #1+#2+#3+#4+#5+#6"
- **当前阶段**：Phase 1 已落地未部署 → Phase 2 待启动（前置：Phase 1 部署后稳定 ≥24h）→ Phase 3 待启动（前置：Phase 1+2 稳定 ≥24h）
- **铁律约束**（CLAUDE.md）：
  - `docker compose restart bot` 只重启 bot，**绝不动 napcat**（设备指纹 → anti-fraud）
  - `./admin/static` 是 bind mount，前端只 `npm run build` 即生效，无需 rebuild bot
  - 跑全量 pytest 前先 `pkill -9 -f pytest`，否则会与 IDE 抢 sqlite 文件锁

---

## 根因摘要

`storage/slang.db` 在 2026-05-11（3 次）/ 2026-05-17 / 2026-05-20 反复物理损坏，每 5–10 天一次。

**真正根因**：macOS Docker bind mount + SQLite WAL + `synchronous=NORMAL` 在重启 / checkpoint 时的 fsync 排序漏洞。

**贡献因子**：

1. graceful shutdown 直接 `await db.close()`，未先 `wal_checkpoint(TRUNCATE)`，WAL 帧滞留
2. 主机 `scripts/dev/slang_*.py` 与容器 bot 跨进程锁定域分裂（host inode POSIX 锁 vs 容器 WAL/shm 锁，互看不见）
3. `slang_db_repair.py` `_sqlite_recover` 用 `text=True` 解码 `.recover` 二进制流，遇非 UTF-8 字节炸 `UnicodeDecodeError`，导致脚本路径在抢救时不可用

**预期验收信号**（30 天观察窗口）：部署 Phase 3 后 30 天内不再出现 `database disk image is malformed` 或 `invalid page number 7xxx`。

---

## Phase 1 — 代码侧三连修（零 infra）

**状态**：🟢 已落地 / 🟡 待部署 / ⏸️ 待 24h 观察

**起止**：

- 2026-05-20 22:00 起规划
- 2026-05-20 22:50 全部修改完成
- 2026-05-20 23:05 完成验证（pytest + ruff + pyright）
- 部署窗口：待用户确认

### 落地清单

| # | 任务 | 状态 | 文件 / 证据 |
|---|------|------|-------------|
| #6.1 | `_sqlite_recover` 改 `text=False` 全程 bytes 流 | ✅ | [scripts/dev/slang_db_repair.py](../scripts/dev/slang_db_repair.py) |
| #5.1 | 抽 `_bot_guard.py` 共享模块 | ✅ | [scripts/dev/_bot_guard.py](../scripts/dev/_bot_guard.py) — 导出 `is_bot_running()` / `assert_bot_stopped(*, action, force=False)` |
| #5.2 | `slang_db_repair.py` 删本地 `_is_bot_running` + `_docker_compose_json`，validate/rebuild 也补守卫 | ✅ | [scripts/dev/slang_db_repair.py](../scripts/dev/slang_db_repair.py) |
| #5.3 | `slang_batch_merge_collisions.py` 加 `--force` + 守卫 | ✅ | [scripts/dev/slang_batch_merge_collisions.py](../scripts/dev/slang_batch_merge_collisions.py) |
| #5.4 | `slang_collision_auto_merge.py` apply 路径加守卫 | ✅ | [scripts/dev/slang_collision_auto_merge.py](../scripts/dev/slang_collision_auto_merge.py) |
| #5.5 | `slang_meta_migration_p02.py` 加守卫 | ✅ | [scripts/dev/slang_meta_migration_p02.py](../scripts/dev/slang_meta_migration_p02.py) |
| #5.6 | `style_seed_approved.py` seed/approve/all 路径加守卫 | ✅ | [scripts/dev/style_seed_approved.py](../scripts/dev/style_seed_approved.py) |
| #3.1 | `close_with_checkpoint` async 工具（aiosqlite） | ✅ | [services/storage/sqlite.py](../services/storage/sqlite.py) |
| #3.2 | `close_with_checkpoint_sync` 工具（sqlite3） | ✅ | [services/storage/sqlite.py](../services/storage/sqlite.py) |
| #3.3 | `services/storage/__init__.py` 导出两个新工具 | ✅ | [services/storage/__init__.py](../services/storage/__init__.py) |
| #3.4 | SlangStore close 接入 | ✅ | [services/slang/store.py](../services/slang/store.py) |
| #3.5 | MessageLog close 接入 | ✅ | [services/memory/message_log.py](../services/memory/message_log.py) |
| #3.6 | KnowledgeGraphStore close 接入 | ✅ | [services/knowledge_graph/store.py](../services/knowledge_graph/store.py) |
| #3.7 | BlockTraceStore close 接入 | ✅ | [services/block_trace/store.py](../services/block_trace/store.py) |
| #3.8 | CardStore close 接入 | ✅ | [services/memory/card_store.py](../services/memory/card_store.py) |
| #3.9 | KnowledgeIndexStore close 接入（sync 版） | ✅ | [services/knowledge/store.py](../services/knowledge/store.py) |
| #3.10 | StyleStore close 接入（D1 同模式扫描补充） | ✅ | [services/style/store.py](../services/style/store.py) |
| #3.11 | EpisodicStore close 接入（D1 补充） | ✅ | [services/episodic/store.py](../services/episodic/store.py) |
| #3.12 | ConversationArchiveStore close 接入（D1 补充） | ✅ | [services/conversation_archive/store.py](../services/conversation_archive/store.py) |
| #3.13 | LearningNormalizerStore close 接入（D1 补充） | ✅ | [services/learning_normalizer/store.py](../services/learning_normalizer/store.py) |
| #3.14 | `plugins/chat/plugin.py` `on_shutdown` 补 `card_store.close()` | ✅ | [plugins/chat/plugin.py](../plugins/chat/plugin.py) |
| #3.15 | `plugins/knowledge/plugin.py` 新增 `on_shutdown` 关 KnowledgeIndexStore | ✅ | [plugins/knowledge/plugin.py](../plugins/knowledge/plugin.py) |
| #3.16 | D2 cancel-path 回归测试 | ✅ | [tests/test_storage_sqlite.py](../tests/test_storage_sqlite.py) — 5 用例 |

### Phase 1 验证记录（2026-05-20 23:05）

| 检查 | 命令 | 结果 |
|------|------|------|
| 全量 pytest | `pkill -9 -f pytest; uv run pytest` | **1197 passed / 8 skipped / 2 failed**（pre-existing：`test_admin_api.py::test_system_services_health_endpoint` + `test_backup_service.py::test_disk_usage_warning_threshold`，已通过 `git stash` 验证为环境/磁盘阈值相关，与 Phase 1 无关） |
| ruff（变更文件） | `uv run ruff check <changed files>` | **All checks passed** |
| pyright（变更文件） | `uv run pyright <changed files>` | **0 新错误**；142 个错误是 untracked 历史文件遗留（reportOptionalSubscript / reportAttributeAccessIssue），与 Phase 1 无关 |
| D1 同模式扫描 | grep `await self\._db\.close\(\)` 在 services/ | 命中 10 个 store，全部接入 close_with_checkpoint |
| D2 cancel-path | `tests/test_storage_sqlite.py::test_close_with_checkpoint_cancel_path` | passed — `wait_for(timeout=0.0001)` 取消后，新 `connect_sqlite` 仍能 `PRAGMA quick_check` 拿 `ok` 且数据全留（32 行） |
| D4 外部可观察证据 | `tests/test_storage_sqlite.py::test_close_with_checkpoint_truncates_wal` | passed — 写 64 行后 `*.db-wal` 文件大小 == 0 |

### Phase 1 部署验收清单（待执行）

部署命令（**不动 napcat**）：

```bash
dot_clean . && docker compose up bot -d --build
```

部署后必须验证：

- [ ] `docker logs qq-bot --tail 200` 无 `slang plugin disabled` 行
- [ ] `docker logs qq-bot` 看到 6+ 个 store init 成功
- [ ] 触发一次 `docker compose restart bot`，shutdown 路径无 `wal_checkpoint(TRUNCATE) failed` 警告
- [ ] 重启后 `ls -la storage/*.db-wal` 全部为 0 字节（或文件不存在）
- [ ] BlockTrace SSE 推送正常（admin 页面 alignment row 持续更新）
- [ ] admin 黑话页面有数据
- [ ] 至少 24 小时无 `database disk image is malformed` 或 `invalid page number` 日志

### Phase 1 回滚

单次 `git revert`，无数据迁移。新 helper 只在 close 路径调用，老 close 行为是新行为的真子集（少跑一次 PRAGMA），向后兼容。

### Phase 1 风险与缓解

| 风险 | 缓解 |
|---|---|
| `wal_checkpoint(TRUNCATE)` 阻塞 close 路径过久 | best-effort + try/except，超时不阻塞 close；日志降级 warn |
| 新加的 `KnowledgePlugin.on_shutdown` 引入意外副作用 | 只关 `_kb`，幂等；如果 `_kb is None`（未启用知识库）直接跳过 |
| `_bot_guard` 在 docker 不可用时误拒绝 | `assert_bot_stopped` 检测 `shutil.which("docker")` 不存在时直接 return（unknown 视为安全） |

---

## Phase 2 — DB schema / PRAGMA 调整

**状态**：⬜ 未启动

**前置**：Phase 1 已部署并稳定运行 ≥24 小时（无 `wal_checkpoint failed` 警告 + 无新 corruption alarm）。

**预计起始**：≥ 2026-05-21 23:00（Phase 1 部署 + 24h 观察后）

### 待落地清单

| # | 任务 | 文件 |
|---|------|------|
| #2.1 | slang.db 切到 `journal_mode=DELETE` + `synchronous=FULL` | [services/slang/store.py](../services/slang/store.py) `init()` |
| #2.2 | 启动时记录第一次 `PRAGMA journal_mode` 输出确认切换成功 | [services/slang/store.py](../services/slang/store.py) |
| #4.1 | `BackupConfig` 加 `quick_check_interval_minutes` + `quick_check_enabled` | [kernel/config.py](../kernel/config.py) `BackupConfig` |
| #4.2 | `BackupScheduler._run_loop` 加 quick_check 第二个 tick 周期 | [services/storage/backup_scheduler.py](../services/storage/backup_scheduler.py) |
| #4.3 | `BackupScheduler.reload()` 签名加新字段 | [services/storage/backup_scheduler.py](../services/storage/backup_scheduler.py) |
| #4.4 | quick_check 失败用 loguru `_L.error(extra={"channel": "backup"})` 触达 admin SSE | [services/storage/backup_scheduler.py](../services/storage/backup_scheduler.py) |
| #4.5 | `services/storage/backup.py` stdlib logging → loguru `_L = logger.bind(channel="backup")` | [services/storage/backup.py](../services/storage/backup.py) |
| #4.6 | quick_check 失败立即触发紧急 backup（`pre_change` profile） | [services/storage/backup_scheduler.py](../services/storage/backup_scheduler.py) |
| #4.7 | admin `/api/admin/system/settings` 暴露新字段 | [admin/routes/api/system.py](../admin/routes/api/system.py) |
| #4.8 | admin SystemBackup.vue 加表单字段 | [admin/frontend/src/views/system/components/SystemBackup.vue](../admin/frontend/src/views/system/components/SystemBackup.vue) |
| #4.9 | admin ConfigSystemBackup.vue 同步新字段（保持现有 desync 不加深） | [admin/frontend/src/views/config/components/ConfigSystemBackup.vue](../admin/frontend/src/views/config/components/ConfigSystemBackup.vue) |
| #4.10 | quick_check 复用 [services/health.py](../services/health.py) `_sqlite_probe` | [services/storage/backup_scheduler.py](../services/storage/backup_scheduler.py) |

### Phase 2 验证清单（部署前）

- [ ] 单元：`tests/test_backup_scheduler.py` 加 case，断言 quick_check 失败路径走 loguru error + 紧急 backup
- [ ] 手动模拟：人工把 `storage/slang.db` 后缀几个字节抠掉，调小 interval 到 1 分钟，admin "运行时错误"面板看到红条
- [ ] `uv run pytest && uv run ruff check && uv run pyright` 全绿
- [ ] `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit && npm run build` 通过

### Phase 2 部署验收

- [ ] startup 日志看到 `slang journal_mode=DELETE` + `synchronous=FULL`
- [ ] 1 小时后第一次 quick_check tick 在日志里出现
- [ ] slang 写延迟监控 < 100ms 中位数（如果显著抬高则降回 NORMAL）

### Phase 2 回滚

- #2 回滚：`PRAGMA journal_mode=WAL` 重新打开（bot 启动时再写一次）；DELETE 模式下产生的 db 完全兼容 WAL 模式，无数据迁移
- #4 回滚：`BackupConfig.quick_check_enabled = false` 即可关掉，不删字段

### Phase 2 风险与缓解

| 风险 | 缓解 |
|---|---|
| slang DELETE 模式下高频写延迟变高 | slang 不是热路径（每天数百次写），`synchronous=FULL` 成本可承受；监控 > 100ms 中位数则降回 NORMAL |
| quick_check 在 1 小时 tick 时碰巧赶上写高峰，性能抖动 | tick 时机加 jitter；`PRAGMA quick_check` 是只读，不阻塞写 |

---

## Phase 3 — Infrastructure（storage → Docker named volume）

**状态**：⬜ 未启动

**前置**：Phase 1+2 已合入并稳定运行 ≥ 24 小时。

**预计起始**：≥ Phase 2 部署 + 24h 观察后

### 待落地清单

| # | 任务 | 文件 |
|---|------|------|
| #1.1 | `docker-compose.yml` bot 服务 `./storage:/app/storage` → `omubot-storage:/app/storage` | [docker-compose.yml](../docker-compose.yml) |
| #1.2 | `docker-compose.yml` 顶层 `volumes:` 块声明 `omubot-storage` named volume | [docker-compose.yml](../docker-compose.yml) |
| #1.3 | napcat 服务**不动**（铁律） | [docker-compose.yml](../docker-compose.yml) |
| #1.4 | `./config` 保持 bind mount（人手编辑频繁、热重载需要） | [docker-compose.yml](../docker-compose.yml) |
| #1.5 | `./admin/static` 保持 bind mount（npm run build 直出，铁律 D6） | [docker-compose.yml](../docker-compose.yml) |
| #1.6 | `scripts/backup-databases.sh` 改 `docker exec qq-bot uv run python -m services.storage.backup create --host-mode` | [scripts/backup-databases.sh](../scripts/backup-databases.sh) |
| #1.7 | `scripts/dev/_bot_guard.py` 扩展 named volume 检测，强制走 `docker exec` | [scripts/dev/_bot_guard.py](../scripts/dev/_bot_guard.py) |
| #1.8 | 新建 `scripts/dev/storage_export.sh` 给开发者导出 named volume 快照 | [scripts/dev/storage_export.sh](../scripts/dev/storage_export.sh) |

### Phase 3 数据迁移步骤（约 5 分钟，bot 停机；napcat 全程不动）

1. `docker compose stop bot`（**不带 napcat**）
2. `cp -a storage storage.bind-mount-snapshot-$(date +%Y%m%d-%H%M%S)`（host 侧最后一份 bind mount 快照）
3. `docker volume create omubot-storage`
4. `docker run --rm -v "$PWD/storage":/src -v omubot-storage:/dst alpine sh -c "cp -a /src/. /dst/"`
5. 应用 docker-compose.yml 改动
6. `docker compose up bot -d --build`（不带 napcat）
7. 观察 startup 日志：5 个 store init 成功 + 第一次 SSE `cache_pipelines`/`block_trace` 推送 + slang plugin 不再 `disabled`
8. 24 小时后确认 quick_check 全绿、无新 corruption alarm，再删除 `storage.bind-mount-snapshot-*`

### Phase 3 验证清单

- [ ] `docker exec qq-bot uv run python -c "from pathlib import Path; print(list(Path('/app/storage').glob('*.db')))"` 列出迁移后所有 db
- [ ] `docker exec qq-bot sqlite3 storage/slang.db "PRAGMA quick_check; PRAGMA journal_mode;"` 应返回 `ok` + `delete`（Phase 2 已切）
- [ ] admin 黑话页面有数据；BlockTrace 页面 alignment row 有 provider 列
- [ ] 部署后 30 天内不出现新的 `database disk image is malformed`（验证窗口长，但这是真正的成功标准）

### Phase 3 回滚步骤

1. `docker compose stop bot`
2. 应用 docker-compose.yml 反向 patch（named volume → bind mount）
3. 用 `scripts/dev/storage_export.sh` 把 named volume 内当前数据导出到 `storage/`，否则丢失迁移期间产生的写入
4. `cp -a storage.bind-mount-snapshot-* storage/` 还原（仅当导出失败时）
5. `docker compose up bot -d --build`

### Phase 3 风险与缓解

| 风险 | 缓解 |
|---|---|
| named volume 迁移期间 ~5min 服务停机 | 选低峰部署窗口；napcat 不停，QQ 连接不重连；快照保留兜底 |
| 主机侧观测 / 调试不方便 | `scripts/dev/storage_export.sh` 提供明确导出口径；备份系统已自动备份不依赖此脚本 |
| 误删 named volume | 部署 24h 后才允许删除 `storage.bind-mount-snapshot-*`；named volume 本身不会被 `docker compose down` 删掉（除非 `-v`） |

---

## 跨 Phase 依赖

```
Phase 1 (代码侧 close_with_checkpoint + _bot_guard + UTF-8 修复)
    │
    ▼ 24h 稳定观察
Phase 2 (slang journal_mode=DELETE + hourly quick_check)
    │   依赖 Phase 1 的 close_with_checkpoint：DELETE 模式下没有 WAL，
    │   但其他 store 仍是 WAL，需要 Phase 1 的 checkpoint 兜底
    ▼ 24h 稳定观察
Phase 3 (storage → Docker named volume)
    │   依赖 Phase 1 的 _bot_guard：named volume 模式下主机侧 sqlite3
    │   不能再访问，必须走 docker exec；guard 要扩展 named volume 检测
    ▼ 30 天观察窗口
真正验收 (无 corruption)
```

---

## 与备份系统的关系

| 系统 | 角色 | 上线日期 |
|---|---|---|
| BackupScheduler | RPO 工具（最大可丢窗口） | 2026-05-17 |
| Phase 1 close_with_checkpoint | prevention（截断坏帧不写盘） | 2026-05-20（待部署） |
| Phase 2 hourly quick_check | detection（损坏发生时立即告警 + 紧急 backup） | 待启动 |
| Phase 3 named volume | 根因消除（绕开 macOS bind mount fsync 漏洞） | 待启动 |

四条线互补，不冲突——detection 把 RPO 与 prevention 衔接成完整闭环。

---

## 历史损坏记录（用于真正验收）

| 日期 | 形态 | 应对 |
|---|---|---|
| 2026-05-11（3 次）| 短窗口连环损坏 | 手动 `.recover` |
| 2026-05-17 | 单次损坏 | 手动 `.recover`，BackupScheduler 上线 |
| 2026-05-20 13:07 | b-tree page 7xxx 越界，约 5 条增量丢失 | 手动 shell pipe `.recover`（绕开 `slang_db_repair.py` UTF-8 bug） |

**真正验收信号**：Phase 3 部署后 30 天内不再出现新条目。

---

## 决策记录

| 日期 | 决策 | 原因 |
|---|---|---|
| 2026-05-20 | 同时修代码 + PRAGMA + 运维三层（不止修脚本） | 5 月以来反复损坏 5 次，单层修复（如只改备份）已被证伪 |
| 2026-05-20 | 分 3 阶段独立交付，每阶段独立可回滚 | 每阶段风险递增，独立 24h 观察确保不会一次性引入新问题 |
| 2026-05-20 | Phase 1 #3 把扫描范围从计划内 6 个 store 扩到 10 个（D1 同模式扫描） | 同模式问题只修一半 = 24 小时内同模式第二刀 |
| 2026-05-20 | slang.db **定向**切 DELETE，其他 store 不动 | slang 是已知反复损坏者；其他 store 由 Phase 1 + Phase 3 兜底，不必牺牲写性能 |
| 2026-05-20 | `./config` + `./admin/static` 保持 bind mount，只迁 `./storage` | config 需热重载，admin/static 是 npm build 直出（铁律 D6） |
| 2026-05-20 | `services/llm/usage.py` 暂不接入 close_with_checkpoint | 用裸 aiosqlite 不带 WAL setup，TRUNCATE 是 no-op，等 store 体系重构时一起处理 |

---

## 待跟进 / 已知遗留

- `docs/project-info.md` 暂未引用本计划，待 Phase 3 完成后总结写入"运维基线"
- `services/llm/usage.py` 用裸 aiosqlite 不带 WAL setup，未来重构时一起统一
- 30 天验收窗口结束（2026-06-19+）后，本 tracker 收尾 + 维护日志写最终条目

---

## 引用

- 计划原文：`~/.claude/plans/modular-forging-allen.md`
- Phase 1 维护日志：[maintenance-log.md](../maintenance-log.md) 顶部条目
- 损坏抢救日志：[maintenance-log.md](../maintenance-log.md) "2026-05-20（深夜·补丁 2）Slang DB 物理损坏抢救"
- 相关纪律：[docs/agent-discipline.md](agent-discipline.md) D1（同模式扫描）+ D2（cancel-path 测试）+ D4（完成声明含证据）
