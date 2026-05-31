# Omubot 拟人 Part 3.5 v3 — 回复频率自适应调度审计与超越式重写

> 状态：2026-05-26 初版（已废）→ v2 重写（已废）→ v3 重写（本文）。
> 触发：v2 把 MaiBot 当作设计参考，但 MaiBot 是用户已弃用的同代框架——
> 学它只能让 Omubot **追平**而非**超越**。本版把 MaiBot 降为「同代基线」，
> 重新调研更前沿、更跨学科的可借鉴范式（点过程建模、预测性 turn-taking、
> 信息增益、上下文 bandit、校准化弃权），并给出 Omubot **可以超越同类框架**
> 的设计与三波交付路线图。

---

## 0. v3 对 v2 的差异（一表对照）

| 维度 | v2（已废） | v3（本文） |
|---|---|---|
| MaiBot 角色 | **设计参考** | **同代基线 / 待超越的下限** |
| 设计立足点 | 「Omubot 现有设计是行业主流，只缺 directed_followup bypass」 | 「Omubot 现有设计 = MaiBot 同级 = 开环静态阈值控制器；行业前沿已经走到闭环自适应」 |
| 修订幅度 | 加 1 个 bypass 分支（P3.11） | 三波路线图：P3.11 临时止血 → P3.12-14 引入 RWS 中间层 → P3.15+ 闭环在线学习 |
| 文献支撑 | MaiBot / NoneBot / SillyTavern + MUCA + Turn-Taking | 加入 **Ancestor Hawkes (2026 group chat 自激点过程)**、**MM-When2Speak (2026 多模态 dense 时序分类)**、**IG LLM question-asking (2026 信息增益)**、**TurnGPT-in-HRI (2025)**、**AURA ε-greedy 对话 RL (2025)**、**Art of Refusal abstention 综述 (2024)**、**ENIGMA OPE 对话评测 (EMNLP 2021)**、**Multi-Party Hangover 多方 addressee (2024)** |
| Omubot 原创合成 | 无 | **Reply Worthiness Score (RWS)**：把 11 个调度位点折成一个由可观测量组成的标量；**Hawkes 反向调制**：群聊越热，bot 越退让；**反事实静默重放**：用日志评估「不该回的回了」与「该回的没回」 |

---

## 1. 重新定性：当前频率控制系统是什么

### 1.1 一句话定性

