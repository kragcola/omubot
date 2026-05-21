# Omubot 待做与待观察清单

> 本文件汇总当前所有未收尾的事项与观察期约定。每条都标注【触发源】+【负责文档/代码】+【完成判据】，避免分散在各 tracker 之间。
> 每完成一项立即划掉并把证据回写到对应 tracker；新增长尾事项也先进本文再分流。

最后更新：2026-05-21（Phase 3 B 段切换完成后）

---

## 1. 主动观察期（含截止时间）

### 1.1 slang.db 反复损坏 — Phase 3 named volume 24h 观察

- **起算**：2026-05-21 16:30
- **截止**：2026-05-22 16:30
- **观察对象**：
  - admin「运行时错误」面板无新红条
  - hourly `quick_check` tick 全绿（[services/storage/backup_scheduler.py](../services/storage/backup_scheduler.py)）
  - daily backup 滚动正常
  - bot 日志无 `database disk image is malformed` / `invalid page number`
- **触发源**：[maintenance-log.md](../maintenance-log.md) Phase 3 B 段条目
- **结束动作**：在 [docs/slang-db-corruption-fullfix-tracker.md](slang-db-corruption-fullfix-tracker.md) Phase 3 验证清单上勾 ✅，并在 maintenance-log 顶部追加 24h 通过条目
- **回滚条件**：任意一项失败 → 立即按 Phase 3 回滚步骤 + 用主机快照 `storage.bind-mount-snapshot-20260521-161720` 还原

### 1.2 slang.db — 30 天 corruption 窗口（真验收）

- **起算**：2026-05-21
- **截止**：2026-06-20
- **判据**：30 天内不再出现新的 `database disk image is malformed` 或 `invalid page number 7xxx`
- **触发源**：[docs/slang-db-corruption-fullfix-tracker.md](slang-db-corruption-fullfix-tracker.md) 「真正验收信号」+ [maintenance-log.md](../maintenance-log.md) Phase 3 条目
- **结束动作**：
  - 删除主机快照 `storage.bind-mount-snapshot-20260521-161720`（约 2.6 GB）
  - 在 fullfix tracker 写入「30 天验收通过」收尾条目
  - 取消 admin 红条邮件订阅（如有）
- **失败动作**：本计划三层之一（代码 / PRAGMA / 基础设施）仍未真正抑制根因；需要回到 fullfix tracker 重新审计

---

## 2. 多层学习记忆方案（P3）— 跨 Phase 待落地

> 来源：[docs/audits/multilayer-memory-learning-report-2026-05-17.md](audits/multilayer-memory-learning-report-2026-05-17.md)
> 当前进度（2026-05-21）：A0/A1/A2/A3/A.5/B/C 已落地；D 设计前置审计完成（[multilayer-memory-phase-d-design-audit-2026-05-21.md](audits/multilayer-memory-phase-d-design-audit-2026-05-21.md)），实现未起；E/F 未启动

| Phase | 现状 | 关键缺口 | 报告原文硬前置 |
| --- | --- | --- | --- |
| **D Episodic Reflection** | 🟡 设计前置审计完成（2026-05-21），实现未起 | 报告硬要求都已满足；缺 5 个 gap：promote 桥 / reflection caller / 反思素材源 / 召回路径 / graph edge 双写。详见 [multilayer-memory-phase-d-design-audit-2026-05-21.md](audits/multilayer-memory-phase-d-design-audit-2026-05-21.md) | A2 隐私字段（done）+ A3 episode 状态机（done）+ Phase C 能产 candidate（done）。**无观察期**，可立即起 |
| **E 图谱跨层主索引（远期）** | 🟡 部分提前到 A.5 | A.5 已落 graph schema + 首批 edge 类型；剩余跨层关系（term_used_in_group / style_applies_to_situation / user_corrected_bot_about / episode_supports_profile / doc_supports_fact）未写入路径 | Phase D 落地（含 edge 写入路径）。**无观察期** |
| **F Episodic-to-Declarative（远期）** | 🔴 不启动（前置不足） | declarative_facts 表 + 凝练触发器 + 冲突解决 + 5 态状态机 + 回退路径全部未实现 | 报告硬要求：Phase D **跑过 ≥ 3 个月真实数据** + ≥ 1 个群累计 ≥ 200 条 `enabled_for_prompt` episode + BlockTraceBus（done）。**有观察期，90+ 天** |

> **实操建议**（非报告硬要求）：Phase C 才落地（2026-05-21），admin queue 还没真实样本。建议先观察 1-3 天，让真实流量产出几条 candidate 再起 D，避免没素材就铺反思路径。这是工程直觉，不是 gate。

### 2.1 Phase C 自身的后置任务（dry-run 已跑通后）

