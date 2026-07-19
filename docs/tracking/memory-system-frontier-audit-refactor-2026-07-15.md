# 记忆系统前沿对比审计与加强重构

> 状态：短期 A/B/C + Entity/Episode typed-refs + Hot-path Write/Temporal Trace v1 + evidence-use eval gate v1 + graph window/hub v1 + Episode v2 decay/rerank + **KG/Learning Autopilot** + **Query-Aware Retrieval Planner v1** + **Graph Provenance Gate v1** + **Graph Population Observability v1（gpo_v1）** + **Joint Dual-Path Telemetry v1（jdt_v1）** + **LongMemEval Raw-Turn Replay v1** 完成离线验收；全记忆 A/B/C/D 证据矩阵与 **staged rollout / rollback v1** 已落地；算法候选继续 NO-GO/defer；**未部署** · 2026-07-17
> 前序完成：`docs/tracking/qzone-journal-implementation-2026-07-15.md`

## 目标

基于 Omubot 当前真实运行接线、前沿长期记忆论文和同类开源项目，审计长期记忆的采集、抽取、检索、时间更新、冲突失效、整合与评测链路；随后按风险从低到高实施加强性重构，使 Bot 的长期记忆更准确、可解释、可评测，而不是直接替换现有生产检索。

## 当前已知架构

```text
QQ 消息
→ GroupTimeline + ShortTermMemory + MessageLog 兼容面
→ ConversationArchive（composition-root 所有者，同一 storage/messages.db）
→ MemoExtractor / CardStore
→ RetrievalGate（权威检索决策 + 结构化 score）
→ ContextService RRF(memory/doc/graph) + 统一 Provenance/ScoreBreakdown
→ ProviderBus(slang/style/episode/climate/...)
→ LLM

后台：Dream / MemoryConsolidator / EpisodeStore / KnowledgeGraph /
SocialNarrative / ConversationArchive / Backup Catalog
```

## 已完成实现摘要（A/B/C）

### A. 审计与基线 / composition

- **ConversationArchive composition-root owner**：`bootstrap/chat_runtime.py` 以 `ConversationArchive(db_path="storage/messages.db")` 挂 `ctx.msg_log`（**无** `cast(Any, ...)`）；`MessageLogPort` 为结构端口，`MessageLog` 保留为兼容客户端。迁移清单：`docs/migrations/conversation-archive-composition-root-2026-07-15.md`。
- **Anti-join legacy backfill**：`backfill_legacy_messages` 按 `legacy_row_id` 幂等镜像（`INSERT OR IGNORE` / unique partial index），不整库复制、不删 legacy 行。
- **shared RetrievalGate**：runtime context 与 memory path 共用 gate；semantic 健康门控 + embedding 失败 ngram fallback；修复重复 memory 注入。
- **统一 ContextProvenance / ContextScoreBreakdown**：memory / knowledge / graph 命中均暴露 owner、source_id、evidence_refs、supersedes、score 分量。
- **本地 LongMemEval/LoCoMo 风格 fixture**：`tests/fixtures/context_eval/long_memory_frontier.json` — 跨会话事实、更新/冲突、时间更新、跨群隔离、无证据零命中 abstention。

### B. 短期加强

- **Card 排序合同**：relevance **45%**、importance **25%**、recency **15%**（半衰减 **30 天**）、confidence **15%**（`services/memory/retrieval.py`）。
- **semantic health / fallback**：embedding backend 在首查前可健康降级；metrics 可观测。
- **评测 runner**：`services/context/eval.py` + `tests/test_context_eval.py` 覆盖 required/forbidden/duplicate/budget/no-evidence。

### C. 中期已落地子集

- **Consolidator promote 闭环**：审核 `approved` 候选 → Episode `dry_run → candidate → approved`；`enabled_for_prompt` 仍为独立门；Admin `POST /api/admin/episodes/{id}/enable`。
- **EpisodePromoter 持久幂等**：`EpisodeStore.find_by_source_meta(source, meta_key, meta_value)` 用 SQLite `json_valid`/`json_extract` 做库级 provenance 查找（meta_key 严格标识符校验）；promoter 不再 `list_episodes(limit=200)` Python 扫描。
- **Truthful retrieval counts**：`MemoryRetrievalResult.total_active` = scope+global 全部可见 active；`matched_active` = 本路径 pre-top_k 候选数。Context metadata：`scope_card_count` / `matched_card_count`（`card_count` 仅兼容别名 → scope total）。
- **Temporal graph supersede**：`GraphContextSource` 暴露 supersedes provenance，旧事实不注入。
- **Bounded multi-hop graph**：`max_hops=2` 上限；≥2 个 direct seed 才扩展；subject/object entity 边；direct 优先；结果 `top_k` 硬封顶。负向回归：单 seed 不扩展；hub 高连通仍 bounded，断开分支不进包。
- **Entity Identity v1**：新增不可变 `EntityRef`；Card owner provenance 与 KG subject/object 使用稳定 key；概念 key 带 scope/namespace；新 active fact additive 写 metadata；GraphContext canonical-first + legacy fallback；supersede 区分昵称改名、显式换人和 pre-v1 旧事实。迁移清单：`docs/migrations/memory-entity-identity-v1-2026-07-16.md`。
- **Hot-path Write Policy v1**：MemoExtractor 受约束 `add/reinforce/supersede/skip`，observations 与原子 supersede；迁移清单：`docs/migrations/memory-hotpath-write-policy-v1-2026-07-16.md`。
- **Temporal Trace / False-Premise Awareness v1**：普通 active-only 不变；显式历史/错误前提时注入 current-first sidecar，pool-aware、typed refs、有界 budget；迁移清单：`docs/migrations/memory-temporal-trace-premise-awareness-v1-2026-07-16.md`。
- **Evidence-use offline eval gate v1**：`ContextEvalCase` additive `current_message` / `rewritten_query` / structured `TemporalTraceExpectation`；严格 fixture schema；对 TemporalTrace 结构化字段计分（非 prose 解析）；`reason`/required 隐含 presence；非 `None` malformed DTO 与 split-KP 拼接 fail-closed；缺 assembler fail-closed；fixture `tests/fixtures/context_eval/long_memory_evidence_use_v1.json`（synthetic 能力维，非官方 LongMemEval/LoCoMo 数据）；迁移清单：`docs/migrations/memory-longmem-evidence-use-eval-gate-v1-2026-07-16.md`。**不改** ContextService/RRF/RetrievalGate/TemporalTraceAssembler 生产语义。
- **KG autopilot promotion loop v1**：AI approve 经 live service 原子 materialize；专用 promotion 连接 + `BEGIN IMMEDIATE` + candidate CAS；reject/keep 不可覆盖 active；legacy `approved` 严格 repair；单 fact reviewer；迁移清单：`docs/migrations/memory-knowledge-autopilot-promotion-loop-v1-2026-07-16.md`。
- **Learning Autopilot applied-outcome v1**：Style/Episode/Slang 的 batch/state counters 仅记录阈值后实际写入；empty cursor 与 actionable remaining 诚实；共享 LLM verdict 严格类型；迁移清单：`docs/migrations/learning-autopilot-applied-outcomes-v1-2026-07-16.md`。
- **Query-Aware Retrieval Planner v1**：纯 deterministic need classifier 在 `ContextPlugin` 调 `ContextService` 前生成 RRF 后 type caps 与 pack budget profile；保留 Thinker mode / RetrievalGate / RRF weights / graph hops / TemporalTrace / Episode / schema；secret-free metrics allowlist + deep-copy；迁移清单：`docs/migrations/memory-query-aware-retrieval-planner-v1-2026-07-17.md`。

## 审计原则（仍适用）

- 先建立可重复评测和统一合同，再动生产排序权重。
- provenance 必须能回答“这条记忆来自哪条消息/哪个事件/哪个抽取器/何时生效”。
- 冲突更新优先失效旧事实，不物理删除证据。
- semantic backend 不健康时必须显式降级并可观测，不能静默宣称 embedding 可用。
- 不在没有本地基线时同时更换检索 planner、排序函数和存储 schema。

## 实施阶段

### A. 审计与基线 — 已完成

- [x] 复核 bootstrap：ConversationArchive 为 composition-root owner；MessageLog 兼容。
- [x] 画清 RetrievalGate、ContextService、MemoPlugin takeover 边界。
- [x] 复核 semantic backend 初始化、fallback、health 与 metrics。
- [x] 复核 consolidator candidate → review → promote 闭环（至 episode approved；enable 另门）。
- [x] 复核 Card/KG/Episode provenance 与时间 supersede。
- [x] 建立本地长期记忆评测最小数据集与 baseline runner。

### B. 短期加强 — 已完成

- [x] 统一 `ContextProvenance` / `ContextScoreBreakdown`（及 retrieval 侧 score 合同）。
- [x] 单一权威 RetrievalGate；context 路径共享，避免重复注入。
- [x] semantic backend health gate 与降级 metrics。
- [x] LongMemEval/LoCoMo 风格本地小基线：事实回忆、时间更新、冲突、跨群、拒答。
- [x] ConversationArchive 正式接入 composition root（非退役）；legacy 兼容表保留。

### C. 中期加强 — 部分完成

