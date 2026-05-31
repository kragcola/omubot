# Issue 17 研究附件 B — 对话气候系统（Dialogue Climate）统筹调研

> 状态：2026-05-27 调研完成，待方案设计
>
> 来源：用户指示——"同记忆、黑话、表达等能统一划分至学习管道系列一样，日程、心情、好感度、日期上下文也可以归到一个新的系统"
>
> 方法：omubot 内部全量代码拆解 + 学习管道架构对比 + 开源项目统筹模式分析

---

## 一、命名：Dialogue Climate（对话气候）

### 为什么叫"气候"

| 类比 | 学习管道（Learning Pipeline） | 对话气候（Dialogue Climate） |
|---|---|---|
| 隐喻 | 管道——数据流入、加工、沉淀 | 气候——多因子叠加形成当下"天气" |
| 时间尺度 | 长期积累（记忆/黑话/风格越用越准） | 短中期波动（心情/好感/日程随时变） |
| 影响方式 | 改变 bot "知道什么" | 改变 bot "当下怎么表现" |
| 核心动词 | 学习、记忆、沉淀 | 感知、漂移、衰减 |
| 输出形态 | 知识块注入 prompt | 行为指导 + 参数调制 |

"气候"精确捕捉了这组系统的本质：

1. **多因子叠加**——真实气候由温度、湿度、气压、风速共同决定；对话气候由心情、好感、日程、时间共同决定
2. **有惯性**——气候不会因一阵风就变天；bot 心情不会因一条消息就翻转
3. **有周期**——昼夜、季节；日程 slot、工作日/休息日
4. **有基线漂移**——全球变暖；长期被骚扰→默认更冷淡
5. **可预报但不可精确控制**——日程给出"预报"，实际运行时有偏差

### 英文标识

- 模块前缀：`dialogue_climate` / `climate`
- RuntimeStateBus namespace：`climate.*`
- 配置段：`[dialogue_climate]`

---

## 二、现状拆解——六个独立系统

### 2.1 系统清单

| # | 系统 | 位置 | 写入点 | 读取点 | 影响维度 |
|---|---|---|---|---|---|
| 1 | MoodEngine | `plugins/schedule/mood.py:112` | 内存缓存（15min TTL） | SchedulePlugin.on_pre_prompt | prompt 注入（行为指导） |
| 2 | MoodClassifier | `services/humanization/mood_classifier.py:49` | `MOOD_CURRENT_SLOT` on RuntimeStateBus | **无生产消费者** | 死代码 |
| 3 | AffectionEngine | `plugins/affection/engine.py:21` | `AFFECTION_FAMILIARITY_SLOT` on RuntimeStateBus + 文件持久化 | AffectionPlugin.on_pre_prompt | prompt 注入（关系描述） |
| 4 | CalendarContextService | `plugins/calendar_context/service.py` | `ctx.calendar_service`（无 Bus 写入） | MoodEngine.get_day_context() | mood 修正 + prompt 注入 |
| 5 | RuntimeClock | `services/humanization/contract.py` | `CLOCK_CURRENT_SLOT` on RuntimeStateBus | Humanizer._runtime_multiplier | delay 调制 |
| 6 | CouplingPolicy | `services/humanization/coupling.py:33` | 纯函数（无状态） | **无生产调用者** | 设计时蓝图，未接线 |

### 2.2 数据流现状图

