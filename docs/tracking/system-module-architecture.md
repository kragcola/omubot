# SystemModule / RuntimeStateBus 架构方案

> 状态：从 Persona Source Importer §16 拆出（2026-05-24）
> 归属：Part B · Runtime/SystemModule
> 上游：[persona-source-importer.md](./persona-source-importer.md)
> 执行追踪：[persona-source-importer-remediation-execution.md](./persona-source-importer-remediation-execution.md)

---

## 0. 文档定位

本文承接原 `Persona Source Importer` 方案中的 §16，用于描述长期 runtime / SystemModule / RuntimeStateBus / SwitchMatrix / DAG / compiler 架构。

本文不是 importer 首版交付合同。Importer Part A 的边界是：`source.md -> v2 draft + _import_report.json + CLI`，不依赖本文的 RuntimeStateBus、26 个模块实现或 compiler。

双向接口：

- Importer 输出 v2.1 draft skeleton、默认模板和 import report。
- 本文定义未来 runtime/compiler 如何消费这些 draft，并如何把状态、模块和 trace 接入主链路。

---

## 16. 系统模块体系（v2 不考虑兼容的清洁架构）

> 目的：把"会影响回复的所有插件 / 状态机 / 学习模块"统一为单一抽象 **SystemModule**，每个模块声明**与人设的耦合面**和**与其它模块的耦合面**，给运行时一个**三层开关矩阵**与**预留扩容槽位**。
>
> 范围：v2 阶段直接重做模块边界，**不保留** Plugin / Provider / Store 三套并行入口；reply-affecting 的工程统一走 SystemModule。command / tool-only 插件（echo / debug_commands / web_fetch）仍按普通 Plugin 处理，不进本节。

### 16.1 设计原则

1. **单一抽象**：与回复相关的运行时单位 = SystemModule。没有"plugin + provider + store + service"四重身份。
2. **耦合显式**：模块之间不再直接 `import` 对方 store；通过 **RuntimeStateBus** 提供的**类型化 state slot** 互相消费。
3. **持仓声明**：每个模块声明 `persona_bindings`（读哪些 persona 文件）+ `persona_outputs`（写哪些 persona 文件）+ `state_owns`（拥有哪些 state slot）+ `state_consumes`（消费哪些 state slot）。
4. **拓扑无环**：模块依赖必须构成 DAG；compile 期校验，发现环立即拒绝启动。
5. **三层开关**：persona / group / turn 三个开关层 **取严** —— 任一为 off 即 off。
6. **扩容预留**：模块 id 命名空间、state slot 命名空间、persona 文件清单都有预留位与版本协商槽。
7. **trace 强制**：每轮所有被命中的 state slot、模块决策、模块输出 prompt 块都进 turn trace。
8. **拟真耦合**：模块之间的耦合不是数据流，而是**心理-语言模型**——thinker 读 mood/affection/schedule，guard 读 hard_rules，relationships 写回 memory，state.calendar 影响 mood，affection 影响 thinker tone。

### 16.2 三层抽象

```text
┌─────────────────────────────────────────────────────────┐
│  Layer 3: SwitchMatrix      persona × group × turn       │
│           ──任一 off 即 off──                             │
├─────────────────────────────────────────────────────────┤
│  Layer 2: SystemModule       declares contract           │
│           ports / state / persona binding / lifecycle    │
├─────────────────────────────────────────────────────────┤
│  Layer 1: RuntimeStateBus    typed state slots           │
│           pub/sub + snapshot for trace                   │
└─────────────────────────────────────────────────────────┘
```

- Layer 1 是**数据**层（state 怎么共享）。
- Layer 2 是**契约**层（每个模块声明自己干啥）。
- Layer 3 是**控制**层（每轮哪些模块开 / 关）。

### 16.3 Module Catalog（首版 + 预留）

#### 16.3.1 首版 26 个一级公民模块

> 命名空间：`<group>.<id>`。`group ∈ {core, runtime, state, memory, learning, context, output, eval}`。

