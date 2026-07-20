# Bot 记忆、措辞与图片行为修复迁移清单（2026-07-19）

> 状态：代码与离线验收完成；后续已提交并完成 bot-only 生产部署与 Style 授权清理复验
> 数据边界：实现阶段未写生产 SQLite；后续仅按独立授权精确清理 Style 数据。未触 QQ/QZone，NapCat 未重启或重建
> 基线：`dcc75aaeb7f08d2e8b02f8cf0522bb48f204b97a`
> 部署：commit `40a8e32faede3cf1a8b9152967c0dd98ecbb6b55`；image `sha256:d89121d98ef0...`；extractor SHA-256 `ef96f86b60985d25a1ecbd45671e5dff22c57b9157bc96fe51217709a7147f7f`

## 迁移目标

将 2026-07-18 审计确认的五条行为链迁移到统一契约：

1. 群聊记忆保留主体、来源群与 visibility；同群只召回当前发言人允许在本群使用的 user 卡。
2. 图片身份纠正按完整 SHA-256、纠正用户与可见 scope 持久化，不再写成 preference/fact 卡。
3. 视觉 observation 只走结构化 system side-channel，不再伪装成用户正文，也不得进入 Style human evidence。
4. persona repair / humanization rewrite / guardrail 之后统一回到最终 visible floor，拒绝空白、省略号和纯标点。
5. phrase-family 使用最近 12 条 assistant-only 历史，用户引用文本不作为 Bot 自重复证据。

## 旧 → 新回归清单

| 旧入口/行为 | 新文件/行为 | 路由 / 菜单 / API 影响 | 验收与回滚 |
| --- | --- | --- | --- |
| `ReplyContext` 只有纯文本 `user_msg` | `kernel/types.py` 增加脱敏 `visual_evidence` 与 `trigger_mode` | 无 HTTP 路由、Admin 菜单或外部 API 变化 | `test_visual_identity_reply_pipeline.py`；回退字段及 `_fire_post_reply` 参数 |
| `LLMClient._fire_post_reply()` 丢失图片 side-channel / trigger | `services/llm/client.py` 仅复制 hash、intent、observation、identity、OCR、summary、provenance；不复制 path/base64/bytes | PluginBus 内部 ABI additive；旧插件不受影响 | 真实 `chat()` trigger 测试 + privacy-safe 单测；回退 sanitization 与字段传递 |
| “这是高松灯”进入通用 Memo 抽取，可能成为偏好卡 | `plugins/memo/plugin.py` 对单图、`correction` trigger、明确命名句式走 `store_visual_correction()` 并跳过通用抽取 | 无外部 API；Memo hook 行为变更 | 实库测试证明 visual identity 可召回且 user card 为 0；错误 trigger、多图、错误 provenance、缺失 SHA 均不写 |
| 无稳定视觉身份存储 | `services/memory/visual_identity.py` 在 `storage/memory_cards.db` 增加 `visual_identities` 表 | `services/storage/catalog.py` 将 visual identity 声明为共享 client；无新 DB 文件 | fresh/legacy migration/store/visibility 测试；回退代码时 additive 表可保留 |
| `store_correction()` 接收但忽略 trigger/evidence，未来内部调用可绕过 parser | 写入点二次验证 `correction`、`evidence_count=1`、`visual_system`、同一 full SHA 与 `user_correction` provenance | 内部 API 收紧；可信后台若需直接导入应改走显式 `upsert()` 并单独授权 | 5 类无效授权上下文均 RED→GREEN；回退会恢复 defense-in-depth 缺口 |
| 实验主键 `(image_sha256, correcting_user_id)`，同用户跨群覆盖 | 主键 `(image_sha256, correcting_user_id, scope_key)`，scope 为 `group:<id>` / `private` / `global` | 无 API 变化 | legacy 两列主键原子迁移测试；异常 visibility 保留到 quarantine scope，召回 fail-closed |
| Router 只使用机器识别结果 | `kernel/router.py` 按完整 SHA 查询当前 user/group；人工纠正覆盖错误机器身份 | QQ 入站渲染内部行为变化；用户正文只保留中性图片占位 | 同用户同群正例、其他用户/群/私聊负例、错误机器身份覆盖测试 |
| 引用图 enrichment 超时会丢已保存 ref | Router 将 enrichment timeout 置于外层 image renderer timeout 之前；超时只降级证据 | 无外部 API；保留引用图 image_ref | recognizer/VL timeout 两个回归测试；回退 inner/outer timeout budget |
| `lookup_cards(scope, scope_id)` 可显式读取其他实体；query 会混入其他 global namespace | `services/tools/memo_tools.py` 只允许当前 user、当前 group、canonical `global/global` | Lookup schema/描述明确可见边界；越权请求返回无权 | other-user / other-group / noncanonical-global 负例；回退 `_scope_allowed`（不建议） |
| `update_cards` 可凭外部 card ID 修改其他实体或写 global | add/update/supersede/expire 均先校验当前 user/current group；global 仅可信后台可直接调用 CardStore | Update schema 仅暴露 user/group，描述与执行权限一致；handler 仍 fail-closed | 四类 mutation + global/other user/group 负例，外部卡内容与状态保持不变 |
| repair/rewrite 后不再执行 visible floor | 所有文本变换路径重新进入 `_finalize_visible_reply()` | 无路由/API 变化 | 真实 chat repair=`……`、rewrite=`...` 回归；回退会恢复已确认缺陷 |
| 视觉 marker / Bot 派生文本可污染 Style | Style extractor/manual path按 provenance fail-closed | 无 Admin/API 变化；未清理生产历史数据 | `test_style_visual_provenance.py`；历史数据清理需另行备份与授权 |
| 只比较紧邻上一条完整回复 | phrase-family 读取最近 12 条 assistant-only 历史 | 无路由/API 变化 | `test_phrase_family_dedup.py`；回退 dedup history ABI |

