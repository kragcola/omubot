# QZone Journal v0.8 - Draft Recompose + Immutable Revision History

> 日期：2026-07-16
> 状态：**implemented offline + state/safety remediation GREEN, not deployed, not committed.**
> 范围：additive SQLite migration v5；显式操作员 recompose；append-only revision lineage；Admin 详情抽屉版本历史 +「修订并重新入队」。
> NO-GO：真实 QZone HTTP、凭据读取、live DB 写入、Docker/NapCat、commit/deploy、`BUILTIN_WIRE_PROFILE.validated=true`、Admin `/publish` UI。

## Why

v0.7 审核流在「拒绝」后结构性死端：

1. 草稿正文与状态在 rejected 后不可改。
2. `enqueue()` / 自动 tick 仍按逻辑事件 dedupe 命中旧行，不会生成替换稿。
3. 操作员无法在保留审计链的前提下把同一逻辑事件重新入队待审。

v0.8 用 **新 draft 行 = 新 revision** 打破死端：旧行永远保留，新行独立 `draft_id` + revision-specific dedupe key，lineage 显式挂在 `revision_root_id`。

## Old -> New

| 面 | v0.7.0 | v0.8.0 |
| --- | --- | --- |
| Schema | migrations v1–v4（review provenance / decisions） | **v5**：`revision_root_id`、`revision`、`supersedes_draft_id` |
| 既有行 | 无 lineage | backfill：`revision=1`，`revision_root_id=draft_id`，`supersedes_draft_id=NULL` |
| 拒绝后修订 | 不可能（正文不可变 + dedupe 占位） | 操作员 `POST …/recompose` → 新 `pending_review` 行 |
| Dedupe | 逻辑事件 key 唯一占位 | **逻辑事件 key 仍抑制自动 tick**；每个 revision 另有 `rev:{root}:{n}:{supersedes}` 唯一 key |
| 源行 | N/A | recompose **永不 mutate** 源行 body/status/history |
| 后继 | N/A | 同一 `supersedes_draft_id` 至多一行（UNIQUE partial index + 事务 CAS） |
| 日草稿预算 | `rejected` 不占；`pending_review` 占 | **仅 lineage tip** 且 tip 状态为占用态才计：`NOT EXISTS` 后继 + occupied statuses；历史 immutable 行不占；同 lineage pending→pending 替换不占第二格；**tip rejected 后 occupied=0**；CAS 与预算同事务 |
| 版本号 | `0.7.0` | `0.8.0` |
| Admin | 审核队列 + 详情抽屉 | 抽屉内「版本历史」+ **仅 lineage tip 且** pending/rejected 显示「修订并重新入队」；成功后切换到新 `draft_id` |

## Schema (migration v5)

```text
ALTER TABLE qzone_journal_drafts ADD COLUMN revision_root_id TEXT;
ALTER TABLE qzone_journal_drafts ADD COLUMN revision INTEGER;
ALTER TABLE qzone_journal_drafts ADD COLUMN supersedes_draft_id TEXT;

-- backfill
UPDATE … SET revision_root_id = draft_id, revision = 1, supersedes_draft_id = NULL
WHERE revision_root_id IS NULL OR revision IS NULL OR revision < 1;

CREATE INDEX idx_qzone_drafts_revision_root ON qzone_journal_drafts(revision_root_id, revision);
CREATE UNIQUE INDEX idx_qzone_drafts_supersedes_unique
  ON qzone_journal_drafts(supersedes_draft_id) WHERE supersedes_draft_id IS NOT NULL;
```

`PRAGMA user_version` → **5**（MigrationRunner ledger 与 v1–v5 一致）。

## State matrix (recompose eligibility)