| group | id | 职责 | persona 文件耦合 | 拥有的 state slot | 消费 |
| --- | --- | --- | --- | --- | --- |
| core | `core.identity` | 注入身份宪法 | persona.yaml（读） | `core.identity.snapshot` | — |
| core | `core.voice` | 注入表达风格 + 表达素材 | voice.yaml（读 + 学习写回） | `core.voice.profile` | `learning.style.deltas` |
| core | `core.knowledge` | 注入已知/未知/禁说事实 | knowledge.yaml（读） | `core.knowledge.facts` | — |
| core | `core.relationships` | 注入关系画像 | relationships.yaml（读 + 学习写回） | `core.relationships.profile` | `state.affection.snapshot` |
| core | `core.examples` | 注入正例/反例 few-shot | examples.yaml（读） | `core.examples.fewshot` | `runtime.thinker.decision` |
| core | `core.guard` | 输入/输出/记忆写入守门 | guard.yaml（读） | `core.guard.verdict` | 所有上游输出 |
| runtime | `runtime.thinker` | 两阶段决策（action/tone/retrieve_mode/sticker/thought/rewritten_query） | thinker.yaml（读） | `runtime.thinker.decision` | `state.mood.current`, `state.affection.<uid>`, `state.schedule.slot`, `state.calendar.today`, `learning.slang.hits`, `runtime.scheduler.last_skip_reason` |
| runtime | `runtime.scheduler` | 触发判定 + 概率 + 冷却 | runtime.yaml.scheduler（读） | `runtime.scheduler.fire_decision` | `state.mood.current`（mult），`runtime.calendar.time_mult`，`runtime.adapter.last_event` |
| runtime | `runtime.adapter` | 平台事件 / 引用 / 撤回 / 发送回执 | adapter.yaml（读） | `runtime.adapter.last_event`, `runtime.adapter.send_receipt` | — |
| runtime | `runtime.calendar` | 时间倍率 / talk_schedule | runtime.yaml.calendar（读） | `runtime.calendar.time_mult` | — |
| state | `state.mood` | 情绪状态机（label/energy/valence/openness/tension） | state.yaml.mood（读） | `state.mood.current`, `state.mood.history` | `state.schedule.slot`, `state.calendar.today` |
| state | `state.schedule` | 当日日程槽位 | state.yaml.schedule（读 + 生成器写） | `state.schedule.slot`, `state.schedule.today` | `state.calendar.today` |
| state | `state.calendar` | 节日 / 调休 / 生日 / 自身生日 | state.yaml.calendar（读） | `state.calendar.today` | — |
| state | `state.affection` | 好感度 / tier / 衰减 | relationships.yaml.affection（读 + 学习写回） | `state.affection.<user_id>`, `state.affection.recent_changes` | `runtime.adapter.last_event` |
| state | `state.board` | 群对话状态板（活跃用户/主题/@） | runtime.yaml.state_board（读） | `state.board.snapshot` | — |
| memory | `memory.short_term` | GroupTimeline / 私聊短期 / pending buffer | memory.yaml.short_term（读） | `memory.short_term.timeline`, `memory.short_term.pending` | `runtime.adapter.last_event` |
| memory | `memory.long_term` | 实体卡片 / 段落 / 全局索引 | memory.yaml.long_term（读 + 工具写） | `memory.long_term.cards`, `memory.long_term.index` | `memory.short_term.compaction_summary` |
| memory | `memory.compactor` | 上下文压缩 + add_card 工具 | memory.yaml.compaction（读） | `memory.short_term.compaction_summary` | `memory.short_term.timeline` |
| memory | `memory.consolidator` | 离线反思 → 学习候选 | memory.yaml.consolidator（读） | `learning.episode.candidates`, `learning.style.candidates`, `learning.slang.candidates` | `memory.long_term.cards`, conversation_archive |
| learning | `learning.slang` | 群黑话学习 + 注入 | voice.yaml.slang（写候选） | `learning.slang.hits`, `learning.slang.candidates` | `memory.short_term.timeline` |
| learning | `learning.style` | 风格表达学习 + 注入 | voice.yaml.expression（写候选） | `learning.style.hits`, `learning.style.candidates`, `learning.style.deltas` | `memory.short_term.timeline` |
| learning | `learning.episode` | 经验反思学习 + 注入 | memory.yaml.episodes（写候选） | `learning.episode.hits`, `learning.episode.candidates` | `memory.consolidator` |
| context | `context.retrieval` | RRF 融合检索 doc/memory/graph | knowledge.yaml.retrieval（读） | `context.retrieval.pack` | `runtime.thinker.decision.rewritten_query` |
| context | `context.graph` | 知识图谱注入 | knowledge.yaml.graph（读） | `context.graph.triples` | `runtime.thinker.decision.rewritten_query` |
| output | `output.sticker` | 表情包规则 + 库 + 工具 | capabilities.yaml.sticker（读） | `output.sticker.library_view` | — |
| output | `output.send` | 文本切段 / kaomoji enforce / humanizer / reply_prefix | adapter.yaml.send（读） | `runtime.adapter.send_receipt` | `core.guard.verdict` |
| eval | `eval.online` | 在线 soft judge 抽样评测 | eval.yaml.online（读） | `eval.online.verdicts` | 所有上游 |

#### 16.3.2 预留扩容槽（**id 已占位，schema 留空**）

