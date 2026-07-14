# 现有插件审计整改最终完成审计

## Objective

对两轮原始插件审计的全部整改范围做 requirement-by-requirement 完成证明：第一轮 ManifestV3 13 Important、2 Minor、1 Decision，以及第二轮角色/指令/业务 10 Important、4 Minor、3 Decision，共 33 项。不得用 tracker 勾选或历史通过记录替代当前代码、专门测试、静态门禁与运行态证据；任何未证明项继续修复。

## Status

- mode: task
- status: complete
- started_at: 2026-07-14 CST
- completed_at: 2026-07-15 CST
- current_step: 两轮 33 项矩阵、三轮 closure review、D1 同模式项、Style tick 运行期预算缺口、本机门禁、最终镜像与公开受限群固定窗均已闭环
- next_step: none；本次范围随本地交付提交固化，后续只剩 push 与远端 CI，不属于本机运行态完成声明
- deployment: image `e31c2a630cd355a9a7e259f471c21e9ebb2d31e1610965d5a8512d08a044184d`（tag `omubot-bot:plugin-closure-style-tick-final-20260715`）/ container `41a5346c3278...` / restart=0 / OOM=false
- rollback: 精确上一版 tag `omubot-bot:pre-style-tick-fix-20260715`=`671078e6bf6c51f4e824b4ef1e23139c02063609b9a9f6936354fcad955f533d`；只允许 bot-only recreate，NapCat 不得 restart/recreate/down

## Authoritative Sources

- `docs/tracking/existing-plugin-manifest-audit-2026-07-13.md`
- `docs/tracking/existing-plugin-manifest-remediation-2026-07-14.md`
- `docs/migrations/existing-plugin-manifest-remediation-2026-07-14.md`
- `docs/tracking/existing-plugin-role-command-bug-audit-2026-07-13.md`
- `docs/tracking/existing-plugin-remediation-2026-07-13.md`
- `docs/migrations/existing-plugin-remediation-2026-07-13.md`

## Completion Gates

1. 33 项分别具备当前实现证据和直接行为/合同测试，不以“未发现问题”或历史 `[x]` 代替。
2. 两条独立复核分别覆盖 ManifestV3 与角色/指令/业务范围，并列出 PROVEN / WEAK / MISSING。
3. Manifest schema/repository/typed gates、插件与 Admin 聚合、前端 typecheck/build、full pytest 当前工作树全绿。
4. 运行镜像、PluginBus/Index/capability health、公开受限群零出站与 NapCat 身份证据仍有效。
5. 文档、迁移清单、ACTIVE、maintenance log 与真实完成范围一致；原未跟踪的 workflow/schema/checker/tests 显式纳入本地交付提交，远端 CI 尚未执行，不伪报已发布。

## Workstreams

| Workstream | Scope | Writer | State |
| --- | --- | --- | --- |
| C-MANIFEST | 第一轮 13I/2M/1D 当前证据复核 | independent reviewers + root | complete_16_proven |
| C-ROLE | 第二轮 10I/4M/3D 当前证据复核 | independent reviewers + root | complete_17_proven |
| C-INTEGRATION | 33 项总矩阵、门禁、缺口修复、运行与文档收口 | root | complete |

## ManifestV3 Completion Matrix

