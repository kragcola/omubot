# QZone Journal v0.6 - Producer Candidate Contract

> 日期：2026-07-16
> 状态：历史合同；其中 Dream global / Schedule 的 `self/public` 映射已被
> `qzone-journal-authenticity-approval-scope-v0.8.2-2026-07-18.md` 取代，禁止恢复。
> 范围：StoryArc journal event typed DTO、Dream/Schedule/Replan producer-owned metadata、QZone adapter fail-closed 收口。无 SQLite migration、无真实 QZone HTTP、无凭据读取、无 validated profile、无 Docker/NapCat 操作。

## Old -> New

| 面 | v0.5.0 | v0.6.0 |
| --- | --- | --- |
| 元数据所有权 | QZone adapter 可按 source / arc scope 推断 `subject_kind`、`privacy`、`salience` | producer 在事件创建时显式提供三个字段；adapter 只验证和搬运，不再推断 |
| 事件 DTO | `last_events` 由各 producer 拼装松散 dict | `JournalEventRecord` 冻结并校验 date/source/summary/subject/privacy/salience/event_id，再以稳定键输出 dict |
| 缺失 subject/privacy | legacy raw event 可能被 adapter 补成可选候选 | fail-closed 为 `reject_missing_subject_privacy` |
| 缺失/坏 salience | adapter 可按 source 补默认值或经 `float()` 宽松转换 | 缺失、bool、非数值、NaN/Inf 均为 `reject_adapter_unparseable`；有限越界仍交 selector 返回 `reject_salience_out_of_range` |
| 稳定事件 ID | producer dict 无统一边界 | 可选 `event_id` 一旦提供必须匹配 `^[A-Za-z0-9][A-Za-z0-9._:-]{0,179}$` |
| 插件版本 | `0.5.0` | `0.6.0` |

## Historical v0.6 Producer Mapping

> 下表用于解释事故前行为，不是当前实现规范。`Dream global` 和 `Schedule`
> 的 `self/public` 被证明确实会把 synthetic fiction 包装成真实自我经历。

| Producer | `subject_kind` | `privacy` | `salience` | `event_id` |
| --- | --- | --- | --- | --- |
| Dream global reflection | `self` | `public` | `0.82` | `dream_reflection:<date>` |
| Dream group reflection | `self` | `unknown` | `0.82` | `dream_reflection:<date>` |
| Schedule generator | `self` | `public` | `0.35` | `schedule_generator:<date>` |
| Event replan on fiction arc | `fiction` | `public` | `0.95` | `event_replan:<date>` |
| Event replan on non-fiction arc | `fiction` | `unknown` | `0.95` | `event_replan:<date>` |

Dream 的公开性由绑定后的 scope 决定，LLM 返回中的 `privacy` / `journal_privacy` 不能自我授权。Event replan 只有 `arc.scope == "fiction"` 才能标为 public；其他 scope 即使文本看似安全也保持 unknown。

## Current v0.8.2 Mapping

| Producer | fiction arc | non-fiction arc |
| --- | --- | --- |
| Dream reflection | `fiction/public/0.82` | `self/unknown/0.82` |
| Schedule generator | `fiction/public/0.35` | `self/unknown/0.35` |
| Event replan | `fiction/public/0.95` | `fiction/unknown/0.95` |

Selector 额外拒绝任何 stale/forged
`schedule_generator|dream_reflection + subject_kind=self`；即使历史 JSON 仍保留
旧字段，也不能再进入 compose 或 live delivery。

## Frozen Contract

- producer 必须提供 `subject_kind`、`privacy`、`salience`；QZone adapter 不从 source、summary 或 `arc.scope` 发明这些字段。
- `JournalEventRecord` 的 source 闭集为 `event_replan / dream_reflection / schedule_generator`，subject 闭集为 `self / fiction`，privacy 闭集为 `public / private / unknown`。
- DTO 的 `salience` 必须是非 bool 的有限实数且位于 `[0, 1]`；adapter 只先关闭不可解析值，范围门禁继续归 selector 所有。
- legacy raw event 不得导致 tick 崩溃；不完整记录映射到闭集 reject reason，且不得进入 LLM compose。
- group Dream、non-fiction arc replan、private/unknown privacy 与 factual subject 继续在 LLM 之前被拒绝。
- 默认 `manual_review=true`、`dry_run=true`、`allow_live_publish=false` 不变。
- `BUILTIN_WIRE_PROFILE.validated` 必须保持 `false`。

## Verification

- QZone focused：**201 passed**。
- Dream/Schedule related：**130 passed**。
- producer candidate contract：**41 passed**。
- Ruff clean；Pyright **0 errors**。
- 全部验证均为离线测试；未发起真实 QZone HTTP，未读取凭据，未部署或操作 Docker/NapCat。

## Explicit Non-goals / NO-GO

- factual/social public projection：Part C payload 仍可能包含真实 user/group/evidence；没有 producer-side 公开化名 DTO 与对抗审计前不得接入。
- 真实 QZone HTTP、real_sanitized CGI fixture、独立 validated profile、用户授权单条 canary。
- 不通过 adapter 恢复 legacy 推断，也不把 `BUILTIN_WIRE_PROFILE.validated` 改为 `true`。

## Rollback

1. 回退 `JournalEventRecord` 与 Dream/Schedule/Replan producer 写出接线。
2. 回退 QZone adapter 的 producer-owned 校验和插件/manifest 版本至 `0.5.0`。
3. 回退 producer contract 测试与本文档；无数据库反向迁移。
4. 回滚前后都保持 `BUILTIN_WIRE_PROFILE.validated=false`，不得借回滚开启真实发布。