| id | 用途 | 触发条件 |
| --- | --- | --- |
| `state.world` | 外部世界状态：天气、群外事件、bot 当前所在虚拟空间 | 当 importer 引入"地点/天气"概念 |
| `state.desire` | 欲望 / 动机 / 短期需求（"想吃点什么"） | 当 thinker 接 desire 输入 |
| `state.values_drift` | 临时价值观偏移（最近被群友影响的小立场） | 当 relationships 学习写回需要约束 |
| `runtime.planner` | 长程意图栈（goal stack / 多日规划） | 当 thinker 输出超过单轮 |
| `state.intimacy` | 亲密度细化（不同于 affection 的速度） | 当关系画像扩容 |
| `observer.feedback` | 用户反馈观测（点赞/踩/吐槽统计） | 当 eval 进入闭环 |
| `self.reflection` | 自反思（替代 Dream agent 心理化方向） | 当 consolidator 升级 |

> 预留槽**不**实现，但 importer / spec / SwitchMatrix 已经为它们留好命名位与 yaml 键，未来落地无需破坏现有 schema。

### 16.4 SystemModule 契约

每个模块在自身目录下提供 `module.yaml`（声明性元信息）+ Python 实现。

```yaml
# config/persona/<persona_id>/modules/runtime.thinker/module.yaml
schema: omubot.module.v2
id: runtime.thinker
group: runtime
version: 2.0.0
status: active

description: 两阶段决策器，主回复前先决定 action / tone / retrieve_mode / sticker / thought
since: 2026-05-23

persona_bindings:
  reads:
    - thinker.yaml
    - persona.yaml#identity.essence       # 用于 prompt 中"你是谁"
    - persona.yaml#constitution.hard_rules # 不允许选 wait 跳过 hard_rules 检查
  writes: []                                # thinker 不学习人设

state_owns:
  - id: runtime.thinker.decision
    schema: omubot.state.thinker_decision.v2
    ttl: per_turn

state_consumes:
  - state.mood.current
  - state.affection.<user_id>
  - state.schedule.slot
  - state.calendar.today
  - learning.slang.hits
  - runtime.scheduler.last_skip_reason     # 防止 force fire 时 thinker 仍 wait

depends_on:                                  # DAG 上游模块（必须先就绪）
  - state.mood
  - state.affection
  - state.schedule
  - state.calendar

provides_for:                                # DAG 下游模块（声明被谁消费）
  - context.retrieval                        # rewritten_query
  - core.examples                            # tone-aware fewshot 选择
  - output.send                              # tone 影响 humanizer 节奏

switch_surface:
  persona_level: true                        # system.yaml 可关
  group_level: true                          # runtime.yaml.per_group 可关
  turn_level: false                          # 不可在单轮关（关了主链路退化）
  on_disabled:
    behavior: degrade                        # 不抛错；用 default decision
    default_decision:
      action: reply
      retrieve_mode: hybrid
      sticker: false
      tone: 日常
      thought: ""

lifecycle:
  on_init: services.thinker.boot
  on_pre_turn: services.thinker.run
  on_post_turn: services.thinker.record_trace
  on_shutdown: services.thinker.flush

trace:
  records:
    - inputs_snapshot                         # 所有 state_consumes 的快照
    - decision                                # 输出
    - latency_ms
    - usage                                   # token 计数

eval_hooks:
  - eval.online.tone_consistency
  - eval.online.action_appropriateness
```

#### 16.4.1 通用契约规则

1. `id` 全局唯一，命名遵循 `<group>.<id>`；group 限定在 §16.3.1 的 8 个枚举值（外加 §16.3.2 预留）。
2. `version` 遵循 SemVer；major bump = 不兼容（importer 拒绝消费旧 source）。
3. `state_owns[].schema` 必须存在 schema 文件（`schemas/state/<schema_id>.json`）；否则模块拒绝注册。
4. `state_consumes` 中的 slot 必须由其它模块声明 `state_owns`，否则启动期校验失败。
5. `depends_on` ⊆ `state_consumes` 所属模块集合（拓扑保证）。
6. `provides_for` 不参与 DAG 排序，仅作可读性 + 文档检查（声明谁是下游）。
7. `switch_surface.on_disabled.behavior ∈ {fail, degrade, skip}`：fail 拒绝启动，degrade 用 default，skip 直接跳过本模块输出。
8. `lifecycle.on_pre_turn` 与 `on_post_turn` 是**确定性钩子**，不允许在其它钩子时机修改自家 state slot。

#### 16.4.2 与 persona 的耦合面

每个模块 `persona_bindings.reads / writes` 必须映射到 §16.5 的 v2 文件清单。importer 校验：

- 若 `module.persona_bindings.writes` ≠ ∅ → 必须有 `core.guard` 配套的 `memory_write_guard` 规则；否则 error。
- 若 `module.persona_bindings.reads` 包含 persona.yaml → 模块 `switch_surface.turn_level` 必须为 false（防止单轮关掉身份注入）。
- 若模块声明读 `voice.yaml.expression_library`，importer 自动给该模块加 `learning.style` 依赖（如未声明）。