- [x] consolidator 审核后 promote 至 episode approved（enable 独立）。
- [x] EpisodePromoter 库级幂等（非 recent-200）。
- [x] RetrievalResult / Context 计数语义明确（total vs matched）。
- [x] composition-root `cast(Any)` 类型债清除（MessageLogPort）。
- [x] temporal invalidation / supersede（graph + card 路径证据）。
- [x] 受限 2-hop 检索（保守：双 seed、max_hops=2、有界）。
- [x] relevance × importance × recency × confidence 排序与可解释 breakdown。
- [x] Card owner / KG fact / Context provenance 的 Entity Identity v1（additive、无 schema migration）。
- [x] Episode entity back-link + alias registry 生产接线：真实 archive/card/kg resolver、scoped promoter、EpisodeProvider typed evidence、EntityAliasStore lifecycle、affection nickname seed；迁移清单 `docs/migrations/memory-episode-typed-refs-alias-wiring-2026-07-16.md`。
- [x] Hot-path conflict-aware write policy：有界 target、duplicate 防重、observations、原子 supersede。
- [x] Temporal Trace / false-premise awareness：ordinary active-only + 独立 current/earlier/trajectory sidecar。
- [x] evidence-use / premise / temporal offline CI gate：active-only hits + structured current/earlier/trajectory/typed refs（capability dimensions only）；真实栈测试已接入 `typed-boundaries.yml`。
- [x] graph eval fail-closed gate：NaN/Inf/overflow/bool/负计数/结构矛盾/空窗口均不能产生 GO；CI 同时运行 eval + production source/service/store 回归。
- [x] scope-aware graph window + hub control v1：current scope/global 优先、每 scope 有界窗口、batch evidence、raw lexical 优先、hub fanout 与 only-new frontier；迁移清单 `docs/migrations/memory-graph-window-hub-control-v1-2026-07-16.md`。
- [x] Episode v2 decay eligibility + query rerank：严格 `decay_at`、默认 recall 读时过期过滤（`julianday` 绝对时间、offset-safe）、审计 `set_decay_at`、Admin `POST .../decay`、Provider `min(CAP, max(top_k, top_k*3))` 候选池 + 确定性 ngram 重排 + composite 1200 cap；迁移清单 `docs/migrations/memory-episode-decay-query-rerank-v2-2026-07-16.md`。
- [x] Learning autopilot applied-outcome v1（Style/Episode/Slang 对齐 KG 合同；empty-cursor 诚实 remaining；Slang `count_pending`；`llm_assess` LLMTask 类型；迁移 `docs/migrations/learning-autopilot-applied-outcomes-v1-2026-07-16.md`；**离线终审 0 Critical / 0 Important**）。
- [x] Knowledge autopilot promotion loop v1（含 Codex correction + final-review remediation A）：AI approve 经专用连接原子 materialize；无 fallback 第二 owner；candidate reject/keep/promote 全部 CAS；严格共享 verdict；畸形 legacy 恰好 1 LLM；真实 task cancel + 旁路普通写隔离；迁移 `docs/migrations/memory-knowledge-autopilot-promotion-loop-v1-2026-07-16.md`。**Live legacy ~56 仅聚合观测未改**；facts populate 前 PPR/replay 仍 NO-GO。
- [x] Query-Aware Retrieval Planner v1：闭集 query need、post-RRF type caps、content-capacity pack budget、kill-switch、secret-free immutable metrics；**不改** retrieve_mode / RetrievalGate / RRF/Card weights / graph hops / Temporal Trace / Episode / schema；迁移 `docs/migrations/memory-query-aware-retrieval-planner-v1-2026-07-17.md`；离线终审 **0 Critical / 0 Important**。
- [x] Card Category Time Eligibility v1：读时 fail-closed；默认仅 `status=30d` / `event=180d`；shared module + RetrievalGate full/keyword/semantic/count + TemporalTrace active-head；keyword 与 malformed-head budget starvation 已补强；kill-switch `card_eligibility.enabled`；**不**解释 `ttl_turns`、无 schema/backfill；focused **252 passed**；独立 review **0 Critical / 0 Important，ACCEPT**；迁移 `docs/migrations/memory-card-category-time-eligibility-v1-2026-07-17.md`。
- [x] Graph Provenance Gate v1（gpg_v1）：pure `normalize_graph_evidence` + 默认写门禁；add_fact/add_candidate/promote/submit/approve/supersede defense-in-depth；读侧滤 `graph_fact:*`；suite 修正：裸 id→`evidence`、type+id 不合成 alias；post-review 修正：bool/NaN/±Inf ID 写拒绝/读 fail-closed；无 schema/bulk repair；相关 **152**、全仓 **4692**；两轮 Grok review 0C/0I；迁移 `docs/migrations/memory-graph-provenance-gate-v1-2026-07-17.md`；**未部署**。
- [x] Graph Population & Evidence-Quality Observability v1（gpo_v1）：纯分类 + store 只读 `health_snapshot` + Admin additive nested payload + `observability_enabled` kill-switch；禁止 conversion rate / CLI / repair / schema；新 gpo **21**、Codex focused **144**、全仓 **4713**；初审 3 Important（I1/I2 修、I3 性能残留接受）；post-fix **0C/0I ACCEPT**；迁移 `docs/migrations/memory-graph-population-observability-v1-2026-07-17.md`；**未部署**。
- [x] Joint Dual-Path Memory Telemetry v1（jdt_v1）：复用既有 block traces 的一次有界只读联合视图；Context main/TemporalTrace/EUC constrained/Episode 四 role 闭集 decisions/outcomes；不公开内嵌群/用户 session 的 runtime request ID；无热路径写入/schema/ranking 改动；新 jdt **24**、focused **56**、全仓 **4737**；初审 1 Critical privacy 已修；post-fix **0C/0I/0M ACCEPT**；迁移 `docs/migrations/memory-joint-dual-path-telemetry-v1-2026-07-17.md`；**未部署**。
- [x] LongMemEval Raw-Turn Replay v1：严格官方 v1 schema/枚举；每 case 临时 CardStore；真实 MemoryContextSource→RetrievalGate→ContextService→pack；pinned recall/nDCG 公式；外部 ID 报告哈希；duplicate ranking fail-closed；显式不冒充 upstream turn→session 扩窗；外部 cancel/init-cancel 清理；修正后 **42 focused / 235 related / 全仓 4785**；不下载数据、不进常规 CI、不改生产 ranking；迁移 `docs/migrations/memory-longmemeval-raw-turn-replay-v1-2026-07-17.md`。
- [ ] Personalized PageRank 类多跳（未做；当前仅 entity-edge 有界 BFS）。**NO-GO** 直至已部署真实脱敏 population、evidence-quality、latency 与 prompt-path 证据。

### D. 长期候选 — 未做

- [ ] GraphRAG 风格离线 community summary。
- [x] LongMemEval v1 手动离线 raw-turn replay（检索/pack 诊断，不是 full benchmark parity）。
- [ ] LongMemEval/LoCoMo 官方全量数据集 CI：**NO-GO/defer**；大数据、reader/LLM judge、GPU/截图与许可边界不适合常规 CI。
- [ ] MemGPT/Letta 风格自管理记忆工具。
- [ ] EpisodeStore `find_by_source_meta` 的 meta 索引（当前 correctness-first 全表 JSON scan）。

## 已知残留

| 项 | 说明 |
| --- | --- |
| ~~EpisodePromoter existing-match 扫描~~ | **已修**：`find_by_source_meta` 库级查找 |
| ~~`MemoryRetrievalResult.total_active` 歧义~~ | **已修**：scope 总量 + `matched_active` |
| ~~composition-root `cast(Any, ConversationArchive(...))`~~ | **已修**：`MessageLogPort` |
| GraphRAG community summaries | 长期 |
| MemGPT 风格 memory tools | 长期 |
| Card/KG/Context entity identity | **v1 已完成**：canonical key + scoped fallback + supersede 连续性 |
| Episode entity back-link / alias registry | **生产接线已完成**：resolver ports + scoped promoter + provider typed evidence + alias lifecycle + nickname seed；**无 backfill、未部署** |
| Temporal Trace / false premise | **v1 已完成**：pool-aware current-first sidecar；typed/bounded evidence；**未部署** |
| Evidence-use offline eval gate | **v1 已完成**：eval schema + real assembler integration + synthetic fixture；**生产 ranking 未改、未部署** |
| Official benchmark bridge | **手动 raw-turn replay v1 已完成**：官方 v1 schema/检索指标 + 真实 Omubot pack；不评 extraction/answer；full CI defer |
| Graph candidate window / hub leakage | **v1 已完成**：scope-fair window + batch evidence + hub-aware bounded expansion；**未部署** |
| Episode decay / query rerank | **v2 已完成**：读时 eligibility（`julianday` offset-safe）+ strict normalize + Admin decay + 有界 ngram 重排；**未部署** |
| KG autopilot promotion | **v1 + final remediation 已完成（离线）**：注入-only 注册、public `db_path`、严格 verdict、专用事务连接、candidate CAS、D2 cancel；**未部署、未改 live 候选** |
| Learning Autopilot applied outcomes | **v1 已完成（离线）**：Style/Episode/Slang 计数与 remaining/completed 对齐实际写入；**未部署** |
| Query-Aware Retrieval Planner | **v1 已完成（离线）**：deterministic need → post-RRF type caps + pack budget；identity/disabled 精确回退；metrics 闭集深拷贝；**未部署** |
| Card category time eligibility | **v1 已完成（离线）**：读时 status/event TTL、full-cache 再过滤、TemporalTrace head-only、kill-switch；**未部署** |
| Graph provenance gate | **v1 已完成（离线 focused）**：写门禁 + 读隔离；legacy 脏行 approve requeue；**无 bulk repair**；**未部署** |
| Graph population / evidence-quality observability | **gpo_v1 已完成（离线终验）**：只读 health 质量分桶 + kill-switch；全仓 **4713**；I3 全量扫描性能残留；**未部署** |
| Context↔Episode joint prompt telemetry | **jdt_v1 已完成（离线终验）**：复用 trace 的只读联合视图；无 request ID/PII、无热路径新写；全仓 **4737**；**未部署** |
| Personalized PageRank | **NO-GO**：gpo/gpg/jdt 只给写门禁与可观测性；**PPR 在已部署真实脱敏 population、evidence-quality、latency 与 prompt-path 证据前禁止**；禁止用合成 fixture 过门 |
| Episode meta 查询索引 | 可选性能增强，本 slice 未做 |

## 验证矩阵

- 结构：composition root archive owner 明确；RetrievalGate 为权威检索决策；MessageLogPort 结构合同。
- 语义：时间更新命中新事实、旧事实可追溯但不再注入；证据不足时拒答；promote 幂等跨越 200+ 新 episode。
- 负向：跨群不可见、冲突事实、失效事实、semantic backend 不可用、单 seed 不扩展、hub 有界。
- Identity：同名不同 platform key 不互连；bare digits 不冒充 QQ；符号/空 legacy role 不毒化 graph source；malformed 顶层 key 不遮蔽合法内嵌 key；supersede rename/reassignment/legacy 三态可解释。
- 回归：memory/card/context/promoter/composition focused tests（见 ledger）。
- 运行态：未部署、未改生产 prompt。

## Test Ledger