```
┌─────────────────────────────────────────────────────────────────────┐
│                        当前架构（无统筹）                              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  [日程 YAML]──→ MoodEngine._compute() ──→ 内存缓存 ──→ prompt 注入  │
│                      ↑                                              │
│  [CalendarCtx]───────┘  (get_day_context)                           │
│                                                                     │
│  [用户消息]──→ MoodClassifier.classify() ──→ MOOD_CURRENT_SLOT      │
│                                               ↓                     │
│                                          (无消费者)                   │
│                                                                     │
│  [交互事件]──→ AffectionEngine.record() ──→ AFFECTION_FAMILIARITY   │
│                                               ↓                     │
│                                          AffectionPlugin prompt 注入 │
│                                                                     │
│  [时间]──→ CLOCK_CURRENT_SLOT ──→ Humanizer delay                   │
│                                                                     │
│  [CouplingPolicy] ──→ (未接线，仅测试)                                │
│                                                                     │
│  ──────── 问题 ────────                                              │
│  ① MoodEngine 和 MoodClassifier 互不通信                             │
│  ② Affection 不影响 delay，Mood 不影响 affection                     │
│  ③ CalendarContext 只被 MoodEngine 单向读取                           │
│  ④ CouplingPolicy 是唯一的统筹点但未接线                              │
│  ⑤ 各系统独立注入 prompt，无一致性保证                                 │
│  ⑥ 无 on_post_reply 反馈回路（mood 不因对话内容变化）                  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.3 各系统详细拆解

#### MoodEngine（日程驱动心情）

```python
# plugins/schedule/mood.py:170-248
def _compute(self, group_id, session_id) -> MoodProfile:
    ① _lookup_base(slot.mood_hint)  # 日程 → 基础 4 维 profile
    ② ±0.15 随机扰动
    ③ 时间修正（深夜 energy -0.15，午后 -0.05）
    ④ 交互量修正（0条 → openness +0.1；>5条 → energy -0.05）
    ⑤ 日历修正（节假日、生日）
    ⑥ 20% 概率异常翻转
    ⑦ clamp → MoodProfile(energy, valence, openness, tension, label)
```

- **输出维度**：energy / valence / openness / tension / label（中文标签）
- **缓存**：15 分钟 TTL，期间静态
- **消费者**：`build_mood_block()` → prompt 注入；`Humanizer._runtime_multiplier()` 读 energy

#### MoodClassifier（消息信号心情）

```python
# services/humanization/mood_classifier.py:104-113
def _transition(signals) -> (label, confidence, reason):
    short_reply + no_particles → "cold"
    slow_reply → "tired"
    high_sticker → "playful"
    high_particles → "high"
    else → "neutral"
```

- **输出维度**：label（英文 5 级）+ confidence + reason
- **写入**：`MOOD_CURRENT_SLOT` on RuntimeStateBus（TTL 300s）
- **消费者**：**无**（死代码——无生产代码实例化 MoodClassifier）

#### AffectionEngine（好感度）

```python
# plugins/affection/engine.py:75-101
familiarity = long_term * 0.7 + daily * 0.3
# long_term = score / 100（累计分）
# daily = daily_count / 10（当日交互次数）
# 写入 AFFECTION_FAMILIARITY_SLOT：{user_id, familiarity, score, tier, daily_count, mood_bonus_valence}
```

- **输出维度**：familiarity（0-1）/ tier（stranger/acquaintance/friend/close）/ mood_bonus_valence
- **持久化**：文件级（跨重启保留）
- **消费者**：AffectionPlugin.on_pre_prompt → prompt 注入关系描述

#### CalendarContextService（日历上下文）

- **输出**：`DayContext(is_holiday, is_workday, is_makeup, holiday_name, special_days, birthdays)`
- **消费者**：MoodEngine._compute() 步骤⑤；SchedulePlugin prompt 注入
- **无 Bus 写入**——通过 `ctx.calendar_service` 直接方法调用

#### CouplingPolicy（统筹蓝图）

```python
# services/humanization/coupling.py:12-22
@dataclass
class CouplingFeatures:
    mood_label: str
    register_label: str
    affection_stage: str
    addressee_self: bool
    topic_drift_score: float
    topic_is_new: bool

@dataclass
class CouplingPolicy:
    reply_bias: str          # suppress / short / elaborate / continue_old_topic
    register_label: str      # override
    max_segments: int
    typing_multiplier: float
    delay_multiplier: float
    sticker_probability: float
    sticker_multiplier: float
    reasons: list[str]
