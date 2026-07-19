# Memory / Knowledge Autopilot Promotion Loop v1 (2026-07-16)

> 状态：implemented in code + **Codex acceptance correction (2026-07-16)**；**offline only / not deployed**.
> 范围：`KnowledgeAIReviewer` 经 `KnowledgeGraphService` 真正 promote；修复无效 `status='approved'`；共享表只注册一个 fact-domain reviewer（**仅** `ctx.knowledge_graph` 注入时）；legacy repair-on-next-run；严格 LLM verdict 校验；D2 cancel regression。
> 非目标：PPR / graph replay / GraphContextSource ranking / RRF / RetrievalGate / TemporalTrace / QZone / frontend / live DB 改写 / 部署 / 并发 service transaction 重构。

## Root cause

1. **Promotion 断环**：`KnowledgeAIReviewer._apply_verdict` 在高置信 approve 时直接
   `UPDATE extraction_candidates SET status='approved'`。`GraphStatus` 合法值仅为
   `active | pending | rejected | superseded`，**不含** `approved`。
2. **从未调用** `KnowledgeGraphService.approve_candidate`，因此不会：
   - 写入 `graph_facts`（active）
   - 复制 `graph_evidence`
   - 触发 fact listeners（如 doc_supports_fact 桥）
   - 把 candidate 置为 `active`
3. **人类审核路径正常**：admin `approve_candidate` 要求 `pending` 并 materialize；
   AI 路径写出的 `approved` 既不进 graph，也不再被 human approve 消费（pending-only）。
4. **双注册竞态**：`_get_autopilot_runner` 对同一 `extraction_candidates` 表注册
   `domain=fact` 与 `domain=graph_relation` 两个 reviewer。表**无 domain 列**，
   `run_all` 会并发扫同一批 `pending` 行，重复 review / 写状态。

生产侧曾观察到约 **56** 条 legacy `status='approved'` 候选（**仅聚合计数，未读/未改私有内容**）。
本 slice **不** 批量改 live 库；repair 发生在 **下一次 autopilot batch**。

## Old → New

| 面 | 旧 | 新 |
|----|----|----|
| AI approve | 写 `status='approved'`，无 fact | `approve_candidate(..., review_note=ai_json, allow_legacy_approved=True)` → `graph_facts.active` + evidence + listeners + candidate `active` |
| AI reject | 直写 SQL `rejected` | `reject_candidate(note=ai_json)` |
| AI kept / 低置信 | 可留下 `approved` 脏态 | `keep_candidate(note=...)` 仅 `pending` |
| Admin approve | `approve_candidate(id)` pending-only | 默认不变；`allow_legacy_approved=False` |
| Service 签名 | `approve_candidate(id)` | `approve_candidate(id, *, review_note=None, allow_legacy_approved=False)` + `keep_candidate` |
| Reviewer 依赖 | `Path` + 自开 aiosqlite | 注入 live `KnowledgeGraphService`；`db_path` 公共只读属性（不读 `_store._db_path`） |
| run-all 注册 | `fact` + `graph_relation` 双实例 / 曾 fallback 自建 service | **仅** `ctx.knowledge_graph` 存在时注册 **一个** fact reviewer；缺失则 fail-closed 不注册 |
| LLM verdict | `str(...).lower()` + `float(conf)` 可吞 bool/大小写 | decision **精确** `approved|rejected|kept`；confidence **精确** 非 bool int/float、有限、`[0,1]`；否则 kept |
| Batch counters | 跟 LLM `verdict.decision` / outcomes 列表 | 仅 **applied** `approved_n/rejected_n/kept_n`（无 unused outcomes 积累） |
| Legacy `approved` + 合法 ai_review | 永久卡死 | confidence 达标则无 LLM 直接 repair materialize |
| Legacy `approved` 畸形 | 永久卡死 / 宽 OR 断言 | fail-closed re-queue 后 **恰好 1 次** fresh LLM；合法 approve → 恰好 1 fact + active |

## Legacy repair-on-next-run

合法 repair 条件（**全部**满足才免 LLM）：