### 16.5 RuntimeStateBus

```python
class StateSlot(Generic[T]):
    id: str                          # e.g. "state.mood.current"
    schema_id: str                   # e.g. "omubot.state.mood.v2"
    ttl: Literal["per_turn", "per_session", "per_user", "persistent"]
    privacy: Literal["public", "group", "user_only", "admin_only"]

class RuntimeStateBus:
    async def get(self, slot_id: str, *, scope: Scope) -> SlotSnapshot[T] | None: ...
    async def set(self, slot_id: str, value: T, *, scope: Scope, source: SourceRef, confidence: float, decay_at: datetime | None = None) -> None: ...
    async def subscribe(self, slot_id: str, listener: Callable[[SlotSnapshot[T]], Awaitable[None]]) -> Subscription: ...
    async def snapshot_all_for_trace(self) -> dict[str, SlotSnapshot[Any]]: ...
```

约束：

1. **每个 slot 有一个 owner**；非 owner 不可 `set`，违者运行时 raise `StateSlotOwnershipError`。
2. **写入必须带 source/confidence**（缺一拒收）。`source` 是 `SourceRef(module_id, evidence_path)`，evidence_path 可指向 conversation_archive 行号 / persona 文件 / 用户输入 hash。
3. **TTL 强制清理**：`per_turn` 在 `on_post_turn` 后归零；`per_session` 在 session reset 后归零；`per_user` 跟 user lifecycle；`persistent` 落 SQLite。
4. **快照可序列化**：`snapshot_all_for_trace()` 输出每轮 trace.yaml 的 `state_snapshots` 块，**不可缺**。
5. **隐私边界**：`privacy=user_only` 的 slot 在群聊 prompt 中必须脱敏（参考 affection 在群里隐藏数值）；guard 模块强制检查。
6. **没有"裸读"**：模块**禁止**绕过 bus 直接读 store / 数据库，importer / CI 各加一条静态检查。

### 16.6 耦合 DAG

```text
                       ┌──────────────────┐
                       │ runtime.adapter  │  (event ingress)
                       └─────────┬────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
      ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐
      │ memory.short │  │ state.affec- │  │ runtime.calendar │
      │ _term        │  │ tion         │  └────┬─────────────┘
      └──────┬───────┘  └──────┬───────┘       │
             │                 │               ▼
             │                 │      ┌────────────────┐
             │                 │      │ state.calendar │
             │                 │      └────────┬───────┘
             │                 │               │
             │                 │               ▼
             │                 │      ┌────────────────┐
             │                 │      │ state.schedule │
             │                 │      └────────┬───────┘
             │                 │               │
             │                 │               ▼
             │                 │       ┌──────────────┐
             │                 └──────►│  state.mood  │
             │                         └──────┬───────┘
             │                                │
             │                                ▼
             │                       ┌─────────────────────┐
             │                       │ runtime.scheduler   │ ← gating
             │                       └────────┬────────────┘
             │                                │ (fire?)
             │                                ▼
             │                       ┌─────────────────────┐
             ├──────────────────────►│  runtime.thinker    │
             │                       └────────┬────────────┘
             │                                │
             ├────────► learning.slang.hits ──┤
             ├────────► learning.style.hits ──┤
             ├────────► learning.episode ─────┤
             │                                │
             │            ┌───────────────────┴────────────┐
             │            ▼                                ▼
             │   ┌────────────────┐               ┌────────────────┐
             │   │ context.       │               │  core.examples │
             │   │ retrieval (RRF)│               └────────┬───────┘
             │   └────────┬───────┘                        │
             │            ▼                                │
             │   ┌────────────────┐                        │
             │   │ context.graph  │                        │
             │   └────────┬───────┘                        │
             │            │                                │
             ├──► memory.long_term.cards ──┐               │
             │                             │               │
             ▼                             ▼               ▼
   ┌─────────────────────────────────────────────────────────┐
   │            core.identity / core.voice / core.knowledge   │
   │            core.relationships / state.board / sticker    │
   └─────────────────┬────────────────────────────────────────┘
                     │ (assemble system prompt)
                     ▼
                ┌─────────┐
                │  LLM    │
                └────┬────┘
                     │
                     ▼
              ┌─────────────┐
              │ core.guard  │ ← hard / soft / rewrite
              └──────┬──────┘
                     ▼
              ┌─────────────┐
              │ output.send │ ← segment / humanizer / receipt
              └──────┬──────┘
                     ▼
              ┌─────────────────────────┐
              │ memory.short_term.add   │ (assistant turn)
              │ memory.compactor (cond) │
              │ memory.consolidator     │ (offline)
              │ learning.* (offline)    │
              │ eval.online (sample)    │
              └─────────────────────────┘
```

约束：