| 时间 | 命令/实验 | 实际结果 | 结论 |
| --- | --- | --- | --- |
| 2026-07-15 | Grok 只读前沿/架构审计 | 7 项高价值差距；web_search 不可用，A/B/C 证据已分级 | 作为入口，不替代主代理代码复核 |
| 2026-07-16 | `tests/test_context_service.py` + `test_context_eval.py` + `test_retrieval.py` | **54 passed** | 含 2 条新增 graph 负向 |
| 2026-07-16 | + `test_conversation_archive_store.py` + `test_admin_memory_consolidator.py` | **100 passed** | archive owner / promote 闭环 focused 回归 |
| 2026-07-16 | 首次全量 pytest | **1 failed, 3784 passed** | promoter 旧 dry_run 合同；已修 |
| 2026-07-16 | 最终全量 pytest（A/B/C 子集） | **3785 passed, 17 skipped, 186 warnings** | 全量回归通过 |
| 2026-07-16 | 三残留 RED：promoter>200 / matched_active / MessageLogPort+no cast | 5 failed 于实现前（AttributeError/KeyError/ImportError/assert cast） | RED 成立 |
| 2026-07-16 | 三残留 GREEN + focused 组合 | **200 passed**（promote/admin/episode + retrieval/context + archive/timeline/state_board） | 三 slice 验收通过 |
| 2026-07-16 | scoped Ruff + Pyright（touched Python） | Ruff clean；Pyright **0 errors** | 静态通过 |
| 2026-07-16 | `rg` 证明 | promoter 无 `limit=200`；bootstrap 无 `cast(Any, ConversationArchive(` | 残留已清 |
| 2026-07-16 | Codex 主审加强 | memory focused **201 passed** | >200 用例显式固定旧时间并证明旧窗口看不到；补 meta-key 注入/坏 JSON 负向 |
| 2026-07-16 | QZone + memory 组合 focused | **305 passed** | 两条主线组合通过；相关 Ruff/Pyright clean |
| 2026-07-16 | 最终全量 pytest | **3819 passed, 17 skipped, 186 warnings** | 新增 harness / residual 与全仓回归兼容 |
| 2026-07-16 | Grok Entity Identity 只读矩阵审计 | Card owner 有 UIN；KG S/O 为 surface；Episode link 为空；Context `entity_key` 语义不一 | 冻结 additive/no-big-bang v1；不直接上 PPR |
| 2026-07-16 | Entity Identity TDD + review remediation | pure/context/KG/supersede/legacy/poison/metadata precedence RED→GREEN | focused **29 passed**；相关组合 **81 passed** |
| 2026-07-16 | Entity Identity scoped Ruff / Pyright | Ruff clean；Pyright **0 errors**（含测试） | 静态合同通过 |
| 2026-07-16 | Entity Identity 最终全量 pytest | **3848 passed, 17 skipped, 186 warnings** | 相对 3819 基线净增 29 个 identity 测试；全仓无回归 |
| 2026-07-16 | QZone v0.2 advanced/provenance + 当前记忆分支组合 | QZone **123 passed**；QZone+manifest/catalog/ownership **159 passed**；全量 **3940 passed, 17 skipped, 186 warnings** | QZone 独立复审 0 Critical/Important；当前 Episode/alias 脏改与全仓兼容 |
| 2026-07-16 | QZone v0.3 review console | migration v4 / 分页详情双审计 / health gate / Admin SPA；QZone 七文件 **133 passed**；Ruff/Pyright/type/build/UI compliance 均通过 | 独立 review 的 approved reject 状态矩阵与陈旧响应 2 Important 已修；未部署、无真实发布 |
| 2026-07-16 | QZone v0.3 + 当前记忆分支最终全量 | **3950 passed, 17 skipped, 186 warnings** | QZone v0.3 相对 3940 基线净增 10 个回归；当前 Episode/alias 脏改继续兼容 |
| 2026-07-16 | Episode typed refs RED（5 新测试文件） | **32 failed, 3 passed** | 真实 resolver / scope kwargs / bootstrap / provider evidence / nickname seed 均缺失 |
| 2026-07-16 | Episode typed refs GREEN + fake 兼容 | focused **35 passed**；+ promoter unit **54 passed**；related **254 passed** | 跨群 poison / 状态过滤 / 非数字 group / 幂等 re-promote / alias reopen 负向成立 |
| 2026-07-16 | scoped Ruff + Pyright（wiring 切片） | Ruff clean；Pyright **0 errors** | 含 prod + 新/改 tests |
| 2026-07-16 | Episode typed refs 全量 pytest | **3985 passed, 17 skipped, 186 warnings** | 相对 3950 基线净增约 35；无 QZone/validated/NapCat 触碰 |
| 2026-07-16 | 独立 Grok review + Codex 反例复核 | **0 Critical / 2 Important**；PK 1 下 bool/float coercion 可复现；空 ids + typed property fallback 可复现 | 评审建议经本地探针确认后进入 TDD remediation |
| 2026-07-16 | review remediation RED→GREEN | 新增 **3 failed** → focused **38 passed**；相关 linked-ref/promoter 组合 **110 passed** | resolver 仅接受正 int/纯数字字符串，scalar fail-closed；Provider 空 ids 回退 typed refs，`LinkedMemoryRef` 可规范化 |
| 2026-07-16 | Unicode 非十进制数字补强 | `"²"` 反例 **2 failed**（archive/promoter `ValueError`）→ `isdecimal()` 后 **2 passed** | 非十进制 Unicode digit 不再穿透 `isdigit()` 或导致 resolver 抛错 |
| 2026-07-16 | review remediation 最终静态 + 全量 | Ruff clean；Pyright **0 errors**；**3988 passed, 17 skipped, 186 warnings** | 两项 Important 已修；最终无 Critical/Important 遗留 |
| 2026-07-16 | Hot-path write policy review/correction | focused **97 passed**；Dream+related **123 passed**；全仓 **4038 passed, 17 skipped, 186 warnings** | 原子写入与 active-only 输入前提验收；未部署 |
| 2026-07-16 | Temporal Trace 三轮 review/correction | focused **149 passed**；Ruff clean；Pyright **0 errors**；全仓 **4116 passed, 17 skipped, 186 warnings** | pool/premise/typed refs/budget/startup 全部收口；未部署 |
| 2026-07-16 | evidence-use eval gate RED | `ImportError: TemporalTraceExpectation`；新测试模块在实现前不可收集 | RED 成立（schema/score/missing-assembler/integration） |
| 2026-07-16 | evidence-use GREEN + fixture | evidence-use **21 passed**；related focused **147 passed**；fixture JSON parse OK | E1–E10 + E11 由既有 2-hop graph 负向覆盖；真实 CardStore+assembler |
| 2026-07-16 | evidence-use scoped Ruff/Pyright | Ruff clean；Pyright **0 errors** | 仅 eval/export/tests 触达；生产 retrieval 未改 |
| 2026-07-16 | QZone focused 基线保留 | **154 passed** | 本 slice 未触 QZone |
| 2026-07-16 | evidence-use 最终全量 pytest | **4148 passed, 17 skipped, 186 warnings** | 相对 4127 基线净增 21；0 failure |
| 2026-07-16 | evidence-use adversarial Grok review + Codex probes | **2 Critical / 5 Important / 1 Minor**；8 个交接风险均复现 | ACTIVE 的初次 accepted 过强，冻结 strict schema / implied presence / valid DTO / same-KP coherence / honest E7 remediation |
| 2026-07-16 | strict remediation RED → GREEN | schema 13 failed；absent 2 failed；malformed 4 failed；split-KP 1 failed → evidence-use **41 passed**；related **167 passed** | 20 条新回归关闭全部 Critical/Important；cancel/legacy/真实 E2/E3 不变 |
| 2026-07-16 | post-remediation Grok review + static/QZone/full | **0 Critical / 0 Important**；Ruff clean；Pyright **0 errors**；QZone **154 passed**；全仓 **4168 passed, 17 skipped, 186 warnings** | 相对 4148 净增 20；`BUILTIN_WIRE_PROFILE.validated=false`；未部署 |
| 2026-07-16 | KG autopilot promotion loop RED | `test_knowledge_ai_reviewer` **8 failed**（Path/service 合同 + 双注册 + approve kwargs） | RED 为行为断言失败，非 import 收集错误 |
| 2026-07-16 | KG autopilot promotion loop GREEN | focused **8 passed**；+ `test_knowledge_graph` + admin learning pipeline + application build **44 passed** | service promote / legacy repair / 单 reviewer 验收 |
| 2026-07-16 | Autopilot applied-outcome Style/Episode/Slang | shared parser + applied-outcome / cursor / slang malformed + KG/Admin/Slang 组合 **116 passed** | counters/remaining/completed 对齐实际写入；`llm_assess` Pyright 类型债清零 |
| 2026-07-16 | scoped Ruff + Pyright（promotion slice） | Ruff clean；Pyright 生产 touched 0 errors | 未部署、未改 live DB |
| 2026-07-16 | KG promotion **Codex correction RED** | 加强后 `test_knowledge_ai_reviewer` **13 failed / 10 passed**（无 `db_path`、fallback 仍注册 fact、非法 conf/decision 仍 promote） | 验收缺口可复现 |
| 2026-07-16 | KG promotion **Codex correction GREEN** | focused **23 passed**；+ KG/llm_extractor/admin learning/application 组合 **91 passed**；Ruff clean；Pyright service/reviewer/pipeline 0 | no-fallback / public db_path / strict verdict / D2 cancel 收口 |
| 2026-07-16 | lightweight CI wiring | workflow YAML/step assertion 通过；CI exact command **41 passed** | PR/main push 将运行真实 CardStore+assembler evidence-use gate；远端 Actions 尚未运行，不伪报 CI green |
| 2026-07-16 | graph eval fail-closed RED -> GREEN | 非有限/越界/负计数/字符串布尔/非法 top-k/空 gold/内部矛盾/overflow 反例由失败或异常转绿；graph eval **53 passed** | harness 可作为候选闸门；仍不冒充官方 benchmark |
| 2026-07-16 | graph window + hub control TDD/review | scope target、star hub、raw lexical、scope priority、batch evidence 回归；CI exact 三文件 **83 passed**；独立 review 无 Critical，Important 全修 | 生产仍为双 seed/max-2-hop；PPR 未接线 |
| 2026-07-16 | QZone v0.6 + memory graph 最终全仓 | **4272 passed, 17 skipped, 187 warnings**；Ruff clean；Pyright **0 errors** | 相对 v0.5 的 4196 基线新增合同/回归，全仓 0 failure；未部署 |
| 2026-07-16 | Episode v2 decay/rerank 实现 + acceptance 收口 | store/provider/admin + focused 三测试；composite cap=1200；`fetch_limit=min(24,max(k,3k))` | 离线验收；**未部署**；见 migration `memory-episode-decay-query-rerank-v2-2026-07-16` |
| 2026-07-16 | Episode v2 **julianday offset-safe** review remediation | RED：`test_list_for_recall_offset_safe_keeps_future_legacy_utc` 断言失败（字典序误杀 future UTC）；GREEN：`list_for_recall`/`expire_decayed` 改 `julianday`；focused 三文件 + Ruff/Pyright/`git diff --check` | 审评 Important 已修；非法 legacy fail-closed；无 backfill/schema migration；**未部署** |
| 2026-07-16 | Episode v2 最终 Codex 验收 | focused 三文件 **79 passed**；typed-refs/bootstrap 相关 **161 passed**；QZone v0.7 保留 **236 passed**；全仓 **4350 passed, 17 skipped, 187 warnings**；scoped Ruff clean；生产 Pyright **0 errors**；`git diff --check` clean | 相对 QZone v0.7 的 4318 基线净增 32 tests；全仓 Ruff/Pyright 仍被既有未跟踪 coursework/research/IPv6 与历史测试类型债阻断，本 slice 未改 |
| 2026-07-16 | KG final-review remediation A + applied-outcome B Codex 验收 | KG CAS/事务/cursor + 四 reviewer + Admin/Application 组合 **177 passed**；QZone v0.7 **236 passed**；Ruff clean；生产 Pyright **0 errors** | 3 个 Grok Important 全部 RED→GREEN；未部署、未触 live DB |
| 2026-07-16 | 最终全仓 + Grok closure review | **4442 passed, 17 skipped, 186 warnings**；Grok **0 Critical / 0 Important** | 相对 4350 基线净增 92 tests；`BUILTIN_WIRE_PROFILE.validated=false`；接受离线代码切片 |
| 2026-07-16 | Query-Aware 只读架构/前沿审计 | 新 endpoint 完成 A-H 包；主调用链、三方案、合同/RED/rollback 齐全；`grok-exit: 0` | 选纯 deterministic planner；不建第二 RetrievalGate，不改 mode/weights/schema |
| 2026-07-16 | Query-Aware 首轮 RED → GREEN | 新模块不存在约 **22 failed** → focused **214 passed**；Ruff/Pyright/diff-check 初步通过 | 首版实现可运行，但不以绿灯替代独立审评 |
| 2026-07-16 | Query-Aware 独立 review | **0 Critical / 4 功能 Important + coverage gap**；英文 substring、中文弱 marker、小预算、plan_meta 浅拷贝均复现 | verdict `ACCEPT AFTER FIXES`；进入 adversarial remediation |
| 2026-07-16 | Query-Aware remediation RED / transport | 四类 Important adversarial tests 先失败；长修正会话在代码落盘后出现 **524**（15 calls / ~677s） | 新 endpoint 启动/短任务稳定，但长流 524 未完全消失；保留工作树进度并缩短后续 review 包 |
| 2026-07-17 | Query-Aware Codex correction + focused/static | focused **251 passed**；最终 sanitizer/marker 补强 **119 passed**；Ruff clean；Pyright **0 errors**；diff-check clean | word-boundary、保守中文 marker、content-capacity 比例预算、metrics allowlist/deep-copy 收口 |
| 2026-07-17 | Query-Aware 最终全仓 + post-fix review | **4543 passed, 17 skipped, 188 warnings**；Grok **0 Critical / 0 Important，ACCEPT** | 相对 QZone v0.8 4473 基线净增 70 tests；未部署、未 commit、未触 live DB/Docker/NapCat |
| 2026-07-17 | gpg_v1 suite regression 修正后全仓 | **4672 passed, 17 skipped, 186 warnings** | 裸 id→evidence 与 alias no-synthesis 关闭原 8 failures；未部署 |
| 2026-07-17 | gpg_v1 独立 Grok adversarial review（更新 endpoint） | **0 Critical / 0 Important，ACCEPT offline**；`grok-exit: 0`；主会话无 524 | 标题生成单次 503 自动降级；主 review 正常完成；无文件修改 |
| 2026-07-17 | gpg_v1 scalar hardening TDD + focused/static | 20 个 bool/NaN/±Inf ID 反例 RED→GREEN；相关 **152 passed**；Ruff clean；Pyright **0 errors**；diff-check clean | 写侧 `invalid_id_scalar`；读侧 fail-closed；普通兼容面不变 |
| 2026-07-17 | gpg_v1 最终全仓 + Grok post-fix review | **4692 passed, 17 skipped, 186 warnings**；Grok **0 Critical / 0 Important，ACCEPT post-fix** | 离线切片接受；未 commit/deploy；未触 live DB/Docker/NapCat/QZone |
| 2026-07-17 | gpo_v1 前沿对比审计 + 合同冻结 | A population/observability ≫ B 只读诊断并入 A > C Episode↔Context 延期；禁止 conversion rate；官方 Zep/Graphiti/HippoRAG/Mem0/LongMemEval/MemTrace 已核验 | 进入 TDD 实施；`grok-exit: 0`、无 524 |
| 2026-07-17 | gpo_v1 TDD RED → GREEN | 新模块 `tests/test_graph_population_observability.py` **21 passed**；分类/store/service/route/kill-switch/cancel 分层绿灯 | 实现可运行；不以绿灯替代独立审评 |
| 2026-07-17 | gpo_v1 独立 Grok review（初审） | **0 Critical / 3 Important** | I1 silent exception fold；I2 weak `>=` counts + kill-switch 未锁 SQL；I3 全量扫描性能 |
| 2026-07-17 | gpo_v1 I1/I2 fix + Codex focused | type-only error/log + cancel 传播；精确 counts + kill-switch never-await；Codex focused **144 passed** | I1/I2 关闭；I3 明示残留 |
| 2026-07-17 | gpo_v1 最终全仓 + static | **4713 passed, 17 skipped, 186 warnings**；Ruff clean；Pyright **0 errors / 0 warnings**；`git diff --check` clean | 相对 gpg 4692 基线净增约 21；全仓 0 failure |
| 2026-07-17 | gpo_v1 Grok post-fix review | **0 Critical / 0 Important，ACCEPT post-fix** | 离线切片接受；未 commit/deploy；未触 live DB/Docker/NapCat/QZone |
| 2026-07-17 | A-D 剩余候选只读对比审计 | **B jdt ≫ A legacy inventory ≫ D episode meta index > C official benchmark CI**；无文件修改 | 选择 B；双路径有意平行，只做联合 telemetry，不 merge ranking |
| 2026-07-17 | jdt_v1 Grok TDD 检查点 | 初版新测试 **22 passed**、Ruff clean；随后 endpoint 返回明确 `auth_unavailable` 503，Codex 停止会话并接管 | 新 Grok 地址本轮无 524；认证故障不冒充成功退出；工作树检查点保留 |
| 2026-07-17 | jdt_v1 Codex hardening RED→GREEN | 未知/shadow decision、空 request、Loguru 模板先 **3 failed**→**3 passed**；unknown-only pure group **1 failed**→**1 passed** | 只统计真实 accepted/trimmed/rejected budget path；虚假 present/sample 关闭 |
| 2026-07-17 | jdt_v1 独立 privacy review/fix | 初审 **1 Critical / 0 Important，REJECT**：raw request ID 内嵌群/用户 session；隐私 RED **3 failed**→移除公开 request ID→**3 passed**；post-fix **0C/0I/0M ACCEPT** | Store 内部仍可分组/排序；Admin payload 不可反推群号/用户号 |
| 2026-07-17 | jdt_v1 最终 focused/full/static | 新 jdt **24 passed**；focused **56 passed**；全仓 **4737 passed / 17 skipped / 186 warnings**；jdt 相关 Ruff clean；targeted Pyright **0 errors / 0 warnings**；diff-check clean | 全项目 Ruff/Pyright 被既有无关 dirty/untracked 文件 **179 / 378** 项挡住，不误报全绿、不越界修改 |
| 2026-07-17 | legacy graph evidence A1/A2/A3 合同审计（更新 Grok 通道重建） | Grok session `e156c799-94b1-4f5c-941f-c403e8132c63` 完整 `grok-exit: 0`，主审无 524/auth_unavailable、无文件修改；Codex 复核 schema/provenance/gpo/Admin/raw list surface | **A3 NO-GO/defer**：aggregate 已由 gpo 覆盖；A1/A2 行级输出在 repair 授权、handle 与真实 population 前不实装；标题辅助请求仍可单次 503 降级 |
| 2026-07-17 | Episode meta index Grok 重建 | session `89847a0e-220c-4ad0-946e-7df0549d4372` 的主模型仍请求旧 endpoint 并返回明确 `auth_unavailable`；按 dispatch skill 立即终止，未改文件 | delegated scope 保持 reconnecting；`~/.grok/config.toml` 的有效 host 仍为旧值，不能把用户侧地址更新误报为已生效 |
| 2026-07-17 | Episode meta index Codex EXPLAIN + synthetic benchmark | SQLite 3.45.3；动态 path：10k 同 source median **2.575 ms**、100k **27.154 ms** 且仍 `SCAN episodes`；固定 literal + partial expression index：`SEARCH ... (source=? AND <expr>=?)`，两规模约 **0.002 ms**；100k index build **63.72 ms**（内存合成） | planner 证明固定 path 才能用 expression index；但唯一生产调用为 Admin approve 低频路径，选择 **C defer**，不以 synthetic 微基准冒充生产收益 |
| 2026-07-17 | LongMemEval raw-turn replay v1 TDD/验收 | 官方 commit/MIT/schema/scorer 复核；新会话全量注入与 init-cancel RED→GREEN；**32 focused / 170 related / 4769 full**；Ruff clean；Pyright 0 | 手动离线诊断接受；full benchmark/CI defer；不改生产 ranking；Grok review 因有效配置仍是旧 endpoint 保持 reconnecting |
| 2026-07-17 | LongMemEval Grok 正常模式重连 `f273adc2-e567-48a0-97b1-6dee5d792497` | 标题辅助模型 503 后降级；主 `grok-4.5` 仍请求旧 endpoint 并明确 `auth_unavailable`；按 skill 中断，exit 130 | 非 524；无有效 review/文件改动；delegated scope 继续 reconnecting，不把启动消息冒充审评结果 |
| 2026-07-17 | LongMemEval 独立 review + Codex correction | Grok `fb683c70-061f-4e9e-8e1d-bb48f35b2859` 正常 `grok-exit: 0`，结论 **ACCEPT**，同时给出 I1 session turn→session 不等价、I2 DEBUG query leak、I3 文档过称、I4 adversarial gaps；Codex 另从官方 pin 复核 README + `eval_utils.py` | RED **11 failed / 31 passed** + CLI log RED **1 failed** → strict fields/enums/non-empty、duplicate fail-closed、opaque IDs、metric scope 标记、CLI 临时禁用 context/memory logger → **42 focused / 235 related / 4785 full**；Ruff/Pyright/diff-check clean；待 post-fix 独立终审 |
| 2026-07-17 | LongMemEval post-fix Grok review | 前两次重建主请求 524、无输出；第三次 session `cf7d58ae-f213-4321-9ed8-c1aeadfc5d42` 完整 `grok-exit: 0` | **ACCEPT / 0 Critical / 0 Important / 3 Minor**；I1-I4 全关闭。M1 stale 4769 文案已清；M2 exact opaque shape test、M3 library logging 为非阻塞残留 |
| 2026-07-17 | 全记忆 A/B/C/D 总审 + staged rollout 初版 | Grok 总审 `615cbc4d-0331-4a40-a01d-225c840db462`；初版 RED **3 failed**→contract **3 passed**、related **312 passed**、full **4788/17/186** | 最高风险是多个 default-on 切片一次首发；Stage 0 全暗但 gpg=true；PPR/GraphRAG/tools/full benchmark 继续 NO-GO |
| 2026-07-17 | staged-rollout post-implementation review | Grok `717a68ed-b0a4-47be-a3a6-acc361754e94` 完整 `grok-exit: 0` | **ACCEPT / 0 Critical / 2 Important / 4 Minor**；I1 缺 TemporalTrace/memo/gpo/jdt 同 profile runtime gate；I2 dotted flag 未绑定真实 plugin/main config owner |
| 2026-07-17 | staged-rollout I1/I2 remediation + verification | runtime/config 四条先通过、runbook mapping 保持 RED **1 failed / 4 passed**→GREEN **5 passed**；Grok 只读调查 `fec176a0-00d4-4fbe-8145-ddec3d8103ee` exit 0；related **314**、config **37**、full **4790/17/186**；Ruff/Pyright/JSON/diff-check clean | I1/I2 均有真实路径证据：sidecar/add-only/pre-gpo/no-CTE；PluginConfigStore canonical wrapper + manifest/schema + runtime loader + BotConfig；待 post-fix 独立终审 |
| 2026-07-17 | staged-rollout post-fix Grok review | 前两次窄审 session `2c5b4191-affa-4d41-805f-e5bbff2af0d4` / `00ec71e7-8c70-48bd-b39c-a60d0ef66729` 在 verdict 前 524；第三次 `9aedda78-378c-4282-832f-3cc84f255baa` 完整 `grok-exit: 0` | **ACCEPT / 0 Critical / 0 Important / 2 Minor**；I1/I2 全关闭。M1 no-canary 是授权边界；M2 set_values 替换语义已由 runbook/test 强制 deep-merge 缓解 |