## SQLite 迁移语义

`VisualIdentityStore.init()` 在自己的连接内检查 `PRAGMA table_info(visual_identities)`：

- 表不存在：创建三列复合主键表与索引。
- 已是三列复合主键：幂等 no-op，仅补索引。
- 旧两列主键或缺少 `scope_key`：`BEGIN IMMEDIATE` 下重命名旧表、创建新表、复制记录、删除临时表并 commit。
- 旧记录无法形成合法 scope 时不删除，迁移到 `quarantine:<row>`；公开 lookup 不查询 quarantine，因此 fail-closed。
- 迁移失败时 rollback 并关闭未发布连接；composition root 启动失败会按 LIFO finalizer 关闭已创建资源。

实现与离线验收阶段没有打开生产 `storage/memory_cards.db` 的可写连接。当时要求未来部署前先创建可信备份，并在维护窗口内仅重建/recreate bot；后续部署已遵守该边界，NapCat 未重启或重建。

## 最终离线验收

- required Grok 只读终审：session `d329e4f7-c07d-433e-a4ec-271bfa92d33a`，completed child `019f7abd-c8e9-7fd1-8107-44ab395892c4`；0 Critical，旧 CardUpdate write-auth finding 判定 stale。
- Post-review hardening：视觉纠正授权上下文 5 类负例、Memo parser 4 类负例、Card tool schema/description、noncanonical global query 均已覆盖。
- 全仓：`5093 passed, 17 skipped, 189 warnings`。
- Scoped Ruff：pass；Scoped Pyright：`0 errors, 0 warnings`。
- 该离线验收阶段未执行 commit/push/deploy、生产 DB 写入、QQ/QZone 发送、Docker/NapCat 操作；后续授权动作见下节。

## 后续授权的部署与最小检查（已执行）

1. 部署前完成 `git stash list`、tracked/untracked 边界审计和 BackupService trusted backup。
2. 防再污染代码随 `40a8e32` 构建为 image `sha256:d89121d98ef0...`，只 recreate bot；未执行 `docker compose down`，NapCat 未重启或重建。
3. 当前 `qq-bot` ID `e95c0b9b...`，`GIT_COMMIT=40a8e32...`，restart=0、OOM=false；宿主与容器 `services/style/extractor.py` SHA-256 均为 `ef96f86b...f7f`。
4. Style focused pytest `49 passed`，Ruff pass，Pyright `0 errors, 0 warnings`；容器 runtime smoke 拒绝 visual/system evidence、接受普通 human 文本，manual extract 已调用同一 eligibility helper。
5. 生产 Style WAL-aware 只读复验：`quick_check=ok`，structured human remaining=0，cleanup revisions=73；当前 approved/pending/rejected=`10/1114/82`，清理后新增 10 条均为 eligible human evidence。
6. 未发送 QQ/QZone；未触发 Style 手工抽取或自动审批。Grok normal required-parallel 复核为 top-level `7a52dcb7-dcd2-45db-9a98-efa03c0fa8f4` + child `019f7d13-8eab-7ff3-b2be-30b071c84eeb`。

## 回滚

- 代码：逐文件反向回退本迁移清单对应 hunks，或回滚到上一 bot image；不 reset/clean/stash 用户 WIP。
- SQLite：`visual_identities` 为 additive。旧生产代码不会读取该表，可原样保留；不要为回滚主动删除表或记录。
- Style：实现阶段未改生产 Style 数据；后续清理可使用 trusted backup `pre-change-20260719-224533`，或按 SHA-256 `53355a9f79d64484b3c42f561a6705d8978fcd2ba37157800318b433ccd42b88` 的 0600 计划做行级恢复。整库恢复必须另行授权并 stop/start bot。
- 外部状态：未发送 QQ/QZone，NapCat 未重启或重建。代码回滚优先切回部署前 bot image，仅 recreate bot。

## 后续授权数据清理（2026-07-19）

用户后续授权生产 Style 污染清理。按当前 provenance fail-closed 合同，仅处理 `source_type=human` 且 evidence 自身 `raw_text` 命中结构化视觉/system marker 的记录；仅 context 邻近 marker 的 558 条候选全部排除。

- trusted backup：`pre-change-20260719-224533`，26 ok / 0 failed；style backup SHA-256 `9c7f8a57bb14733c7a481190d8906dd8865f532c22521d165a252acaaa80a47b`，quick_check=ok。
- cleanup plan：SHA-256 `53355a9f79d64484b3c42f561a6705d8978fcd2ba37157800318b433ccd42b88`，mode 0600。
- 73 条 evidence `human→system`；对应 73 条 expression `pending/approved→rejected`；73 条 revision；无物理删除。
- production counts：approved `12→10`、pending `1175→1104`、rejected `9→82`；post-write `quick_check=ok`，错误 structured human evidence remaining=0。
- 清理事务完成时容器未重启、restart=0，且当时尚未部署代码；这是清理阶段的历史事实。
- 后续授权已完成提交与 bot-only 部署：当前容器 `GIT_COMMIT=40a8e32...`，extractor SHA 与宿主一致。当前数据为 approved/pending/rejected=`10/1114/82`，`quick_check=ok`，structured human remaining=0；防再污染防线已经生效。
