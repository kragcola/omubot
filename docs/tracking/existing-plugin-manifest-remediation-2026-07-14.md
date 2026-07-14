# 现有插件 ManifestV3 平台合同全量整改

## Objective

修复 `existing-plugin-manifest-audit-2026-07-13.md` 的全部剩余问题，使 manifest v3 成为 Bus、Index、Admin、配置、CI 与仓库插件共同执行的单一合同；补齐依赖、后台任务与 capability health，并保持第二轮插件整改和公开群零出站语义不回归。

## Status

- mode: task-bug
- status: complete
- started_at: 2026-07-14 CST
- source_audit: `docs/tracking/existing-plugin-manifest-audit-2026-07-13.md`
- prior_completed: `docs/tracking/existing-plugin-remediation-2026-07-13.md`
- current_step: 实现、全量验证、独立 review、D7/rollback、bot-only build/recreate 与运行验收全部完成
- next_step: none；后续只按 ACTIVE 中独立 pending 线另行立项
- deployment: bot image `93cef508a4d3...` / container `9507ed557b36...` / StartedAt `2026-07-14T10:26:46.509646721Z` / restart=0 / OOMKilled=false
- rollback: `omubot-bot:pre-manifest-v3-20260714`=`c446ff2a...` + 旧 SPA snapshot `8fc04188...`；NapCat `19f6cf...` 全程未 restart/recreate/down
- migration_checklist: `docs/migrations/existing-plugin-manifest-remediation-2026-07-14.md`

## Already Closed By The Second Audit

- canonical system plugin identity 与 `context` Admin/toggle 错误合同。
- Food `web_search` optional dependency、dependency recovery 与 governance `enabled` 双真值。
- HistoryLoader 产品决策：迁入 `RuntimeConnectionPipeline` 核心阶段，以 capability status 暴露，不再作为可切生命周期插件。
- Schedule 对 canonical `calendar_context` 的 required dependency 与生产 provider ownership。
- History capability health 的 service/list/detail 运行态同源。

以上只作为回归基线，不重新实现；若当前代码证据矛盾则恢复为 RED。

## Remaining Requirements

### P1 Shared ManifestV3 Contract

- [x] 单一 typed `PluginManifestV3` parser/model 供 PluginBus、PluginIndex、Admin 与 CI 共用。
- [x] 仓库 manifest 强制 version=3、必填字段、类型/枚举、SemVer、目录名=name、唯一 name、config/store path、restart fields 与未知字段门禁。
- [x] runtime 执行 `min_omubot_version`；`min_omu_version` 仅作为显式 legacy alias。
- [x] 收敛旧 `kernel.manifest.PluginManifest`，不再导出会生成非 v3 清单的模型。
- [x] 生成并校验 canonical manifest JSON Schema，schema 携带 version/min/dependency/name pattern。

### P2 Cross-Layer Truth

- [x] PluginConfigStore、EffectiveConfigSnapshot 与 Admin settings 共用 manifest config path、apply mode、restart fields；nested dotted field 同源分类。
- [x] required/optional dependencies 进入 typed model、Bus、Index、Admin API 与前端展示；legacy `dependencies` 在 model 内归一为 required alias。
- [x] duplicate runtime name、manifest/directory mismatch、future min version 全部 fail-closed，并返回可诊断状态。
- [x] 正式仓库插件缺失/坏 manifest fail-closed；外部 legacy 与根目录单文件保持 blocked/index compatibility。

### P3 Lifecycle And Capability Health

- [x] Food/Memo fire-and-forget task 具有明确 owner、异常回收和 cancel+await shutdown 测试。
- [x] 当前真实 optional capability 图显式入 manifest；不靠 priority 或 shared ctx 猜顺序。
- [x] Vision capability enabled/health 来自真实 service availability/probe，不再常量 healthy。
- [x] runtime lifecycle CI gate 覆盖 Food/Memo task、Vision health 与 Bus dependency/lifecycle 基线。

### P4 Minor And Documentation

- [x] `context` manifest 补齐 canonical author/min version，不依赖基类 fallback。
- [x] wiki sticker 版本与 manifest 对齐，CHANGELOG 补当前 1.2.0 勘误。
- [x] architecture/setup/wiki/plugin-development/CHANGELOG 删除旧简化格式、错误 runtime 策略和未落地能力声明。
- [x] CI 同时执行 schema、class/manifest parity、dependency、lifecycle 与 capability health gates。

