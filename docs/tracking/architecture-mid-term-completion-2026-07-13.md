# Omubot 中期架构 M1-M6 完成收口

> 状态：complete
> mode: none
> 开始：2026-07-13 CST
> 完成：2026-07-13 CST
> 当前阶段：M1-M6 实装、独立复审、正式构建、bot-only 部署与运行验收全部收口
> 阻塞：无。保留一个不可追溯的历史证据缺口：生产 named-volume 三库迁移前精确行数与 pre-adoption 快照未捕获，不得事后声称行数前后精确相等。
> 运行版本：image `c2831e16c135` / release tag `omubot-bot:midterm-m3m6-20260713-1d108a6b` / container `fe045dee4150` / restart=0。
> 回滚入口：旧 image tag `omubot-bot:pre-midterm-m3m6-20260713`；生产 named-volume v1 安全备份 `pre-change-20260713-173655`。后者创建于 adoption 后，不是 v0 pre-adoption 快照。部署只替换了 `qq-bot`，NapCat 未操作。

## Objective

把 2026-07-12 架构审计列出的中期 M1-M6 从“方向”变成可验证、可回滚的运行边界。M1 Composition Root 与 M2 typed pipeline 已完成，本 tracker 只实现剩余 M3-M6，并对既有 M1/M2 证据做最终汇总，不重做已上线切片。

## Completion Definition

### M1 / M2 已完成基线

- [x] M1 Composition Root、ChatRuntime 生命周期所有权和逆序补偿已上线。
- [x] M2 Router connection、Scheduler outbound delivery、LLM visible guardrail typed stages 已上线。
- [x] M1/M2 targeted Pyright 0、取消/失败回归、独立复审和公开群零出站证据完整。

### M3 DatabaseCatalog / Migration / Retention / Backup

- [x] Catalog 对源码声明的 21 个 SQLite 库给出唯一 ID、路径、schema owner、clients、连接/备份/retention profile、敏感与可重建属性。
- [x] 统一连接 profile 可声明 WAL/NORMAL、DELETE/FULL 与 short-lived，旧 `connect_sqlite` 保持兼容。
- [x] migration ledger 与 `PRAGMA user_version` 同事务；支持 fresh、legacy baseline adoption、checksum drift、downgrade、partial schema、取消和回滚。
- [x] 首批低风险库完成真实接入；其余库进入 Catalog、健康、容量与备份覆盖，不再是盲区。
- [x] research v1 不允许被旧代码无条件降级；未知更高版本 fail-closed。
- [x] retention 由 owner handler 执行，默认关闭/dry-run/限批次，不做猜测式通用 DELETE。
- [x] Admin 提供只读数据库健康、容量、版本、owner、backup/retention profile 视图。

### M4 BackgroundTaskSupervisor

- [x] 建立 typed inline/deferred/periodic/heavy 分类与 task owner 元数据。
- [x] Supervisor 统一 spawn、活跃重名 fail-closed、失败记录、取消传播、shutdown drain/cancel 和运行快照。
- [x] PluginBus、Backup、HealthGuard、Hawkes、Dream、Schedule 迁入进程级 owner；reply-critical slot tasks 保留原状态机 owner。
- [x] Admin health 能读取任务状态；失败不伪装 healthy，shutdown 不遗留 task。

### M5 Admin God Module Incremental Extraction

- [x] `plugins.py` 的 runtime/restart toggle 事务迁入 typed service；route 只保留 HTTP 适配，普通 JSON 契约不变。
- [x] `learning_pipeline.py` 的 extract-all run registry/lock/runner 迁入 typed coordinator；路由只委托。
- [x] 未机械重写未触达的 Groups/Config 前端；“下次触达必须抽 slice”保留为持续规则。

### M6 Partitioned Type Gate

- [x] 修复 `admin/` 两个真实 Pyright 错误，不绕过类型检查。
- [x] 固定 Admin + M1/M2 + 本轮 M3/M4/M5 边界 target manifest 与可执行脚本。
- [x] 自动化门禁能在注入边界类型错误时 RED、恢复后 GREEN；不以全仓既有债阻断。

## Frozen Boundaries

- 不修改 TopicBlock 参数、research raw schema、群策略、人格/prompt 或 provider 行为。
- 不合并 SQLite、不切 PostgreSQL、不做微服务化、插件进程化或全后台 UI 重写。
- 不把 per-group reply slot/arbiter task 强迁进全局 Supervisor；任务 owner 必须真实变化才算迁移。
- 任何 DB 写迁移部署前必须有 pre-change backup、dry-run/只读检查与恢复路径。
- 仅 force-recreate `qq-bot`；NapCat `19f6cf13607c` 不 restart/recreate/down。

