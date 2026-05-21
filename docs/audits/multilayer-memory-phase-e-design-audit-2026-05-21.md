# Phase E Graph Edge Double-Write — 设计前置审计（2026-05-21）

> 配套：[multilayer-memory-learning-report-2026-05-17.md § 5 Phase E](multilayer-memory-learning-report-2026-05-17.md) / [pending-and-observation.md § 2](../pending-and-observation.md)
> 目的：把报告 § 5 Phase E 列出的 5 条跨层 edge type 中**剩余 4 条**（`episode_supports_profile` 已在 D.5 落地）的写入路径设计清楚，再开 E.1 ~ E.4 实现。
> 状态：审计稿，**未落地任何代码**；下一步据本审计敲定 E.1 ~ E.4 子任务。

---

## 0. 一句话结论

Phase D.5 已建立**「source-of-truth event → bridge → graph edge」**模板（listener-pattern 或 hook-pattern）。剩余 4 条 edge type 的 source-of-truth 事件全部已存在、可挂；E.1 ~ E.4 是机械化复用，每条独立可验、可回滚。本 Phase 不需要新建任何 LLM task，不需要 schema migration（A.5 已经在 `GraphEdgeType` Literal 里枚举完毕）。

---

## 1. 现状盘点

### 1.1 已就绪（D.5 / A.5 留下的基础设施）

