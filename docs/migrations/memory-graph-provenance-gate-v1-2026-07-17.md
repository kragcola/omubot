# Memory Graph Provenance Gate v1 (gpg_v1) Migration

> 状态：离线终验通过；独立 Grok review 与 post-fix review 均 0 Critical / 0 Important；未部署、未 commit、未触 live DB / Docker / NapCat / QZone。日期：2026-07-17。
> `validated=false`。

## 目标

在知识图谱写路径统一 **primary typed evidence** 合同，阻止空/空白/冲突/`graph_fact` 伪证据进入 pending/active；读路径轻隔离，使 peg 自然把 derived-only 图命中视为无证据。**不**改 RRF、hop、confidence、type caps、内容、peg 分层逻辑本身；**无** schema migration、**无** bulk repair。

## 根因（Codex 临时 DB 已确认）

| 输入 | 旧行为 | 后果 |
|------|--------|------|
| `{"type":"message","id":"   "}` | 可 active conf=0.9 | 读侧 strip 后 `evidence_refs=()`，peg 空证据 |
| `evidence={}` + conf≥0.60 | 可 pending | 日后 approve 抛 `ValueError` |
| supersede 无旧证据 | 伪造 `graph_fact:self` | peg 可能把 derived 当支持 |

## 旧行为

- store `_require_evidence` 仅检查 truthy `id|card_id|chunk_id`；空白字符串可通过。
- candidate 可存空 `{}`；promote 时再炸。
- supersede 无证据时 fallback 为 `graph_fact`/`self` 类派生引用。
- `_graph_evidence_refs` 会把 `graph_fact:*` 塞进 `ContextProvenance.evidence_refs`。

## 新行为

### 纯合同 `services/knowledge_graph/provenance.py`

- `GraphProvenanceError(code, detail?)` — 闭集 code，适合 `provenance_gate:<code>`。
- `normalize_graph_evidence` — strip + 规范化；拒绝 missing/whitespace id、bool/NaN/±Inf ID 标量、冲突 alias/type、非法 type token、`graph_fact` 作为 primary。
- **Primary 合同（v1）**：非空 type 即 primary，**除非**属于 derived deny-set；v1 deny-set 仅 `graph_fact`。`is_primary_evidence_type` / `is_primary_evidence_mapping` 为唯一判定；读路径与 supersede 复制共用，**无** allowlist。
- 规范形状：
  - `card_id` → `type=memory_card, id=…, card_id=…`
  - `chunk_id` → `type=doc_chunk, id=…, chunk_id=…`
  - `message_id` → `type=message, id=…, message_id=…`
  - **legacy 裸 id**（仅非空 `id`、无 type）→ `type=evidence, id=…`（诚实 unknown-type 溯源；规范化后为 primary；与 pre-gpg `evidence:<id>` 读语义 / reverse lookup 的 bare `evidence_id` 兼容）。空白 id 仍拒绝。
  - 显式 `type` + 通用 `id`（无 alias 字段）→ 仅 `type`/`id`；**不**合成 `card_id`/`chunk_id`/`message_id`（避免 FactGraphBridge 等 listener 被无意拓宽）。
  - 显式 type 亦可为 `fixture`、`observation`、`episode` 等；未来类型无需改 allowlist。
- 显式 type 保守 token：`^[a-z][a-z0-9_]*$`（不破坏既有 canonical 类型）。
- 保留 listener 辅助字段：`source` / `scope` / `scope_id` / **输入实际使用的** alias / `quote`。
- **永不**发明 message/card/chunk id；alias 字段仅在输入使用了对应 alias 时写出。
- `primary_evidence_from_rows` — supersede 复制用；无 primary 返回 `None`（无 `graph_fact:self`）。

### 写门禁（默认开）

- `KnowledgeGraphConfig.provenance_gate_enabled: bool = True`
- bootstrap `chat_runtime` 透传至 `KnowledgeGraphService` / store。
- 构造器均可显式传入（测试用）。
- **defense in depth**：`add_fact` / `add_candidate` / `promote_candidate` 均经 `_prepare_evidence`。
- 落库 `evidence_json` 与 `graph_evidence` 行为规范 type/id。
- `submit_fact_candidate`：规范化失败 → `None` + 闭集 code 日志（无 raw evidence/query/content）；无 pending/active 行。
- `approve_candidate`：legacy 无效 pending → store rollback 后 `GraphProvenanceError` → requeue pending + `review_note=provenance_gate:<code>` → `None`；无 partial fact/evidence；`CancelledError` 仍向上抛。
- `supersede_relationship`：无显式 evidence 时仅复制旧 primary；空/derived-only → refuse，旧 fact 保持 active。显式合法 evidence 仍可用。
- **gate=false（unsafe rollback）**：尽量复现 legacy truthy 接受；**禁止**当正常配置。