| 源状态 | recompose | 备注 |
| --- | --- | --- |
| `pending_review`（**lineage tip**） | 允许 | 新 revision 仍 `pending_review`；源行不变；同 lineage 不额外占预算 |
| `rejected`（**lineage tip**） | 允许 | 主路径：拒绝后修订再审 |
| 任意状态但 **非 tip**（已有后继） | **409** | 历史行 append-only/immutable；不引入 persisted `superseded` 状态 |
| `approved` | **409** | 不得绕过审核决策；preflight 在 LLM 前失败 |
| `dispatching` | **409** | 发布中 |
| `unknown` | **409** | 仅人工 confirm 路径 |
| `published` | **409** | 已外发 |
| `failed` | **409** | 失败态不走 recompose |

**Actionable tip only**：`approve` / `reject` / `create_revision` / `claim_for_publish` 仅接受 lineage tip（无 `supersedes_draft_id = current.draft_id` 的后继行）。幂等 replay（已是目标态）仅当该行仍是 tip 时允许。

新 revision 固定：

- `status = pending_review`
- `publish_date / external_post_id / last_error_code = NULL`
- **不**写入 review_decisions / manual_resolutions
- 继承：`event_date`、`source`、`stable_id`、`subject_kind`、`privacy`、`salience`、verified `source_summary`、既有 provenance（**无 caller override 参数**）
- `revision_root_id` = 源 root；`revision = source.revision + 1`；`supersedes_draft_id = source.draft_id`
- 必须有非空 verified `source_summary`（content 仅为 untrusted wording）
- factual body **必须等于** scrub 后的 inherited `source_summary`

## Factual / fiction recompose contract

| subject_kind | 正文来源 | operator_guidance | 旧正文 |
| --- | --- | --- | --- |
| `factual` | **等于** verified projected `source_summary`（闭集模板 by-construction） | scrub 后忽略于事实；不进 provenance | untrusted wording only（不用于 factual body） |
| `fiction` / `self` | LLM 重写措辞；失败回退 verified summary | scrub、bounded、仅措辞指导 | untrusted wording reference |

公开投影 / public_safety / provenance 校验合同与 v0.7 相同；不得 free-form 编辑 factual 以绕过 `public_projection`。

## API (Admin)

| Method | Path | 行为 |
| --- | --- | --- |
| `GET` | `/api/admin/qzone-journal/drafts/{id}/revisions` | lineage 升序；含 root id |
| `POST` | `/api/admin/qzone-journal/{id}/recompose` | body 可选 `{ "operator_guidance": "…" }`；成功返回新 draft JSON |

错误语义与既有审核路由对齐：`404` 未知 id、`409` 非法状态/已有后继/日预算、`422` 校验失败。

## Admin SPA

- 页面仍为 `QzoneJournalView` 审核队列；**无**新路由页。
- `DraftDetailDrawer`：`AppPanelSection`「版本历史」；**allowed status + lineage tip** 才显示「修订并重新入队」+ 可选修订指导。
- **Fail-closed tip gate**：`revisions=[]` ⇒ `isLineageTip=false`（正常加载 API 总会返回当前行；空历史不可行动）。
- 成功 recompose：emit typed `select` 携带新 `draft_id`；父组件拥有 `selectedDraftId` 并切换到新 tip；再 refresh 队列。
- detail / action 使用既有 request-generation 防迟到响应串写。
- **无** `/publish` 前端路径。
- Admin 列表 `total`/分页与 store `list`/`count`/`stats` 一致：默认 tip-only。

## Automatic tick after reject / revise

- 原逻辑事件 dedupe key 仍被源行（或任意 revision 行的逻辑身份）占用 → tick **不会**静默再生成同事件。
- 仅操作员 recompose 创建带 **revision-specific** dedupe key 的新行。
- 日预算按 **lineage tip 占用态** 计：tip rejected/failed 不占用；历史 immutable 状态不参与；同 lineage 替换 pending tip 仍计 1。
- `list` / `count` / `stats` / `list_recent_source_summaries` 默认 **tip-only**（Admin 队列 / health / novelty）；`include_superseded=True` 为物理行 escape；`list_revisions`/`get` 仍全历史。

## D2 cancellation / failure

