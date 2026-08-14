# Agent Runtime v2 Upgrade

> 状态：completed（dark/local；生产激活需单独立项授权）
> mode: completed
> 最后更新：2026-07-22 CST
> 当前下一步：P0-P5 暗态架构升级已完成；本 tracker 无剩余实施项。生产 principal、source path、LLMClient/ToolRegistry/bootstrap 接线与部署必须另行授权和立项。
> 阻塞：无。
> 验证证据：P5 focused 90 passed；P0-P5 core 509 passed；tools/client/application/Worldbook/Social/Schedule 兼容 515 passed / 42 upstream warnings；frontend contracts 9 passed、vue-tsc/build 通过；Ruff/Pyright/diff/isolation 全绿；最终独立复验无 P0-P3 finding。
> 回滚入口：P0-P5 新切片均 dark；删除 P5 Admin/query/action/readiness 模块、路由、页面与对应测试即可；没有生产 Runtime/Memory/Worldbook governance DB、worker、接线或数据迁移。

## Resume Capsule

- objective: 将现有对话内 tool loop 升级为受治理、可持久恢复、可审计的 Agent Runtime，同时保留 PluginBus、SQLite、OneBot、Worldbook 和领域 Store。
- next_step: none。本项目暗态交付已完成；若未来授权生产激活，必须新建 tracker，逐项取得 readiness attestation 后再接 source/principal/LLMClient/ToolRegistry/bootstrap。
- current_files: `services/agent_runtime/`；`services/memory/governance_*`；`services/worldbook/governance_*`；`admin/routes/api/agent_runtime.py`；`admin/frontend/src/views/agent-runtime/`；P0-P5 tests/docs。
- last_verified: P5 显式来源、no-create-on-missing、bounded/redacted query、token-bound decision-only actions、triple-cutoff Worldbook snapshot 与 attested report-only readiness 全绿；focused 90、core 509、compatibility 515 / 42 warnings、frontend 9，Ruff/Pyright/diff/isolation clean；最终独立复验无 P0-P3 finding。生产无 governance wiring/DB，内建 QZone wire profile `validated=false`。
- do_not_redo: 不重新调研外部 Agent 框架；不重做已验收 P2 工具/reconciliation；不把 mixed HTTP/POST 或 raw QZone transport 误标为已迁移；不改 LLMClient/生产接线。
- rollback: dark 代码与临时测试 DB only；未部署。

## Project Boundary

### In scope

1. Tool ABI v2：effect、owner/version、approval、idempotency、retry、concurrency、结构化结果。
2. Agent run/tool invocation 持久 ledger、审批/outbox/reconcile 状态机。
3. Capability Planner 与执行时 Policy Gate；工具可见性不再等于授权。
4. 从 `LLMClient` 抽离 tool orchestration，支持 message/tick/domain_event/recovery trigger。
5. 统一 Memory Observation/Candidate/Conflict/Promotion 合同；领域 Store 成为受治理投影。
6. Worldbook 只接受受约束 evidence/event proposal；Schedule 旧直写逐步收口到 reducer。
7. Admin Runtime/Memory governance 可观测与人工裁决。

### Out of scope until separately authorized

- 生产部署、bot/NapCat/sidecar 重启或重建。
- QQ、QZone、NapCat、webhook 或任意真实外部发送。
- 开启 QZone live、修改 `BUILTIN_WIRE_PROFILE.validated`、处置既有远端日志。
- 在 Phase F 硬门槛满足前启动 Episodic-to-Declarative 自动事实化。
- 引入 Temporal、Neo4j、LangGraph、Letta 或托管 memory 服务作为默认运行依赖。

## Phase Plan

| Phase | Status | Deliverable | Activation |
| --- | --- | --- | --- |
| P0 Contract + dark ledger | completed | Tool ABI v2；run/tool ledger；PluginToggle 基础工具保留 | 不接管生产路径 |
| P1 Runtime spine | completed | RunCoordinator、CapabilityPlanner、PolicyGate、EffectExecutor | dark/offline only |
| P2 Tool migration | offline contracts completed; production activation pending | P2-1..P2-5 + QZone/reaction/sticker + manual reconciliation 已落地；principal wiring 未授权 | dry-run / policy tests |
| P3 Memory governance | completed (dark/local) | ObservationV1、ConflictV1、PromotionEventV1；Memo/Compaction candidate-only；verified append-only shadow store | not production-wired |
| P4 Living Story integration | completed (dark/local) | world_id、typed Social/Schedule proposal、verified legacy reducer receipt | not production-wired |
| P5 Admin + rollout | completed (dark/local) | bounded/redacted offline query、operator decision contracts、attested readiness/runbook | separate deploy authorization |

## P0 Acceptance

- [x] Existing Tool subclasses continue to instantiate and expose unchanged OpenAI tool schema.
- [x] Default legacy ToolSpec is explicitly `legacy_unclassified`, never silently treated as read-only.
- [x] ToolContext can carry runtime-generated run/call/auth/idempotency fields without breaking existing callers.
- [x] Ledger migrations are checksum-verified and created only at an explicit caller-supplied path.
- [x] Run and ToolCall transitions reject illegal edges and preserve append-only events.
- [x] Cancellation and post-dispatch uncertainty are durably distinguishable.
- [x] Runtime plugin toggle preserves non-plugin base tools and remains atomic on provider/persistence failure.
- [x] Focused pytest, ruff, pyright and SQLite quick_check pass.
- [x] No production config, storage DB, Docker container or external service is mutated.

## Decisions

| Decision | Choice | Why | Date |
| --- | --- | --- | --- |
| Runtime strategy | Native Omubot Runtime on PluginBus + SQLite | Existing topology is sound; external frameworks add migration/ops cost without replacing business authorization | 2026-07-21 |
| External effects | Exactly-once intent, never claim universal exactly-once delivery | QQ/QZone may not support provider idempotency; ambiguous post-dispatch state must become `unknown` | 2026-07-21 |
| Tool authorization | Complete mediation at execution | Prompt filtering and plugin manifest permissions are supply-side only | 2026-07-21 |
| Memory truth | Immutable observation + explicit conflict + append-only promotion | Direct LLM writes cannot guarantee subject, visibility, time or provenance | 2026-07-21 |
| Story truth | Existing Worldbook ledger/reducer remains canonical | Avoid competing StoryArc truth paths | 2026-07-21 |
| P0 activation | Dark structural slice | Establish contracts and recovery semantics before changing production behavior | 2026-07-21 |
| Admin authority | Server-owned context token and store truth; browser fields are assertions only | Prevent Admin/API clients from reconstructing principal, target, conflicts or invocation context | 2026-07-22 |
| Rollout evidence | Closed attestation gates; unknown is `not_assessed`, never inferred absent | A boolean UI cannot prove production resources, identity or rollback readiness | 2026-07-22 |

## Assumption / Risk Ledger

