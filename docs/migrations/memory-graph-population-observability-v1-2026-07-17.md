# Memory Graph Population & Evidence-Quality Observability v1 (gpo_v1)

> 状态：离线终验通过；最终全仓 **4713 passed / 17 skipped / 186 warnings**；独立 Grok post-fix review **0 Critical / 0 Important，ACCEPT**。未部署、未 commit、未触 live DB / Docker / NapCat / QZone；`BUILTIN_WIRE_PROFILE.validated=false`。

## 目标

在 gpg_v1 已阻止新脏 evidence 写入之后，补齐知识图谱的**人口/证据质量可观测性**：让运维区分“没有抽取”“pending 堵塞”“gate 阻止”“active fact 无 primary evidence”，而不是只看候选/边总量。v1 只读，不做 repair、bulk backfill、ranking/hop/PPR 或 Episode 合并。

## 前沿与同类项目依据

- Zep / Graphiti：<https://export.arxiv.org/abs/2501.13956> — temporally-aware graph 动态整合对话/业务数据并保留历史关系。
- Graphiti OSS：<https://cdn.jsdelivr.net/gh/getzep/graphiti@main/README.md> — temporal context graph、hybrid retrieval、validity windows、episode provenance。
- HippoRAG：<https://export.arxiv.org/abs/2405.14831> — KG + PPR 用于长期记忆多跳；Omubot 在 facts populate/可观测性过门前继续 PPR NO-GO。
- Mem0：<https://export.arxiv.org/abs/2504.19413> — 动态抽取、整合、检索显著信息并维持多会话一致性。
- LongMemEval：<https://export.arxiv.org/abs/2410.10813> — 长期记忆拆为 indexing / retrieval / reading，并评估抽取、多会话、时间、更新、abstention。
- MemTrace：<https://export.arxiv.org/abs/2606.17328> — 最终准确率会掩盖知识点在年龄、问题类型和证据条件变化下的失败；支持按事实/证据质量观测。

本地只借鉴能力维度，不声称 LongMemEval / LoCoMo / HippoRAG / Zep benchmark parity。

## 根因

旧 `GET /api/admin/knowledge/graph/health` 由 Admin route 直接访问 `writer._db`，只返回：

- `candidate_24h` / `candidate_total`
- `facts_active_by_source` / `facts_active_24h`
- `edges_24h`

它不能回答 active fact 是否有 primary evidence，也不能看到 pending evidence 质量或 `provenance_gate:<code>` 分布。`graph_facts` 又没有持久化 `candidate_id`，所以 candidate 与 fact 的独立 24h 活动不能诚实称为 conversion rate。

## 新行为

### 纯分类 `services/knowledge_graph/observability.py`

- 单 evidence：`primary | derived | invalid | missing`。
- active fact 聚合：`primary | derived_only | invalid_only | none`；优先级 primary > derived > invalid > none。
- type metric 只允许：`memory_card | doc_chunk | message | evidence | fixture | observation | episode | graph_fact | other | empty`。
- gate code 只允许当前 gpg 闭集 + legacy `missing_type`；未知后缀归 `other`，不暴露任意 review_note 内容。
- 复用 `normalize_graph_evidence(..., allow_graph_fact=True)` 与 gpg primary/derived 语义；不发明 ID。

### Store / Service 所有权

- `KnowledgeGraphStore.health_snapshot()` 拥有所有 SQLite SELECT 与质量聚合。
- `KnowledgeGraphService.health_snapshot()` 是 Admin 的公开入口。
- Admin `graph_health` 不再直接访问 `._db`。
- snapshot 只执行 SELECT；重复调用除 `checked_at` / `since` 外稳定。

### Additive API

旧顶层字段全部保留。`knowledge_graph.observability_enabled=true` 时新增：

```json
{
  "observability": {
    "version": "gpo_v1",
    "enabled": true,
    "provenance_gate_enabled": true,
    "active_fact_support": {
      "primary": 0,
      "derived_only": 0,
      "invalid_only": 0,
      "none": 0
    },
    "active_evidence_rows": {
      "primary": 0,
      "derived": 0,
      "invalid": 0
    },
    "active_evidence_type_histogram": {
      "memory_card": 0,
      "doc_chunk": 0,
      "message": 0,
      "evidence": 0,
      "fixture": 0,
      "observation": 0,
      "episode": 0,
      "graph_fact": 0,
      "other": 0,
      "empty": 0
    },
    "pending_evidence_quality": {
      "primary": 0,
      "derived": 0,
      "invalid": 0,
      "missing": 0
    },
    "pending_gate_codes": {
      "missing_id": 0,
      "other": 0
    }
  }
}
```

实际 `pending_gate_codes` 始终返回完整闭集零值键；示例仅展示两个代表字段。

### Error / Cancel

- snapshot 异常：HTTP 200 `{"available":false,"error":"health_snapshot_failed:<ExceptionType>"}`。
- 响应与 warning 日志只记录异常类型；不记录 `str(exc)`、路径、ID、quote、evidence、query/content。
- `asyncio.CancelledError` 不被 `Exception` catch，继续向上抛。

