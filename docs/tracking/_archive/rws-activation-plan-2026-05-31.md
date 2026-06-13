# RWS 激活方案（从空壳到名副其实的回复价值打分）

> **状态：2026-05-31 P1–P7 全量编码完成 + 开 flag 激活，全量 pytest 2314 passed / ruff / pyright(范围内) 全绿；rebuild 待执行。** 收尾 D3 清单见 [docs/migrations/rws-activation-2026-05-31.md](../migrations/rws-activation-2026-05-31.md)，维护日志见同日「RWS 激活 P1–P7 全量收尾」条。本文以下为原始实施方案（保留备查）。
>
> 2026-05-31 实施方案。承接审计 [rws-necessity-reaudit-2026-05-31.md](rws-necessity-reaudit-2026-05-31.md)（现状 + 缺陷 D1–D7 + 前沿调研）。本文只讲**怎么落地**：每阶设计、接缝（已核实行号）、reward 公式、配置、回滚、风险。
> 定位：RWS 方向对（打分层取代固定死板概率）、骨架在，但 reward 回路断、特征空置。本方案把它逐阶激活，**直接按设计实现 + 单测/离线验证保证正确性 + 可一键回退 + 带 dark-pattern 防护**。
> **不走 shadow/灰度观察**（2026-05-31 用户定）：bot 尚未发布、无活跃群与测试群，线上 shadow 观察无真实流量可看、纯空转。激活直接生效（`rws_primary`/各 flag 直接开），正确性靠单测 + offline replay + 设计审查保证；上线后 KPI/metric 仍记录供事后回看，但**不作为"先观察再推进"的前置门**。

## 0. 总原则

1. **先修回路，再加特征，最后学**：P1（reward 管道）是地基——没它 bandit 永远空转；P2（eot）补最强特征；P4 才换学习器。顺序不可乱。
2. **直接生效、不灰度观察**：每阶按设计直接实现并开启（无活跃群/测试群，shadow 观察无流量可看），正确性靠单测 + offline replay；KPI/metric 落盘供事后回看，不作前置门。
3. **reward 选错比不学更危险**（Pang/De Freitas 实证）：P6 防护与 P1 绑定，非可选。
4. **不在线学多维权重**：稀疏反馈下高维 logistic 必过拟合。bandit 只学标量 theta（保留现有设计），特征权重靠 warm-start 先验 + 离线监督。

## 0.5 复用基线（2026-05-31 全仓复审，避免重复造轮子）

复审结论：**RWS 的"算分—决策—bandit—落盘—replay"框架几乎全已搭好并接进 scheduler，真正缺的只是反馈闭环那一段接线。** 本方案据此把"新增"压到最小。逐能力对照：

| 能力 | 仓库现状 | 复用/扩展/新建 | 关键件:行号 |
| --- | --- | --- | --- |
| RWS 打分公式 / 决策接线 / shadow-primary flag | **已有** | 复用，不动 | `scheduler_rws/rws.py:57` `compute_rws`、`scheduler.py:1235` `_maybe_compute_rws` |
| bandit reward 回灌口 | **已有但悬空**（仅 admin 手点） | 复用 `observe` 口 | `scheduler_rws/bandit.py:33`、`scheduler.py:404` `observe_rws_bandit` |
| 延迟归因 / 待结算队列 | **没有**（只有 deferred-fire / reconcile-loop） | **新建队列**，但复用 loop 范式 | 范式参考 `scheduler.py:1469` `_reconcile_self_mute_loop`、`scheduler_hawkes/offline.py:76` |
| reward 入队挂点 | **已有** on_post_reply hook | 复用作"记一笔"挂点 | `kernel/bus.py:291` `fire_on_post_reply`、`kernel/types.py` `ReplyContext`(含 group_id/user_id/reply_content/thinker_action) |
| 发言后群反应读取 | **已有** | 复用，不新建捕获 | `scheduler.py:1058` `_latest_assistant_reply_after`、`timeline.py:296/320` `get_turn_time`/`recent_interaction_count` |
| 对 bot 的表情回应/poke | **已解析,只是没当 reward** | 复用 `is_tome` 信号 | `humanization/qq_interactions.py:90` `is_tome`、router 已 dispatch |
| 弱 reward 代理(被理睬/被无视) | **已有** | 复用作反应度量原料 | `plugins/chat/plugin.py:98` `_timeline_reply_delay_s`、`:118` `_timeline_consecutive_no_reply` |
| eot / 话轮完整性 | **已有两套,已接** | 复用,二选一,**不新建** | `scheduler_eot/classifier.py` `EOTClassifier`+Cache(已喂 `RWSFeatures.eot_probability`)、`llm/arbiter.py:95` `judge_completeness` |
| hawkes / 群活跃度 | **已有 proxy,已接** | 复用;真 Hawkes=背后替换 | `scheduler_hawkes/cache.py:74` `estimate_rho_from_times`、`offline.py:18` refresher |
| 情感 / valence | **关键词表 vs MoodEngine** | 复用 MoodEngine valence 替关键词表 | `schedule/mood.py:112` `MoodEngine`/`recent_profiles`、`memory_signals.py:70` `mood_trend` 已接 |
| metric 落盘 | **已有通用设施** | 复用,只用新 metric_key | `block_trace/store.py:394/424` `record_runtime_metric`/`list_runtime_metrics`、`scheduler.py:1417` 封装 |
| bandit 状态 admin 端点 | **已有** | 复用 | `admin/routes/api/bandit.py` `GET /bandit/rws` |
| offline replay / 反事实框架 | **框架已有,无人写数** | 复用 `ReplayStore`,补采样喂数(非造结构) | `scheduler_replay/replay.py`(`record_run` 全仓无生产调用方) |
| Thompson / contextual bandit | **没有**(仅 epsilon-greedy) | **真新建**(P4 后期,先复用 epsilon 跑通闭环再升级) | — |
| RWS admin 看板 | **没有专区** | **新建前端区块**,仿 MetricCard | `SchedulerView.vue:195`(仅槽位指标) |

