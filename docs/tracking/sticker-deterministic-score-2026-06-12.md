# 表情发图决策：去掉纯概率，复用 RWS 打分范式（方案，待审）

> 状态：**已实施 + 已部署**（2026-06-12）。用户拍板：§3.5b 窄带软化 + 草案权重 + closing/greeting 一起修 + 无匹配降级纯文字 + threshold 进配置页。
> 日期：2026-06-12
> 关联：[58d7b07](去掉每段必发) 引入的 Bernoulli 概率门；[rws-activation-2026-05-31](../migrations/rws-activation-2026-05-31.md)；迁移清单 [migrations/sticker-deterministic-score-2026-06-12.md](../migrations/sticker-deterministic-score-2026-06-12.md)

## 1. 背景与动机

### 1.1 用户质疑

> "所有概率都是初版不健康产物，在 RWS 实装时删过一次，发图为什么还保留？"

调研结论（git 史钉死）：

- scheduler 的**开火概率**（`prob fire threshold=0.30`）确实在 RWS 激活（`b960a09`, 5-31）时被 reward-weighted 评分取代。
- 但 sticker 的概率门在 `services/sticker/decision_provider.py`，是**独立的另一套**，RWS 那次没碰它。
- 现在的 Bernoulli 概率门（`rng() < prob`）不是初版残留——是 **6-10 `58d7b07`「杀掉每段必发」时新引入的**，比 RWS 还晚 10 天。

### 1.2 为什么概率门压太狠（端到端漏斗）

普通群回复带图要连过两道串联门，概率相乘：

```
thinker 判 sticker=true (~55%)  ×  Bernoulli 概率门 (0.511)  ≈  28% 带图
```

概率门 0.511 的来历（base 0.7 封顶，energy 再打折）：

```
base 0.7 (frequent + thinker在场) × freq 1.0 (normal) × energy_mult 0.73 (energy=0.46) = 0.511
```

即使 thinker 100% 要图、心情拉满，概率门天花板也只有 0.70。配合 thinker 的 ~55%，端到端理论上限 ~38.5%。用户观察到"一张都没发"是 28% 在小样本下的涨落。

### 1.3 已排除的假嫌疑

- ✅ sticker 库 300 张 **100% 有 usage_hint** → frequent pool 恒满，不空
- ✅ fairmatch 只重排不砍空
- ✅ cooldown_active 写死 False，不衰减
- ✅ already_sent 仅回合内防重，不跨回合
- ✅ 主路径 sticker 调用点（client.py:5439）覆盖大多数回复

唯一结构性零配图缺口：closing/greeting short-circuit（见 §6，本方案外的独立 bug）。

## 2. 设计哲学：复用 RWS 范式，不复用 RWS 实例

### 2.1 RWS 不能直接复用（语义不匹配）

| | RWS（开火） | sticker（配图） |
|---|---|---|
| 问题 | 要不要开口 | 这句要不要配图 |
| 特征 | mode/addressee/eot/hawkes/familiarity | 情绪二轴/affection/thinker/刚发过 |
| 反馈信号 | 群 ack/cold/制止（`_measure_reaction` 读 timeline 后续 turn） | **无等价信号**——配不配图群几乎不 ack/cold |

硬阻断点：RWS 的学习闭环（bandit + reward queue）靠"开火后群有没有反应"。**配图动作没有可观测群反应**，reward 信号为空，bandit 学不到东西。bandit 调的是单一全局 theta，也套不上 per-reply 的情绪判断。

### 2.2 复用 RWS 的**范式**（logit 线性打分 + sigmoid + 阈值）

RWS 核心范式（`rws.py:130-143`）是**确定性、可解释、无随机数**：

```
logit = Σ(weight_i × feature_i)
score = sigmoid(logit)
decision = score >= theta        # 同输入同输出
```

这正是替代"掷骰子"的范式。把 sticker 的情绪/affection/话题信号从"相乘成概率再 rng 掷骰"改成"加权成 logit 过阈值"——可预测、可解释、权重可调。复用 `_sigmoid` / logit 结构，但**不接 bandit/reward 闭环**（无反馈信号）。这是与 RWS 的唯一结构差异。

## 3. 方案：`compute_sticker_score` 确定性评分器

### 3.1 决策流程（替换 `decide()` 第 96-118 行）

保留前置硬门不变：`cooldown_active` / `no_candidates` / `affection_withdraw_gate`。

```
1. thinker veto 硬门（不变）：
   thinker_ran && !thinker_suggested && source ∈ {frequent,thinker}  → 不发 (reason=thinker_veto)

2. 评分（替换 Bernoulli）：
   logit = Σ weighted features
   score = sigmoid(logit)
   should_send = score >= threshold        # 确定性，无 rng

3. tool_call / kaomoji 仍走高基线（见 §3.3）
```

### 3.2 特征与权重（草案，待调）

所有特征映射到 residual ∈ [-1,1] 或 [0,1]，仿 `compute_rws`：