## 当前风险

- 工作树含并行脏改与未跟踪产物；记忆/QZone 收口禁止 `git add -A`。
- composition-root / alias / typed promoter / write-policy / temporal trace 接线**未部署**；生产仍运行旧行为。
- Query-Aware Planner v1 **未部署**；规则刻意保守，真实 profile 分布/时延/回答收益仅能在独立部署窗观测；可用 `query_aware_plan.enabled=false` 回退。
- `find_by_source_meta` 无索引：episode 量大时 promote 幂等查询为 O(n) JSON scan（正确性优先）。
- 历史 episode 无 backfill：仅新 promote 路径写出完整 typed refs；旧 episode 仍可能只有 message_pk/legacy。
- 自定义/旧 unit fake 若未接受 `chat_type`/`allowed_scopes`，promoter 会 best-effort 跳过 enrichment（日志 warning），不阻断 promote。
- scope-aware graph window 最多读取 8x200 facts；evidence 已按 400 IDs 分块批量加载，但真实数据规模下的延迟仍需部署后观测。
- `KnowledgeGraphStore.set_candidate_status` 仍是未被生产调用的无条件内部 API；未来调用者必须优先使用 CAS transition/service。
- gpg kill-switch 只重新打开 unsafe 写路径；读侧 `graph_fact:*` quarantine 与 supersede no-self-fallback 仍保留，运维不得把它理解为完整 legacy 时光倒流。
- gpo kill-switch `knowledge_graph.observability_enabled=false` 恢复 pre-gpo health 顶层 key shape 并跳过质量扫描；**不**关闭 provenance gate。启用时 admin-only 全量只读扫描（I3）在大图下可能增加 health 延迟/SQLite 读竞争；不在聊天热路径。
- jdt kill-switch `block_trace.joint_dual_path_telemetry_enabled=false` 返回 disabled zero shape 并跳过联合 SELECT；不关闭原 trace 写入/retention。jdt 依赖现有 retention，只观察最终 emitted block role，不公开 runtime request ID，也不伪报精确 `ContextPack.pack_state` 或 trimmed 后剩余字符。Admin CTE 在长历史上仍需部署后观测延迟。
- legacy evidence 行级 inventory 当前明确 defer：`graph_evidence` 已压平为 type/id/quote，输入 alias/conflict 原形不可恢复；`extraction_candidates.evidence_json` 虽保留原形，但 Admin 已有 raw candidate surface。新增脱敏 handle 若可关联则有枚举风险，若不可关联则没有 repair 消费价值。
- Episode `find_by_source_meta` 仍为 source + `json_valid/json_extract` 的 O(n) 查询；当前只服务 Admin episode promote 幂等，不在聊天热路径。只有 deployed p95 >10 ms、同 source >50k 或批准路径可见受阻时才重新打开 expression-index migration；不得把本轮内存 synthetic 数字当生产容量结论。
- LongMemEval replay 的 source-message provenance 确定，success report 的 case/session/turn ID 已按 case 域哈希；production CardStore 随机 ID 使等分项顺序不保证跨 fresh DB 一致，report 明示 `ranking_tie_break_deterministic=false`。session 指标只代表最终 pack 内 turn-top-k 的有序 unique sessions，明示 `upstream_turn_to_session_equivalent=false`；不为跑分扩窗或改生产 ranking。

