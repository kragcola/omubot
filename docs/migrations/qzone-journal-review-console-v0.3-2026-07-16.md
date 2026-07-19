# QZone Journal Review Console v0.3 Migration (2026-07-16)

> 状态：implemented in code, not deployed.
> 范围：QZone Journal v0.3.0 的人工审核决策审计、分页/详情 Admin API 与 Calm Ops 审核控制台；不真实发布、不读凭据、不创建 validated profile、不触 NapCat。

## Old → New Mapping

| 面 | v0.2.0 | v0.3.0 |
|---|---|---|
| SQLite 审核审计 | unknown 人工恢复写 `qzone_journal_manual_resolutions`；approve/reject 无独立审计行 | governed migration v4 新增 `qzone_journal_review_decisions`，approve/reject 与审计行同事务 |
| 草稿列表 API | `GET /drafts?status=`，固定内部 limit，无总量/偏移 | `status + limit + offset`；返回 `drafts/total/limit/offset/has_more`，API limit 上限 100 |
| 草稿详情 | 列表行承担全部信息 | `GET /drafts/{draft_id}` 独立详情；404 明确 |
| 审计详情 | 仅 Store 可查询 manual resolutions | `GET /drafts/{draft_id}/audit` 分别返回 `review_decisions` 与 `manual_resolutions` |
| reject | Admin 无 operator note；Store 允许 pending/approved 拒绝 | Admin note 必填；普通审核只允许 `pending_review → rejected`，approved 仅保留 dry-run 路径 |
| Health | 四态 counts，无结构化 live gate | 七态 counts；`live_publish_gate.ready + reasons[{code,message}]`，不读取凭据 |
| Admin SPA | 无专用页面 | `/admin/qzone-journal` 审核队列、详情抽屉、dry-run 与 unknown 人工处置；无 live publish 按钮/调用 |
| 导航/开发路由 | 无 | SideMenu「日常 / 空间日志」；Vite SPA route 补 `/admin/qzone-journal` |

## SQLite v4

数据库仍为 `storage/qzone_journal.db`，migration ledger 从 v3 additive 升至 v4：

```sql
CREATE TABLE qzone_journal_review_decisions (
    decision_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id          TEXT NOT NULL,
    decision          TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
    note              TEXT NOT NULL DEFAULT '',
    previous_status   TEXT NOT NULL,
    new_status        TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE INDEX idx_qzone_review_decisions_draft
ON qzone_journal_review_decisions(draft_id, created_at);
```

- 不复用 `qzone_journal_manual_resolutions`：前者记录常规审核，后者只记录 unknown 的人工发布结果确认。
- approve/reject 的状态 UPDATE 与 review decision INSERT 位于同一个 `BEGIN IMMEDIATE` 事务。
- SQLite trigger 故障注入已证明审计 INSERT 失败时，状态与 `last_error_code` 整体回滚。
- 重复 approve/reject 保持幂等，不重复写审计行。
- operator note 复用公开文本 scrub，不保留 secret assignment 或 QQ-like 编号；不虚构 operator identity。
- v3→v4 临时库验证：旧草稿保留、`user_version=4`、ledger `[1,2,3,4]`。

## Admin Review Contract

- pending：可 approve；reject 必须有非空备注。
- approved：只允许 dry-run；普通 reject 返回 409。
- unknown：必须有处置备注；可 confirm-published（external id 可选）或 confirm-not-published。
- dispatching / published / rejected / failed：控制台只读。
- 前端所有动作成功后刷新详情、审计、列表与 health，并保持抽屉打开。
- 列表/health/详情均有 request generation guard；旧请求不得覆盖新筛选、新草稿或已关闭抽屉。
- dry-run 只显示固定安全字段白名单；未知扩展字段默认不展示。
- QZone 前端源码无 `/publish` 路径调用；后端既有 `/publish` 端点仍由原有 fail-closed gate 保护。

## Deployment / Observation

本 migration 未部署。未来若部署：

1. 只 recreate bot，永不 restart/recreate NapCat。
2. 启动后只读检查 `qzone_journal.db`：`PRAGMA user_version=4`、ledger `[1,2,3,4]`、`quick_check=ok`。
3. Admin `/qzone-journal/health` 应显示七态 counts 与结构化 block reasons；内置 profile 下 `ready=false`。
4. Admin SPA 只验证审核、dry-run、unknown 人工确认；不调用真实发布。
5. 未取得 real_sanitized CGI fixture、独立 validated profile 与用户授权 canary 前，不进行 live publish。

## Rollback

1. 回退 store/plugin v4、QZone review-console API/测试与 admin frontend QZone 文件。
2. v4 表为 additive：旧 runtime 不读取，可保留不删；无需重建数据库。
3. 如需整库回退，恢复部署前 `qzone_journal.db` 备份；不修改 NapCat 数据。
4. 前端回退路由、菜单、Vite SPA route 并重新 `npm run build`。
5. 始终保持 `BUILTIN_WIRE_PROFILE.validated=false`。

## Verification

- QZone 七文件：**133 passed**。
- scoped Ruff clean；Pyright **0 errors**。
- `vue-tsc --noEmit`（在 `admin/frontend` 工作目录）：通过。
- Admin production build：通过；生成独立 `QzoneJournalView` CSS/JS chunk。
- UI compliance：QZone 新文件无 raw color、`!important`、静态 inline style；新 radius/spacing 服从 Calm Ops。
- 静态扫描：QZone 前端无 `/publish` 调用。
- 独立 Grok review：0 Critical；2 Important（approved reject 状态矩阵、陈旧请求覆盖）均已修复并回归。
- 最终全仓 pytest：**3950 passed, 17 skipped, 186 warnings**。

## Explicit Non-goals

- 真实 CGI request/response 捕获、validated profile 或单条 canary。
- 在 Admin SPA 中提供 live publish。
- 自动审核、自动发布或取消 manual review。
- operator 身份系统或审计签名。
- Part C factual 真人公开投影。
