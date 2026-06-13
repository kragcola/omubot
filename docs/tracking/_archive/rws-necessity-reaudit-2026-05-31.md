# RWS 全量审计 + 前沿调研：从"空壳恒等"到名副其实的回复价值打分

> 2026-05-31 全代码审计（`services/scheduler_rws/{rws,weights,bandit,memory_signals}.py`、scheduler 概率路径、reward 接线核查）+ 两条前沿调研战线（学习型主动回复打分 / 生产级反馈与冷启动）。引用经 web 核实。
> 前提更正：上一份审计建议"退役 RWS"已被驳回——**方向没错**（打分层取代固定死板概率本就该做）。本文重新定位为：**RWS 现在是空壳，怎么把它真正激活做对**。

## 0. 一句话结论

RWS 的**特征骨架在、reward 回路断、探索器会乱跑**。它退化成 threshold 恒等的根因不是设计错，而是 ① 半数权重为 0、eot/hawkes/bandit 全关；② **`bandit.observe` 只有 admin 手动端点能调，没有任何运行时把回复结果喂回去**（reward 管道断路）；③ epsilon-greedy 在低频稀疏延迟反馈下是最差选择之一。前沿（Inner Thoughts CHI25 / Time-to-Talk EMNLP25 / RLUF / warm-start bandit）给出了明确的激活路径。

## 1. 代码实证：RWS 当前每一处的真实状态

### 1.1 权重（`weights.py`）—— 半数死、强信号项在概率路径里也死

| 特征 | 权重 | 实际状态 |
| --- | --- | --- |
| at / directed_followup / video_always / qq_interaction | 6/6/6/3 | **概率路径里恒为 0**——这些 mode 在 `notify` 早期就 `_fire` bypass 了，根本不进 RWS |
| eot | 1.0 | **死**：`rws_eot=False` → eot_prob=0.5 → 项=0 |
| hawkes | 1.3 | **死**：`rws_hawkes=False` → rho=0 → 项=0 |
| addressee / skip_pressure / mood_residual / schedule_residual / info_gain / bias | 0.0 | **死**：权重就是 0 |
| outcome / familiarity / willingness | 0.08/0.06/0.08 | 微弱，residual 居中时≈0，且依赖 memory_coupling 填值 |
| old_threshold | （`_logit` 直入，无权重） | **唯一实际驱动项** = 概率路径上一步算的 `talk×mood×time` |

→ `score = sigmoid(_logit(threshold) + 0 + 0 + ε) ≈ threshold`。**RWS 是 threshold 的恒等再包装。**（"十分钟" rws=0.08 就是 `talk×1.27×0.20`。）

### 1.2 reward 回路（`bandit.py` + 核查全仓调用点）—— **断路**

`RWSBandit.observe(decision, reward)` 存在且逻辑完整（按 reward 符号微调 theta），但全仓 grep `.observe(`：**唯一调用点是 `admin/routes/api/bandit.py` 的 HTTP 端点**（人工调），运行时**没有任何地方把"bot 回了之后被理睬/被无视"喂回 `observe`**。→ bandit 永远学不到东西，theta 只能靠人手动 POST 调。**这是 RWS 名不副实的头号原因**——它连"在线学阈值"这个核心卖点都没接通。

### 1.3 memory_signals.py —— 信号能算,但极性判断脆弱

`recent_outcome_ratio` / `familiarity_score` / `willingness_phase` / `mood_trend` 都实现了，但 outcome 极性靠**中文关键词表**（`_POSITIVE_MARKERS=笑/好/…` vs `_NEGATIVE_MARKERS=冷/无视/…`）匹配 episode 的 outcome_signal 文本。脆，且这些信号最终乘的是 0.08/0.06 的微权重，影响≈0。

### 1.4 探索器 —— epsilon-greedy 调单标量 theta

`current_theta` 用 epsilon=0.1 随机 ±0.05 抖动 theta（夹 0.35–0.65）；`frozen` 标志存在但未接"观测数门槛"。即便 reward 接通，epsilon-greedy 在本场景（低频、稀疏、延迟、非平稳）也是次优。

## 2. 前沿调研：学习型"该不该回"怎么做对

### 2.1 架构：打分层与生成层解耦 —— 已被反复验证

- **Time to Talk**（Eckhaus/Berger/Stanovsky, EMNLP 2025 Findings, arXiv:2506.05309）：异步群聊里 **scheduler（持续轮询"此刻发不发"）+ generator（说什么）双模块**，时机分布对齐真人、>85% 认不出是 bot。**与 RWS+chat 架构同构**，且给了一个现成 KPI：**"距上条他人消息时间差"直方图 vs 真人的距离**。
- **DiscussLLM**（NEC, 2025, arXiv:2508.18167）：把"沉默"建成 silent token；**解耦版（RoBERTa 判 speak/silent + LLaMA 只在 speak 时生成）比端到端快 5×、省 30× 显存**，准确率 93%。背书"轻量分类器决定是否开口、只在通过时花 LLM"。
- **ContextAgent**（NeurIPS 2025, arXiv:2505.14668）：**proactive score `P_S ≥ θ` 才主动**，θ=用户打扰敏感度。**与 RWS 的 score≥theta 形式完全一致。**