## 下一步

1. **legacy graph evidence inventory 选择 A3 defer**：不新增重复 gpo 的 aggregate endpoint，不新增 A2 CLI，也不暴露 row id/quote/scope。仅在 gpg/gpo 已部署且有真实脱敏 materiality、repair 授权/audit、稳定 handle、max-N 性能预算与独立 privacy review 后重新评估 A1。
2. **Episode meta index 选择 C defer；LongMemEval bridge 已收口**：手动 raw-turn replay v1 已完成，官方全量 benchmark/CI 选择 NO-GO/defer。只有独立 capacity job、合法本地数据、reader/LLM judge 预算与稳定 tie policy 获批后才重开，不改生产 ranking 追分。
3. QZone v0.8 离线终验（全仓 4473、post-fix 0C/0I）；真实发布仍等待真实脱敏 CGI fixture → 独立 validated profile → 用户授权单条 canary；内置 profile 保持 `validated=false`。
4. **PPR 明确 NO-GO**：在**已部署**真实脱敏 population、evidence-quality、latency 与 prompt-path 证据存在前，禁止 Personalized PageRank / diffusion；禁止用合成 fixture 或 gpo/jdt 计数 alone 过门。GraphRAG / Memory Tools 继续后置。
5. 可选性能项：`episodes.meta_json` generated column / 表达式索引；历史 episode 仅允许有界 re-promote，禁止 big-bang。部署永不 touch NapCat。

## 2026-07-16 Query-Aware RetrievalGate v1 入口决策

- 前序双流前沿审计已把该项排在 Card decay bridge / pack-time confidence gate 之前；不重新比较 PPR、GraphRAG、完整 MemGPT tools。
- 当前仅冻结**审计问题**，尚未写代码：用户 query 的时间/实体/偏好/关系/纠错意图如何影响 Card、document、graph、episode、Temporal Trace 的 source selection 与 budget；何时 abstain；如何保持 scope 隔离和统一 provenance。
- 第一包必须只读：画清 `services/memory/retrieval.py`、`services/context/service.py`、`services/context/sources.py`、ProviderBus/TemporalTrace 的真实调用顺序；列出与 LongMemEval-V2 evidence-use、MemTrace failure point、LoCoMo multi-session、HippoRAG multi-hop、Mem0 memory routing 的可比点与不可比点。
- 禁止在没有本地 RED baseline 前同时改 query rewrite、source planner、ranking weights 与 store schema；首个实现 slice 只允许 additive、可回退、可由现有 eval fixture 证明。

## 2026-07-16 下一切片前沿比较审计决策

依据（论文编号沿用项目已记录来源；本地 fixture 仅覆盖能力维度，不冒充官方数据集或 leaderboard）：

- LongMemEval-V2（arXiv:2605.12493）：前提错误、时间更新与 evidence-use 不能只用 hit-rate 代替。
- MemTrace（arXiv:2606.17328）：长期记忆失败点包含检索后没有正确使用 current / earlier / trajectory。
- LoCoMo（arXiv:2402.17753）：多会话时间与多跳维度；当前仓库仅做 style/capability fixture。
- Graphiti/Zep（arXiv:2501.13956）：历史边/时间关系保留支持后续图扩散，但不能替代证据使用门禁。
- MemGPT（arXiv:2310.08560）：受控 memory tools 有长期价值；当前已有 lookup/update + write policy 子集，立即扩为完整 tool loop 会放大双写和预算风险。

| 候选 | 当前收益 | 风险/前置 | 决策 |
| --- | --- | --- | --- |
| evidence-use / false-premise CI gate | 高：把已完成但未部署的 active-only、supersede、Temporal Trace 合同统一回归 | 纯离线、无生产时延、可用 synthetic chain | **本切片实施** |
| bounded PPR / diffusion | 中：可能改善多跳召回 | 需先有统一 gate、图密度/枢纽泄漏基线，当前 `limit=200` + 2-hop BFS 仍应先审计 | 后置 |
| MemGPT/Letta memory tools | 中长期 | 已有部分 tools；完整 loop 会增加 tool rounds、双写和 write-policy 不一致风险 | 后置 |

冻结合同：现有 `ContextEvalCase/Result/Summary` additive 扩展 `current_message` 与 trace expectation；可选真实 `TemporalTraceAssembler`；验证 trace present/reason、current/earlier/trajectory required/forbidden、typed evidence ref 前缀、KP/字符预算、missing assembler fail-closed。普通 `ContextService` hits/RRF/pack 行为不改；不做答案 LLM judge、PPR、GraphRAG、Memory Tools、数据集下载、部署或 NapCat。

## 2026-07-16 Query-Aware Retrieval Planner v1 合同冻结

只读 Grok 审计在更新后的 endpoint 上正常完成（`grok-exit: 0`，未再出现 524）；Codex 复核 `ContextPlugin -> ContextService -> RetrievalGate / Knowledge / Graph -> RRF -> type caps -> pack` 真实调用后接受“纯 deterministic query planner”方向，但收紧为以下最小合同：

- 插入点唯一：`ContextPlugin.on_pre_prompt` 完成 `rewritten_query || conversation_text` 与 `retrieve_mode` 解析之后、调用 `build_prompt_context` 之前。
- planner 是同步纯函数、无存储、无 LLM、无第二 RetrievalGate；RetrievalGate 继续是 Card 检索唯一权威 owner。
- v1 只识别闭集 need：`ordinary_fact`、`preference`、`temporal_current`、`temporal_earlier`、`premise_check`、`relation_multihop`、`broad_recall`、`doc_grounding`；最多两个，无法可靠识别时回到 `ordinary_fact`。
- **不修改 Thinker 的 `retrieve_mode`**：`skip/doc/fact/hybrid` 原样传递，planner 不 widening、不 narrowing；v1 仅生成 `type_caps`、pack `ContextBudget` profile、`top_k` clamp 与 secret-free reason codes。
- `type_caps` 是 RRF 后置限制，`ContextBudget` 只影响 pack；文档和指标不得把 v1 描述成底层 source recall / ranker 已改变。
- 空字符串或纯标点继续沿用现有 ordinary-pack abstain；“别猜/不知道”等自然语言不触发检索前跳过，避免误杀有证据查询。all-source miss 继续返回空 pack。
- Temporal Trace 仍由 `current_message` 独立授权；planner 不调用 assembler、不修改 trace reason/预算，不把 rewritten query 升格为授权信号。
- graph 仍为现有 bounded max-2-hop + hub control；EpisodeProvider 继续独立使用既有 query 路径，Episode 对齐另列 follow-up。
- RRF 权重、Card score 权重、semantic fallback、scope 解析、provenance schema、SQLite schema 全部不改。
- 配置新增 `query_aware_plan.enabled` kill-switch；默认随离线验收代码启用，但关闭时必须与 pre-v1 hit order / pack 行为一致。
- 观测仅记录 version、needs、profile id、type caps、top_k、reason codes 与 enabled/identity 状态；不新增 raw query/card 内容日志。

RED 矩阵：ordinary identity、preference memory profile、temporal/premise memory profile、relation graph profile、doc grounding profile、broad recall clamp、skip 不可强制检索、empty/punctuation abstain、disabled identity、planner exception fail-open、hard budget/type caps、Temporal Trace current-message authorization、semantic fallback、cross-group isolation、RRF dedup、legacy fake、cancel propagation。

NO-GO：修改 query rewrite、`retrieve_mode`、RetrievalGate tier/full-periodic、per-source candidate rerank、RRF/Card weights、graph hops、Episode merge、pack confidence gate、PPR、GraphRAG、MemGPT tools、schema migration、官方 benchmark parity、部署或 NapCat。

## 2026-07-17 持久目标续推：QZone 完成度 + 全记忆残留审计

- 不把 QZone v0.8 的离线 ACCEPT 误报为真实发布完成：真实脱敏 CGI fixture、独立 validated profile、用户授权单条 canary 仍是发布证据缺口；内置 profile 继续 `validated=false`。
- 先逐条对照 QZone charter / implementation tracker / code / tests，区分：已实现、仍可离线开发、只能由真实外部证据解锁。只有存在实质离线缺口才立 v0.9，不为版本号继续开发。
- 记忆系统下一审计从已记录排序继续：Card decay bridge 与 pack-time confidence gate 优先比较，同时复核全系统是否还有更高价值残留；不重新做已完成的 Query-Aware / Temporal Trace / graph window / Episode v2 / evidence-use / KG promotion。
- 前沿证据必须来自论文或同类项目的官方资料；本地 synthetic fixture 只证明合同维度，不冒充 LongMemEval/LoCoMo/HippoRAG 等官方 benchmark。
- 第一包只读，由一个顶层 Grok 正常模式协调自适应并行；Codex 保留排序、合同与验收权。审计完成前不改 ranking weights/schema、不部署、不触 live DB/Docker/NapCat。