Omubot 当前 [services/scheduler.py:130-245](../../services/scheduler.py#L130-L245) 的 11 个频率控制位点，
是一个**开环静态阈值控制器（open-loop static threshold controller）**——
所有阈值（`talk_value=0.3` / `planner_smooth=3.0` / `consecutive_skip≥5`）由人工配置，
所有乘数（`mood_mult` / `time_mult`）由确定性映射查表得到，
最末端用 `random()` 把"频率"实现为"独立伯努利抽样"。

**没有任何位点会从『回复后果』中学习**。这是 Tier 0 设计。

### 1.2 与同代框架（含 MaiBot）的同构

| 框架 | 全局频率 | 时段 | 群级 override | bypass | 抗饥饿 | 学习闭环 |
|---|---|---|---|---|---|---|
| MaiBot 麦麦 | `talk_value` | `talk_value_rules.time` | `talk_value_rules.item_id` | `inevitable_at_reply` | `planner_interrupt_max_consecutive_count` | ❌ |
| NoneBot diss-anybody | `diss_global_chance` | — | — | — | — | ❌ |
| SillyTavern Group Chats | `Talkativeness` | — | — | mention 优先 | — | ❌ |
| **Omubot 现状** | **`talk_value`** | **`talk_schedule.json`** | **`GroupOverride`** | **`is_at` / `video_always`** | **`consecutive_skip≥5`** | **❌** |

### 1.3 评估「同代」的两条准绳

判定 Omubot 与 MaiBot 同代，不是因为代码结构相像，而是基于两条**外部可观察**的判据：

1. **决策函数没有反馈环**：`should_reply(ctx) → bool` 的输入只来自 `ctx`（消息、配置、最近 K 条历史），不读「上一条 bot 回复发出后该群是否变热/变冷」「@bot 频率是否在涨」「pass_turn 命中率是否塌方」。所有反馈型信号即使被采集（[services/llm/usage.py](../../services/llm/usage.py)），也仅供运维/admin 看板消费，不回流到调度阈值。
2. **配置变更必须人介入**：`talk_value` 的下调只能靠 admin 手动触发或 `talk_schedule.json` 静态时段切换。bot 自身没有「最近 1 小时回复 23 次、其中 14 次被忽略 → 主动调低 talk_value」这条路径。

这两条判据下，Omubot ≡ MaiBot ≡ NoneBot diss-anybody ≡ SillyTavern Group Chats 处于**同一代设计**，差异只在工程精修度（比如 Omubot 的 11 位点比 MaiBot 多 2-3 个 bypass 分支）。这不是「行业主流」，这是「上一代的工程公约数」。

---

## 2. 跨学科前沿研究（surpass-MaiBot 的素材库）

本节调研 2024-2026 年群聊调度、turn-taking、信息论决策、上下文 bandit、校准化弃权、off-policy 评估、addressee 识别 7 个方向的最新工作，并标注每篇与 Omubot 现有代码面的具体可挂载点。**不要求一波吃下，§4 路线图会按风险分层落盘**。

### 2.1 Ancestor Hawkes — 群聊自激点过程（arXiv:2605.02613，2026-05）

**核心思想**：把群聊看作多变量自激点过程。每条消息要么是 immigrant（独立发起新话题），要么是 triggered（被某条历史消息激发）。Immigrant 矩阵 K 与 trigger 矩阵 L 分别建模，稳定性由谱半径 ρ(L)<1 保证，参数靠 Bayesian Gibbs 采样在分支变量 B 上估计。**输入只需 sender ID + 时间戳**，不读消息内容——天然适合 Omubot 这种把消息内容留给 LLM 的架构。

**对 Omubot 的可借鉴点**：
- 现有 [services/group_timeline.py](../../services/group_timeline.py) 已经按群保存 turn 时间戳，足以重放出 Hawkes 拟合所需的 (sender, ts) 序列。
- 拟合结果 ρ(L) 是一个**群级热度的标量**，可以直接喂给后续的 Reply Worthiness Score（§3.1）作为「该群是否处于自激爆发期」的 prior。
- 与 MaiBot 的 `talk_value_rules.item_id` 静态群级 override 不同，Hawkes 是**自动**根据该群最近 1-3 小时消息节奏推断热度，不需要人工配 override。

**风险**：Gibbs 采样器是离线 batch，不能放到 hot path。落盘策略是**离线计算 ρ̂(L) 写入 SQLite，热路径只读 cache**（参考 §4.2 P3.13）。

### 2.2 MM-When2Speak — 多模态预测式 turn-taking（arXiv:2505.14654，2026 v2）

**核心思想**：dense 时序分类，把每 0.5 秒滑窗映射到 9 类响应类型 {silence, full_response, affirmation, gratitude, farewell, greeting, question, surprise, pondering}。窗口长 10 秒、stride 0.5 秒。Encoder-Adaptor-LLM 架构（InternViT + Mel + Qwen2-7B-base），文本-only 基线 LLM 被 3× 反超，与人工标注 pipeline 87.62% 一致。

**对 Omubot 的可借鉴点**：
- Omubot 是**纯文本 IM** 不需要 0.5s stride，但**「多分类回复类型」这个抽象可以保留**：当前 [services/scheduler.py](../../services/scheduler.py) 决策是二分类 reply / skip；MM-When2Speak 提示我们应当扩展到至少四类 {silence, light_ack, full_reply, sticker_only}。
- light_ack 和 sticker_only 是 Omubot 已有但**没被调度层认知**的能力——sticker 走的是 [services/sticker_decision.py](../../services/sticker_decision.py)，没有反向告诉 scheduler「我已经回了一个 sticker，本回合视同 reply」。
- 落地形式：把 `should_reply` 的返回值从 `bool` 升级为 `ResponseClass` 枚举，scheduler 不再二分。

**风险**：扩展枚举会让现有的 11 个调度位点都需要适配。属于结构性改动，列入 §4.3 P3.16+。

### 2.3 TurnGPT-in-HRI — 连续 turn-end 预测（arXiv:2501.08946，2025）

**核心思想**：HRI（人机交互）领域首次把 TurnGPT（语言模型预测 EOT 概率）+ VAP（声学预测）两条流融合，给机器人提供连续的「该不该现在开口」概率，replace 传统的固定 silence-threshold。降低响应延迟、减少打断。

**对 Omubot 的可借鉴点**：
- Omubot 的 debounce 是固定时间（`debounce_seconds`），本质是 silence-threshold——和 HRI 圈 5 年前的状态一样。
- TurnGPT 的思路：用一个**轻量分类器**在每条新消息到达时输出 `P(should_speak_now | last_K_msgs)`，而不是死等 N 秒。
- 落地代价低：Omubot 已经有 [services/llm/client.py](../../services/llm/client.py) 的批量请求能力，可以用 Haiku 4.5 当 EOT 分类器，input 只是最近 5 条文本，output 一个 logit。
- 与 MaiBot 完全无重合——MaiBot 没有 turn-end 预测概念。

**风险**：每条群消息都过一次 Haiku 即使 cache hit 也是真金白银。落地必须配额（[services/llm/usage.py](../../services/llm/usage.py) 已有 token 上限），列入 §4.2 P3.14。

### 2.4 AURA ε-greedy + ConUCB — within-session 上下文 bandit（arXiv:2510.27126 / WWW 2020）

**核心思想**：
- AURA：会话内 RL，ε=0.30 / α=0.30，每轮交互后更新 EV 表，10-15 轮即收敛。
- ConUCB：在 contextual bandit 上引入 key-term 级会话，理论 regret bound 优于纯 LinUCB。

**对 Omubot 的可借鉴点**：
- 把 `talk_value` 从「全局静态」升级为「per-(group, time_bucket) 的 bandit arm」：当前小时该群应当用哪个 talk_value，由最近 N 次回复后果（pass_turn 命中、被 @ 提醒、admin 投诉日志）驱动。
- ε-greedy 而非 Thompson sampling 的好处：实现 30 行 Python，[services/llm/usage.py](../../services/llm/usage.py) 已经有 SQLite 存历史，回填 reward 路径短。
- 关键约束：bandit 只决定 `talk_value` 这一个标量，不接管整条调度链——避免 P3.13 引入的 RWS（§3.1）与 bandit 互相打架。

**风险**：reward 函数定义比 algorithm 更难。误把 admin 频繁修改 personality 当成 negative reward 会让 bandit 学崩。reward 设计放 §4.3 P3.17。

### 2.5 信息增益 LLM 提问选择（arXiv:2601.17716）

**核心思想**：让 LLM 在多轮交互中**只在信息增益 ΔH > 阈值时才发问**。Shannon 熵作为主指标，CoT 提示能拉高每轮 IG。

**对 Omubot 的可借鉴点**：
- Omubot 当前 bot 主动发起一段对话的逻辑只在 [services/dream_agent.py](../../services/dream_agent.py)（dream consolidate）和 idle proactive，不区分「这一句新消息提供了多少新信息」。
- 把 IG 当作 `should_reply` 的一个加权项：群里最近 10 条消息如果都在重复「在不在」，IG 极低，bot 应当压抑回复欲；如果突然出现一条「@bot 你昨天讲的 Hawkes 怎么算」这类高 IG 消息，bot 应当抢答。
- 落地形式：用 LLM 的 `next_token_logprobs` 算近似 entropy，不需要离线训练。

**风险**：每条消息算 logprobs 很贵。折中：只对 already-passed-debounce 的候选消息计算，作为 RWS（§3.1）的最后一道乘数。

### 2.6 Art of Refusal / SALU — 校准化弃权（arXiv:2407.18418 / 2507.16951）

**核心思想**：
- Art of Refusal 综述：从 query / model / human values 三视角梳理 abstention 策略。
- SALU：confidence score 调制 abstention reward，幻觉率从 90% → 1.3%。

**对 Omubot 的可借鉴点**：
- Omubot 当前的 `pass_turn` tool 是「LLM 决定不发声」，但**没有信心维度**——LLM 自己也不知道这次 skip 是「我故意冷处理」还是「我没听懂」。
- 引入 confidence-gated skip：`pass_turn` 强制 LLM 输出 `confidence ∈ [0,1]`，confidence < 0.4 时 scheduler 接管，回退到 light_ack（§2.2）而非沉默。
- 这条单独看像锦上添花，但配合 §3.3「反事实静默重放」可以反向定位「pass_turn 用错了哪些位点」。

**风险**：要求 LLM 显式输出置信度会增加 prompt 长度。可借力 [kernel/prompt/builder.py](../../kernel/prompt/builder.py) 的 cache 机制，prefix 不变所以 cache 命中率不掉。

### 2.7 ENIGMA OPE / COPT 反事实训练（EMNLP 2021 / arXiv:2004.14507）

**核心思想**：
- ENIGMA：pseudo-state padding + DICE 估计离策略价值。
- COPT：用结构性因果模型对对话数据做反事实样本扩增。

**对 Omubot 的可借鉴点**：
- Omubot 现在没有任何「反事实评估」机制——上线一个 talk_value 调整，看一周聊天日志是否变好，是肉眼判断。
- ENIGMA 思路最直接的落地：**离线 replay 一段历史消息流，把当时 should_reply 的结果替换成「相反决策」**，然后用 LLM judge 评估「替代轨迹是否更好」。
- 这就是 §3.3 反事实静默重放的理论根。
- COPT 的 SCM 对 Omubot 太重，**只借 ENIGMA 的轨迹替换思路，不上 SCM**。

**风险**：LLM judge 自身有 bias。需 admin 抽样人工校验（每周 30 条）作为 ground truth 校准 judge。

### 2.8 Multi-Party Hangover — addressee 识别（arXiv:2409.18602）

**核心思想**：群聊中识别一句话**指向谁**。用消息图的 degree centrality + outgoing weight 做诊断特征。

**对 Omubot 的可借鉴点**：
- Omubot 现状：[kernel/router.py](../../kernel/router.py) 仅靠 `is_at` 判断是否被点名，没有 mentions 之外的 addressee 推断。
- 多人混聊场景下，一句「你刚那个观点我反对」可能在指 bot 也可能在指上一个发言者。当前 Omubot 默认不响应，错过了大量本该参与的对话。
- 集成代价：addressee 分数作为 RWS（§3.1）的一个 +0.3~+0.6 加权项，不替换现有 `is_at` bypass。

**风险**：误判会导致 bot 抢答原本指向他人的对话。须配合 §2.6 confidence-gated skip 双保险。

---

## 3. Omubot 原创合成（surpass，不只是 catch up）

§2 的 8 篇前沿都是**单点突破**。Omubot 的优势是有完整的群聊场景 + 配置体系 + 调度位点矩阵——可以**把这些点融合成同代框架做不到的中间层**。本节给出三个原创组件。

### 3.1 Reply Worthiness Score（RWS）

**问题**：当前 11 个调度位点散落在 [services/scheduler.py:130-245](../../services/scheduler.py#L130-L245)，每个独立 if-else，互相之间没有可微分的协调机制。新增一个 bypass 就要 patch 一处，删除一个就可能漏覆盖。

**方案**：把 11 个位点折叠成一个由可观测量构成的**单一标量** RWS ∈ [0, 1]，scheduler 唯一决策点变成 `RWS > θ → reply`。

**RWS 公式（首版，待 §4.2 P3.12 落盘）**：

```
RWS(ctx) = sigmoid(
    w_at      * is_at(ctx)                     # +0.6 强 bypass
  + w_addr    * addressee_score(ctx)           # §2.8 多人 addressee
  + w_ig      * info_gain_norm(ctx)            # §2.5 信息增益
  + w_eot     * P_should_speak_now(ctx)        # §2.3 TurnGPT-style
  - w_hawkes  * spectral_radius_recent(group)  # §2.1 Hawkes 反向（§3.2）
  - w_starv   * consecutive_skip_pressure      # 抗饥饿原项
  + w_mood    * mood_mult_residual             # 现 mood_mult 的残差
  + w_sched   * talk_schedule_residual         # 现 talk_schedule 残差
  + b
)
```

**关键设计选择**：
- **mood_mult / talk_schedule 用残差形式**：不是 RWS 的主导项，避免和现有 admin 配置冲突；admin 改 mood 仍然有效，但不会主导决策。
- **w_hawkes 是负权**：群越热，bot 越退（§3.2 详述）。
- **θ 由 §4.3 bandit 自适应**：默认 0.5，按群按时段微调。
- **可解释性**：每次决策都把 8 项加权值写日志，admin 看板上能直接看「这次为什么 reply / skip」。MaiBot 做不到。

### 3.2 Hawkes 反向调制

**问题**：直觉上「群越热 bot 越该参与」是错的。真实场景下，群里一群人激烈讨论时 bot 插嘴只会变成噪声，bot 应当**反向**——群冷清时主动维系，群火热时退后观察。

**方案**：把 §2.1 Ancestor Hawkes 的 ρ̂(L) 当**负权**喂给 RWS。

**形式**：
- 定义 `spectral_radius_recent(group)` = 最近 1 小时该群消息流拟合的 ρ̂(L)，离线每 10 分钟更新写 SQLite cache。
- 当 ρ̂(L) → 1 时（自激爆发临界），RWS 的 `- w_hawkes * ρ̂(L)` 项接近 -1，强力抑制回复。
- 当 ρ̂(L) → 0 时（群冷清），该项 ≈ 0，不抑制。
- bot 的 `is_at` bypass **不**被 Hawkes 调制——被 @ 时无论群多热都要回。

**这是 §2.1 的反向用法**：原论文用 Hawkes 解释群聊热度，Omubot 用 Hawkes 决定 bot **不**说什么。同代框架（MaiBot 等）没有点过程概念，根本想不到这条路。

### 3.3 反事实静默重放

**问题**：当前所有调度阈值的调整都是「上线 → 看一周聊天 → 拍脑袋调」。没有客观度量「这次调整是变好了还是变差了」。

**方案**：基于 §2.7 ENIGMA OPE 思路，构建一条离线 replay pipeline。

**实现要点**：
1. **轨迹采样**：每天凌晨从 [services/group_timeline.py](../../services/group_timeline.py) + [services/llm/usage.py](../../services/llm/usage.py) 联合 SQLite 抽 24 小时所有群的消息流 + bot 决策轨迹。
2. **反事实生成**：对每条 bot 实际 reply 的位点，构造「如果当时 skip 会怎样」轨迹；对每条 skip 位点，构造「如果当时 reply 会怎样」轨迹。reply 内容用 LLM dry-run（不实际发出）。
3. **LLM judge 评估**：用一个 reasoner-class 模型（比如 sonnet-4.6）打分，对比真实轨迹与反事实轨迹，输出三类标签 {真实更好 / 反事实更好 / 不可分辨}。
4. **聚合写报表**：admin 看板每周展示 N 个「该回未回」「不该回回了」点位，附原文链接。
5. **闭环回流**：若某类点位（比如「mood<0.3 时被 @ 仍然 skip」）连续 2 周占「反事实更好」高位，自动写一条 admin 候选规则，**不直接改阈值**，等 admin 确认。

**这个组件让 Omubot 拥有**「自己审计自己调度系统」的能力——MaiBot / NoneBot / SillyTavern 都没有。是 surpass 的关键差异化。

**风险**：LLM judge 成本不可忽略。控制：每天只跑 2-3 个群的样本，每群 ≤30 条候选位点，token 预算放进 [services/llm/usage.py](../../services/llm/usage.py) 的离线配额池，不挤压在线对话。

---

## 4. 三波路线图

把 §3 的合成切成「立刻能上 → 中期重构 → 长期闭环」三波，每波都给到代码触点和回滚方案。

### 4.1 Wave 1 — P3.11：临时止血（1-2 天）

**目标**：堵住当前最高频的可见痛点，不引入新架构。

**改动 1：directed_followup bypass**

- 现状：[kernel/router.py:1116-1133](../../kernel/router.py#L1116-L1133) 用 `TriggerContext(mode="directed_followup")` 包了 follow-up 判定，但 [services/scheduler.py](../../services/scheduler.py) 的 `should_reply` 没识别 mode 字段，仍走 talk_value 抽样。
- 改法：在 `should_reply` 入口加 `if ctx.mode == "directed_followup": return True` 分支，紧贴 `is_at` bypass 之后。
- 测试：新增 `tests/test_scheduler_directed_followup.py`，构造 mode=directed_followup 的 ctx，断言 100% reply（D2 cancel-path 不适用，纯同步逻辑）。

**改动 2：consecutive_skip 阈值从 5 → 3 + 时间衰减**

- 现状：[kernel/config.py](../../kernel/config.py) 默认 `consecutive_skip_max=5`，且 skip 计数永久累加。
- 改法：默认改为 3，同时加 30 分钟时间衰减（最近 30 分钟内的 skip 才计入）。
- 触点：[services/scheduler.py](../../services/scheduler.py) 的 skip 计数 + [services/group_timeline.py](../../services/group_timeline.py) 的最近事件时间戳。
- 测试：构造 4 次连续 skip 跨 30 分钟，断言第 4 次不会强制 reply（衰减生效）；构造 3 次连续 skip 在 5 分钟内，断言第 4 次强制 reply。

**改动 3：planner_smooth 从 3.0 → 2.0（保守降低）**

- 仅 config 改动，不动代码。等 Wave 2 RWS 上线后这个旋钮被吸收掉。

**Wave 1 完成判据（D4）**：
- pytest 全绿 + ruff/pyright 全绿。
- 灰度 24 小时（用户已说暂时剃去灰度门，但仍要看日志）：993065015 + 984198159 群里 directed_followup 命中率 ≥ 95%（[services/llm/usage.py](../../services/llm/usage.py) SELECT 验证）。
- 回滚：单 commit revert，30 秒。

### 4.2 Wave 2 — P3.12-14：RWS 中间层（1-2 周）

**P3.12 RWS scaffolding**

- 新建 [services/scheduler/rws.py](../../services/scheduler/rws.py)（不存在则建），定义 `compute_rws(ctx) → float` 与 8 项加权值的 `RWSExplanation` dataclass。
- 默认权重让 RWS 复刻**当前调度行为**：迁移期 RWS 不改变决策结果，只输出可解释日志。
- 灰度方式：env flag `RWS_SHADOW=true` 在 993065015 同时跑 old path + RWS path，比对一致性 ≥ 99%。
- 测试：[tests/test_rws_shadow.py](../../tests/test_rws_shadow.py) 加 50 条历史消息回归，old vs RWS 决策 diff ≤ 1%。

**P3.13 Hawkes ρ̂(L) cache**

- 离线 cron job：[services/scheduler/hawkes_offline.py](../../services/scheduler/hawkes_offline.py)，每 10 分钟扫所有活跃群最近 1 小时消息，跑 Gibbs 拟合（限制 200 轮），写 `storage/hawkes_cache.db`。
- 热路径改动：RWS 读 cache，cache miss → 用最近 30 分钟消息率简化 fallback。
- D6 提醒：纯 .py 改动需要 rebuild bot；hawkes cache db 路径走 .gitignore。

**P3.14 EOT 概率（§2.3 TurnGPT-in-HRI）**

- 新建 [services/scheduler/eot_classifier.py](../../services/scheduler/eot_classifier.py)，调 Haiku 4.5 over 最近 5 条文本输出 `P_should_speak_now`。
- 配额：每个群每分钟最多 2 次调用，超限 fallback 0.5。
- 测试：mock LLM 返回固定 logit，断言 RWS 单调性。

**Wave 2 完成判据（D4）**：
- RWS_SHADOW 一致性 ≥ 99%（一周日志），切到 RWS_PRIMARY 之后再观察一周决策日志「为什么 reply / skip」可读。
- 维护日志加一条「RWS scaffolding 上线」+ 同模式扫描结果（D1）：列出所有 11 个原调度位点是否都被 RWS 项覆盖。
- 回滚：env flag `RWS_PRIMARY=false` 立刻回退到 Wave 1 状态。

### 4.3 Wave 3 — P3.15+：闭环在线学习（按需）

**P3.15 反事实静默重放（§3.3）**

- 新建 [services/scheduler/counterfactual_replay.py](../../services/scheduler/counterfactual_replay.py)，实现轨迹抽样 + LLM judge + 报表。
- admin 看板加 `/admin/replay/weekly` 路由（属 admin/frontend 范围，需 invoke skill omubot-admin-console，本路线图按本仓 D6 + UI guideline 要求实施）。
- 不直接改阈值，候选规则进 admin 审核队列。

**P3.16 ResponseClass 枚举（§2.2 MM-When2Speak）**

- `should_reply: bool → ResponseClass {silence, light_ack, full_reply, sticker_only}`。
- 11 个调度位点迁移 + [services/sticker_decision.py](../../services/sticker_decision.py) 集成。
- 这是结构性改动，需要 D3 迁移清单（旧→新位点四列对照表）。

**P3.17 ε-greedy 自适应阈值（§2.4 AURA）**

- 仅调整 RWS 的 θ，不接管 RWS 内部。
- reward 函数：admin manual 标注（每周 50 条）作为 ground truth，bandit 在线只更新 θ。
- 风险：reward 设计错误会让 bandit 学崩，必须有 `BANDIT_FREEZE=true` 紧急关停 flag。

**P3.18 Confidence-gated skip（§2.6 SALU）**

- `pass_turn` 工具增加 `confidence` 输出，confidence < 0.4 触发 light_ack（依赖 P3.16）。

**Wave 3 完成判据**：
- 反事实重放报表连续 4 周显示「该回未回」「不该回回了」总和单调下降。
- 维护日志记录每个 P3.x 子项独立的同模式扫描 + 回滚路径。

---

## 5. 验收 / 风险 / 回滚（每波分述）

### 5.1 Wave 1 验收

| 项 | 通过条件 | 证据来源 |
|---|---|---|
| pytest | 全绿 | 本地 `uv run pytest` |
| lint | ruff + pyright 全绿 | 本地命令 |
| directed_followup 命中 | ≥ 95% | `usage.db` SELECT |
| consecutive_skip 衰减 | 单元测试 4 case 全绿 | tests/ |

**风险**：directed_followup mode 字段在某些异常路径未设置 → 默认走老抽样不退化。

**回滚**：`git revert <commit>` + `docker compose restart bot`，30 秒。

### 5.2 Wave 2 验收

| 项 | 通过条件 | 证据来源 |
|---|---|---|
| RWS_SHADOW 一致性 | ≥ 99% | shadow log diff 统计 |
| Hawkes cache 命中 | ≥ 95%（活跃群） | `hawkes_cache.db` SELECT |
| EOT 调用预算 | 不超 token 上限 5% | `usage.db` 聚合 |
| 决策可解释 | admin 看板能展示每条决策 8 项加权值 | UI 截图 |

**风险**：Hawkes Gibbs 偶发不收敛 → fallback 简化版；EOT classifier 偶发抖动 → 配额硬限。

**回滚**：env flag `RWS_PRIMARY=false`，瞬时回退。Hawkes cache 表物理删除不影响主流程。

### 5.3 Wave 3 验收

| 项 | 通过条件 | 证据来源 |
|---|---|---|
| 反事实报表 | 4 周「该回未回 + 不该回回了」总和下降 | weekly_replay 表 |
| ResponseClass 迁移 | 11 位点四列对照表 + D3 迁移清单 | docs/migrations/ |
| bandit 不学崩 | θ 漂移 ≤ ±0.15 | `bandit_state.db` |
| confidence 输出 | LLM 输出 confidence 字段命中率 ≥ 90% | tool call 日志 |

**风险**：bandit 学崩；LLM judge 偏差。

**回滚**：每个 P3.x 独立 flag；`BANDIT_FREEZE=true` 冻结 θ；`COUNTERFACTUAL_REPLAY=false` 关 replay job。

---

## 6. 参考资料

### 6.1 学术论文（surpass 素材）

| ID | 标题 | 出处 | 用途 |
|---|---|---|---|
| Hawkes-2026 | Ancestor Hawkes Process for Group Chat | arXiv:2605.02613 | §2.1 + §3.2 |
| MM-W2S-2026 | MM-When2Speak: Multimodal Predictive Turn-Taking | arXiv:2505.14654 | §2.2 + §4.3 P3.16 |
| TurnGPT-HRI-2025 | TurnGPT in Human-Robot Interaction | arXiv:2501.08946 | §2.3 + §4.2 P3.14 |
| AURA-2025 | AURA ε-greedy Adaptive Surveys | arXiv:2510.27126 | §2.4 + §4.3 P3.17 |
| ConUCB-2020 | Conversational Contextual Bandit | CUHK WWW 2020 | §2.4 |
| IG-LLM-2026 | Information Gain in LLM Question Asking | arXiv:2601.17716 | §2.5 |
| Refusal-2024 | Art of Refusal: Abstention Survey | arXiv:2407.18418 | §2.6 |
| SALU-2025 | Confidence-Gated Abstention RLHF | arXiv:2507.16951 | §2.6 + §4.3 P3.18 |
| ENIGMA-2021 | Off-Policy Evaluation for Dialogue | EMNLP 2021 | §2.7 + §3.3 |
| COPT-2020 | Counterfactual Off-Policy Training | arXiv:2004.14507 | §2.7（仅借思路） |
| MP-Hangover-2024 | Multi-Party Addressee Recognition | arXiv:2409.18602 | §2.8 |

### 6.2 同代基线（仅作下限对照）

| 框架 | 角色 | 用途 |
|---|---|---|
| MaiBot | 已弃用同代 | §1.2 同构判定 |
| NoneBot diss-anybody | 同代变种 | §1.2 |
| SillyTavern Group Chats | 同代变种 | §1.2 |

### 6.3 Omubot 内部触点

| 文件 | 角色 |
|---|---|
| [services/scheduler.py](../../services/scheduler.py) | 11 调度位点宿主，Wave 1-2 主战场 |
| [kernel/router.py](../../kernel/router.py) | directed_followup mode 注入点 |
| [services/group_timeline.py](../../services/group_timeline.py) | Hawkes 输入数据源 |
| [services/llm/usage.py](../../services/llm/usage.py) | 配额池与 reward 数据源 |
| [services/sticker_decision.py](../../services/sticker_decision.py) | ResponseClass.sticker_only 集成点 |
| [kernel/prompt/builder.py](../../kernel/prompt/builder.py) | confidence 字段 prompt 改造点 |

---

## 7. 与 v2 的最终差异声明

v3 不是 v2 的增量，是定性重写：

- **v2 立足点**：「Omubot 已经是行业主流，只缺一两个 bypass」→ 错。MaiBot 弃用框架不是行业主流。
- **v3 立足点**：「Omubot ≡ MaiBot 同处 Tier 0 开环静态阈值，前沿已是闭环自适应」→ 把追平目标从 MaiBot 改为前沿。
- **v2 修订幅度**：单点 bypass 补丁。
- **v3 修订幅度**：三波路线图，最终引入 Hawkes / RWS / 反事实重放三件 same-generation 框架做不到的中间层。

落实节奏由用户决定。Wave 1 是低风险止血，可立即排期；Wave 2-3 须按用户优先级与 [docs/tracking/omubot-humanization-part6-execution.md](omubot-humanization-part6-execution.md) 主线协调。


