# 现有插件第二轮审计全量整改

## Objective

落实 `existing-plugin-role-command-bug-audit-2026-07-13.md` 的全部整改项：修复 10 项 Important、4 项 Minor，并对 3 项 Decision 采用明确、可测试的产品语义；同时保持所有 blocked/off/silent_learn/mute 公开群零出站，且不重建 NapCat。

## Status

- mode: task-bug
- status: audit_complete_remediated_deployed
- checkpoint_2026_07_14: R-CMD/R-BIZ 主缺陷已 GREEN；R-OWN 的 Dream/Calendar/Memory 与 D-01 stage 已 GREEN，进入全量门禁。代理线曾连续 429，均未产生未审查 production 改动。
- agent_outage_2026_07_14_0602: R-OWN RED 连续 30m 无成功进展，标记 `failed_after_30m` 并停止该 canonical workstream 的自动重试；无 surviving artifact，所有权 scope 仍未完成。R-CMD/R-BIZ 因已有独立 RED 可继续等待 GREEN 容量。
- business_red_complete_2026_07_14: 两个业务 RED 文件共 12 assertion failures；新增 D-03 on/off 均证明 `_search_enabled` 即时变化但 PluginConfigStore 未持久化，新实例恢复旧值。Bilibili hint 断言已与既有 BV 合同对齐。`plugin_remediation_biz_green_r1` 单代理运行中。
- business_green_checkpoint_2026_07_14: Affection zero、Food negation/candidate/exclusion/recent/dislike/persistence、Echo rollover 已落盘并由 root 复验；业务 RED 10 passed / 2 failed，既有 Affection/Food/Echo 58 passed。仅 Bilibili ep/ss 未完成，已重建单文件 replacement。
- command_green_checkpoint_2026_07_14: R-CMD 22/22 新合同 GREEN；命令/PluginBus/Toggle/Admin 基线合并 158 passed；Ruff/Pyright 通过；Vue typecheck 与 build 通过。旧 unknown=false 用例已按锁定 D-02 迁移为 command-layer consumed。
- business_green_checkpoint_2_2026_07_14: R-BIZ 新增+既有 Affection/Bilibili/Food/Echo 共 152 passed；Bilibili 单文件 0 Pyright，Food 单文件 0 Pyright。
- business_contract_green_2026_07_14: Food Web-search 结构化候选合同新增 1 例并通过；网络结果只作为补充，本地候选仍是唯一合法输出集合。Bilibili 真实 Bangumi resolver 新增 ep/ss 2 例通过，直接 mock `bilibili_api.bangumi.Episode/Bangumi`，未 mock 总 resolver。
- ownership_green_checkpoint_2026_07_14: effective capability、Schedule owner、反向 import、Calendar canonical service、Dream typed handle、Calendar birthday owner、MemoryConsolidatorLifecycle 与 History debug 精确边界已落盘；所有权 selector 10 passed、相关 Dream/background/calendar/birthday/memory/Admin 回归 161 passed，scoped Ruff/Pyright 通过。旧 event-boundary 合同已迁到 MemoryConsolidatorLifecycle。
- history_stage_green_2026_07_14: HistoryLoader 已迁为 `services.history_backfill.run_history_backfill`；RuntimeConnectionPipeline 在 hooks 前运行并发布 `{status,runs,last_error}`；PluginBus discovery 跳过 `capability_only` manifest；旧 helper 通过兼容导出保留。History/构建/贴纸/所有权相关 71+45 例通过。
- final_boundary_green_2026_07_14: Schedule 删除 legacy Calendar 生产 import/fallback，缺 provider 时显式 neutral；旧模块收缩为 canonical data/type compatibility facade。History pipeline status 同时接入 service health 与 manifest-only plugin list/detail。独立 RED 13 failures 后 GREEN 13 passed，扩展回归 284 passed，scoped Ruff/Pyright 0。
- deployment_green_2026_07_14: rollback tag `omubot-bot:pre-plugin-remediation-20260714` 已锁定旧 image `c2831e16c135...`；新 image `c446ff2a2f67...` 仅替换 `qq-bot`，History/OneBot/Admin/后台任务与固定零出站观察窗均通过，NapCat 身份完全未变。
- started_at: 2026-07-13 CST
- completed_at: 2026-07-14 CST
- current_step: complete；第二轮 10I/4M/3D 已实现、复核、部署并完成运行验收
- next_step: 本 tracker 已关闭；第一轮 ManifestV3 平台合同余项保持独立，未经另行批准不自动实施
- source_audit: `docs/tracking/existing-plugin-role-command-bug-audit-2026-07-13.md`
- migration_checklist: `docs/migrations/existing-plugin-remediation-2026-07-13.md`

