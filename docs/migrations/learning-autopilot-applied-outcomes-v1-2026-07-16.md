# Learning Autopilot Applied Outcomes v1 (2026-07-16)

> 状态：implemented in code；**offline only / not deployed**.
> 范围：对齐 Style / Episode / Slang 三个非 KG autopilot reviewer 与 KnowledgeAIReviewer 的 **applied-outcome** 合同；batch/state 计数与 API 字段描述阈值后真实写入态，而非原始 LLM decision；completion/remaining 只统计可行动 unreviewed backlog。
> 非目标：KG store/service/reviewer 行为改写（已在 promotion-loop v1）、部署、live DB、NapCat、QZone、validated profiles。

## Root cause

1. **计数跟 LLM raw decision**：`StyleAIReviewer` / `EpisodeAIReviewer` / `SlangReviewerAdapter` 用 `verdict.decision` 直接累加 `state[approved|rejected|kept]` 与 batch 计数。低置信 `approved`/`rejected` 实际仍 keep，却被记成 promote/reject。
2. **batch 字段缺失或不诚实**：Style/Episode 未填充 `approved_in_batch` / `rejected_in_batch` / `kept_in_batch`；`completed` 与 empty cursor 可能在 sticky pending 仍在时宣称 done。
3. **Slang remaining 虚高/虚低**：`remaining` 走 `count_backlog_candidates`（所有 candidate），把已写 `ai_reviewed_at` 的 sticky kept 仍算作可行动 backlog；empty / 全已审 page 可 `completed=True, remaining=0` 但与真实 actionable 不一致。

## Old → New

| 面 | 旧 | 新 |
|----|----|----|
| `_apply_verdict` 返回值 | `None` | applied outcome：`approved` / `rejected` / `kept` |
| Style 写入 | 阈值后 `status` pending/approved/rejected | **不变**；计数只跟 applied |
| Episode 写入 | 阈值后 `enabled_for_prompt` / `disabled` / `candidate` | **不变**；applied approved↔enabled、rejected↔disabled、kept↔candidate |
| Slang 写入 | 阈值后 approve / mute / meta keep | **不变**；applied approved / rejected(muted) / kept |
| Batch / state counters | 跟 raw `verdict.decision` | 仅 **applied** 后状态 |
| `completed` | empty cursor 常直接 `True` | `completed == (remaining == 0)` |
| Empty cursor | 可能 `last_done` + completed 而 sticky 仍在 | 计真实 pending/candidate/actionable；reset pass cursor / inactive；**仅 drained 时** done |
| Slang `remaining` | `count_backlog_candidates` | `count_pending`：`candidate AND ai_reviewed_at IS NULL` |
| `llm_assess` task 类型 | `_TASK_MAP: dict[str, str]` → Pyright 报错 | `dict[str, LLMTask]` + `TYPE_CHECKING` import；**parser 语义不变** |

## Checklist (D3)

- [x] Style：`_apply_verdict` → applied；batch/state 用 applied；empty cursor 诚实 remaining
- [x] Episode：同上；episode_state 映射保持
- [x] Slang adapter：applied + `count_pending` remaining；empty/no-unreviewed 诚实
- [x] Shared parser 43 例保留；malformed conf → kept、不 promote
- [x] 低置信 valid approve/reject → applied kept 计数回归
- [x] 高置信 applied 计数回归
- [x] Style/Episode sticky empty-cursor 不假 completed
- [x] `llm_assess` production Pyright 清零（LLMTask 类型）
- [ ] 部署窗（仅 bot recreate；永不 NapCat）— **未做**
- [x] ACTIVE / memory tracker final counts 已由 Codex 回填

## Wiring

- 文件：`services/learning_autopilot/{style_reviewer,episode_reviewer,slang_adapter,llm_assess}.py`
- 测试：`tests/test_learning_autopilot_llm_assess.py`（共享 parser + applied-outcome）
- KG reviewer **不改**（对照合同来源）

## Rollback

1. 回退上述四个 reviewer/parser 文件与 focused 测试、本迁移文档、maintenance-log 对应条目。
2. 不自动改写已写过的 `meta_json` / slang `ai_reviewed_at` / episode_state。
3. 永不 touch NapCat / live DB bulk repair。

## Verification (actual, offline)

- Packet B focused：shared parser + applied-outcome / cursor / slang malformed + KG/Admin/Slang 组合 **116 passed**。
- Codex final focused：KG CAS/事务 + 四 reviewer + Admin/Application 组合 **177 passed**。
- QZone v0.7 保留：**236 passed**；`BUILTIN_WIRE_PROFILE.validated=false`。
- 全仓：**4442 passed, 17 skipped, 186 warnings**。
- Scoped Ruff clean；production Pyright（七个 KG/autopilot production files）**0 errors**；`git diff --check` clean。
- Grok final closure review：**0 Critical / 0 Important**；接受本离线切片。
