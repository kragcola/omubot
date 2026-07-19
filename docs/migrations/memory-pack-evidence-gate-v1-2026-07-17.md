# Memory Pack-Time Confidence/Evidence Gate v1 Migration

> 状态：离线代码验收通过；未部署、未 commit、未触 live DB / Docker / NapCat / QZone。日期：2026-07-17。

## 目标

在 `ContextService.build_prompt_context()` 的 **search 之后 / pack 之前** 增加 evidence-aware tiering，降低弱无证据记忆/多跳图事实进入主 prompt 的概率；**不**改 RRF/Card 权重、RetrievalGate、Query-Aware、graph hops、Episode、TemporalTrace 鉴权/schema，**不**新增 store 写入或 schema。

## 旧行为

- `search()` 完成 RRF + type caps + top_k 后，全部命中直接进入 `pack_context_hits()`。
- 无 pack 期 confidence/evidence 分层；post-RRF `hit.score` 仅表示融合秩。
- TemporalTrace 的 `active_memory_hits` 仅来自 **已打包** memory ids。
- 无 `pack_evidence_gate` 配置与 metrics。

## 新行为

- 唯一插入点：`build_prompt_context()` 在 `search()` 之后、`pack_context_hits()` 之前调用纯模块 `services/context/pack_evidence_gate.py`。
- Evidence-aware tiering（keep / demote / omit），**不是**全局 confidence hard floor；**永不**把 post-RRF `score` 当 confidence。
- 输出顺序：kept 原序，再 demoted 原序；omit 不出现。
- `memory_card`：
  - synthetic hint（`retriever=card_store_hint` 或 id 前缀 `memory_hint:`）始终 keep
  - 非 active 或 confidence 为 NaN/Inf → omit（fail-closed）
  - 信任空证据源 `{manual,user_config,migration,food_plugin}` 无 refs 仍 keep
  - 有 `source_message_id` / `evidence_refs` → keep
  - 非信任源、无 refs、soft conf 低于 `memory_soft_confidence` → **demote**
  - 其余有效卡 keep
- `doc_chunk`：无 confidence 合法；仅空正文 omit
- `graph_fact`：非 active / 非有限 conf omit；有 evidence keep；弱 conf + 空 evidence + `graph_hop>=1` → `gate_omit_graph_weak_uncorroborated`；其它空 evidence/低 conf **demote**
- `ContextPack.trace_seed_ids`：gate **前** 真实 memory ids（排除 hint）；`to_dict()` **不**暴露
- `ContextPlugin` TemporalTrace 优先 `pack.trace_seed_ids`，旧/fake pack 回退 packed memory ids
- empty-after-gate：`text=''`、`hits=[]`，不注入主 context block；TemporalTrace 仍可走 pre-gate seeds
- metrics：闭集 `version/enabled/identity/actions/reasons` 挂到 recent 行 `pack_evidence_gate`；无 query/content/title/message-id/card-id

## 配置

```json
{
  "pack_evidence_gate": {
    "enabled": true,
    "memory_soft_confidence": 0.45,
    "graph_soft_confidence": 0.60
  }
}
```

- 阈值 runtime/schema 夹紧到 `[0, 1]`
- 默认 offline 代码开启；`enabled=false` 为严格 identity
- 插件版本 `0.1.13`；`restart_required`

## 文件清单

| 旧 | 新 | 说明 |
|----|----|------|
| （无） | `services/context/pack_evidence_gate.py` | 纯 gate 模块 |
| `services/context/service.py` | 同左 | build_prompt_context 接线 + metrics |
| `services/context/types.py` | 同左 | `ContextPack.trace_seed_ids` additive |
| `plugins/context/plugin.py` | 同左 | 配置 + TemporalTrace seed 优先 |
| `plugins/context/config.default.json` | 同左 | 默认 policy |
| `plugins/context/config.schema.json` | 同左 | bounds |
| `plugins/context/plugin.json` | `0.1.13` | restart_required |
| （无） | `tests/test_context_pack_evidence_gate.py` | 单元 RED/GREEN |
| `tests/test_context_service.py` | 同左 | 集成 |
| `tests/test_context_plugin.py` | 同左 | 配置 + seed 优先 |

## 前沿依据（能力维，非官方 benchmark parity）

- LongMemEval-V2：compact evidence + evidence-use
- MemTrace：reachable evidence 使用失败点
- Mem0：scope/top-k/threshold 与记忆生命周期分离
- Graphiti/Zep：temporal invalidation + episodic provenance
- A-MEM：typed/evolving memory organization

**非对等声明**：本切片 **不** 声称 LongMemEval/LoCoMo/HippoRAG 等官方数据集或 leaderboard 对等；仅借鉴能力维度，合成 fixture 只证明合同维。

## NO-GO

- 全局 confidence/evidence hard floor
- 要求所有 Card 必须有 message ref
- 修改 RRF / Card score 权重、RetrievalGate mode、query plan 逻辑
- 新增 graph hop / LLM judge / store schema / observations join
- PPR / GraphRAG / MemGPT tools
- 触 QZone v0.8、部署、Docker、NapCat、live DB、凭据
- `BUILTIN_WIRE_PROFILE.validated=true`
- 下载官方 benchmark 数据集

## 回滚

1. 首选：`pack_evidence_gate.enabled=false` 后仅 restart/recreate **bot**（不触 NapCat）→ 严格 identity。
2. 代码回滚：移除 gate 模块接线、配置字段、版本回退 `0.1.12`。
3. 无数据库迁移，无数据回滚。

## 验收证据（实现会话 2026-07-17）

- focused implementation：`tests/test_context_pack_evidence_gate.py` + `test_context_service.py` + `test_context_plugin.py` + `test_context_eval.py` + `test_context_query_plan.py` + `test_retrieval.py` + `test_temporal_trace.py` + `test_context_rrf.py` → **248 passed**；Codex raw-mapping hardening gate suite → **27 passed**（总 focused 以不重复计数为 **249**）
- Ruff（touched scope）：All checks passed
- Pyright（`pack_evidence_gate.py` / `service.py` / `types.py` / `plugin.py`）：**0 errors**
- JSON parse：`config.default.json` / `config.schema.json` / `plugin.json` ok；plugin version **0.1.13**
- `git diff --check`（in-scope）：clean
- 实现包阶段未跑全量 pytest；随后 Codex 已完成全仓回归；未 commit / push / deploy；未触 live DB / Docker / NapCat / QZone / 凭据

## 独立审评

- 最新 Grok 独立 review：**0 Critical / 0 Important，ACCEPT**。
- review 发现的 dormant `policy_from_mapping` 宽松 bool coercion 已经 RED→GREEN：只有真实 bool 可关闭，非法 raw mapping fail-safe 保持 enabled；生产 Pydantic ingress 行为不变。
- Codex 全仓 pytest：**4618 passed, 17 skipped, 186 warnings**；focused 最终 **249 passed**。
