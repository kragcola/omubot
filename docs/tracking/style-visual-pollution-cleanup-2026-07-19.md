# Style 视觉污染生产数据清理（2026-07-19）

> 状态：completed
> mode: task-bug
> 授权解释：用户“s授权”仅授权生产历史 Style 污染数据清理；后续“部署，提交，测试”另行授权了防再污染代码的提交与 bot-only 部署。两个阶段均未授权 QQ/QZone 发送、NapCat 操作或其他记忆数据清理。
> 当前下一步：无；历史清理与防再污染部署均已验收闭环。
> 阻塞：无。
> 回滚：trusted backup `pre-change-20260719-224533`；plan SHA-256 `53355a9f79d64484b3c42f561a6705d8978fcd2ba37157800318b433ccd42b88`；可按 73 个 expression/evidence 主键行级恢复，整库 restore plan 需 stop/start bot 才可执行。

## Objective

清理 `docs/audits/bot-memory-language-visual-behavior-audit-2026-07-18.md` 已确认的生产历史 Style 污染：只处理可证明来自视觉/system/bot 派生文本、却被标成 human/approved 的记录；不使用宽泛文本猜测，不清理正常人类措辞，不改变运行代码或插件配置。

## Scope / Prohibitions

- allowed: WAL-aware read-only inventory、可信备份、精确 dry-run、经证据确认后的生产 Style 行级修复、验证与文档。
- forbidden: commit/push/deploy、QQ/QZone send、Docker/NapCat restart/recreate、其他 SQLite 批量清理、凭据读取/输出。
- follow-up authority: 后续明确授权仅覆盖代码 commit、bot-only deploy 与测试；仍不含 push、QQ/QZone send 或 NapCat restart/recreate。
- protected WIP: 当前 dirty/untracked 工作区全部属于用户；不 reset/clean/stash。
- parallel fit: Style DB inventory/backup/write share one mutable conflict domain and must serialize；仅并行普通只读文件查询，不派代理。

## Plan

- [x] 恢复审计证据并确认 Style 存储/运行态路径。
- [x] 定义 fail-closed 污染谓词并输出逐行 dry-run。
- [x] 创建可信备份与独立候选行回滚载荷。
- [x] 执行最小行级修复。
- [x] WAL-aware 验证 quick_check、计数、正常样本不变与运行态边界。
- [x] 更新 maintenance log / ACTIVE 并收口。

## Decisions

| Decision | Choice | Reason |
| --- | --- | --- |
| Authorization | Style production data cleanup only | “s授权”结合上一轮唯一待授权项解释；若用户纠正则立即停止 |
| Deployment | no | 数据清理不自动授权代码/容器变更 |
| Cleanup policy | evidence-exact, fail closed | 历史启发式发生率不能作为删除依据 |
| Rollback | backup + row-level payload | 避免覆盖并发产生的其他生产数据 |
| Context marker | not sufficient | 仅 context 邻近图片会误伤 558 条；必须是 evidence 自身 raw_text 命中 |
| Data action | evidence `human→system` + expression `pending/approved→rejected` | 保留审计正文与 revision，不物理删除 |
| Enabled profiles | none | 无需禁用或重建 profile |
| Preventive deployment | completed by later authorization | 清理时授权不包含部署；后续防线随 `40a8e32` / image `sha256:d89121d98ef0...` 完成 bot-only 部署 |

## Test Ledger