- `status == 'approved'`（历史脏态）
- `review_note` JSON 含 `ai_review.decision == "approved"`（精确字符串）
- `ai_review.confidence` 为 **有限非 bool 数值**，且 `>= auto_approve_min_confidence`

否则：`keep_candidate` 写回 `pending` + 说明 note，再走正常 LLM / 阈值路径。

**不**做 offline SQL bulk UPDATE；**不**在本 slice 触碰 live `storage/knowledge_graph.db`。

## Wiring

- `admin/routes/api/learning_pipeline.py` `_get_autopilot_runner`：
  - **仅**当 `ctx.knowledge_graph` 非空时 `register(KnowledgeAIReviewer(kg_service))`
  - **禁止** uninitialized fallback `KnowledgeGraphService(kg_db)`（第二连接 owner）
  - 缺失 service → 不注册 fact/graph_relation reviewer（fail closed）
  - **不再** `register(..., domain="graph_relation")` 扫共享表
- `KnowledgeGraphService.db_path: Path` 公共只读属性；reviewer 用 `self._graph.db_path`
- Consolidator 的 `graph_relation` **库存/列表** 仍可走既有 consolidator 管线；本 slice **不** promote 关系 inventory。

## Observability

- Autopilot batch 日志：`approved_in_batch` / `rejected_in_batch` / `kept_in_batch` 反映 applied 结果。
- 成功后 `extraction_candidates.status='approved'` 计数应趋向 0（对已跑过的 backlog）。
- `graph_facts` active 与 evidence 行可对照增长。
- Promotion 失败：candidate 保持/回到 `pending`，batch **不** 宣称 approved。

## Rollout

- **仅代码 + 测试 + 文档**；**未部署**、不 recreate bot/NapCat、不写 live 私库。
- 未来部署窗：仅 recreate **bot**；永不 recreate NapCat。
- 部署后第一次 autopilot run 会 repair 合法 legacy `approved` 并 re-review 畸形行。
- **meaningful real graph replay / PPR 仍 NO-GO**，直到 active facts 实际 populate 且有脱敏 replay 证据。

## Rollback

1. 回退 `services/knowledge_graph/service.py`（approve 签名 / keep_candidate）。
2. 回退 `services/learning_autopilot/knowledge_reviewer.py`。
3. 回退 `admin/routes/api/learning_pipeline.py` 注册逻辑。
4. 回退 `tests/test_knowledge_ai_reviewer.py` 与本文档。
5. 已 materialize 的 active facts 保留（正向数据）；若需硬回滚业务态，需另开运维窗按 fact_id 审拒，**不**自动删 evidence。

## D1 Same-pattern scan

| 位点 | 结果 |
|------|------|
| `KnowledgeAIReviewer` 直写 `status='approved'` | **本修**：改 service promote |
| `StyleAIReviewer` / `EpisodeAIReviewer` 写 `approved` | **有意不同**：style/episode 域 status 合同本身含 approved；**不** 套用 GraphStatus |
| `admin/routes/api/knowledge.py` human approve | 默认 pending-only 未改；kwargs 向后兼容 |
| Slang `status='approved'` | 独立 SlangStatus 合同，无关 |
| learning_pipeline consolidator `domain IN ('fact','graph_relation')` | inventory 查询仍可分 domain；**共享 extraction_candidates 表** 只由 fact reviewer 消费 |
| `AutopilotRunner.register` 按 domain 字典 | 单 fact 注册后 `graph_relation` 不再覆盖/并行扫同表 |

## D2 Cancellation / failure behavior

| 场景 | 期望 |
|------|------|
| `approve_candidate` 返回 `None` / 失败 | `approved_in_batch` **不** 增加；`keep_candidate` 写 pending + note；无 active fact |
| LLM 不可用 | `assess_candidate` → kept；pending + note |
| Batch 中途取消（asyncio cancel / wait_for） | **无** active fact；candidate 仍 valid `pending`；`approved`/`rejected` 计数不假涨；**下一批**正常 batch 可成功 materialize |
| 畸形 / 非法 LLM verdict | 永不 auto-approve/reject；pending + 可观察 kept note |
| 畸形 legacy 元数据 | 永不因脏 note 单独 materialize；必须 re-review 或合法 repair 字段 |