- Existing untracked workspace paths are user-owned and excluded from all adds/commits.
- Tool ABI changes must remain import-compatible through `services.tools.base/context` re-exports.
- Legacy tools remain callable only by the old loop during P0; P1 Policy must fail closed on `legacy_unclassified` until each tool is classified.
- A non-idempotent call found in `dispatching` after restart must become `unknown`; no automatic retry.
- A claimed/dispatching worker must present the exact durable lease owner on every non-cancellation transition; restart-wide recovery requires explicit exclusive startup.
- The 141 legacy user cards without visibility remain quarantined; no automatic ownership inference.
- Phase F remains gated by >=3 months real Episode data and >=200 `enabled_for_prompt` Episodes in one group; earliest calendar boundary is 2026-08-21.
- `allow_proxy_dns_net=true` 只在域名 DNS 结果上兼容 `198.18.0.0/15`，模型给出的该网段字面 IP 永远拒绝；生产启用前仍需把本地 fake-IP proxy 视作显式可信解析边界，或改为可验证公网解析。
- `RuntimePrincipal.allowed_target_refs` 仍无生产构造；P2-3 只证明 exact canonical origin gate，不能据此宣称任意 web origin 已可安全上线。
- same-origin-only redirect 是有意比浏览器严格：授权目标是 canonical origin，跨源跳转必须重新提议/授权。
- 默认 `plugins/http_api/config.default.json` 仍允许 GET+POST，因此当前插件实例必须保持 legacy；只有显式 GET-only 配置才能进入 governed runtime。POST 在 provider receipt/reconcile/credential broker 完成前不分类。
- `react_to_message` 已要求 runtime trusted canonical message refs，但生产 principal/trigger 尚未构造这些 refs；不得据此上线。
- `send_sticker(intent=...)` 已把首次解析的 exact sticker ID 写入 durable target；生产 principal 仍无 exact target 构造，且 provider reconcile 尚未实现。
- OneBot provider 不支持通用 idempotency：群管理/message/poke 都是 reconcile-only + never retry，dispatch 后异常落 `unknown`；自动 reconcile 仍 pending。

## Files Touched

| File | Change | Status |
| --- | --- | --- |
| `docs/tracking/agent-runtime-v2-2026-07-21.md` | Canonical project tracker | done |
| `docs/tracking/ACTIVE.md` | Active recovery pointer | done |
| `docs/migrations/agent-runtime-v2-2026-07-21.md` | Migration/rollback design | done |
| `kernel/types.py` | Additive Tool ABI v2 metadata/context/result contract | done |
| `tests/test_agent_runtime_tool_abi.py` | Tool ABI RED/GREEN behavior contracts | done |
| `services/agent_runtime/ledger.py` | Explicit-path SQLite Run/ToolCall snapshot + append-only event ledger | done |
| `tests/test_agent_runtime_ledger.py` | Migration, transition, cancel, restart and fault-injection contracts | done |
| `services/plugin_toggle.py` | Preserve non-plugin base tools and restore post-write persistence failures | done |
| `services/plugin_state.py` | Add exact removal of an inherited/no-override plugin state | done |
| `tests/test_plugin_toggle_policy.py` | Base-tool and persistence rollback regressions | done |
| `services/agent_runtime/policy.py` | Capability planning, execution-time policy and approval binding | done |
| `services/agent_runtime/executor.py` | Governed execution, schema/result gates, timeout/cancel and concurrency mediation | done |
| `services/agent_runtime/coordinator.py` | Dark Run lifecycle, approval/retry resume and waiting-state projection | done |
| `tests/test_agent_runtime_policy.py` | Planner/PolicyGate fail-closed contracts | done |
| `tests/test_agent_runtime_executor.py` | Complete-mediation, failure, cancel, schema and concurrency contracts | done |
| `tests/test_agent_runtime_coordinator.py` | Run start/wait/resume and registry TOCTOU contracts | done |
| `kernel/types.py` | P2-2 `ToolInvocationBinding` + `Tool.bind_invocation` + `ToolSpec.binding_required` | done (P2-2) |
| `services/agent_runtime/coordinator.py` | `_resolve_binding` before ToolCall; caller target assertion-only | done (P2-2) |
| `services/agent_runtime/policy.py` | WRITE_LOCAL (+ external effects) require `target_ref` | done (P2-2) |
| `services/tools/memo_tools.py` | `update_cards` WRITE_LOCAL + trusted-scope bind | done (P2-2) |
| `services/tools/affection_tools.py` | `set_nickname` WRITE_LOCAL + trusted user bind | done (P2-2) |
| `services/tools/sticker_tools.py` | `save_sticker`/`manage_sticker` bind; SendSticker production path preserved | done (P2-2) |
| `tests/test_agent_runtime_tool_catalog.py` | frozen catalog + four write specs/bindings | done (P2-2) |
| `tests/test_agent_runtime_tool_abi.py` | binding ABI + keyed_serial+binding_required | done (P2-2) |
| `services/tools/safe_http.py` | canonical public URL、validated resolver、pinned aiohttp transport、same-origin redirect 与有界读取 | done (P2-3 dark) |
| `services/tools/web_fetch.py` | EXTERNAL_READ spec/origin binding；governed retry/terminal 与 legacy 文本兼容 | done (P2-3 dark) |
| `services/tools/web_search.py` | EXTERNAL_READ fixed-target binding；governed provider retry 与 legacy 文本兼容 | done (P2-3 dark) |
| `tests/test_safe_http.py` | DNS/fake-IP/URL/redirect/stream/cancel/charset transport contracts | done (P2-3) |
| `tests/test_agent_runtime_coordinator.py` | durable web target、allowlist/mismatch、retry/terminal integration | done (P2-3) |
| `services/tools/http_api.py` | GET-only 条件 spec/binding/pinned transport；mixed/POST fail-closed 且 legacy 路径保留 | done (P2-4 dark) |
| `tests/test_tools.py` | GET-only transport + default mixed httpx POST compatibility | done (P2-4) |
| `tests/test_agent_runtime_tool_catalog.py` | strict method-config/header/origin contracts | done (P2-4) |
| `services/agent_runtime/executor.py` | TrustedToolContext 增加非持久 bot 并重建 ToolContext | done (P2-5) |
| `services/agent_runtime/coordinator.py` | binding context 透传 trusted bot | done (P2-5) |
| `services/tools/group_admin.py` | mute/title/message external specs + trusted admin/group/member binding | done (P2-5 dark) |
| `services/tools/interaction_tools.py` | poke external spec/binding；governed provider error propagation；reaction 保持 legacy | done (P2-5 dark) |
| `services/group/outbound_access_guard.py` | group poke/ban/title 纳入 fail-closed group policy | done (P2-5) |
| `services/tools/qzone_journal.py` | QZone draft publish governed adapter；trusted ID/target、ALWAYS approval、reconcile-only/never retry | done (P2 dark) |
| `plugins/qzone_journal/delivery.py` | pre/post-dispatch 相位异常、claim/finalize unknown、shielded inspection/cleanup 与取消传播 | done (P2 dark) |
| `tests/test_agent_runtime_qzone_tool.py` | 双审批、可信 binding、双 ledger unknown、claim/cleanup 重复取消合同 | done (P2) |
| `kernel/types.py` | `ToolContext.target_ref` 承载 ledger-owned durable execution target | done (P2) |
| `services/agent_runtime/coordinator.py` | resume binder 读取既有 durable target，不重新解析动态资源 | done (P2) |
| `services/agent_runtime/executor.py` | execute ToolContext 注入 `ToolCall.target_ref` | done (P2) |
| `services/tools/interaction_tools.py` | reaction trusted message ref/emoji target + live group policy + governed unknown | done (P2 dark) |
| `services/group/outbound_access_guard.py` | 为 message-id-only provider action 暴露同源实时群策略断言 | done (P2) |
| `services/tools/sticker_tools.py` | sticker intent 单次解析、durable exact target、pre/post dispatch 分类 | done (P2 dark) |
| `services/agent_runtime/reconciliation.py` | authorized/idempotent adapter mediation + receipt verification + OneBot attestation | done (P2 dark) |
| `services/agent_runtime/ledger.py` | append-only unique reconciliation event + all-unknown Run projection | done (P2 dark) |
| `services/tools/qzone_journal.py` | QZone manual domain reconciliation adapter | done (P2 dark) |
| `tests/test_agent_runtime_reconciliation.py` | scope/target/adapter/receipt/attestation/conflict contracts | done (P2) |
| `services/memory/governance_contracts.py` | immutable Observation/Candidate/Conflict/Promotion contracts and append-order fold | done (P3 dark) |
| `services/memory/candidate_producers.py` | Memo/raw-message and Compaction item-aligned candidate-only producers | done (P3 dark) |
| `services/memory/governance_store.py` | explicit-path verified append-only SQLite shadow ledger | done (P3 dark) |
| `services/storage/sqlite.py` | acquired-connection cleanup survives repeated cancellation | done (shared P3 hardening) |
| `tests/test_memory_governance_{contracts,store}.py` | immutable, temporal, integrity, concurrency, schema and cancellation contracts | done (P3) |
| `tests/test_memory_candidate_producers.py` | provenance/scope/evidence mapping contracts | done (P3) |
| `tests/test_storage_sqlite_profiles.py` | repeated cancellation during connection PRAGMA setup | done (shared P3 hardening) |
| `services/worldbook/governance_contracts.py` | typed world/source binding, immutable proposal identity and verifier-only receipt proof | done (P4 dark) |
| `services/worldbook/governed_adapters.py` | pure Social/Schedule proposal adapters and legacy reducer commit verifier | done (P4 dark) |
| `tests/test_agent_runtime_worldbook_integration.py` | world/scope/privacy/identity/reducer/receipt/adversarial contracts | done (P4) |
| `services/agent_runtime/admin_query.py` | explicit-source bounded/redacted Runtime projection | done (P5 dark) |
| `services/memory/governance_query.py` | explicit-source bounded/redacted Memory projection | done (P5 dark) |
| `services/worldbook/governance_store.py` | explicit-path append-only Worldbook governance ledger | done (P5 dark) |
| `services/worldbook/governance_query.py` | bounded Worldbook query with proposal/decision/receipt snapshot cursor | done (P5 dark) |
| `services/agent_runtime/admin_actions.py` | token-bound decision-only approval/reconciliation/Memory actions | done (P5 dark) |
| `services/agent_runtime/rollout_readiness.py` | source integrity and attested report-only activation/rollback gates | done (P5 dark) |
| `admin/routes/api/agent_runtime.py` | strict explicitly injected P5 GET/context/decision routes | done (P5 dark) |
| `admin/routes/api/__init__.py` | aggregate router forwards only explicit P5 sources/actions/readiness | done (P5 dark) |
| `admin/frontend/src/api/agentRuntime.ts` | bounded P5 DTO and GET/token-POST client contracts | done (P5 dark) |
| `admin/frontend/src/views/agent-runtime/` | Calm Ops Runtime/Memory/Worldbook/readiness console | done (P5 dark) |
| `tests/test_agent_runtime_{admin_query,admin_actions,admin_contexts,admin_api,rollout_readiness,rollout_attestation,worldbook_admin_api}.py` | P5 backend trust/query/action/readiness contracts | done (P5) |
| `tests/test_{memory,worldbook}_governance_admin_query.py` | bounded/redacted keyset and snapshot contracts | done (P5) |
| `admin/frontend/tests/agent-runtime-governance.test.ts` | frozen frontend trust, readiness, Worldbook and accessibility contracts | done (P5) |