```

- **设计意图**：读取 mood + register + affection + topic 信号，输出统一行为策略
- **现状**：仅测试调用，**无生产接线**

---

## 三、学习管道对比——为什么需要统筹

### 3.1 学习管道的成功模式

omubot 的"学习管道"（memo / slang / style）虽然也是独立插件，但有以下统筹特征：

| 特征 | 学习管道实现 | 对话气候现状 |
|---|---|---|
| 共享存储模式 | 各有独立 Store（CardStore / SlangStore / StyleStore） | 各有独立存储（内存缓存 / Bus slot / 文件） |
| 统一注入接口 | `ctx.add_block(position, priority, source)` | ✅ 同模式（schedule pri=10, affection pri=20） |
| ProviderBus 超越 | slang/style 检查 `provider_bus.has_provider()` 让位 | ❌ 无——各自独立注入，无超越机制 |
| 反馈回路 | memo: on_post_reply 提取；style: on_post_reply 记录 | ❌ mood 无 on_post_reply；affection 有但不影响 mood |
| 跨系统读取 | 极少（各自独立） | ❌ 极少（CalendarCtx→Mood 单向；其余无） |
| 统一消费者 | PromptProviderBus 统一 trace + 注入 | ❌ 无——Humanizer 读 mood，CouplingPolicy 未接线 |

### 3.2 对话气候的统筹缺口

学习管道的"统筹"是**松耦合**的——各插件独立工作，通过 PromptProviderBus 统一出口。这对"知识积累"类系统足够。

但对话气候需要**紧耦合**——因为各因子之间有物理因果关系：

```
好感度高 → 心情更容易被用户影响（惯性低）
心情差 → 回复冷淡 → 好感度可能下降（如果用户感到被冷落）
日程"排练" → 心情"专注" → 被打扰 → 心情"烦躁" → 下一 slot 心情"疲惫"
节假日 → 心情基线上移 → 好感度增长更快（节日互动更有价值）
```

学习管道不需要这种因果链——记忆不会因为黑话多了就变少。但对话气候的各因子天然耦合。

---

## 四、目标架构——Dialogue Climate Runtime

### 4.1 核心设计原则

从 Issue 17 研究附件 A 的开源项目和论文中提炼：

1. **双层分离**：快速反应层（turn-level）+ 慢速漂移层（session/day-level）
2. **统一状态空间**：所有因子写入同一个 `ClimateState`，而非散落各处
3. **单一消费出口**：一个 `ClimateProvider` 读取 `ClimateState` 产出统一行为策略
4. **反馈闭环**：on_post_reply 写回信号，形成 感知→决策→行为→反馈 循环
5. **差异化动力学**：各维度有独立的衰减率、惯性、基线

### 4.2 架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                   Dialogue Climate Runtime（目标架构）                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────── 信号源（Sensors）──────────────────┐                   │
│  │                                                   │                   │
│  │  [日程 slot]  → ScheduleSensor                    │                   │
│  │  [日历]       → CalendarSensor                    │                   │
│  │  [用户消息]   → MessageSensor (原 MoodClassifier) │                   │
│  │  [交互事件]   → InteractionSensor (原 Affection)  │                   │
│  │  [时间]       → CircadianSensor                   │                   │
│  │  [@ 频率]     → IrritationSensor (Issue 17 新增)  │                   │
│  │                                                   │                   │
│  └───────────────────────┬───────────────────────────┘                   │
│                          │ ClimateSignal[]                                │
│                          ▼                                                │
│  ┌─────────────── 动力学引擎（Dynamics）─────────────┐                   │
│  │                                                   │                   │
│  │  ClimateState {                                   │                   │
│  │    energy:    f32  (0-1, 衰减λ=0.08)              │                   │
│  │    valence:   f32  (0-1, 衰减λ=0.06)              │                   │
│  │    openness:  f32  (0-1, 衰减λ=0.05)              │                   │
│  │    tension:   f32  (0-1, 衰减λ=0.12)  ← 快衰减   │                   │
│  │    trust:     f32  (0-1, 衰减λ=0.02)  ← 慢衰减   │                   │
│  │    familiarity: f32 (per-user, 衰减λ=0.01)        │                   │
│  │  }                                                │                   │
│  │                                                   │                   │
│  │  动力学规则：                                      │                   │
│  │  ① 指数平滑：S_t = α·signal + (1-α)·S_{t-1}     │                   │
│  │  ② 动量惯性：+ β·ΔS_{t-1}                        │                   │
│  │  ③ 差异化衰减：各维度独立 λ 向 baseline 衰减      │                   │
│  │  ④ 基线漂移：baseline 以 0.01/day 向当前均值漂移  │                   │
│  │  ⑤ 基线回归：baseline 以 0.003/day 向 neutral 回归│                   │
│  │  ⑥ 惯性滤波：familiarity 调节 α（熟人反应快）     │                   │
│  │                                                   │                   │
│  └───────────────────────┬───────────────────────────┘                   │
│                          │ ClimateState                                   │
│                          ▼                                                │
│  ┌─────────────── 策略合成（Policy）─────────────────┐                   │
│  │                                                   │                   │
│  │  ClimatePolicy = f(ClimateState, context)         │                   │
│  │  {                                                │                   │
│  │    mood_label: str        → prompt 行为指导        │                   │
│  │    reply_bias: str        → suppress/short/full   │                   │
│  │    delay_multiplier: f32  → Humanizer 消费        │                   │
│  │    openness_hint: str     → prompt 开放度指导      │                   │
│  │    sticker_prob: f32      → sticker 决策          │                   │
│  │    register_override: str → 语域覆盖              │                   │
│  │  }                                                │                   │
│  │                                                   │                   │
│  │  ≈ CouplingPolicy 的升级版（从纯函数→状态驱动）    │                   │
│  │                                                   │                   │
│  └───────────────────────┬───────────────────────────┘                   │
│                          │ ClimatePolicy                                  │
│                          ▼                                                │
│  ┌─────────────── 输出适配（Adapters）───────────────┐                   │
│  │                                                   │                   │
│  │  PromptAdapter:                                   │                   │
│  │    → ctx.add_block("当前状态", position="dynamic") │                   │
│  │    → 替代 SchedulePlugin + AffectionPlugin 的独立注入│                 │
│  │                                                   │                   │
│  │  HumanizerAdapter:                                │                   │
│  │    → 提供 delay_multiplier 给 Humanizer           │                   │
│  │    → 替代 _runtime_multiplier 的 ad-hoc 读取      │                   │
│  │                                                   │                   │
│  │  ThinkerAdapter:                                  │                   │
│  │    → 提供 reply_bias 给 Thinker 决策              │                   │
│  │    → 替代 _get_mood_multiplier 的 talk_value 调制 │                   │
│  │                                                   │                   │
│  └───────────────────────────────────────────────────┘                   │
│                                                                         │
│  ┌─────────────── 反馈回路（Feedback）───────────────┐                   │
│  │                                                   │                   │
│  │  on_post_reply:                                   │                   │
│  │    → 记录本次回复的 register / length / latency   │                   │
│  │    → 注入 interaction signal 到 Dynamics          │                   │
│  │    → 更新 familiarity（原 AffectionEngine 逻辑）  │                   │
│  │                                                   │                   │
│  │  on_message（被动观察）:                           │                   │
│  │    → @ 频率统计 → IrritationSensor                │                   │
│  │    → 用户语气分析 → MessageSensor                 │                   │
│  │                                                   │                   │
│  └───────────────────────────────────────────────────┘                   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 4.3 与学习管道的对称性

```
学习管道 (Learning Pipeline)          对话气候 (Dialogue Climate)
─────────────────────────────────    ─────────────────────────────────
输入：用户消息 + bot 回复              输入：时间 + 日程 + 交互事件 + 消息信号
加工：提取 → 验证 → 存储              加工：感知 → 动力学 → 策略合成
输出：知识块注入 prompt                输出：行为策略注入 prompt + 参数调制
存储：CardStore / SlangStore / StyleStore  存储：ClimateState（内存）+ Baseline（持久化）
反馈：on_post_reply 提取新知识         反馈：on_post_reply 注入交互信号
时间尺度：天/周/月（越用越准）          时间尺度：分钟/小时/天（实时波动）
```

---

## 五、迁移路径——从现状到目标

### 5.1 渐进式迁移（不破坏现有功能）

关键原则：**不一次性重写**。现有 6 个系统都在工作（或已实现待接线），迁移应该是"加桥梁"而非"拆重建"。

#### Phase 0 — 接线已有死代码（~50 行，与 Issue 17 同批）

| 动作 | 效果 |
|---|---|
| 实例化 MoodClassifier，挂 on_post_reply | 快速层激活 |
| CouplingPolicy 接入 Thinker 决策路径 | 统筹蓝图生效 |
| MoodEngine 读取 MOOD_CURRENT_SLOT 作为额外输入 | 双层桥接 |

这一步**不新建任何模块**，只是把已有代码接线。

#### Phase 1 — ClimateState 统一状态（~150 行）

```python
# services/dialogue_climate/state.py
@dataclass
class ClimateState:
    energy: float = 0.5
    valence: float = 0.5
    openness: float = 0.5
    tension: float = 0.0
    trust: float = 0.5       # per-user
    familiarity: float = 0.0  # per-user（从 AffectionEngine 迁入）

    # 基线（慢速漂移）
    baseline_energy: float = 0.5
    baseline_valence: float = 0.5
    baseline_openness: float = 0.5

    # 元数据
    last_update_ts: float = 0.0
    update_count: int = 0