## Parallel Work Ledger

| Workstream | Active target / lineage | Scope | State |
| --- | --- | --- | --- |
| M3 audit | `/root/midterm_database_audit` | Catalog/migration/backup/retention 只读审计 | completed after 429 reconnect |
| M4 audit | `/root/midterm_task_supervisor_audit` | Task owner 与 Supervisor 只读审计 | completed after 429 reconnect |
| M5/M6 audit | `/root/midterm_admin_types_audit` | Admin slice 与 Pyright gate 只读审计 | completed |
| M3 final review | `/root/review_m3_storage` | Storage/backup/retention/restore 独立复审 | completed after third review: 0 Critical / 0 Important / 0 Minor |
| M4/M5 final review | `/root/review_m4_m5_lifecycle` | Supervisor/Admin extraction 独立复审 | completed after route/concurrency repair: 0 / 0 / 0 |
| M6/Admin/deploy review | `/root/midterm_closeout_docs_audit` → `/root/midterm_closeout_docs_audit/m6_deploy_surface_review` | 类型门禁、Admin、部署证据独立复审 | completed after interruption recovery: 0 Critical / 0 Important / 0 Minor |
| M3 backup/restore fixes | `/root/fix_m3_backup_restore` | optional backup、restore、scheduler 与 Admin route RED tests | completed |
| M5 plugin/UI fixes | `/root/fix_m5_plugin_toggle_ui` | runtime/restart toggle、pending UI、atomic tool registry | completed |

## Test Ledger

