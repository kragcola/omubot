# Active Omubot Work

> This is the compaction/new-session recovery entrypoint. Keep it short.

## Current

- mode: none
- tracker: none
- objective: none
- status: Style 历史视觉/system provenance 污染清理与防再污染代码提交、bot-only 部署、测试及生产只读复验均已闭环。
- next_step: none
- last_completed: 防再污染代码随 `40a8e32` / image `sha256:d89121d98ef0...` 在 `qq-bot` 运行；Style 定向测试 49 passed，生产 `quick_check=ok`、structured human evidence=0，当前 approved/pending/rejected=`10/1114/82`。Grok normal required-parallel 只读交叉验证完成；未发送 QQ/QZone，NapCat 未重启或重建。
- rollback: 代码回退到部署前 bot image；历史数据恢复使用 trusted backup `pre-change-20260719-224533` 或 SHA-256 `53355a9f...2b88` 的 0600 行级计划。任何整库恢复必须另行授权并 stop/start bot；NapCat 禁止重建。

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