| 特征 | 来源 | 权重(草案) | 说明 |
|---|---|---|---|
| `bias` | 常量 | 0.0 | 基线倾向，调总体松紧用 |
| `thinker_wants` | thinker_ran && suggested | **+2.5** | thinker 明确要图 → 强正，主导项 |
| `thinker_absent` | !thinker_ran (force_reply/@) | +0.3 | thinker 没意见 → 弱正基线 |
| `source_tool_call` | source==tool_call | +3.0 | LLM 显式调 send_sticker → 几乎必发 |
| `source_kaomoji` | source==kaomoji | +1.5 | 颜文字转图意图 |
| `valence_playful` | valence>=0.4 && energy>=0.7 | +0.6 | 开心活泼 → 更想配图 |
| `valence_negative` | valence<=-0.4 | +0.2 | 难过≠不发（共情图），轻微正 |
| `energy` | (energy-0.5)*2 | +0.4 | 高能量轻微加，低能量轻微减（不归零） |
| `affection_close` | stage==close | +0.5 | 亲密 → 更随性配图 |
| `affection_stranger` | stage==stranger | -0.7 | 陌生 → 收敛 |
| `base_freq` | rarely/normal/frequently | logit 偏移 -0.7/0/+0.7 | web 可配基线 |

`threshold` 默认 0.5（= sigmoid(0)）。可做成 config 字段 `sticker_score_threshold`，调总体发图率的单一旋钮。

### 3.3 tool_call / kaomoji 不可 veto（不变）

显式 send_sticker tool_call 与 kaomoji-enforce 是 LLM 自己的动作/强制路径，不经 thinker veto、走高 logit 几乎必过阈值（与现 base 0.85/0.65 等价语义）。

### 3.4 行为对比（端到端带图率，估算）

| 场景 | 现状(概率门) | 方案(确定性评分) |
|---|---|---|
| thinker 要图 + acquaint + energy0.46 | ~51% × → 实际 ~51% | score=sigmoid(2.5+0+...) ≈ **~0.92 → 发** |
| thinker 要图 + stranger | ~36% | sigmoid(2.5-0.7) ≈ 0.86 → 发 |
| thinker 不要 (veto) | 0% | 0%（硬门不变） |
| thinker 缺席 (@/force) | ~29% | sigmoid(0.3) ≈ 0.57 → 发（边界） |
| 严肃解释 (thinker false) | 0% | 0% |

端到端从 ~28% 拉到 **≈ thinker true 率 (~55%)** 且**可预测**：thinker 要图就发（除非刚发过/withdraw/陌生压到阈值下）。符合"thinker 说了算、不要散落魔法概率"。

### 3.5 拟人波动怎么办（6-10 引入概率的初衷）

6-10 加 Bernoulli 是为了打破"同语境必发"的机械感。确定性评分会丢这个波动。两个权衡选项（待用户定）：

- **3.5-a 纯确定性**：彻底去随机。thinker 要图就发。最可预测，但同语境恒定。
- **3.5-b 评分 + 边界软化**：score 在阈值附近 [θ-0.1, θ+0.1] 的窄带内保留小幅 rng（只在"拿不准"区抖动），其余区间确定。保留一点人味，又不让骰子主导。**推荐**——既去掉"纯粹概率"主导，又不显得机械。

## 4. 改动面（D3 迁移清单预览）

| 旧 | 新 | 文件 |
|---|---|---|
| `_send_probability()` 相乘链 | `compute_sticker_score()` logit 加权 | decision_provider.py |
| `rng() < probability` Bernoulli | `score >= threshold` 确定性(+§3.5b 可选窄带) | decision_provider.py |
| 常量 `_MIN/_MAX_SEND_PROBABILITY` | 删除或转为 score clamp | decision_provider.py |
| reason `sampled_send/skip` | `score_send/score_skip`（veto 不变） | decision_provider.py + client.py 日志 |
| `send_probability` 字段 | 保留（=score，观测用） | StickerDecision |
| 新增 config `sticker_score_threshold` | — | kernel/config.py |

调用方 `client.py:1741` `decide(rng=...)` 签名：§3.5a 删 rng 参数；§3.5b 保留供窄带 + 测试注入。

## 5. 测试计划（D2/D4）

- 现有 `test_sticker_decision_provider.py` 的 Bernoulli 测试（`sampled_send/skip`、`rng=lambda`）需重写为确定性断言。
- 新增：thinker 要图 → 必发；thinker false → veto；陌生人压到阈值下 → 不发；tool_call 不可 veto；energy 单调；valence 负仍可发（难过≠不发）。
- §3.5b 若采纳：窄带内 rng 注入测试（θ±0.1 抖动，区间外确定）。
- 蒙特卡洛验证端到端分布（如现 commit 做法）。

## 6. 范围外（独立缺口，本方案不含）

`_handle_light_reply` 的 **closing/greeting 分支 short-circuit（client.py:4730-4732 `return None`），跳过所有 sticker 调用点**，结构性零配图。与"弱回复必须带温度"设计冲突。这是另一个 bug，需单独修（在 closing/greeting 分支补 sticker 决策调用）。本方案聚焦概率门重构，不动 light_reply。

## 7. 待用户拍板

1. **§3.5 波动**：a 纯确定性 / **b 阈值窄带软化（推荐）**？
2. **§3.2 权重**：按草案先实施再调，还是先定目标带图率（如 thinker 要图时希望 ~70%/85%/必发）反推权重？
3. **§6 closing/greeting 缺口**：本次一起修，还是单独排期？
4. **threshold 配置化**：是否要 `sticker_score_threshold` 进 admin 可调？
