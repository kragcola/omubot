# QZone Journal v0.4 — Selection Decision Trace + Publish-Worth Ranking

> 日期：2026-07-16
> 范围：QZone Journal v0.4.0 离线选择质量（闭集 reason、确定性 publish-worth 排名、日草稿预算、process-lifetime health counters、runtime selection metrics）。**无 schema migration**；不真实发布、不读凭据、不创建 validated profile、不触 NapCat。

## 旧 → 新

| 面 | v0.3.0 | v0.4.0 |
| --- | --- | --- |
| 版本 | `0.3.0` | `0.4.0` |
| Selector API | `select() → CandidateEvent \| None` | 保留 `select()`；新增 `evaluate() → SelectionDecision` + 闭集 `SELECTION_REASON_CODES` |
| Tick 选稿 | 按事件顺序、每通过门禁即 compose | 全量硬门禁 + dedupe 后，按 publish-worth 排名；每 tick 最多 `max_drafts_per_tick`（默认 1） |
| 日预算 | 仅 `max_posts_per_day`（发布额度） | 新增 `max_drafts_per_day`（按 `event_date` 的草稿占用预算，LLM 前检查） |
| 预算占用状态 | n/a | `pending_review` / `approved` / `dispatching` / `unknown` / `published` 占用；`rejected` / `failed` 不占用 |
| Health | counts + live_publish_gate | + `selection_decisions`（闭集 reason → int）+ `max_drafts_per_tick` / `max_drafts_per_day` |
| Runtime metrics | `qzone_draft_created` 等 | + `qzone_draft_rejected`、`qzone_selection_decision`（metadata 仅 reason + source） |
| SQLite schema | v4 review decisions | **无变更**（additive 纯运行态逻辑） |

## 闭集 reason 枚举（冻结）

```
reject_source_not_allowed
reject_privacy_not_public
reject_subject_not_allowed
reject_empty_identity
reject_salience_out_of_range
reject_below_threshold
reject_adapter_unparseable
reject_missing_subject_privacy
reject_duplicate_dedupe
reject_out_ranked
reject_day_draft_budget
reject_review_field
accept
```

## Publish-worth 公式（冻结）

- `importance = clamp(salience)`
- `recency = 1.0` when `event_date == today` else `0.0`
- `novelty = 1 - max Jaccard(summary tokens, recent draft source_summaries)`（有界 N）
- `relevance = Jaccard(summary, day_narrative)` when narrative nonempty else `0.5`
- `total = 0.50·importance + 0.20·recency + 0.20·novelty + 0.10·relevance`
- 并列：更高 total → 更高 salience → 字典序更小 `stable_id`

## Config 字段

| 字段 | 默认 | 范围 | restart |
| --- | --- | --- | --- |
| `max_drafts_per_tick` | 1 | 1..3 | yes |
| `max_drafts_per_day` | 1 | 1..3 | yes |

已写入：`config.default.json`、`config.schema.json`、`plugin.json` restart_required_fields、`PluginConfig`、health。

## 不变合同

- `manual_review=true`、`dry_run=true`、`allow_live_publish=false` 默认不变。
- `BUILTIN_WIRE_PROFILE.validated` 保持 `false`。
- factual / advanced 真人路径仍 fail-closed；dream 缺 subject/privacy → `reject_missing_subject_privacy`。
- 不真实 QZone HTTP、不创建 validated profile、不部署。

## 回滚

1. 代码回退到 v0.3.0 插件树即可（无 DB migration 需反向）。
2. 已写入的 runtime metric 行（`qzone_selection_decision` / `qzone_draft_rejected`）可保留；旧 stats 白名单不识别时忽略，新白名单会聚合。
3. health 新增键对前端无强制依赖（本轮无 admin SPA 改动）。

## 验证

见 `docs/tracking/qzone-journal-implementation-2026-07-15.md` Test Ledger v0.4 条目。

## v0.4 审查修正（2026-07-16，未部署 / 未 commit）

审查冻结 6 用例中曾 **2 GREEN / 4 RED**；外部 worker 曾误截断 untracked `store.py`，已由 Codex 从独立 review 会话完整恢复（权威：48045 bytes / 1406 行 / AST valid）。本轮仅在权威 `store.py` + `plugin.py` 上做小上下文编辑，不重写、不 `git restore`。

| 合同 | 修正 | 结果 |
| --- | --- | --- |
| store 日草稿预算原子性 | `JournalStore(max_drafts_per_day=None\|int)`；`DayDraftBudgetExceededError`；`enqueue` 在 BEGIN IMMEDIATE 内、same-dedupe 后、insert 前计占用；插件传入配置并 map 类型拒绝为 `reject_day_draft_budget`（无 false accept / draft_created） | GREEN |
| 同 tick 重复 dedupe | 入排名/LLM 前 `seen_dedupe_keys`；compose 前再查 persistent dedupe | GREEN：1 row / 1 LLM / 1 accept / 1 `reject_duplicate_dedupe` |
| 毒 review 字段 | `validate_review_fields_for_compose` 复用 store sanitizer；compose 前校验 known fields + provenance；毒候选不占 slot、不调 LLM | GREEN |
| metric source 闭集 | 仅 `event_replan` / `dream_reflection` / `schedule_generator` 可落库；其余 → `unknown`；作用于 `qzone_selection_decision` 与 `qzone_draft_rejected` | GREEN |

**验证证据（worker）**：6 frozen **6 passed**；七 QZone 文件 + `tests/test_humanization_metrics_persist.py` **153 passed**；scoped ruff clean；scoped pyright 0 errors；`BUILTIN_WIRE_PROFILE.validated is False`；`store.py` ≥1400 行且 AST valid；本 worker **未改** tests / selector / config / manifest / Admin SPA / ACTIVE。

## v0.4 same-tick hard-gate vs dedupe 排序修正（2026-07-16，未部署 / 未 commit）

**问题**：`on_tick` 在 `selector.evaluate` 之前检查/登记 `seen_dedupe_keys`。同一 tick 内先出现适配成功但硬门禁失败的候选（如 `subject_kind=factual`），会占用 dedupe key，压制后到的同 source/date/stable_id 的公开虚构孪生 → 误计 `reject_duplicate_dedupe`，零草稿/零 LLM。

**冻结流水线顺序**：adapter → hard gate → dedupe → day budget → rank → compose。

| 项 | 内容 |
| --- | --- |
| RED | `test_same_tick_hard_gate_reject_does_not_poison_dedupe_key_for_valid_twin`：`reject_duplicate_dedupe==1`（expected 0），`accept==0` |
| 修正 | `plugin.py`：仅 hard-gate **accept** 后做 same-tick/persistent dedupe 检查并 `seen_dedupe_keys.add`；硬门禁拒绝不占 key |
| 保留 | 同 tick 双合法孪生仍 1 accept + 1 `reject_duplicate_dedupe`；compose 前 persistent dedupe 仍在 |
| GREEN | 新回归 + 既有 same-tick duplicate **2 passed**；七 QZone + metrics **154 passed**；scoped ruff/pyright clean |

**范围**：仅 `plugin.py` + runtime 回归 + 本文档/tracker/maintenance-log。未改 store/selector/config/manifest/Admin SPA/ACTIVE；未部署、未 commit。
