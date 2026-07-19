# Style 视觉污染生产数据清理（2026-07-19）

> 状态：completed
> mode: task-bug
> 授权解释：用户“s授权”按授权清理生产历史 Style 污染数据执行；不扩张为部署、QQ/QZone/NapCat 或其他记忆数据清理。
> 当前下一步：无；若要防止再次污染，需另行授权部署已验收的 Style provenance 代码。在此之前不要再次触发 Style 手工抽取/自动审批。
> 阻塞：无。
> 回滚：trusted backup `pre-change-20260719-224533`；plan SHA-256 `53355a9f79d64484b3c42f561a6705d8978fcd2ba37157800318b433ccd42b88`；可按 73 个 expression/evidence 主键行级恢复，整库 restore plan 需 stop/start bot 才可执行。

## Objective

清理 `docs/audits/bot-memory-language-visual-behavior-audit-2026-07-18.md` 已确认的生产历史 Style 污染：只处理可证明来自视觉/system/bot 派生文本、却被标成 human/approved 的记录；不使用宽泛文本猜测，不清理正常人类措辞，不改变运行代码或插件配置。

## Scope / Prohibitions

- allowed: WAL-aware read-only inventory、可信备份、精确 dry-run、经证据确认后的生产 Style 行级修复、验证与文档。
- forbidden: commit/push/deploy、QQ/QZone send、Docker/NapCat restart/recreate、其他 SQLite 批量清理、凭据读取/输出。
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
| Preventive deployment | deferred | 容器 extractor SHA 与宿主修复版不同；本授权不包含部署 |

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
| T12 | Runtime code SHA | host extractor `ef96f86b…f7f` vs container `20ac3799…9cf` | Preventive code not deployed；new extraction can recur until separate deployment | 2026-07-19 |

## Final Outcome

- Production `style.db`: approved `12→10`, pending `1175→1104`, rejected `9→82`.
- Exact cleanup: 73 evidence reclassified to `system`; their 73 expressions rejected; no physical deletes.
- Active feedback loop: both audited approved visual expressions are no longer selectable; enabled profiles remain 0.
- Backup: `/app/storage/backups/pre-change/2026-07-19/manifest.json`，style backup quick_check=ok.
- Plan: `/app/storage/backups/pre-change/2026-07-19/style-visual-cleanup-plan.json`，mode 0600.
- No commit/push/deploy；no QQ/QZone/NapCat；bot container unchanged and restart count 0.