## Verification Gates

1. 每个行为切片先得到 assertion RED，再最小 GREEN；不得用 import/collection failure 充当 RED。
2. parser/model 纯逻辑 → Bus/Index service → Admin/API/frontend → lifecycle/runtime，按内到外验证。
3. D1 扫描全部 manifest consumer、裸 `json.load(plugin.json)`、`dependencies`/task owner/capability-only 同模式。
4. D2 对 Food/Memo shutdown 做 cancellation 与异常回收可观察测试。
5. D3 逐项核对旧 overlay/model/config path/文档入口是否删除或明确兼容。
6. targeted → plugin/Admin/frontend → full pytest；scoped Ruff/Pyright 0；Vue typecheck/build 通过。
7. 最终独立 review 后才允许 bot-only build/recreate；NapCat 不动，并复验公开受限群零出站。

## File Ownership

| Workstream | Scope | Writer | State |
| --- | --- | --- | --- |
| M-CORE | shared parser/model/schema、Bus/Index/config truth、核心 tests | root | implementation_complete |
| M-LIFE | dependency manifests、Food/Memo task owner、lifecycle tests | root | implementation_complete |
| M-ADMIN | Vision probe、Admin API/frontend dependency/health/config surface、tests | root | implementation_complete |
| M-DOC | tracker/migration/docs/CI/integration/deploy | root | complete |

## Rollback

- 各 phase 按 parser/consumer、lifecycle、Admin/frontend、docs/CI 文件边界独立回退；本项目不做数据库 schema/data migration。
- 部署前给当前 `c446ff2a...` 建新的 pre-ManifestV3 rollback tag；部署/回滚仅 bot-only recreate。
- `admin/static` 是 bind mount；回滚必须先恢复 `.workspace/rollback/pre-manifest-v3-20260714/admin-static`（98 files，tree `8fc04188...`），再切旧 image 并 bot-only recreate。
- 禁止 `docker compose down`，禁止 restart/recreate NapCat。

## Test Ledger

