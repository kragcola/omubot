# Bot 记忆/视觉修复授权提交与部署（2026-07-19）

> 状态：completed
> mode: task-bug
> 授权：用户明确要求“部署，提交，测试”。
> 当前下一步：无；本任务完成后立即终止，不自动承接其他 pending。
> 阻塞：无。
> 回滚：旧 bot tag `omubot-bot:rollback-9aa7e39a-20260719` → image `sha256:9aa7e39aae781dd4f27757b23784f7631386765f94dc884cafee1ccdd6427a51`；fresh trusted backup `pre-change-20260719-232315`；NapCat 禁止重建。
> 用户收尾指令：完成本任务后立即终止，不展开其他事项；保留“重启任务”作为后续 handoff。

## Objective / Acceptance

提交并部署已完成离线验收的 Bot memory/language/visual remediation 与 Style provenance 防再污染代码，同时保持当前生产已部署的 Worldbook/QZone/Memory 基线可由 commit 重建。

验收条件：

1. commit 仅包含项目源码/配置/schema/tests/docs 的可复现闭包；排除 coursework、Reasonix、NapCat、pytest/tmp、IPv6/压力实验和本地缓存。
2. staging diff 与 clean-worktree checkout 可构建、可测试；不从主 dirty worktree 构建生产镜像。
3. full pytest、scoped/static checks、Docker build 通过。
4. 仅 bot image/recreate；NapCat container/image/start/restart 均不变。
5. 运行态 startup/PluginBus/Admin health 正常；Style structured human evidence 仍为 0；preventive extractor SHA 与 commit 相同。
6. QZone 保持 dry-run/live locked，不发送 QQ/QZone；`BUILTIN_WIRE_PROFILE.validated=false`。
7. commit 成功且 HEAD 外部验证；不 push。

## Scope / Exclusions

- include: 当前容器依赖的 Omubot runtime source closure、remediation、Worldbook/QZone/Memory 已部署基线、对应 tests/docs/config/schema/admin source。
- exclude: `.codegraph/`, `.reasonix/`, `REASONIX.md`, `]+`, `docs/coursework/`, Database Coursework docs/tracker, `napcat/`, `pytest-of-kragcola/`, `tmp/`, `tools/ipv6/`, `tools/stress_test.sh`, `.workspace/` 与任何凭据/备份 payload。
- external: no push；no QQ/QZone send；no NapCat restart/recreate；no production data mutation beyond normal bot startup migrations already covered by backup/rollback.
- parallel fit: staging/commit/build/runtime share Git/image/container conflict domains and must serialize；用户已授权 Grok parallel，因此仅派发一个只读 staged-closure review packet，要求 Grok top-level + 至少一个真实 child；Codex 独占 index、commit、Docker、DB 与 tracker 写入。

## Parallel Run Ledger

- stream id: `grok-staged-closure-review-20260719`
- accepted base: `dcc75aaeb7f08d2e8b02f8cf0522bb48f204b97a` + 冻结 staged tree（308 files；排除项 0 命中；不含 runtime/untracked payload）
- global budget: Codex main + 1 Grok top-level + 至少 1 Grok child；ordinary Codex subagent = 0
- Grok ownership: read-only review，检查 staged scope/secret false positives/deployment invariants；不得编辑、提交、部署、读写生产 DB 或联系外部系统
- Codex ownership: whitespace/index、clean worktree、tests/build、commit、BackupService、image tag/build、bot-only recreate、runtime/DB/NapCat acceptance、tracker/maintenance
- isolation: Grok 在冻结 tree/commit 上只读；所有 cache、Docker、SQLite、ports、generated frontend、index 与 worktree mutations 归 Codex
- status: completed；Grok top-level `eb5d2b5b-662b-4aca-87c9-59dfb35fbe6d`，child `019f7aef-da26-7c51-bbcf-5e2c170ba7cd`；0 Critical，3 个 Important 均由 Codex 独立关闭或转为运行验收项
- completion requirement: 最终 manifest 必须包含 Grok top-level session、真实 child id/assignment/activity、检查证据、风险与碰撞结论；Codex 独立验收

## Stop / Restart Handoff

- 机器重启后已完成恢复：先盘点宿主高活跃进程、内存压力和 Docker Desktop 资源配置，再只启动 `napcat`、`ccip-sidecar`、`qq-bot` 三个必要原容器。
- 使用 `docker start` 复用原 identity/image/volume；未 build、未 Compose up、未 down、未 recreate/rebuild NapCat。`pmubot`、三个 socket proxy、watchtower 与四个测试 NapCat 全部保持停止。
- Docker Desktop `MemoryMiB=5120` 保持不变：修改该上限会重启 Docker VM 并再次冲击 NapCat；实际三容器合计约 1.08 GiB，VM 约 2.99 GiB，宿主仍 65% memory available、无 swap，已通过最小服务集控制实际占用。
- 重启任务完成；本轮不自动承接其他 pending。

## Plan

- [x] Capture current production/container/git/config baseline and commit manifest.
- [x] Stage exact source closure and inspect cached diff/secrets/exclusions.
- [x] Build clean worktree from staged tree and run required verification.
- [x] Commit and verify HEAD/tree.
- [x] Tag rollback image and build deployment image from clean commit worktree.
- [x] Bot-only force recreate; verify runtime/DB/config/NapCat invariants.
- [x] After host reboot, recover Docker with minimum necessary memory/services and re-run runtime invariants.
- [x] Update maintenance log / trackers / ACTIVE and checkpoint handoff.

## Test Ledger