## P2 Live Status (do not mark P2 complete)

| Slice | Status | Notes |
| --- | --- | --- |
| P2-1 read classification | landed (prior WIP) | `get_datetime`, `lookup_cards`, `slang_lookup` pure/read specs preserved |
| P2-2 local-write binding | accepted dark slice | `update_cards`, `set_nickname`, `save_sticker`, `manage_sticker`；恢复重绑与 sticker 身份/tag 缺口已关闭 |
| P2-3 external reads | accepted dark slice | `web_search` fixed target；`web_fetch` canonical origin + pinned DNS/redirect/byte/cancel boundary；无真实网络调用 |
| P2-4 conditional HTTP API GET | accepted dark slice | only explicit GET-only config; safe headers + canonical origin；default mixed/POST remains legacy |
| P2-5 OneBot governed subset | accepted dark slice | mute/title/send_group_msg + conditionally registered poke；approval + trusted binding + unknown；no actual QQ action |
| P2 QZone journal adapter | accepted dark slice | wraps `JournalDelivery`, never raw transport；trusted draft + approval + unknown；not registered/wired in production |
| P2 reaction provenance | accepted dark slice | canonical trusted group/user message ref + emoji-bound target；dispatch-time group policy；no actual QQ action |
| P2 sticker durable resolution | accepted dark slice | intent resolves once into ToolCall target；resume/execute reuse exact ID；no actual QQ action |
| P2 reconciliation | accepted dark slice | unknown remains immutable；QZone domain-first + OneBot attestation；unique event；no auto replay |
| P2 activation remaining | **not authorized / not done** | principal production construction、ToolRegistry/LLMClient wiring、live rollout |

Thirteen default catalog tools are explicit non-legacy (3 local reads + 4 local writes + 2 web reads + 3 group admin + send_sticker). GET-only HTTP and profile-gated poke/reaction are conditional additions. Default mixed HTTP remains legacy; QZone has a dark adapter but is not registered in production.

## P3 Acceptance

- Observation, Candidate, Conflict and Promotion facts are immutable, canonical-hashed and deep-frozen; evidence, subject, owner, visibility and time basis are closed contracts.
- Memo raw message evidence remains byte-faithful; Compaction output is item-aligned and candidate-only. Neither producer changes the current prompt or production write path.
- The shadow store requires an explicit caller path and does not initialize on import/startup. Observation/candidate/conflict/promotion rows are append-only and schema/checksum verified on every reopen.
- Conflict and promotion records share one global `append_seq`. Replay uses record order to distinguish late/backdated conflicts, while occurrence time still prevents resolving a conflict before detection.
- Conflict discovery unions canonical payload relationships with auxiliary link indexes, then requires exact observation/candidate/projection link equality. Missing or extra links fail closed before approval.
- Same-object init is single-flight; close waits for active writes; transaction rollback and connection initialization cleanup remain attached through repeated cancellation.
- No production `LLMClient`, ToolRegistry, bootstrap or Worldbook path imports/constructs the P3 store; no production governance DB exists.
- Independent current-snapshot audit found temporal, lifecycle, schema and auxiliary-link defects; all have explicit RED/GREEN evidence. The reviewer confirmed the repaired baseline before the final missing-link fix; its last re-probe response was rejected by an external platform policy filter, so no fabricated final "no findings" claim is recorded.

