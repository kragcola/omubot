# Agent Runtime v2 Production Activation

> 状态：active
> mode: task
> 最后更新：2026-08-15 CST
> 当前下一步：完成本轮日志驱动 bot 行为修复的隔离提交、bot-only 发布与运行核验；随后继续等待 operator 提供真实 production source、备份、restore/rollback rehearsal 与 Worldbook witness，才可进行新的 activation 决策。全程绝不启动真实 worker。
> 阻塞：真实 production source/schema、冻结 backup SHA-256、restore/rollback rehearsal、具名 operator ACL、Worldbook witness/profile-bound manifest 仍未齐备；这些只阻断真实 worker activation，不阻断本次默认关闭的代码发布。
> 验证证据：P0-P5 dark/local 基线已在 2026-07-22 验收；本轮 provider fence focused 25 passed、Runtime/应用/router/scheduler 交叉 530 passed、范围 Ruff clean、范围 Pyright 0 errors、`git diff --check` clean。日志驱动 bot 修复补齐 raw visual CQ callback、贴图失败后续话、stale continuation 与排班 JSON 回归；交叉 199 passed、全仓 5729 passed / 17 skipped。独立复审无 P0-P1；全仓 Pyright 的 361 个 sidecar/research 既有可选依赖/类型错误可在未修改主工作树复现，未作为本次回归。生产 `qq-bot` image/commit=`6dc8ab9e8a30…`/`ba32cdf`、`agent_runtime_enabled=False`、新 Runtime source files=0、worker log events=0；NapCat ID/image/start/restart 均未变。
> 回滚入口：保持 feature gate 默认关闭；将 `omubot-bot:pre-agent-runtime-v2-fence-20260815` 重标为 `omubot-bot:latest` 后仅替换 bot，不创建 production Runtime/Memory/Worldbook DB，不接管 LLM loop，不启动 worker。NapCat 永不重建。

## Related Bot Behavior Fix (2026-08-15)

这组改动与 Runtime v2 activation 相互独立，只修复已经在生产日志中观察到的对话行为；发布后仍保持 `agent_runtime.enabled=false`。

- **表情包不看文字**：2026-08-15 20:52 的真实反馈为“但是你根本发之前不看上边的字”，旧路径只用 bot 回复做意图检索，仍发送 `stk_95fba825`。`LLMClient` 现在把当前用户文字和引用正文带入 selector；明确“不要发/不看文字/先看文字”等反馈在普通、kaomoji 和 force-send 路径统一硬 veto。
- **间隔续话断裂**：2026-08-15 16:07 的真实序列中，`？` 触发 companion rescue 后，`你怎么不叫` 被记录为 `chat text=''`，并在 16:07:24 以 `busy, skip` 丢弃。调度器现在在 stale target 找不到时回退最新真实用户消息；同用户在首段发送前续话会取消并重合并，首段后排队一次有界 follow-up。
- **排班 JSON**：模型返回解释文字或代码块包裹 JSON 时先抽取有效对象；首次解析失败只重试一次，仍失败则返回 false 且不写入坏日程。
- **回调绕过与架构基线**：明确反馈或一次贴图尝试后，normal、light、plan-then-utter、pause-extend 及 timeline/callback 写入前都会移除模型生成的 `image/mface/face` CQ，并保留 reply/at 与可见文本下限。全仓回归首次暴露 `services.tools.qzone_journal` 反向 import `plugins`；以 kernel 的 `ExternalEffectPreDispatchError` 作为中性 pre-dispatch 合同后，QZone 既有 `DeliveryPreDispatchError` 保持兼容子类，post-dispatch unknown 语义不变。

本轮隔离 release 输入还包括 `kernel/types.py`、`plugins/qzone_journal/delivery.py`、`services/tools/qzone_journal.py` 与其契约测试。贴图/排班/QZone/插件所有权交叉为 **199 passed**，完整 pytest 为 **5729 passed / 17 skipped**；Runtime activation 的真实 source/backup/Worldbook 前置仍未提供，不能以这组行为修复作为 activation 证据。

## Resume Capsule