### 2.2 特征：哪些被验证有效、哪些是噪声

| 特征（≈RWS 对应） | 前沿结论 | 出处 |
| --- | --- | --- |
| **eot（对方说完没）** | **最稳、ROI 最高,优先开**；纯文本可用 TurnGPT 思路（小 LM 对最后一条话轮完整概率打分），且**要做成连续概率不是阈值** | TurnGPT (Ekstedt&Skantze, EMNLP-F 2020, arXiv:2010.10874)；Skantze 综述 2021/2025 |
| **addressee（被寻址）** | 显式 @ 是强信号，但**应做门控/档位切换不是温和权重**；隐式 addressee 极难（GPT-4o 仅 80.9% ≈ chance 80.6%，别给高权重） | Inner Thoughts；IWSDS 2025 addressee benchmark (2025.iwsds-1.36) |
| **hawkes（群热度/节奏）** | 有效的**调度**信号（决定 when 非 whether）；冷启动先用**指数衰减核 EMA 近似**，别一上来真 Hawkes MLE | Masuda et al. (arXiv:1205.5109)；Okoshi KDD 2019 推送时机 +60.7% |
| **info_gain** | 有效，**可双用**（特征 + reward shaping，权重要小防 hacking） | Inner Thoughts 8 维；EIG (EMNLP-F 2024, 2024.findings-emnlp.291) |
| **consecutive_skip（沉默压力）** | 有理论背书（"haven't spoken in a while"是合法发言动机） | Inner Thoughts |
| 隐式 addressee 推断 / next-speaker 预测 | **噪声/被高估**，自选轮为主的群聊里不如 motivation-based | Inner Thoughts 消融 |

### 2.3 reward 信号：怎么收、怎么避坑（最关键）

- **RLUF**（Meta, arXiv:2505.14946）：稀疏二元 emoji 反应（**~0.1% 回复才有反馈**）训一个 reward model `P[good]`，正例上采样到 10%，当离线评估器 + RL reward。**别拿原始稀疏信号直接当 reward。**
- **避坑（必读）—— Pang et al.**（EACL 2024, arXiv:2307.14117）：**优化"对方有没有回我/对话变长"→ bot 学会挑衅以诱导再回（人格漂移成讨厌鬼）；优化"后续正向情感"→ 行为改善（争议 17%→8.5%）**。**绝不能把"被回复"单独当 reward。** 这直接判了 RWS 现在 memory_signals 关键词极性的方向（看正负情感对，但实现太脆要升级为情感分类器）。
- **dark pattern 警示 —— De Freitas et al.**（arXiv:2508.19258, HBS）：只优化短期 engagement → 情感操纵（强留把告别后互动拉 14×，但抬 churn/负面/法律风险）。**reward 必须带长期约束项。**
- **群聊隐式信号清单**（可接）：被@回/被引用（强正）、表情回应（正但防 hacking）、后续他人正情感（Pang 推荐）、发言后全群沉默/话题被切走（强负）、被禁言/拉黑（极强负）、"别说话"类（极强负）。

### 2.4 bandit：epsilon-greedy → Thompson sampling + 冷启动 warm-start