## Locked Product Decisions

用户已批准“全部修复”，本轮据当前架构作以下收敛，不再把 Decision 留作未完成项：

1. **HistoryLoader 迁入核心连接阶段。** 历史回填当前是 locked 且承担启动一致性，不再伪装成可切插件；迁入 `RuntimeConnectionPipeline`，以 stage/capability status 暴露。
2. **未知 slash 根命令不再交给 LLM。** 通过访问/静默/mute 门禁后，`/unknown` 由命令层消费并返回统一提示；静默公开群仍在上游零出站返回。
3. **`/food search` 采用持久设置并即时生效。** 管理员聊天控制与 Admin 使用同一 effective config 真值；重启后不回退到旧 session 值。

## Scope Checklist

### Important

- [x] I-01 affection/schedule effective capability state 与 PluginBus state 收敛；disable 后无 LLM/Router 残留。
- [x] I-02 移除 kernel/services → plugins 反向 import；`calendar_context` 成为唯一日历 API。
- [x] I-03 Dream 只拥有 DreamAgent tick；birthday 与 memory consolidation 回归各自 owner。
- [x] I-04 Dream 发布/回收 typed runtime handle，Admin status/trigger 与真实运行态一致。
- [x] I-05 Command registry 在 startup 后原子建立，toggle/dependency transaction 同步替换，dispatch 校验 owner state。
- [x] I-06 群命令 fast path 位于公开群/访问/mute 门禁之后、所有有副作用 hooks 之前。
- [x] I-07 Affection `score_increment=0` 不再除零，schema/model/runtime 合同一致。
- [x] I-08 Bilibili ep/ss 链路具有真实 resolver/summary/trigger，或不再宣称支持；本轮选择补真实解析。
- [x] I-09 Food 正确理解“不辣/不要辣”等排除语义。
- [x] I-10 Food 校验 LLM 输出属于候选且满足 exclusion/recent/dislike，失败走确定性本地 fallback。

### Minor

- [x] M-01 Echo 同文窗口过期或完成后能重开新链。
- [x] M-02 群内 `/debug save` 与 image/reply/text segment 顺序无关。
- [x] M-03 子命令 Tab/换行等任意空白参数不丢失。
- [x] M-04 Admin 命令元数据补 aliases/subcommands/继承门禁；`pattern`/help 合同一致；HistoryLoader 精确过滤 debug 命令；admins/SUPERUSERS 统一。

### Decisions

- [x] D-01 HistoryLoader 迁 `RuntimeConnectionPipeline` 并删除旧 PluginBus 可切换假象。
- [x] D-02 未知 slash 根命令统一由 command layer 消费，不进入普通聊天/状态写入。
- [x] D-03 `/food search` 持久且即时生效，与 Admin effective config 一致。

## Workstreams

| ID | Scope | File ownership | State |
| --- | --- | --- | --- |
| R-CMD | 命令 fast path、registry lifecycle、解析/权限/metadata/unknown command、Admin UI | `services/command.py`, `kernel/router.py`, `services/plugin_toggle.py`, commands Admin API/UI 与专属测试 | verification_green |
| R-BIZ | Affection zero、Bilibili ep/ss、Food 两缺陷与 search 持久语义、Echo reset | 对应 plugin/service/config schema 与专属测试 | verification_green |
| R-OWN | Dream handle/tick owner、affection/schedule state、calendar canonicalization、HistoryLoader stage、反向依赖 | bootstrap/capability/calendar/dream/history lifecycle 与专属测试 | verification_green |
| R-INT | tracker、冲突整合、D1/D3/D4、全量验证、维护日志、部署验收 | root only | deployed_complete |

Scope boundary：本 tracker 只证明第二轮 10 Important、4 Minor、3 Decision。第一轮 manifest 审计的共享 parser/schema、runtime version gate、剩余依赖/task owner、Vision probe 与 CI 合同仍是独立余项，不能随本轮自动清零。

共享工作树规则：每条实现线一名 writer；写前必须检查目标文件既有 diff，不覆盖前期中期架构改动。RED 作者与 GREEN 实现者使用不同代理。

## Verification Gates

1. 每个缺陷必须经历可观察 RED → 最小 GREEN → REFACTOR，RED 不是 import/collection error。
2. targeted tests → 命令/插件子系统 → full pytest。
3. `uv run ruff check`、`uv run pyright`。
4. Admin：`./node_modules/.bin/vue-tsc --noEmit`、`npm run build`。
5. D1：扫描所有 slash fast path、plugin domain reverse imports、LLM candidate trust、window rollover 同模式。
6. D3：逐项核对旧 owner/入口是否移除、兼容入口/数据是否迁移、Admin/manifest/docs 是否同步。
7. D4：内存/HTTP/运行态外部可观察验证；blocked/off/silent_learn/mute 群负向验证；只允许 bot-only rebuild，NapCat 不动。

