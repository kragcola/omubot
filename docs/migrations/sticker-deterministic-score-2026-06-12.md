# 迁移清单：表情发图 Bernoulli 概率门 → 确定性评分（2026-06-12）

D3 重构迁移清单。背景与设计见 [tracking/sticker-deterministic-score-2026-06-12.md](../tracking/sticker-deterministic-score-2026-06-12.md)。

## 旧 → 新（四列）

| 维度 | 旧（58d7b07 Bernoulli） | 新（确定性评分） | 文件 |
|---|---|---|---|
| 决策核 | `rng() < probability`（掷骰子） | `score >= threshold`（确定，§3.5b 窄带内保留 rng） | `services/sticker/decision_provider.py` |
| 打分 | `_send_probability()` 各因子相乘 | `compute_sticker_score()` logit 线性加权 + sigmoid（仿 RWS `compute_rws`） | `services/sticker/decision_provider.py` |
| reason | `sampled_send` / `sampled_skip` | `score_send` / `score_skip`（`thinker_veto`/`cooldown_active`/`affection_withdraw_gate`/`no_candidates` 不变） | decision_provider.py + client.py 日志 |
| 删除常量 | `_BASE_FREQUENCY_MULT`、`_mood_energy_multiplier`、`_MIN/_MAX_SEND_PROBABILITY` | `_W_*` 权重组、`_BASE_FREQUENCY_LOGIT`、`_DEFAULT_SCORE_THRESHOLD`、`_SCORE_SOFT_BAND`、`_sigmoid`、`_band_send_fraction` | decision_provider.py |
| `decide()` 签名 | `decide(..., rng=None)` | `decide(..., threshold=0.5, rng=None)` | decision_provider.py |
| 选图无匹配 | `search_by_intent()` → fallback `candidate_pool[0]`（任意图） | `search_by_intent_scored()` + `intent_floor`：无匹配过下限 → `None`（纯文字降级） | sticker_store.py + client.py `_select_post_reply_sticker` |
| closing/greeting 配图 | short-circuit `return None`，结构性零配图 | 发完 token 后调 `_maybe_light_reply_sticker` → 同评分门 | client.py `_handle_light_reply` |
| 配置 | 无（写死常量） | `StickerPlacementConfig.score_threshold` + `intent_relevance_floor`，admin 配置页自动渲染 | kernel/config.py |

## 行为变化（蒙特卡洛，threshold=0.5, energy=0.46 线上值）

| 场景 | 旧（Bernoulli） | 新（确定性） |
|---|---|---|
| thinker 要图 acquaint | ~51% | ~100% |
| thinker 要图 stranger | ~36% | ~100% |
| thinker 缺席（force/@） | ~29% | ~84%（窄带 jitter） |
| thinker 不要（veto） | 0% | 0% |
| 难过 valence-0.9 | — | 100%（难过≠不发） |

普通回复端到端带图率 ≈ thinker true 率（~55%），旧版 ~28%。可预测："thinker 说了算"。

## 回归测试

- `tests/test_sticker_decision_provider.py`：Bernoulli 测试全重写为确定性断言 + 窄带 jitter + 阈值可配 + 蒙特卡洛分布。
- `tests/test_sticker_placement.py`：新增无匹配→None 降级、force_send 绕过 floor。
- `tests/test_closing_light_reply_client.py`：新增 closing/greeting 调 sticker hook、sticker 失败不破坏 light reply。
- 全量 `uv run pytest` 2653 passed / 17 skipped / 0 failed；ruff + pyright 改动文件全绿。

## 回滚

- 一键软退：admin 配置页 `score_threshold` 调高（如 0.95）→ 几乎不发图；`intent_relevance_floor=0` → 关闭纯文字降级。
- 代码回滚：`git revert` 本次 commit。
