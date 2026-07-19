# QZone Web UX + plugin-aware navigation + live publish

> 状态：complete · 2026-07-17 · Web/侧栏/一次性真实发布测试已完成；运行态已恢复 fail-closed

## Objective

1. 将空间日志页改造成可快速阅读、筛选、审核和操作的 Calm Ops 详情型控制台。
2. 让空间日志、生日祝福等插件强相关的一级侧栏严格跟随运行态插件 enabled 状态；禁用时隐藏，启用时显示。
3. 前端与运行门通过后，将 QZone 从 dry-run 切到真实发布，并对现有唯一 approved draft 执行一次真实发布测试。

## Non-negotiable boundaries

- 不使用 `docker compose down`，不 restart/recreate NapCat，不清理无关 dirty/untracked 文件。
- 不把 `BUILTIN_WIRE_PROFILE.validated` 改为 true；真实发布必须使用独立、secret-scanned、可审计的 validated profile 输入。
- 不批量发布；仅复用现有 approved draft。成功后不自动远端删除。
- 侧栏显隐读取真实插件运行态，不用硬编码“默认显示”冒充 enabled。
- 页面业务 API、draft 状态机、tip/revision/action boundary 保持不变，除非真实发布 profile 接线明确需要后端变更。

## Final state

- QZone 页面已完成 Calm Ops 详情控制台重构；第四个指标为“已发布”，当前显示 `1`。
- 插件专属一级菜单显式映射：`/stickers → sticker`、`/knowledge → knowledge`、`/birthday → calendar_context`、`/qzone-journal → qzone_journal`；disabled、缺失或 API 失败均 fail-closed 隐藏。
- 单次授权发布 POST 只执行一次。严格解析器未得到明确成功响应，工具返回 `publish_failed_or_ambiguous`，未重试、未生成 fixture/attestation，并将草稿置为 `unknown`。
- 随后的只读认证 QZone feed 查询返回 HTTP 200 / code 0，精确命中该草稿正文和远端 post ID；使用既有人工处置状态机确认成 `published`，没有第二次发布。
- 最终 draft：`qzd_9e0753f900cd837b46d6d3bd`，content SHA-256 `cae01edff8ec7db05407cd58e14d18ff2d3b12cecf52cd1f22c79b6654f5b4c2`，external ID SHA-256 `ab9355123c5bbf63322e97d0ab0a2b555b53468a2b30f7a4f02f1b846e2508e8`。
- 最终运行态：QZone plugin enabled；`dry_run=true`、`allow_live_publish=false`、内置 profile、空 UIN allowlist；live gate 保持锁定。只读 feed 响应不得伪造成原始 publish fixture。
- Bot 为 container `ce829a2f7657...` / image `sha256:4689af9a1e18a80cd7995b015155901d50f1be502286ed237a163ca0a8f27f3b` / tag `omubot-bot:qzone-live-20260717-v2`。NapCat 保持 `19f6cf13607c...`，restart=0，未 restart/recreate。

## Workstreams

### A. QZone page UX

- [x] 重构 `QzoneJournalView.vue` 与详情抽屉的信息层级、响应式布局、状态说明和操作可发现性。
- [x] 复用 `AppPage`、`MetricCard`、`PageToolbar`、`AppPanelSection`、`EmptyState`、drawer layout。
- [x] 通过 light/dark、900/1280/1440/1920 视觉检查。

### B. Plugin-aware top-level navigation

- [x] 建立 route/menu item 到 plugin name 的显式映射。
- [x] 从 Admin plugins API 读取 runtime `enabled`；对应插件 disabled/缺失时一级菜单隐藏。
- [x] API 暂时失败时 fail-closed 隐藏插件专属菜单，但保留核心/system 菜单。
- [x] 覆盖 QZone、生日祝福、表情包、知识库四个插件专属一级菜单。
- [x] 运行态负向验收：`calendar_context` 初始 enabled/可见 → 保存 disabled 后仅重启 Bot → “生日祝福”隐藏 → 保存 enabled 后仅重启 Bot → 菜单恢复，最终 runtime enabled。

### C. Live publish

- [x] 复核现有 approved draft、凭据、UIN、QZone DB/health、NapCat identity。
- [x] 建立独立 HMAC attestation、专用 32-byte QZone key、精确 response echo 与同 bytes 解析；内置 profile 永久 `validated=false`。
- [x] 使用一次性 capture/publish 工具临时进入授权发布边界；不做阶段灰度，不把 live flags 留在常驻运行态。
- [x] 同一 draft 只发布一次；歧义响应立即停止且不重试。只读远端 feed 证实已发布后，走人工处置状态机确认。

