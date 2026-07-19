# Bot 记忆/视觉修复授权提交与部署（2026-07-19）

> 状态：preflight
> mode: task-bug
> 授权：用户明确要求“部署，提交，测试”。
> 当前下一步：冻结可复现生产源码闭包与排除清单；在干净 worktree 验证 staged tree 后提交，再由该 commit 构建并 bot-only recreate。
> 阻塞：无。
> 回滚：当前 bot image `sha256:9aa7e39aae781dd4f27757b23784f7631386765f94dc884cafee1ccdd6427a51`；Style trusted backup `pre-change-20260719-224533`；NapCat 禁止重建。
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
- status: preparing
- completion requirement: 最终 manifest 必须包含 Grok top-level session、真实 child id/assignment/activity、检查证据、风险与碰撞结论；Codex 独立验收

## Stop / Restart Handoff

- 本轮完成部署、提交、测试与证据落盘后立即停止。
- 不自动承接其他 pending；存在“重启任务”，留待后续任务按当时 ACTIVE/tracker 恢复。

## Plan

- [~] Capture current production/container/git/config baseline and commit manifest.
- [ ] Stage exact source closure and inspect cached diff/secrets/exclusions.
- [ ] Build clean worktree from staged tree and run required verification.
- [ ] Commit and verify HEAD/tree.
- [ ] Tag rollback image and build deployment image from clean commit worktree.
- [ ] Bot-only force recreate; verify runtime/DB/config/NapCat invariants.
- [ ] Update maintenance log / trackers / ACTIVE and final handoff.

## Test Ledger

| ID | Command / Evidence | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| T00 | Gate/skills/ACTIVE/trackers/status/runtime recovery | HEAD `dcc75aa`；bot image `9aa7e39a…a51`；large protected dirty WIP；Style cleanup complete | Must deploy from clean committed tree, not current working directory | 2026-07-19 |