## P4 Acceptance

- `WorldRefV1` makes world/Arc identity explicit without changing existing StoryArc/Life/partner/Dream JSON or production IDs. Legacy Arc snapshots can prove only `omubot.default`; an explicit conflicting snapshot world fails closed.
- Schedule and Social use separate frozen source bindings. Schedule identity binds date plus summary digest and carries no evidence; Social binds factual experience/group/user/message/source/time while proposal evidence is a fixed raw-free attestation.
- Event and logical proposal IDs are deterministic from typed source truth. A retry with a later `proposed_at` keeps event/proposal IDs while changing the content fingerprint; any Schedule payload change changes both IDs.
- Direct constructor/splice attempts cannot replace binding digests, event IDs, Social group/message/source, closed effects, event metadata or structured evidence carriers without validation failure.
- Receipt verification accepts the actual `EventReducer` history projection and requires exact committed semantics, Arc/world/revision, durable ID set and one exact history row. The receipt has no public digest factory or mutable Event/Arc payload and binds event/Arc/ID-set proof digests.
- No production Worldbook runtime/plugin/config imports the new modules; there is no default path, DB, store, gate change, worker, StoryArc mutation, deploy or external action. Activation must re-read authoritative Social/Schedule stores before commit.

## P5 Acceptance

- Runtime、Memory 与 Worldbook Admin 查询只读取调用者显式注入且已打开的 source；缺失或查询失败返回通用 unavailable，不推导路径、不创建目录/DB、不暴露 raw payload、证据、principal、target、operator note 或异常文本。
- 所有列表使用 1..100 有界 keyset。Worldbook cursor v2 同时绑定 proposal、decision、receipt 三个序列上界；列表成员、status 与 presence DTO 保持初始快照，detail 有意读取当前态。
- approval、reconciliation 与 Memory decision 必须先 GET server-owned context，再提交绑定 token。Admin 只追加裁决/核对记录，receipt 明示 `execution_started=false` 或 `projection_started=false`，不执行工具、不重放外部调用、不写领域投影。
- Memory `conflict_ids` 仅存在于 server preview/token/store truth，API/TS/action request 不接受浏览器值。store 在同一写事务复核完整当前 conflict set，并返回原子 inserted/existing outcome；同 task 与双连接 exact retry 都只有一个 first write。
- Rollout readiness 是 report-only attestation：缺少证据为 `not_assessed`，不能由 `false` 推断生产资源不存在；activation、rollback、Worldbook authoritative reread/no-dual-truth 等 gate 必须逐项闭合。
- Admin 页面完成 Runtime/Memory/Worldbook/readiness 只读与裁决工作流、409 refresh、503 read-only、移动端 dialog 和 44px 可访问入口；生产构建只生成静态资源，未部署。
- 最终独立安全审查关闭原四个 P2 与 ContextVar outcome bridge P3，当前快照无 P0-P3 finding。残余仅前端 409→503 仍是静态 SFC 合同、SQLite 未另起两个 OS 进程；当前单 worker 暗态边界不受影响。

## Verification