- **C.1 admin queue 真实使用反馈**：当前 `MemoryConsolidator.run_once` 只在被显式调用时执行；需要确认是否要接 cron / 周期 tick 还是继续保持「人工触发」语义。决策点：等 24h 后看 admin 是否有人为审阅压力
- **C.2 5 类 typed candidate promote 路径（属于 Phase D 范围）**：当前 `decide_candidate` 只更新 candidate 状态、绝不写生产 store；Phase D 再决定 promote 闸如何挂

### 2.2 报告自身仍需更新的地方

- 报告 § 5 Phase A 状态字段已经落后于 maintenance-log；下次开 Phase D 前先把 § 5 表里的 Phase B / Phase C 条目从「未开始」/「🔴 待落地」改为 ✅ green，并写明落地 commit
- 报告 § 8.3 三步迁移（步骤 1: ContextProvider 并存 → 2: 双跑 → 3: 插件只上报）只走到步骤 1；这是 Phase B 真正的「完成」判据，需要在 ContextProvider 双跑期跑过几轮真实流量后再推进步骤 2

---

## 3. 已落地但带遗留的项目

### 3.1 slang_extractor `meta_json` UTF-8 解码失败

- **症状**：bot 日志反复出现 `slang extraction failed | error=Could not decode to UTF-8 column 'meta_json' with text '{...}'`
- **判定**：与 Phase 3 无关（`cp -a` 字节级复制不会改 db 内容）；是 pending 候选 `meta_json` 字段中已存在的非 UTF-8 数据
- **同模式**：与 Phase 1 修过的 `slang_db_repair._sqlite_recover` UTF-8 bug 同族，触发点不同——这次在 [services/slang/extractor.py](../services/slang/extractor.py) 的反序列化路径
- **行动**：独立 task 处理；先 grep 所有 `meta_json` 反序列化点（D1 同模式扫描），统一改成 `json.loads(bytes_field.decode("utf-8", errors="replace"))` 或在写入侧强校验
- **优先级**：中（不阻塞主流程，但每次写候选都会噪音报警）

### 3.2 `services/llm/usage.py` 未接 close_with_checkpoint

- **来源**：[docs/slang-db-corruption-fullfix-tracker.md](slang-db-corruption-fullfix-tracker.md) § 决策记录 2026-05-20
- **现状**：`usage.db` 用裸 aiosqlite，没有 WAL setup，`close_with_checkpoint` 是 no-op；当前不构成损坏风险
- **行动**：等 store 体系下一次重构时统一接入；不单独开 task

### 3.3 ConversationArchive 后续 phase 暂缓

- **来源**：[docs/conversation-archive-implementation-tracker.md](conversation-archive-implementation-tracker.md)
- **状态**：Phase 0-4 dry-run 原语已实现；Phase 5（segment + 真实清理）/ Phase 6（群词频统计）/ Phase 7（私聊备忘录归档）暂缓
- **触发条件**：Phase 4 dry-run 长期稳定（≥ 1 个月无 `legacy_fallback` scan run / 无 `needs_rescan` 卡死）后再讨论

### 3.4 reply-workflow Phase 3+ 未启动

- **来源**：[docs/reply-workflow-implementation-tracker.md](reply-workflow-implementation-tracker.md)
- **状态**：Phase 1（shadow log）+ Phase 2（私聊 actor / wait / complete）已验收
- **未做**：
  - Phase 3 `ProactiveIntentStore` + 主动后续仅允许明确来源
  - Phase 4 群聊 reply gate boost + gate latency 决策日志
  - Phase 5 候选生成器 + ranker（暂缓）
  - Phase 6 tiny LLM gate 灰区试验（暂缓）
- **触发条件**：Phase 2 真实流量验收 ≥ 2 周无 wait/complete 异常

### 3.5 group-concurrency Phase 4 持久化高优先级事件

- **来源**：[docs/group-concurrency-implementation-tracker.md](group-concurrency-implementation-tracker.md)
- **状态**：Phase 0-3 已实现 + 真人验收通过，正在做收口防回退
- **未做**：Phase 4 — 高优先级事件（@bot / direct_followup / video_always）持久化到 SQLite，避免 bot 重启时丢失队列
- **触发条件**：视 Phase 1-2 运行效果决定；当前未观察到丢事件

---

## 4. 前端 / 文档欠债

### 4.1 web-refactor 阶段 0/1/3 验收清单未勾完

