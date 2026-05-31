# Issue 17 研究附件 — Mood-Schedule-Reply 反馈体系调研

> 状态：2026-05-27 调研完成，待方案设计
>
> 来源：Issue 17 独立立项后的深度研究。用户判断：mood 体系不止重复信息反馈，有与日程系统捆绑进行心情-日程-回复复杂迭代的潜力，有成为 humanization Part 8 的潜质。
>
> 方法：拆解 8 个开源项目代码 + 12 篇学术论文 + omubot 内部系统深度分析。所有结论基于代码，不依赖 README。

---

## 一、omubot 现有 Mood 系统拆解

### 1.1 两套独立的 Mood 系统

omubot 当前存在**两套完全独立、互不通信**的 mood 系统：

| 维度 | MoodEngine（日程插件） | MoodClassifier（humanization 层） |
|---|---|---|
| 位置 | `plugins/schedule/mood.py:112` | `services/humanization/mood_classifier.py:49` |
| 输入 | 日程 slot 的 `mood_hint` + 时间 + 随机噪声 | 最近 12 条消息的文本特征 |
| 输出 | `MoodProfile`（energy/valence/openness/tension） | `MoodDecision`（cold/tired/neutral/playful/high） |
| 标签空间 | 困倦/兴奋/专注/放松/匆忙/低落/烦躁/期待 | cold/tired/neutral/playful/high |
| 存储 | MoodEngine 内存缓存（15 分钟 TTL） | `MOOD_CURRENT_SLOT` on RuntimeStateBus（300s TTL） |
| 消费者 | system prompt 注入、thinker mood text | humanizer delay factor、coupling policy、sticker decisions |
| 生产环境接线 | ✅ 完整（SchedulePlugin.on_pre_prompt） | ❌ **死代码**——无生产代码实例化 |

### 1.2 MoodEngine 数据流（当前）

```
[2AM] ScheduleGenerator → LLM 生成日程 JSON（含 mood_hint per slot）
                          ↓
[存储] ScheduleStore.save() → storage/schedule/YYYY-MM-DD.json
                          ↓
[运行时] MoodEngine._compute(group_id):
    ① _lookup_base(slot.mood_hint) → _MOOD_BASE 字典模糊匹配 → 基础 MoodProfile
    ② ±0.15 随机扰动
    ③ 时间修正（深夜 energy -0.15，午后 energy -0.05）
    ④ 交互量修正（0 条 → openness +0.1；>5 条 → energy -0.05）  ← 唯一的"反馈"
    ⑤ 日历修正（节假日、生日）
    ⑥ 20% 概率异常翻转
    ⑦ clamp
                          ↓
[缓存] 结果缓存 15 分钟，期间所有请求返回同一 profile
                          ↓
[注入] SchedulePlugin.on_pre_prompt → build_mood_block() → system prompt
```

### 1.3 关键缺口

| # | 缺口 | 影响 |
|---|---|---|
| 1 | **无对话内容反馈** | 用户热情/敌意/骚扰不影响 bot 心情 |
| 2 | **MoodClassifier 死代码** | 群聊能量检测能力已实现但未接线 |
| 3 | **无 on_post_reply 钩子** | 回复后不更新任何 mood 状态 |
| 4 | **两系统无桥接** | MoodClassifier 写 RuntimeStateBus，MoodEngine 不读 |
| 5 | **15 分钟缓存** | 活跃对话期间 mood 实质静态 |
| 6 | **mood_hint 不可变** | 日程生成后 mood_hint 固定一天，无法被运行时事件修正 |
| 7 | **无 mood 持久化** | 重启后 mood 归零，无"昨天被骚扰→今天开局就烦"的记忆 |

---

## 二、开源项目代码拆解

### 2.1 BoTTube — `mood_engine.py`（964 行，Python）

**仓库**：github.com/Scottcjn/bottube

**状态机架构**：