1. DAG **只能**单向流；compile 期 Tarjan 检测环。
2. 离线模块（`memory.consolidator` / `learning.*` 的离线分支）**不进**主回复 DAG，只读 conversation_archive。
3. `core.guard` 出现两次：input guard（在 thinker 之前）+ output guard（LLM 之后），但仍是同一模块两个 lifecycle hook，不算环。

### 16.7 SwitchMatrix（三层开关 / 取严）

#### 16.7.1 三层

| 层 | 位置 | 粒度 | 写入者 |
| --- | --- | --- | --- |
| L0 persona | `system.yaml.modules.<id>.enabled` | 整个 persona 范围 | admin（importer 写） |
| L1 group | `runtime.yaml.per_group_overrides.<gid>.modules.<id>.enabled` | 单群 | admin per-group |
| L2 turn | `runtime.thinker.decision.turn_overrides.disable_modules` 数组 | 单轮 | thinker（自动）+ adapter event flag（人为） |

**取严**：`final_enabled = L0 AND L1 AND L2`。任一 false 即 false。

#### 16.7.2 必备开关位

每个模块默认 `enabled=true`，但以下模块**必须**至少能在 L0 + L1 关：

| 模块 | 关时表现 |
| --- | --- |
| `runtime.thinker` | 退化为 default decision（action=reply, tone=日常, retrieve_mode=skip） |
| `state.mood` | 不注入 mood block；scheduler 用 mult=1.0 |
| `state.schedule` | 不注入 activity；calendar 仍可独立工作 |
| `state.affection` | 不注入关系块；relationships 退化为 disposition only |
| `learning.slang/style/episode` | 不注入对应 prompt 块；离线学习仍可继续（除非也关了 consolidator） |
| `context.retrieval` | retrieve_mode 强制 skip |
| `context.graph` | 不注入 KG triples |
| `output.sticker` | 移除 sticker 工具 + 表情包库 + kaomoji enforce |
| `eval.online` | 不抽样 |

#### 16.7.3 不可关

| 模块 | 理由 |
| --- | --- |
| `core.identity` | 关了就不是这个 persona |
| `core.guard` | 关了就没有 OOC 兜底 |
| `runtime.adapter` | 关了无法收发消息 |
| `runtime.scheduler` | 关了无法决定是否开口（私聊路径 force fire 仍走它） |
| `memory.short_term` | 关了上下文断流 |
| `output.send` | 关了无法发送 |

importer 在 system.yaml 校验时**强制**这 6 个模块 `enabled=true`，用户写 false 直接 error。

#### 16.7.4 turn-level 用例

- 用户在群里说"切换严肃模式" → adapter 发 turn flag `disable_modules=[output.sticker, learning.slang]`，本轮 thinker 看见后保留 tone=认真。
- thinker 自己输出 `turn_overrides.disable_modules=[context.retrieval]` 因为判断"无需查资料"。

### 16.8 与 v2 spec 的对齐（清洁重做）

> 不考虑兼容，v2 spec 文件清单从 12 文件升到 **15 文件 + 一个 modules 子目录**。

```text
config/persona/<persona_id>/
  persona.yaml          # 身份宪法
  voice.yaml            # 表达风格 + 表达素材
  knowledge.yaml        # 已知/未知/禁说事实 + retrieval/graph 子节
  relationships.yaml    # 关系画像 + affection 子结构
  memory.yaml           # short_term / long_term / episodes / compaction / consolidator
  examples.yaml         # 正例/反例/保护性场景/自然度
  guard.yaml            # input / prompt / memory_write / plugin_diff / output / stream
  eval.yaml             # 离线 + 在线评测
  trace.yaml            # 每轮 trace schema
  thinker.yaml          # 思考器策略 + 决策原则 + 输出 schema
  state.yaml            # mood / schedule / calendar / state_board / 预留 (world/desire/...)
  runtime.yaml          # scheduler / adapter / context_retrieval / per_group_overrides / post_process
  capabilities.yaml     # 工具 / 插件 / 权限 / sticker / 学习管道开关
  adapter.yaml          # 平台事件 / 消息源 / 引用 / 撤回 / send 子节（segmenter/humanizer/reply_prefix）
  system.yaml           # ★ 新增：模块开关矩阵 L0 + DAG + 模块 manifest 索引
  modules/              # ★ 新增：每个模块自带 module.yaml
    core.identity/module.yaml
    runtime.thinker/module.yaml
    state.mood/module.yaml
    ...
  source.md             # 用户唯一手写入口（importer §4）
  .draft/               # importer 输出（importer §4）
```

#### 16.8.1 system.yaml schema