- objective: 按 `docs/migrations/agent-runtime-v2-2026-07-21.md` 的 Future Production Activation Runbook，依次完成受限生产组合、认证/ACL、可信触发、attestation、recovery 和交付验证，同时保持所有外部效果 fail-closed。
- next_step: 只收集并独立核验真实 production source path、schema、backup payload/digest、restore/rollback rehearsal、具名 operator ACL、deployment input 与 Worldbook authoritative-reread/reducer/no-dual-truth witness。完整 manifest 到位前不得启动 worker，禁止用测试 manifest 替代。
- current_files: `bootstrap/application.py`、`services/agent_runtime/{production,executor,coordinator,invocation_store}.py`、`tools/agent_runtime_preflight.py`、`docs/runbooks/agent-runtime-v2-production-activation.md`、对应测试与本 tracker。
- last_verified: fence 在实际 provider 调用前持有进程锁和 SQLite owner/token CAS；同一 token 的不同 lease_until 可连续扩展，失权/超时/guard 异常全部终结为 `worker_not_ready`，取消 shutdown 会等待 fence 后释放 exact lease。production composition 25 passed、交叉 530 passed、preflight CLI 2 passed、范围 Ruff clean、范围 Pyright 0 errors、diff clean；新生产 `qq-bot` 为 image `sha256:6dc8ab9e8a30780f4dcdb276dd252c0cf13d29fc10a89572ddd3d31e2ddbc58b` / `GIT_COMMIT=ba32cdf`，容器 restart=0。容器内 preflight 返回 `agent_runtime_disabled`，未认证 `/api/admin/agent-runtime/summary` 为预期 401，`/app/storage` 无 Runtime 文件；NapCat 的 ID/image/start/restart 与发布前一致。实际配置没有 `agent_runtime`，所以没有 Runtime source/lease/worker。
- do_not_redo: 不重写已验收 P0-P5 dark 合同；不把 HTTP POST、OneBot 自动 reconciliation 或 raw QZone transport 标记为已迁移；不把浏览器 token 当作 principal。
- rollback: 禁用 `agent_runtime.enabled`，停止有界 worker，保留 `unknown`/`dispatching` ledger 供人工 reconcile；仅在 schema compatibility 检查后回滚 bot image，绝不重建 NapCat。

## Boundaries And Baseline

- accepted base: `3b7e6dfc7fbbadfff9ef36fc7eae06cb2d4e3397` (`main`，相对 `origin/main` 领先 38 commits)。
- dirty baseline: 90 条 `git status --short` 记录，其中 4,724 个未跟踪文件；Agent Runtime v2 本身仍是未提交 WIP，与 NapCat、课程资料及其他用户 WIP 混存。
- isolation: 不执行 `git add -A`、不清理/stash/reset 用户 WIP；不直接从当前工作树 build/deploy Docker image。若最终需要 release，必须从隔离的精确输入构造 bot-only artifact。
- external boundary: 本次实现不发送 QQ/QZone/webhook，不启用 QZone live，不改 `BUILTIN_WIRE_PROFILE.validated`，不重启/重建 NapCat。
- deployment boundary: 用户授权的 bot-only 暗态交付已完成，输入来自隔离 release worktree 的 `ba32cdf`；生产配置保持 `agent_runtime.enabled=false`。source/backup/restore/rollback/Worldbook attestation 全绿前不得把它解释为或升级为 worker activation。

## Parallel Ledger

| Workstream | Owner | Conflict domain | Isolation | Status | Checkpoint |
| --- | --- | --- | --- | --- | --- |
| ARV2-A | Codex main | runtime composition, config, schemas, tests, docs, dark deployment | isolated release worktree; single writer | in_progress | `ba32cdf` provider fence/lifecycle dark release deployed and verified; real activation artifacts remain pending |
| ARV2-B | bootstrap_tdd_tests | new bootstrap contract test only | shared workspace; sole writer for `tests/test_agent_runtime_bootstrap.py`; no production-file reads/writes | completed | Bootstrap contracts delivered; implementation integrated and cross-verified |
| ARV2-C | arv2_attestation_audit | read-only current-snapshot attestation audit | shared workspace; no writes | completed | Confirmed absent production attestors and direct worker-start bypass; findings incorporated in A9 |
| ARV2-D | arv2_a11_contracts | A11 test/repair design for profile, lease and manifest code | shared workspace; read-only, no test or production writes | completed | Confirmed four findings plus same-pattern renew lease; contracts and repair integrated by main writer |
| ARV2-E | arv2_pending_reconcile | Bootstrap/host-ingress tracker reconciliation | shared workspace; read-only, no writes | completed | No fifth established local defect; bootstrap/host ingress contracts are implemented and notices intentionally fail closed |
| ARV2-F | arv2_release_diff_review | release diff P0-P3 review | shared workspace; read-only, no writes | completed | Same-token concurrent extension finding repaired; final review has no P0-P2, with cooperative cancellation and serial provider throughput recorded as P3 constraints |
| ARV2-R2 | arv2_release_diff_review | sticker/scheduler/schedule behavior review | isolated release worktree; read-only, no writes | completed | Raw-CQ callback probes and failed-sticker continuation probe passed; no P0/P1, no files edited |
| ARV2-R3 | arv2_worker_lifecycle_tests | Runtime activation artifact inventory | shared workspace; read-only, no writes | waiting_external | Local contracts are complete; real source/backup/restore/operator/Worldbook artifacts have not been supplied and must not be synthesized |

The production implementation remains serial because composition, source paths and context ownership share one conflict domain. ARV2-B is isolated to a new test file so TDD can keep test intent separate from implementation; it must deliver a manifest before integration. Other parallelism is restricted to independent read-only checks.

## Section Progress

