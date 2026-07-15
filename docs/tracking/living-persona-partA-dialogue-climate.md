# Living Persona — Part A：Dialogue Climate（情绪状态）

> 系列：[Living Persona / 角色生命化](living-persona-part0-overview.md)（Part 0 总纲）
> 状态：**立项待批 · 2026-06-08**
> 取代规划来源：[omubot-grayscale-issue17-research-dialogue-climate.md](omubot-grayscale-issue17-research-dialogue-climate.md)（2026-05-27 调研，理论与现状拆解仍有效；但其第五章 Phase 0–4 迁移路径已被 2026-06-08 代码现实推翻，本书重写）
> 关联：[omubot-grayscale-issue17-research-mood-feedback.md](omubot-grayscale-issue17-research-mood-feedback.md)（mood 反馈调研）、[issue17-part0-landing-2026-06-06.md](issue17-part0-landing-2026-06-06.md)（part0 已落地，本书起点）
> 在系列中的角色：bot 当下"什么心情"——统一情绪状态 + on-read 动力学；为 Part B-L3 提供 tension/mood，并承载 Part C 的关系温度。
> 原则：**全量愿景保留，分期推进，全量可搁置但不砍。** 本书不缩小目标架构，只把"何时做、先做哪段、用什么机制"定清楚。

---

## 0. 一句话

把 mood / 日程 / 好感度 / 日历 / 时钟 / 耦合策略这 6 个独立系统，统一为一个 **Dialogue Climate（对话气候）** 状态-动力学-策略运行时；本书锁定单维 tension 闭环为 MVP，全量 6 维 + 完整动力学保留为后续 Phase，并就 R5（状态更新时机）做出**研究支撑的架构决策：on-read 连续时间闭式衰减**。

---

## 1. 立项前的代码现实核验（2026-06-08）

设计主文写于 2026-05-27，本书立项前已把其每条关键断言拿到当前代码逐一 grep/read 核验。**4 处已过时或不准确，必须作为修正起点**：