- 7 离散状态 Enum：ENERGETIC / CONTEMPLATIVE / FRUSTRATED / EXCITED / TIRED / NOSTALGIC / PLAYFUL
- 显式转移矩阵 `MOOD_TRANSITIONS`：定义每对状态间的基础转移概率 + 命名触发条件
- 50% "stay" 概率抵抗变化——防止快速振荡
- 最小持续时间 `MIN_MOOD_DURATION = 1h`——强制状态稳定性
- 强度（intensity）以 0.1/h 衰减至 floor 0.3

**时间-日程集成**：

```python
TIME_MODIFIERS = {
    "late_night": {"TIRED": 1.7, "ENERGETIC": 0.4, ...},
    "morning":    {"ENERGETIC": 1.3, "TIRED": 0.6, ...},
    ...
}
DAY_MODIFIERS = {
    "Saturday": {"PLAYFUL": 1.5, ...},
    "Monday":   {"FRUSTRATED": 1.1, ...},
}
```

时间和星期作为**转移概率乘数**，不是硬切换。

**交互频率效应**：

- 信号（view_count, comment_sentiment, upload_success, streak_length）记录到 SQLite，24h 窗口聚合
- 高 views → boost EXCITED/ENERGETIC；低 views → boost FRUSTRATED
- 正面 sentiment → boost PLAYFUL；连续上传 → boost ENERGETIC

**输出耦合**：

```python
def get_comment_style(self) -> dict:
    # TIRED: length_factor=0.5, tone="brief"
    # EXCITED: exclamation_density=0.6, tone="ecstatic"
    # 所有输出按 intensity 缩放
```

**对 omubot 的启示**：时间乘数模式直接可用——omubot 的 `_MOOD_BASE` + 时间修正可以升级为概率转移矩阵。信号聚合窗口（24h）对应 omubot 的"被骚扰频率"统计。

---

### 2.2 SillyTavern-EchoText — `lib/emotion-system.js`（JavaScript）

**仓库**：github.com/mattjaybe/SillyTavern-EchoText

**连续情绪模型**：

- 9 个 Plutchik 情绪维度，各 0-100 连续值：love / joy / trust / fear / surprise / sadness / disgust / anger / anticipation
- 人格锚定基线：从 MBTI 类型 + 角色原型关键词推导每个情绪的 resting baseline
- 对立情绪对强制约束：love + disgust > 80 时压制弱者

**衰减机制**（核心创新）：

```javascript
EMOTION_DECAY_PROFILE = {
    love:    { lambda: 0.035 },  // 衰减最慢
    anger:   { lambda: 0.12 },   // 衰减最快
    fear:    { lambda: 0.14 },   // 衰减最快
    joy:     { lambda: 0.06 },
    ...
}
// applyEmotionDecay(): 1 - exp(-lambda * elapsed_minutes)
// 每个情绪向其 baseline 指数衰减，速率不同
```

**长期基线漂移**（affinity shift）：

```javascript
updateAffinityShift() {
    // learning_rate = 0.018 (messages), 0.03 (reactions)
    // 重复正面互动永久提升 joy/trust baseline
    // 重复负面互动永久降低 trust baseline
}
```

**输出耦合**：

```javascript
buildBehavioralGuidance() {
    // 3 层行为指导，按主导情绪生成
    // high anger: "Openly angry. Replies have real heat — blunt, forceful"
    // high joy: "Genuinely happy. Warm, open, might ramble a bit"
}
// 注入为 <emotional_state> XML block 到 LLM prompt
```

**对 omubot 的启示**：
1. **差异化衰减率**——irritation（anger）衰减快，trust 衰减慢，完美匹配"被骚扰后很快恢复，但长期信任缓慢建立"
2. **基线漂移**——重复互动模式永久改变 resting state，对应"经常被某用户骚扰→对该用户默认更冷淡"
3. **行为指导而非标签注入**——告诉 LLM "怎么表现"而非"你现在是什么情绪"

---

### 2.3 Personaut PDK — `src/personaut/emotions/state.py`（Python）

**仓库**：github.com/Personaut/python-pdk

**36 情绪 + Markov 转移**：