## 2026-07-17 Pack-Time Confidence/Evidence Gate v1 合同冻结

前沿/项目依据：LongMemEval-V2（compact evidence + evidence-use）、MemTrace（reachable evidence 的使用失败点）、Mem0（scope/top-k/threshold 与记忆生命周期分离）、Graphiti/Zep（temporal invalidation + episodic provenance）、A-MEM（typed/evolving memory organization）。仅借鉴能力维，不冒充官方 benchmark parity。

- 唯一插入点：`ContextService.build_prompt_context()` 在 `search()`（RRF + type caps + top_k）之后、`pack_context_hits()` 之前；不改 source retrieval、RRF、Card score、Query-Aware、graph hops、Episode、TemporalTrace auth 或 store/schema。
- v1 采用 evidence-aware tiering，拒绝全局 confidence/evidence hard floor；post-RRF `hit.score` 是 fusion rank，不可当作证据质量。
- `memory_card`：`card_store_hint` 永远保留；`manual|user_config|migration|food_plugin` 无 message ref 仍合法；普通低置信且无 refs 仅降级到 memory bucket 尾部；非 active 或非有限 confidence fail-closed 省略。
- `doc_chunk`：无 confidence 是合法语义，只省略空正文，不要求 source_message_id。
- `graph_fact`：非 active/非有限 confidence 省略；弱置信 + 空 evidence + `graph_hop>=1` 才硬省略；其它空 evidence/低 confidence 只降级，不改 graph ranker。
- gate disabled 必须严格 identity；默认 offline code enabled，部署前可设 `pack_evidence_gate.enabled=false` 回退。
- gate 不 mutate 输入 `ContextHit`；输出保留 kept 原顺序，再接 demoted 原顺序。metrics 仅闭集 action/reason/count，不记录 query/content/title/message id。
- `ContextPack` additive 内部 `trace_seed_ids` 保存 gate 前真实 memory ids；`ContextPlugin` 优先用这些 ids 授权 TemporalTrace，兼容旧/fake pack 时回退 packed memory ids。hint id 不作为 trace seed。
- empty-after-gate：返回空 pack、不注入主 context block；TemporalTrace 仍可用 pre-gate seed/store head 扫描，不因普通 pack 省略而失效。
- `policy_from_mapping` 仅接受真实 bool 的 `enabled`；raw `0`/`"false"` fail-safe 保持 enabled，生产仍以 Pydantic 配置为入口。

RED：trusted legacy/manual/user_config 空 refs 保留；evidence-rich extractor 保留；low-conf untrusted memory 降级；NaN/Inf 省略；doc no-conf 保留/blank 省略；graph evidence-rich 保留、weak hop empty-evidence 省略、direct empty-evidence 降级；hint 保留；type caps 不复活；tight budget 强项优先；disabled identity；empty pack；cross-scope；输入 mutation safety；secret-free metrics；pre-gate trace seeds；cancel/legacy fake 兼容。

NO-GO：全局 confidence floor、要求所有 Card 必须有 message ref、修改 RRF/Card weights、修改 RetrievalGate mode/query plan、加入新 graph hop/LLM judge、join observations/store schema、PPR/GraphRAG/MemGPT tools、部署或 NapCat。

## 2026-07-17 Evidence-Use / Pack-State Contract v1 离线验收

前沿/项目依据继续沿用 LongMemEval-V2（检索后 evidence use）、MemTrace（reachable evidence 不等于正确使用）、Mem0（路由/阈值与生命周期分离）、Graphiti/Zep（可追溯 temporal provenance）。本切片只实现确定性 pack-state 合同，不把本地 synthetic fixture 冒充官方 benchmark。

- 新增闭集 `euc_v1`：`empty | hint_only | nonempty | demote_present | omit_only | skip`；只描述最终 pack 与 gate 动作，不声称回答已经 grounding。
- final-pack-first：`demote_present` 仅当 demoted non-hint hit 实际幸存；demote 后被预算丢空归 `empty`。`empty/hint_only/omit_only` 可注入低优先级中文软约束，`skip/nonempty/有幸存 hit 的 demote_present` 不注入。
- `ContextPack.evidence_use_contract` 与 `trace_seed_ids` 均为内部字段，`to_dict()` 公开 shape 不变；TemporalTrace 仍使用 pre-gate seeds。
- recent / metrics 仅闭集 state/action/reason/count；禁止 raw query/content/title/card/message/answer id；不新增 `answer_used_evidence=true`。
- 默认 eval 路径仍是 `search()+pack_context_hits()`；opt-in 只记录 pack_state，不冒充答案侧 judge。EpisodeProvider、tool loop、BudgetManager post-inject drop 仍是已知旁路，未在本切片假装解决。
- Codex 验收时复现并修正两个缺口：demote→budget-empty 原误标为 `demote_present`；`omit_only` 原未注入软约束。sanitizer 同时收紧 identity/action/inject 不变量。
- 验收：focused context **181 passed**；全仓 **4646 passed / 17 skipped / 186 warnings**；Ruff clean；Pyright **0 errors**；JSON / `git diff --check` clean；独立 Grok review **0 Critical / 0 Important，ACCEPT offline**。
- 回滚：`evidence_use_contract.enabled=false` 后仅 restart/recreate bot；无 DB migration；未部署、未 commit、未触 live DB/Docker/NapCat/QZone。

下一残留排序：**graph provenance completion/repair** 优先于 Episode/Context query-budget alignment。先只读审计 GraphContextSource/KG store/promoter 对 `evidence_refs` 的写入、读取、空 refs 分布和可修复边界；不顺手改 PPR/hops/ranking/schema/QZone。

## 2026-07-17 Graph Provenance Gate v1 离线终验

- suite correction 后全仓 **4672 passed**，证明裸 id→`type=evidence` 与 alias no-synthesis 关闭既有 8 个回归。
- 更新后的 Grok endpoint 完成长时 adversarial review，`grok-exit: 0`、无 524；结论 **0 Critical / 0 Important，ACCEPT offline**。启动标题生成仍可能单次 503 后降级，不影响主会话。
- Codex 接受 review 的 scalar residual：`True` / `NaN` / `±Inf` 不能成为证据 ID。20 个参数化反例 RED→GREEN；写侧闭集 `invalid_id_scalar`，读侧空锚点 fail-closed。
- post-fix 相关组合 **152 passed**；最终全仓 **4692 passed / 17 skipped / 186 warnings**；Ruff clean；Pyright **0 errors**；`git diff --check` clean。
- Grok post-fix 窄审 **0 Critical / 0 Important，ACCEPT post-fix**；未改文件。
- 离线接受边界不变：无 schema/bulk repair、无 commit/deploy、未触 live DB/Docker/NapCat/QZone；`BUILTIN_WIRE_PROFILE.validated=false`。
- 下一候选按价值重新比较：graph facts populate/observability、legacy graph evidence 有界诊断/repair 工具、EpisodeProvider↔Context query-budget-provenance alignment。PPR 继续 NO-GO，直到有真实脱敏图 replay 的收益/时延证据。

## 2026-07-17 Graph Population & Evidence-Quality Observability v1 合同冻结

### 前沿对比审计结论

- 一个正常全量 Grok 主进程协调 A/B/C 三条代码审计流与官方来源核验；`grok-exit: 0`、无 524、未改文件。
- 排序：**A graph population/observability ≫ B legacy evidence diagnostic（只读计数并入 A）> C EpisodeProvider↔Context alignment（延期）**。
- A 的真实缺口：现有 `graph_health` 只有 candidate status、active facts by source、24h edge/fact activity；无法区分“抽取为空 / pending 堵塞 / gate 拒绝 / active 缺 primary evidence”。
- B 尚无产品化 inventory/dry-run；gpg 只是新写门禁与 lazy requeue，不是 repair workflow。v1 只吸收只读诊断，不做 repair。
- C 是两条有意平行路径：ContextService 的 `ContextHit` 经 RRF/type caps/peg/euc；EpisodeProvider 经 ProviderBus 输出独立 `PromptBlockCandidate`。当前问题是联合 telemetry，不是应立即强行 merge 的 bug。
- Codex 复核发现：`graph_facts` 没有持久化 `candidate_id`，因此 v1 **禁止**把近 24h candidate/fact 独立活动伪报成 conversion rate；只保留“近期候选当前状态”和事实证据质量。

### 官方来源与能力维度（不声称 benchmark parity）

- Zep / Graphiti：<https://export.arxiv.org/abs/2501.13956>；temporally-aware graph、动态整合对话/业务数据、维护历史关系。
- Graphiti OSS：<https://cdn.jsdelivr.net/gh/getzep/graphiti@main/README.md>；temporal context graph、hybrid retrieval、validity windows、episode provenance。
- HippoRAG：<https://export.arxiv.org/abs/2405.14831>；KG + PPR 用于多跳长期记忆。Omubot 在事实 populate/可观测性过门前继续 PPR NO-GO。
- Mem0：<https://export.arxiv.org/abs/2504.19413>；动态抽取、整合、检索显著信息并支持多会话一致性；映射到本切片的是生产级 funnel/质量可观测性，不是照搬 benchmark 数字。
- LongMemEval：<https://export.arxiv.org/abs/2410.10813>；长期记忆拆为 indexing/retrieval/reading，并覆盖抽取、多会话、时间、更新、abstention。
- MemTrace：<https://export.arxiv.org/abs/2606.17328>；最终准确率掩盖知识点在年龄、问题类型、证据条件变化下的失败；直接支持按事实/证据质量观测，而不是只看总行数。

### 冻结实现合同 `gpo_v1`

- **唯一目标**：只读 graph population 与 evidence-quality health snapshot；不改变写入、检索、prompt、ranking 或 graph topology。
- 新增纯分类层（优先新文件 `services/knowledge_graph/observability.py`），复用 gpg normalizer/primary 语义。
- store/service 拥有只读 snapshot；Admin `graph_health` 不再直接操作私有 `writer._db`。
- 现有 health 顶层字段全部兼容；启用时新增嵌套 `observability`：
  - `version=gpo_v1`、`enabled=true`、`provenance_gate_enabled`。
  - `active_fact_support`: `primary | derived_only | invalid_only | none`（按 active fact 计数，优先级 primary > derived > invalid > none）。
  - `active_evidence_rows`: `primary | derived | invalid`（按 evidence row 计数）。
  - `active_evidence_type_histogram`: 仅闭集 `memory_card|doc_chunk|message|evidence|fixture|observation|episode|graph_fact|other|empty`。
  - `pending_evidence_quality`: `primary | derived | invalid | missing`。
  - `pending_gate_codes`: 仅已知 `provenance_gate:<code>` 闭集；未知归 `other`。
- 现有 `candidate_24h` 仅描述“近 24h 创建候选的当前状态”；**不**新增 conversion rate。
- 配置 `knowledge_graph.observability_enabled: bool = true`；关闭时 `graph_health` 恢复 pre-gpo 顶层 key shape，不执行质量扫描。
- **无 schema migration**；不写 DB；不输出 raw evidence id/quote/JSON、subject/object、scope/group、query/content。

