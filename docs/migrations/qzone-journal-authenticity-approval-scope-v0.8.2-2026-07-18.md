# QZone Journal v0.8.2 Authenticity and Approval Scope

> 日期：2026-07-18 · 版本：`0.8.2` · schema `v6` · live 发布继续锁定

## Why

一次真实发布证明旧链路会把 fiction schedule 经 Dream 反思包装为
`self/public`，再以没有虚构标识的第一人称正文进入 QZone。问题不在远端
协议，而在 producer 分类、compose 事实边界、审批作用域和合成记忆反馈环。

本迁移修复本地真实性合同。既有远端日志不删除、不隐藏、不重发。

## Behavior Contract

| Boundary | v0.8.2 contract |
| --- | --- |
| Schedule / Dream producer | fiction arc 输出 `fiction/public`；非-fiction arc 输出 `self/unknown` |
| Selector | `schedule_generator` / `dream_reflection` 的 stale 或伪造 `self` 记录 fail-closed |
| Fiction compose | 正文确定性以 `虚构故事里，` 开头；recompose 同样保持 |
| Factual compose | 正文严格等于闭模板公开投影摘要，不调用 LLM，不消费 synthetic day narrative |
| Live delivery | 在读取凭证前重验 source/subject/privacy；拒绝 synthetic-self、无前缀 fiction、被改写 factual 和缺失审核元数据 |
| Approval | 普通 Admin 审批显式为 `dry_run`；只有请求明确指定 `live` 且完整 live gate ready 才能写入 live approval |
| Memory loop | global synthetic Dream reflection 不写通用 memory card；Schedule 不再读取 `dream_reflection` 卡 |
| Provenance | review provenance 增加 `arc_scope`，Admin 展示 `approval_scope` |

## Schema v6

`qzone_journal_drafts` 新增：

```sql
approval_scope TEXT NOT NULL DEFAULT 'dry_run'
  CHECK (approval_scope IN ('dry_run', 'live'))
```

- 所有 v1-v5 历史行迁移后均为 `dry_run`，不会继承或猜测 live 权限。
- 已批准行不能原地从 `dry_run` 改为 `live`。
- 中央数据库 catalog 的 `qzone_journal.target_user_version` 同步为 `6`，避免备份/健康检查将正确的 v6 库判为漂移。
- 列为 additive；旧镜像可忽略该列，但回滚旧镜像不会恢复 live 发布能力。

## Deployment

1. 用容器内 `BackupService` 创建 trusted `pre-change` 备份。备份必须包含
   `qzone_journal.db`、`memory_cards.db`、plugin config 和 active StoryArc。
2. 记录 bot 与 NapCat 的 created/restart 状态。
3. 只 build / force-recreate `bot`；禁止 restart/recreate NapCat。
4. 启动后只读验证：`PRAGMA user_version=6`、`quick_check=ok`、
   `approval_scope` 存在、历史 published 行为 `dry_run`、actionable queue 为零。
5. 验证运行配置仍为 `dry_run=true`、`allow_live_publish=false`、live allowlist 为空，
   `BUILTIN_WIRE_PROFILE.validated=false`，health live gate 为 closed。
6. 只有备份验证成功后，才按精确 card ID expire 已确认污染的 memory cards。

本部署不发起 QZone POST、retry、delete 或任何远端消息变更。

## Rollback

1. 保持 live flags 关闭。
2. 将 bot 回滚到上一镜像并只 force-recreate bot；NapCat 不动。
3. 如需数据回滚，停止 bot 后按 BackupService restore plan 恢复精确 DB/item；
   不复制 live WAL sidecar，不执行远端补偿操作。
4. v6 为 additive；若仅代码回滚，旧 runtime 会忽略 `approval_scope`。

## Verification

- QZone/Dream/Schedule/catalog/backup relevant suite：`447 passed`。
- Full repository：`4840 passed, 17 skipped, 186 warnings`。
- Ruff：clean；Pyright：`0 errors`。
- `vue-tsc --noEmit`、Admin build、plugin-aware sidebar tests：通过。
- Grok 只读交叉复核确认 producer/selector/composer 主链已关闭，并指出的历史草稿
  bypass、隐式 live approval、delivery 内容不变量和迁移文档缺口已纳入 v0.8.2。
- Grok post-fix 复核：旧 P0/P1 全关闭，无新 Critical/Important；focused 115 passed。
  P3 残留为 store 允许保存可供审核但不能通过 live delivery 的不合规草稿等防御纵深事项。

## Deployment Result

- BackupService：`pre-change-20260718-091031`，trusted，26 ok / 0 failed；
  QZone DB、memory DB、plugin config、StoryArc payload 均 ok。
- 仅 build / force-recreate bot；最终 bot running、restart 0。NapCat 的
  created / started / restart count 均未变化。
- Runtime DB：schema v6、`quick_check=ok`；历史 published 行为 `dry_run`。
- Runtime config：enabled=true、dry-run=true、live=false、allowlist empty、
  built-in profile unvalidated；认证 health API 的 live gate `ready=false`。
- 精确污染卡已 expired；active fiction StoryArc 的两条 stale self 事件已修正为
  `fiction/public`。
- 首轮部署发现 fullwidth comma 被 store 折叠；新增 compose→store 回归并二次
  bot-only 部署。当前 pending tip 是 revision 2、`fiction/public`、
  `arc_scope=fiction`、canonical `虚构故事里，`、`approval_scope=dry_run`。
- in-app browser 可加载 Admin 登录页且无 console error/warn；因没有现成登录态，
  未读取或填写 Admin Token。实际 QZone health 数据由容器内本地认证 API 验收。