```yaml
schema: omubot.system.v2
persona_id: fengxiaomeng
version: 2.0.0
modules:
  core.identity:        { enabled: true,  required: true }
  core.voice:           { enabled: true }
  core.knowledge:       { enabled: true }
  core.relationships:   { enabled: true }
  core.examples:        { enabled: true }
  core.guard:           { enabled: true,  required: true }
  runtime.thinker:      { enabled: true }
  runtime.scheduler:    { enabled: true,  required: true }
  runtime.adapter:      { enabled: true,  required: true }
  runtime.calendar:     { enabled: true }
  state.mood:           { enabled: true }
  state.schedule:       { enabled: true }
  state.calendar:       { enabled: true }
  state.affection:      { enabled: true }
  state.board:          { enabled: true }
  memory.short_term:    { enabled: true,  required: true }
  memory.long_term:     { enabled: true }
  memory.compactor:     { enabled: true }
  memory.consolidator:  { enabled: true }
  learning.slang:       { enabled: true }
  learning.style:       { enabled: true }
  learning.episode:     { enabled: true }
  context.retrieval:    { enabled: true }
  context.graph:        { enabled: true }
  output.sticker:       { enabled: true }
  output.send:          { enabled: true,  required: true }
  eval.online:          { enabled: false, note: "灰度后再开" }

  # ── 预留位（schema_only，default disabled） ──
  state.world:          { enabled: false, reserved: true }
  state.desire:         { enabled: false, reserved: true }
  state.values_drift:   { enabled: false, reserved: true }
  runtime.planner:      { enabled: false, reserved: true }
  state.intimacy:       { enabled: false, reserved: true }
  observer.feedback:    { enabled: false, reserved: true }
  self.reflection:      { enabled: false, reserved: true }

dag_check:
  on_cycle: refuse_boot
  on_missing_dep: refuse_boot

trace:
  retention_days: 30
  records_required:
    - state_snapshots
    - module_decisions
    - prompt_blocks_per_module
    - guard_verdict
    - send_receipt
```

#### 16.8.2 模块与 persona 文件的互锁矩阵（importer 校验依据）

| 模块 | 强读取 | 强写入 | 校验规则 |
| --- | --- | --- | --- |
| core.identity | persona.yaml | — | persona.yaml 缺失 → refuse_boot |
| core.voice | voice.yaml | voice.yaml.expression_library（仅 candidate） | 写入必经 core.guard.memory_write |
| core.relationships | relationships.yaml | relationships.yaml.profiles（仅 candidate） | 同上 |
| core.guard | guard.yaml + persona.yaml.constitution | — | 每条 hard_rule 必须有匹配的 hard_check pattern |
| runtime.thinker | thinker.yaml + persona.yaml.identity.essence | — | 输出 tone ⊂ voice.yaml.tone_palette |
| state.mood | state.yaml.mood | — | mood 阈值不能让 scheduler 永远 fire 或永远 skip |
| state.schedule | state.yaml.schedule | state.yaml.schedule.today（生成器写） | 写入必经 core.guard |
| state.calendar | state.yaml.calendar | — | self_birthday 必须与 persona.yaml.identity 一致 |
| state.affection | relationships.yaml.affection | `relationships.yaml.affection.<uid>`（仅 candidate） | tier 阈值单调递增 |
| memory.compactor | memory.yaml.compaction | memory.yaml.long_term.cards（仅 add_card 工具） | 写入必经 core.guard.memory_write |
| memory.consolidator | memory.yaml.consolidator | learning.*.candidates | 离线，不进主 prompt DAG |
| learning.slang/style/episode | voice.yaml / memory.yaml.episodes | 同左（candidate） | promotion 必须由 admin 改 review_status=approved |
| context.retrieval | knowledge.yaml.retrieval | — | RRF 权重和 ≤ 1 |
| context.graph | knowledge.yaml.graph | — | edge_kind 受 schema 约束 |
| output.sticker | capabilities.yaml.sticker | — | 工具集合 ⊂ tool registry |
| output.send | adapter.yaml.send | runtime.adapter.send_receipt | 切段上限 ≤ adapter.yaml.send.max_segments |
| eval.online | eval.yaml.online | eval.online.verdicts（per_session） | 抽样率 ≤ 0.5 |

### 16.9 与 importer 的对齐

#### 16.9.1 source.md 增章节

> §11 之上扩展，全部归在 §8「偏好覆写」**之外**新增独立 §12 / §13。

```markdown
# 12. 模块开关（选填，默认全开除 eval.online）

> 列出你想关的模块；不填即按 system.yaml 默认。

- [ ] runtime.thinker
- [ ] state.mood
- [x] eval.online   （灰度前不开）
- ...

# 13. 模块定制（选填）

> 对单个模块的偏好覆写（YAML patch 风格）。

## 13.1 runtime.thinker
（默认）

## 13.2 state.mood.thresholds
（默认）

## 13.3 state.affection.tiers
- tier_1: 0
- tier_2: 30
- tier_3: 60
- tier_4: 85
（如果留空则用默认）
```

#### 16.9.2 §6 字段映射表新增 11 行