- 6 大类（Anger/Sad/Fear/Joy/Powerful/Peaceful）× 6 子情绪，各 0.0-1.0
- `MarkovTransitionMatrix`：类别间转移概率（ANGER stays 0.4, → SAD 0.2, → JOY 0.05）
- 人格特质调制：`emotional_stability` 高 → 抑制反应性；`sensitivity` 高 → 放大反应

**双层衰减 + 基线漂移**：

```python
def decay(self):
    # compound decay: 1 - (1-rate)^turns_elapsed
    # 向 mood_baseline 衰减，不是向 0

def update_mood_baseline(self):
    # 基线本身以 10% learning rate 向当前状态漂移
    # 重复焦虑 → 提升 resting anxiety
    # 基线以 3%/turn 向 neutral 回归（防止永久极端化）
```

**对立情绪压制**：

```python
def apply_antagonism(self):
    # cheerful vs depressed, content vs angry
    # 同时高时压制弱者
```

**对 omubot 的启示**：
1. **基线漂移 + 回归中性**——完美平衡"记住被骚扰"和"不会永久记仇"
2. **人格特质调制转移概率**——omubot 的人设性格可以映射为转移矩阵的调制参数
3. **compound decay**——比简单指数衰减更自然

---

### 2.4 Koishi satori-ai — `src/mood.ts`（177 行，TypeScript）

**仓库**：github.com/gfjdh/koishi-plugin-satori-ai

**极简单值模型**：

- 单一数值 mood per user，4 级：happy / normal / upset / angry
- **每次交互（输入 AND 输出）都消耗 mood**——高频互动自然导致 mood 下降
- 好感度调节消耗速率：厌恶 ×1.5，夫妻 ×0.4
- 每日午夜重置

**输出耦合**：

```typescript
generateMoodPrompt() {
    // upset/angry/happy 各有独立 prompt 文本改变 bot 语气
    // mood < max/2 时拒绝某些功能（如发红包）
}
```

**对 omubot 的启示**：**每次交互消耗 mood** 是最简单有效的"被骚扰→烦躁"机制。不需要复杂的情绪分析——纯粹的交互频率就能产生自然的 irritation 效果。

---

### 2.5 GLaDOS — `emotion_agent.py`（Python）

**仓库**：github.com/dnhkng/GLaDOS

**PAD 双层架构**：

- State（快速层）：对事件即时反应
- Mood（慢速层）：缓慢向 State 漂移
- HEXACO 人格模型生成 personality prompt → LLM 自行推理情绪转移（无硬编码规则）
- `_apply_baseline_drift()`：空闲时 mood 向配置的 baseline 漂移

**对 omubot 的启示**：**双层分离**（快速反应 vs 慢速漂移）是最优雅的架构——omubot 可以用 MoodClassifier 做快速层（已有），MoodEngine 做慢速层（已有），只需加桥接。

---

### 2.6 lacuna_core — `engine.py`（Python）

**仓库**：github.com/ameagaru12-gif/lacuna_core

**三参数体感模型**：

- mood（-1.0 ~ +1.0）、stress（0.0 ~ 10.0）、heart_rate（50-130 bpm）
- 处理管线 per turn：情绪传染 → 惯性滤波 → 1/f 粉红噪声漂移 → 生理耦合 → 参数交互

**惯性滤波**（防止情绪急转）：

```python
# inertia_coefficient 由亲密度动态调节
# 亲密度高 → 惯性低 → 情绪反应更敏感
# 亲密度低 → 惯性高 → 情绪变化缓慢
```

**粉红噪声**（1/f noise）：

- 不是白噪声（完全随机）——粉红噪声产生缓慢、连续、不可预测的漂移
- 模拟真人心情的自然波动模式

**昼夜节律**：

```python
# 凌晨 4 点 heart_rate -3 bpm
# 中午 heart_rate +3 bpm
# stress 随时间自然衰减
```