```

- 写入 RuntimeStateBus 新 slot `CLIMATE_STATE_SLOT`
- MoodEngine 的 `MoodProfile` 和 AffectionEngine 的 `familiarity` 都映射到这个统一状态
- 现有消费者（Humanizer、SchedulePlugin prompt）通过适配层读取，无需改动

#### Phase 2 — Dynamics 引擎（~200 行，Part 8 核心）

```python
# services/dialogue_climate/dynamics.py
class ClimateDynamics:
    """Exponential smoothing + momentum + differential decay."""

    DECAY_RATES = {
        "energy": 0.08,
        "valence": 0.06,
        "openness": 0.05,
        "tension": 0.12,   # 快衰减——irritation 消散快
        "trust": 0.02,     # 慢衰减——信任建立慢、破坏也慢
    }

    def update(self, state: ClimateState, signals: list[ClimateSignal]) -> ClimateState:
        # ① 应用信号（指数平滑 + 动量）
        # ② 应用衰减（各维度独立 λ）
        # ③ 应用基线漂移
        # ④ 应用惯性滤波（familiarity 调节 α）
        ...
```

- 参考 arxiv 2601.16087 的二阶动力学
- 参考 EchoText 的差异化衰减率
- 参考 lacuna_core 的惯性滤波

#### Phase 3 — Sensor 适配 + 反馈回路（~200 行）

将现有系统包装为 Sensor 接口：

```python
class ScheduleSensor:
    """Wraps MoodEngine._compute() as a ClimateSignal source."""
    def sense(self, ctx) -> list[ClimateSignal]: ...