| Original requirement | Verdict | Current evidence |
| --- | --- | --- |
| I-P4 三套开发规范冲突 | PROVEN | canonical V3 docs + repository gate；文档示例与 23 manifests 由 shared parser 校验 |
| I-P2 runtime schema 门禁 | PROVEN | `PluginManifestV3`、checked-in schema、pre-import loader；manifest model/schema/discovery tests |
| I-P2 min Omubot version 只展示 | PROVEN | discovery/register compatibility fail-closed；manifest V3 contract tests |
| I-P2 public `PluginManifest` 分叉 | PROVEN | canonical alias/public export tests，不再生成旧清单 |
| I-P2 三套 config 路径/默认语义 | PROVEN | manifest-resolved Store/Effective/Admin truth；config/effective contract tests |
| I-P2 identity/duplicate 无结构门禁 | PROVEN | directory/name/runtime duplicate fail-closed；registration/index tests |
| I-P2 required/optional 未入完整合同 | PROVEN | typed model、Bus、Index、Admin/UI；optional missing/disabled/version/startup/recovery health tests |
| I-P2 `context` system whitelist 不一致 | PROVEN | canonical system whitelist/toggle policy tests |
| I-P3 Food→web_search required 且恢复不可逆 | PROVEN | optional manifest + required recovery/toggle tests |
| I-P3 Food/Memo 无 owner task | PROVEN | owned task + failure retrieval + cancel/await lifecycle contracts |
| I-P3 optional capability 只靠 priority/shared ctx | PROVEN | explicit optional graph、degraded truth、runtime recovery；dependency/Admin tests |
| I-P3 Vision 恒 enabled/healthy | PROVEN | real client unavailable/idle/failed/healthy/empty/recovery telemetry；Admin list/detail tests |
| I-P3 Food startup 改治理字段 | PROVEN | effective config/state ownership business constraints tests |
| M-P1 `context` fallback metadata | PROVEN | explicit author/min fields + 23-manifest gate |
| M-P1 wiki sticker 版本漂移 | PROVEN | manifest-derived docs/CHANGELOG contract scan |
| D-P3 HistoryLoader locked 产品决策 | PROVEN | 已裁定并迁 `RuntimeConnectionPipeline` capability/status；application/history ownership tests |

Result: `16 PROVEN / 0 WEAK / 0 MISSING`.

## Role / Command / Business Completion Matrix

| Original requirement | Verdict | Current evidence |
| --- | --- | --- |
| I-01 capability state 双真值 | PROVEN | effective capability tests，disable 后无 LLM/Router 残留 |
| I-02 kernel/services 反向依赖 plugin domain | PROVEN | canonical calendar/service types + reverse-import scan；Style extraction 也已下沉 service |
| I-03 Dream 是无关能力总 cron | PROVEN | Calendar/Memory 独立 owner + lifecycle tests |
| I-04 Dream 运行但 Admin unavailable | PROVEN | typed handle publish/unpublish + Admin tests |
| I-05 command registry 陈旧快照 | PROVEN | prepare/commit/snapshot/restore 两阶段事务，register/unregister/toggle/bind/tool failure 全回滚 |
| I-06 群命令 hook-first 污染 | PROVEN | access/silent/mute 后 command-first；command-flow tests |
| I-07 affection zero 除零 | PROVEN | schema/model/runtime no-op-safe tests |
| I-08 Bilibili ep/ss 假支持 | PROVEN | real resolver/summary/trigger adapter tests |
| I-09 Food 否定变正向 | PROVEN | structured exclusions tests |
| I-10 Food 信任任意 LLM 输出 | PROVEN | membership/constraint/fallback tests |
| M-01 Echo 同文窗口不重开 | PROVEN | expired/completed rollover tests |
| M-02 `/debug save` 依赖 segment 顺序 | PROVEN | complete segment scan tests |
| M-03 子命令仅字面空格 | PROVEN | arbitrary-whitespace parsing tests |
| M-04 metadata/History/admin truth 不完整 | PROVEN | recursive command metadata、precise History filter、canonical effective admins；LLM authority 也为 level 4 |
| D-01 HistoryLoader plugin/stage | PROVEN | 裁定为 core connection stage，capability-only manifest/status |
| D-02 unknown slash 是否给 LLM | PROVEN | 裁定 command-owned consume/提示；污染负测 |
| D-03 `/food search` session/persistent | PROVEN | 裁定 persistent effective override + immediate apply/restart tests |