**对 omubot 的启示**：
1. **惯性滤波**——防止 mood 因单条消息剧烈变化，需要持续刺激才能改变
2. **粉红噪声**——比 omubot 当前的 ±0.15 均匀随机更自然
3. **亲密度调节惯性**——对熟人情绪反应更快，对陌生人更迟钝

---

## 三、学术论文核心机制

### 3.1 Explicit State Dynamics（arxiv 2601.16087，2026）

**最直接可用的数学框架**。

核心：VAD 状态 + 指数平滑 + 动量惯性，外置于 LLM：

```
S_t = α · signal_t + (1-α) · S_{t-1}           # 一阶（无动量）
S_t = α · signal_t + (1-α) · (S_{t-1} + β · ΔS_{t-1})  # 二阶（有动量）
```

- `signal_t`：当前 turn 的瞬时情感信号（由无记忆估计器从对话提取）
- `α`：平滑系数（0.1-0.3），控制对新信号的敏感度
- `β`：动量系数（0.8-0.95），控制情绪惯性——**irritation 持续效应的数学基础**
- 二阶动力学引入 hysteresis（滞后）——被骚扰后即使停止骚扰，irritation 仍持续一段时间

**开源代码**：github.com/drsukeshs/agent-behavior-ext-dynamics

### 3.2 Hormones/Circadian（arxiv 2508.11829，2025）

将昼夜节律建模为周期函数：

- 皮质醇曲线：早晨高（乐观）→ 夜晚低（内省）
- 能量曲线：午后低谷、深夜低谷
- 直接通过 system prompt 注入当前"生理状态"

**对 omubot 的启示**：omubot 的 `_MOOD_BASE` + 时间修正已经是这个思路的简化版。可以升级为连续周期函数而非离散 if-else。

### 3.3 Sentipolis（arxiv 2601.18027，2026）

**双速情绪动力学**：

- 快速反应层：对事件即时响应（高 α）
- 慢速 mood 漂移层：缓慢跟随快速层（低 α）+ 自然衰减
- 情绪-记忆耦合：事件存储时附带 PAD 情绪标签，回忆时重新激活对应情绪

### 3.4 CTEM / Auri（arxiv 2605.15812，2026，CHI，Microsoft Research）

**完整闭环框架**：

```
行为历史 → 更新情绪状态 S_t → 条件化当前交互 → 用户反馈 → 修正记忆 + 情绪
```

- 每个时间步从预定义行为类别生成行为清单
- 基于当前情绪状态选择一个行为执行
- 执行结果 + 用户反馈同时更新状态和清单
- 21 天 96 人部署验证

### 3.5 Dynamic Personality State Machines（arxiv 2602.22157，2026）

- 人格维度作为独立状态轴，各有独立转移概率
- 对话上下文动态调整转移概率
- 双向传染：用户人格状态也影响 bot 转移概率

### 3.6 FiSMiness（arxiv 2504.11837，2025）

- 将情感支持对话建模为有限状态机
- 每 turn：LLM 自生成对方当前情绪 → 确定支持策略 → 生成回复
- 超越 chain-of-thought 和 self-refine baseline

---

## 四、对比矩阵

| 维度 | BoTTube | EchoText | Personaut | Koishi | GLaDOS | lacuna | omubot 现状 | omubot 需求 |
|---|---|---|---|---|---|---|---|---|
| 状态模型 | 离散 FSM | 连续 Plutchik | 连续 + Markov | 单值 | PAD 双层 | 三参数体感 | 离散标签（无转移） | 连续 + 双层 |
| 时间集成 | 乘数表 | 指数衰减 | compound decay | 日重置 | LLM 推理 | 昼夜节律 | if-else 修正 | 周期函数 + 乘数 |
| 交互反馈 | 信号聚合 | 关键词 + 基线漂移 | 人格调制 | 每次消耗 | LLM 推理 | 传染 + 惯性 | 仅计数（±0.05） | 多维信号 + 惯性 |
| 衰减 | intensity 0.1/h | per-emotion λ | compound + 基线回归 | 日重置 | baseline drift | 自然衰减 | 无（缓存 15min） | 差异化 λ |
| 持久化 | SQLite | 内存 + 基线 | 内存 + 基线 | 数据库 | 内存 | 内存 | 无 | 日级持久化 |
| 输出耦合 | style params | 行为指导 XML | NL 描述 + mask | prompt 切换 | subagent summary | prompt 注入 | mood_block prompt | 行为指导 + delay |