| Check | Command / Evidence | Result |
| --- | --- | --- |
| Repository gate | Git root + `# Omubot` + ACTIVE | pass |
| Dirty baseline | `git status --short` | existing tracked/untracked WIP enumerated and preserved; no stash/reset/clean |
| Tool ABI regression | `uv run pytest -q tests/test_agent_runtime_tool_abi.py tests/test_tool_registry_atomic.py tests/test_tools.py tests/test_client.py` | 94 passed |
| P0 combined regression | ABI + ledger + registry/client + plugin transaction suites | 183 passed, 40 upstream aiohttp warnings |
| Scoped Ruff | P0 implementation and tests | pass |
| Scoped Pyright | P0 implementation and tests | 0 errors, 0 warnings |
| SQLite integrity | temp explicit-path DB, v1→v2 upgrade, reopen/tamper/structure tests | `user_version=2`, `quick_check=ok` |
| P0/P1/plugin super-set | Runtime + registry/client + PluginToggle/PluginBus/command transaction suites | 284 passed, 40 upstream aiohttp warnings |
| P1 final Grok review | top `66534738-...`; child `019f80ee-...` ledger/migration/concurrency | no dark P1 Critical; stale transition/cancel fencing fixed; zero Grok writes |
| Production isolation | non-test import/wiring scan + storage filename scan | no runtime import/wiring; no Agent Runtime production DB |
| Full-repo static baseline | `uv run ruff check`; `uv run pyright` | blocked by unrelated existing/untracked WIP: Ruff 178, Pyright 394; P0 scope clean |
| Grok required parallel | top `99b25e79-260b-4e68-80de-05322889dd44`; children `019f807a-...`, `019f807c-...` | read-only findings integrated; no Grok file changes |
| Codex independent final review | `runtime_research` two-pass review | all five initial findings closed; final no P0 blocker; absent-override P2 also fixed |
| P2-2 Codex acceptance | focused runtime/tool suites + broader plugin/client suites | 158 passed; 345 passed / 82 upstream warnings |
| P2-2 static/isolation | scoped Ruff/Pyright; `git diff --check`; import/storage scan; SendSticker class diff | Ruff pass; Pyright 0/0; no wiring/DB; SendSticker byte-equivalent from class declaration to EOF |
| P2-2 Grok post-GREEN audit | top `d0481be7-b7c7-478d-9557-b235b420844d`; child `019f827e-c1d3-73d1-91cc-a5f285e42544` | findings independently reproduced; background auxiliary 503 `auth_unavailable` terminated, no duplicate dispatch |
| P2-3 focused acceptance | tools + safe HTTP + catalog + coordinator | 100 passed |
| P2-3 P2 super-set | ABI/catalog/policy/coordinator/executor/ledger/memo/sticker/registry/tools/safe HTTP | 231 passed |
| P2-3 broader compatibility | P2 super-set + client/PluginToggle/PluginBus/command transactions | 387 passed / 40 upstream aiohttp warnings |
| P2-3 static/isolation | scoped Ruff/Pyright; diff/credential/import/storage scans | Ruff pass; Pyright 0/0; no credential literals, production consumer wiring or Agent Runtime DB |
| P2-3 Grok cross-review | prior canonical Grok stream remained terminal `auth_unavailable` | not performed; no duplicate/fabricated Grok session; Codex direct re-read only |
| P2-4 conditional HTTP GET | catalog/tools/coordinator + plugin/application build | focused 198 passed; default mixed path preserved |
| P2-4 P2/broader acceptance | P2 super-set; plus client/plugin/application | 246 passed; 402 passed / 40 upstream warnings |
| P2-4 static/isolation | scoped Ruff/Pyright; diff/import/storage | Ruff pass; Pyright 0/0; no production wiring or DB |
| P2-5 OneBot focused | group catalog/coordinator/executor/tools/outbound guard/application | 228 passed; interaction-integrated focused 158 passed |
| P2-5 P2/broader acceptance | P2 + interaction/guard; plus client/plugin/application | 321 passed; 477 passed / 40 upstream warnings |
| P2-5 static/isolation | scoped Ruff/Pyright; diff/import/storage | Ruff pass; Pyright 0/0; no production wiring/DB/QQ action |
| QZone adapter runtime | all `test_agent_runtime_*.py` | 157 passed |
| QZone complete | all `test_qzone_*.py` + runtime QZone adapter | 337 passed |
| QZone/P2 broader | Runtime + QZone + tools/sticker/interaction/guard/client/plugin/application | 835 passed / 40 warnings |
| QZone static/isolation | scoped Ruff/Pyright; diff/import/storage/profile/credential scan | Ruff pass; Pyright 0/0; no production import/DB; `BUILTIN_WIRE_PROFILE.validated=false`; only intentional leak canary matched |
| Reaction focused | catalog/coordinator/interaction/outbound guard | 131 passed |
| Reaction/sticker P2 focused | Runtime + sticker + interaction + outbound guard | 275 passed |
| Reaction/sticker broader | Runtime + QZone + tools/client/plugin/application | 850 passed / 40 warnings |
| Reaction/sticker static/isolation | scoped Ruff/Pyright; diff/import/storage/profile | Ruff pass; Pyright 0/0; no production import/DB/external action |
| Reconciliation focused | ledger/service/QZone/OneBot manual resolution | 10 passed |
| Reconciliation Runtime/QZone | all Runtime + QZone tests | 501 passed |
| Reconciliation broader | P2/QZone/tools/client/plugin/application | 859 passed / 40 warnings |
| Reconciliation static/isolation | scoped Ruff/Pyright; diff/import/storage/profile | Ruff pass; Pyright 0/0; schema remains v2; no production import/DB/action |
| Post-crash P2 current-snapshot reacceptance | executor/QZone focused + Runtime/QZone + independent review | 55 passed; 501 passed; Ruff pass; Pyright 0/0; diff/isolation clean; all five stale-review findings closed |
| P3 focused acceptance | governance contracts/producers/store + shared SQLite profile | 120 passed |
| P3 shared storage compatibility | SQLite profiles/helpers/migrations/governed migrations | 40 passed |
| P3 Memo/Client/Consolidator compatibility | Memo extractor/task/tools + client + consolidator suites | 172 passed / 40 upstream aiohttp warnings |
| P3 static/isolation | scoped Ruff/Pyright; `git diff --check`; production import/storage scan | Ruff pass; Pyright 0/0; no production wiring/default DB/external action |
| P3 independent audit | original reviewer probes + ten RED/GREEN contracts | future/backdated conflict, lifecycle/schema, init cancellation, global seq and extra/missing link findings closed; final response unavailable due external policy error |
| P4 focused acceptance | `tests/test_agent_runtime_worldbook_integration.py` | 64 passed |
| P4 cross-stage compatibility | Runtime/Memory/SQLite + Worldbook/Social/Schedule/Scheduler suites | 729 passed / 3 upstream aiohttp warnings |
| P4 static/isolation | scoped Ruff/Pyright; `git diff --check`; import/storage/config/profile scans | Ruff pass; Pyright 0/0; no production import/default DB/gate/ID/external action; `validated=false` |
| P4 independent final audit | current-snapshot review + 19 adversarial probes | no P0-P3 findings; reducer/binding/receipt/world/revision/carrier closures verified |
| P5 focused acceptance | Admin query/action/context/API/readiness/Worldbook query suites | 90 passed |
| P0-P5 core | Runtime/Memory/Worldbook governance + SQLite/plugin contracts | 509 passed |
| P5 broader compatibility | tools/client/application + Worldbook/Social/Schedule/Scheduler suites | 515 passed / 42 upstream aiohttp warnings |
| P5 frontend | Node contracts；`vue-tsc --noEmit`；`npm run build` | 9 passed；typecheck/build pass |
| P5 static/isolation | scoped Ruff/Pyright；`git diff --check`；production import/config/storage/profile scans | Ruff pass；Pyright 0/0；no production wiring/default DB/config/action；`validated=false` |
| P5 independent final audit | current-snapshot review + temp DB adversarial probes | no P0-P3 findings；原四个 P2 与 outcome bridge P3 closed |

## Test Ledger

Append-only. Record RED, GREEN, cancellation, migration, collision and rollback checks with exact commands.

