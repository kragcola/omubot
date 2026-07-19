# QZone Journal Advanced Fiction / Review Provenance Migration (2026-07-16)

> 状态：implemented in code, not deployed.
> 范围：QZone Journal v0.2.0 的 fiction 世界书上下文、公开文本护栏与草稿审计 provenance；不真实发布、不读凭据、不创建 validated profile、不触 NapCat。

## Old → New Mapping

| 面 | 旧 | 新 |
|----|----|----|
| `advanced_enabled` | 仅配置占位，selector 中不产生行为 | 只为**已经通过 selector 的 fiction 事件**补充 StoryArc / fiction partner 世界书上下文；不新增候选、不改变触发频率 |
| 草稿审核 | 仅 `source + content`，无法核对原事件 | v3 保存 `stable_id`、`subject_kind`、`privacy`、`salience`、`source_summary`、`provenance_json` |
| Admin drafts | 仅成稿/状态 | 新增强制 `review` bundle，同时保留顶层兼容字段 |
| Health | enabled/dry-run/live/counts | 增加 advanced/manual-review/wire-profile/profile-validated/salience/allowed-sources |
| 公开文本 | prompt/LLM output/旁路 store 可带编号或秘密赋值 | 共享 `public_safety` 覆盖 prompt、世界书、LLM 成稿、Store 正文、review provenance、人工恢复 note |
| Tick 隔离 | provenance 校验失败可中止整个事件循环 | 每个候选独立处理；`ValueError` 只拒绝该候选并记录 best-effort metric，后续健康事件继续 |

## SQLite v3

数据库仍为 `storage/qzone_journal.db`，migration ledger 从 v2 additive 升至 v3：

```text
stable_id       TEXT NULL
subject_kind    TEXT NULL
privacy         TEXT NULL
salience        REAL NULL
source_summary  TEXT NULL
provenance_json TEXT NULL
```

- 旧草稿不回填，六列保持 `NULL`，读取兼容。
- 不改 draft 状态机、daily quota、manual resolution 表或索引。
- provenance schema_version 固定为 1，只允许 arc/revision/stage/context-flag/fiction partner IDs；未知键、真人/群 ID 字段、secret assignment、非布尔 context flag 均 fail-closed。
- v2→v3 在临时 SQLite 中验证：旧行保留、`user_version=3`、ledger `[1,2,3]`。

## Public Safety Contract

- 去除 Unicode `Cf`（ZWSP/soft hyphen 等），折叠全角拉丁/数字并识别全角 `：＝`。
- QQ-like 5–16 位连续编号即使带 `qq_` / `user` 前缀或由 ZWSP 拆分也会被 redaction。
- `user_id/group_id/uin` 与 cookie/token/authorization/credential/password/secret assignments 被移除；Bearer token 整段不残留。
- 世界书使用 newline-preserving scrub，保持 `开放线索` / `虚构伙伴状态` 多行结构，并在最终 scrub 后硬限制 1600 字符。
- Part C factual 真人数据仍不接入公开空间；`subject_kind=factual` 在 advanced 开启时仍拒绝。

## Deployment / Observation

本 migration 未部署。未来若部署：

1. 只 recreate bot，永不 restart/recreate NapCat。
2. 启动后只读检查 `qzone_journal.db`：`PRAGMA user_version=3`、migration ledger、`quick_check=ok`。
3. Admin health 应显示 `profile_validated=false`、`manual_review=true`；默认 `enabled=false` / `dry_run=true` / `allow_live_publish=false`。
4. 未取得真实脱敏 CGI fixture、独立 validated profile 与用户授权 canary 前，不进行 live publish。

## Rollback

1. 回退 `advanced_fiction_context.py`、`public_safety.py`、composer/plugin/store v0.2.0 hunks与对应测试。
2. v3 六列为 nullable additive：旧 runtime 可忽略，多余列可保留；无需删列或重建数据库。
3. 若必须整库回退，使用部署前备份恢复 `qzone_journal.db`；不修改 NapCat 数据。
4. 保持 `BUILTIN_WIRE_PROFILE.validated=false`。

## Verification

- QZone 六文件：**123 passed**。
- QZone + manifest/catalog/ownership：**159 passed**。
- scoped Ruff clean；Pyright **0 errors**；JSON parse / diff-check clean。
- 独立 Grok adversarial review：原 1 Critical + 5 Important 全部复审为 **FIXED**，无 Critical/Important 重开。
- 当前混合工作树全量：**3940 passed, 17 skipped, 186 warnings**。

## Explicit Non-goals

- 真实 CGI request/response 捕获与 live publish。
- 新建独立 validated wire profile、单条 canary。
- Part C factual 真人化名/公开投影。
- 自动发布、取消 manual review。
- 新增 fiction_partner 日常候选或改变“有事才发”的触发频率。
