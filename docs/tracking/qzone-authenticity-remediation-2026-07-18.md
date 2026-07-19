# QZone authenticity remediation

> 状态：complete · 2026-07-18 · v0.8.2/schema v6 已 bot-only 部署，live 继续锁定

## Objective

1. Schedule / Dream 合成事件不得再成为 `self/public` 候选。
2. fiction 内容允许保留，但成稿必须有确定性的虚构标识，不能伪装为真实经历。
3. factual 内容只允许闭模板公开投影原样进入正文，LLM 不得二次扩写。
4. dry-run 审批不得用于 live delivery/capture。
5. Dream 合成反思不得继续写入通用 active/global memory 或反馈给未来 schedule。

## Boundaries

- 不删除、不隐藏、不重发现有远端 QZone 日志。
- 不读取/输出 cookie、UIN、`p_skey`、token、attestation secret。
- built-in wire profile 永远保持 `validated=false`。
- NapCat 不 restart/recreate；真实发布保持锁定。
- 保留现有 dirty worktree，只触及本修复直接相关文件。

## Acceptance

- fiction arc 的 Schedule / Dream producer 输出 `subject_kind=fiction`；非-fiction arc 的 synthetic 输出 `privacy=unknown`。
- selector 对任何 stale/forged `schedule_generator|dream_reflection + self` fail-closed。
- fiction compose/recompose 正文确定性以 `虚构故事里，`开头；factual compose 不调用 LLM 且正文等于 projected summary。
- provenance 暴露 `arc_scope`，Admin review 能看见。
- DB schema 新增 approval scope；旧记录默认 `dry_run`；live delivery/capture 只接受 `live` approval。
- Dream life reflection 仍可更新 fiction story arc，但不写通用 memory cards；Schedule 不再读取历史 dream_reflection cards。
- QZone/Dream/Schedule 专项回归、ruff/pyright 和容器 fail-closed 检查通过。

## Assumptions

- fiction 日志是既有产品能力，不整体禁用；真实性通过 subject/provenance + deterministic fiction framing 保证。
- 当前 producer 没有 grounded self-observation carrier；因此 Schedule/Dream 的 `self` 一律拒绝。
- 现有远端日志处置需要单独授权，不属于代码修复默认动作。

## Test Ledger

| Time | Experiment | Actual result | Conclusion |
| --- | --- | --- | --- |
| 2026-07-18 | recovery + audit reuse | 复用 2026-07-17 完整根因链；未重做 QZone POST/feed | 从 producer、composer、approval、memory 四个根因修复 |
| 2026-07-18 | remediation RED | 初始专项为 `17 failed`；旧 producer/composer/approval/memory 合同按预期失败 | 测试能复现四层根因 |
| 2026-07-18 | remediation GREEN | QZone + Dream + Schedule 初次收口 `381 passed` | producer、selector、composer、schema v6、memory loop 基线完成 |
| 2026-07-18 | catalog RED/GREEN | `target_user_version` 期望 6 时单点失败 `5 != 6`；修正后 catalog/backup/remediation `70 passed` | 中央 DB catalog 与 store schema v6 对齐 |
| 2026-07-18 | delivery authenticity RED/GREEN | stale synthetic-self、无前缀 fiction、缺失 review metadata、被改写 factual 均先复现取凭证，再改为凭证前 `DeliveryGateError` | 历史/手工草稿不能绕过 producer/selector 进入 live |
| 2026-07-18 | approval scope HTTP RED/GREEN | 双 live flags 下普通 Approve 原返回 `approval_scope=live`；修正后默认 `dry_run`，显式 live 在 gate 未 ready 时 409 | 配置变化不再把日常审核自动升级为 live 授权 |
| 2026-07-18 | Grok read-only review | 指出历史 outbox bypass、隐式 live approval、content invariant、旧文档与数据迁移缺口；未改文件、未读 secret、未操作 QZone/NapCat | 关键 P0/P1 已在 delivery/API/schema/docs 收口 |
| 2026-07-18 | pre-deploy relevant suite | `446 passed`；Ruff clean；Pyright 0；`vue-tsc` 与 frontend build 通过 | 达到备份与 bot-only 部署门槛 |
| 2026-07-18 | BackupService pre-change | `pre-change-20260718-091031`：trusted，26 ok / 0 failed；qzone/memory/config/story arc 均 ok，DB quick_check ok | 迁移与数据修复具备精确回滚基线 |
| 2026-07-18 | first bot-only deploy/runtime probe | schema v6、历史 published=`dry_run`、live gate locked；新 fiction pending 的 prefix 为 `虚构故事里,` | 发现 composer→store 边界的全角逗号折叠，草稿未审批/未发布 |
| 2026-07-18 | prefix round-trip RED/GREEN | compose→store 测试先失败；normalizer 保留 `，` 后 GREEN，相关 `447 passed`，全仓 `4840 passed / 17 skipped` | 确定性 fiction frame 在持久化后仍成立 |
| 2026-07-18 | second bot-only deploy + immutable revision | 原 ASCII-prefix draft rejected；revision 2 仅修正 prefix，现 `fiction/public`、`arc_scope=fiction`、`dry_run` | 无正文事实扩张，无 live approval，无远端操作 |
| 2026-07-18 | precise live data repair | 两张精确卡 active→expired；fiction arc 两条 stale self→`fiction/public`；DB quick_check ok | schedule-memory 反馈污染与历史 producer metadata 已清理 |
| 2026-07-18 | final runtime acceptance | authenticated local health 200；enabled=true、dry_run=true、live=false、allowlist empty、profile unvalidated、live gate false；NapCat created/started/restart 未变 | v0.8.2 已部署，真实发布保持 fail-closed |
| 2026-07-18 | Grok post-fix review | focused 115 passed；旧 P0/P1 全关闭；无新 Critical/Important；仅余 delivery 已兜底的 P3 防御纵深 | 最终 source acceptance 通过；runtime 由 Codex 独立验收 |

## Rollback

- 代码：回退本 tracker 记录的 source/test/migration 变更并重建 bot；不触碰 NapCat。
- DB：v6 为 additive `approval_scope` 列，旧 runtime 可忽略；部署前做一致 SQLite backup。
- memory：若处置污染卡，先做 SQLite backup，仅按精确 card IDs expire；可从备份恢复。

## Next step

完成。保持 live gate locked；未来真实发布必须取得新的显式授权并对新的 draft 单独授予 live scope，不得重发或处置既有远端日志。