class InteractionSensor:
    """Wraps AffectionEngine.record_interaction() as a signal."""
    def sense(self, ctx) -> list[ClimateSignal]: ...

class IrritationSensor:
    """Issue 17 新增：@ 频率 → tension signal."""
    def sense(self, ctx) -> list[ClimateSignal]: ...
```

- 现有 MoodEngine / AffectionEngine / CalendarContextService **不删除**
- Sensor 是适配层，读取现有系统的输出，转换为统一 `ClimateSignal`
- 反馈回路通过 on_post_reply 钩子注入 InteractionSignal

#### Phase 4 — Policy 合成 + Adapter 输出（~150 行）

```python
class ClimatePolicy:
    """Replaces ad-hoc mood reading with unified policy output."""

    def synthesize(self, state: ClimateState, context: PolicyContext) -> PolicyOutput:
        # 升级版 CouplingPolicy
        # 读取统一 ClimateState 而非散落各处的 slot
        ...

class PromptAdapter:
    """Single prompt block replacing schedule+affection independent injection."""
    def build_block(self, policy: PolicyOutput) -> str: ...
```

- PromptAdapter 产出**一个**统一 prompt block，替代 SchedulePlugin（pri=10）+ AffectionPlugin（pri=20）的两个独立 block
- HumanizerAdapter 提供 `delay_multiplier`，替代 `_runtime_multiplier` 的 ad-hoc 读取
- 现有 SchedulePlugin / AffectionPlugin 的 `on_pre_prompt` 检查 `provider_bus.has_provider("climate")` 后让位（同 slang/style 模式）

### 5.2 迁移兼容性保证

| 现有消费者 | 迁移策略 |
|---|---|
| `Humanizer._runtime_multiplier()` | Phase 4 前：不动。Phase 4 后：读 `ClimatePolicy.delay_multiplier` |
| `SchedulePlugin.on_pre_prompt` | Phase 4 前：不动。Phase 4 后：检查 provider_bus 让位 |
| `AffectionPlugin.on_pre_prompt` | 同上 |
| `_get_mood_multiplier()` in scheduler | Phase 4 前：不动。Phase 4 后：读 `ClimatePolicy.reply_bias` |
| `build_mood_block()` | Phase 2 前：不动。Phase 2 后：由 PromptAdapter 替代 |

每个 Phase 都是**增量叠加**，不删除现有代码直到新路径验证通过。

---

## 六、与 Issue 17 的关系

Issue 17 的三层修复自然映射到 Dialogue Climate 架构：

| Issue 17 层 | Climate 对应 |
|---|---|
| 第 1 层：inter-fire cooldown | HumanizerAdapter 读 `tension` → delay_multiplier 增大 |
| 第 2 层：burst dedup hint | PromptAdapter 在 `tension > 0.5` 时注入"已被连续打扰"行为指导 |
| 第 3 层：mood irritation feedback | IrritationSensor → Dynamics → tension 上升 |

**推荐执行顺序**：

1. Issue 17 第 1 层（cooldown）独立做——纯 scheduler 改动，不依赖 Climate
2. Phase 0（接线死代码）与 Issue 17 第 3 层同批——激活 MoodClassifier + 桥接
3. Phase 1-2（ClimateState + Dynamics）作为 Part 8 独立 PR
4. Phase 3-4（Sensor + Policy）作为 Part 8 后续 PR

---

## 七、开源项目中的统筹模式参考

### 7.1 GLaDOS — 双层 + 单一消费出口

GLaDOS 的 `emotion_agent.py` 实现了最接近 Dialogue Climate 的模式：

- **State 层**（快速）：对事件即时反应
- **Mood 层**（慢速）：缓慢向 State 漂移
- **单一出口**：`_apply_baseline_drift()` + personality prompt → LLM

omubot 映射：MoodClassifier = State 层；MoodEngine = Mood 层；ClimatePolicy = 单一出口

### 7.2 lacuna_core — 多参数体感 + 参数交互

lacuna_core 的三参数（mood / stress / heart_rate）之间有**交互规则**：

```python
# stress 高 → mood 下降
# heart_rate 高 + mood 低 → stress 进一步上升（正反馈环）
# 亲密度高 → 惯性低 → 情绪反应更敏感
```

omubot 映射：tension 高 → valence 下降；familiarity 高 → α 增大（反应更快）

### 7.3 Personaut PDK — 基线漂移 + 回归中性

```python
def update_mood_baseline(self):
    # 基线以 10% learning rate 向当前状态漂移
    # 基线以 3%/turn 向 neutral 回归