**真正要新建的只有四样**：① P1 的待结算队列(复用 loop 范式)；② Thompson bandit(P4 后期，且先用现成 epsilon 跑通)；③ RWS admin 看板前端；④ memory_signals 极性升级(其实是改用现成 MoodEngine valence)。其余全是**接线/复用**。

**最小闭环(P1)= 纯接线，零新数据结构**：`on_post_reply` 入队 → settle loop(仿 reconcile)→ 用 `_latest_assistant_reply_after`/`recent_interaction_count`/`qq_interactions.is_tome`/`_timeline_consecutive_no_reply` 测反应 → 算 reward → 现成 `observe_rws_bandit` → 现成 `record_runtime_metric` 落盘。

## 1. P1 — 修 reward 回路（治 D1/D7，地基，最先做）

**问题**：`RWSBandit.observe(decision, reward)` 全仓唯一调用点是 `admin/routes/api/bandit.py`（人工）；运行时无反馈 → bandit 空转。

**设计（复审修正：纯接线，复用既有件，唯一新建是待结算队列）**：把每次 fire/skip 决策记进一个**待结算队列**，过窗口后用"后续群反应"算 reward 回填 `observe`。

- **决策时入队**（`notify` 的 prob fire/skip 两个分支末尾）：记 `PendingReward(group_id, features_snapshot, decision, t0, turn_baseline=len(get_turns), rws_score)`。features_snapshot 存当时算好的 role/threshold/各信号，供日后离线训权重。
- **结算**（后台 job，复用 `_reconcile` 式 loop 或新 `asyncio` task）：对 `now - t0 >= reward_window_s`（默认 300s）的条目，读窗口内群反应算 reward，调 `self._rws_bandit.observe(...)` + 落盘 `block_trace_store.record_runtime_metric(metric_key="rws_reward", …)`（设施已存在，store.py:394）。
- **reward 公式（组合信号，严禁单用"被回复"——Pang）**：
  ```text
  reward = clamp(
      + w_ack  · 被理睬(窗口内 有人@bot / 引用bot那条 / 后续他人消息正情感)
      − w_cold · 致冷(bot 发言后 群沉默≥cold_s 或 话题被切走)
      − w_neg  · 强负(显式"别说话" / bot 被禁言/踢)
  , -1, 1)
  ```
  - 反应数据源：`get_turns` 窗口内新 turn（timeline.py）、`recent_interaction_count`（scheduler.py:1295 已有）、NapCat 表情/reply notice（router 已解析）。
  - skip 决策也结算（reward 用反事实近似：skip 后群自然延续=skip 对了；skip 后冷场且有人等 bot=skip 错了），让 bandit 双向学。
- **极性升级（治 D4）**：`memory_signals` 的中文关键词极性表 → 轻量情感判定（先复用现有 mood/sentiment 设施，避免新模型）。