| 组件 | 文件 | 状态 |
| --- | --- | --- |
| `graph_nodes` / `graph_edges` schema | [services/knowledge_graph/store.py](../../services/knowledge_graph/store.py) | ✅ A.5 |
| `GraphEdgeType` Literal 含 5 种 edge | [services/knowledge_graph/types.py:14-18](../../services/knowledge_graph/types.py#L14-L18) | ✅ |
| `GraphNodeType` Literal 含 7 种 node | [services/knowledge_graph/types.py:10-13](../../services/knowledge_graph/types.py#L10-L13) | ✅ 含 `term / style_expression / episode / fact / user / group / document_chunk` 全套 |
| `ensure_graph_node` / `write_node` / `write_edge` / `set_edge_status` / `find_edge` / `get_node_by_source` | [services/knowledge_graph/dual_write.py](../../services/knowledge_graph/dual_write.py) + [graph_writer.py](../../services/knowledge_graph/graph_writer.py) | ✅ D.5 加全 |
| listener-pattern + bridge-pattern 模板 | [services/episodic/store.py:261](../../services/episodic/store.py#L261) `add_transition_listener` + [services/episodic/graph_bridge.py](../../services/episodic/graph_bridge.py) | ✅ D.5 |
| 优雅降级（graph 写挂 ≠ source-of-truth 回滚） | D.5 `_fire_transition_listeners` try/except | ✅ |

### 1.2 未就绪（E.1 ~ E.4 要补的写入路径）

| Edge Type | 源 store 事件 | 当前 caller | E.x |
| --- | --- | --- | --- |
| `term_used_in_group` | `SlangStore.record_hit(term_id, group_id, ...)` | 0（无 graph 写入） | **E.1** |
| `style_applies_to_situation` | `StyleStore.set_status(expression_id, 'approved')` （或 `update_expression` 的 status 变更） | 0（无 graph 写入） | **E.2** |
| `user_corrected_bot_about` | `StyleStore.record_feedback(rating='negative', target_type, target_id, group_id, user_id)` | 0（无 graph 写入） | **E.3** |
| `doc_supports_fact` | `KnowledgeGraphStore.add_fact(... evidence={'type':'doc_chunk', 'chunk_id':...})` 或 `KnowledgeGraphService.approve_candidate(...)` | 0（无 graph 写入） | **E.4** |

> 4 条 edge 当前都是 **0 caller**，仅在 `GraphEdgeType` Literal 中作为字符串声明存在；A.5 提前落 schema 时**没有**铺写入路径，全留给 Phase E（D.5 已先做了 `episode_supports_profile`）。

---

## 2. 报告原文 § 5 Phase E 目标对照

> 引用 [multilayer-memory-learning-report-2026-05-17.md:469-482](multilayer-memory-learning-report-2026-05-17.md#L469-L482)：

### 报告硬要求

- **graph fact 来源扩展到 slang/style/evidence/episode** — ✅ A.5 已落 schema，D.5 已落 episode；E.1 ~ E.4 完成 slang/style/evidence
- **新增边类型 5 种** — ✅ Literal 已声明；`episode_supports_profile` ✅ D.5 落地；剩余 4 种由 E.1 ~ E.4 接通写入路径
- **检索时图谱可补充相关实体、群、用户、事件链** — ⏳ 写入路径完成后，**召回侧不在本 Phase 范围**（属于 Phase E 后续 / 未来 ContextProvider 改造），但 admin `/api/admin/knowledge/graph` 已能浏览（`list_edges` / `list_nodes`）

### 报告硬前置（来自 § 5 Phase E + § 7.4）

| 前置 | 状态 | 证据 |
| --- | --- | --- |
| A.5 graph schema + 首批 edge 类型 | ✅ | 2026-05-08 落地，`graph_nodes` / `graph_edges` 表 + `GraphEdgeType` 5 种 |
| Phase D 落地（含 edge 写入路径之首） | ✅ | 2026-05-21 D.1 ~ D.5 全部 ✅，episode → `episode_supports_profile` 写入路径已通 |
| BlockTraceBus | ✅ | Phase B 已落地 |
| **观察期** | **❌ 报告未要求** | § 5 Phase E + § 7.4 全文无观察期表述 |

> **结论**：Phase E **可立即起**，不需要等 Phase D 累积观察期。Phase D 24h 观察期是「episode 召回路径生效后多久才有数据」的工程直觉，与图谱写入路径无关。

---

## 3. 推荐子阶段拆分（E.1 ~ E.4）

每个子阶段独立可验、可回滚，按风险递增 + 流量频次递增排列。

### E.1 — `term_used_in_group` 写入路径：slang term 命中 → graph

**改动文件**：

- [services/slang/store.py](../../services/slang/store.py) — `SlangStore` 加 `add_hit_listener(listener)` + `_fire_hit_listeners`，仿 D.5 `add_transition_listener` 模式；在 `record_hit()` 末尾（`record_observation` 之后）触发监听
- [services/slang/graph_bridge.py](../../services/slang/graph_bridge.py)（新建）— `SlangGraphBridge`：
  - `attach(store)` 把 `_on_hit` 绑到 `store.add_hit_listener`
  - `_on_hit(term_id, group_id, ...)` → upsert term node（`source_table='slang_terms'`, `node_type='term'`）+ group node（`source_table='groups'`, `node_type='group'`） → upsert `term_used_in_group` edge；evidence_refs=(term_id,)；properties={'usage_count': term.usage_count, 'last_seen_at': now}
  - 边状态固定 `active`（不需要 disable 路径——term 被 mute/expire 时另外处理，本 Phase 不动）
- [plugins/chat/plugin.py](../../plugins/chat/plugin.py) — 仿 D.5 把 bridge attach 到 `ctx.slang_store`，try/except 优雅降级
- [kernel/types.py](../../kernel/types.py) `PluginContext` — 新增 `slang_graph_bridge: Any = None`

**Edge 形态**：

- `edge_type='term_used_in_group'`
- `from_node`: term node `(source_table='slang_terms', source_id=term_id, node_type='term')`
- `to_node`: group node `(source_table='groups', source_id=group_id, node_type='group')`
- `confidence`: `term.confidence`（term-level confidence）
- `evidence_refs=(term_id,)`
- `properties={'usage_count': N, 'last_seen_at': ISO}`

**与 normalizer 的关系**：normalizer 写的是「term 文本归一化」域，与 graph edge「term-group 命中关系」不重叠。两者都通过 SlangStore 不同事件触发，无 schema 冲突。

**频次预估**：每条群消息平均匹配 0.3–1 个已知 term（slang 命中本就稀疏），日均增量约几百条 edge。upsert 语义保证同 (term, group) 不会重复落行。

**验收**：

- 单元（[tests/test_slang_graph_bridge.py](../../tests/test_slang_graph_bridge.py)）：
  - `test_record_hit_writes_term_used_in_group_edge`
  - `test_repeated_hits_upsert_same_edge`（usage_count properties 更新）
  - `test_listener_exception_is_caught`
  - `test_graph_write_failure_does_not_block_record_hit`
  - `test_cancel_path_leaves_clean_state`（D2）
- 启动后 grep 一条已知 term 命中日志，admin `/api/admin/knowledge/graph/edges?edge_type=term_used_in_group` 应见到对应 edge

**回滚**：撤掉 attach 调用 + 删 bridge 文件；schema 不动，graph_edges 表清理可选。

---

### E.2 — `style_applies_to_situation` 写入路径：style 表达 approved → graph

**改动文件**：

- [services/style/store.py](../../services/style/store.py) — `StyleStore` 加 `add_status_listener(listener)` + `_fire_status_listeners`；在 `update_expression` 内（status 字段实际变更时）触发；listener 签名 `async (expression, prev_status, new_status, actor) -> None`
- [services/style/graph_bridge.py](../../services/style/graph_bridge.py)（新建）— `StyleGraphBridge`：
  - `_on_status` → 仅在 `new_status == 'approved'` 时 upsert：style_expression node（`source_table='style_expressions'`, `node_type='style_expression'`，label=`expression.style[:40]`）+ situation node（`source_table='style_situations'`, `node_type='style_situation'`，source_id=`hash(expression.situation)` 或 normalize 后的 situation 文本）
  - upsert `style_applies_to_situation` edge；evidence_refs=(expression_id,)；properties={'persona_fit', 'mood_fit'}
  - `new_status == 'muted'` 或 `'rejected'` → `set_edge_status('disabled')`
- [plugins/chat/plugin.py](../../plugins/chat/plugin.py) — attach
- [kernel/types.py](../../kernel/types.py) — `style_graph_bridge: Any = None`

**关于 `style_situation` 节点类型**：当前 `GraphNodeType` 没有 `style_situation`。两条解：

- **方案 A（推荐）**：直接复用现有 `node_type='fact'` 把 situation 文本当一个轻量「场景事实」节点，`source_table='style_situations'`（仅作为 dedup key，无对应 SQL 表）；不动 Literal
- 方案 B：扩 `GraphNodeType` 加 `style_situation`；改动面更大

**风险点**：同一 situation 文本在不同 expression 中可能有微差异（`_clean_text` max_len=160 已 normalize）。E.2 把 situation 文本完整作为 source_id 用 SHA1 摘要降一阶，避免长文本作主键；相同语义、不同表述会产生不同 node，这是 expected——后续 Phase F declarative facts 才解决「situation 聚类」。

**频次预估**：style approve 是低频事件（admin 手动 + 偶发），每周几十次。

**验收**：

- 单元：approve / mute / reject 三态翻转 + 重复 approve 不重写 + cancel-path
- 启动后人工 admin approve 一条 expression，admin graph 应见到 edge

**回滚**：同 E.1。

---

### E.3 — `user_corrected_bot_about` 写入路径：style_feedback negative → graph

**改动文件**：

- [services/style/store.py](../../services/style/store.py) — `StyleStore` 加 `add_feedback_listener(listener)` + `_fire_feedback_listeners`；在 `record_feedback()` 末尾触发；listener 签名 `async (feedback, target_type, target_id, group_id, user_id, rating) -> None`
- [services/style/feedback_graph_bridge.py](../../services/style/feedback_graph_bridge.py)（新建，与 E.2 bridge 区分）— `StyleFeedbackGraphBridge`：
  - `_on_feedback` 仅在 `rating == 'negative'` 时写：user node `(source_table='users', source_id=user_id, node_type='user')` + target node（按 target_type 分发：`'expression'` → style_expression node；`'reply'` → 取消（因为 reply 没有 source_id 可查），等价 noop；`'persona'` → 直接挂 group node 当 anchor）
  - upsert `user_corrected_bot_about` edge；evidence_refs=(feedback_id,)；properties={'target_type', 'feedback_at', 'note'}
  - 不需要 disable/反转——feedback 是单向事件
- [plugins/chat/plugin.py](../../plugins/chat/plugin.py) — attach
- [kernel/types.py](../../kernel/types.py) — `style_feedback_graph_bridge: Any = None`

**Edge 形态**：

- `from_node`: user node
- `to_node`: 取决于 target_type
- `confidence=1.0`（人工 feedback 视为 ground truth）
- `evidence_refs=(feedback_id,)`

**频次预估**：admin 手动 feedback 每周几条到几十条，是真正稀疏信号。

**验收**：

- 单元：positive / neutral / negative + 不同 target_type + cancel-path
- 启动后 admin POST `/api/admin/style/expressions/{id}/feedback?rating=negative`，graph 应见到 edge

**回滚**：同 E.1。

---

### E.4 — `doc_supports_fact` 写入路径：fact 落库时双写到 chunk

**改动文件**：

- [services/knowledge_graph/service.py](../../services/knowledge_graph/service.py) — `KnowledgeGraphService` 加 `add_fact_listener(listener)` + `_fire_fact_listeners`；在 `submit_fact_candidate` 写入 active 路径 + `approve_candidate` 路径触发；listener 签名 `async (fact, evidence) -> None`
- [services/knowledge_graph/fact_graph_bridge.py](../../services/knowledge_graph/fact_graph_bridge.py)（新建）— `FactGraphBridge`：
  - `_on_fact` 仅在 `evidence.get('type') == 'doc_chunk'` 且 `evidence.get('chunk_id')` 非空时写
  - upsert fact node `(source_table='graph_facts', source_id=fact_id, node_type='fact')` + chunk node `(source_table='knowledge_chunks', source_id=chunk_id, node_type='document_chunk')`
  - upsert `doc_supports_fact` edge；from=fact, to=chunk；evidence_refs=(fact_id,)；properties={'quote': evidence.get('quote'), 'fact_confidence': fact.confidence}
- [plugins/chat/plugin.py](../../plugins/chat/plugin.py) 或 [plugins/knowledge/plugin.py](../../plugins/knowledge/plugin.py) — attach
- [kernel/types.py](../../kernel/types.py) — `fact_graph_bridge: Any = None`

**关于事件触发位**：`approve_candidate` 调 `add_fact`；高 confidence 直接走 `add_fact`；都汇集到 `_store.add_fact`，但 store 层不应感知 graph。所以 service 层 `submit_fact_candidate` 中所有产出 `GraphFact` 实例的分支都需触发监听。

**频次预估**：fact extraction 取决于 KnowledgeGraphExtractor 跑频；当前主要从 admin 显式触发 + context_hits 提取，日均几十到几百条 fact，doc_supports_fact 边数与之同阶。

**验收**：

- 单元：active 写入 + candidate→approved 翻转 + 非 doc_chunk evidence skip + cancel-path
- 启动后 admin POST `/api/admin/knowledge/extract` 跑一轮，graph 应见到 edge

**回滚**：同 E.1。

---

## 4. 跨子阶段共性约束

每个 E.x bridge 都遵循 D.5 同款契约：

1. **listener 失败不影响 source-of-truth**：bridge 内部 try/except，失败仅 WARN log
2. **listener fan-out 用 store 内部 `_fire_*_listeners` 包裹**：每个 listener 独立 try/except，单个 listener 挂掉不影响其他 listener
3. **upsert 语义**：`(source_table, source_id)` 决定 node、`(edge_type, from_node_id, to_node_id)` 决定 edge；重复事件不创建新行
4. **D2 cancel-path 测试**：每个 bridge 必须有 `pytest.raises(asyncio.TimeoutError) + asyncio.wait_for(coro, timeout=0.0)` 测试，断言 source store 状态干净
5. **bridge 不写 GraphFact 表**（fact 表是 KnowledgeGraphService 自己的事），只写 `graph_nodes` / `graph_edges`
6. **bridge 落点固定**：`services/{源 store}/[*_]graph_bridge.py`，与 `services/episodic/graph_bridge.py` 同层级 + 命名风格

---

## 5. 验收前置自检（Phase E 整体完成时勾）

- [ ] 报告 § 5 Phase E 「graph fact 来源扩展到 slang/style/evidence/episode」全部接通（E.1 ~ E.4 完成 + D.5 已完成 episode）
- [ ] 报告 § 5 Phase E 「新增边类型 5 种」全部有 caller（E.1 ~ E.4 + D.5）
- [ ] D1 同模式扫描：grep `term_used_in_group` / `style_applies_to_situation` / `user_corrected_bot_about` / `doc_supports_fact` 在 caller 端全部 ≥ 1 处
- [ ] D2 cancel-path 回归测试：4 个 bridge 全部有 `wait_for(timeout=0.0)` 测试
- [ ] D4 完成声明含证据：pytest 全绿 + ruff/pyright on E scope 干净 + admin `/api/admin/knowledge/graph/stats` 显示 5 种 edge type 都 ≥ 1 行（人工触发后）
- [ ] 多层报告 § 5 Phase E 状态字段同步：从 🟡（部分提前）改为 ✅（写入路径完成）
- [ ] [pending-and-observation.md § 2](../pending-and-observation.md) 表格刷新

---

## 6. 启动建议

1. **E.1 起手**：slang 命中是高频信号，bridge 跑通后能很快收到第一条 edge，验证模板
2. **E.2 + E.3 并行可拆**：都在 StyleStore 上挂 listener，但触发事件不同（status 变更 vs feedback record），可以拆两次 commit
3. **E.4 最后**：依赖 KnowledgeGraphService 改动，且 fact 写入是相对低频但每条 evidence shape 多样，单测要覆盖 doc_chunk / memory_card / 无 evidence 三档
4. **不需要 admin 前端改动**：admin 已有 `/api/admin/knowledge/graph` 路由可看节点/边，本 Phase 不引入新 UI

时间预估：

- E.1 term_used_in_group ~ 3h
- E.2 style_applies_to_situation ~ 3h
- E.3 user_corrected_bot_about ~ 2h
- E.4 doc_supports_fact ~ 4h（service 层有 candidate 状态机分叉）

---

## 7. 引用

- [docs/audits/multilayer-memory-learning-report-2026-05-17.md § 5 Phase E](multilayer-memory-learning-report-2026-05-17.md) — 5 种 edge 类型清单
- [docs/audits/multilayer-memory-phase-d-design-audit-2026-05-21.md](multilayer-memory-phase-d-design-audit-2026-05-21.md) — D.5 listener 模板来源
- [services/episodic/store.py:261](../../services/episodic/store.py#L261) — `add_transition_listener` 模板
- [services/episodic/graph_bridge.py](../../services/episodic/graph_bridge.py) — `EpisodeGraphBridge` 模板
- [services/knowledge_graph/types.py:14-18](../../services/knowledge_graph/types.py#L14-L18) — `GraphEdgeType` Literal 5 种
- [services/knowledge_graph/dual_write.py](../../services/knowledge_graph/dual_write.py) — `ensure_graph_node` 工具
- [services/knowledge_graph/graph_writer.py](../../services/knowledge_graph/graph_writer.py) — `write_edge` / `set_edge_status` / `find_edge` 工具
- [docs/agent-discipline.md](../agent-discipline.md) — D1 / D2 / D4 / D6 / D7 条款