| ID | Command / Evidence | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| T00 | Gate/skills/ACTIVE/trackers/status/runtime recovery | HEAD `dcc75aa`；bot image `9aa7e39a…a51`；large protected dirty WIP；Style cleanup complete | Must deploy from clean committed tree, not current working directory | 2026-07-19 |
| T01 | Frozen staged closure audit | 308 files；92,454 insertions / 1,322 deletions；diff check pass；explicit exclusions 0；credential hits均为 placeholder/test/substring | Source closure safe to commit；untracked NapCat/coursework/tmp remains excluded | 2026-07-19 |
| T02 | Clean-tree frontend/static/plugin/full tests | Node 11/11；vue-tsc pass；Vite build pass；strict plugin layout pass；pytest 5093 passed / 17 skipped / 189 warnings | Clean source closure behavior verified | 2026-07-19 |
| T03 | Static checks | changed Python Ruff 195 files pass；runtime Pyright 105 files 0 errors；tests-as-explicit-Pyright-input produced 111 fixture typing errors and was rejected as wrong scope；two staged Ruff issues fixed then 53 targeted tests pass | Runtime static boundary clean；failed command retained, no mass test ignores | 2026-07-19 |
| T04 | Grok required parallel review | top-level `eb5d2b5b…` + child `019f7aef…`；0 Critical；Grok changed 0 files | Parallel contract met；SPA/build-context、historical tracked storage、host QZone config由 Codex复核 | 2026-07-19 |
| T05 | Source commit | `40a8e32faede3cf1a8b9152967c0dd98ecbb6b55`；tree `78b119cded3502db72e89e7c5e5e7c907b616b5f` 等于 frozen tree；未 push | Reproducible source commit established | 2026-07-19 |
| T06 | Backup / rollback / clean Docker build | backup `pre-change-20260719-232315`，26 ok / 0 failed / trusted；rollback tag points to `9aa7e39a…`；new image `d89121d98ef0…`，GIT_COMMIT=`40a8e32…`；6 key source SHA match；image SPA refs 4/4、assets 101 | Deployment and rollback artifacts trusted | 2026-07-19 |
| T07 | Bot-only recreate | `docker compose up -d --no-deps --force-recreate --no-build bot`；bot `e95c0b9b…` / image `d89121d9…` / restart 0；NapCat ID/image/created/started/restart/status byte-for-byte unchanged | Only bot replaced；NapCat red line preserved | 2026-07-19 |
| T08 | Runtime/API/UI acceptance | startup complete、PluginBus 24、OneBot connected；Admin health 200；services 11 ok / 2 warning / 0 error；context/memo/worldbook/qzone enabled且 plugin errors=0；Worldbook snapshot available；browser dashboard settled to Bot online/NapCat normal，QZone/Worldbook pages render | Primary deployment acceptance passed before interruption | 2026-07-19 |
| T09 | Style/QZone read-only invariants | style quick_check=ok、structured human remaining=0、cleanup revisions=73、approved/rejected=10/82；3 new pending all post-cleanup normal human extractor rows，structured=0；QZone dry_run=true/live=false/validated=false/allowlist empty/gate ready=false；no send | Preventive provenance code active；QZone remains test/dry-run only | 2026-07-19 |
| T10 | Post-reboot host/Docker inventory | 16 GiB host；74% available before service start、no swap；Docker `MemoryMiB=5120`；image `d89121d9`、volume `omubot-storage` 与三个原容器均存在；0 running / 13 stopped | No rebuild required；avoid Docker VM restart and preserve identities | 2026-07-20 |
| T11 | Minimal existing-container recovery | sequential `docker start napcat` → `ccip-sidecar` → `qq-bot`；IDs/images/created/restart=0 unchanged；OneBot login retcode=0；CCIP 4 packs / 136 characters | Necessary stack restored without recreate；auxiliary/test containers remain stopped | 2026-07-20 |
| T12 | Post-reboot runtime/memory acceptance | Bot health 200、connected_bots=1、services 11 ok / 2 warning / 0 error、PluginBus 24；Worldbook available；Style quick_check=ok/structured=0/revisions=73；QZone dry-run/live/validated/allowlist 四重锁；container memory约 600.7/321.6/160.5 MiB，host 65% available/no swap | Recovery complete；actual Docker memory limited by minimal running set；no external send | 2026-07-20 |

## Interruption Checkpoint

- Deployed commit: `40a8e32faede3cf1a8b9152967c0dd98ecbb6b55`.
- Active image before host reboot: `sha256:d89121d98ef0baaa94827ebf1465c6095b4c48b815bb819f468fb6fd40a93e30`.
- Active bot container before host reboot: `e95c0b9b20e96e6f9c008e97036e6c23ebc427c74d1ac1b1d78833afe9a6b2b9`, restart 0.
- NapCat before host reboot: container `19f6cf13607c…`，image `cde89d76…`，restart 0；严禁 recreate。
- Post-reboot recovery completed 2026-07-20；ACTIVE 可置 none。不要重跑已经通过的全量测试，除非 commit/image发生变化。

## Final Outcome

- Source commit `40a8e32` 与 deployed image `d89121d9` 在机器重启后继续复用；未重新 build。
- Necessary running set precisely equals `napcat`、`ccip-sidecar`、`qq-bot`；所有辅助/测试容器停止。
- NapCat、CCIP、Bot container identity 与 restart count 均保持；OneBot、Admin、Worldbook、Style、QZone 安全锁运行正常。
- No push，no QQ/QZone send，no NapCat restart/recreate，no production cleanup/write beyond normal service operation。