- Plugin **preflight**（status / tip / non-empty `source_summary`）在 composer/LLM **前**失败 → `llm_calls=0`、无新行。
- LLM recompose 在 store 事务 **外** 执行；取消发生在 compose 阶段 → **无**新行、**无**预算占用、**无** audit 污染。
- `create_revision` 使用 `BEGIN IMMEDIATE` + `BaseException` rollback；插入失败不得留下半成品；事务内再次校验 tip/status/summary/factual body。

## Safety remediation notes (post-implementation)

Codex 复现 Important gaps 后的根因修复：

1. ~~预算按 lineage DISTINCT 计~~ → **纠偏**：预算仅计 **tip + occupied status**（`NOT EXISTS` 后继），禁止历史 occupied 行在 tip 已 rejected 后仍占日额度；允许 default `max_drafts_per_day=1` 下 reject tip 后再 recompose 到 r3。
2. 非 tip 行的 approve/reject/recompose/claim 在 store 边界拒绝。
3. factual `create_revision` 任意正文失败；无 override 参数。
4. plugin 信任边界：content ≠ verified summary；preflight 先于 LLM。
5. Admin 成功后选中新 tip；`canRecompose` tip-aware；**`revisions=[]` fail-closed**（`isLineageTip=false`）。
6. 移除测试专用 `mark_failed` 公共 API；failed 态 fixture 经 `_transition_from_dispatching`。
7. **运营队列 tip-only**：`list`/`count`/`stats` 默认排除非 tip；`include_superseded` 仅内部物理行；`list_recent_source_summaries` 每 lineage 一条 tip 摘要。

### Independent review I1 (c78ba9b3) — factual recompose vs max_chars

Independent review **c78ba9b3-85d1-4b20-b919-86262c113f30** pre-fix: **0 Critical / 1 Important / 4 Minor**. **I1** was the only blocker:

- Store accepts `source_summary` up to **500** and factual `create_revision` requires `body ==` scrubbed inherited summary.
- `JournalComposer.recompose(factual)` previously returned `safe_summary[:self._max_chars]` (default **280**), so a legal ~400-char verified summary truncated and store raised `ValueError: factual revision content must equal verified projected source_summary` (permanent 422 dead-end for boundary/legacy rows).

**Remediation (Codex decision, Grok TDD):** factual recompose returns the **full** scrubbed `verified_summary` without the generic LLM wording cap. Store equality / provenance unchanged; self/fiction compose/recompose still use `max_chars`. Existing closed templates stay short; this only recovers legal long legacy/boundary rows.

**Evidence (this session):** RED → GREEN composer unit + plugin E2E (~400-char factual); revisions **31 passed**; QZone + producer **267 passed**. **Does not claim** post-fix independent closure or full-repo pytest.

## Rollback

1. 代码：回退 `store.py` v5 / `create_revision` / `list_revisions`、`composer.recompose`、`plugin` recompose 路由与 version `0.8.0`、Admin API/types/drawer 修订 UI、`tests/test_qzone_journal_revisions.py`。
2. 版本钉：manifest / ownership tests → `0.7.0`；user_version 期望回退需与 v4 测试对齐（仅开发库）。
3. 数据：v5 列为 additive；开发库可删 `qzone_journal.db` 重建。**无**生产 live DB 操作本包不做。
4. 已产生的 revision 行在回退后成为多余列数据；若曾上线需另做运维计划（本包 **no-live-deploy**）。
5. 始终保持 `BUILTIN_WIRE_PROFILE.validated is False`。

## Explicit non-goals

- 不自动 recompose；不在 tick 内静默替换 rejected。
- 不提供 Admin 真实发布按钮。
- 不把 operator guidance 写入 provenance / health / 错误回显原毒值。
- 不截断/整文件替换 `store.py`。

## Verification note (implementation session)

实现会话负责 focused revisions + QZone 回归 + 静态/前端 build。**全仓 pytest / 独立 adversarial review 由 Codex 另跑**；本文档与 tracker 不得伪称 Codex 已完成最终全量验收。