## Rollback

- 代码按 R-CMD/R-BIZ/R-OWN 文件边界独立回退；不触碰数据库数据迁移。
- 旧镜像已标记为 `omubot-bot:pre-plugin-remediation-20260714`（`c2831e16c135...`）；回滚时将该镜像重标为 `omubot-bot:latest`，再执行 `docker compose up -d --no-deps --force-recreate bot`。
- 本轮没有数据库 schema/data migration；镜像回滚不需要恢复数据。禁止 `docker compose down`，禁止 restart/recreate NapCat。

## Test Ledger

| ID | Command / Input | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| R0 | 连续性恢复：读取 AGENTS、session state、ACTIVE、审计 tracker、scoped git status | 审计完成但整改源码尚未开始；工作树存在大量前期 dirty/untracked；真实仓库为 `/Volumes/OmubotDisk/omubot` | 建独立整改 tracker，严格分文件与先 RED 后 GREEN，不重跑审计 | 2026-07-13 |
| R1 | R-CMD baseline：`tests/test_command.py tests/test_plugin_bus.py tests/test_plugin_toggle_policy.py tests/test_plugin_mute_gate.py tests/test_admin_api.py` | 136 passed；新增 3 文件 22 collected，初始 8 passed / 14 assertion failures；Ruff/Pyright 绿 | 命令 registry/order/unknown/segment/whitespace/admin/metadata/help 均已形成 RED；registry 返回值与 D-02 有冲突，已交原测试作者修正 | 2026-07-14 |
| R2 | R-BIZ core baseline + `tests/test_plugin_remediation_business_core.py` | 基线 Affection 32、Food 2、Echo 24 passed；新文件 4 assertion failures，Ruff 绿 | 稳定锁定 zero increment、Food 不辣反筛、Echo expired/completed 两类 rollover；Bilibili/I-10/D-03 仍在补测 | 2026-07-14 |
| R3 | 三轮 R-OWN RED agent spawn/reconnect/rebuild | 均在启动或子 fan-out 阶段遭 429；无测试文件、无 production 写入 | 保留 R-OWN 独立工作流并继续重建，主线不把它静默算完成 | 2026-07-14 |