### RED 矩阵

1. 空库：旧 health 字段存在，nested observability 全零。
2. active primary / graph_fact-derived / malformed / no-evidence 四类事实准确分桶。
3. 多 evidence fact 按 primary > derived > invalid > none 聚合，row histogram 独立诚实。
4. pending `{}` / graph_fact / malformed JSON-or-shape / valid primary 分类准确。
5. `review_note=provenance_gate:<known>` 进入闭集；任意未知/密文后缀仅 `other`，payload 不泄漏原文。
6. type histogram 未知自定义 type 归 `other`，不把攻击者标签作为 metric key。
7. kill-switch false：返回 key 集与 pre-gpo health 完全一致，且不调用质量扫描。
8. Admin route 只调用 service public snapshot，不访问 `._db`。
9. 重复 GET 除 `checked_at` 外稳定；取消/异常无写入副作用。
10. config default/bootstrap 透传；旧 fake/不可用 service fail-closed `available=false`。

### 回滚 / NO-GO

- 回滚：`knowledge_graph.observability_enabled=false` 后仅 restart/recreate bot；或还原 additive route/service/store/pure module。无 DB rollback。
- NO-GO：CLI、自动 repair/bulk backfill、live DB mutation、candidate→fact 虚假 conversion、PPR/hop/RRF/type caps、Episode merge、QZone、部署、`validated=true`。

## 2026-07-17 gpo_v1 离线终验与接受

### 实施与 TDD

- 纯分类 `services/knowledge_graph/observability.py`；`KnowledgeGraphStore.health_snapshot()` 拥有全部只读 SELECT/聚合；service 为 Admin 公开入口；route 不再碰 `writer._db`。
- Additive nested `observability`（`version=gpo_v1`）；`candidate_24h` 仍只是近期候选当前状态，**不**提供 candidate→fact conversion rate。
- 配置默认 `knowledge_graph.observability_enabled=true`；关闭后顶层 key shape 回到 pre-gpo，且不执行质量扫描。
- TDD：新测试 **21 passed**（空库、四类 fact support、row/type histogram、pending quality/gate codes、kill-switch、route ownership、cancel/type-only error）。

### Review 与修正

- 独立初审：**0 Critical / 3 Important**。
  - **I1**：route 将 snapshot 异常静默折叠为 `available=false` → 修为 type-only error token + warning 仅异常类型；`CancelledError` 继续上抛。
  - **I2**：事实/行计数仅 `>=`、kill-switch 未锁 evidence/pending SQL → 精确 counts；approved/rejected 排除；kill-switch never-await + 不触发质量扫描。
  - **I3**：admin-only 全量扫描性能 → **v1 明示残留接受**（非聊天热路径；大图可能延迟/读竞争）。
- Codex focused **144 passed**；全仓 **4713 passed / 17 skipped / 186 warnings**；Ruff clean；Pyright **0 errors / 0 warnings**；`git diff --check` clean。
- post-fix 独立 review：**0 Critical / 0 Important，ACCEPT post-fix**。

### 接受边界 / 下一候选

- **未** commit / push / deploy；**未**触 live DB / Docker / NapCat / QZone；`BUILTIN_WIRE_PROFILE.validated=false`。
- 回滚：`knowledge_graph.observability_enabled=false` 后仅 restart/recreate **bot**。
- 下一候选诚实顺序：legacy evidence 有界只读诊断/repair 工具 vs EpisodeProvider↔Context 联合 telemetry；**PPR 在已部署真实脱敏 population、evidence-quality 与 latency 证据前继续 NO-GO**。
- 迁移：`docs/migrations/memory-graph-population-observability-v1-2026-07-17.md`。

## 2026-07-17 Joint Dual-Path Memory Telemetry v1 离线终验

### 对比审计与合同收敛

- 剩余候选排序：**B Context↔Episode 联合 telemetry ≫ A legacy graph evidence 有界 inventory ≫ D Episode meta index > C official LongMemEval/LoCoMo CI**。
- ContextService 的 RRF/type caps/PEG/pack/EUC 与 EpisodeProvider 的 ProviderBus 是有意双路径；v1 不把 Episode 合并进 `ContextHit`，也不统一 ranking。
- 初始建议是在 `LLMClient` budget 汇合点新增 emission。Codex 复核发现 `PromptBudgetManager` 已将所有候选的真实 budget decision 按同一 request 写入 `prompt_block_traces`，因此冻结为 **BlockTraceStore 只读 read model**：不在聊天热路径重复写第二份事实。
- `jdt_v1` 仅识别 `context|context_temporal_trace|context_evidence_use|episode` 与 `accepted|trimmed|rejected`；accepted/trimmed 视为 survived；shadow/未知/畸形 fail-closed。

### Store / API / 隐私

- `BlockTraceStore.joint_dual_path_snapshot(limit)` 用一次有界 CTE SELECT 获取最近完整相关 request；不调用 `recent()` 截断半个 request，不 N+1 `list_for_request`，无 schema migration。
- Admin `GET /api/admin/block-trace/joint-memory-paths` 只调用公共 store snapshot；异常仅 type token，`CancelledError` 传播。
- kill-switch `block_trace.joint_dual_path_telemetry_enabled=false` 返回 exact disabled zero shape，且不执行联合 SELECT。
- 独立初审发现 privacy Critical：运行时 request ID 由 `session_id + monotonic` 组成，而 session ID 内嵌 `group_<群号>` / `private_<用户号>`。最终公开 recent 完全移除 request ID，只保留闭集 decision totals/outcomes；不得透传 query/text/label/group/user/session/candidate/evidence refs/provider/metadata/budget reason。

### 验收与边界

- TDD 新 jdt **24 passed**；Codex focused **56 passed**；全仓 **4737 passed / 17 skipped / 186 warnings**。
- jdt 相关 Ruff clean；targeted Pyright **0 errors / 0 warnings**；`git diff --check` clean。
- 初审 **1 Critical / 0 Important，REJECT**；privacy RED→GREEN 后 post-fix **0 Critical / 0 Important / 0 Minor，ACCEPT**。
- 全项目 Ruff/Pyright 受既有无关 dirty/untracked coursework/research/IPv6 等 **179 / 378** 项阻断；不归因于 jdt，也未越界修改。
- 未 commit/push/deploy；未触 live DB/Docker/NapCat/QZone；`BUILTIN_WIRE_PROFILE.validated=false`。
- 残留：依赖 trace retention；不冒充精确 ContextPack `pack_state` 或 trimmed 后字符；Admin CTE 延迟待真实规模部署后观测。
- 下一切片：legacy graph evidence **有界只读 inventory / dry-run design**；apply/UPDATE/bulk repair/CLI mutate 继续 NO-GO。PPR 在已部署真实脱敏 population、evidence-quality、latency 与 prompt-path 证据前继续 NO-GO。
- 迁移：`docs/migrations/memory-joint-dual-path-telemetry-v1-2026-07-17.md`。

## 2026-07-17 Legacy Graph Evidence Inventory A1/A2/A3 决策

- **A1 Admin/store read model**：aggregate 会重复 gpo；row-level max-N 只有在稳定、获授权的 repair handle 存在时才有增量价值。直接 fact/candidate/evidence ID 可枚举关联，普通 hash 仍可枚举，HMAC 又引入密钥生命周期；当前不实装。
- **A2 offline read-only CLI**：同样重复 aggregate，导出文件扩大泄漏面；仓库没有获批准的 apply/repair 消费者，且 gpo 迁移已冻结 CLI/repair NO-GO；当前不实装。
- **A3 design-only defer（接受）**：gpo 是 aggregate inventory，gpg 是唯一既有 pending-row 安全反应（approve 时非法 evidence requeue + 闭集 note）。`graph_evidence` 仅保留压平 type/id/quote，无法恢复输入 alias/conflict；只有 `extraction_candidates.evidence_json` 保留候选原形，现有 Admin candidate API 已能查看 raw 行但不是脱敏质量诊断。
- 重新开放 A1 的必要条件：gpg/gpo 已部署；取得真实脱敏 `active_fact_support` / pending gate materiality；明确谁可 list/repair 与审计日志；批准稳定 handle 策略；定义未来 CAS apply 的独立 GO；max-N/cursor 性能预算；达到 jdt 同级 privacy review。即使重新开放，也只能复用 `provenance.py` / `observability.py` 分类，禁止第二套 classifier、conversion rate、raw quote/subject/object/scope/id。
- Grok 更新通道复测：主审 session `e156c799-94b1-4f5c-941f-c403e8132c63` 完整退出 `0`，无 524/auth_unavailable；启动标题辅助请求仍可在旧辅助路由上 503 后降级。无 Grok 文件改动；未读 live DB、未触 Docker/NapCat/QZone。

## 2026-07-17 Episode Meta Index A/B/C 决策

- **真实调用面**：`EpisodePromoter._find_existing_episode()` 是唯一生产 caller，固定 `source="consolidator"`、`meta_key="consolidator_candidate_id"`；只在 Admin 把 episode-domain candidate 决定为 approved 时执行。测试另有直接 API 覆盖；没有聊天 prompt/retrieval 热路径调用。
- **现有查询**：`find_by_source_meta` 校验 meta key 后把 `$.<key>` 作为绑定参数传给 `json_extract(meta_json, ?)`；episodes 现有 index 仅 state/group/decay/cross-group/revision/observations，没有 source/meta index。
- **SQLite 官方合同**：expression index 只有查询表达式与 `CREATE INDEX` 表达式按文本一致时才会使用（<https://www.sqlite.org/expridx.html>）；generated column 可索引，但 STORED 不能通过 `ALTER TABLE ADD COLUMN` 加入，VIRTUAL 仍需 schema/restore 合同（<https://www.sqlite.org/gencol.html>）；Text JSON 每次 JSON 函数读取需解析，SQLite JSONB 也不承诺 O(1) element lookup（<https://www.sqlite.org/json1.html>）。因此现有动态 path 不能直接享受固定-path expression index。
- **前沿项目对比**：Graphiti `EpisodicNode` 把 `uuid/group_id/source/source_description/created_at/valid_at` 作为一等字段并以 episode 保存 provenance；Mem0 把 `user_id/agent_id/run_id` 提升为存储 metadata 与查询 filter。共同能力维是“高价值身份键应可直接过滤”，不是“任意 JSON key 都建索引”；本轮不声称 benchmark parity。
- **离线 planner/微基准**：SQLite 3.45.3、1% malformed JSON、hit/miss、source 100%/10%、1k/10k/100k。动态 path 在 100% source 下始终 `SCAN episodes + TEMP B-TREE`；固定 literal + `source, CAST(json_extract(...literal...) AS TEXT), updated_at DESC WHERE json_valid(meta_json)` 命中 `SEARCH ... (source=? AND <expr>=?)`。同 source baseline median：1k **0.230 ms**、10k **2.575 ms**、100k **27.154 ms**；literal indexed 约 **0.002 ms**。这是内存合成的 planner/数量级证据，不是生产延迟。
- **决策 C defer**：A（固定-path expression index）技术可行，但要新增 episodic v2 migration、schema/backup verifier 与字面量 query 分支；B（generated/dedicated column/table）迁移与双写更重。当前唯一 caller 低频且非热路径，10k 量级收益只有数毫秒，不足以承担 schema blast radius。
- **重新开放条件**：部署后 `find_by_source_meta` p95 >10 ms，或 consolidator source >50k rows，或该查找占 approve 路径可见比例；届时只重新评估 A，保留 generic fallback，不做 B/big-bang backfill，并加入 EXPLAIN、malformed JSON、migration adoption/restore/cancel/rollback 测试。
- **Grok 状态**：本轮 session `89847a0e-220c-4ad0-946e-7df0549d4372` 因有效配置仍指向旧 endpoint 而 `auth_unavailable`，已按 skill 终止；无 Grok 文件改动。未读 live DB、未触 Docker/NapCat/QZone。