**配置**：`[rws_reward] enabled=false（默认）, window_s=300, cold_s=120, w_ack/w_cold/w_neg`。
**接缝**：`services/scheduler.py`（队列 + 结算 loop + observe 调用）、`services/scheduler_rws/memory_signals.py`（极性）、`kernel/config.py`（新 config 段）。
**验证**：单测——构造"发言后被@回"→ reward>0；"发言后沉默"→ reward<0；结算窗口未到不结算；D2 cancel-path（结算 task 取消不污染队列）。
**上线方式（不灰度）**：直接开 `enabled=true` 接通 reward 回路；bandit 是否同步学由 P4 的 `min_obs` 门槛自然控制（观测数不足时本就 frozen，无需人为分阶"先只记不学"）。正确性由 §验证的单测保证。
**回滚**：`rws_reward.enabled=false` → 回队列空转（== 现状）。

## 2. P2 — 开 eot（治 D2，最稳特征）

**复审更正：eot 不用新建。** 仓库已有**两套**话轮完整性判定且 `scheduler_eot` 已接进 RWS：`scheduler_eot/classifier.py` `EOTClassifier.classify`（LLM 打分 `{probability,reason}` + `EOTCache` TTL/限频）已喂 `RWSFeatures.eot_probability`（`scheduler.py:1251`），受 `rws_eot` flag 门控（现关）；另有 `llm/arbiter.py:95` `judge_completeness`（"用户说完没"，arbiter 触发用）。

**设计（开关 + 验证 + 必要时降本，非新建）**：
- **直接开 `rws_eot=true`**，让现成 EOTClassifier 生效（`weights.eot=1.0` 已就位）。权重沿用现有 1.0（设计审查值），无活跃群可观察相关性，不靠观察调权重。
- EOTClassifier 是 **LLM 调用**（带 cache/限频），若成本/延迟可接受直接用；若太贵，再补一个**规则旁路**（结尾标点/连词启发式）作为 cache miss 时的 fallback——这是唯一可能的小新增，且是降本不是造能力。
- **不碰 arbiter judge_completeness**（它管触发时机，与 eot 特征用途不同，二选一即可）。
**配置**：`humanization.rws_eot=true`（+ 可选规则 fallback 开关）。
**接缝**：`scheduler.py:_eot_probability`（现读 cache，fallback 0.5）、`scheduler_eot/`（已存在）。
**验证**：单测——完整句→eot 高、半截话→低；flag 关时回 0.5（现状）。权重 1.0 用现有设计值，待有真实流量后再按数据微调（非上线前置）。
**风险**：低（纯特征 + 现成件，只是没开）。

## 3. P3 — addressee 门控化 + hawkes 开关（治 D2/D3）

- **addressee 改门控**：显式 @bot/reply-bot → 高优先档（已由 B2 `_receiver_role`=addressed + bypass 做）。RWS 里 `addressee` 权重保持 0（不做温和权重——隐式 addressee 不可靠，IWSDS25 证据），改由 role 档位切换 theta（见 P5）。**即 D3 的死权重确认作废，不修复成温和权重。**
- **hawkes 开关（复审更正：不用新建,proxy 已接）**：`scheduler_hawkes/cache.py:74` `estimate_rho_from_times`（基于消息间隔的 rho proxy，文件头自注"非完整 Bayesian Hawkes"）+ `offline.py:18` 后台 refresher **已创建、已 start、已传入 scheduler**（`plugins/chat/plugin.py:1498-1530`），`_hawkes_rho`（`scheduler.py:1290`）已喂 `RWSFeatures.hawkes_rho`（`weights.hawkes=1.3` 负向），只是受 `rws_hawkes` flag 门控（现关）。**直接开 `rws_hawkes=true`** 即生效；真 Hawkes 拟合是日后在 `estimate_rho_from_times` 背后替换（API 已留），非现在新建。
**配置**：`humanization.rws_hawkes=true`。
**接缝**：`scheduler.py:_hawkes_rho`、`scheduler_hawkes/`（已存在且已接线）。
**验证**：刷屏期 rho 高→插话概率降；冷场 rho 低；flag 关时回 0（现状）。

## 4. P4 — bandit 换 Beta-TS + warm-start + 观测门槛（治 D5）

**设计**：epsilon-greedy → **Beta-Bernoulli Thompson Sampling**（reward 已二元，Beta 共轭天然适配；TS 对延迟/稀疏鲁棒）。