| ID | Command / Input | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| C0 | ACTIVE/Phase2/架构审计恢复 + 三线只读审计 | M1/M2 完成；M3/M4/M5/M6 仍有真实缺口；工作区包含大量用户改动 | 新建统一完成 tracker，不重做 M1/M2，不回退无关文件 | 2026-07-13 |
| C1 | M3 只读 inventory | 21 SQLite paths、54 direct connection sites、14 shared-helper sites；backup/health 仅 9 库；17 个工作区活库均 user_version=0 | M3 必须建立 Catalog/ledger/profile/coverage，不能只改文档 | 2026-07-13 |
| C2 | M5/M6 只读 inventory + `uv run pyright` 分区 | Admin API 已拆约 40 routers，但 learning_pipeline 2354 行、groups 1084 行、plugins 820 行；Admin=2 errors/1 warning，全仓=358 errors/2 warnings，M1/M2 targets=0 | 本轮抽 plugin toggle 与 learning extract owner；建立分区 gate，不清全仓债 | 2026-07-13 |
| C3 | M4 task owner inventory | Phase1 lifecycle 保留；首批候选=PluginBus/Backup/HealthGuard/Hawkes/Dream/Schedule；Memo/Food 无 shutdown owner；Scheduler slot/coalesce/send/research 有领域 owner | Supervisor 负责注册、异常、重启、快照、cancel+await；不机械迁移领域状态机 task | 2026-07-13 |
| C4 | Catalog domain RED/GREEN | RED 13 failed（目标模块不存在）；GREEN 13 passed；Ruff clean；实现+测试 Pyright 0 | 21 DB 精确覆盖、唯一性、shared owner/clients、profile enum、安全路径和 resolve contract 成立；无 DB I/O | 2026-07-13 |
| C5 | SQLite connection profiles RED/GREEN | RED 1 passed / 6 failed（缺 profile 参数）；GREEN existing+new 12 passed；Ruff/Pyright 0 | legacy 默认 WAL/NORMAL 保持；显式 WAL/NORMAL、DELETE/FULL、short-lived 与非法 profile 资源安全成立 | 2026-07-13 |
| C6 | Migration runner core RED/GREEN | RED 5 failed（目标模块不存在）；GREEN 5 passed；Ruff/Pyright 0 | fresh apply、ledger+user_version同事务、幂等 reopen、异常/取消 rollback、版本缺口 fail-closed 成立 | 2026-07-13 |
| C7 | Migration adoption/conflict RED/GREEN | 扩展前8 passed/3 RED（缺 adopt_existing）；GREEN 11 passed；Ruff/Pyright 0 | verified legacy adoption、partial reject、fresh apply、checksum drift、future-version reject、并发单次 apply 成立 | 2026-07-13 |
| C8 | Research future-version guard RED/GREEN | RED 1 failed/6 passed：v2无异常且被降为1；GREEN research schema/store 12 passed；Ruff/Pyright 0 | init在写schema前读版本，v2+ fail-closed且不降级/不丢表，v1幂等 reopen | 2026-07-13 |
| C9 | 首批 governed stores RED/GREEN | RED 8/8（version/ledger/partial guard缺失）；GREEN governed+usage+episode+block-trace 79 passed；M3 focused 38 passed；Ruff/Pyright 0 | block_trace/usage/episodic schema owner迁入runner；fresh apply、legacy adoption、partial reject、CRUD reopen成立 | 2026-07-13 |
| C10 | Catalog 驱动 backup/health | `tests/test_backup_service.py` 32 passed；21 库 exact registry、嵌套路径、manifest schema v2 元数据、optional missing、统一只读 health snapshot；Ruff/Pyright 0 | 备份/健康盲区从 9 库扩展为全部 21 库，legacy 状态可见且不误报损坏 | 2026-07-13 |
| C11 | Owner retention RED/GREEN | RED=默认禁用/dry-run/有界接口缺失；GREEN retention+block trace 46 passed；取消第二次 DELETE 时整批 rollback；Ruff/Pyright 0 | 仅 block_trace 注册显式 handler；messages 无 handler 时 fail-closed；默认 disabled、dry-run、总批次上限成立 | 2026-07-13 |
| C12 | Database Admin status | service/API 31 passed；`vue-tsc --noEmit` + Vite production build 通过 | System 页只读展示 21 库健康、容量、版本、owner 与三类 profile；health 与页面复用同一 snapshot | 2026-07-13 |
| C13 | Restore compatibility | backup suite 32 passed；future-version apply 在任何 live 写前退出；legacy manifest 默认阻断、专用 override；metadata/sha/fingerprint/version preflight | schema v2 元数据真正参与 restore gate，不再只是记录；旧扁平路径仅显式人工确认后可用 | 2026-07-13 |
| C14 | M4 Supervisor + production wiring | 核心/owner 120 passed；composition/chat 35 passed；主线整合后 M4 expanded 169 passed；Ruff/Pyright 0 | 单一 supervisor 由 composition 创建、最先 start/最后 stop；PluginBus/Backup/HealthGuard/Hawkes/Dream/Schedule 同 owner 边界；失败进 Admin health | 2026-07-13 |
| C15 | M5 Admin extraction | learning 13 passed；plugin bus/tools 100 passed；取消/并发/JSON contract 聚焦 7 passed；Ruff/Pyright 0 | toggle 事务与 extract-all owner 离开 route；外部取消转 terminal failed/cancelled 并释放 owner | 2026-07-13 |
| C16 | M6 partitioned gate | 真实临时 assignment error 使 gate 非零，恢复后 31 targets `0 errors, 0 warnings`；CI workflow + 4 gate/route tests | Admin/M1-M5 类型边界可执行、自动化且不受全仓历史债阻断 | 2026-07-13 |
| C17 | 宿主 checkout 只读预检（后续勘误） | host `storage/`：21 库中 15 存在/6 missing/0 error/68,907,008 bytes；三库 v0 verifier=true/ledger absent；`pre-change-20260713-140943` 为 17 ok/4 skipped/0 failed | 这些数据来自宿主 checkout，不是生产 Docker named volume；只能作为构建前静态预检，不能称为生产迁移前备份或生产 pre-count | 2026-07-13 |
| C18 | M3 独立最终复审 | 0 Critical / 7 Important：optional missing 触发紧急备份、immutable 忽略 WAL、三库 verifier 可误 adopt 畸形 schema、Admin prune 绕过 retention、future-version reject 前切 WAL、restore 可放行 untrusted/required-failed、fingerprint 不验证目标 schema | C10-C13 完成声明存在反例；部署冻结，按 RED/GREEN 修复后重新独立复审。现有 pre-change backup payload 本身仍验证健康 | 2026-07-13 |
| C19 | M3 repair + third review | M3 分区 125 passed；schema+backup 57 passed；backup service/scheduler 52 passed；生产 block_trace/usage/episodic v0 `semantic_contract=True`；第三轮复审 0 Critical / 0 Important / 0 Minor | 七项 Important 全关闭：完整 schema/index 语义、WAL-aware `mode=ro`、optional missing、owner retention、写前 future-version reject、restore fail-closed 与目标 schema 验证成立 | 2026-07-13 |
| C20 | M4/M5 lifecycle + Admin route repair | Coordinator 稳定任务名 25-run=20 history/1 Supervisor record；backup route 从 System God Module 移出，7 个 method/path 唯一；取消、并发局部更新、quick-check、legacy 合同 9 passed；最终复审 0/0/0，扩大回归 120 passed | Supervisor health 不再增长；`settings` 的读取→merge→校验→reload→原子落盘由 route lock + shield 统一拥有，runtime/disk 不再分叉 | 2026-07-13 |
| C21 | M6 + frontend final gates | 34-target typed gate 0 errors/0 warnings；相关 Ruff clean；`vue-tsc --noEmit` 通过；Vite 4392 modules 临时 production build 通过；`git diff --check` clean | M1-M6 新边界类型门禁与 Admin SPA 构建成立；全仓 Ruff 177 项仍为 coursework/research/IPv6 既有债，不属于本次边界 | 2026-07-13 |
| C22 | Final integration | M3-M6 聚焦聚合 307 passed；新增并发/quick-check 后全仓最终 `3108 passed / 17 skipped`；真实 `config/config.json` 在 RED 副作用后按 trusted pre-change payload 精确恢复并在 GREEN 后保持相等 | 当前源码回归无失败；两条 aiosqlite closed-loop warning 为既有测试清理噪声，不阻断本轮交付 | 2026-07-13 |
| C23 | Deploy-surface hardening | canonical 文档统一 `docker compose build bot` + `docker compose up -d --no-deps --force-recreate bot`；删除不存在 `scripts/deploy.sh` 的操作依赖；D7 同时审核 tracked/untracked；`.dockerignore` 排除 `.codegraph/.reasonix/.github` 等动态工具输入；SQLite live 指引改 `mode=ro` | 已关闭已知危险命令、失效脚本和动态构建上下文问题；独立 deploy-surface 终审最终 0 Critical / 0 Important / 0 Minor | 2026-07-13 |
| C24 | 正式构建与 source/image identity | 正式前端 98 files；source 596 files SHA256 `1d108a6b…`；frontend SHA256 `c21f8bd7…`；image-source 812 files SHA256 `a055880e…`；build context 2.50 MB；部署时 440 个生产文件和 98 个前端文件 host/image 0 mismatch | deploy-surface 独立终审 0/0/0；manifest 固化的是已部署镜像快照。部署后的 tracker/maintenance 勘误会使当前宿主文档与 image-source manifest 不再相等，不代表运行代码漂移 | 2026-07-13 |
| C25 | bot-only 部署 | rollback tag `omubot-bot:pre-midterm-m3m6-20260713` → old image `91e8a36c6713…`；new image `c2831e16c135…` / release tag `omubot-bot:midterm-m3m6-20260713-1d108a6b`；new container `fe045dee4150…`，StartedAt `2026-07-13T09:33:11.554438591Z`，restart=0 | 仅执行 canonical bot build/recreate；NapCat 未 restart/recreate/down | 2026-07-13 |
| C26 | 生产 named-volume adoption + 备份勘误 | `block_trace`、`usage`、`episodic` 均 v1、ledger `adopted=1`、semantic=true；部署验收快照计数分别为 `8085/757/669317`、`56949`、`0/0/0`（另 `episode_meta=1`）。立即补建 named-volume v1 安全备份 `pre-change-20260713-173655`：20 ok/1 optional skipped/0 failed/trusted=true，三库 quick_check=ok、restore plan=ALLOW | adoption 为 metadata-only；当前 schema/ledger/rows/恢复面健康。未捕获 live pre-count 与 pre-adoption named-volume snapshot，明确保留为不可补写的证据缺口，不声称精确行数不变 | 2026-07-13 |
| C27 | Admin / OneBot / capture 运行验收 | `/api/admin/databases` 19/21 ok、2 optional missing、0 error；Background Tasks 7/7 running、failed=0/backoff=0；OneBot `/get_status` online=true/good=true；Protocol health connected、106 ok/0 failed/0 pending；research capture healthy 且 drop/write/error=0；Backup settings/list 命中新 route 与真实生产备份 | 新 M3-M6 边界在运行态可观察且无内部失败；overall warning 仅为 5 条既有运行 warning 与 2 个 optional DB missing | 2026-07-13 |
| C28 | silent 群零出站固定窗口 | NapCat `json-file` 日志窗口 `2026-07-13T09:33:11.554438591Z` 至 `09:46:20.962305000Z`：186 行中群入站 182、群出站 0；7 个 silent 群有自然入站且全部零出站，另 3 群窗口内无流量；`send_group_msg`/发送成功/发送失败/blocked 均 0。两条 error 是 bot recreate 时反向 WebSocket 短暂拒绝与 5 秒重试，不是发送错误 | 10 个 silent 群在部署后验收窗口内实际成功出站为 0；同一 NapCat 在窗口前能记录群发送，排除日志格式漏搜。证据只覆盖固定窗口，不能单凭 NapCat 日志推导上游 guard 阻断次数 | 2026-07-13 |
| C29 | 容器身份与 OneBot 独立复核 | qq-bot full ID `fe045dee4150dd6c…`、image `c2831e16c135…`、StartedAt `2026-07-13T09:33:11.554438591Z`、running/restart=0/OOMKilled=false、Replaces=`5b9a72…`；NapCat full ID `19f6cf13607cb7e…`、Created `2026-06-22T07:00:35.653702969Z`、StartedAt `2026-07-09T22:51:47.963549084Z`、running/restart=0/OOMKilled=false、无 Replaces；OneBot online=true/good=true | qq-bot 是本轮唯一替换容器；NapCat identity、创建/启动时间与 restart 精确匹配基线，确认未重建 | 2026-07-13 |