## 2026-07-17 LongMemEval Raw-Turn Replay v1 离线验收

- **方案选择**：官方全量 LongMemEval/LongMemEval-V2/LoCoMo CI 继续 NO-GO。LongMemEval v1 约 500 cases，S 每 case 约 115k tokens；V2 还有截图、固定 reader/embedding/GPU 与 LLM judge；LoCoMo 为 CC BY-NC。仓库仅提供用户显式传入本地 JSON 的手动 raw-turn retrieval/pack replay。
- **官方 pin**：LongMemEval `9e0b455f4ef0e2ab8f2e582289761153549043fc`（MIT）；schema 与 `src/retrieval/eval_utils.py` 已从该 commit 复核。nDCG 忠实保留 upstream `log2(2..n)` 折扣，不替换成另一套标准公式。
- **真实栈**：严格 parser → 每 case 临时 CardStore → MemoryContextSource → RetrievalGate → ContextService → evidence gate/pack。benchmark question 不冒充在线新 session，避免 `full_new_session` 绕过 query relevance；不改 RetrievalGate/RRF/Card weights/production wiring。
- **provenance/隐私**：turn/session/source-message 确定映射；完整 `[date] role: content` 仅留在 replay DTO，CardStore search projection 为 `[date] content`，不让 adapter 角色标签制造关键词命中。success report 对 case/session/turn 外部 ID 做 case-scoped SHA-256 opaque 化；CLI replay 期间临时 disable `services.context` / `services.memory` 日志并在 finally 恢复，堵住 DEBUG query/keyword 旁路；JSON 无正文，失败只输出异常类型。
- **严格/取消**：官方六种 question type、必需非空 `answer/question_date`、`user|assistant` role、非空 haystack/session turns；重复 question/session/answer ID、数组错位、string boolean、未知 answer session、非法 root/case 与无 selected case 参数旁路全部 fail-fast。外部 search cancel 与 init 已开连接后的 cancel 均传播 `CancelledError` 且关闭 store/清理临时 DB。
- **验收更新**：独立 review 后 RED **11 failed / 31 passed** + CLI log RED **1 failed**；修正后 focused **42 passed**；CardStore/RetrievalGate/ContextService/pack 组合 **235 passed**；全仓 **4785 passed / 17 skipped / 186 warnings**；Ruff clean；Pyright **0 errors / 0 warnings**；diff-check clean。
- **诚实边界**：report 固定 `benchmark_parity=false`、`memory_extraction_scored=false`、`answer_generation_scored=false`、`deterministic_provenance=true`、`ranking_tie_break_deterministic=false`、`upstream_turn_to_session_equivalent=false`。session 只计最终 pack 中 turn-top-k 的有序 unique sessions；不冒充 upstream `evaluate_retrieval_turn2session` 的扩窗，generic English keyword 等分仍按生产行为暴露。
- **Grok review**：初审 `fb683c70-061f-4e9e-8e1d-bb48f35b2859` 完整退出并列 I1-I4；Codex 完成四项 correction。post-fix 前两次主请求 524 后按同 scope 重建，最终 `cf7d58ae-f213-4321-9ed8-c1aeadfc5d42` **ACCEPT / 0C / 0I / 3M，grok-exit: 0**；离线 replay review 闭环。
- **迁移/回滚**：`docs/migrations/memory-longmemeval-raw-turn-replay-v1-2026-07-17.md`。无生产接线/schema/data；删除 replay service/tool/test/doc 即可回滚，永不 touch NapCat/live DB/QZone，`validated=false`。

## 2026-07-17 全记忆当前态 A/B/C/D 证据审计 + staged rollout v1

### 部署真相

- 文档化生产 bot 仍为 `98887a5`；本轮未重读 live container。当前 HEAD `dcc75aa` 仅在其上补部署文档。
- `98887a5..HEAD` 无 7 月 16–17 记忆代码提交；`write_policy`、`temporal_trace`、`query_plan`、PEG/EUC、gpg/gpo/jdt、LongMemEval replay 等均为 dirty/untracked worktree。
- 因此：offline tests 证明代码合同，**不**证明生产收益/时延/人口分布；所有运行态能力仍归类为“已实现但未部署”。

### A/B/C/D 矩阵

| 能力域 | 分类 | 当前证据 / 边界 |
| --- | --- | --- |
| ConversationArchive composition、MessageLogPort、anti-join | **B 已实现未部署** | bootstrap/store/composition tests + migration；生产仍旧接线 |
| shared RetrievalGate、统一 provenance/score、semantic fallback | **B** | retrieval/context tests 与 full suite；未部署 |
| write policy、observations、原子 supersede | **B** | memo `write_policy_enabled` + focused review；默认 true，但未部署 |
| Entity Identity、alias、Episode typed refs | **B** | entity/alias/provider/promoter tests；无历史 backfill、无 kill-switch |
| TemporalTrace、card/graph supersede、Episode decay/rerank | **B** | temporal/episode tests；TemporalTrace 可关，Episode v2 为结构/读契约 |
| query planner、card eligibility、PEG、EUC | **B** | 四组纯合同/集成测试；worktree defaults 均 enabled |
| graph window/hub、gpg、gpo | **B** | graph eval/provenance/observability；window/hub 无专用 flag，gpo 有 scan-cost 残留 |
| KG/Learning autopilot applied outcomes | **B** | atomic promote/CAS/cancel/applied counters；live candidate inventory 未改 |
| jdt 联合 telemetry | **B** | privacy post-fix accepted；依赖 deployed trace retention/真实 CTE latency |
| evidence-use synthetic eval、graph eval、LongMemEval manual replay | **A 离线完成** | CI/manual harness；不接 production、不声称 benchmark parity |
| staged rollout profile/runbook + Stage-0 identity gate | **A 离线完成** | `docs/runbooks/memory-system-staged-rollout-v1.{json,md}` + `tests/test_memory_staged_rollout_contract.py` |
| deployed canary、真实 population/latency/prompt-path/user outcome | **C 真残留** | 需要单独用户授权部署窗；当前任务不具授权 |
| bounded historical Episode re-promote | **C / defer** | 仅新 promote 有 typed refs；禁止 big-bang，部署稳定后才评估 |
| PPR / GraphRAG / full MemGPT tools / official full benchmark CI | **D NO-GO** | 仍受真实 population、成本、tool owner、许可/judge 等过门条件阻塞 |
| legacy row inventory、Episode meta index | **D defer** | 分别等待 repair 授权/隐私 handle 与 deployed p95/source-scale 阈值 |

### 前沿候选结论

- HippoRAG 2 的价值是图上 associativity / multi-hop；当前只有 bounded BFS，且没有 deployed graph density/latency/prompt-path gain，PPR 继续 NO-GO。
- Microsoft GraphRAG 明示 indexing 成本高；community summary 在动态图人口尚无真实证据时低于 rollout 安全性。
- Letta/MemGPT 的完整 agent memory tools 会增加 tool rounds、双写与 write-policy owner 风险；Omubot 仅保留已有受控 lookup/update 子集。
- Graphiti/Zep 强调 temporal invalidation、episode provenance、hybrid retrieval；Omubot 的对应能力已离线实现，当前缺口是部署与真实指标，而不是再复制框架。
- LongMemEval bridge 已手动离线闭环；full dataset/reader/judge 仍为独立 capacity job。

### staged rollout v1 合同

- Grok 总审 `615cbc4d-0331-4a40-a01d-225c840db462` 将“默认开启多切片一次性首发”列为 Critical，将“无 cross-flag baseline gate”列为 Important，`grok-exit: 0`。
- TDD RED：新 rollout contract 因 machine profile/runbook 缺失 **3 failed**。
- GREEN：新增累积 phase profile + runbook；Stage 0 关闭全部 flappable 行为，但 `knowledge_graph.provenance_gate_enabled=true` 始终保持（false 为 unsafe legacy escape）。
- 顺序：observe → write hygiene → read eligibility → pack observe → evidence instruction → query planner → TemporalTrace。无 flag 的 archive/identity/typed refs/graph window/Episode v2/atomic promotion 以旧 bot image 回滚。
- 初版 contract **3 passed**；rollout 相关组合 **312 passed**；全仓 **4788 passed / 17 skipped / 186 warnings**；Ruff clean；targeted Pyright **0 errors / 0 warnings**；JSON parse / diff-check clean。
- 初版 post-implementation review session `717a68ed-b0a4-47be-a3a6-acc361754e94`：**0 Critical / 2 Important / 4 Minor**。两个阻塞均为合同证据缺口：Stage-0 未覆盖 TemporalTrace/memo/gpo/jdt 真实 disabled 路径；dotted flag 仅手写映射，未证明真实 plugin/main config owner。
- remediation：同一 Stage-0 profile 现在直接验证 TemporalTrace 零 assembler/零 sidecar、memo public branch 的 legacy add-only、gpo seeded pre-gpo shape 且无 quality SQL、jdt exact disabled zero shape 且无 joint CTE。operator mapping 按 root 分流：context/memo 经 `PluginConfigStore` canonical wrapper、真实 manifest/schema 与 runtime loader；knowledge_graph/block_trace 经 `BotConfig`；plugin values 深合并保留无关 operator 设置。
- remediation RED **1 failed / 4 passed**（仅 runbook mapping 缺失）→contract **5 passed**；rollout related **314 passed**；config-store/loader **37 passed**；全仓 **4790 passed / 17 skipped / 186 warnings**；scoped Ruff clean；targeted Pyright **0 errors / 0 warnings**；JSON/diff-check clean。只读调查 Grok `fec176a0-00d4-4fbe-8145-ddec3d8103ee` `grok-exit: 0`。post-fix 前两次 verdict 前 524；最终 `9aedda78-378c-4282-832f-3cc84f255baa` **ACCEPT / 0 Critical / 0 Important / 2 Minor / grok-exit 0**，I1/I2 全关闭。

### 当前真实残留 / 下一门

1. 当前 offline objective 已以 staged rollout post-fix **0C/0I ACCEPT** 收口；没有证据支持继续加 PPR/GraphRAG/tools。
2. 下一步若无部署授权，只能保持停在 offline-accepted；不得读取 live DB 或虚构 canary 指标。
3. 若用户另行授权部署，必须从 Stage 0 开始，bot-only，先冻结 numeric latency/error budget；任何 cross-scope、empty-pack、instruction/supersede/gate-reject 异常即停。
4. `provenance_gate_enabled=false` 不是普通 rollback；结构切片失败必须恢复 `98887a5` 旧 bot image，永不 recreate NapCat。