- 保留"只学标量 theta、不学多维权重"（稀疏下高维必过拟合）。
- **warm-start**：手调的 at/eot/hawkes 当 logistic 初始先验，不清零从头学；可用 LLM 对历史 timeline 离线标"该不该插话"做监督预训练补冷启动。
- **防乱跑**：`observations < min_obs(默认50)` 强制 `frozen`（只用先验）；Beta 计数按天指数衰减（应对群氛围非平稳）；群级池化（新群继承全局后验）。
- **离线先验证**：上线前用 LinUCB 式 offline replay，在历史日志上无偏估计新阈值策略，再切。
**配置**：`[rws_bandit] algo="thompson", min_obs=50, decay_per_day=0.99, pool_prior=true`。
**接缝**：`services/scheduler_rws/bandit.py`（新 TS 类，保留 RWSBandit 接口）。
**验证**：稀疏 reward 下 TS 收敛、frozen 门槛生效、衰减不让旧数据永久主导；与现 epsilon-greedy 行为对照测试。
**回滚**：`algo="epsilon"` 回现实现。

## 5. P5 — 单 sigmoid → 双阈值（治 D6，呼应通道审计分裂点 A）

**设计**：RWS 现所有项进一个 sigmoid + 一个 theta。拆成 Inner Thoughts 式双阈值：

- **imThreshold（该不该说）**：relevance/info_gap/addressee 主导。
- **interruptThreshold（现在合不合适/会不会打断）**：eot/hawkes/打断惩罚主导,**主动插话设比被@更高的阈值**。
- 与 B2 role 衔接：addressed→低门槛(必答)；ratified→中；overhearer→高门槛(几乎沉默)。**这一步顺带把通道审计的分裂点 A(RWS/B2/necessity 三处判"要不要说")向单一裁定收敛。**
**风险**：中(动主决策结构)，靠单测覆盖各 role×阈值组合的判定 + 设计审查保证；无活跃群可 KPI 对照。
**接缝**：`services/scheduler_rws/rws.py`(双 score)、`scheduler.py`(双阈值 + role 衔接)。

## 6. P6 — 防 reward hacking / dark pattern（与 P1 绑定，上线前必做）

**非可选**——Pang(挑衅型漂移)/De Freitas(情感操纵)实证:reward 选错会把 bot 训坏。

- **多目标 + 硬惩罚（设计期保证，非观察期）**：reward 公式里"致冷率/显式负反馈/被禁言"是硬惩罚项，不被"被理睬"单项压过——**这是 reward 公式的结构性约束，在 P1 落地时就写死，不依赖事后 KPI 观察来"发现训坏了再改"**。dark-pattern 风险靠"reward 公式本身不奖励纠缠/挑衅"在设计上规避（Pang/De Freitas 的教训直接编码进权重符号），而非靠灰度监控兜底。
- **KPI/metric 落盘（事后回看用，非前置门）**：仍记录以下指标供 bot 发布后回看，但**不作为"先观察再推进"的门**（当前无活跃群）：
  - 插话被理睬率、插话致冷率（过度主动检测器）、时机分布对齐度（Time-to-Talk 指标）、长期群留存/被禁言率/"别说话"频率。
- **正确性保证（替代灰度）**：reward 公式与各 gate 的单测 + P4 的 offline replay（历史日志离线评估，若日后有数据）；上线即生效，靠回退 flag 兜底而非灰度观察。

**复审更正（KPI/replay 大半复用）**：KPI 落盘**复用** `block_trace/store.py:394` `record_runtime_metric`（新 metric_key `rws_decision`/`rws_reward`/`rws_theta`，scheduler 已有 `_record_runtime_metric` 封装），**不新建后端**；bandit 状态用现成 `GET /bandit/rws`。**唯一前端新增**=仿 `SchedulerView.vue:195` 的 MetricCard 加一个 RWS 区块。P4 的 offline replay **复用** `scheduler_replay/replay.py` 的 `ReplayStore`/`make_counterfactual_sample`（框架已在，但 `record_run` 全仓无生产调用方）——补的是"采样喂数 + 接线"，不是造结构。

## 7. 落地顺序与依赖

| 阶段 | 治 | 依赖 | 独立可验证 | 风险 |
| --- | --- | --- | --- | --- |
| **P1 reward 回路** | D1/D7 | 无（地基） | ✅ 单测（reward 公式 + 结算队列） | 低 |
| **P2 eot** | D2 | 无 | ✅ 单测 | 低 |
| **P6 防护/KPI** | — | 与 P1 同批 | ✅ reward 公式单测 + metric 落盘 | — |
| P3 addressee门控/hawkes | D2/D3 | 无 | ✅ 单测 | 低-中 |
| P4 Beta-TS/warm-start | D5 | **P1（要有 reward 才有得学）** | ✅ 单测 + offline replay（若有数据） | 中 |
| P5 双阈值 | D6 | P2/P3（特征就位） | ✅ 单测（role×阈值组合） | 中-高 |

