# Memory Evidence-Use / Pack-State Contract v1 Migration

> 状态：离线验收通过；未部署、未 commit、未触 live DB / Docker / NapCat / QZone。日期：2026-07-17。

验收证据：focused context **181 passed**；全仓 **4646 passed / 17 skipped / 186 warnings**；Ruff clean；Pyright **0 errors**；JSON / `git diff --check` clean；独立 Grok review **0 Critical / 0 Important，ACCEPT offline**。

## 目标

在现有 `peg_v1`（pack-time evidence gate）结果之上，增加 **可观测 + 软模型约束** 的闭集 pack-state 合同（`euc_v1`）。
**不是**「回答是否使用了证据」的证明；**不**声称 LongMemEval / LoCoMo 等 grounding / benchmark 对等。

## 旧行为

- `build_prompt_context()` 在 gate+pack 后仅记录 `pack_evidence_gate` 闭集 metrics。
- 空包 / 仅 hint 时主 LLM 无额外「资料约束」提示；与检索 miss / `retrieve_mode=skip` 在观测上不易区分。
- 无 `pack_state` 聚合计数。

## 新行为

- 纯模块 `services/context/evidence_use_contract.py`：从 **最终 pack hits** + peg metrics + `retrieve_mode` **确定性**推导 `pack_state` / `action` / `inject_instruction`。
- 闭集 `pack_state`：`empty` | `hint_only` | `nonempty` | `demote_present` | `omit_only` | `skip`。
  - `skip`（`retrieve_mode=skip`）与检索 miss（`empty`）可区分。
  - **最终 pack 优先**：`demote_present` 仅当 pack 中仍有 ≥1 个 demoted non-hint survivor 且 gate `keep=0`；demote 后被 budget 丢光 → `empty`（不是 demote_present）。
  - `omit_only`：最终 pack 为空且 gate 为纯 omit（keep=0/demote=0/omit>0）的诊断态。
- 闭集 `action`：`none` | `constrained_instruction` | `identity`。
- `empty` / `hint_only` / `omit_only` 且启用注入、mode≠skip 时：`ContextPlugin` 以 **低优先级 dynamic** 块注入短中文软约束（不强制 `pass_turn` / 静默 / 硬拒答）。`omit_only` 虽为诊断态，最终无可用证据，故与 empty 同软约束。
- 有幸存 hit 的 `demote_present` / `nonempty` / `skip` 默认 **不** 注入指令。
- Sanitizer 不变量：`action=identity` 仅当 disabled 或 `identity=true`；`constrained_instruction` 仅当 `inject_instruction=true` 且 state∈{empty,hint_only,omit_only}；否则 `action=none`；畸形组合 fail-closed。
- `ContextPack.evidence_use_contract`：内部 additive 字段；**不**进入 `to_dict()`。
- recent 行挂 `evidence_use_contract`（sanitize 后闭集）；`metrics()` 增加聚合 `state_counts` / `action_counts` / rates。
- **Kill-switch**：`evidence_use_contract.enabled=false` → `identity`，不注入指令；行为与启用前主路径兼容。
- 离线 eval：**默认**仍 `search()` + `pack_context_hits()`；opt-in `score_evidence_use_contract=True` 仅记录 `pack_state`，不写 `answer_used_evidence` / grounding 声明。

## 配置

```json
{
  "evidence_use_contract": {
    "enabled": true,
    "inject_constrained_instruction": true
  }
}
```

- 插件版本 `0.1.14`；`restart_required` 字段含 `evidence_use_contract`。
- 回滚：设 `enabled=false` 后 restart bot；无 DB 迁移。

## 文件清单

| 路径 | 说明 |
|------|------|
| `services/context/evidence_use_contract.py` | 纯 derive / sanitize / aggregate |
| `services/context/types.py` | `ContextPack.evidence_use_contract` |
| `services/context/service.py` | build + recent + metrics 接线 |
| `services/context/eval.py` | opt-in `score_evidence_use_contract` |
| `plugins/context/plugin.py` | 配置 + 条件注入 |
| `plugins/context/config.default.json` / `config.schema.json` / `plugin.json` | 0.1.14 |
| `tests/test_context_evidence_use_contract.py` | RED→GREEN 合同测试 |

## NO-GO（本切片）

- 不改 RRF / type caps / Card schema / TemporalTrace 鉴权 / EpisodeProvider / tool loop / QZone / NapCat / live DB / 公开 wire payload。
- 不强制 `pass_turn`、不抑制回复、不声明 hard abstention。
- 不输出 `answer_used_evidence=true` 或 grounding/benchmark parity。
- metrics 禁止 raw query / pack 正文 / ids / titles / card·message id / answer 正文。

## Same-pattern 扫描（D1）

见实现 handoff：`pack_context_hits` / `build_prompt_context` 直接调用方已扫；合同仅挂在 `ContextService.build_prompt_context` + `ContextPlugin.on_pre_prompt`；eval 默认路径未改。
