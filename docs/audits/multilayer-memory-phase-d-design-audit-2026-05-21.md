# Phase D Episodic Reflection — 设计前置审计（2026-05-21）

> 配套：[multilayer-memory-learning-report-2026-05-17.md](multilayer-memory-learning-report-2026-05-17.md) § 5 Phase D / [pending-and-observation.md](../pending-and-observation.md) § 2
> 目的：在动 Phase D 实现之前盘清「现状已就绪到哪、报告原文要什么、还差哪些」，避免仓促开工撞设计死角
> 状态：审计稿，**未落地任何代码**；下一步据本审计敲定 D.1 ~ D.5 子任务

---

## 0. 一句话结论

EpisodeStore 5 态机和两支 LLM task（`reflection_consolidator` / `episode_summarizer`）都已成熟在位，Phase D 的真正工作量是**「从 Phase C 的 candidate 表到 EpisodeStore 的 promote 桥」+「反思素材源接入（style_feedback / 用户纠正）」+「召回路径接进 ContextProvider」三件事**——既不需要从零造存储，也不依赖任何观察期前置（前置在 A2/A3/Phase C 已全部满足）。

---

## 1. 现状盘点（已就绪 vs 未就绪）

### 1.1 已就绪（无需重做）

| 组件 | 文件 | 关键字段 / 行为 | 状态 |
| --- | --- | --- | --- |
| EpisodeStore 5 态机 | [services/episodic/store.py](../../services/episodic/store.py) | `dry_run` → `candidate` → `approved` → `enabled_for_prompt` ↔ `disabled`；`VALID_TRANSITIONS` + `PER_GROUP_MAX_ACTIVE=50` + `CANDIDATE_CONFIDENCE_THRESHOLD=0.6` | ✅ A3 落地 |
| Phase B gate | [services/episodic/store.py:146-159](../../services/episodic/store.py#L146-L159) `_phase_b_unlocked()` | 检查 `BlockTraceBus.{record,list_for_request,find_by_source_ref}` 三方法存在 | ✅ Phase B 已上线，gate 通过 |
| Episode CRUD + revision | 同上 `create_episode` / `transition_state` / `record_revision` / `list_revisions` | 写入 + 状态迁移 + 历史可审计 | ✅ |
| EpisodePayload 类型契约 | [services/memory_consolidator/types.py:108-114](../../services/memory_consolidator/types.py#L108-L114) | `situation / observed_context / action_taken / outcome_signal / reflection` 与 EpisodeStore 字段 1:1 | ✅ Phase C 落地 |
| `consolidator_candidates` 表（domain="episode"） | [services/memory_consolidator/store.py](../../services/memory_consolidator/store.py) | Phase C 已能产 episode-domain candidate，含 `payload` JSON / `confidence` / `source_message_pks` / `normalizer_cluster_id` | ✅ Phase C dry-run 跑通 |
| `decide_candidate` admin 接线 | [admin/routes/api/memory_consolidator.py:258-299](../../admin/routes/api/memory_consolidator.py#L258-L299) | POST `/candidates/{id}/decide` 改 candidate.state（dry_run → queued / approved / rejected） | ✅ |
| Phase D LLM task 注册 | [services/llm/llm_request.py:56-57,284-285](../../services/llm/llm_request.py#L56-L57) | `reflection_consolidator` + `episode_summarizer` 进 spine，缓存 profile = `system_breakpoints=1` | ✅ |
| `episode_summarizer` 调用点 | [services/memory_consolidator/consolidator.py:399-435](../../services/memory_consolidator/consolidator.py#L399-L435) | Phase C 每 batch 一次，**output 不落库**（注释明示 "promotion is out of scope for the dry-run"） | ✅ caller-presence guarantee 在位 |
| StyleStore feedback 信号源 | [services/style/store.py:91-133,857](../../services/style/store.py#L91) `style_feedback` 表 + admin POST `/style/expressions/{id}/feedback` | rating ∈ {positive, negative, neutral}，自带 `target_type / target_id / group_id / created_at` 索引 | ✅ 可作为反思触发素材源 |
| 隐私字段全层迁移 | A2 落地（cross_group_visibility, scope, evidence 保护） | episode 写入双写 normalizer + 隐私字段 | ✅ |

### 1.2 未就绪（Phase D 真正要补的）

| 缺口 | 表现 | 报告对应章节 |
| --- | --- | --- |
| **G1 — promote 桥** | `decide_candidate(state="approved")` 只改 candidate.state，**不调** `EpisodeStore.create_episode`；admin approve 一条 episode-domain candidate 后，`storage/episodic.db` 行数仍为 0 | § 5 Phase A3.3 「人工 approve 后转 approved，但默认**不进 prompt**」+ § 5 Phase D 「补上真人式长期学习最缺的一层」 |
| **G2 — `reflection_consolidator` 无 caller** | LLM task 已注册，但 grep 全仓**无任何调用方**；Phase C 只调 `episode_summarizer`，反思生成完全没接通 | § 5 Phase D 「从管理员反馈和用户纠正中生成『下次怎么做』的反思」 |
| **G3 — 反思素材源未接入** | `style_feedback`（rating=negative）+ slang 复核拒绝 + 表达风格 reject 这三种"被纠正"信号天然适合触发反思，目前都不进 reflection 流水 | § 5 Phase D 「从管理员反馈和用户纠正中生成…反思」+ § 7.3 决议 BlockTraceBus 已能 trace 进 prompt 的来源 |
| **G4 — 召回路径未接 ContextProvider** | EpisodeStore 有 `list_episodes(state_filter="enabled_for_prompt")`，但 ContextProvider 在 [services/cards/store.py](../../services/cards/store.py) 等下游不会查 EpisodeStore；即便 admin 把 episode 推进到 `enabled_for_prompt`，也不会进 prompt | § 5 Phase D 验收「bot 被纠正一次后，后续同类场景能召回反思」 |
| **G5 — 跨层 graph edge 未写** | A.5 落了 graph schema，`episode_supports_profile` edge 类型也已声明（§ 4.2），但 EpisodeStore 不写 graph；这是 Phase E 跟 Phase D 同步要做的「双写」 | § 5 Phase E + § 7.4 决议「episode 层时双写 normalizer + graph edge」 |

---

## 2. 报告原文 § Phase D 目标对照

> 引用 [multilayer-memory-learning-report-2026-05-17.md:443-461](multilayer-memory-learning-report-2026-05-17.md#L443-L461)：

### 报告硬要求

- **新建 `services/episodic/`** — ✅ 已存在（A3 落地）
- **episode schema** — ✅ 完全匹配（situation / observed_context / action_taken / outcome_signal / reflection / linked_memory_ids / confidence / decay / last_used_at）
- **从管理员反馈和用户纠正中生成"下次怎么做"的反思** — 🔴 G2 + G3 缺
- **验收 1**：被纠正一次后，同类场景能召回反思 — 🔴 G4 缺
- **验收 2**：episode 不直接改人格，只作为动态经验提示 — ✅ 状态机 default 不进 prompt，符合

### 报告硬前置（来自 § 5 Phase D + § 7.4）

| 前置 | 状态 | 证据 |
| --- | --- | --- |
| A2 隐私字段全层迁移 | ✅ | a2 已合入，`cross_group_visibility` / `scope` 列在 EpisodeStore 表中 |
| A3 episode 状态机 + admin queue | ✅ | EpisodeStore 5 态 + admin episodes router |
| Phase C 能产 episode-domain candidate | ✅ | Phase C 落地 2026-05-21，`CANDIDATE_DOMAINS` 包含 `episode` |
| BlockTraceBus 落地（Phase B）— 仅 `enabled_for_prompt` 推进时检查 | ✅ | `_phase_b_unlocked()` 当前 `True` |
| **观察期** | **❌ 报告未要求** | § 5 Phase D / § 7.4 / § 7.7 全文无任何观察期表述；Phase F 才有 90+ 天硬要求 |

> **结论**：Phase D **可立即起**。先前 [pending-and-observation.md](../pending-and-observation.md) § 2 把 30 天 corruption 窗口和 Phase D 启动条件混淆，已于本次审计前修正——「等 Phase C 1-3 天有真实样本」是工程直觉，**不是报告 gate**。

---

## 3. 推荐子阶段拆分（D.1 ~ D.5）

每个子阶段独立可验、可回滚，按风险递增排列。

### D.1 — promote 桥：`decide_candidate(approved)` → `EpisodeStore.create_episode`

**改动文件**：

- [services/memory_consolidator/store.py](../../services/memory_consolidator/store.py) — `decide_candidate` 不动；新增 `MemoryConsolidator.promote_episode_candidate(candidate_id) -> Episode | None`，仅在 candidate.domain == "episode" 且 state == "approved" 时调 EpisodeStore
- [admin/routes/api/memory_consolidator.py](../../admin/routes/api/memory_consolidator.py) — `decide` 路由在 `state == "approved"` 且 `domain == "episode"` 时附加调一次 promote
- [services/episodic/store.py](../../services/episodic/store.py) — 不动；`create_episode` 已支持 `meta=` 透传，promote 时把 `consolidator_candidate_id / run_id / source_message_pks / normalizer_cluster_id` 全塞进 meta_json 留审计

**关键约束**：

- promote 后的 episode 默认状态 = `dry_run`（EpisodeStore.create_episode 默认行为），**不**自动晋升到 candidate；保留 admin queue 二次 approve 入口
- 失败不阻塞 candidate.state 回写——`decide_candidate` 的事务先于 promote，promote 失败只 warn 不抛
- D2 cancel-path 测试：`pytest.raises(asyncio.CancelledError)` 模拟 promote 中段被取消，断言 candidate.state 已回写但 EpisodeStore 行数 = 0（一致性优先）

**验收**：

- 单测 `tests/test_memory_consolidator_promote.py`：构造 candidate(domain="episode", state="dry_run") → admin POST decide(state="approved") → grep `storage/episodic.db` 表 `episodes` 出现新行
- 手动：admin 页面跑一次 Phase C → approve 一条 episode candidate → admin episodes 列表能看到该 episode（state=`dry_run`）
- 不变量：candidate(domain="slang"/"style"/"fact"/"graph_relation") 走 `decide_candidate` 路径**不写** EpisodeStore；只 episode-domain 触发 promote

### D.2 — admin queue UX：episode 候选筛选 + reflection 字段编辑

**改动文件**：

- [admin/frontend/src/views/memory/](../../admin/frontend/src/views/memory/) — 候选列表加 `domain` 筛选 chip（5 域）；episode 行展开显示 5 字段 payload；预留 `reflection` 字段 inline 编辑（admin 在 approve 之前可补改 LLM 漏写的反思）
- [admin/routes/api/memory_consolidator.py](../../admin/routes/api/memory_consolidator.py) — 新增 PATCH `/candidates/{id}/payload` 用于 admin 编辑 payload（仅在 state="dry_run" / "queued" 允许）

**关键约束**：

- 编辑后的 payload 走 `normalize_payload(domain="episode", payload)` 投影一遍，避免 admin 写入未知字段污染
- 修改 payload 必须 record_revision（admin actor）
- D6 admin SPA：只 `npm run build`；不 rebuild bot

**验收**：

- vue-tsc + npm run build 通过
- 手动：admin 在某条 episode candidate 上改 reflection 字段 → approve → EpisodeStore 中该 episode 的 reflection 与编辑后值一致

### D.3 — 反思生成路径：`style_feedback` (negative) → `reflection_consolidator` LLM → episode candidate

**改动文件**：

- [services/memory_consolidator/consolidator.py](../../services/memory_consolidator/consolidator.py) — 新增 `_maybe_reflect_on_feedback(group_id, since_ts)`：扫近期 `style_feedback.rating='negative'` + slang 复核 reject + 表达风格 reject 三类信号，每条信号触发一次 `reflection_consolidator` LLM，输出按 EpisodePayload schema 写入 `consolidator_candidates(domain='episode')`
- [services/llm/llm_request.py](../../services/llm/llm_request.py) — 不动（task 已注册）
- 新建 [services/memory_consolidator/feedback_sources.py](../../services/memory_consolidator/feedback_sources.py)：抽象「负反馈 signal」接口，先实装 StyleFeedback / SlangReviewReject / StyleExpressionReject 三个 source，每个返回 `Iterable[NegativeSignal]`，方便日后接 user_correction（"bot 你说错了"）

**关键约束**：

- 反思候选默认 `confidence = 0.5`（中等），不自动晋升 candidate（需 D.1 admin approve）
- 同 source_ref 在 24h 窗口内只触发一次反思（去重 by `(domain, source_table, source_id)`）
- `reflection_consolidator` LLM prompt 必须强制 JSON schema 输出，匹配 EpisodePayload 5 字段；非法 JSON 按 § Phase A 的 `reflection_*` 错误归类，不阻塞主流程
- Phase C 的 `_maybe_summarize_episodes` 不动；`reflection_consolidator` 是独立 caller，便于日后退出 D 时单点 disable

**验收**：

- 构造一条 `style_feedback(rating='negative', target_type='expression')` → 调 `_maybe_reflect_on_feedback` → grep `consolidator_candidates` 应见新 row（domain='episode', state='dry_run', payload 含 5 字段）
- 单测断言：同 source_ref 二次调用不重复产 candidate
- D2 cancel-path：LLM 调用 timeout → reflection 步骤跳过，candidate 表无脏数据

### D.4 — 召回路径：ContextProvider 查 EpisodeStore（state=`enabled_for_prompt`）

**改动文件**：

- [services/cards/store.py](../../services/cards/store.py) 或新建 [services/context/episode_provider.py](../../services/context/episode_provider.py) — 实现 `EpisodeContextProvider.list_relevant(group_id, situation_query)`，按 `enabled_for_prompt` + group_id 过滤，返回 ≤ N 条
- [plugins/chat/plugin.py](../../plugins/chat/plugin.py) 或更上游的 prompt builder — 在系统块加入「相关历史反思」section（仅当列表非空），每条以 "曾经在 {situation} 时 {action_taken}，结果 {outcome_signal}，下次：{reflection}" 渲染
- BlockTraceBus 双写：每次召回的 episode 都 `record(source_ref=episode_id, source_table='episodes')`，让 admin 能 trace 「这条回复用了哪条反思」

**关键约束**：

- 默认 `top_k=3`，token 预算优先级低于 slang / style（与 § Phase B PromptBudgetManager 排序一致）
- `last_used_at` 在召回时更新，作为后续 decay 输入
- **不引入新 LLM 调用** — 召回只是 SQL 检索 + 字符串拼接；情境匹配先用 normalizer cluster_id 做硬匹配，复杂语义检索留 D.6 备选

**验收**：

- 报告 § Phase D 验收：构造一条 enabled_for_prompt episode（situation="用户问技术问题时，bot 用了过于俏皮的语气"，reflection="技术场景下保持简洁，不堆 emoji") → 群里发同类问题 → bot 回复前的 prompt block 应包含该 reflection（看 BlockTraceBus 日志）
- 不变量：`enabled_for_prompt` 之外的状态**绝不**进 prompt

### D.5 — graph edge 双写：episode → `episode_supports_profile`

**改动文件**：

- [services/episodic/store.py](../../services/episodic/store.py) — `transition_state(new_state="approved")` 时调 GraphWriter 写一条 `(subject=episode_id, predicate='episode_supports_profile', object={user_id|group_id}, edge_type='episode_supports_profile')`
- [services/graph/writer.py](../../services/graph/writer.py)（A.5 落地） — 不动；只是新增 caller

**关键约束**：

- 仅在 `approved` 触发；`disabled` 撤销 edge（`edge_state='disabled'`）
- 写 graph 失败不回滚 state 迁移（log warn，graph 是辅助索引，不是 source of truth）
- 与 § Phase E 的 5 类 edge 中 `episode_supports_profile` 一一对应；其余 4 类（term_used_in_group / style_applies_to_situation / user_corrected_bot_about / doc_supports_fact）属于 Phase E 范围，**不**在 D.5 内做

**验收**：

- 构造 episode → approve → grep `knowledge_graph.db` 表 `graph_edges` 应见新 row
- admin 知识库图谱页能看到该 edge

---

## 4. 风险摘要

| 风险 | 缓解 |
| --- | --- |
| LLM `reflection_consolidator` 输出格式漂移（非 JSON） | strict JSON schema + retry 1 次 + 失败归类 `reflection_invalid` 不阻塞主流程；admin 页面单独筛 invalid 候选 |
| episode 召回过多导致 prompt 膨胀 | top_k=3 + token 预算硬上限 + Phase B BlockTraceBus 监控；超阈值时降级为只放 reflection 字段 |
| admin 反馈/纠正信号误归因（user 误点 negative） | reflection candidate 默认 `confidence=0.5` 不自动晋升；admin queue 必须人工 approve 才进 EpisodeStore，再次 approve 才进 prompt（双闸） |
| `episode_supports_profile` edge 写入路径出 bug 污染图谱 | edge_state='dry_run' 默认值，graph 检索时过滤；写失败不阻塞 episode state；可通过 GraphWriter 测试覆盖 |
| 跨层耦合（episode ↔ style_feedback ↔ knowledge_graph） | feedback_sources.py 抽象层 + GraphWriter 单 caller 双写；任何一环挂掉单点回滚 |
| Phase C dry-run 样本不足导致 D.1 promote 桥首日空跑 | D.1 不依赖样本数；D.3 反思路径上线后第一次反馈即可触发 |

---

## 5. 不做项（Phase D 范围之外，明确登记）

| 项 | 决策原因 |
| --- | --- |
| 自动晋升 episode 到 `enabled_for_prompt` | 报告硬要求 admin 手动推进；自动化只在 dry_run → candidate（A3 已落 `auto_promote_dry_runs`） |
| 引入新 LLM 调用做语义召回 | D.4 先用 normalizer cluster_id 硬匹配；语义检索若需要走 § Phase E graph traversal，不在 D 范围 |
| Phase E 其余 4 类 edge | term_used_in_group / style_applies_to_situation / user_corrected_bot_about / doc_supports_fact 全部留给 Phase E |
| Phase F declarative_facts 凝练 | 报告硬前置「Phase D 跑过 ≥ 3 个月真实数据 + 1 群累计 ≥ 200 enabled_for_prompt episode」全部不满足；本审计周期内不开 |
| 直接接 user_correction（"你说错了"自动触发反思） | 留给 D.3 后续扩展 — 现版仅接 admin/系统侧 reject 信号；用户语义识别需要 thinker 改造，超出 D 范围 |

---

## 6. 验收前置自检（Phase D 整体完成时勾）

> 2026-05-21 整体落地后回填。落地 commits：D.1 bf53119 / D.2 428907f / D.3 128edf6 / D.4 17b4769 / D.5 9f7c6e2。

- [x] 报告 § 5 Phase D 验收 1：bot 被纠正一次后，后续同类场景能召回反思（D.3 + D.4 完成；EpisodeProvider 注册在 plugin.py:934，ReflectionGenerator 注册在 plugin.py:966）
- [x] 报告 § 5 Phase D 验收 2：episode 不直接改人格，只作为动态经验提示（D.4 EpisodeProvider 走 ContextProvider 通道注入，default state 不进 prompt，与状态机 `enabled_for_prompt` gate 一致）
- [x] § 7.4 决议：episode 写入双写 normalizer + graph edge（D.1 normalizer 已在 Phase C；D.5 graph edge 通过 EpisodeGraphBridge 在 transition_state(approved/disabled) 时写 `episode_supports_profile`）
- [x] D1 同模式扫描：grep `_maybe_reflect_on_feedback` / `promote_episode_candidate` / `episode_supports_profile` 三处接入点，确认无遗漏 caller。实测三处对应三个 wire point：EpisodePromoter @ plugin.py:785、ReflectionGenerator @ plugin.py:966、EpisodeGraphBridge @ plugin.py:799–800；EpisodeProvider @ plugin.py:934 是召回端
- [x] D2 cancel-path 回归测试：promote 桥 + reflection 生成 + graph 双写 + recall 四处均覆盖：
  - D.1 [tests/test_memory_consolidator_promote.py:260](../../tests/test_memory_consolidator_promote.py#L260) `test_promote_cancel_path_leaves_episodes_empty`
  - D.3 [tests/test_memory_consolidator_reflector.py:228](../../tests/test_memory_consolidator_reflector.py#L228) `test_run_once_cancel_marks_run_failed`
  - D.4 [tests/test_episode_context_provider.py:275](../../tests/test_episode_context_provider.py#L275) `test_provide_cancel_path_leaves_clean_state`
  - D.5 [tests/test_episode_graph_bridge.py:217](../../tests/test_episode_graph_bridge.py#L217) `test_cancel_path_leaves_clean_state`
- [x] D4 完成声明含证据：Phase D 测试 sweep 94 passed（test_episode + test_episode_context_provider + test_episode_graph_bridge + test_memory_consolidator_reflector + test_memory_consolidator_promote + test_admin_memory_consolidator）；ruff 全绿；pyright on Phase D scope 0 errors。回滚路径：每个子阶段对应 commit 单独 revert，互相独立，graph edge 因 source-of-truth 在 EpisodeStore，graph 表干净 truncate 即可重新生成
- [x] 多层报告 § 5 状态字段同步：Phase D 从 🔴 改为 ✅（落地日期 2026-05-21，commits 见上）
- [x] [pending-and-observation.md](../pending-and-observation.md) § 2 表格刷新（同次 commit 落地）

---

## 7. 启动建议

1. **D.1 单测优先**：先用 in-memory candidate fixture 跑 promote 桥单测，跑通再接 admin POST
2. **D.2 + D.3 并行可拆**：UX 工作不依赖反思生成路径；可以让前端先把 5 域筛选 + reflection 编辑做掉，后端 reflection 调用后接
3. **D.4 召回必须等 D.1 + D.3 都有数据**：否则验收 1 没法验
4. **D.5 graph edge 最后做**：依赖 D.1 已有 EpisodeStore approved 行，且 GraphWriter A.5 已稳定 ≥ 1 周

时间预估（参考 § 5 Phase D 整体规模 ≈ 1-2 周）：

- D.1 promote 桥 ~ 4h（含单测）
- D.2 admin UX ~ 6h（前端单点工作）
- D.3 反思生成 ~ 1 天（含 LLM prompt 调试 + 去重逻辑）
- D.4 召回路径 ~ 1-2 天（含 BlockTraceBus 接线 + prompt builder 改动）
- D.5 graph 双写 ~ 4h

> 起 D.1 之前先 grep `style_feedback` 在过去 7 天的实际 negative 行数（`docker exec qq-bot sqlite3 storage/style.db "SELECT COUNT(*) FROM style_feedback WHERE rating='negative' AND created_at > datetime('now','-7 days')"`），确认有素材再做 D.3 反思路径，否则 D.3 上线后没数据可反思。

---

## 8. 引用

- [docs/audits/multilayer-memory-learning-report-2026-05-17.md](multilayer-memory-learning-report-2026-05-17.md) — § 4.1 / § 5 Phase D / § 7.4
- [docs/pending-and-observation.md](../pending-and-observation.md) § 2 — Phase D 启动条件（无观察期）
- [services/episodic/store.py](../../services/episodic/store.py) — EpisodeStore 5 态机
- [services/memory_consolidator/types.py](../../services/memory_consolidator/types.py) — EpisodePayload schema
- [services/memory_consolidator/consolidator.py](../../services/memory_consolidator/consolidator.py) — Phase C dry-run + episode_summarizer caller-presence
- [services/memory_consolidator/store.py](../../services/memory_consolidator/store.py) — `consolidator_candidates` 表 + `decide_candidate`
- [services/style/store.py](../../services/style/store.py) — `style_feedback` 表（反思素材源）
- [services/llm/llm_request.py](../../services/llm/llm_request.py) — `reflection_consolidator` / `episode_summarizer` 注册
- [admin/routes/api/memory_consolidator.py](../../admin/routes/api/memory_consolidator.py) — admin queue 接口
- [admin/routes/api/episodes.py](../../admin/routes/api/episodes.py) — admin episodes router
- [docs/agent-discipline.md](../agent-discipline.md) — D1 同模式扫描 / D2 cancel-path / D4 证据声明 / D6 admin SPA / D7 git hygiene