```

omubot 映射：`baseline_energy` 以 0.01/day 向当前均值漂移 + 以 0.003/day 向 0.5 回归

### 7.4 CTEM / Auri（CHI 2026）— 完整闭环

```
行为历史 → 更新情绪状态 → 条件化当前交互 → 用户反馈 → 修正记忆 + 情绪
```

omubot 映射：on_post_reply → Sensor → Dynamics → Policy → Adapter → on_pre_prompt

---

## 八、与学习管道的组织对称

### 8.1 omubot 子系统全景

```
omubot 核心子系统
├── 学习管道 (Learning Pipeline) — "bot 知道什么"
│   ├── memo    — 记忆（事实、偏好、历史）
│   ├── slang   — 黑话（群内用语、梗）
│   ├── style   — 表达（语气、句式、习惯）
│   └── context — 知识图谱（实体关系）
│
├── 对话气候 (Dialogue Climate) — "bot 当下怎么表现"
│   ├── schedule  — 日程（活动、时间框架）
│   ├── mood      — 心情（能量、情绪、开放度）
│   ├── affection — 好感（亲密度、信任、关系阶段）
│   ├── calendar  — 日历（节假日、生日、特殊日）
│   └── circadian — 昼夜节律（时间→生理状态映射）
│
├── 拟人化运行时 (Humanization Runtime) — "bot 怎么发消息"
│   ├── humanizer — 延迟模拟（打字速度、思考时间）
│   ├── register  — 语域选择（正式/随意/亲密）
│   ├── coupling  — 耦合策略（回复/忽略/简短/详细）
│   └── sticker   — 表情决策（何时发、发什么）
│
└── 人设运行时 (Persona Runtime) — "bot 是谁"
    ├── compiler  — 人设编译（source.md → prompt blocks）
    ├── selector  — 运行时选择（v1/v2 切流）
    └── shadow    — 影子比对（v1 vs v2 parity）
