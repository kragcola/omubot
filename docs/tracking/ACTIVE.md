# Active Omubot Work

> This is the compaction/new-session recovery entrypoint. Keep it short.

## Current

- mode: task
- tracker: `docs/tracking/agent-runtime-v2-production-activation-2026-08-14.md`
- objective: 按 Agent Runtime v2 production activation runbook 依次完成显式 source、operator/ACL、trusted trigger、组合根、attestation 与验证；全程保持外部效果 fail-closed。
- status: B2 长间隔续话与 A16 provisioner 修复已于 2026-08-16 22:48 CST 上线：`c0a7309` / image `aed72adb25a0…`。`qq-bot` restart=0、OOM=false、Admin=200、未认证 Runtime API=401；NapCat 的 ID/image/created/started/restart 未变。当前 production JSON 已安装 enabled ingress-only profile：五个 source、冻结 backup、restore 副本、profile-bound manifest 和具名 operator ACL 均存在；启动后 source preflight 全 `ready`，但 17 个 activation 与 4 个 rollback gate 均为 `not_assessed`，worker lease/trusted invocation 均为 0，dispatcher 保持 fail-closed。
- next_step: 依次收集并独立核验真实 operator action、自然 OneBot canary 与 registry/LLM witness、provider cooperative-cancellation/serial-throughput、source-bound Worldbook authority，以及 bot-only image/config rollback rehearsal。只在每项都有可审计证据、完整 manifest 更新并经新独立审查后，才启动唯一 worker；不得伪造 gate 或发送 QQ/QZone 测试消息。
- last_completed: Runtime v2 的 profile/source、operator ACL、trusted invocation、受限组合根、lease/recovery、bootstrap 生命周期、worker hard gate、Admin operator transport、offline reconciliation、strict GET query、provider 前 owner/token execution fence、同 token extension、guard terminalization 和 cancel-safe worker stop 已进入生产镜像。2026-08-16 独立 artifact audit 确认主机 named volume 内的五源 schema `2/1/1/1/2`、`quick_check`、backup digest、restore 副本、0600 operator credential、exact ACL、legacy Worldbook read-only witness 均有效；本地 Admin login=200，带 operator credential 的不存在 context=404，验证身份/ACL 而不写入业务状态。B2 bootstrap renewal regression 在当前 `c0a7309` 重复 30/30 通过。
- rollback: 代码回滚使用 `omubot-bot:pre-b2-long-gap-20260816`（`60f179753e94…`）重标为 `omubot-bot:latest`，再仅执行 `docker compose up -d --no-deps --force-recreate --no-build bot`；未来 worker 回滚先将 `agent_runtime.enabled=false`，保留 `unknown`/`dispatching`。不建/删 production DB、不重建 NapCat；`BUILTIN_WIRE_PROFILE.validated=false`。

## Recovery Order

1. Read `.workspace/agent-session-state.md` if present.
2. Read this file.
3. If `tracker` is not `none`, read the tracker above.
4. Run `git status --short`.
5. Continue from `next_step`.

## Notes

- 全局 OneBot 群出站守卫已部署：最终 `GroupConfig.allows_active_group()` fail-closed。
- Worktree retains character-pack notes、deep-delivery drafts、coursework/tool outputs、NapCat data and temp artifacts; never use `git add -A`.
- Pytest baseline: `source ./scripts/dev/env.sh` then `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-}`.
- 本任务迁移/回滚：`docs/migrations/bot-memory-language-visual-remediation-2026-07-19.md`。
- 当前 Agent Runtime v2 迁移/回滚（临时 DB schema v2）：`docs/migrations/agent-runtime-v2-2026-07-21.md`。
- **禁止**将 `BUILTIN_WIRE_PROFILE.validated` 设为 `true`。

## Pending (authoritative; not the Current task)