## Silent Group Acceptance Window

固定共同窗口：UTC `2026-07-13T09:33:11.554438591Z` 至 `09:46:20.962305000Z`（CST 17:33:11.554–17:46:20.962）。NapCat 记录实际传输，qq-bot 与 Admin protocol trace 交叉验证没有发送尝试、阻断或失败。

| 群 ID | NapCat 入站 | Bot 入站 | 实际出站 | 阻断/发送错误 |
| --- | ---: | ---: | ---: | ---: |
| `1092460228` | 0 | 0 | 0 | 0 |
| `426727294` | 0 | 0 | 0 | 0 |
| `477640404` | 15 | 15 | 0 | 0 |
| `625618470` | 0 | 0 | 0 | 0 |
| `717096900` | 8 | 7 | 0 | 0 |
| `805836168` | 92 | 92 | 0 | 0 |
| `860324414` | 42 | 41 | 0 | 0 |
| `953023811` | 9 | 9 | 0 | 0 |
| `963085812` | 6 | 6 | 0 | 0 |
| `963737802` | 10 | 10 | 0 | 0 |

Bot 少于 NapCat 的两条入站分别来自 09:33:16 的 `860324414` 和 09:33:17 的 `717096900`，发生在 OneBot 09:33:20 连接、09:33:20.237 安装 outbound guard 之前。Admin trace 在共同窗口内保有 77/120 条、没有滚落；全局 send action=0，silent 群相关 18 条仅为成功的 `get_group_member_info/list`。有流量的 7 群构成强负向证据；其余 3 群只能证明固定窗口内被动零出站，不能外推未来。