| source 章节 | 目标文件.字段 | 抽取器 | 必填 |
| --- | --- | --- | --- |
| §10.1 心情倾向 | state.yaml.mood.bias | sentence_md | 否 |
| §10.2 心情阈值覆写 | state.yaml.mood.thresholds（patch） | yaml_patch | 否 |
| §10.3 自身生日 / wxs_member | state.yaml.calendar.self / state.yaml.calendar.wxs_members | kv_md | 否 |
| §10.4 日程偏好 | state.yaml.schedule.preferences | list_md | 否 |
| §11.1 思考器决策原则补充 | thinker.yaml.policy.extra_principles | list_md | 否 |
| §11.2 tone_palette | voice.yaml.tone_palette + thinker.yaml.policy.tone_set | list_md（>= 3 条） | 是 |
| §9.1 主动性偏好 | runtime.yaml.scheduler（patch） | yaml_patch | 否 |
| §9.2 per_group 覆写 | runtime.yaml.per_group_overrides | yaml_patch | 否 |
| §5.x affection 子结构 | relationships.yaml.affection | yaml_patch | 否 |
| §12 模块开关 | `system.yaml.modules.<id>.enabled` | checkbox_md | 否 |
| §13 模块定制 | `modules/<id>/module.yaml.overrides` | yaml_patch | 否 |

#### 16.9.3 默认模板新增

§8 默认模板首版从 3 份扩到 **9 份**：

1. `guard.yaml`（已有）
2. `eval.yaml`（已有）
3. `trace.yaml`（已有）
4. `runtime.yaml`（**新增**：scheduler 默认 + context_retrieval RRF + per_group_overrides 空骨架 + post_process）
5. `state.yaml`（**新增**：mood 阈值 + schedule 生成器配置 + calendar 数据源 + state_board 开关；预留 world/desire/values_drift/intimacy 空键）
6. `thinker.yaml`（**新增**：thinker_enabled + max_tokens + 决策原则 prompt + tone_set 同步 voice）
7. `adapter.yaml`（**新增**：QQ/NapCat 事件 + send.max_segments=4 + send.segment_delay=0.8s + send.humanizer + send.reply_prefix_for_at=true）
8. `capabilities.yaml`（**新增**：工具列表占位 + sticker 模式默认 inherit + slang_enabled=true 等）
9. `system.yaml`（**新增**：见 §16.8.1，全部模块默认 enabled，eval.online + 7 个预留位 disabled）

#### 16.9.4 校验规则补强（importer §7.2 引用闭环）

新增：

- `system.yaml.modules.<id>.enabled=false` 但 `module.required=true` → error
- 任一 `state_consumes` slot 没有 owner → error
- DAG 有环 → error，列出环路
- `voice.yaml.tone_palette` 与 `thinker.yaml.policy.tone_set` 不一致 → warn + 自动取交集
- `state.affection.tiers` 不单调递增 → error
- `runtime.scheduler.talk_value × state.mood.max_multiplier × runtime.calendar.max_time_mult > 2.5` → warn（fire 风暴风险）
- 任一模块 `persona_bindings.writes ≠ ∅` 但 `core.guard.memory_write` 没列对应规则 → error
- 任一预留模块 `enabled=true` 但 schema 未实现 → error

#### 16.9.5 不变量补强（importer §9）

新增：

- #7 模块写入受 guard 控制：`persona_bindings.writes ≠ ∅` 必经 `core.guard.memory_write` 钩子
- #8 模块禁止裸读 store：所有 cross-module 数据访问必须经 RuntimeStateBus；importer 在 source.md → draft 时不会生成裸 store import
- #9 切换不破坏 DAG：禁用某模块时，其下游模块必须有 `on_disabled` 行为声明，否则 importer error
- #10 预留模块占位：所有 §16.3.2 预留模块必须在 system.yaml 显式列出（即使 disabled），不允许"完全不写"

### 16.10 扩容空间

#### 16.10.1 命名空间预留

- `state.<future>` —— 预留 7 个槽位（§16.3.2）
- `runtime.<future>` —— 预留 `runtime.planner`
- `observer.<future>` —— 预留新 group `observer`
- `self.<future>` —— 预留新 group `self`

新增模块只需走 §16.4 模板写一份 `module.yaml`，并在 `system.yaml` 添加一行；不动既有模块代码。

#### 16.10.2 版本协商

`system.yaml.modules.<id>` 支持 `version_constraint: ">=2.0,<3.0"`：

- importer 启动时校验本地 module 实现 version 满足约束。
- 不满足 → 退到 `on_disabled.behavior=degrade`（用 default）+ 写 trace warning，**不**拒绝启动。
- 这给了"灰度升级单个模块"的能力。

#### 16.10.3 能力发现

每个模块 `module.yaml.capabilities`：