Result: `17 PROVEN / 0 WEAK / 0 MISSING`; total `33 PROVEN / 0 WEAK / 0 MISSING`.

## Test Ledger

| ID | Command / Input | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| C0 | repository gate + AGENTS/session/ACTIVE + full status + 两份原始审计/整改/迁移清单 | 真实仓库 `/Volumes/OmubotDisk/omubot`；原记录已关闭但工作树 266 项 dirty/untracked；原始范围精确为 33 项 | 另立完成审计，不重做实现历史；以当前状态逐项证明 | 2026-07-14 |
| C1 | command root/alias/pattern collision probes + `tests/test_command_registry_collision.py` RED | 当前实现 name/alias 为 warning+last-wins，重复 pattern 为 first-match；runtime enable 异常泄漏且 Bus 状态半切换 | Phase C-2 的 fail-fast/health error 确为真实缺口，不可用“当前 0 collision”代替合同 | 2026-07-14 |
| C2 | command collision/transaction TDD + 两轮独立 code review | 逐步补出 register/unregister/set-enable/bind、多 registry、rollback-primary、provider、subcommand、case/punctuation、pattern witness 共性缺口；单阶段 refresh 无法保证原子 | registry 升为 prepare/commit/snapshot/restore 两阶段协议；所有验证在 prepare，rollback 直接恢复 active snapshot | 2026-07-14 |
| C3 | command/Bus/Toggle/Admin targeted | `196 passed`；另两阶段 transaction/validation 专测 `15 passed`；Ruff 与 scoped Pyright 0 | command fail-fast、旧快照、Bus/plugin/health/persistence、联合 tools+commands rollback 均有当前行为证据；待最终 reviewer 收口 | 2026-07-14 |
| C4 | `tests/test_effective_admin_access.py` + 四消费者接线 | 纯 helper 与结构/Command/GroupAdmin 合同 `69 passed`；四处不再直接读取 NoneBot driver/superusers；`/authority` 也使用 effective set | migration 的 one effective admin predicate 已从“集合语义相似”升级为单一服务真值 | 2026-07-14 |
| C5 | typed-boundary RED + gate | 加入 `plugins/affection/plugin.py`、`plugins/group_admin/plugin.py`，目标 36→38；`bash scripts/check-typed-boundaries.sh` 为 0 errors/0 warnings | Affection 已知 override 类型错与同型 GroupAdmin 边界均修复并进入持续门禁 | 2026-07-14 |
| C6 | final reviewer 复现 unhashable registry、tool provider、regex、LLM authority、Tool ABI | 5 Important 均有独立最小复现；新增 RED 初始 `2 failed/8 passed` 与 `2 failed/7 passed` | 历史 GREEN 不足，进入补强实现 | 2026-07-14 |
| C7 | command regex/transaction/tool rollback 聚合 | `52 passed`；lookaround/backreference/conditional fail-closed；unhashable identity rollback 与 provider owner error 通过；Ruff/Pyright 0 | command registry 两阶段协议与联合 rollback 闭环 | 2026-07-15 |
| C8 | effective admin + canonical Tool ABI | admin runtime wiring `18 passed`；Tool 相关 `37 passed`；services compatibility re-export，插件 cast 清零；typed targets 38→42 | NoneBot-only superuser 的自然语言 authority 与跨层 Tool 类型闭环 | 2026-07-15 |
| C9 | Chat debug command owner 迁移 | ownership/旧行为/admin tests `43 passed`；Chat command_count=0，debug_commands runtime 显示 4 roots | `/debug`、`/authority` 与 handlers/manifest 权限归唯一 ops owner | 2026-07-15 |
| C10 | Slang/Style learning owner + Style→Admin 反向依赖 | extraction 实现下沉 `services/style/manual_extract.py`；两插件共用 coordinator；相关 `66 passed`，Ruff/Pyright 0 | 周期提取与 Admin extract-all 共用 lock/timeout/shutdown owner | 2026-07-15 |
| C11 | Manifest parity + lifecycle workflow | description/显式空 dependency parity 负测；8 个类描述对齐；真实 Memory stop cancellation；`17 passed`、23 manifests validated | parity 与 ownership CI 不再假绿 | 2026-07-15 |
| C12 | optional degraded truth + Vision positive truth | dependency 聚合 `23 passed`；Vision `7 passed`；Bus/Index/Admin/system health 与 empty/failure/recovery 语义通过 | 第一轮最后两个 health 缺口闭环 | 2026-07-15 |
| C13 | 当前工作树集成门禁 | plugin/command/application 聚合 `405 passed`；typed boundary `44 targets / 0 errors`；manifest gate 23 validated | targeted/static 全绿 | 2026-07-15 |
| C14 | current full pytest | `3345 passed / 17 skipped / 161 warnings` in 50.35s | warnings 仅既有 aiohttp/NoneBot deprecation；当前工作树全量 GREEN | 2026-07-15 |
| C15 | frontend | `vue-tsc --noEmit` 通过；Vite `4392 modules` build 成功 | 当前 SPA 构建 GREEN | 2026-07-15 |
| C16 | bot-only deploy/runtime/zero-outbound | image `d157e014...`、container `45b560...`、16 runtime source SHA 0 mismatch；23 entries/21 Bus enabled、command registry 13/0/0、8/8 tasks；UTC 17:20:11-17:23:11 NapCat 23 restricted inbound、0 group outbound、0 send_group_msg、0 error，bot 0 send/error，Protocol send actions 0；NapCat `19f6cf...` restart=0 | 本机实现、镜像与公开受限群运行验收完成 | 2026-07-15 |
| C17 | final closure review + Unicode probe + Style/archive/manifest source trace | 发现 4 Important：Python Unicode `re` 与 interegular 对 `\\w` 等语义不等价，2 个中文 collision RED 均 DID NOT RAISE；Style tick 读不存在的 `ctx.message_log` 而生产只注入 `msg_log`；manual extract timeout/cancel 后 `conversation_scan_runs` 残留 `running/finished_at NULL`；manifest checker 从 manifest 单向枚举，漏 package orphan `plugin.py` 与 root legacy `plugin.py` | 撤回 complete；三线 TDD 修复，完成后必须重跑全部门禁与部署验收 | 2026-07-15 |
| C18 | D1 same-pattern scan: `read_scan_batch` / `finish_scan_batch` 与 cancellation cleanup | Style 之外仅 MemoryConsolidator 消费 archive scan batch；其 cancel 测试只闭合 memory run，自身当前 archive batch 仍会残留 `running`。Slang 使用独立 extraction run，已有 timeout/cancel 闭合测试，不属于 archive batch 路径 | 将 Memory archive batch cancellation 纳入同一生命周期修复；补 status/finished_at/cursor 与 repeated-cancel 证据 | 2026-07-15 |
| C19 | 第一轮独立 closure after C17/C18 fixes | 再发现 5 Important：Unicode `IGNORECASE` case-fold 假阴性；Style manifest 缺 `tick` 导致生产不可达；archive read start-commit handoff 残留 running；success finish/cursor commit 取消歧义；Memory owner finish 前过早 `completed=True` | 补 Python case-fold 超集、真实 Bus tick、archive start cleanup/finish commit barrier、owner finish 时序；均先 RED 后 GREEN | 2026-07-15 |
| C20 | 第二轮 cancellation/实际调用链复核 | 再发现 3 Important：burst repeated cancel 只清一次 cancelling debt；scanner compatibility wrapper 吞 commit error；Memory owner start commit 后取消留 running | 按 barrier 期间 cancelling 增量清零；wrapper 改 fail-fast；owner start 独立 task handoff 后 failed cleanup 再传播原取消 | 2026-07-15 |
| C21 | 第三轮最终只读 closure review | 主线 `63 passed`；command/style `26 passed` + `runtime_true_fsm_false=[]`；archive 全文件 `17 passed`。真实 SQLite 交错/突发 repeated cancel、read handoff、wrapper error、Memory owner start/finish 均通过 | `0 Critical / 0 Important`，前两轮共 8 Important 全部关闭 | 2026-07-15 |
| C22 | 最终本机代码/静态/full/frontend 门禁 | 统一 targeted 最高 `214 passed`，补强 focused `85 passed`；Ruff clean、scoped Pyright 0、typed boundary 44 targets / 0 errors、23 manifests、strict layout、diff check；full `3369 passed / 17 skipped / 161 warnings`；Vue typecheck 与 Vite 4392 modules build 通过 | 本机工作树 GREEN；待新镜像与运行固定窗，不沿用旧 image `d157e014...` 作为补强完成证据 | 2026-07-15 |
| C23 | 补强代码首轮 bot-only 镜像与延长运行观察 | image `671078e6...`、container `453294b...` 启动/OneBot/source/Admin/3 分钟零出站均通过；但延长到首轮周期 tick 后累计 7 次 `style on_tick` 5 秒超时，Admin runtime errors=9 warnings | 中间镜像不可作为最终完成证据；真实生产链揭示 Style 同步等待最长 120 秒提取与 PluginBus 5 秒 hook 预算冲突 | 2026-07-15 |
| C24 | Style tick 运行缺口系统化调试 + TDD + review | RED 精确证明长提取阻塞 Bus；改为插件自有单后台 job、重复 tick 去重、shutdown cancel+await，真实 coordinator run 离开 running 并写 finished_at；Style 11 passed、生命周期聚合 126 passed、Ruff clean、Pyright 0；独立审查清单 `0 Critical / 0 Important`；full `3372 passed / 17 skipped / 161 warnings` | 根因关闭，不放宽全局 Bus 预算；先前 3369 证据已由生产代码变更后的 3372 取代 | 2026-07-15 |
| C25 | 最终 bot-only rebuild/runtime/zero-outbound | image `e31c2a...`、container `41a5346...`；9 个关键 source 0 mismatch，镜像内 23 manifests + strict layout；23 entries enabled、Index 23/21+2、15 commands/0 patterns；Style 首轮 extract completed，hook max 5.47ms、0 error/timeout；Background 8 persistent running + 1 completed、0 failed/backoff；Protocol 30 ok/0 failed/0 pending、0 send/poke。UTC 18:59:25-19:02:25 受限公开群自然 inbound 6（625618470=4、717096900=1、805836168=1），NapCat/OneBot group outbound、`send_group_msg`、bot/NapCat error 均 0 | 本机工作树、最终镜像与固定窗完成；仅余两条既有外部 QQ 图片 404 warning 与 2 optional DB missing，不是本轮回归 | 2026-07-15 |

## Constraints

- 禁止 `docker compose down`，禁止 restart/recreate NapCat。
- 除非发现真实缺口，本审计不改业务代码、不重新部署。
- 高脏工作树禁止 reset/checkout、禁止 `git add -A`；所有结论必须区分 tracked/untracked 与 local/runtime/remote CI。

## Completion

两轮 33 项当前矩阵为 `33 PROVEN / 0 WEAK / 0 MISSING`，三轮 closure review、D1 同根项与最终运行期 Style tick 预算缺口均已关闭；第三轮静态复核及 Style 补丁复核均为 `0 Critical / 0 Important`。最终 image `e31c2a...` 已完成本机 source、Admin、OneBot、周期任务与公开受限群固定窗验收，NapCat 全程保持原容器且 restart=0。本次完成范围、workflow/schema/checker 与回归 tests 已显式纳入本地交付提交；工作树仍保留其他项目和本机产物，尚未 push，远端 CI 尚未获得这些门禁；本结论是本机运行态完成，不得描述为远端已发布。