## Test Ledger

| Time | Command / experiment | Actual result | Conclusion |
| --- | --- | --- | --- |
| 2026-07-17 | recovery + production baseline | Bot image `sha256:0c013680...`, QZone enabled/dry-run, one approved draft, published=0; NapCat restart=0 | Start from deployed dry-run state; preserve same draft and NapCat identity |
| 2026-07-17 | `pytest -q tests/test_qzone_journal_live_profile.py`（接线前） | 2 failed / 6 passed：custom profile 未进入 live gate；synthetic 未在资源前失败 | RED 有效，需把 profile load 移到 DB/transport 之前 |
| 2026-07-17 | custom profile runtime wiring | `tests/test_qzone_journal_live_profile.py` 8 passed；built-in 仍 `validated=false` | 独立 real fixture profile 可进入运行门，synthetic fail-closed |
| 2026-07-17 | one-shot capture/publish TDD | `tests/test_qzone_journal_live_capture.py` 6 passed；正文 SHA 前置、唯一 approved、UIN SHA、明确成功 fixture、确认口令均覆盖 | 工具不会自动重试，ambiguous 不生成 fixture，复用 JournalDelivery 状态机 |
| 2026-07-17 | QZone focused regression | `PYTHONPATH=/tmp/omubot_pytest_stubs... pytest -q tests/test_qzone*.py` → 290 passed | QZone 页面契约、状态机、transport、profile/capture 回归通过 |
| 2026-07-17 | frontend static verification | plugin menu tests 5 passed；`vue-tsc --noEmit` passed；`npm run build` passed | 生产静态资源已生成；状态事件会刷新侧栏，restart-required 插件以重启后的 runtime `enabled` 为准 |
| 2026-07-17 | in-app browser visual QA | light/dark；900/1280/1440/1920；pageOverflow=false；900 toolbar stacked；drawer opened and readable | Calm Ops 页面与响应式布局通过真实浏览器检查 |
| 2026-07-17 | NapCat QZone credential recheck | cookie/p_skey/g_tk present；UIN SHA-256=`0e5e6e5315a6...` | 授权目标与既有 allowlist identity 一致；未输出凭据/UIN |
| 2026-07-17 | profile/capture hardening + independent review | 最新完整 QZone regression `301 passed`；最终专项复跑 `24 passed`；review GO，0 Critical / 0 Important | attestation TOCTOU、密钥域、echo、commit 时序与 cleanup 边界关闭 |
| 2026-07-17 | one-shot real publish | 单个授权 POST 恰好执行 1 次；严格 parser 返回 ambiguous，draft → `unknown`，无 retry/fixture/attestation | 停止发布路径，不将不明确响应冒充成功 |
| 2026-07-17 | authenticated read-only QZone feed verification | HTTP 200、code 0、精确正文命中、远端 post ID 存在 | 远端实际上已发布；禁止再发同一 draft |
| 2026-07-17 | manual resolution | 既有 `confirm_published` 状态机将同一 draft 从 `unknown` 确认为 `published`，external ID 写入，manual resolution=1 | DB 与远端事实对齐，无第二次 publish |
| 2026-07-17 | final QZone UI + sidebar runtime QA | 页面显示“已发布 1”；`calendar_context` runtime disabled 时“生日祝福”隐藏，恢复 enabled 后菜单恢复 | 插件专属一级侧栏跟随真实运行态；最终插件保持 enabled |
| 2026-07-17 | final runtime/DB read-only check | schema v5；`PRAGMA quick_check=ok`；counts=`{published: 1}`；所有 actionable/unknown 队列为 0；Bot running；NapCat identity/restart unchanged | 任务终态稳定，live gate 继续 fail-closed |

## Rollback

- Frontend：恢复本任务涉及的 view/menu helper 与 `admin/static` 构建产物。
- QZone config：恢复当前 plugin override（enabled + dry-run + live=false + empty allowlist）后 bot-only recreate。
- Code/profile：恢复旧 image tag `omubot-bot:qzone-prelive-20260717`；当前部署 tag 为 `omubot-bot:qzone-live-20260717-v2`。保留 QZone DB 审计，不自动删除远端内容。
- 备份：`/app/storage/backups/qzone-live-20260717-215117-prepublish`，包含一致 SQLite backup、config backup 与 manifest。

## Current next step

本任务无剩余执行项。后续若要再次真实发布，必须获得新的显式授权、创建新的已审核 draft，并捕获一份新的原始 publish response 生成可验证 attestation；不得重发本 draft、不得从只读 feed 伪造 fixture、不得解锁内置 profile。