| # | 设计主文断言 | 核验结果（file:line 证据） | 对立项的影响 |
|---|---|---|---|
| R1 | RuntimeStateBus 是 "dry-run 骨架，未接入正式 runtime" | **已过时**。bus 已在生产大量读写：[affection/engine.py:86](../../plugins/affection/engine.py#L86)、[chat/plugin.py:143](../../plugins/chat/plugin.py#L143)、[scheduler.py:1070](../../services/scheduler.py#L1070)、[sticker_tools.py:307](../../services/tools/sticker_tools.py#L307)、block_trace 多个 provider。"dry-run 骨架"是 persona-v2 引入时的历史措辞，humanization 层早已真实接管 | Phase 1 写新 slot 是低风险增量，比文档设想简单 |
| R2 | `MOOD_CURRENT_SLOT` "无消费者（死代码）" | **不准确**。[sticker/decision_provider.py:179](../../services/sticker/decision_provider.py#L179) 在读它（读空则写回 sticker density 反馈空壳）。但无生产者用 MoodClassifier 填它 → 链路断、读到的多是自写空壳 | Phase 0 若激活生产者会**改变 sticker 决策行为**，需纳入回归 |
| R3 | Phase 0 "~50 行接线死代码，与 Issue 17 同批" | **已偏离**。2026-06-08 落地的 part0（commit `1b82fa2`）走了不同路线：不激活 MoodClassifier、不接 CouplingPolicy，而是把 reaction/poke 信号直接喂 MoodEngine 三维 nudge（[mood.py:214](../../plugins/schedule/mood.py#L214) `register_interaction_signal`） | 原 Phase 0 **作废**，本书 Phase 重画 |
| R4 | CouplingPolicy 是"唯一统筹点，仅测试调用" | **属实且更弱**：全仓仅 [`__init__.py`](../../services/humanization/__init__.py#L24) 导出，**零生产调用**；且它是纯 dataclass，无 `synthesize` 逻辑 | ClimatePolicy 是"从零写"，不是"升级 CouplingPolicy" |

**已验证可用的基础设施（设计可直接继承，无需重建）**：
- `ctx.add_block(text, label, position, priority, source)` —— [kernel/types.py:365](../../kernel/types.py#L365)，schedule/affection 已用
- `provider_bus.has_provider(name)` 让位模式 —— [slang/plugin.py:191](../../plugins/slang/plugin.py#L191)、[style/plugin.py:88](../../plugins/style/plugin.py#L88) 真实在用，Phase 4 可复刻
- `on_post_reply(ctx: ReplyContext)` 钩子 —— affection/style/memo 三插件已用（[kernel/types.py:601](../../kernel/types.py#L601) 定义），反馈回路载体就绪

---

## 2. R5 架构决策：状态更新时机（核心决策）

### 2.1 冲突陈述

MoodEngine 现状是 **15 分钟 TTL 惰性缓存 + 过期才重算**（[mood.py:144-164](../../plugins/schedule/mood.py#L144)）；设计要的 ClimateDynamics 是**指数平滑 + 动量 + 差异化衰减**，直觉上需要 per-turn / 周期 tick 持续演化状态。两者语义看似不兼容——一个"过期才算"，一个"每步都变"。这是设计主文一笔带过、却决定整体工作量与架构形态的关键点。

候选方案三选一：

| 方案 | 机制 | 代价 |
|---|---|---|
| A. per-turn tick | 每条消息/每个 scheduler tick 调 `dynamics.update()` 推进状态 | 需常驻 tick；空群也耗算力；与现有惰性缓存冲突；状态依赖"被调用频率"而非真实时间 |
| B. 周期后台 tick | 固定间隔（如 60s）后台推进 | 常驻定时任务；多群×多用户 fan-out；重启丢状态；同样不对齐真实时间 |
| **C. on-read 连续时间闭式衰减** | 只存 `(value, baseline, last_ts)`，**读时**按 `Δt = now − last_ts` 解析式重算 | 无常驻 tick；与缓存天然兼容；状态对齐 wall-clock |

### 2.2 研究依据

**结论：选 C（on-read 连续时间闭式衰减）。** 依据如下：

1. **数学成熟性 —— 时间感知 EMA 有闭式解。** 连续时间指数平滑的有效平滑系数为
   `α(Δt) = 1 − exp(−Δt / τ)`，其中 Δt 为两次访问间的真实流逝时间、τ 为时间常数。
   据此，"向 baseline 衰减"与"向 signal 平滑"这类**一阶**动力学，读时按 Δt 解析求值与每 tick 迭代**数学等价**——无需固定步长（[Exponential Moving Average Sampled at Varying Times](https://stackoverflow.com/questions/1023860/exponential-moving-average-sampled-at-varying-times/1023906)；[Exponential decay](https://en.wikipedia.org/wiki/Exponential_decay)、[Half-life](https://en.wikipedia.org/wiki/Half-life) 给出闭式衰减的标准形）。

2. **设计主文引用的开源实现本身就是 on-read 闭式。** 主文 §2.2 引的 **EchoText** 衰减是 `applyEmotionDecay(): 1 − exp(−lambda · elapsed_minutes)`——按流逝分钟解析算，不靠 tick；§2.3 引的 **Personaut** 是 `compound decay: 1 − (1−rate)^turns_elapsed`——同样基于 elapsed。设计在抄机制时其实已经在抄 on-read 范式，只是规划层没点破。

3. **前沿论文倾向连续时间。** 《Time-Continuous Modeling for Temporal Affective Pattern Recognition in LLMs》（[arxiv 2601.12341](https://arxiv.org/html/2601.12341v1)）主张以连续时间而非离散步建模情感时序；主文已引的《Explicit State Dynamics》（[arxiv 2601.16087](https://arxiv.org/abs/2601.16087)）的指数平滑同样可写成时间参数化形式。方向与 C 一致。

4. **omubot 当前代码已经是 C。** 2026-06-08 落地的 `_active_nudge`（[mood.py:240](../../plugins/schedule/mood.py#L240)）就是 on-read 闭式：`factor = 1.0 − (age / decay_s)`，按 `now − ts` 实时算，零 tick。选 C = **沿用刚验证过的本仓范式**，不引入新的运行时形态。

### 2.3 决策与边界

- **承重动力学（衰减 + 向 baseline/signal 平滑 + 基线漂移）全部 on-read 闭式**：存 `(value, baseline, last_update_ts)`，读时按 Δt 解析重算并回写。15 分钟缓存**降级为结果 memoization**（短 TTL，仅省重复计算），不再承担状态语义——缓存与动力学不再冲突。
- **二阶动量项（β·ΔS 迟滞）是唯一不能纯闭式的部分。** 决策：MVP **不做动量**；迟滞效应改由 **baseline 的慢时间常数**（τ 大）近似——"被骚扰后慢慢恢复"用 tension 快衰减 + baseline 慢漂移即可表达，无需显式二阶项。若后续 A/B 证明动量必要，再以"事件触发时记一次 ΔS 快照"的方式做半闭式补丁，不引入常驻 tick。
- **持久化（R6）顺势简化**：因状态是 `(value, baseline, ts)` 三元组，per-user×per-group baseline 落盘只需周期快照（复用 [dream/plugin.py:283](../../plugins/dream/plugin.py#L283) 已有的 `clear_stale_per_session` 同款过期清理思路），重启按 ts 解析恢复，无需重放历史。

> R5 一句话：把"状态随时间演化"重新表述为"状态是 last_ts 与 now 之间的解析函数"，tick 冲突即消失。这是被本仓现有代码、设计引用的开源实现、以及前沿论文三方共同支持的方向。

---

## 3. 重画的 Phase 路径（取代设计主文第五章）

起点变了：part0 已用 MoodEngine nudge 落地、bus 已是热路径、MoodClassifier 仍死、CouplingPolicy 空壳。据此重画。**全量愿景不变，仅重排顺序与机制**。

| Phase | 目标 | 关键产物 | 依赖 | 状态 |
|---|---|---|---|---|
| ~~P0 旧~~ | ~~激活 MoodClassifier + 接 CouplingPolicy~~ | —— | —— | **作废**（被 part0 取代） |
| **P-now** | reaction/poke → MoodEngine 三维 nudge | commit `1b82fa2` | —— | ✅ 已上线 2026-06-08 |
| **M1（MVP）** | tension 单维闭环 | IrritationSensor + on-read tension 动力学 + prompt 行为指导 | P-now | 本书申请立项 |
| M2 | ClimateState 统一状态对象 + on-read 动力学引擎（全维） | `services/dialogue_climate/state.py` + `dynamics.py` | M1 | **实现侧已落地 2026-06-16**（休眠，默认关，未接 sensor；见 §9） |
| M3 | Sensor 适配层（schedule/calendar/message/interaction/circadian）+ 反馈回路 | 6 个 Sensor + on_post_reply 写回 | M2 | 搁置·保留 |
| M4 | ClimatePolicy 合成 + Adapter 输出 + provider 让位 | Policy + PromptAdapter/HumanizerAdapter | M3 | 搁置·保留 |

> 全量（M2–M4）**搁置但不砍**：M2–M4 的设计、维度、动力学规则、配置段全部保留设计主文 §4/§9 原样，只等 M1 验证 on-read 范式与调参手感后顺序推进。任何一期都可独立停在稳定态。

---

## 4. M1（MVP）范围定义

**唯一目标：把"被连续打扰 → 变烦 → 自然恢复"这一条因果链跑通且调得动。** 选 tension 单维，因为它①是 Issue 17 burst 的实际痛点，②衰减快、最容易观测验证，③信号源（@/poke 频率）已存在。

M1 做：
- **IrritationSensor**：@ 频率 / poke 频率 → tension 信号。复用 [qq_interactions.py](../../services/humanization/qq_interactions.py) 已解析的 poke 速率与 is_tome（part0 已接 `_POKE_TENSION`，M1 升级为频率聚合）。
- **on-read tension 动力学**：存 `(tension, baseline, last_ts)` per group；读时 `tension(now) = baseline + (tension_last − baseline)·exp(−(now−last_ts)/τ_tension)`。τ_tension 取快衰减（对应主文 λ=0.12）。
- **prompt 行为指导**：tension 超阈值 → `ctx.add_block` 注入"已被连续打扰，回复更短更冷淡"行为指导（行为指导而非标签，主文 §5.1 共识 6）。
- **灰度开关 + 单测**：`dialogue_climate.m1_enabled` flag 默认关；单测断言 on-read 衰减在固定 Δt 下的解析值 + 阈值触发 prompt。

M1 **不做**（留给 M2+）：ClimateState 统一对象、其余 5 维、动量项、per-user baseline 持久化、Policy 合成、Adapter 让位、MoodClassifier 激活。

M1 与现有 mood 的关系：M1 的 tension 通过**已有的** `register_interaction_signal`（part0 通道）注入 MoodEngine，不另起状态容器——M1 是 part0 的频率聚合升级，不是新系统。ClimateState 统一容器延后到 M2 才出现。

---

## 5. 风险登记（立项需正面持有）

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| R2 | 激活 mood 生产者会改变 [sticker decision_provider](../../services/sticker/decision_provider.py#L179) 行为 | 中 | M1 不激活 MoodClassifier，不碰该链路；留到 M3 接 message sensor 时带 sticker 决策回归 |
| R5 | 缓存/动力学冲突 | **已解**（见 §2） | on-read 闭式，缓存降级 memoization |
| R6 | per-user baseline 持久化是新基础设施 | 中 | M1 不持久化（tension 快衰减，重启丢失可接受）；M2 才引入，用三元组快照 + 过期清理 |
| R7 | 行数/工时估算乐观（主文 ~750 行 2 PR） | 高 | 重估：M1 ~250 行/1 PR；全量 M2–M4 实估 **1500–2500 行**+大量调参，分 3–4 PR。情绪系统主成本在"调到感觉对"，无自动化测试可覆盖 → M1 必须先验证调参手感再批 M2 |
| R8 | 调参无 ground truth，易陷入主观反复 | 中 | M1 设可观测指标：tension 注入次数、超阈触发率、衰减半衰期实测；用烤群真实数据回放校准，不靠拍脑袋 |
| R9 | 与人设/humanizer 既有 mood 消费者耦合 | 中 | 全程 provider 让位模式（[slang](../../plugins/slang/plugin.py#L191)/[style](../../plugins/style/plugin.py#L88) 同款），新路径未验证不夺旧路径 |

---

## 6. 排期建议

1. **M1 先行**，独立 PR，灰度开关默认关，烤群灰度观测 1–2 周，校准 τ_tension 与阈值。
2. M1 调参手感确认后，再批 M2（ClimateState + 全维 on-read 动力学）。**M2 立项以 M1 实测数据为前置**。
3. M3/M4 视 M2 稳定度顺序推进，每期可独立停在稳定态。
4. 全量完成 = 设计主文 §4 目标架构，但走的是 on-read 而非 tick 路线。

---

## 7. 决策模板

```text
Dialogue Climate 立项：
[ ] 批准 R5 决策：on-read 连续时间闭式衰减（放弃 per-turn/周期 tick）
[ ] 其他 R5 倾向：___

范围：
[ ] 批准 M1（tension 单维 MVP）先行，M2–M4 搁置保留
[ ] 调整 MVP 维度为：___
[ ] 全量一次推进（不推荐，R7/R8 风险）

前置门槛：
[ ] 同意 M2 立项以 M1 烤群实测数据为前置条件
[ ] 其他：___
```

---

## 8. 运行实测与修订（启用后回填）

> A-M1 随 Living Persona 全量于 **2026-06-09 启用**（`dialogue_climate.m1_enabled=true`，runtime override）。本章记录启用后的真实数据与据此做的修订，是 §6.1「烤群灰度观测 1–2 周校准 τ/阈值」的执行记录。

### 8.1 2026-06-14 采集口缺口：昵称呼叫此前未进 tension 感知（已修）

**实测**（启用后 4 天，进容器只读 `storage/living_persona/m1_metrics.db`）：M1 tension 事件**全程仅 1 条**（一次真@，delta 0.03，峰值 0.03，从未触阈 0.12）。初判"信号源稀疏"是错的——同窗口群里有大量**伪@：连续叫 bot 昵称**（group 993065015 用户连环刷「emu。」，一分钟内 4 条）。

**根因**：`kernel/router.py` 三条线判据不一致——回复义务 `is_addressed`（认昵称前缀）、role 判定 `addressing.evidence`（认 `nickname_original`）都把昵称等同于 @，**唯独 M1 mention 感知用了最窄的 `_message_ats_self`（只认协议层真@）**。而 persona source 明确"用名字/昵称叫我 = @我 = 直接对我说话"（别名：凤笑梦/emu/笑梦/姆/姆姆/凤同学/凤/Emu）。结果：真实世界"被连续 cue"的主要形态（昵称连呼）对 tension 零贡献，R8 的采集口径错了，不是样本不够。

**修订**（采集口收敛到既有信号，2 文件）：M1 mention 触发条件改用上方已算好的 `AddressingContext`，新增谓词 `_addressing_triggers_m1_mention`，认 `target=="self" and evidence ∈ {at_self, nickname_original}`，与回复义务线同源。误报天然有保障（`nickname_original` 只匹配句首昵称+vocative 边界，「看凤笑梦表情包」不触发）；`reply_to_self` 刻意排除（语义弱于主动 cue）。4 个回归测试覆盖。详见 [维护日志 2026-06-14](../../maintenance-log.md)。

### 8.2 待校准（R8 接续）

- **mention 单价**：`_M1_IRRITATION_MENTION_TENSION=0.03`。昵称接入后信号量从"4天1次"→"数十次/天"，连呼可能很快撞 0.2 cap（`_M1_IRRITATION_TENSION_CAP`），需跑几天看 `m1_metrics.db` 真实分布后下调。**这恰好补齐 R8 长期缺的校准样本**——8.1 修复前根本采不到数据。
- **τ_tension 半衰期**：默认 `_M1_DEFAULT_TENSION_TAU_S=600s`，待积累超阈触发样本后用 `m1_tension_metrics()`（injection_count / trigger_rate / half_life_s）实测回放校准。
- **M2 解冻前置仍未满足**：§6.1 要求"M1 调参手感确认"才批 M2，8.1 之前数据为空、谈不上手感；现在采集口修好，需重新积累 1–2 周有效样本再评估。

---

## 9. M2 实现侧落地（2026-06-16，休眠态，默认关）

> 用户在知情"M1 调参样本仍为空（6-14 采集口修复后约 2 天，自然流量 0 条新 tension 事件；live `m1_metrics.db` 仅 13 条 6-13 压测残留）、§6.1 前置未满足、R7/R8 风险"后，裁定**直接落地 M2 全维实现侧**，调参用公开实证数据辅助锚定（不拍脑袋）。本节是该决定的执行记录。

### 9.1 落地范围（对标 Wave 2 story_arc：建模块+开关+测试，不接消费者）

- 新增 `services/dialogue_climate/state.py`：`ClimateState`（6 维 energy/valence/openness/tension/trust/familiarity + 3 baseline + 元数据，字段对齐设计主文 §5 Phase 1）、`ClimateSignal`（M3 sensor 输出契约，本批只定义不产信号）、`clamp01`。
- 新增 `services/dialogue_climate/dynamics.py`：`ClimateDynamics`（on-read 闭式 `resolve`/`apply_signal`/`drift_baseline`，差异化 λ，familiarity 调 α，**按 §2.3 不做动量项**）+ `ClimateEngine`（per-key transient 容器，镜像 MoodEngine M1 模式，`m2_enabled` 门控）+ `ClimateDynamicsConfig`。
- 配置 4 文件加 `dialogue_climate.m2_enabled`（默认 `false`，restart_required）：`plugin.py`/`config.default.json`/`config.schema.json`/`plugin.json`。
- 新增 `tests/test_climate_dynamics.py`（15 例）。

### 9.2 调参：公开实证数据锚定（替代设计主文占位 λ）

因 live 校准样本为空，差异化衰减率改以已发表的情感动力学数据锚定（M3 接 sensor 后用真实信号再调）：

- **Verduyn & Lavrijsen (2015), _Motivation and Emotion_ 39:119–127**：27 种情绪里悲伤最长（~120h），烦躁/惊讶/羞耻最短，悲伤可达烦躁 ~240×。→ 锚定快/慢维**比例**：tension/irritation 必须最快衰减、trust/familiarity 最慢。落为 per-hour λ：tension 0.69（半衰期 ~1h）> energy 0.35 > valence 0.17 > openness 0.14 > trust 0.012（~58h）> familiarity 0.004（~173h ≈ 1 周）；tension:familiarity ≈ 170×（取 Verduyn 比例精神，不照搬其临床自报绝对量级）。
- **Emotional inertia / AR(1) ESM 文献**（Kuppens、Hamaker；de Haan-Rietdijk 2017 连续时间 ESM）：自回归惯性映射到指数平滑 α（默认 0.2，familiarity 提升）；连续时间建模背书 R5 的 on-read 闭式选择。

### 9.3 收口证据（D4）

- `ruff check` `All checks passed!`；`pyright` `0 errors, 0 warnings`；3 个 schedule JSON `python -m json.tool` 通过。
- `tests/test_climate_dynamics.py` 15 passed（核心：固定 Δt 闭式解析值 = `baseline+diff·exp(−λΔt)`、差异化衰减 tension>trust、α familiarity 提升、baseline 漂移、disabled 零写入）；`tests/test_mood.py + test_climate_dynamics.py` 66 passed；`-k schedule` 186 passed（flag 默认关无回归）。
- **边界负证据**：`grep ClimateState|ClimateEngine|m2_enabled` 在 plugins/kernel/services（除 dialogue_climate 自身与测试）仅命中 `plugin.py` 的 `m2_enabled` 配置字段定义——无 generator/chat/mood/client 消费者，休眠态成立。
- **回滚**：`m2_enabled` 默认关 = 零行为变更（无消费者，天然成立）；删 2 新文件 + 移除 config 字段，或 `git checkout`；无 DB / 无 NapCat 变更。

### 9.4 仍未做（M2 当时）

(此节记录 M2 落地时的状态；M3/M4 已于 2026-06-16 续做，见 §10。)

Sensor 适配层、on_post_reply 反馈回路、ClimatePolicy 合成、PromptAdapter/HumanizerAdapter/ThinkerAdapter 让位、provider 让位、baseline durable 持久化、接 prompt/humanizer/thinker。

---

## 10. M3/M4 实现侧落地（2026-06-16，休眠态，默认关）

> 承接 M2（§9）。用户裁定"不做最小闭环，要做就全量"+ 4 个架构点冻结（见 [M3/M4 调研](dialogue-climate-m3-m4-research-2026-06-16.md) §5、[实现方案](dialogue-climate-m3-m4-impl-plan-2026-06-16.md)）。全程灰度 `m2_enabled`/`m3_sensors_enabled`/`m4_policy_enabled` 默认关，关时逐字节回归基线。

### 10.1 落地范围（Wave 串行）

- **M3-0**：ClimateEngine 键 per-(group,session)→**per-(group,user)**（F1）+ `clear_stale` 过期清理；tension 迁移地基（M1↔M2 闭式等价性测试钉死）。
- **M3-1**：`services/dialogue_climate/sensors.py`（`Sensor`/`SensorHub` + Schedule/Irritation/Circadian）；运行态接线（schedule on_pre_prompt 馈 Schedule/Circadian；qq_interactions 馈 Irritation，F2 tension 切 ClimateEngine 并行 M1）；`m3_sensors_enabled` 配置。
- **M3-2**：Interaction（per-user familiarity，F1 直接落位）/ Calendar（富版 calendar_context，F3）/ Message（复活 MoodClassifier 的 label→climate 映射，F4）三 sensor + familiarity/calendar 运行态馈入。
- **M3-3**：`m2_metrics.py` `ClimateMetricsRecorder`→`storage/living_persona/m2_climate.db`（6 维快照 + signal 分布）；ClimateEngine recorder 钩子；schedule `on_post_reply` 反馈回路（F6 用 elapsed_ms/user_id）。
- **M4**：`policy.py` `ClimatePolicy.synthesize`（纯函数，ClimateState→reply_bias/delay_multiplier/mood_label/guidance，阈值对齐设计主文 §9）；schedule on_pre_prompt 在 `m4_policy_enabled` 时注入"对话气候"block 并**让位 M1 tension block**（climate 接管 tension）；`m4_policy_enabled` 配置。

### 10.2 与 plan 的偏离（如实记录）

- **provider-bus 让位收窄**：plan 原想走 PromptProviderBus + `has_provider` 让位（register_provider 范式）。调研发现 `QueryContext` 拿不到内存 ClimateEngine（只有 RuntimeStateBus），走 provider 须先把 ClimateState 推过 bus 槽（需自有 contract + ownership）。改用 ClimatePolicy 纯函数 + 持有 engine 的 schedule plugin 直接注入，达成核心目标省掉 bus 复杂度。affection block（priority 20）与 climate block 暂共存，未强行合并为单一 block。
- **Humanizer/Thinker adapter 收窄**：`PolicyOutput` 已返回 `delay_multiplier`/`reply_bias` 结构化字段，但未接到 Humanizer/Thinker 消费点（更深热路径），字段就绪、消费留增量。
- **MessageSensor 运行态馈入收窄**：sensor 类 + 测试就绪、在 default_sensors，但 classifier 接到用户消息路径喂 `message_label` 未做（涉与 sticker 槽协调），现馈入为 no-op。

### 10.3 收口证据（D4）

- `ruff` `All checks passed!`、`pyright` `0 errors`、3 schedule JSON 合法。
- 新增测试：`test_climate_dynamics.py`(19，含 per-(group,user) 隔离/clear_stale/M1↔M2 等价)、`test_climate_sensors.py`(21)、`test_climate_m2_metrics.py`(6)、`test_climate_policy.py`(8)。
- 全量 `pytest` **2743 passed / 17 skipped**（flag 默认关无回归；`-k schedule` 189 passed）。
- **边界负证据**：M3 提交时 grep 确认 reply 路径无 ClimateState 消费者；M4 后唯一消费者是 schedule on_pre_prompt 的 `_maybe_inject_climate_block`，且 `m4_policy_enabled` 默认关。
- **回滚**：三 flag 默认关零行为变更；M4 关时走旧 M1 block（双跑过渡保留）；删 climate 文件 + 移除 config 字段或 `git checkout`。

### 10.4 后续整合已实现（2026-07-15，待本轮部署验收）

- **单一 snapshot/provider**：新增 `CLIMATE_CURRENT_SLOT` 与 `ClimateProvider`。LLM 每轮在 Thinker 前发布一次按 `(session, group, user)` 校验的 snapshot，关系文本先按 `privacy_mask` 构建，再由 provider 输出唯一“关系上下文 + 本轮气候方向”candidate；schedule/affection 仅在该 candidate 实际存在时让位，关 `m4_policy_enabled` 仍可回旧 fallback。
- **adapter 真消费**：Thinker 从同一 snapshot 消费 `reply_bias`；Humanizer 按当前 `user_id` 读取同一 snapshot，climate 存在时由 `delay_multiplier` 单独拥有延迟倍率，避免再与 legacy mood 重复相乘。真实 streaming 首段与 non-streaming 单段均不再绕过 Humanizer。
- **MessageSensor 生产馈入**：Chat 用户消息路径调用本地 `MoodClassifier.classify()`，仅把 label/confidence 喂 SensorHub，不写 `MOOD_CURRENT_SLOT`；按 `message_id` 幂等，分类异常 fail-open。@/poke 在频率 mutation 前去重，IrritationSensor 只应用本次事件的边际 burst 增量。poke 的进程内 nonce 附着在解析事件对象上，同一对象重放幂等、不同到达对象分别处理；OneBot/NapCat 不提供稳定 notice ID，因此跨反序列化 replay 与同秒完全相同的合法 poke 无法同时区分。
- **durable slow baseline**：`storage/living_persona/climate_baselines.db` 只保存 energy/valence/openness 三个慢 baseline、持久严格递增 `revision` 与 wall-clock `updated_at`，不恢复 tension 等瞬态峰值。store 具备 schema/version/fingerprint 门禁、有限范围校验、批量 flush、取消 requeue、revision CAS、single-flight start/close/schema worker 与 producer-before-store shutdown 顺序；metrics DB 继续只作观测。
- **动力学修正**：energy/valence/openness 的 transient + slow baseline 作为连续时间二元系统解析推进，24h one-step 与 hourly partition 仅有约 `2.2e-16` 浮点差；避免读取频率改变 durable persona baseline，baseline 速率单位保持 per-day。
- **M1 完全退役**：删除 MoodEngine M1 state/guidance/recorder 与运行配置面；mention/poke、schedule event replan、Dream/StoryArc 均读取 ClimateEngine。历史 `m1_metrics.db`、只读 CLI/catalog 记录保留，不再有运行写入。
- **低优先级缺陷**：persona drift 的 bot-name 后缀现覆盖 `呀/哦/啦/呢` 与 `~ / ～ / ……`，声明-only 回复稳定 fail-closed，不再残留尾字。
- **验证**：reviewer 两轮 Important 均 RED→GREEN，最终 stable snapshot `0 Critical / 0 Important`；本地 full pytest `3446 passed / 17 skipped`，任务文件 Ruff clean、Pyright 0、schedule JSON 与 tracked/untracked diff-check clean。部署与运行态证据在本轮 tracker/维护日志补齐。

---

## 附：核验命令留痕（D4）

```text
ls services/dialogue_climate            → No such file（全量未实现，确认）
grep ClimateState|ClimateSignal|...     → 0 命中（确认）
grep 'MoodClassifier('  非测试          → 0（死代码确认）
grep CouplingPolicy 非测试非定义        → 仅 __init__ 导出（空壳确认）
grep on_post_reply 定义+使用            → affection/style/memo 在用（钩子就绪）
grep _SLOT= + 生产 set/get              → bus 已热路径（R1 推翻 dry-run 说）
grep has_provider 非定义                → slang/style 在用（让位模式就绪）
```