- **来源**：[docs/web-refactor-plan.md](web-refactor-plan.md) 14 处 `- [ ]`
- **关键剩余**：
  - 阶段 0：`git status` 不再因构建产物刷屏；`admin/templates/` 为空；`deploy.sh` 单机跑通
  - 阶段 1：`grep -c '!important' admin/frontend/src/styles/global.css` ≤ 10；浅深主题切换无闪白
  - 阶段 3：每个视图 PR 模板的 7 项验收（内联 style ≤ 5、无 `!important`、无重复容器样式、浅深切换不闪、3 种屏幕宽度可用、人工点过、无控制台 warning）
- **状态**：随每个视图重构 PR 单独勾；没有截止时间，但下次开新视图重构时必须先勾完上一轮

### 4.2 docs/project-info.md 与 maintenance-log 一致性

- **现状**：本次（Phase 3 B 段）已同步「存储路径」段，新增 named volume 说明
- **遗留**：[docs/architecture.md](architecture.md) / [docs/operations.md](operations.md) 均提到过 `./storage` bind mount，需要在下一次涉及部署 / 备份的修改时一并校对
- **行动**：不开独立 task；下次修部署文档时顺手更新

### 4.3 fullfix tracker 与 multilayer 报告状态字段同步

- **现状**：tracker 对 Phase 1 / Phase 2 / Phase 3 A 段都标了 ✅，但 Phase 3 B 段刚部署完，tracker 表格仍是「未启动」
- **行动**：Phase 3 24h 观察通过后，把 tracker § Phase 3 表格的 ⬜ 改成 ✅ + 写最终条目；同时把 multilayer 报告的 § 5 / § 8.3 状态字段刷一遍

---

## 5. 长尾守护

| 项 | 来源 | 触发节奏 | 做什么 |
| --- | --- | --- | --- |
| 主机快照清理 | Phase 3 B 段 | 2026-06-20 后 | `rm -rf storage.bind-mount-snapshot-20260521-161720` |
| BackupScheduler quick_check 误报排查 | Phase 2 | 任意红条出现时 | 先看 `services/storage/backup_scheduler.py` quick_check tick 日志，确认是否真损坏；不要立刻删 wal/shm |
| 30 天 corruption 验收完成后收尾 | Phase 3 真验收 | 2026-06-20 | fullfix tracker 写最终条目；本文 § 1.2 删除 |
| Phase D 启动前置 | multilayer 方案 | Phase C 跑过 ≥ 1 周 | grep `MemoryConsolidator.run_once` 调用频次 + admin queue 决策记录 ≥ 10 条 |
| `meta_json` UTF-8 修复 | § 3.1 | 任意维护窗口 | D1 同模式扫描所有 `meta_json` 反序列化点 → 统一加 `errors='replace'` |

---

## 6. 不做项（明确决策，防止回头讨论）

| 项 | 决策来源 | 不做原因 |
| --- | --- | --- |
| 把全部 store 都切到 `journal_mode=DELETE` | Phase 2 决策 2026-05-20 | slang 是已知反复损坏者，定向治理；其他 store 由 Phase 1 + Phase 3 兜底，不必牺牲写性能 |
| Phase 13 P1-1（黑话反向重申） | slang module tracker 2026-05-16+ | 数据决断后搁置；当前数据不足以证明反向重申的收益 > 假阳性成本 |
| `napcat` 服务 storage 切 named volume | 铁律 D6 + Phase 3 spec | napcat 设备指纹反风控，绝不动；napcat data volume 仍是 bind mount |
| `./config` / `./admin/static` 切 named volume | Phase 3 spec | config 需热重载、admin/static 是 npm build 直出（铁律 D6） |
| Phase 5 候选生成器 ranker / Phase 6 tiny LLM gate | reply-workflow tracker | 当前 Phase 2 已能覆盖核心决策，不引入新的 LLM 调用增加延迟 |

---

## 7. 引用

- [maintenance-log.md](../maintenance-log.md) — 历次部署与变更
- [docs/slang-db-corruption-fullfix-tracker.md](slang-db-corruption-fullfix-tracker.md) — slang.db 全栈治本
- [docs/audits/multilayer-memory-learning-report-2026-05-17.md](audits/multilayer-memory-learning-report-2026-05-17.md) — 多层学习记忆方案（P3）
- [docs/conversation-archive-implementation-tracker.md](conversation-archive-implementation-tracker.md)
- [docs/reply-workflow-implementation-tracker.md](reply-workflow-implementation-tracker.md)
- [docs/group-concurrency-implementation-tracker.md](group-concurrency-implementation-tracker.md)
- [docs/style-learning-implementation-tracker.md](style-learning-implementation-tracker.md)
- [docs/slang-module-implementation-tracker.md](slang-module-implementation-tracker.md)
- [docs/web-refactor-plan.md](web-refactor-plan.md)
- [docs/agent-discipline.md](agent-discipline.md) — D1-D7 纪律
