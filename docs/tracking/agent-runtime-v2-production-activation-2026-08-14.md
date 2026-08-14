# Agent Runtime v2 Production Activation

> 状态：active
> mode: task
> 最后更新：2026-08-14 CST
> 当前下一步：用户已明确授权仅在 `agent_runtime.enabled=false` 条件下发布暗态代码；先提交隔离 release 输入、备份当前 bot/admin 静态产物，再 bot-only 替换并验证运行态。全程绝不启动 worker。
> 阻塞：真实 production source/schema、冻结 backup SHA-256、restore/rollback rehearsal、具名 operator ACL、Worldbook witness/profile-bound manifest 仍未齐备；这些只阻断真实 worker activation，不阻断本次默认关闭的代码发布。
> 验证证据：P0-P5 dark/local 基线已在 2026-07-22 验收；2026-08-14 当前快照审计的七项 release 缺口已关闭；production composition 19 passed，Runtime/Memory/Worldbook/router/guard 交叉回归 879 passed，Ruff clean、Pyright 0 errors、`git diff --check` clean。worker 缺失/部分/无效/取消/manifest/backup 篡改均在 lease 前 fail-closed。
> 回滚入口：保持 feature gate 默认关闭；不创建 production Runtime/Memory/Worldbook DB，不接管 LLM loop，不启动 worker；恢复 legacy loop。NapCat 永不重建。

## Resume Capsule

- objective: 按 `docs/migrations/agent-runtime-v2-2026-07-21.md` 的 Future Production Activation Runbook，依次完成受限生产组合、认证/ACL、可信触发、attestation、recovery 和交付验证，同时保持所有外部效果 fail-closed。
- next_step: 提交当前已审计 release，再从隔离 worktree 构建 bot-only image 和 Admin 静态产物，以 `agent_runtime.enabled=false` 替换生产 bot；随后核验容器 commit、Runtime API、无 lease/新 production DB/外部效果。真实 activation 继续等待完整 manifest 与 operator-owned artifact，禁止用测试 manifest 替代。
- current_files: `kernel/config.py`、`services/agent_runtime/`、`services/memory/governance_*.py`、`services/worldbook/governance_*.py`、`services/llm/client.py`、`bot.py`、`admin/__init__.py`、`admin/routes/api/`、对应测试与本 tracker。
- last_verified: 当前快照审计关闭 worker 多 tool lease、取消 run 收束、group-policy terminal、offline reconciliation adapter、Admin operator header/401、GET query strictness 和 rollback key 七项缺口；production composition 19 passed、跨域交叉 879 passed、Ruff clean、Pyright 0 errors、diff clean。前一 tracker 的 P0-P5 focused 90、core 509、compatibility 515 / 42 upstream warnings、frontend contracts 9、vue-tsc/build 仍有效；当前运行 `qq-bot` 容器未包含 Runtime v2 后端或 governance DB。
- do_not_redo: 不重写已验收 P0-P5 dark 合同；不把 HTTP POST、OneBot 自动 reconciliation 或 raw QZone transport 标记为已迁移；不把浏览器 token 当作 principal。
- rollback: 禁用 `agent_runtime.enabled`，停止有界 worker，保留 `unknown`/`dispatching` ledger 供人工 reconcile；仅在 schema compatibility 检查后回滚 bot image，绝不重建 NapCat。

## Boundaries And Baseline

- accepted base: `3b7e6dfc7fbbadfff9ef36fc7eae06cb2d4e3397` (`main`，相对 `origin/main` 领先 38 commits)。
- dirty baseline: 90 条 `git status --short` 记录，其中 4,724 个未跟踪文件；Agent Runtime v2 本身仍是未提交 WIP，与 NapCat、课程资料及其他用户 WIP 混存。
- isolation: 不执行 `git add -A`、不清理/stash/reset 用户 WIP；不直接从当前工作树 build/deploy Docker image。若最终需要 release，必须从隔离的精确输入构造 bot-only artifact。
- external boundary: 本次实现不发送 QQ/QZone/webhook，不启用 QZone live，不改 `BUILTIN_WIRE_PROFILE.validated`，不重启/重建 NapCat。
- deployment boundary: 用户已授权本次 bot-only 暗态交付，输入必须来自隔离 release worktree，生产配置保持 `agent_runtime.enabled=false`；source/backup/restore/rollback/Worldbook attestation 全绿前不得把它解释为或升级为 worker activation。

## Parallel Ledger