---

## 五、Part 8 架构设计方向

### 5.1 核心洞察

从所有项目和论文中提炼的共识：

1. **双层分离是最优架构**——快速反应层（turn-level）+ 慢速 mood 层（session/day-level）
2. **基线漂移 + 回归中性**——重复模式改变 resting state，但有自然回归防止极端化
3. **差异化衰减**——不同情绪维度衰减速率不同（irritation 快衰减，trust 慢衰减）
4. **惯性/动量**——防止单条消息剧烈改变 mood，需要持续刺激
5. **时间作为乘数而非硬切换**——周期函数调制转移概率
6. **行为指导优于标签注入**——告诉 LLM "怎么表现"而非"你是什么情绪"

### 5.2 omubot 已有基础设施映射

```
[已有，可复用]
├── MoodEngine（慢速层骨架）→ 升级为双层 mood 的慢速层
├── MoodClassifier（快速层骨架）→ 激活为双层 mood 的快速层
├── RuntimeStateBus（状态总线）→ 桥接两层的通信通道
├── on_post_reply 钩子机制（affection plugin 已验证）→ 反馈写入点
├── ScheduleStore（日程持久化）→ mood 持久化可复用同模式
├── build_mood_block()（prompt 注入）→ 升级为行为指导注入
├── _humanizer_runtime()（delay 调制）→ 已消费 mood，无需改
└── coupling policy（sticker/reply 决策）→ 已消费 mood label，无需改

[需新建]
├── MoodFeedbackEngine（反馈引擎）→ on_post_reply 写入信号
├── MoodDynamics（动力学计算）→ 指数平滑 + 动量 + 衰减
├── MoodPersistence（日级持久化）→ 基线漂移存盘
└── MoodBridge（桥接层）→ 快速层 → 慢速层信号传递
```

### 5.3 与日程系统的深度捆绑潜力

当前日程系统的 `mood_hint` 是**单向的**（日程 → mood）。Part 8 可以实现**双向迭代**：

```
[日程 → mood]（已有）
  schedule slot "排练" + mood_hint "专注"
  → MoodEngine 基础 profile: energy=0.7, openness=0.4

[交互 → mood]（Part 8 新增）
  被连续 @ 5 次 + 用户语气敌意
  → MoodFeedbackEngine 注入 irritation signal
  → 动力学计算：energy -0.2, valence -0.3, tension +0.4
  → 慢速层更新：从"专注"漂移到"烦躁"

[mood → 回复]（已有，自动生效）
  build_mood_block() 输出变化：
  - 之前："专注状态，回复简洁但友好"
  - 之后："被打扰后有些烦躁，回复更短更冷淡"

[mood → 日程]（Part 8 新增，高级）
  如果 irritation 持续 > 30 分钟且当前 slot 是"排练"
  → 下一个 slot 的 mood_hint 从"放松"修正为"疲惫"
  → 形成"被骚扰→排练时心情差→排练后更累"的因果链
```

### 5.4 与 Issue 17 burst 问题的关系

Issue 17 的三个根因在 Part 8 框架下自然解决：

| Issue 17 根因 | Part 8 解法 |
|---|---|
| A. re-fire 零延迟 | mood tension 高 → humanizer delay 乘数增大 → 自然变慢 |
| B. 重复回复 | mood 从"专注"漂移到"烦躁" → build_mood_block 输出"不想重复回答" |
| C. 情绪不变 | MoodFeedbackEngine 的 irritation signal 直接改变 mood |

Issue 17 的 inter-fire cooldown 可以作为 Part 8 的**最小可行子集**先行落地。

---

## 六、推荐实现路径

### Phase 1（与 Issue 17 同批，~200 行）