## Rollback

1. 每个阶段维护独立旧到新 mapping，代码回滚不跨阶段。
2. M3 没有可用的生产 v0 pre-adoption 快照；`pre-change-20260713-173655` 是 adoption 后的 v1 安全备份，只能在当前 restore preflight 通过后恢复 v1 安全点，不能回退 ledger/user_version。代码镜像回滚前必须单独验证旧 image 对 additive v1 metadata 的兼容性；禁止把宿主 `pre-change-20260713-140943` 当生产备份，也禁止直接删除 ledger 表伪造降级。
3. M4 可恢复旧 task owner；先由 Supervisor 完整 shutdown，再切旧入口。
4. M5 可把 typed service/coordinator inline 回原 route；HTTP 契约必须保持。
5. M6 gate/script 可独立移除，不改变 runtime。
6. 镜像回滚 tag 为 `omubot-bot:pre-midterm-m3m6-20260713`；只替换 bot，不操作 NapCat。

## Evidence Boundary

宿主 `storage/` 不是生产 named volume。部署前记录的 `pre-change-20260713-140943` 和三库 `43/2208/0` 等行数属于 host checkout，不能用于证明生产迁移前后行数一致。生产启动后确认 adoption 只写 migration metadata，三库当前语义合同与 ledger 正常，并立即生成真实 named-volume 可信备份；但精确 live pre-count 和 pre-adoption payload 已无法事后重建，因此本 tracker 明确保留该证据限制。

## Next Step

本项目已完成并从 `ACTIVE` 关闭。等待用户选择下一项目，不自动启动 Pending 工作。