| Section | Status | Evidence / Note | Next Update |
| --- | --- | --- | --- |
| Context | done | P0-P5 dark/local complete; default-off production composition and bootstrap wiring complete | Keep source facts current |
| Plan | in_progress | 默认关闭 bot-only fence release `ba32cdf` 已完成生产发布；本轮 bot 行为与 QZone ownership 候选已全仓通过，等待隔离提交/build/recreate；真实 activation 仍按原 runbook 等待 artifact | 提交、bot-only 发布和只读运行核验；随后只读核验真实 artifact |
| Implementation | done | Provider 前 execution fence、exact owner/token extension、worker lifecycle、只读 preflight CLI 与 activation runbook 已完成 | 等待真实 source/restore/rollback/Worldbook evidence 才可 activation |
| Verification | in_progress | fence focused 25 passed、交叉 530 passed、CLI 2 passed、范围 Ruff/Pyright/diff clean；本轮交叉 199 passed、全仓 5729 passed / 17 skipped，独立复审无 P0/P1 | 完成本轮发布后 runtime negative verification；未来做真实 artifact preflight |
| Handoff | pending | P3: provider 必须协作取消，fence 故意串行 provider | Update when real artifacts arrive |

## Todo

- [x] Create production activation tracker and preserve dirty baseline.
- [~] Define explicit, fail-closed Runtime/Memory/Worldbook source paths, schema expectations, backup/restore and rollback attestations. Code contract plus digest-pinned/profile-bound manifest verifier done; real source/backup/restore/rollback artifacts pending.
- [x] Implement named/scoped operator authentication and store-backed exact resource ACL; browser tokens remain assertions. Admin sensitive routes require credential-authenticated named principal; tool target and Memory candidate grants are exact and checked on every context/action request.
- [x] Persist authoritative triggers and reconstruct trusted invocation context with exact target refs and registry generation. OneBot group/private ingress returns a bound receipt; router, scheduler and private LLM path forward only its exact canonical ID, while missing, malformed, cancelled or stale receipts fail closed.
- [x] Compose production principal, ToolRegistry, LLMClient and bootstrap without changing legacy behavior while disabled. Assembly occurs after plugin-tool merge; disabled config has no source/LLM/context side effects and never starts a worker.
- [~] Attest source integrity, single worker, exclusive recovery, Worldbook authoritative reread/no-dual-truth and rollback. `start_worker()` now requires dark, activation and rollback reports before it can acquire a lease; the digest-pinned evidence reader is complete, but real evidence and Worldbook authoritative integration remain.
- [~] Rehearse dark readiness and rollback; synthetic source/manifest rehearsal passes, while a real rehearsal is blocked on operator-supplied artifacts. Enable a bounded worker only after every gate is independently ready.
- [~] Run focused/compat/static/storage-isolation/external-effect-negative verification and independent current-snapshot review. A11 focused/static/negative/runtime checks pass; real-artifact verification is pending.
- [x] Close A11 local fail-closed audit: `start_worker()` re-runs source/backup preflight before lease; acquire and renew cleanup exact committed tokens before cancellation propagates; every enabled profile requires a digest-pinned manifest and production rejects callback readiness; boolean manifest schema versions are rejected. RED 4+2 failed, focused 45 passed and cross 238 passed.
- [x] Close current-snapshot release audit: recheck the exact worker lease before every tool use; project pre-dispatch cancellation to a cancelled run; classify group-policy denial as terminal; inject only offline OneBot reconciliation; require Admin operator headers without logging out the browser cookie session; reject unknown Runtime/Memory query keys; correct every rollback gate reference to `agent_runtime.enabled`.
- [x] Commit, build and deploy the user-authorized dark release. `6880dd0` -> image `sha256:4f02f5b17f66…`; `agent_runtime.enabled=false`，未创建 Runtime/Memory/Worldbook production DB，未启动 worker；旧 image/static manifest 已保存，NapCat 未操作。
- [x] Close execution-fence and lifecycle audit: provider entry now reserves the exact owner/token lease until tool completion; same-token extensions do not shorten TTL; lease extension/renew/stop cancellation cleans up observable ownership; guarded dispatches fail terminal as `worker_not_ready`; default-off bootstrap creates no source or worker, while fully attested startup owns one bounded worker.
- [x] Commit, build and deploy the user-authorized fence follow-up as bot-only dark code. `ba32cdf` -> image `sha256:6dc8ab9e8a30…`; container image/API/preflight/no-source/no-worker/NapCat invariants are verified, and `omubot-bot:pre-agent-runtime-v2-fence-20260815` preserves `6880dd0` rollback.
- [~] Deliver the independent log-driven sticker/scheduler/schedule behavior fix plus the discovered QZone ownership-boundary repair. Cross suite 199 passed、full pytest 5729 passed / 17 skipped、Ruff/Pyright/diff clean; commit/build/deploy/runtime evidence is pending. This item does not authorize Runtime v2 worker activation.

## Decisions