| R4 | Root 在中断后复跑两组 RED | Business 10 failed；Command 15 failed / 7 passed；全部为行为 assertion，未发现 collection/import error；production mtime 无本轮变化 | RED 证据完整；Bilibili hint exact-dict 过度约束与 D-03 缺测试待 test finalizer 修正 | 2026-07-14 |
| R5 | `pytest` R-CMD 3 新文件 + command/PluginBus/toggle/mute/Admin 基线 | 158 passed；唯一旧 unknown=false 冲突按锁定 D-02 更新后全绿 | registry owner 刷新、fast path、未知 slash、任意 segment/whitespace、权限与 metadata 形成 GREEN；待独立 review | 2026-07-14 |
| R6 | R-BIZ 两新文件 + Affection/Bilibili/Food/Echo 既有测试 | 152 passed；Bilibili/Food 单文件 Ruff/Pyright 均绿 | 指定业务缺陷 GREEN；Web-search candidate truth 与真实 resolver 单测仍缺，不得据此勾全量完成 | 2026-07-14 |
| R7 | R-CMD 静态/前端：Ruff、Pyright scoped、`vue-tsc --noEmit`、`npm run build` | 全绿；Vite 4392 modules transformed，build 成功 | Admin 命令 aliases/subcommands/effective gates 可构建；最终仍需 full frontend/full Pyright | 2026-07-14 |
| R8 | R-OWN Dream/Calendar/Memory selector + broader subsystem + scoped static | selector 10 passed / 8 deselected；Dream/background/calendar/birthday/memory/Admin 161 passed；Ruff 通过、Pyright 0 errors、diff check 通过 | foreign owners 已从 Dream 拆出；application lifecycle 接线与旧 owner 测试迁移仍待 root 完成 | 2026-07-14 |
| R9 | `pytest tests/test_plugin_remediation_ownership_lifecycle.py tests/test_plugin_remediation_ownership_history.py -q` | 18 passed / 5 assertion failures | lifecycle 合同已 GREEN；D-01 RED 稳定且仅剩 PluginBus/package/pipeline 三个迁移边界 | 2026-07-14 |
| R10 | Application/Memory owner migration + History stage + old build contracts | 45 targeted passed；History/贴纸/构建/PluginBus/Admin 合计 71 passed；Ruff/Pyright 0 errors | Memory lifecycle is now an application component; History is manifest-only core stage; old Dream event-boundary contract migrated | 2026-07-14 |
| R11 | Food Web candidate contract + Bilibili resolver contract | Food/Bilibili business constraints 13 passed；Bangumi resolver 2 passed；production scoped Ruff/Pyright 0 errors | Web context cannot bypass local candidate legality; ep/ss resolver directly exercises real adapters | 2026-07-14 |
| R12 | 独立 final-boundary RED：Calendar provider/package/manifest + History service/plugin health | 13 assertion failures；无 import/collection/TypeError；前两次 replacement 被失效 `/Users/...` 工作区带偏，r3 在真实仓库交付 | Calendar 双真值与 History 静态健康均形成可观察 RED；未伪记 r1/r2 成功 | 2026-07-14 |
| R13 | final-boundary GREEN + Calendar/Schedule/Admin/connection 扩展回归 + scoped static | 新合同 13 passed；扩展 284 passed；旧 07-07 七夕断言迁到 canonical 母亲节规则后 284/284；Ruff passed、Pyright 0、dependency subset 13 passed、diff check clean | Calendar production consumer 清零，compat 只读 canonical 数据；History status 在 service/list/detail 同源可见 | 2026-07-14 |
| R14 | 最终聚合、前端与 full pytest | remediation/zero-outbound 154 passed；Vue typecheck 通过；Vite 4392 modules build；full pytest 3205 passed / 17 skipped | 第二轮行为面与前端门禁 GREEN；warning 为既有 aiohttp/NoneBot 弃用和 aiosqlite 测试线程收尾 | 2026-07-14 |
| R15 | full static baseline + D1/D3/D7 | Ruff 177 个既有错误，仅 coursework/research/IPv6；Pyright 353 errors / 1 warning，692 files，既有债；本轮 scoped 0/0。kernel/services 反向 plugin import=0，legacy Calendar production consumer=0，现行 stale owner 文案=0；stash empty、diff check clean、AppleDouble=0，Docker excludes NapCat/config/storage/tmp/tests/research/coursework | 全仓基线未因本轮变绿，但未新增 scoped 债；镜像输入面与 NapCat 红线已审计，待 review 与部署 | 2026-07-14 |
| R16 | 独立最终 review + 修复 + closure review | 首轮 0 Critical / 2 Important / 2 Minor：package API break、compat 第二 service/data、普通生日 energy 漂移、capability_only 文档；逐项新 RED 3 failures 后修复。关闭复核 0/0/0，56 passed，scoped static 0/0 | package canonical exports 恢复；Calendar facade 只委托 canonical runtime provider；Mood 语义恢复；D3 例外同步 | 2026-07-14 |
| R17 | review 修复后 full pytest | 3207 passed / 17 skipped / 166 warnings | 最终行为门禁 GREEN；warning 仍为既有依赖弃用与 aiosqlite 测试线程收尾 | 2026-07-14 |
| R18 | D7 + rollback tag + `dot_clean .` + `docker compose build bot` + `docker compose up -d --no-deps --force-recreate bot` | diff check clean、stash empty、AppleDouble 0；build 18/18、context 2.96 MB、镜像内 plugin layout 通过。旧 `fe045dee.../c2831e16...` 被新 `24707fce.../c446ff2a...` 替换，restart=0 | 只替换 `qq-bot`；rollback tag 已验证，构建与部署成功 | 2026-07-14 |
| R19 | Admin/OneBot/History/后台任务/NapCat/公开群固定窗运行验收 | Admin 200；History `success/runs=1/last_error=""`，service=ok、list/detail=healthy；OneBot connected，protocol trace 47/47、0 failed/pending/send；后台任务 8/8 running。UTC 04:48:04.596-04:56:09.590 内 NapCat 群入站 74、群出站 0、`send_group_msg` 0、error 0；5 个已配置 silent 群和 1 个 whitelist 外群有自然入站。NapCat `19f6cf...` 的 image/Created/StartedAt/restart=0 完全不变 | 第二轮整改已部署且公开受限群固定观察窗零出站；仅有既有图片 404 warning 与 2 个 optional DB missing，无 runtime error | 2026-07-14 |

## Completion

第二轮插件审计整改已完整关闭：实现、独立 review、全量验证、bot-only 部署与运行验收均有证据。不得把本结论扩大为第一轮 ManifestV3 平台合同已闭环；后者仍见 `docs/tracking/existing-plugin-manifest-audit-2026-07-13.md`，需要独立审批与 tracker。