### 读时轻隔离

- `services/context/sources.py::_graph_evidence_refs` / `_graph_provenance_kind`：调用 provenance 中心谓词；跳过 derived（`graph_fact`）与空 type/id。
- `graph_provenance_kind` metadata：`primary` | `derived_only` | `none`。
- 底层 item.evidence / audit 可仍含 `graph_fact:*`；**不**进入 `ContextProvenance.evidence_refs`。
- peg_v1 / euc_v1 / TemporalTrace **保持既有逻辑**；derived-only 自然 evidence-empty。

## 配置

```toml
# BotConfig / knowledge_graph（默认）
[knowledge_graph]
provenance_gate_enabled = true
```

- 紧急回滚：`provenance_gate_enabled = false` 后仅 restart/recreate **bot**（不触 NapCat）。
- **无**运行时改配置文件；无 admin 热开关要求于 v1。
- 关闭后文档标明 **unsafe**：空/空白/`graph_fact-primary` 可再进 pending/active。

## 文件清单

| 路径 | 变更 |
|------|------|
| `services/knowledge_graph/provenance.py` | **新增** 纯 normalizer |
| `services/knowledge_graph/store.py` | gate 标志 + `_prepare_evidence` + promote 抛 `GraphProvenanceError` |
| `services/knowledge_graph/service.py` | submit/approve/supersede 接线 |
| `services/knowledge_graph/__init__.py` | 导出 |
| `services/context/sources.py` | 读隔离 + kind metadata |
| `kernel/config.py` | `KnowledgeGraphConfig` |
| `bootstrap/chat_runtime.py` | 透传 |
| `tests/test_graph_provenance_gate.py` | **新增** RED→GREEN 矩阵 |
| 本文档 | 迁移清单 |

## 旧 → 新（行为）

| 场景 | 旧 | 新（gate on） |
|------|----|---------------|
| whitespace message id promote | 可 active | 拒绝，无 fact/evidence |
| empty evidence conf≥0.60 | pending | 无 candidate |
| conflict id/card_id | 可能脏写 | typed error / None |
| bool / NaN / ±Inf ID | stringify 后成为伪锚点 | `invalid_id_scalar`；读侧 fail-closed |
| valid card/chunk/message/fixture | 不一致形状 | 规范 round-trip |
| legacy bare `{"id":"9001"}` | 空 type 写库 / reverse 靠 bare id | `type=evidence` primary；`evidence:9001` refs；reverse 仍靠 bare evidence_id |
| `type=doc_chunk` + id only | 可能无 chunk_id | 仍 type/id only；**不**合成 chunk_id；bridge 保持 no-op |
| legacy invalid approve | ValueError / partial | pending + `provenance_gate:*` |
| supersede 无 primary | graph_fact:self | refuse，旧 active |
| graph_fact in evidence_refs | 进入 peg 支持 | 过滤；kind=derived_only |

## NO-GO（v1）

- schema migration / live offline bulk repair / 伪造 backfill（C repair 仍延期）
- 改 peg tier 实现、RRF、hop selection、confidence、type caps、pack content
- 非必要 context plugin version bump
- Docker / NapCat / live DB / QZone / 凭据
- `BUILTIN_WIRE_PROFILE.validated=true`
- commit / push / deploy（实现会话）

## 同模式扫描（D1）

| 位点 | 结果 |
|------|------|
| `store.add_fact` | 经 `_prepare_evidence` |
| `store.add_candidate` | 经 `_prepare_evidence`（含 empty candidate 合同） |
| `store.promote_candidate` | 事务内 prepare；`GraphProvenanceError` rollback 后 re-raise |
| `service.submit_fact_candidate` | 写前 normalize；双层 catch |
| `service.approve_candidate` | catch → requeue pending |
| `service.supersede_relationship` | `primary_evidence_from_rows`；无 self fallback |
| `FactGraphBridge` / listeners | 收规范 dict（type + alias）；chunk_id 路径保持 |
| admin `knowledge.py` supersede | 走 service，无需旁路 |
| CardStore / Episode supersede | **无关**（非 KG evidence 合同） |
| `graph_fact:self` 全仓 | 仅 provenance 文档/拒绝路径提及；**无**制造 fallback 代码 |

## 回滚