- 激活 MoodClassifier → 接入 RuntimeStateBus
- 新建 MoodFeedbackEngine（on_post_reply 钩子）
- 实现 irritation signal：@ 频率 → tension/valence 修正
- MoodEngine 读取 RuntimeStateBus 上的 feedback signal 作为额外输入

### Phase 2（Part 8 独立 PR，~400 行）

- 引入 MoodDynamics：指数平滑 + 动量（二阶，参考 2601.16087）
- 差异化衰减率（irritation λ=0.12, trust λ=0.035）
- 惯性滤波（防止单条消息剧变）
- 基线漂移 + 回归中性（参考 Personaut PDK）

### Phase 3（Part 8 进阶，~300 行）

- mood → 日程反向修正（当前 slot 的 mood_hint 可被运行时覆盖）
- 日级持久化（基线存盘，跨重启保留）
- 粉红噪声替代均匀随机（参考 lacuna_core）
- per-user 亲密度调节惯性（参考 lacuna_core + EchoText affinity shift）

---

## 七、引证索引

### 论文

| # | 标题 | ID | 年份 | 核心贡献 |
|---|---|---|---|---|
| 1 | Controlling Long-Horizon Behavior with Explicit State Dynamics | arxiv 2601.16087 | 2026 | VAD + 指数平滑 + 动量，有开源代码 |
| 2 | Hormones and Emotions Scaffolding | arxiv 2508.11829 | 2025 | 昼夜节律周期函数建模 |
| 3 | Sentipolis: Emotion-Aware Agents | arxiv 2601.18027 | 2026 | 双速动力学 + 情绪-记忆耦合 |
| 4 | CTEM / Auri (Cross-Temporal Emotional Modeling) | arxiv 2605.15812 | 2026 | 完整闭环：历史→状态→交互→反馈→更新 |
| 5 | Dynamic Personality via State Machines | arxiv 2602.22157 | 2026 | 人格轴独立状态机 + 双向传染 |
| 6 | FiSMiness (FSM for Emotional Support) | arxiv 2504.11837 | 2025 | ESC 建模为 FSM，超越 CoT |
| 7 | Heartbeat-Driven Activity Scheduling | arxiv 2604.14178 | 2026 | 周期心跳驱动认知模块调度 |
| 8 | SAGE: State-Action Chain | NLPerspectives@ACL 2025 | 2025 | 状态-动作链 + 对话树搜索 |
| 9 | DialogXpert: Emotion-Tracking RL | arxiv 2505.17795 | 2025 | 情绪追踪 Q-network + LLM prior |
| 10 | Proactive Agents with Inner Thoughts | arxiv 2501.00383 | 2025 | 内在动机驱动主动发言 |
| 11 | Emotion-Sensitive Dialogue Policy | Nature Sci Rep 2024 | 2024 | 用户情绪作为即时奖励 |
| 12 | Affective Computing in LLM Era (Survey) | arxiv 2408.04638 | 2024 | 综述：RL 情感系统缺乏长期动态建模 |

### 开源项目

| # | 项目 | 核心文件 | 核心机制 |
|---|---|---|---|
| 1 | BoTTube | `mood_engine.py` | 离散 FSM + 时间乘数 + 信号聚合 |
| 2 | SillyTavern-EchoText | `lib/emotion-system.js` | 连续 Plutchik + 差异化衰减 + 基线漂移 |
| 3 | Personaut PDK | `src/personaut/emotions/state.py` | 36 情绪 + Markov + 人格调制 + compound decay |
| 4 | Koishi satori-ai | `src/mood.ts` | 单值消耗模型（每次交互 drain） |
| 5 | GLaDOS | `emotion_agent.py` | PAD 双层 + LLM 推理转移 |
| 6 | lacuna_core | `core/engine.py` | 体感三参数 + 惯性 + 粉红噪声 + 昼夜节律 |
| 7 | OpenFeelz | — | OCEAN 人格 + PAD 情绪演化 |
| 8 | OpenCode Personality | — | mood drift + per-personality tracking |