```yaml
capabilities:
  emits_events: [decision_made]
  consumes_events: [adapter.message_received]
  exposes_admin_routes:
    - "GET /api/system/<id>/status"
    - "POST /api/system/<id>/reload"
  exposes_metrics:
    - "<id>.latency_ms"
    - "<id>.error_count"
```

importer 据此自动给 admin SPA 生成"模块状态卡片"骨架；新模块上线无需手写 admin 页。

#### 16.10.4 状态 slot 演化

每个 slot 的 schema_id 带版本号（`omubot.state.mood.v2`）。新版本上线时：

1. 旧版本 reader 仍消费旧 schema（importer 把 mood.v2 投影成 mood.v1）。
2. 灰度结束后 importer 一次性把 system.yaml 升到 v3。
3. 旧 schema 归档到 `schemas/archive/`。

### 16.11 实施切片更新（覆盖 §10）

| # | 切片 | 关键交付 | 依赖 |
| --- | --- | --- | --- |
| **S1'** | system.yaml + module.yaml schema + RuntimeStateBus 接口骨架 | `services/system_module/` + `schemas/state/` | — |
| **S2'** | source.md 模板 + 解析器（覆盖 §4 + §16.9.1 新章节） | `services/persona/source_parser.py` | S1' |
| **S3'** | 列表型 + 键值型抽取器 + 默认模板载入（9 份） | `services/persona/extractors/*` + `config/persona/_defaults/v2/` | S2' |
| **S4'** | LLM 抽取器（受限 schema） | `services/persona/extractors/llm.py` | S3' |
| **S5'** | 校验：DAG / 闭环 / hard_rule ↔ guard / module ownership | `services/persona/validator.py` | S4' |
| **S6'** | core.identity / core.voice / core.knowledge / core.relationships / core.examples / core.guard 模块实现 | `services/system_module/core/*` | S1' |
| **S7'** | runtime.thinker / runtime.scheduler / runtime.adapter / runtime.calendar 模块实现 | `services/system_module/runtime/*` | S1' |
| **S8'** | state.mood / state.schedule / state.calendar / state.affection / state.board 模块实现 | `services/system_module/state/*` | S1' |
| **S9'** | `memory.*` + `learning.*` + `context.*` + `output.*` + `eval.online` 模块实现 | `services/system_module/{memory,learning,context,output,eval}/*` | S1' |
| **S10'** | admin API + admin SPA importer + 模块状态卡片 | `admin/routes/api/persona_importer.py` + `admin/frontend/src/views/persona/` | S2'-S5' + S6'-S9' |
| **S11'** | persona compiler v2（消费 .draft → prompt blocks） | `services/persona/compiler.py` | S6'-S9' |
| **S12'** | 灰度切流：source.md → 真正运行时 | feature flag `persona_v2.enabled` | S10' + S11' |

S1' 必须最先；S2'-S5' 与 S6'-S9' 可并行；S10' 与 S11' 可并行；S12' 是收口。

### 16.12 已确认的衍生开放问题（首轮审计 → §17）

> Q13-Q17 已在 §17.1 决策表锁定。下方保留原文 + 选项 + 已确认结论作为可追溯审计记录。后续修订请改 §17。

- **Q13：模块边界 — 是否接受首版 26 个 + 7 预留？**
  - A. 接受
  - B. 砍：把 state.schedule + state.calendar 合并到 runtime.calendar
  - C. 加：现在就实现 state.world / runtime.planner 之一
  - **已确认：A**（§17.1 / state / runtime / calendar 三者职责差异大，不合并；7 预留只占 schema 不参与 DAG）

- **Q14：单轮关模块（L2）由谁主导？**
  - A. 仅 thinker 自动决定
  - B. 仅 adapter event flag（用户/管理员显式触发）
  - C. 两者并存
  - **已确认：C**（§17.1 / `ThinkDecision.disable_modules` ∪ `event.flags.disable_modules` 取严）

- **Q15：消除"裸读 store"（不变量 #8）的检查方式**
  - A. 静态检查（importer 扫描 module.yaml + Python AST）
  - B. 运行时检查（store 加 caller 拦截）
  - C. 两者都做
  - **已确认：C**（§17.1 / 静态防新写漏洞，运行时兜底既存调用）

- **Q16：source.md 是否在 §12 / §13 让用户写模块开关？**
  - A. 是（用户可显式禁用）
  - B. 否（system.yaml 由 admin 通过 SPA 改，source.md 不暴露开关）
  - **已确认：A**（§17.1 / source.md 仍是版本化真相源；SPA 改完最终回写 source.md）

- **Q17：persona compiler v2（S11'）落地节奏**
  - A. 与 importer 同期上线（一次性切流）
  - B. importer 先上线产 draft；compiler 后上线消费 draft
  - **已确认：B**（§17.1 / 灰度更安全；feature flag `persona_v2.enabled` 默认 off）

---