| ID | Command / Input | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| AR2-P0-001 | `uv run pytest -q tests/test_agent_runtime_tool_abi.py` | 1 failed: legacy Tool had no `spec` | RED confirmed missing additive ABI | 2026-07-21 |
| AR2-P0-002 | same focused test after minimal spec | 2 passed across first two slices | GREEN: legacy schema preserved and policy metadata present | 2026-07-21 |
| AR2-P0-003 | ABI + registry + tools + client regression | 94 passed, 40 upstream aiohttp warnings | No compatibility regression detected | 2026-07-21 |
| AR2-P0-004 | Ledger init/Run transition focused tests | 2 passed | GREEN: explicit DB path, migration v1, append-only Run events and terminal fail-close | 2026-07-21 |
| AR2-P0-005 | ToolCall create/transition RED then GREEN | RED `create_tool_call` missing; GREEN full proposed→succeeded event chain | Durable policy/idempotency snapshot and legal ToolCall graph established | 2026-07-21 |
| AR2-P0-006 | Cancel/restart RED then GREEN | RED missing APIs; GREEN pre-dispatch cancelled, post-dispatch/restart unknown, claimed requeued | No automatic retry from ambiguous dispatch | 2026-07-21 |
| AR2-P0-007 | Double-cancel rollback/close fault injection | RED open transaction/live connection; GREEN cleanup survives repeated cancellation | D2 cancellation contract closed | 2026-07-21 |
| AR2-P0-008 | PluginToggle base-tool RED then GREEN | RED base registry became empty; GREEN disable/enable and post-write persistence rollback preserve base | Runtime registry composition is atomic | 2026-07-21 |
| AR2-P0-009 | Migration tamper/structure RED then GREEN | semantic label, missing/wrong index and omitted column all reproduced then rejected | DDL SHA-256 plus column/FK/index structural verification | 2026-07-21 |
| AR2-P0-010 | Parent/child state and recovery atomicity fault injection | RED late call/open success/double claim/partial recover; all GREEN | Run/ToolCall cross-entity invariants and batch recovery closed | 2026-07-21 |
| AR2-P0-011 | Independent review remediation | invalid enums, empty required key and stale recovery error reproduced then GREEN | Five initial findings closed; final re-review found no P0 blocker | 2026-07-21 |
| AR2-P0-012 | P0 combined acceptance | 183 passed; scoped Ruff pass; scoped Pyright 0/0 | P0 dark structural slice accepted | 2026-07-21 |
| AR2-P0-013 | Absent plugin override write-then-raise | RED `None→true`; GREEN exact `None` restoration via `clear_override` | Inherited plugin state semantics preserved | 2026-07-21 |
| AR2-P1-001 | Planner/PolicyGate RED→GREEN | 9 policy tests pass | visibility separated from execution authority; scope/target/idempotency/approval/generation fail closed | 2026-07-21 |
| AR2-P1-002 | Executor deny/approval/allow/schema/failure/timeout/cancel RED→GREEN | 15+ executor tests pass | tool never runs before durable dispatch; unsafe/ambiguous outcomes are model-safe and durable | 2026-07-21 |
| AR2-P1-003 | Concurrency/lease migration and cross-connection RED→GREEN | v1→v2 upgrade, global/keyed/parallel claim, expired recovery pass | durable occupancy and stale-worker fencing established | 2026-07-21 |
| AR2-P1-004 | Coordinator approval/retry/registry TOCTOU RED→GREEN | 7 coordinator tests pass | same-call resume and Run waiting states established without LLMClient wiring | 2026-07-21 |
| AR2-P1-005 | Explicit P0/P1/plugin final super-set | 284 passed; scoped Ruff pass; scoped Pyright 0/0 | broad compatibility gate passed after final fencing fixes | 2026-07-21 |
| AR2-P1-006 | Independent final review remediation | stale owner status/cancel and non-exclusive recovery reproduced then GREEN | lease-owner fencing covers worker transitions/cancel; whole restart recovery is explicit-exclusive only | 2026-07-21 |
| AR2-P2-001 | RED: catalog/abi assert `ToolSpec.binding_required` / `ToolInvocationBinding` / write bind semantics before impl | AttributeError / missing contract on pre-impl tree | RED confirmed missing P2-2 binding contract | 2026-07-21 |
| AR2-P2-002 | GREEN: `uv run pytest -q tests/test_agent_runtime_tool_abi.py tests/test_agent_runtime_tool_catalog.py tests/test_agent_runtime_policy.py tests/test_agent_runtime_coordinator.py` | 41 passed | ABI binding default empty; four write specs; WRITE_LOCAL missing target deny; derived durable target/key; caller target mismatch → zero ToolCalls | 2026-07-21 |
| AR2-P2-003 | GREEN focused super-set: ABI+catalog+policy+coordinator+executor+ledger+memo+sticker+registry | 154 passed | P0/P1 + P2-1/P2-2 local tools compatible | 2026-07-21 |
| AR2-P2-004 | `tests/test_sticker_tools.py` after SendSticker restore | 41 passed | intent/base64/sub_type production send path preserved; only Save/Manage gained bind | 2026-07-21 |
| AR2-P2-005 | scoped `ruff check` + `pyright` on P2-2 modules/tests | Ruff All checks passed; Pyright 0/0 | static clean on touched scope | 2026-07-21 |
| AR2-P2-006 | `git diff --check` + production isolation scan | no whitespace errors; no `storage/agent_runtime*` | no production Agent Runtime DB; no external action | 2026-07-21 |
| AR2-P2-007 | required-parallel RO child `019f827e-c1d3-73d1-91cc-a5f285e42544` binding/spoof/TOCTOU audit | pass core bind-before-ledger + identity spoof guards; residuals: model-chosen card_id/sticker_id keys; empty-trusted manage_sticker; policy deny after create leaves ToolCall row; principal allowlist not production-wired | residual risks for Codex; not P2-2 blockers for dark slice | 2026-07-21 |
| AR2-P2-008 | RED unknown `save_sticker.image_tag` outside trusted `ctx.extra.image_tags` | expected `ValueError`; current binding did not raise | reproduced pre-ledger source-binding gap | 2026-07-21 |
| AR2-P2-009 | RED manage identity matrix: empty trusted + claimed admin; regular update; empty trusted delete fallback | 3 failed; update/delete mutated global store or accepted spoof | reproduced model-parameter authorization and inconsistent update ACL | 2026-07-21 |
| AR2-P2-010 | RED changed trusted user on approval/retry resume for a binding-required write | 2 failed; same call executed under changed trusted context | args digest alone did not preserve invocation binding across waits | 2026-07-21 |
| AR2-P2-011 | GREEN after tag/admin/resume fixes | focused new cases 5 passed; sticker/catalog/coordinator 64 passed; focused super-set 158 passed | invalid resume leaves Run/ToolCall/approval unchanged; original trusted context still resumes successfully | 2026-07-21 |
| AR2-P2-012 | broader compatibility/static/isolation acceptance | 345 passed / 82 warnings; Ruff pass; Pyright 0/0; diff/isolation/SendSticker checks pass | P2-2 dark slice accepted; no production wiring, DB, external action, commit or deploy | 2026-07-21 |
| AR2-P2-013 | P2-3 interrupted-work recovery: tools/safe HTTP/catalog | 58 passed; Ruff pass; Pyright 0/0 | fake-IP literal/domain fix preserved existing external-read slice | 2026-07-21 |
| AR2-P2-014 | RED chunked body + malformed hostname matrix | 3 failed: first chunk only; space/underscore hostname accepted | reproduced bounded-stream and DNS-label gaps; GREEN 11 passed after loop/label validation | 2026-07-21 |
| AR2-P2-015 | RED empty/zero port, raw control and oversized URL matrices | empty/zero port 2 failed; LF/DEL 2 failed; oversized URL/schema 2 failed | canonical input now rejects ambiguous port, raw controls and >8192 chars before ledger | 2026-07-21 |
| AR2-P2-016 | web_fetch coordinator target contracts | canonical target persisted; spoof target zero ToolCalls; disallowed origin denied without execute | binding/allowlist mediation proven with real WebFetchTool | 2026-07-21 |
| AR2-P2-017 | RED governed web_fetch timeout + web_search provider failure | both returned strings and were recorded `succeeded` | governed failures now `failed_retryable`/waiting_retry; legacy strings preserved | 2026-07-21 |
| AR2-P2-018 | RED unsafe DNS resolver/wrapper classification | generic OSError / wrapped ClientConnectorDNSError escaped safety type | dedicated resolution rejection translates to UnsafePublicUrl and `failed_terminal`, never retry | 2026-07-21 |
| AR2-P2-019 | safe transport cancellation/stream/redirect/charset suite | 27 safe HTTP tests pass within final focused 100 | contexts close on cancellation; byte cap spans chunks; unknown charset and redirect edges deterministic | 2026-07-21 |
| AR2-P2-020 | P2-3 P2 runtime/tool super-set | 231 passed | no P0/P1/P2-2 regression detected | 2026-07-21 |
| AR2-P2-021 | final broader/static/isolation acceptance | 387 passed / 40 upstream warnings; Ruff pass; Pyright 0/0; scans clean | P2-3 dark slice accepted locally; no real network, production wiring, DB, deploy or external action | 2026-07-21 |
| AR2-P2-022 | RED conditional `http_api` spec/binding/header matrix | 5 failed: GET-only remained legacy and unsafe method/headers bound empty | strict GET-only spec, canonical API origin and safe header allowlist established; mixed/POST stay legacy | 2026-07-21 |
| AR2-P2-023 | RED GET-only transport/coordinator | 2 failed: old `_is_safe_url`/httpx path, fake pinned transport never called | GET-only now uses safe transport and persists gated API origin | 2026-07-21 |
| AR2-P2-024 | HTTP API focused + plugin/application compatibility | 198 passed; Ruff pass; Pyright 0/0 | default GET+POST plugin shape and build path preserved; invalid method configs fail closed | 2026-07-21 |
| AR2-P2-025 | P2-4 final P2/broader acceptance | 246 passed; 402 passed / 40 upstream warnings; isolation clean | conditional GET-only slice accepted dark; POST/default config not upgraded | 2026-07-21 |
| AR2-P2-026 | RED TrustedToolContext bot passthrough | constructor rejected `bot=` | non-persistent trusted bot now reaches binding and execute ToolContext; never ledger/model payload | 2026-07-21 |
| AR2-P2-027 | RED group admin spec/binding matrix | 8 failed: legacy specs, empty targets, untrusted identity/group/bot accepted by binder | 3 tools classified with trusted targets and ALWAYS approval | 2026-07-21 |
| AR2-P2-028 | real mute coordinator approval + provider failure | approval-pending produced zero API calls; resume called once; failure became unknown/waiting_external | OneBot execution is completely mediated with no retry after ambiguous dispatch | 2026-07-21 |
| AR2-P2-029 | RED poke spec/binding + group guard | 7 failed: legacy/empty binding and poke/ban/title bypass | poke target/profile bound; group policy covers three action families; private poke preserved | 2026-07-21 |
| AR2-P2-030 | poke cancel/provider bucket cleanup + P2/broader acceptance | focused 158; P2 321; broader 477 / 40 warnings; static/isolation clean | safe OneBot subset accepted dark; reaction/sticker explicitly excluded | 2026-07-21 |
| AR2-P2-031 | QZone adapter spec/binding/dual-approval RED→GREEN | trusted `qzd_` allowlist、exact target、legacy direct-call refusal、approval before provider all pass | raw transport remains outside ToolRegistry; only governed `JournalDelivery` is callable | 2026-07-21 |
| AR2-P2-032 | provider/claim/finalize failures + secret canary | QZone/Runtime both `unknown`; provider called zero or one as phase dictates; only exception type persisted | ambiguous effects cannot become terminal/retryable and transport text cannot leak | 2026-07-21 |
| AR2-P2-033 | independent review fault injection | ToolSpec substitution、claim ambiguity、contention retry、finalization windows、cancel projection and transport leakage reproduced | all original review findings closed; two final cancellation windows remained RED | 2026-07-21 |
| AR2-P2-034 | `pytest ... -k 'cleanup_cancellation_after_provider_error or claim_inspection_survives'` | RED 2 failed: cancellation swallowed / draft left dispatching；GREEN 2 passed | shielded inspection/cleanup now finish first and then propagate cancellation without `uncancel()` | 2026-07-21 |
| AR2-P2-035 | final Runtime/QZone/broader/static/isolation acceptance | 157；337；835 passed / 40 warnings；Ruff pass；Pyright 0/0；diff/import/storage/profile scans clean | QZone adapter accepted dark; no deploy、production DB、LLM/registry wiring or external action | 2026-07-21 |
| AR2-P2-036 | reaction spec/trusted binding RED→GREEN | RED 5: legacy spec + empty binding；GREEN 5 | canonical current group/user message ref + numeric emoji bind exact durable target before ledger | 2026-07-21 |
| AR2-P2-037 | live group policy capability/runtime RED→GREEN | guard marker RED 1；blocked runtime incorrectly succeeded RED 1；GREEN 15/1 | message-id-only action now reuses live group policy; rejection is proven pre-dispatch terminal | 2026-07-21 |
| AR2-P2-038 | reaction negative/provider acceptance | untrusted ref leaves ToolCall absent；provider error→unknown；legacy/token focused 131 passed | provenance, approval, live policy and post-dispatch ambiguity all mediated | 2026-07-21 |
| AR2-P2-039 | durable target ABI + sticker spec RED→GREEN | ToolContext target RED 1；sticker spec/binding RED 4；GREEN 1/4 | exact resolved sticker ID can be persisted without changing legacy model schema | 2026-07-21 |
| AR2-P2-040 | intent rerank approval-resume TOCTOU | RED binding changed after A→B rerank；GREEN sends durable A | resume and execute consume ledger target rather than rerunning semantic search | 2026-07-21 |
| AR2-P2-041 | sticker local segment phase RED→GREEN | RED raw local exception；GREEN `ToolExecutionError(external_effect_started=False)` | pre-provider construction failure cannot become external unknown | 2026-07-21 |
| AR2-P2-042 | sticker provider/cancel + final acceptance | failure/cancel both unknown/waiting_external and send_count=0；focused 275；broader 850 / 40 warnings；static/isolation clean | reaction/sticker dark slices accepted; no production wiring or external action | 2026-07-21 |
| AR2-P2-043 | ledger reconciliation RED→GREEN | RED API missing；GREEN append-only/idempotent/conflict/multi-unknown Run projection | unknown ToolCall remains immutable; only unique resolution event changes Run readiness | 2026-07-21 |
| AR2-P2-044 | cross-connection resolution race | opposite decisions concurrently produce one record + one semantic ValueError + one event | `BEGIN IMMEDIATE` serializes truth without schema v3 or duplicate resolution | 2026-07-21 |
| AR2-P2-045 | reconciliation coordinator RED→GREEN | RED module missing 3；GREEN scope/target authorization, idempotent adapter, receipt mediation | unauthorized/forged/non-idempotent paths write no ledger and call no provider | 2026-07-21 |
| AR2-P2-046 | existing resolution short-circuit RED→GREEN | RED adapter called before conflict check；GREEN exact retry returns old record, conflict calls adapter zero times | retries cannot touch domain after Runtime truth already exists | 2026-07-21 |
| AR2-P2-047 | QZone domain reconciliation RED→GREEN | adapter missing RED；both succeeded→published and not-applied→approved GREEN | idempotent domain resolution completes before Runtime event; ToolCall remains unknown | 2026-07-21 |
| AR2-P2-048 | OneBot manual attestation RED→GREEN | adapter missing RED；owner/effect/onebot target attestation GREEN | no provider query or replay is fabricated where OneBot has no generic receipt API | 2026-07-21 |
| AR2-P2-049 | reconciliation final acceptance | focused 10；Runtime/QZone 501；broader 859 / 40 warnings；Ruff/Pyright/diff/isolation clean | P2 offline contracts accepted; production principal/wiring remains separately authorized | 2026-07-21 |
| AR2-P2-050 | post-crash current-snapshot audit: `pytest -q tests/test_agent_runtime_executor.py tests/test_agent_runtime_qzone_tool.py tests/test_qzone_journal_delivery.py`; `pytest -q tests/test_agent_runtime_*.py tests/test_qzone_*.py`; scoped Ruff/Pyright/diff/isolation + independent re-review | 55 passed；501 passed；Ruff pass；Pyright 0/0；`git diff --check` pass；无生产 Runtime DB；`validated=false`；review 无 actionable finding | 崩溃前后工作树时间差导致旧 review 报告过时；当前实现已关闭 ready/claimed terminal write、dispatch marker、QZone cleanup/claim inspection 与 executor outer cleanup 重复取消窗口 | 2026-07-21 |
| AR2-P3-001 | immutable contracts + Memo/Compaction candidate-only RED/GREEN | original focused contracts/producers GREEN；raw Memo 与 item-aligned Compaction evidence preserved | P3 producers emit proposals only; no prompt/write-path activation | 2026-07-21 |
| AR2-P3-002 | explicit-path store, atomic append, restart/idempotency/tamper RED/GREEN | original P3 baseline 102 passed | shadow ledger created only under temp paths and preserves canonical payload truth | 2026-07-21 |
| AR2-P3-003 | six independent-review REDs | concurrent init, future conflict approval, backdated post-projection conflict, extra table/view, close-vs-write all failed as expected | reviewer findings reproduced before implementation | 2026-07-21 |
| AR2-P3-004 | global append order + lifecycle/schema GREEN | store file 32 passed after two stale SQL/return-value fixtures were corrected | exact retry does not allocate twice; late conflict is recoverable; close waits for writes | 2026-07-21 |
| AR2-P3-005 | shared SQLite init cancellation + global seq collision + extra auxiliary link RED/GREEN | RED 3；GREEN 3；repeated cancellation close count=1 | acquired connections cannot leak; record order is globally unique; extra links fail closed | 2026-07-21 |
| AR2-P3-006 | missing sole observation-link discovery RED/GREEN | RED approval succeeded and persisted；GREEN missing/extra/normal focused 3 passed | canonical Conflict payload participates in discovery before exact-link verification | 2026-07-22 |
| AR2-P3-007 | final P3 acceptance | focused 120；storage 40；compatibility 172 / 40 warnings；Ruff/Pyright/diff/isolation clean | P3 accepted dark/local; no production DB/wiring/deploy/external action | 2026-07-22 |
| AR2-P3-008 | final independent re-probe handoff | reviewer had verified repaired 116-test baseline and reproduced final missing-link defect; post-fix response hit non-transient platform policy error | preserve audit truth; do not retry/replace or claim fabricated no-findings verdict | 2026-07-22 |
| AR2-P4-001 | Worldbook v1 truth mapping + initial contract RED | 24 expected failures for missing pure contracts/adapters; mapping found implicit singleton world, reducer-compliant daily Schedule path and legacy replan direct mutation | freeze dark scope; keep StoryArcStore/reducer sole fiction truth and production paths unchanged | 2026-07-22 |
| AR2-P4-002 | initial contracts/adapters + singleton/constructor hardening RED/GREEN | 36 passed after world/arc/source/evidence identity, raw-free Social, Schedule zero-evidence and durable receipt checks | independent Worldbook proposal contract; no P3 projection enum expansion | 2026-07-22 |
| AR2-P4-003 | first independent audit remediation | real reducer history mismatch, opaque binding, public receipt construction, incomplete carriers, bool revision and unstable identity all reproduced | typed source binding, logical/content identity split, reducer projection verifier and scalar proof receipt closed findings | 2026-07-22 |
| AR2-P4-004 | final re-review remediation RED/GREEN | receipt factory, Social group/message/source splice, conflicting snapshot world, bool event metadata and quote/occurred_at carriers reproduced then GREEN | supported API and canonical boundary fail closed; one test-writer final response policy-filtered after checkpoint, not retried | 2026-07-22 |
| AR2-P4-005 | final P4 acceptance | focused 64; cross-stage 729 / 3 warnings; Ruff pass; Pyright 0/0; diff/import/storage/config/profile isolation clean | P4 accepted dark/local; no production wiring/DB/StoryArc mutation/deploy/external action | 2026-07-22 |
| AR2-P4-006 | final independent current-snapshot review | 19/19 adversarial probes; focused/static/isolation rechecked; no P0-P3 findings | P4 audit closed; residual DB attestation belongs to explicit P5 store/activation boundary | 2026-07-22 |
| AR2-P5-001 | explicit-source Runtime/Memory/Worldbook query + readiness RED/GREEN | missing source no-create、bounded keyset、deep redaction、integrity/readiness and strict routes pass | Admin observability does not infer source/path or expose raw governance truth | 2026-07-22 |
| AR2-P5-002 | token-bound context/action/API/frontend RED/GREEN | approval/reconciliation/Memory decisions record only bound receipts；409 refresh/503 read-only and 44px controls pass | browser cannot reconstruct trusted invocation context and Admin cannot execute tools | 2026-07-22 |
| AR2-P5-003 | independent review four-finding RED set | 6 backend behavioral RED + 1 frontend contract RED reproduced | Memory conflict trust、concurrent retry、409→503 authority and Worldbook snapshot defects were real | 2026-07-22 |
| AR2-P5-004 | trust/concurrency/snapshot remediation | API/browser conflict spoof rejected；same-task and two-store outcome `[False, True]`；Worldbook pagination stable/detail current；frontend 9 pass | all four P2 findings closed with persistent regression coverage | 2026-07-22 |
| AR2-P5-005 | ContextVar outcome bridge review/remediation | child-task wrapper could commit then lose marker；explicit outcome API replacement actions+store 52 passed | hidden task-local side channel removed; legacy append return and cancellation behavior preserved | 2026-07-22 |
| AR2-P5-006 | final P5 acceptance and audit | focused 90；core 509；compat 515 / 42 warnings；frontend 9/type/build；Ruff/Pyright/diff/isolation clean；review no P0-P3 | P0-P5 accepted dark/local; no deploy、production DB/wiring、worker or external action | 2026-07-22 |

## Next Session Starts Here

- Direction: P0-P5 暗态架构升级已完成，tracker 关闭。生产激活不属于本项目完成条件，仍需独立授权、独立 tracker 和逐 gate attestation。
- First action if separately authorized: 冻结生产 source path、named/scoped operator authentication、principal/trusted invocation persistence/reconstruction、single-worker/exclusive recovery 与 rollback evidence；未全部 ready 前不得接 LLMClient/ToolRegistry/bootstrap。
- Open questions for that future project: store-backed ID ACL、真实 operator evidence source、provider reconciliation evidence、Worldbook authoritative reread transaction、multi-process occupancy strategy。
- Do not redo: 不重做 P0-P5 暗态合同；不把当前 Admin 页面或 `activation_authorized=false` 当作生产启用；不分类 POST、不暴露 raw QZone transport、不重放 unknown external effects。