| Decision | Choice | Why | Date |
| --- | --- | --- | --- |
| Activation order | Inside-out TDD, then composition | Prevent a bootstrap path from silently deriving authority or storage | 2026-08-14 |
| Storage source | Explicit configured paths, no admin-state discovery | P5 contract requires caller-owned sources and no-create-on-missing behavior | 2026-08-14 |
| Production effects | Disabled until full readiness | `not_ready` and `not_assessed` are hard stops | 2026-08-14 |
| Release input | Isolated artifact only | Current worktree contains unrelated user WIP and untracked runtime files | 2026-08-14 |
| Attestation transport | SHA-256-pinned, profile-bound strict manifest | Keeps operator evidence explicit, repeatable and redacted without allowing test callbacks to replace configured deployment evidence | 2026-08-14 |
| A11 evidence closure | Enabled profile pin plus internal-only attestor and exact-token cancellation cleanup | Removes test callback authority, post-assembly backup drift and acquire/renew orphan leases before any external artifact decision | 2026-08-14 |
| Provider execution fence | Hold a process-local fence through provider execution and extend the exact SQLite owner/token before entry | Prevents stale workers from crossing into a provider call; provider execution is intentionally serial and requires cooperative cancellation during future activation | 2026-08-15 |
| QZone pre-dispatch contract | Put the generic no-external-effect error in `kernel.types`; retain the QZone exception as a subclass | Removes services-to-plugin reverse import without converting post-dispatch ambiguity into a terminal failure | 2026-08-15 |

## Files Touched

| File | Change | Status |
| --- | --- | --- |
| `docs/tracking/agent-runtime-v2-production-activation-2026-08-14.md` | Active production activation ledger | done |
| `docs/tracking/ACTIVE.md` | Recovery pointer to this tracker | done |
| `kernel/config.py` | Default-off Agent Runtime production profile inputs | done |
| `services/agent_runtime/activation.py` | Explicit source/evidence validation and read-only preflight | done |
| `services/agent_runtime/operator_auth.py` | Named credential, scope and exact resource-ref ACL store | done |
| `services/agent_runtime/admin_operator.py` | Request-scoped Admin principal/action factory over named credential and live ACL | done |
| `services/agent_runtime/admin_actions.py` | Exact tool-target and Memory-candidate resource authorization hook | done |
| `services/agent_runtime/invocation_store.py` | Immutable authoritative trigger persistence/reconstruction store | done |
| `services/agent_runtime/{production,executor,coordinator}.py` | Provider execution fence and terminal `worker_not_ready` plumbing | done |
| `services/agent_runtime/host_ingress.py` | OneBot identity to immutable trusted-record receipt adapter | done |
| `services/agent_runtime/production.py` | Durable single-worker lease, exclusive startup recovery and dispatcher gate | done |
| `services/agent_runtime/rollout_attestation.py` | SHA-256-pinned strict manifest reader bound to the full activation profile | done |
| `services/agent_runtime/rollout_readiness.py` | Named gate-set constants shared with the attestation reader | done |
| `bootstrap/application.py` | Default-off assembly lifecycle after plugin-tool merge; cancellation-complete teardown | done |
| `kernel/types.py` / `admin/__init__.py` | Explicit Runtime/Memory/Worldbook query, readiness and operator-factory publication | done |
| `kernel/config.py` | Default-empty all-or-nothing attestation manifest input | done |
| `docs/migrations/agent-runtime-v2-2026-07-21.md` | Dark manifest contract and activation hard-gate runbook supplement | done |
| `maintenance-log.md` | Durable attestation/hard-gate handoff record | done |
| `kernel/router.py` | Bound receipt validation and group/private host-adapter propagation | done |
| `services/scheduler.py` | Per-trigger invocation propagation without stale-ID reuse | done |
| `services/llm/arbiter.py` | Internal-only pending trigger invocation carrier | done |
| `services/humanization/qq_interactions.py` | Configured-ingress notice path fails closed without trusted message identity | done |
| `tests/test_agent_runtime_activation_profile.py` | Profile/config source integrity contracts | done |
| `tests/test_agent_runtime_operator_auth.py` | Operator auth/ACL and cancellation contracts | done |
| `tests/test_agent_runtime_invocation_store.py` | Trigger persistence/reconstruction and cancellation contracts | done |
| `tests/test_agent_runtime_production_composition.py` | Composition, worker ownership, recovery and cancellation contracts | done |
| `tests/test_agent_runtime_bootstrap.py` | Default-off/attested worker lifecycle and renewal shutdown contracts | done |
| `tests/test_agent_runtime_executor.py` / `tests/test_agent_runtime_invocation_store.py` | Guard ABI plus owner/token extension and cancellation contracts | done |
| `tools/agent_runtime_preflight.py` / `tests/test_agent_runtime_preflight_cli.py` | Secret-safe, read-only source and manifest readiness command | done |
| `docs/runbooks/agent-runtime-v2-production-activation.md` | Production input, canary, cancellation and rollback procedure | done |
| `tests/test_agent_runtime_admin_operator_http.py` | Cookie/principal separation and exact HTTP resource ACL contracts | done |
| `tests/test_agent_runtime_host_ingress.py` | Receipt identity, scheduler merge and cancellation contracts | done |
| `tests/test_router_b_cluster_wiring.py` | Group/private host ingress, stale receipt and coalescer cancellation contracts | done |
| `tests/test_router_qq_interactions.py` | Notice path canonical-ID/fail-closed contract | done |
| `services/llm/client.py` | Include current user/quote text in sticker intent and hard-veto explicit feedback | release candidate |
| `services/sticker/decision_provider.py` | Preserve feedback veto as a single decision-point guard | release candidate |
| `services/scheduler.py` | Recover latest user content from stale anchors and merge same-user continuations | release candidate |
| `plugins/schedule/generator.py` | Extract embedded/fenced JSON and retry once without writing invalid schedules | release candidate |
| `tests/test_sticker_context_regression.py` / `tests/test_scheduler.py` / `tests/test_schedule_generator.py` | Log-shaped regressions for sticker context, interval continuation and schedule parsing | release candidate |
| `kernel/types.py` / `plugins/qzone_journal/delivery.py` / `services/tools/qzone_journal.py` | Neutral pre-dispatch contract removes reverse plugin import while preserving QZone failure phase | release candidate |
| `tests/test_agent_runtime_qzone_tool.py` | Neutral pre-dispatch mapping contract | release candidate |