1. **首选**：`knowledge_graph.provenance_gate_enabled=false` → restart/recreate **bot only**。
2. 代码回滚：移除/还原 `provenance.py` 接线与 config 字段。
3. **无** DB 迁移；已写入的规范 evidence 对 legacy 读兼容；legacy 脏 pending 仍可在 gate on 下 approve 时 requeue。

## 最终验收证据（2026-07-17）

- suite regression 修正后首次全仓：**4672 passed / 17 skipped / 186 warnings**。
- 独立 Grok adversarial review（新 endpoint 长会话）：**0 Critical / 0 Important，ACCEPT offline**；`grok-exit: 0`，未出现 524。标题生成仍可单次 503 后自动降级，不影响主会话。
- Codex post-review scalar hardening：20 个 `id|card_id|chunk_id|message_id × bool|NaN|±Inf` RED 全失败 → GREEN **20 passed**；读侧异常标量 refs 为 `()`。
- 相关组合：KG / bridge / Context / Episode / Application **152 passed**。
- 最终全仓：**4692 passed / 17 skipped / 186 warnings**。
- Ruff（touched）：All checks passed；Pyright（KG）：**0 errors**；`git diff --check` clean。
- Grok 窄范围 post-fix review：**0 Critical / 0 Important，ACCEPT post-fix**；focused **27 passed**；`grok-exit: 0`。
- 未 commit / push / deploy；未触 live DB / Docker / NapCat / QZone / 凭据。

## 残留风险

- 历史已 active 的空白/`graph_fact-primary` 事实 **不**自动清理；读侧 quarantine + peg 空证据兜底。
- gate=false 仅重新打开 unsafe **写路径**；读侧仍隔离 `graph_fact:*`，supersede 仍不恢复 `graph_fact:self`。这是安全回退，不是完整时光倒流。
- supersede 从 DB row 复制 primary 时只保留 type/id/quote，不重建 alias；当前 reverse lookup 只需 bare evidence_id，未来若为 supersede 增 listener，禁止假设 alias 存在。
- 显式 type 须通过保守 token `^[a-z][a-z0-9_]*$`；非法 token 在 normalize 时拒绝（不兼容宽泛 Unicode/带空格 type）。
- C 路径 bulk repair 仍延期。

## Acceptance correction（2026-07-17 primary deny-set）

- 根因：`is_primary_evidence_type` 曾为 allowlist，与读路径「非 graph_fact 即 primary」不一致 → `observation` normalize/refs 成功但 supersede-copy 返回 None。
- 修复：primary = 非空 type 且非 `DERIVED_EVIDENCE_TYPES`（v1 仅 `graph_fact`）；`primary_evidence_from_rows` 与 `_graph_evidence_refs` / kind 共用谓词。
- 测试：`test_observation_custom_typed_ref_unified_primary_contract` + deny-set 断言。

## Suite regression correction（2026-07-17 bare id + alias 合成）

- **A**：全量 8 失败中多条 typed-ref / episode 路径传 `evidence={"id":"9001"}`（无 type）。旧合同 `missing_type` 拒绝 → Episode↔Fact 断链。修正：非空裸 id → `type=evidence` primary；空白 id 仍 `whitespace_id`；不伪造 message/card/chunk。
- **B**：`normalize({"type":"doc_chunk","id":"fallback_id"})` 曾合成 `chunk_id`，使 FactGraphBridge 写出 `doc_supports_fact`，违背「须显式 chunk_id」合同。修正：仅当输入实际使用 alias 字段时写出 alias；`test_missing_chunk_id_is_noop` 保持 green。
- 测试：`test_normalize_bare_id_canonicalizes_to_evidence_primary`、`test_normalize_explicit_type_id_does_not_synthesize_alias`；既有 bridge / episode 反向查找回归。

## Post-review scalar correction（2026-07-17）

- 根因：ID 字段复用 `_strip_str(value or "")`；truthy bool 与非有限 float 会被 stringify 为 `True` / `nan` / `inf`，形成没有真实来源的伪证据锚点。
- 修复：写侧对四类 ID 字段统一拒绝 bool 与非有限 float，闭集 code 为 `invalid_id_scalar`；读侧 `_strip_id` 对同类值返回空锚点，保证 `evidence_id_from_mapping` / `primary_evidence_from_rows` / Context refs fail-closed 且不抛异常。
- 兼容：普通字符串、正整数、既有可通过的非零有限 float、裸 id→evidence、显式 alias、primary/derived 判定及 listener 行为不变。
- TDD：20 个参数化反例 RED→GREEN；最终全仓 **4692 passed**；独立 post-fix review **ACCEPT**。