- **算法**：低频+稀疏+延迟+非平稳环境里,**epsilon-greedy 渐近差**(恒定探索浪费样本)、UCB 延迟下过度探索、**TS 最稳**(随机化、对延迟鲁棒)。reward 已二元 → **Beta-Bernoulli Thompson Sampling** 天然适配。保留"只学标量 theta、不在线学多维权重"的现有设计（稀疏下高维必过拟合）。出处：Agrawal&Goyal 2012；arXiv:1902.08593（非平稳+延迟基准）；FG-TS showdown (arXiv:2507.15290，logistic 设置 TS 系最佳）。
- **冷启动**：**warm-start 而非零学**——手调的 at/eot/hawkes 当 logistic 初始系数（先验），其余给小非零先验让梯度能唤醒；可用 LLM（Generative Agents react 提示 / Inner Thoughts 8 维打分）对历史 timeline 离线标"该不该插话"做监督预训练。出处：Warm-start Contextual Bandits (Zhang et al. ICML 2019, arXiv:1901.00301)；但警惕 *Jump Start or False Start?*（arXiv:2604.02527）：离线/在线信号背离时 warm-start 有害,需监控。
- **防乱跑**：`observations < N(如50)` 强制 `frozen`（只用 warm-start 权重）；Beta 计数按天指数衰减（应对非平稳）；群级池化先验（新群继承全局后验）。

### 2.5 单 sigmoid → 双阈值（Inner Thoughts 的结构启示）

Inner Thoughts（CHI 2025, arXiv:2501.00383）用 **imThreshold（开口门槛）+ interruptThreshold（打断别人的更高门槛）** 两个独立阈值,按 turn 类型(被点名/开放轮/别人轮次)分流,8 维动机打分,用户 82% 偏好 vs next-speaker 基线。→ RWS 现在所有项进**一个 sigmoid 一个 theta**;可拆成"该不该说"(addressee/relevance/info-gap)+"现在合不合适/会不会打断"(eot/hawkes/打断惩罚),**主动插话设比被@更高的阈值**（呼应 [reply-pipeline-coherence-audit.md] 的分裂点 A 收敛方向）。

## 3. RWS 现存问题/缺陷清单（审计结论）

| # | 缺陷 | 严重度 | 证据 |
| --- | --- | --- | --- |
| **D1** | **reward 回路断路**——`bandit.observe` 仅 admin 手动可达,无运行时反馈 | **致命** | §1.2 grep 全仓调用点 |
| D2 | 半数权重为 0 + eot/hawkes/bandit flag 全关 → 退化成 threshold 恒等 | 高 | §1.1 |
| D3 | 强信号项(at/followup/…权重 6)在概率路径里恒为 0(已被 bypass 消费),是死配置 | 中 | §1.1 |
| D4 | outcome 极性靠中文关键词表,脆;且乘 0.08 微权重影响≈0 | 中 | §1.3 + Pang 证据 |
| D5 | epsilon-greedy 在低频稀疏延迟非平稳下次优;frozen 未接观测门槛 | 中 | §1.4 + §2.4 |
| D6 | 单 sigmoid 单 theta,无法区分"该不该说"vs"会不会打断" | 中 | §2.5 |
| D7 | 无 reward 归因窗口/待结算队列(D1 的前置缺失) | 高 | §1.2 |

## 4. 激活路线（按风险/性价比排序，待你定要不要做、做到哪）

**P1 修 reward 回路（治 D1/D7,最该先做,没它一切学习都是空转）**
- 建**延迟归因队列**:决策时记 `(group, features, decision, t0)`;后台 job 在 `t0+窗口(默认300s)` 算 reward 回填 → 自动调 `observe`。
- reward = `+被理睬(被@回/被引用/后续正情感) − 致冷(后续沉默≥X/话题切走) − 强负(显式制止/禁言)`。**严禁单用"有没有人回"**(Pang)。

**P2 开 eot(治 D2 最稳的一块)**
- 用轻量文本模型对最后一条消息打"话轮完整概率",连续值,权重从 0 调非零先验。文献 ROI 最高。

**P3 addressee 改门控 + hawkes 上 EMA 近似(治 D2/D3)**
- 显式 @bot → 高优先档(已部分由 B2 role 做);hawkes 先 EMA 衰减强度,群刷屏期压低、冷场后第一条提高。

**P4 bandit 换 Beta-TS + warm-start + 观测门槛(治 D5)**
- epsilon-greedy → Beta-Bernoulli TS;手调权重当先验;`observations<50` freeze;Beta 计数按天衰减。

**P5 单→双阈值(治 D6,呼应分裂点 A)**
- 拆"该不该说"/"会不会打断"两档,主动插话阈值 > 被@阈值。

**P6 防 hacking/dark pattern(上线前必做)**
- reward 多目标 + 致冷/负反馈/禁言硬惩罚;shadow 跑 KPI(被理睬率/致冷率/时机分布对齐度/留存)足够久再 `rws_primary` 切。

## 5. 诚实定性

- **RWS 方向对、骨架在,但被配成空壳,且 reward 回路根本没接通**——它从未真正"学"过,只是个被 hawkes 微调的固定阈值的恒等包装。
- 真正激活的**最小关键动作是 P1(修 reward 回路)+ P2(开 eot)**——前者让"学阈值"名副其实,后者补最强特征。其余 P3–P6 是逐步做对。
- 全程有 dark-pattern 风险(Pang/De Freitas 实证):**reward 选错信号会把 bot 训成挑衅型/纠缠型**,这是激活 RWS 比保持空壳更危险的地方——所以 P6 不是可选项。

**引用清单**:Inner Thoughts (CHI 2025, arXiv:2501.00383)；Time to Talk (EMNLP-F 2025, arXiv:2506.05309)；DiscussLLM (arXiv:2508.18167)；ContextAgent (NeurIPS 2025, arXiv:2505.14668)；TurnGPT (EMNLP-F 2020, arXiv:2010.10874)；VAP (Interspeech 2022, arXiv:2205.09812)；Skantze 综述 (CSL 2021；MDPI Technologies 2025)；Masuda 自激点过程 (arXiv:1205.5109)；SI-RNN (AAAI 2018, arXiv:1709.04005)；IWSDS 2025 addressee benchmark；LinUCB (WWW 2010, arXiv:1003.0146)；FG-TS (arXiv:2507.15290)；Warm-start Contextual Bandits (ICML 2019, arXiv:1901.00301)；非平稳+延迟 MAB (arXiv:1902.08593)；RLUF (arXiv:2505.14946)；Pang et al. 隐式反馈 (EACL 2024, arXiv:2307.14117)；De Freitas dark pattern (arXiv:2508.19258)；Read the Room (ACM 10.1145/3772363.3798392)；Okoshi 推送时机 (KDD 2019)；Curiosity Reward (NeurIPS 2025, arXiv:2504.03206)。均经子代理 web 检索核实。