**最小关键路径 = P1 + P2 + P6**：reward 回路接通 + 最强特征开 + 防护到位,RWS 就从"空壳恒等"变成"真在学、有 eot 信号、不训坏"。P3–P5 是逐步做对、做全。

## P7 — 触发分层：规则在前、RWS 退为灰区打分（从 reply-scheduling 实施方案 S3 迁入；呼应通道审计分裂点 A）

> 2026-05-31 迁入：此条原为 [reply-scheduling-impl-plan-2026-05-31.md](reply-scheduling-impl-plan-2026-05-31.md) 的 S3，本质是 RWS 激活的一部分（界定"RWS 该管什么"），故归并到本 plan。它是 P1–P6 的**边界前提**——RWS（激活后）只作用于"灰区"，不碰规则层决定的"必回/必不回"。

**问题**：当前 RWS（空壳恒等）+ B2 角色门 + necessity_gate 三处判"要不要说"、互相否决（见 [reply-pipeline-coherence-audit](reply-pipeline-coherence-audit.md) 分裂点 A）。同类项目代码（[peer-projects-codeaudit](peer-projects-codeaudit-reply-scheduling-2026-05-31.md)）证明："该不该回"主体是**便宜的规则在前**，打分只在灰区。

**设计（不推翻 RWS，重整层次）**：明确两层，规则层在打分层之前：

- **规则层（强信号，确定性，在前）**：@bot/reply-bot → 必回（已有 scheduler bypass）；bot↔bot 熔断（S1）→ 必不回；黑名单（`blocked_users`，router.py:1017）→ 必不回。命中即决定，不进打分。LangBot resprule 分层的同构。
- **打分层（灰区，仅非寻址）**：规则层不命中的"要不要主动插话"才走 RWS/B2。RWS **只管灰区**，不覆盖"被寻址必回"。**这界定了 P1–P6 激活的 RWS 的作用域**。
- **收敛分裂点 A**：necessity_gate 不再在 chat 层二次否决规则层已定的"必回"（C1 已用统一 role 缓解，P7 进一步明确"规则层决定的不容打分层推翻"）。

**接缝**：
- `services/scheduler.py` notify：现有 bypass 分支（@/closing/followup/...）即规则层雏形，固化其"先于一切打分"地位；RWS 计算只在概率路径（非寻址）发生（现状已大致如此，P7 是固化+文档化边界，防止再加的门重新搅乱）。
- **可选收敛（复审发现）**：`reply_workflow.py:588 evaluate_group_gate_shadow` 已产出完整 rule 决策（has_trigger/legacy_directed/explicit_continuation → `action=force_reply source=rule`），现为 shadow。P7 若要更彻底，可把它**提权为 primary**，让 notify 内联 if 链与它收敛到单一规则判定——**复用现有件，非搭骨架**。

**这一步主要是"边界固化 + 防回潮"**：代码改动小（明确注释 + 确保新增门不越界），核心价值是**防止未来再往"要不要说"里塞互相否决的层**，并为 P1–P6 的 RWS 圈定作用域。
**验证**：规则层命中（@bot）时 RWS/necessity 不参与的回归；灰区（非寻址）才算 RWS。
**风险**：低（边界明确化，非新行为）。
**与 P1–P6 关系**：P7 是 RWS 作用域的边界界定，建议**先于或同步于 RWS 激活（P1+）**——先划清"RWS 只管灰区"，再往灰区里接 reward/特征，否则激活的 RWS 会重新和规则层抢"要不要回"。

## 8. 全局回滚

- 每阶独立 flag，最外层 `rws_primary=false` 一键回到现固定阈值决策。
- reward 队列/KPI 是旁路记录，关掉不影响决策。
- bandit `algo="epsilon"` / `frozen` 回现行为。
- 纯内存 + 复用既有 `block_trace_store` 落盘，无新 DB schema(reward 走 runtime_metric)。

## 9. 待用户决策

- 是否进入实施、从 **P1+P2+P6**（最小关键路径）起?
- reward 窗口/权重(window_s=300, cold_s=120, w_ack/w_cold/w_neg)用建议默认还是先调?
- 实施时每阶单独出 D3 四列迁移清单 + 测试（不走灰度观察——无活跃群；正确性靠单测 + offline replay + 回退 flag）。