| ID | Command / Evidence | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| T00 | Repository gate + ACTIVE/status recovery | Omubot gate pass；previous task completed；dirty WIP preserved | New production-data tracker required before inspection/write | 2026-07-19 |
| T01 | Container WAL-aware `style.db` inventory | `quick_check=ok`；12 approved / 1175 pending / 9 rejected；1196 evidence 全被历史标成 human；无 profile | Runtime truth is container `/app/storage/style.db`, not host storage | 2026-07-19 |
| T02 | Approved expression/evidence inspection | 2 approved rows的 raw_text 均以 `«图片...` 开始，confidence 0.8/0.7，命中审计原结论 | 两条 active feedback-loop 污染精确确认 | 2026-07-19 |
| T03 | Broad context-marker inventory | 558 candidates | context 邻近图片不能证明 expression 来源污染；谓词否决，不执行 | 2026-07-19 |
| T04 | Raw-only current-contract inventory | 73 expressions / 73 evidence；2 approved + 71 pending；40 image marker + 33 angle system annotation；mixed=0；candidate SHA `0c2a0041…5756c` | 精确清理集稳定；每个 expression 没有混合正常 evidence | 2026-07-19 |
| T05 | BackupService `pre-change` | `pre-change-20260719-224533`；26 ok / 0 failed / trusted=true；style SHA `9c7f8a57…a47b`；quick_check=ok | 写前可信热备份完成 | 2026-07-19 |
| T06 | Cleanup program static checks | Ruff pass；Pyright 0 | Deterministic apply/dry-run utility clean | 2026-07-19 |
| T07 | First backup-copy apply | `sqlite3.Row` generic Ruff rewrite caused IndexError before any UPDATE；transaction rollback | Root cause fixed with explicit Row keys + scoped lint exception；production untouched | 2026-07-19 |
| T08 | Fresh backup-copy apply + second dry-run | 73/73 applied；73 revisions；status 12/1175/9→10/1104/82；noncandidate digest stable；quick_check=ok；second dry-run 0 | Exact transaction, idempotent outcome and rollback metadata verified before production | 2026-07-19 |
| T09 | Production plan | same 73 rows / SHA `0c2a0041…5756c`；mixed=0；enabled profiles=0；plan mode 0600 / SHA `53355a9f…2b88` | No drift between backup and live apply boundary | 2026-07-19 |
| T10 | Production atomic apply | 73 evidence `human→system`；73 expressions `rejected`；73 revisions；approved 12→10，pending 1175→1104，rejected 9→82 | Authorized production cleanup committed successfully | 2026-07-19 |
| T11 | Independent post-write read-only verification | `quick_check=ok`；structured human evidence remaining=0；target system/rejected=73；target approved=0；container running/restart=0 | Data objective complete without container restart or external send | 2026-07-19 |
| T12 | Runtime code SHA at cleanup boundary | host extractor `ef96f86b…f7f` vs container `20ac3799…9cf` | 这是清理完成时的历史 mismatch；当时尚未获得部署授权，不代表当前运行态 | 2026-07-19 |
| T13 | Commit/image/container lineage | commit `40a8e32faede3cf1a8b9152967c0dd98ecbb6b55`；image `sha256:d89121d98ef0...`；`qq-bot` `GIT_COMMIT=40a8e32...`；宿主与容器 extractor SHA 均为 `ef96f86b60985d25a1ecbd45671e5dff22c57b9157bc96fe51217709a7147f7f` | 防再污染代码已在当前生产 bot 运行；无需重复 build/recreate | 2026-07-20 |
| T14 | Focused/static/runtime verification | Style focused pytest `49 passed`；Ruff pass；Pyright `0 errors, 0 warnings`；容器 smoke 为 visual/system `False`、普通 human `True`；manual extract 调用 eligibility helper | 自动与手工 Style 抽取路径均受 provenance fail-closed 防线约束 | 2026-07-20 |
| T15 | WAL-aware current production verification | `quick_check=ok`；approved/pending/rejected=`10/1114/82`；source human/system=`1133/73`；cleanup revisions=73；structured human remaining=0；approved/pending 与 non-human evidence 交集=`0/0`；清理后新增 10 条均为 eligible human | 历史清理保持有效，后续正常 human 学习未被误阻断 | 2026-07-20 |
| T16 | Grok normal required-parallel cross-check | top-level `7a52dcb7-dcd2-45db-9a98-efa03c0fa8f4` + child `019f7d13-8eab-7ff3-b2be-30b071c84eeb`；只读、零文件变更 | 独立结论与 Codex 验收一致：代码已提交/部署，无需二次部署；最终 live DB/container 证据由 Codex 直接复核 | 2026-07-20 |

## Final Outcome

- Cleanup-boundary production `style.db`: approved `12→10`, pending `1175→1104`, rejected `9→82`；当前为 `10/1114/82`，新增 10 条均为 eligible human pending evidence。
- Exact cleanup: 73 evidence reclassified to `system`; their 73 expressions rejected; no physical deletes.
- Active feedback loop: both audited approved visual expressions are no longer selectable; enabled profiles remain 0.
- Backup: `/app/storage/backups/pre-change/2026-07-19/manifest.json`，style backup quick_check=ok.
- Plan: `/app/storage/backups/pre-change/2026-07-19/style-visual-cleanup-plan.json`，mode 0600.
- 清理事务当时未 commit/push/deploy，且 bot container unchanged / restart=0；这是历史阶段事实。
- 后续防再污染代码已由 `40a8e32` 提交并随现有 bot image 部署。当前容器已是目标代码，因此本次复验采用 no-op deploy 决策，没有重复 build/recreate；bot restart=0，NapCat restart=0 / OOM=false。
- 全过程未 push、未发送 QQ/QZone，未重启或重建 NapCat，也未触发 Style 手工抽取或自动审批。