## Acceptance correction (Codex, 2026-07-16)

| 缺口 | 修复 |
|------|------|
| fallback 自建 `KnowledgeGraphService` | 删除；仅注入 service 注册 |
| `graph._store._db_path` 私访 | `KnowledgeGraphService.db_path` 公共属性 |
| LLM verdict 宽松 | `_normalize_llm_verdict` + `_parse_verdict` 不 case-fold / 不 float 强转脏 conf |
| malformed legacy 宽 OR | 严格：1 LLM call + approve → 1 fact + active |
| 缺 D2 cancel 回归 | `test_cancelled_llm_review_does_not_pollute_state` |
| unused `outcomes` 列表 | 删除；仅 applied batch counters |

## Acceptance evidence (offline)

### v1 初版

- RED：`tests/test_knowledge_ai_reviewer.py` 在实现前 8 failed（AttributeError / TypeError / 双注册 AssertionError）。
- GREEN：同文件 8 passed；组合回归见 maintenance-log。

### Codex correction

- RED（加强断言后、修实现前）：**13 failed / 10 passed**（无 `db_path`、fallback 仍注册 fact、非法 conf/decision 仍 promote）。
- GREEN focused：`tests/test_knowledge_ai_reviewer.py` **23 passed**.
- 组合：`test_knowledge_ai_reviewer` + `test_knowledge_graph` + `test_knowledge_graph_llm_extractor` + `test_admin_api_learning_pipeline` + `test_application_composition` + `test_application_build` + `test_application_runtime` → **91 passed**.
- Ruff：touched production + test **clean**.
- Pyright：最终 production KG/autopilot 七文件 **0 errors**；`llm_assess.py` 的 `LLMTask` 类型债已在 applied-outcome v1 一并清零。
- **Live**：56 条 legacy 仅聚合观测，**未修改**；facts populate 前 PPR/replay 仍 NO-GO。

## Final-review remediation A (Codex Important findings, 2026-07-16)

| 缺口 | 修复 |
|------|------|
| `reject_candidate` / `keep_candidate` 可 TOCTOU 或无条件覆盖 `active` | Store `transition_candidate_status` 单次 CAS；reject：`pending\|legacy approved → rejected`；keep：`pending\|legacy approved → pending`；**永不**经 service 把 `active`/`rejected` 拉回 pending/rejected |
| Promote 与 late reject/keep 竞态可留下 active fact + 被 revert 的 candidate | Service 仅委托 CAS；promote 仍走原子 `promote_candidate`；回归：reject/keep-after-active、approve vs reject、approve vs keep、re-approve 无重复 fact |
| 空 cursor 页把 `remaining=0` / `completed=True` 且写 `last_done`，即便 sticky kept 仍在 backlog | 空页 `_count_backlog`；`remaining=真实计数`；`completed` 仅当 remaining=0；`last_done` 仅真正 drained；`active=False` + `last_id=""` 以便后续显式 run 重扫 |
| `domain=` 任意字符串静默写入 state key | 仅接受 `fact` / legacy `graph_relation`（均 canonical 为 `fact`）；其它 `ValueError`；pipeline 仍单 fact reviewer |

### Acceptance evidence (remediation A, offline)

- New / focused：reject-after-active、keep-after-active、concurrent approve↔reject、approve↔keep、re-approve no-dup；cursor 3-pending batch_size=2 kept；domain ValueError。
- Codex focused：KG CAS/事务/cursor + 四 reviewer + Admin/Application 组合 **177 passed**。
- QZone v0.7 保留 **236 passed**；全仓 **4442 passed, 17 skipped, 186 warnings**。
- Scoped Ruff clean；production Pyright **0 errors**；`git diff --check` clean。
- Grok closure review：**0 Critical / 0 Important**，建议接受。
- **未**部署、**未** commit、**未**触 live DB / NapCat / QZone。