## 配置与回滚

```toml
[knowledge_graph]
provenance_gate_enabled = true
observability_enabled = true
```

- `observability_enabled=false` 后 restart/recreate **bot only**：恢复 pre-gpo health 顶层 key shape，不执行 active evidence / pending evidence 质量扫描。
- 不改变 `provenance_gate_enabled`。
- 无 DB migration，无数据回滚。

## 旧 → 新

| 位点 | 旧 | 新 |
|------|----|----|
| Admin health DB owner | route 直接 `writer._db` | service public API → store SELECT owner |
| active fact 质量 | 不可见 | fact support + evidence row/type 闭集计数 |
| pending gate 原因 | 不可见 | pending quality + closed gate code histogram |
| 未知类型/密文后缀 | 可能成为动态标签风险 | `other` |
| snapshot 失败 | route/DB 行为不可区分 | type-only safe error token |
| candidate→fact conversion | 无关联却易误读 | 明确不提供；`candidate_24h` 仍只是近期候选当前状态 |

## 文件清单

| 路径 | 变更 |
|------|------|
| `services/knowledge_graph/observability.py` | 新增纯分类与闭集聚合 |
| `services/knowledge_graph/store.py` | `observability_enabled` + read-only `health_snapshot` |
| `services/knowledge_graph/service.py` | public `health_snapshot` + flag 透传 |
| `kernel/config.py` | `KnowledgeGraphConfig.observability_enabled=true` |
| `bootstrap/chat_runtime.py` | composition root 透传 |
| `admin/routes/api/knowledge.py` | route 改用 public service；type-only error |
| `tests/test_graph_population_observability.py` | 21 个 RED→GREEN / adversarial 回归 |
| 本文档 / tracker / maintenance | 合同、证据、回滚、残留 |

## TDD / Review 修正

- 初始纯分类 RED 为正常断言失败；store/config 缺口为 constructor/attribute RED，随后分层 GREEN。
- 独立 review 初判：**0 Critical / 3 Important**。
  - I1：route 把所有 snapshot 异常静默折叠成 `available=false`。
  - I2：事实/行计数只断言 `>=`；kill-switch 未锁住 evidence batch / pending SQL。
  - I3：admin-only 全量扫描性能残留。
- I1/I2 修正：type-only error/log；取消传播；精确 facts/rows/hist/pending counts；approved/rejected 排除；kill-switch never-await + SQLite trace。
- post-fix review：**0 Critical / 0 Important，ACCEPT**。I3 作为 v1 明示残留接受。

## 验收证据（冻结口径）

| 检查 | 结果 |
|------|------|
| 新 gpo 测试 | **21 passed** |
| Codex focused suite | **144 passed** |
| 全仓 pytest | **4713 passed / 17 skipped / 186 warnings** |
| Ruff | clean / All checks passed |
| Pyright | **0 errors / 0 warnings** |
| `git diff --check` | clean |
| 独立初审 | **0 Critical / 3 Important**（I1、I2 已修；I3 性能残留接受） |
| post-fix 独立 review | **0 Critical / 0 Important，ACCEPT post-fix** |

- 实现路径中可能另有实现方自测组合数字；**对外/文档冻结验收以本表 Codex 口径为准**。
- **未** commit / push / deploy；**未**触 live DB / Docker / NapCat / QZone / 凭据；`BUILTIN_WIRE_PROFILE.validated=false`。

## 残留风险

- **I3（已接受）**：`observability_enabled=true` 时，Admin-only health snapshot 对 active facts、evidence rows 与 pending candidates 做完整只读扫描。不在聊天热路径，但大部署可能增加 health 延迟或 SQLite 读竞争。
- 即时缓解：kill-switch `knowledge_graph.observability_enabled=false` 后仅 restart/recreate **bot**。
- v1 不凭空选 SQL 聚合 / TTL cache / sample 阈值；仅当真实规模与延迟证据证明需要时再评估后续切片。
- 这些计数描述 population/evidence **形状**，不证明答案已正确使用证据；euc / MemTrace 仍是独立层。

## 隐私与指标边界

- 响应与日志不得暴露：raw evidence ID、quote、JSON 正文、subject/object、scope/group、query/content、任意 `review_note` 原文。
- 指标仅为闭集计数与已知 gate code；未知 type/后缀归 `other`。
- **不得**声称或暴露 candidate→fact conversion rate。

## NO-GO

- CLI、自动 repair、bulk backfill、live DB mutation
- candidate→fact 虚假 conversion rate
- PPR / graph hop / RRF / type caps / peg / euc 语义修改
- EpisodeProvider 合并进 ContextHit / RRF
- QZone / wire profile / canary / `validated=true`
- 部署、commit、push
- **PPR 过门**：在**已部署**真实脱敏 population、evidence-quality 与 latency 证据存在前保持 NO-GO；禁止用本切片计数 alone 或合成 fixture 过门