```

### 8.2 子系统间的数据流

```
Persona Runtime → 提供身份锚点（性格、说话方式基线）
       ↓
Learning Pipeline → 提供知识上下文（记忆、黑话、风格）
       ↓
Dialogue Climate → 提供当下状态（心情、好感、时间感）
       ↓
Humanization Runtime → 执行最终行为（延迟、语域、耦合）
       ↓
LLM → 生成回复
       ↓
Feedback → 回流到 Learning Pipeline + Dialogue Climate
```

---

## 九、配置设计

```json
{
  "dialogue_climate": {
    "enabled": true,
    "dynamics": {
      "alpha": 0.2,
      "beta": 0.85,
      "decay_rates": {
        "energy": 0.08,
        "valence": 0.06,
        "openness": 0.05,
        "tension": 0.12,
        "trust": 0.02
      },
      "baseline_drift_rate": 0.01,
      "baseline_regression_rate": 0.003,
      "inertia_base": 0.7,
      "inertia_familiarity_factor": 0.3
    },
    "sensors": {
      "schedule": true,
      "calendar": true,
      "message": true,
      "interaction": true,
      "irritation": true,
      "circadian": true
    },
    "persistence": {
      "baseline_file": "storage/climate_baseline.json",
      "save_interval_s": 3600
    },
    "policy": {
      "suppress_threshold_tension": 0.8,
      "short_reply_threshold_energy": 0.3,
      "elaborate_threshold_openness": 0.7
    }
  }
}
```

---

## 十、成本估算与排期建议

| Phase | 行数 | 依赖 | 建议排期 |
|---|---|---|---|
| Phase 0（接线死代码） | ~50 | 无 | 与 Issue 17 同批 |
| Phase 1（ClimateState） | ~150 | Phase 0 | Part 8 PR #1 |
| Phase 2（Dynamics） | ~200 | Phase 1 | Part 8 PR #1 |
| Phase 3（Sensors + Feedback） | ~200 | Phase 2 | Part 8 PR #2 |
| Phase 4（Policy + Adapters） | ~150 | Phase 3 | Part 8 PR #2 |
| **总计** | **~750** | | **2 个 PR** |

与 Issue 17 研究附件 A 的 Phase 1/2/3 对应关系：

- 附件 A Phase 1（激活 + 反馈引擎）≈ 本文 Phase 0 + Phase 3 的 IrritationSensor
- 附件 A Phase 2（动力学）≈ 本文 Phase 2
- 附件 A Phase 3（持久化 + 日程反向）≈ 本文 Phase 4 的高级功能

---

## 十一、决策模板

```text
Dialogue Climate 统筹方案：
[ ] 接受命名"对话气候 / Dialogue Climate"
[ ] 其他命名建议：___

执行路径：
[ ] 按 Phase 0-4 渐进迁移（推荐）
[ ] 只做 Phase 0（接线死代码）+ Issue 17 三层修复，Part 8 后议
[ ] 一次性重写（不推荐，风险高）
[ ] 暂不做，观察 Issue 17 修复效果

与 Issue 17 的关系：
[ ] Issue 17 第 1 层独立做，Phase 0 同批做第 3 层
[ ] Issue 17 全部独立做，Climate 作为后续独立项目
[ ] 其他：___
```