- **Character pack gap filling（pending）**：sidecar healthy，4 packs / 136 characters；Lily expression 已清，剩 BangDream 10 个 `chibi` 与 `haru:chibi`。
- **QZone Journal v0.8.2（真实性/schema v6 已部署，常驻 live 锁定）**：Schedule/Dream fiction 分类、确定性 fiction frame、factual exact compose、delivery live authenticity、显式 approval scope、memory-loop 断开已上线；历史 published 行为 `dry_run`，当前仅 1 条 canonical fiction pending/dry-run tip，approved/dispatching/unknown=0。built-in `validated=false`、dry-run=true、live=false、allowlist 空。既有远端日志不处置；未来需新授权 + 新 draft + 明确 live approval + 新原始 attested capture。详见 `docs/tracking/qzone-authenticity-remediation-2026-07-18.md`。
- **Memory graph window / hub control v1（结构切片已部署）**：scope 公平窗口、batch evidence、hub-aware max-2-hop 已随 Stage-0 image 生效；PPR 仍 NO-GO。迁移：`docs/migrations/memory-graph-window-hub-control-v1-2026-07-16.md`。
- **Memory Episode typed refs / alias（结构切片已部署）**：真实 resolver、scoped promoter、provider typed evidence、alias lifecycle、nickname seed 已上线；无 big-bang backfill。
- **Memory hot-path write policy v1（代码已部署，flag off）**：JSONL 决策 + observations + 原子 supersede 已在 image；`memo.write_policy_enabled=false`。
- **Memory Temporal Trace v1（代码已部署，flag off）**：current/earlier/trajectory sidecar + false-premise awareness 已在 image；`context.temporal_trace.enabled=false`。
- **Memory evidence-use eval gate v1（严格代码验收，未部署）**：离线 ContextEval 扩展 + synthetic fixture；20 条 adversarial regression；生产 ranking 未改。
- **Memory Episode v2 decay/rerank（结构切片已部署）**：严格 `decay_at`、读时 eligibility（julianday offset-safe）、Admin decay、有界 ngram 重排已随 Stage-0 image 生效。
- **Memory Query-Aware Retrieval Planner v1（代码已部署，flag off）**：闭集 need、post-RRF type caps、content-capacity pack budget、identity/disabled 精确回退、secret-free metrics；`context.query_aware_plan.enabled=false`。迁移 `docs/migrations/memory-query-aware-retrieval-planner-v1-2026-07-17.md`。
- **Memory Card Category Time Eligibility v1（代码已部署，flag off）**：读时 fail-closed status/event TTL 与 starvation hardening 已在 image；`context.card_eligibility.enabled=false`。
- **Memory Pack-Time Confidence/Evidence Gate v1（代码已部署，flag off）**：evidence-aware tiering 已在 image；`context.pack_evidence_gate.enabled=false`。
- **Memory Evidence-Use / Pack-State Contract v1（代码已部署，flag off）**：闭集 `pack_state` 观测与软约束已在 image；`context.evidence_use_contract.enabled=false` 且 instruction=false。
- **Memory Graph Provenance Gate v1（已部署启用）**：写侧 pure normalizer + 读侧 `graph_fact:*` quarantine 已上线；`knowledge_graph.provenance_gate_enabled=true`；无 bulk repair。
- **Memory Graph Population & Evidence-Quality Observability v1（代码已部署，flag off）**：Admin additive nested observability 已在 image；`knowledge_graph.observability_enabled=false`。
- **Memory Joint Dual-Path Telemetry v1（代码已部署，flag off）**：联合 outcomes 代码已在 image；`block_trace.joint_dual_path_telemetry_enabled=false`。
- **LongMemEval Raw-Turn Replay v1（离线终验）**：官方 pin 数据形状手动 replay；strict schema + opaque report IDs + duplicate-rank fail-closed；session 指标不冒充 upstream 扩窗；post-fix **0C/0I/3M ACCEPT**；不接 regular CI/leaderboard parity。
- **Memory staged rollout / rollback v1（Stage 0 已部署）**：closed cumulative profile + runbook + Stage-0 multi-flag identity；gpg=true，其余可翻转新行为保持暗态；无 flag 结构切片已进入 production image，异常仅允许回滚旧 bot image；尚未推进 Stage 1+。
- **Learning Autopilot applied-outcome v1（结构切片已部署）**：Style/Episode/Slang 与 KG 统一 applied counters 已随 Stage-0 image 上线。
- **关闭中的两项 LLM 行为开关**：`schedule_overshare.enabled=false` 与 `addressee_hint.enabled=false`。

## Historical Pending Snapshot (superseded; do not use for status)

（以下历史条目保留供检索，状态以 Current / Pending 为准，不再逐条刷新。）

- 进阶话题块 Phase 2（未启动）。
- Character pack / Living Persona / Climate M3-M4 / sticker mood / 话题块缺陷审计 等见既有 tracker 与 maintenance-log。