| Workstream | Owner | Conflict domain | Isolation | Status | Checkpoint |
| --- | --- | --- | --- | --- | --- |
| ARV2-A | Codex main | runtime composition, config, schemas, tests, docs, dark deployment | isolated release worktree; single writer | in_progress | Current-snapshot seven-gap audit closed; local cross verification 879 passed; preparing authorized default-off delivery |
| ARV2-B | bootstrap_tdd_tests | new bootstrap contract test only | shared workspace; sole writer for `tests/test_agent_runtime_bootstrap.py`; no production-file reads/writes | completed | Bootstrap contracts delivered; implementation integrated and cross-verified |
| ARV2-C | arv2_attestation_audit | read-only current-snapshot attestation audit | shared workspace; no writes | completed | Confirmed absent production attestors and direct worker-start bypass; findings incorporated in A9 |
| ARV2-D | arv2_a11_contracts | A11 test/repair design for profile, lease and manifest code | shared workspace; read-only, no test or production writes | completed | Confirmed four findings plus same-pattern renew lease; contracts and repair integrated by main writer |
| ARV2-E | arv2_pending_reconcile | Bootstrap/host-ingress tracker reconciliation | shared workspace; read-only, no writes | completed | No fifth established local defect; bootstrap/host ingress contracts are implemented and notices intentionally fail closed |

The production implementation remains serial because composition, source paths and context ownership share one conflict domain. ARV2-B is isolated to a new test file so TDD can keep test intent separate from implementation; it must deliver a manifest before integration. Other parallelism is restricted to independent read-only checks.

## Section Progress

| Section | Status | Evidence / Note | Next Update |
| --- | --- | --- | --- |
| Context | done | P0-P5 dark/local complete; default-off production composition and bootstrap wiring complete | Keep source facts current |
| Plan | in_progress | 用户已授权默认关闭的 bot-only 交付；真实 activation 仍按原 runbook 等待 artifact | 构建、替换并做只读运行验收 |
| Implementation | in_progress | Profile/config、operator ACL、trusted trigger、host ingress receipt、disabled-safe assembly/LLM bridge、durable lease/recovery、offline reconciliation、strict query 和 Admin operator transport complete | 等待真实 source/restore/rollback/Worldbook evidence 才可 activation |
| Verification | in_progress | Current-snapshot audit seven gaps closed；production composition 19 passed，交叉 879 passed，Ruff/Pyright/diff clean | 做暗态 image/API/no-effect 验收 |
| Handoff | pending |  | Update when paused or complete |

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
- [~] Commit, build and deploy the user-authorized dark release. Preserve `agent_runtime.enabled=false`, do not create Runtime/Memory/Worldbook production DBs, and do not start a worker.

## Decisions

| Decision | Choice | Why | Date |
| --- | --- | --- | --- |
| Activation order | Inside-out TDD, then composition | Prevent a bootstrap path from silently deriving authority or storage | 2026-08-14 |
| Storage source | Explicit configured paths, no admin-state discovery | P5 contract requires caller-owned sources and no-create-on-missing behavior | 2026-08-14 |
| Production effects | Disabled until full readiness | `not_ready` and `not_assessed` are hard stops | 2026-08-14 |
| Release input | Isolated artifact only | Current worktree contains unrelated user WIP and untracked runtime files | 2026-08-14 |
| Attestation transport | SHA-256-pinned, profile-bound strict manifest | Keeps operator evidence explicit, repeatable and redacted without allowing test callbacks to replace configured deployment evidence | 2026-08-14 |
| A11 evidence closure | Enabled profile pin plus internal-only attestor and exact-token cancellation cleanup | Removes test callback authority, post-assembly backup drift and acquire/renew orphan leases before any external artifact decision | 2026-08-14 |

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
| `tests/test_agent_runtime_admin_operator_http.py` | Cookie/principal separation and exact HTTP resource ACL contracts | done |
| `tests/test_agent_runtime_host_ingress.py` | Receipt identity, scheduler merge and cancellation contracts | done |
| `tests/test_router_b_cluster_wiring.py` | Group/private host ingress, stale receipt and coalescer cancellation contracts | done |
| `tests/test_router_qq_interactions.py` | Notice path canonical-ID/fail-closed contract | done |

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

## Next Session Starts Here

- Direction: Commit and deploy the explicitly authorized dark release from this isolated worktree. The release must retain `agent_runtime.enabled=false`; no worker start or production source/DB creation is permitted.
- First action: Verify git input, commit the explicit Runtime v2 file set, backup current bot image and `admin/static` with SHA-256, build isolated bot/Admin assets, bot-only replace, then verify container commit/API/no lease/no DB/no external effect.
- Open questions: Exact production source locations, backup artifact owner, restore rehearsal, rollback rehearsal and Worldbook witness remain unavailable; they must never be inferred from Admin state or replaced with test manifests.
- Do not redo: P0-P5 dark/local implementation or the seven completed current-snapshot fixes. Do not turn dark deployment into real activation.