| ID | Command / Input | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| M0 | continuity gate + AGENTS/session/ACTIVE/source audit + `git status --short` | 真实仓库 `/Volumes/OmubotDisk/omubot`；第二轮已部署，ACTIVE 原为 none；工作树含大量既有 dirty/untracked | 新目标明确批准第一轮全部余项，另立 tracker；不重做第二轮 | 2026-07-14 |
| M1 | `tests/test_plugin_manifest_v3_contract.py` future `manifest_version=999` discovery RED → GREEN | RED: discovered=1；根因是 discovery 只读取 `capability_only` 且 overlay 忽略版本。最小入口门禁后新测试+既有 discovery 2 passed，Ruff clean | 正式目录插件的显式 future version 已在模块导入前 fail-closed；missing/type/Index/共享 parser 尚未证明 | 2026-07-14 |
| M2 | 正式目录 manifest 缺 `manifest_version` RED → GREEN | RED: future case pass、missing case discovered=1；收紧入口要求精确 version=3 后新文件+既有 discovery 3 passed，Ruff clean | 缺版本不再绕过；非-object、字段类型和其他 consumer 仍待 RED | 2026-07-14 |
| M3 | Vision capability runtime availability RED → GREEN | RED: `ctx.vision_client=None` 时 list/detail enabled=true；改为由 client availability 生成同源 top-level/health 后 Vision+History 4 passed，Ruff clean、Pyright 0 | 无 client 时 Admin 正确 disabled；真实 probe success/failure 仍待后续合同 | 2026-07-14 |
| M4 | 非 object manifest discovery RED → GREEN | RED: `plugin.json=[]` 时 discovered=1；入口对非 dict payload fail-closed 后，ManifestV3/History/Vision 相关回归 `10 passed`，Ruff clean、Pyright 0 | JSON array/scalar 不再绕过版本门禁后被运行时导入；共享 parser 的字段合同仍待实现 | 2026-07-14 |
| M5 | Vision degraded runtime probe RED → GREEN | RED: fake snapshot `available=true,status=failed,calls=7,errors=2` 仍被常量映射 healthy；Admin 改为消费 snapshot 并透出 telemetry 后，与 M4 同批 `10 passed` | list/detail 已同源反映 degraded；真实 `VisionClient.health_snapshot()` 仍须单独 RED/GREEN | 2026-07-14 |
| M6 | canonical model unknown-field RED → GREEN | RED: parser 不存在时 `unexpected_field` 得到 `DID NOT RAISE ValueError`；新增 strict frozen `PluginManifestV3`/nested config-store-display models 与 `parse_plugin_manifest_data()` 后模型+discovery `4 passed`，Ruff clean、Pyright 0 | 共享 in-memory typed contract 已建立；尚未接入文件、Bus、Index、Admin/CI | 2026-07-14 |
| M7 | manifest version SemVer RED → GREEN | RED: `version=1.2` 得到 `DID NOT RAISE ValueError`；canonical model 接严格三段 SemVer validator 后模型+discovery `5 passed`，Ruff clean、Pyright 0 | 插件自身 version 已严格；min version、dependency constraint 与文件/目录合同仍待切片 | 2026-07-14 |
| M8 | 真实 VisionClient failure telemetry RED → GREEN | RED: 一次 `ClientConnectionError("vision probe failed")` 后 snapshot fallback 为空、`available is None`；client 增加 calls/errors/last_error/status 并覆盖现有全部 None 失败分支后 Vision/Admin/render 回归 `11 passed`，Ruff clean、Pyright 0 | 真实 runtime client 不再因缺 method 永久伪 degraded；成功/失败状态可由 Admin 同源展示 | 2026-07-14 |
| M9 | legacy min-version alias RED → GREEN | RED: 仅给 `min_omu_version=1.2.3` 时 canonical 值为 None；pre-validator 在 canonical 缺失时归一 legacy、双字段冲突 fail-closed 后模型/discovery `6 passed`，Ruff clean、Pyright 0 | alias 现在是显式兼容层，不再成为独立版本真值 | 2026-07-14 |
| M10 | 全仓正式 manifest typed parse RED → GREEN | RED: 23 份中仅 context 缺 author/min version；补 `author=Omubot` 与空 min version 后仓库合同 `4 passed`、JSON/Ruff clean | 正式 manifest 不再依赖基类 fallback 补这两项元数据 | 2026-07-14 |
| M11 | explicit register/file identity RED → GREEN | RED: future version 在 `bus.register()` 为 DID NOT RAISE；name/目录 mismatch 也未拒绝；新增 `load_plugin_manifest()` 并让 register 共用后模型/文件测试 `7 passed`、Ruff clean、Pyright 0 | 生产显式注册不再绕过 typed parse，目录身份已 fail-closed | 2026-07-14 |
| M12 | checked-in JSON Schema RED → GREEN | RED: schema actual=None；按 `PluginManifestV3.model_json_schema(by_alias=True)` 生成 `schemas/plugin-manifest-v3.schema.json` 后模型合同 `6 passed`、jq/Ruff clean | schema artifact 已建立；CI 同步/全仓 gate 仍待接线 | 2026-07-14 |
| M13 | discovery pre-import validation RED → GREEN | RED: priority=`high` 最终 discovered=0 但 import sentinel 已写；discovery 改为 import 前强制 shared file parser，旧 discovery fixture 补合法 v3 sidecar 后相关 `75 passed`，Ruff clean、Pyright 0 | 缺失/坏/错误类型/name mismatch manifest 在任意插件代码执行前 fail-closed | 2026-07-14 |
| M14 | runtime min-version gate RED → GREEN | RED: current=1.5.0 仍发现 min=999.0.0 插件；新增 kernel version truth 并让 discovery/register 共用 compatibility gate 后 Admin/Bus/build 相关 `94 passed`，Ruff clean、Pyright 0 | 最低版本从 Index 展示提升为运行时保证；services.version 复用 kernel truth | 2026-07-14 |
| M15 | dependency constraint SemVer RED → GREEN | RED: required `>=1.2` 未拒绝；三张 dependency map 统一只接受 `*` 或受支持操作符+三段 SemVer 后模型/Bus `77 passed`，Ruff clean、Pyright 0 | typed dependency 值已进入模型边界；legacy alias 合并、跨层展示仍待实现 | 2026-07-14 |
| M16 | legacy public model 收敛 RED → GREEN | RED: `PluginManifest is PluginManifestV3` identity 失败；删除早期 dataclass generator 并保留 canonical alias 后模型/注册 `12 passed`，Ruff clean、Pyright 0 | 旧导出名仍兼容，但不再能生成非 v3 清单 | 2026-07-14 |
| M17 | PluginIndex strict parse/dependency RED → GREEN | RED1: unknown field 仍 `manifest_status=ok`；正式目录改用 shared loader 后 invalid；RED2: required/optional entry 均 `{}`；加入 legacy→required merge 与 optional 视图后 Admin/Index 相关 `16 passed`，Ruff clean、Pyright 0 | Index 与 Bus 共用 typed contract，legacy root blocked object-only 兼容保留 | 2026-07-14 |
| M18 | config path/file/restart contract RED → GREEN | RED1: PluginConfigStore 忽略 custom path；RED2: `../outside.json` 未拒绝；RED3: restart field 不存在仍通过。Store 改用 manifest path，loader 增 relative/resolve confinement、文件存在、defaults/schema identity/shape 和递归 restart field 校验；相关 `76 passed`，23/23 正式 file manifests 验证通过，Ruff clean、Pyright 0 | config 路径与文件合同由 shared loader 执行；symlink/`..` 逃逸已 fail-closed | 2026-07-14 |
| M19 | EffectiveConfig field apply RED → GREEN | RED: restart_required 插件的非列出 `hot` 字段仍标 required；改用 shared loader/path，并按 flattened field 与 restart list 匹配，旧 fixture 升合法 v3 后两个 effective 文件 `6 passed` | EffectiveConfig 与 Admin 的字段级 restart 语义一致；apply_mode 仍保留原声明 | 2026-07-14 |
| M20 | Admin/frontend dependency surface RED → GREEN | RED: list/detail required actual `{}`；API 普通/capability payload 统一 legacy+required/optional，Vue 类型与概览补两行依赖摘要。Admin 相关 `15 passed`、Pyright/Ruff 0；`vue-tsc --noEmit` 与 production build 通过 | required/optional 已进入 Index/API/详情 UI；未改启停与配置交互 | 2026-07-14 |
| M21 | Food/Memo/Vision lifecycle 聚合复核 | `test_food_task_lifecycle_contract + test_memo_task_lifecycle_contract + test_plugin_manifest_lifecycle_health + business_constraints` 为 `24 passed`；异常回收、cancel+gather、真实 Vision unavailable/degraded 均有可观察断言 | lifecycle/health 实现不是只读审计结论，可进入 CI gate | 2026-07-14 |
| M22 | generated schema pattern + workflow manifest gate RED → GREEN | schema RED 精确缺 `version.pattern`；为 version/min/dependency/name 加 generated pattern 并同步 checked-in schema。workflow RED 精确缺 manifest script；加入 `uv run python scripts/check_plugin_manifests.py`。模型/schema `14 passed`，23 manifests validated，Ruff/Pyright 0 | JSON Schema 可独立表达主要字符串约束，CI 不再只靠开发者手跑 | 2026-07-14 |
| M23 | Bus 单次 typed overlay + duplicate runtime name RED → GREEN | discovery 改为把已解析 model 传入 module loader，删除第二次裸 JSON 读取；duplicate RED 为 DID NOT RAISE，注册前门禁后旧同名 fake fixtures 改唯一身份，Bus/registration 聚合 `71 passed`，Ruff/Pyright 0 | 消除 TOCTOU 双解析；最终 runtime identity 全局唯一，失败注册不污染 registry | 2026-07-14 |
| M24 | model/index/public API 余项 RED → GREEN | name regex、legacy required alias merge、Index manifest-declared config paths、顶层 kernel canonical exports 分别取得精确 RED；相关聚合最高 `21 passed`，Index/Admin `4 passed`，23 manifests validated，Ruff/Pyright 0 | 命名、alias、路径与开发者入口均由 canonical model 收口 | 2026-07-14 |
| M25 | Store/Admin config single truth + dotted restart RED → GREEN | Store metadata RED 为 `(None,None)`；Admin GET RED 返回实例 `hot/[]`；POST RED 为 `requires_restart=false/hot_apply=1`；nested RED 同样误分。Store 输出 manifest metadata，Admin GET/POST 共用，并展平 leaf path 分类；legacy missing-field 语义回归修复后聚合 `77 passed`，Ruff/Pyright 0 | 正式插件不再依赖实例 config_spec 双真值；nested restart fields 与 EffectiveConfig 一致，legacy fallback 保留 | 2026-07-14 |
| M26 | docs/CI contract closeout | workflow lifecycle gate 本地精确命令 `87 passed`；4 个文档 ManifestV3 JSON block 均经 canonical parser 验证；D1 stale sparse/fail-open/fixed-path/旧能力声明扫描清零 | 文档示例可执行，CI 覆盖 manifest + lifecycle/health；待总体验证与部署 | 2026-07-14 |
| M27 | 总体验证 | plugin/lifecycle 聚合最终 `214 passed`，Admin 聚合 `119 passed`；Ruff clean、scoped Pyright 0；Vue typecheck + Vite 4392 modules build；`uv sync --frozen`、23 manifest gate、34 typed boundary targets 0；最终 full pytest `3256 passed / 17 skipped` | 静态、结构、前端、全量行为面均 GREEN；warnings 为既有 aiohttp/NoneBot deprecation 与 retrieval aiosqlite thread 收尾 | 2026-07-14 |
| M28 | 独立 requesting-code-review → remediation → closure | 首轮发现 2 Important：discovery 二读 TOCTOU、config schema 未执行；修复后又发现 schema missing 被 Admin 吞错 fail-open。分别新增 import-time rewrite、defaults invalid、override invalid、schema missing RED；引入 `jsonschema`，loader/Admin 共用 validator；三轮 closure 后 reviewer 明确无 Critical/Important | pre-import snapshot、defaults/override/schema-missing 均 fail-closed，错误包含字段路径，hook/store 不受污染 | 2026-07-14 |
| M29 | D7/rollback/deploy preflight | stash empty、AppleDouble 0；rollback tag `omubot-bot:pre-manifest-v3-20260714`=`c446ff2a...`；旧 image SPA 98 files snapshot tree `8fc04188...`，当前 host SPA 98 files tree `1dd388ca...`；host 与运行 baseline 482/479 production manifests 比较为精确 23-file delta，artifact SHA `af4be32...`；preflight reviewer closure 无 Critical/Important | 高脏工作树已缩为可审计 ManifestV3 build delta；`.github/workflow`/schema/checker 当前仍 untracked，远端 CI 未激活，未伪报已提交 | 2026-07-14 |
| M30 | bot-only build/recreate 与镜像边界复验 | 仅执行获准的 `docker compose build bot` 和 `docker compose up -d --no-deps --force-recreate bot`；build context 2.59 MB；新 image `93cef508a4d3...`、container `9507ed557b36...`、restart=0、OOMKilled=false；运行 source 与 host production manifest 0 mismatch，容器内 gate `validated 23 plugin manifests` | ManifestV3 正式运行镜像已替换；rollback image、旧 SPA snapshot 与 23-file delta 均保留 | 2026-07-14 |
| M31 | Admin/lifecycle/协议/公开群固定窗运行验收 | Admin 200；23 plugin entries 全 enabled（21 Bus + 2 capability-only），Index 23/23 manifest valid；History `success/runs=1/errors=0`；Vision `available=true/status=idle/calls=0/errors=0`；services 10 ok / 2 warning / 0 error，background tasks 8/8 running；OneBot connected。固定窗 UTC `2026-07-14T10:26:54.456781Z` 至 `10:39:00Z`（12 分 05.5 秒）内，NapCat 与 NoneBot 均见 251 条群入站，来自 7 个配置为 `silent_learn` 的公开受限群（477640404=20、625618470=7、717096900=3、805836168=119、860324414=32、963085812=68、963737802=2）；NapCat 群出站 0、`send_group_msg` 0、error 0，bot ERROR/CRITICAL/Traceback 0；Protocol Trace 快照 70 ok / 0 failed / 0 pending / 0 send action | 公开受限群只收不发与 ManifestV3 runtime 均闭环。部署瞬间 UTC `10:26:49Z` 有 1 次预期反向 WS `ECONNREFUSED` + 5 秒重试两条 NapCat error，`10:26:54Z` 连接成功后固定窗清零；不伪报为全启动过程零错误 | 2026-07-14 |

## Next Session Starts Here

本 tracker 已完成，不再继续 M1-M31。后续任务从 `docs/tracking/ACTIVE.md` 的独立 pending 线重新选择并立项；任何部署仍须遵守 NapCat red line。