## Verification

| Check | Command / Evidence | Result |
| --- | --- | --- |
| Workspace health | `source ./scripts/dev/env.sh && bash ./scripts/dev/doctor.sh` | 0 fail / 0 warn |
| Dirty baseline | `git status --short` / `git ls-files --others --exclude-standard` | 90 entries / 4,724 untracked files |
| Runtime state | `docker compose ps` plus container source inspection from prior recovery | qq-bot live but does not contain Runtime v2 backend or governance DB |
| Production inner contracts | `uv run pytest tests/test_agent_runtime_activation_profile.py tests/test_agent_runtime_operator_auth.py tests/test_agent_runtime_invocation_store.py -q` | 16 passed |
| Production composition | `uv run pytest tests/test_agent_runtime_production_composition.py tests/test_agent_runtime_activation_profile.py tests/test_sticker_placement.py tests/test_tool_registry_atomic.py -q` | 30 passed / 9 upstream aiohttp deprecation warnings |
| Production A1-A6 focused | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_agent_runtime_activation_profile.py tests/test_agent_runtime_operator_auth.py tests/test_agent_runtime_invocation_store.py tests/test_agent_runtime_production_composition.py -q` | 24 passed |
| Runtime execution regression | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_agent_runtime_ledger.py tests/test_agent_runtime_coordinator.py tests/test_agent_runtime_executor.py -q` | 85 passed |
| Admin principal/ACL focused | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_agent_runtime_admin_operator_http.py tests/test_agent_runtime_admin_api.py tests/test_agent_runtime_admin_contexts.py tests/test_agent_runtime_worldbook_admin_api.py tests/test_agent_runtime_production_composition.py -q` | 37 passed |
| Authoritative host ingress | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_agent_runtime_host_ingress.py tests/test_agent_runtime_production_composition.py tests/test_router_b_cluster_wiring.py tests/test_router_qq_interactions.py tests/test_scheduler.py tests/test_agent_runtime_invocation_store.py -q` | 142 passed; group/private receipt, stale/malformed receipt, cancellation and missing-ID negative paths covered |
| Scoped static analysis | `uv run ruff check ...` and `uv run pyright ...` for the new profile/store modules | clean / 0 errors |
| Admin governance frontend | `node --experimental-strip-types --test tests/agent-runtime-governance.test.ts`; `vue-tsc --noEmit`; `npm run build` | 10 passed; typecheck passed; Vite 4421 modules built (existing Rollup `#__PURE__` warnings only) |
| Production dark release | isolated `docker compose build bot`; active `docker compose up -d --no-deps --force-recreate --no-build bot`; read-only inspect/curl/config/storage checks | image/commit match; SPA+asset 200; unauth summary 401; gate=false; source files/worker log events=0; bot healthy; NapCat image/start/restart unchanged |
| Provider execution fence | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest -p no:cacheprovider tests/test_agent_runtime_production_composition.py -q` | 25 passed; stale lease, same-token concurrency, guard failure, overlong timeout and cancellation shutdown paths covered |
| Fence cross regression | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest -p no:cacheprovider tests/test_agent_runtime_*.py tests/test_application_composition.py tests/test_router_b_cluster_wiring.py tests/test_router_qq_interactions.py tests/test_scheduler.py -q` | 530 passed |
| Fence static analysis | scoped Ruff and Pyright over changed Runtime/bootstrap/preflight modules; `git diff --check` | clean / 0 errors / clean; whole-repository Pyright has 361 pre-existing optional sidecar/research errors reproduced in unchanged main |
| Read-only preflight CLI | `tests/test_agent_runtime_preflight_cli.py`; CLI against production config | 2 passed; production reports `not_ready/agent_runtime_disabled`, without opening source stores |
| Independent release review | ARV2-F read-only P0-P3 review | no P0-P2; provider cooperative-cancel and serialized throughput recorded as P3 activation constraints |
| Provider fence dark release | isolated `GIT_COMMIT=ba32cdf docker compose build bot`; active `docker compose up -d --no-deps --force-recreate --no-build bot`; container/API/preflight/storage/log inspection | `qq-bot` image `6dc8ab9e…`, `GIT_COMMIT=ba32cdf`, restart=0; CLI disabled; Runtime API 401; no Runtime file; NapCat ID/image/start/restart unchanged |
| Log-driven behavior regression | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest -q tests/test_sticker_context_regression.py tests/test_scheduler.py tests/test_schedule_generator.py`; scoped Ruff/Pyright/diff | **129 passed**; Ruff clean; Pyright 0 errors/0 warnings; diff clean; includes exact feedback “根本发之前不看上边的字” and “你怎么不叫” continuation |

## Test Ledger

| ID | Command / Input | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| A0 | `source ./scripts/dev/env.sh && bash ./scripts/dev/doctor.sh` | 0 fail / 0 warn | Active workspace is healthy; dirty baseline is logical, not filesystem corruption | 2026-08-14 |
| A1-RED | `uv run pytest tests/test_agent_runtime_activation_profile.py -q` | 5 failed: `ProductionActivationProfileV1` absent | Captured explicit source/profile contract before implementation | 2026-08-14 |
| A1-GREEN | Same focused profile suite | 6 passed | Default-off config and only-read profile preflight satisfy source evidence contract | 2026-08-14 |
| A2-RED | `uv run pytest tests/test_agent_runtime_operator_auth.py -q` | 4 failed: `OperatorAuthorizationStoreV1` absent | Captured named operator/ACL contract before implementation | 2026-08-14 |
| A2-GREEN | Same focused operator suite | 4 passed | Credential, exact ACL, revocation and shutdown cancellation are covered | 2026-08-14 |
| A3-RED | `uv run pytest tests/test_agent_runtime_invocation_store.py -q` | 3 failed: `TrustedInvocationStoreV1` absent | Captured authoritative trigger contract before implementation | 2026-08-14 |
| A3-GREEN | Combined focused suites | 14 passed; scoped Ruff clean; scoped Pyright 0 errors | Profile, operator and invocation layers compose statically without type debt | 2026-08-14 |
| A4-RED | `uv run pytest tests/test_agent_runtime_activation_profile.py -q` | 1 failed / 6 passed: profile lacked `principal_scopes` | Captured the explicit principal policy contract before wiring | 2026-08-14 |
| A4-GREEN | Same profile suite; then combined profile/operator/invocation suite | 8 passed; combined 16 passed; scoped Ruff clean; Pyright 0 errors | Enabled profile requires nonempty explicit scopes and exact target refs; scope/target wildcards fail closed | 2026-08-14 |
| A5-RED | `uv run pytest tests/test_agent_runtime_production_composition.py -q` | 4 failed: production composition module and LLM dispatcher bridge absent | Captured disabled/preflight/dispatcher no-fallback contract before implementation | 2026-08-14 |
| A5-GREEN | Composition + profile + legacy sticker/registry regression | 30 passed / 9 aiohttp deprecation warnings; scoped Ruff clean; Pyright 0 errors | Enabled composition opens only all-ready explicit sources; selected dispatcher rejects absent invocation/worker and disables post-reply legacy sticker bypass | 2026-08-14 |
| A6-VERIFY | A1-A6 focused suite; runtime ledger/coordinator/executor regression; scoped Ruff/Pyright | 24 passed; 85 passed; Ruff clean; Pyright 0 errors | Worker lease is durable, exclusive and token-bound; startup requires exclusive recovery; dispatcher executes only while current lease is held; stop/cancellation releases it before propagating cancellation | 2026-08-14 |
| A7-RED | `uv run pytest tests/test_agent_runtime_admin_operator_http.py -q` | 2 failed: credential-authenticated Admin operator factory absent | Captured cookie/principal separation and exact tool-target/Memory-candidate ACL contract before implementation | 2026-08-14 |
| A7-GREEN | Same focused test plus Admin/Worldbook/production composition regression | 2 new passed; combined 37 passed; Ruff clean; Pyright 0 errors | Admin sensitive routes now require named Bearer credential, never use browser cookie as actor, and enforce live exact resource grants | 2026-08-14 |
| A8-ID-RED | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_router_b_cluster_wiring.py::test_configured_host_ingress_rejects_missing_onebot_message_identity -q` | 2 failed: group/private mock ingress returned a record for stringified `None` | Missing host message identity was not rejected before a replaceable ingress adapter | 2026-08-14 |
| A8-GREEN | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_agent_runtime_host_ingress.py tests/test_agent_runtime_production_composition.py tests/test_router_b_cluster_wiring.py tests/test_router_qq_interactions.py tests/test_scheduler.py tests/test_agent_runtime_invocation_store.py -q` | 142 passed; Ruff clean; Pyright 0 errors; `git diff --check` clean | Router canonicalizes group/user/message IDs before ingress; only a canonical `TrustedInvocationRecordV1` bound to the current group/user/message/session/OneBot ref reaches `LLMClient`; stale/malformed/missing/cancelled paths do not fall back | 2026-08-14 |
| A9-RED | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_agent_runtime_production_composition.py::test_worker_start_requires_attested_activation_and_rollback_before_lease -q` | 1 failed: `start_worker()` acquired a lease without raising | Confirmed direct worker-start bypass despite all production attestation gates being `not_assessed` | 2026-08-14 |
| A9-GREEN | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_agent_runtime_production_composition.py tests/test_agent_runtime_rollout_readiness.py tests/test_agent_runtime_rollout_attestation.py tests/test_agent_runtime_bootstrap.py tests/test_application_composition.py -q` | 49 passed | Worker startup now checks dark/activation/rollback readiness before lease; missing, partial, invalid and cancelled attestation leave no worker lease or dispatcher activation | 2026-08-14 |
| A10-RED | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_agent_runtime_rollout_manifest.py::test_pinned_profile_bound_manifest_returns_only_readiness_gate_fields -q` | 1 failed: digest-pinned reader absent; automatic composition test then reported activation `not_ready` | Captured missing evidence transport and missing profile-to-readiness binding | 2026-08-14 |
| A10-GREEN | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_agent_runtime_activation_profile.py tests/test_agent_runtime_rollout_manifest.py tests/test_agent_runtime_rollout_readiness.py tests/test_agent_runtime_rollout_attestation.py tests/test_agent_runtime_production_composition.py tests/test_agent_runtime_bootstrap.py tests/test_application_composition.py -q` | 67 passed | Strict manifest digest/profile/gate schema is enforced; evidence refs are redacted; pinned evidence auto-binds but cannot be callback-overridden; tamper leaves no lease | 2026-08-14 |
| A10-CROSS | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest` over Runtime profile/operator/invocation/composition/readiness/manifest/bootstrap/application/host ingress/router/scheduler/Admin focused modules | 234 passed | Cross-module legacy, Admin, host ingress and external-effect-negative contracts remain intact; no service restart or external send was used | 2026-08-14 |
| A10-RUNTIME | `bash ./scripts/dev/doctor.sh`; read-only config/storage/container inspection | doctor 0 fail / 0 warn; config has no `agent_runtime`; no matching storage source; qq-bot lacks `/app/services/agent_runtime` | Current runtime is not carrying this WIP; real production readiness is still blocked rather than inferred | 2026-08-14 |
| A11-AUDIT | Independent source review of `production.py`, `activation.py`, `invocation_store.py`, `rollout_attestation.py` and existing contracts | Four findings: assembly-time-only backup preflight, post-commit lease cancellation window, unpinned enabled callback path, and `True == 1` schema acceptance | Do not collect or infer real artifacts until all four have RED-GREEN evidence; no existing lease/dispatcher safety claim covers them | 2026-08-14 |
| A11-RED | Four narrow A11 contracts: backup tamper after assembly, unpinned callback/profile, acquire cancellation after commit, boolean schema | 4 failed exactly as expected | Captured the four independent local security gaps before repair | 2026-08-14 |
| A11-LEASE-RED | Direct acquire and renew cancellation after underlying SQLite commit | 2 failed: second owner could not acquire | Same-pattern scan proved renew had the same opaque-token availability defect | 2026-08-14 |
| A11-GREEN | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest` over activation profile, manifest, production composition, invocation store and bootstrap contracts | 45 passed | Enabled config needs pin; manifest-only attestation, start-time backup recheck, schema type check and acquire/renew cancellation cleanup are covered | 2026-08-14 |
| A11-CROSS | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest` over Runtime/Admin/host ingress/router/scheduler focused modules | 238 passed | Stronger production inputs preserve default-off bootstrap, legacy routing and existing Admin/host contracts | 2026-08-14 |
| A11-STATIC-RUNTIME | Scoped Ruff/Pyright, `git diff --check`, doctor, read-only config/storage/container inspection | Ruff clean; Pyright 0 errors; doctor 0 fail / 0 warn; no config/storage source; `qq-bot` lacks Runtime v2 | No deployment, worker start, production DB creation or NapCat operation occurred | 2026-08-14 |
| A12-AUDIT | Independent current-snapshot release review of worker lease, cancellation, group guard, reconciliation, Admin transport, query routes and rollback docs | 7 actionable gaps, all reproduced against the staged release input | Closed local release gaps without supplying any real activation artifact or opening the worker | 2026-08-14 |
| A12-STATIC | `uv run --no-sync ruff check` and `uv run --no-sync pyright` over every Python file changed from `HEAD`; both diff checks | Ruff clean; Pyright 0 errors / 0 warnings; both diff checks clean | Release input has no scoped lint, type or whitespace debt | 2026-08-14 |
| A12-CROSS | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run --no-sync pytest -q` over all `test_agent_runtime_*`, governed Memory/Worldbook, router, scheduler, guard and affected tool modules | 879 passed in 18.26s | Runtime v2 changes preserve governed execution, dark bootstrap, legacy route and external-effect-negative contracts | 2026-08-14 |
| A12-FRONTEND | `node --experimental-strip-types --test tests/agent-runtime-governance.test.ts && vue-tsc --noEmit && npm run build` | 10 passed; typecheck passed; Vite built 4,421 modules | Admin operator header transport, read-only governance views and the generated SPA entry are release-ready; only existing Rollup `#__PURE__` warnings remain | 2026-08-14 |
| A12-PROD-DARK | Build from isolated commit `6880dd0`; backup old image/static manifest; sync verified SPA; active `docker compose up -d --no-deps --force-recreate --no-build bot`; inspect/curl/config/storage/log counts | `qq-bot` image `4f02f5b17f66…` / commit `6880dd0…`, restart=0; SPA+asset=200; unauth summary=401; gate=false; new source files=0; worker log events=0; post-deploy error events=0; NapCat unchanged/restart=0 | Default-off code is live and externally inert. Rollback is `omubot-bot:pre-agent-runtime-v2-dark-20260814` plus static snapshot `agent-runtime-v2-dark-20260814.WhgUDO`; no worker activation claim | 2026-08-14 |
| A13-RED | Independent stale-lease provider-entry and bootstrap lifecycle tests before fence/lifecycle implementation | Provider was reachable after expired L1; all-ready bootstrap did not own a worker; renewal/shutdown contracts failed | Captured provider-entry, automatic lifecycle and cancellation gaps before repair | 2026-08-15 |
| A13-GREEN | Production composition fence suite; invocation-store, executor, bootstrap and preflight CLI suites | 25 + 9 + 27 + 11 + 2 passed | Exact owner/token fence, extension cleanup, guard ABI, single worker lifecycle and disabled-safe preflight are covered | 2026-08-15 |
| A13-CROSS | Runtime/application/router/scheduler regression command above | 530 passed in 18.44s | Fence/lifecycle changes preserve selected dispatcher, trusted ingress, legacy routing and cancellation behavior | 2026-08-15 |
| A13-STATIC | scoped Ruff/Pyright, `git diff --check`, CLI disabled production report | Ruff clean; Pyright 0 errors on changed modules; diff clean; CLI `not_ready/agent_runtime_disabled` | Full Pyright's 361 optional-dependency errors reproduce in unchanged main and are outside this release scope | 2026-08-15 |
| A13-REVIEW | ARV2-F final read-only release review | No P0-P2; same-token concurrent extension regression added and passed | Provider must cooperate with cancellation; fence serializes provider execution, both recorded for canary evaluation | 2026-08-15 |
| A13-PROD-DARK | Tag `4f02f5b17f66…` as `omubot-bot:pre-agent-runtime-v2-fence-20260815`; isolated `GIT_COMMIT=ba32cdf docker compose build bot`; tag `6dc8ab9e…` as `omubot-bot:latest`; active bot-only recreate; inspect/curl/container CLI/storage/logs | `qq-bot` image `6dc8ab9e…` / commit `ba32cdf`, restart=0; preflight `not_ready/agent_runtime_disabled`; unauth Runtime summary 401; no Runtime storage file; NapCat `19f6cf…` / v4.15.0 / same start / restart=0 | Default-off fence code is live and externally inert for Agent Runtime. Startup exposed one non-fatal pre-existing schedule LLM JSON parse warning; bot reached ready state and schedule module is outside this diff | 2026-08-15 |
| B1-LOG-RED | Read-only `docker logs` around group `993065015` 16:07 and 20:52 | Old path recorded `chat text=''`, then `busy, skip`; feedback turn still sent `stk_95fba825` after “根本发之前不看上边的字” | Reproduced both user-visible failures before the behavior candidate | 2026-08-15 |
| B1-GREEN | Isolated sticker/scheduler/schedule regression suite plus scoped static checks | 129 passed; Ruff clean; Pyright 0 errors/0 warnings; `git diff --check` clean | Current-user/quote context, feedback veto, stale-anchor fallback, same-user cancel/remerge and strict schedule retry are covered; awaiting runtime rollout evidence | 2026-08-15 |
| B1-RAW-CQ | Independent `arv2_bot_final_review` callback probes plus failed-sticker continuation probe | 4 callback paths and failed delivery continuation passed; no P0/P1 | Visual CQ cannot bypass explicit feedback or one-send budget through light/planner/pause callbacks or timeline writes | 2026-08-15 |
| B1-ARCH-RED | Full pytest before the QZone repair | 1 failed / 5727 passed / 17 skipped: `services/tools/qzone_journal.py` imported `plugins.qzone_journal.delivery` | Isolated a pre-existing release-base ownership violation; not caused by the bot behavior diff, but a release blocker | 2026-08-15 |
| B1-ARCH-GREEN | QZone tool, delivery and ownership focused tests; sticker/scheduler/schedule/QZone cross suite | 17 + 14 + 1 passed; cross suite 199 passed; Ruff clean; Pyright 0 errors/0 warnings; diff clean | A kernel-level phase contract removes the reverse import while preserving terminal pre-dispatch and ambiguous post-dispatch classification | 2026-08-15 |
| B1-FULL | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run --no-sync pytest -p no:cacheprovider -q` | 5729 passed / 17 skipped / 206 warnings in 75.90s | Full regression gate is green; warnings are existing aiohttp/NoneBot deprecations, not failures | 2026-08-15 |

## Next Session Starts Here

- Direction: Default-off fence release `ba32cdf` is live. Real activation remains blocked by missing operator-owned artifacts; preserve the current dark state.
- First action: Obtain explicit operator-owned source locations/schema, backup payloads/digests, restore and rollback rehearsal records, named operator ACL and Worldbook authoritative-reread/reducer/no-dual-truth witness. Verify them read-only, including provider cooperative-cancellation and serial-throughput canary evidence, then produce a new independent review before any activation decision.
- Open questions: Exact production source locations, backup artifact owner, restore rehearsal, rollback rehearsal and Worldbook witness remain unavailable; they must never be inferred from Admin state or replaced with test manifests.
- Do not redo: P0-P5 dark/local implementation or the seven completed current-snapshot fixes. Do not turn dark deployment into real activation.
