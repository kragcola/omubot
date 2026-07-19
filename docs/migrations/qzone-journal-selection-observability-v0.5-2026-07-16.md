# QZone Journal v0.5 - Selection Observability

> 日期：2026-07-16
> 状态：implemented offline, not deployed, not committed.
> 范围：进程级选材摘要、Admin 选材诊断、typed source allowlist 收口与 QZone 页窄屏修正。无 SQLite migration、无真实 QZone HTTP、无凭据读取、无 validated profile、无 Docker/NapCat 操作。

## Old -> New

| 面 | v0.4.0 | v0.5.0 |
| --- | --- | --- |
| Health | `selection_decisions` 闭集 reason 计数 | 保留原键；新增 `selection_summary`：`process_lifetime / total / accepted / rejected / acceptance_rate` |
| Admin SPA | health 已返回选材计数，但页面不消费 | 新增“选材诊断”：本进程摘要、单次/当日草稿预算、非零 reason 列表、零状态 |
| `allowed_sources` | JSON Schema 仅允许三种官方来源；`PluginConfig` 可程序化注入任意字符串 | typed validator 与 Schema 对齐，只允许 `event_replan / dream_reflection / schedule_generator`，去重且不在异常中回显原值 |
| 窄屏 | QZone surface 可被 min-content 撑宽；live-gate 长 code 不换行 | 仅 QZone 页局部 `min-width: 0`；reason code 可收缩并 `overflow-wrap:anywhere` |
| 异步 UI | 详情请求有 generation；dry-run/审核/处置动作无统一迟到保护 | 所有动作绑定 draft id + action generation；切换/关闭后不串写结果、字段或 toast |
| Gate 真值 | cold load 会暂时显示“仍被锁定”；失败可混入旧 reasons/meta | 明确 `loading/error/ready/blocked`；失败清空旧 health，只有 definitive blocked 显示 reasons |
| 外部帖子 ID | 人工确认消毒至 120 字符；自动 `mark_published` 未复用，parser 可接受 128 | Store 自动/人工统一 120 字符合同；parser 120 接受、121 起 ambiguous |
| 插件版本 | `0.4.0` | `0.5.0` |

## Frozen Contract

- `selection_summary.scope` 固定为 `process_lifetime`；重启后重新计数，不冒充持久历史。
- `accepted` 只统计成功 enqueue 后的 `accept`；`rejected = total - accepted`；无样本时通过率为 `0.0`。
- 原 `selection_decisions` 保留完整闭集 reason -> non-negative int，兼容既有调用方。
- UI 只显示聚合计数与官方 source 配置；不显示 summary、stable_id、QQ-like identifier、凭据或任意原始事件文本。
- Admin SPA 仍无 live publish 按钮或 `/publish` 调用。
- `manual_review=true`、`dry_run=true`、`allow_live_publish=false` 默认不变。
- `BUILTIN_WIRE_PROFILE.validated` 必须保持 `false`。

## TDD Evidence

1. `selection_summary` RED：health 缺键，精确失败于 `assert "selection_summary" in body`。
2. Admin contract RED：TypeScript health 类型与页面均缺 selection observability。
3. 版本 RED：manifest 仍为 `0.4.0`。
4. typed source RED：`PluginConfig` 未拒绝 secret-shaped custom source。
5. 窄屏 RED：QZone 页缺局部 surface shrink 合同，live-gate code 仍为 `flex-shrink:0`。
6. Drawer action RED：无 action generation，切换草稿后迟到 dry-run 可写入当前抽屉。
7. Gate phase RED：cold load 缺“正在检查”状态，失败路径保留旧 health。
8. Store ID RED：`mark_published("  tid-parser-42  ")` 未规范化，非法/超长 ID 未 fail-closed。
9. Parser/Store 边界 RED：121 字符 ID 被 parser 误判为 `published`，但 Store 上限为 120。

最终 GREEN：新增四条精确回归 **4 passed**；QZone 七文件 + metrics **160 passed**；全仓 **4196 passed / 17 skipped / 186 warnings**；Ruff clean；Pyright **0 errors**；`vue-tsc --noEmit` 与 production build 通过。第二轮独立 Grok review 的 **0 Critical / 2 Important** 均已修复；Codex 追加的 parser/store 边界问题也已回派收口；实现后最终只读复审为 **0 Critical / 0 Important**。

## Visual Evidence

- 使用独立 `127.0.0.1:4179` 静态预览与合成 health/drafts 响应；没有连接现有 `8081` 管理端或 live DB。
- 1440px 浅色与深色模式：选材摘要、reason 列表、空草稿状态可读，无文本遮挡。
- 390px、侧栏折叠：`document.scrollWidth == innerWidth == 390`，QZone 诊断面板位于主内容内；长 reason code 可换行。
- 侧栏默认展开时的 220px 全局布局压缩属于共享 layout 行为，本 slice 不修改；QZone surface 自身已不再撑宽。

## Explicit Non-goals

- 真人 factual/social public projection。现有 Part C 数据仍含真实 user/group/evidence，未建立生产者侧公开化名 DTO 与审计证据，不接入 QZone。
- 持久 selection reason 明细、时间窗趋势或跨重启统计。本版如实标注 process lifetime。
- real_sanitized CGI fixture、独立 validated profile、单条 canary 或任何自动发布。

## Rollback

1. 回退 `plugin.py` 的 `selection_summary`、typed source validator 与版本号。
2. 回退 `plugin.json` 至 `0.4.0`。
3. 回退 QZone 前端 types/view 的诊断区与局部响应式样式，并重新 build。
4. 若仅回退上线前补强：回退 Drawer action generation、gate phase、Store `mark_published` 消毒和 parser 120 字符上限，以及对应四条回归。
5. 回退对应测试与本文档；无数据库反向迁移。
6. 始终保留 `BUILTIN_WIRE_PROFILE.validated=false`。
