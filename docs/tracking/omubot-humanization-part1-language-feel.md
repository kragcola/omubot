# Omubot 拟人修复 Part 1 — 语感重构（编排既有基础设施 / 跨子系统统一化）

> 状态：2026-05-25 v3+ 全量审计版。v1（persona 文本负向约束 + post-LLM 剥离）作废；v2（并建 L1/L2/L3 三层新模块）经用户驳回；v3 首版只覆盖用户点名 6 模块，本次（v3+）扩展为整链路审计——把 thinker / 日期上下文 / chat plugin / reply_workflow 语义前置 gate 加入接入图；§3 U 系列扩到 U13；§5 V 系列扩到 V17；并立 Part 0「屎山清债」前置 commit 链。
>
> v3 设计原则：**编排（orchestrate）现有基础设施 → 不是并建（parallel-build）新模块**。允许的新代码限于"集线器 / 协议 / 评分器 / 重采样器"四类，所有状态、检索、注入、记账走既有通道（RuntimeStateBus / ContextProvider / PromptBudgetManager / BlockTraceStore / learning_normalizer / AffectionProfile / MoodEngine / ScheduleGenerator / TalkSchedule / DreamAgent / StickerStore / EpisodeStore / CardStore）。
>
> 用户原话锚点（保留勿删）：
> - 「bot 性格活跃不代表语句里面要通过——，感叹号，夸张的惊讶来表达，而是从其他方面展现，这是人文方向的研究」
> - 「通过调整人设文件和负约束是失败的方向」
> - 「驳回本板方案，该版本方案未能与现有之前预留的元素做协同，白白浪费了先前的设计。心情、每日节奏，学习管线的表达方式，插件的用户好感度，上下文和记忆，表情包，都没有看到」
> - 「目前的研究结果得到初步肯定，进行更深一步的研究，给我第三版方案。允许在该方案中重构会统一化其它模块」
> - 「我只说了那几个模块，你就只看了那几个？chat 呢，日期上下文呢？thinker 呢？整个 bot 对话流程全量检索，我怀疑先前修改太多积压的内容成屎山，本次重构统一化正好检索排查，将过期内容，要重写内容，未达到预期内容重写。part1 统一化列表加入检索后同工作流其它模块，检索后审计是否需要增加 part0 代码重写」
>
> 上下文授权（保留勿删）：
> - 「依据文档自主做上线前准备，不用问我。我最终做上线前最后验收」
> - 灰度群已落地 993065015 / 984198159

---

## 0. 研究锚点（v2 §0 沉淀，结论保留）

### 0.1 三篇核心证据（Surface ≠ 活跃）

| 论文 | 关键数字 | 推论 |
|---|---|---|
| **Jain 2024** ([arxiv:2409.10245](https://arxiv.org/abs/2409.10245)) | LLaMA-2-7B Neuron 2070 / Mistral 1512 专责"性格→emoji"；训练集无 emoji 时 PEFT 仍 99.5% 触发 | 神经元级耦合，prompt 负向约束无效；删 ☆ 模型用 ✦/♪ 代偿 |
| **Liu 2026 SDH** ([arxiv:2605.20602](https://arxiv.org/abs/2605.20602)) | 跨 5 LLM ρ_depth=0.540：surface +24.9%（discourse markers +126.2%）；d≥2 句法死亡 −47.2% | 剥离 surface 不修复深层句法塌缩；表面变干净反而更 AI |
| **Padmakumar & He 2024** ([arxiv:2309.05196](https://arxiv.org/abs/2309.05196)) | InstructGPT 同质化分 0.1660 vs Solo 0.1536（p<0.05）；同质化 100% 来自模型贡献 token 段 | RLHF 阶段熵下降是模型级现象，不是 prompt 问题 |

### 0.2 真人活跃 markers

**心理语言学**（Pennebaker / Schwartz / Yarkoni / Mehl）：性格信号分布在 5 层 feature——内容 / 篇章 / 语用 / 句法 / 词汇。外向 / 活跃在统计上稳定耦合到 *话题选择* + *功能词分布* + *词长 / 句长方差大*，**不在标点**。

**戏剧 / 小说创作论**（McKee / Egri / Stein / Hemingway）：活跃 = subtext 三层落差 + register 漂移幅度 + 私人词汇 markers + 回避与省略 + 话语动作。McKee："dialogue is action, not information"——表层惊叹号是 on-the-nose。

**中文 IM 语料**（PACLIC / 微信硕论 / 商务印书馆）：

| 真人活跃 marker | 频次 / 比例证据 | 来源 |
|---|---|---|
| 短句 ≤20 字 | **77.95%** 消息 ≤20 字 | PACLIC 2008 |
| 句末不加句号 | "句号 = 突兀/敷衍" 实验 | Houghton 2018 |
| 单字 / 双字应答 | 嗯嗯 2280 / 哈哈 1674 / 月 | 腾讯云语料 |
| 自定义表情 + emoji（非颜文字） | 哭笑不得 75 亿次/年 | 中国青年网民报告 |
| 话语叠连 | 三音节以下完全叠连 | 李先银 2016 |
| 拼音缩写 yyds/kswl/dbq | 入选国语委十大网络用语 | 国语委 2021 |
| 当年度热梗（松弛感 / 班味） | 80 亿语料抽取 | 商务印书馆 2024-25 |

**Register 边界**：☆ / ～ / 颜文字 / "哇——" = 二次元亚文化 register（ACG 25 岁以下占 63%）。普及到普通群 = 强行身份标注 = 身份冒充。

### 0.3 推论

唯一正确方向：把"活跃"从 surface 解耦到 **内容（topic / lexicon）+ register（节奏 / 语气）+ 关系（rapport / mood）+ 重采样（critic）**。这正好是 Omubot 已有但未串通的 6 大保留子系统的工作分工。v3 的事就是 **把它们串起来跑**。

---

## Part 0 — 屎山清债（v3 主线之前的强制前置）

> 用户原话锚点：「我怀疑先前修改太多积压的内容成屎山，本次重构统一化正好检索排查，将过期内容，要重写内容，未达到预期内容重写。检索后审计是否需要增加 part0 代码重写」
>
> 三个 Explore agent 全量审计，确认 23 净候选（≥ 12 阈值），单独立 Part 0 commit 链，**先做完 P0.1~P0.8 才进入 §3 U 系列**。每条独立可 revert，不依赖任何 v3 humanization 设计。

### 0a.1 审计取证（agent 1 / agent 2 / agent 3 三方互证）

| 类型 | 计数 | 取证 |
|---|---|---|
| **A 配置死链**（写在 toml/json，运行时不读） | 18 字段 | `[reply_segmentation]` 13 字段（`max_send_segments` / `soft_max_send_segments` / `soft_limit_notice` / `boundary_backend` / `preserve_ascii_tokens` / `merge_short_tail` / `first_segment_humanize` / `later_segment_humanize` / `inter_segment_delay_s` 等）；`[scheduler.concurrency]` 5 字段（`global_llm_limit` / `max_group_queue` / `max_low_priority_queue` / `first_segment_release` / `drop_stale_low_priority_after_s`）；`reply_workflow.shadow_log_private` 死字段 |
| **B 双注入 / 双调用** | 5 处 | mood block 双注入（mood.py:280 build_mood_block 与 client.py:1794 `_build_thinker_mood_text` 同源 valence 写两次）；语义 gate 双 LLM call（router.py:935 evaluate_semantic_gate + thinker.py:320 think 同 turn 两个 haiku）；BackupScheduler 双实例化（`admin/__init__.py` 与 plugin 启动钩子各一次）；`card_store.close()` 双调用（chat/plugin.py:1044 + 1051）；StickerStore.format_prompt_view 全表 dump 在 system block 与 stable block 两处 |
| **C 0 调用方 dead code** | 3 模块 | `services/llm/segmentation.py` 574 行（已列入 U1）；`services/system_module/state_bus.py` 86 行 RuntimeStateBus（已列入 U6）；`services/scheduler/group_send_queue.py` GroupSendQueue 类生产 0 caller |
| **D 结构性疑点**（不 Part 0 处理，单独跟踪）| 4 项 | chat plugin god-file 584-1031 行 on_startup（建议 Part 1.5 拆 ChatPluginBootstrap）；thinker 输出旁路注入 client.py:2107-2124（建议 V15 走 PromptBlock 管线）；reply_segmentation 配置项与 humanizer 双跑（U1 之后再清 dead config）；slash-command 双解析 chat/plugin.py:_handle_debug_split + register_commands |

### 0a.2 P0.1 ~ P0.8 子任务

| 编号 | 任务 | 范围 | 风险 |
|---|---|---|---|
| **P0.1** | 删 `[reply_segmentation]` 死配置 13 字段 | config/config.toml + config/config.json（顶层 reply_segmentation 镜像）+ kernel/config.py:ReplySegmentationConfig 字段；保留 enabled / max_segment_chars / min_segment_chars / prefer_sentence_break 4 字段（生产真读） | 低：纯配置，bot 不读不影响行为 |
| **P0.2** | 删 `[scheduler.concurrency]` 死配置 5 字段 | 同 P0.1 路径；scheduler.concurrency 整段删（或仅保留 enabled） | 低：纯配置 |
| **P0.3** | 删 `reply_workflow.shadow_log_private` 字段 | router.py grep 验证 0 caller 后删 toml/json 字段 + Pydantic 字段 | 低：纯配置 |
| **P0.4** | 修 mood block 双注入 | client.py:1794 `_build_thinker_mood_text` 改为读 `bus.state.mood.current`（v3 §1.3 缺口 1 自然成立后回收）；本期先去重——thinker prompt 不再独立 build mood，复用 prompt_builder mood block | 中：thinker prompt 行为变；先跑 shadow |
| **P0.5** | 修 `card_store.close()` 双调用 | chat/plugin.py:1044 与 1051 任删一处；保留更下游的（plugin shutdown hook） | 极低：close 幂等但浪费 |
| **P0.6** | 删 GroupSendQueue 0 caller 类 | `services/scheduler/group_send_queue.py` 整文件删；grep 同模式扫 0 引用确认 | 低：纯死代码 |
| **P0.7** | 修 BackupScheduler 双实例化 | `admin/__init__.py` 与 plugin 启动钩子各启一次 → 单一启动入口；admin SPA 仍走原 API | 中：备份调度，需手动验证 24h 后只跑一次 |
| **P0.8** | 订正 thinker `on_thinker_decision` hook 语义 | hook 保留为 PluginBus 扩展协议；V13 之后生产读取改走 RuntimeStateBus 的 `bus.state.thinker.last_decision`，不依赖 fan-out 结果 | 低：保留协议，新增 bus 读点 |

P0.1~P0.8 是纯清债，**不引入任何新行为**。8 条独立 commit 或合并 1~2 个 commit 落地，per agent 3 评估完整工作量 ≤ 200 行净删除。

### 0a.3 不在 Part 0 处理（D 类结构性疑点）

| 项 | 处理时机 | 原因 |
|---|---|---|
| chat plugin on_startup 584-1031 god-file | Part 1.5 或 Part 2 | 拆分需重新装配 PluginContext 路径，blast radius 大；本期 V17 在此函数加 humanization 装配 30 行可承受 |
| thinker block 旁路注入 client.py:2107-2124 | V15 | 走 PromptBlock 管线后由 PromptProviderBus + BudgetManager 自然记账；本期保留旁路保证 v1 行为 |
| segmentation 双实现 | U1（已列入 §3） | Part 0 不动它（U1 主线已覆盖） |
| slash-command 双解析 | 不动 | 两条路径用法不同（debug split 与 register_commands）；保留 |

### 0a.4 提交节奏

**C0：Part 0 清债**（在 C1 之前；可拆 1~2 个 commit）

| sub-commit | 范围 |
|---|---|
| C0a | P0.1 + P0.2 + P0.3 + P0.5 + P0.6 + P0.8（纯删除 / 极低风险） |
| C0b | P0.4（mood 双注入修复，需 shadow 验证）+ P0.7（BackupScheduler，需手动 24h 验证） |

C0a 落地后立即 `pytest -q`；C0b 落地后跟进 24h shadow log。

---

## 1. 现有基础设施盘点（v3 必须编排的对象）

### 1.1 已落地但未消费 / 未串通的子系统（v3 的「金矿」）

| 子系统 | 文件 | 现状 | v3 角色 |
|---|---|---|---|
| `RuntimeStateBus` | [services/system_module/state_bus.py:43-123](../../services/system_module/state_bus.py#L43-L123) | 完整 schema（owner / scope / TTL / evidence_path / confidence），catalog 27 个槽位声明，**生产 0 调用方**（仅 builder.py:1166 字符串验证） | v3 的状态总线；humanization ModuleContract 为 owner |
| `style_expressions.persona_fit / mood_fit` | [services/style/store.py:55-56,1826-1843](../../services/style/store.py#L1826-L1843) | 字段写盘但 `_expression_relevance` 注入时**只比 situation/style 文本**，两个数值字段在召回时是死列 | v3 激活：召回时按 `bus.mood.valence` 加权 |
| `MoodEngine._cache: tuple[MoodProfile, float] \| None` | [plugins/schedule/mood.py:125,134-145](../../plugins/schedule/mood.py#L125) | 全局单 tuple（同一 cache 跨所有群 / 会话）；30min 缓存；`recent_interaction_count` 形参所有调用点恒为 0 | v3 扩 per-(group, session) 字典；激活 recent_interaction_count |
| `Humanizer.delay()` | [services/humanizer.py:36-43](../../services/humanizer.py#L36-L43) | 56 行纯 sleep，按 `len(text)` 线性；不知 register 不知 mood 不知 group | v3 升级 register-aware；输入 (group_id, slot, mood, register) 计算 delay |
| `DreamAgent` | [plugins/dream/plugin.py:217-469,498](../../plugins/dream/plugin.py) | 24h 后台扫，默认 disabled；run 后 `ctx.prompt_builder.invalidate()`；只清表情包 LRU 与 memo | v3 接入：扫 catchphrase TTL set + register_state per_session 状态 |
| `services/llm/segmentation.py` | [services/llm/segmentation.py](../../services/llm/segmentation.py) | 574 行新模块，**生产 0 调用方**；生产仍走 [services/llm/client.py:359-538 `_reply_segments`](../../services/llm/client.py#L359) | v3 之前必须先合并（U1） |

### 1.2 已落地且已生产消费的子系统（v3 编排的「现成轨道」）

| 子系统 | 文件 | 入口 | v3 接入方式 |
|---|---|---|---|
| `AffectionProfile` | [plugins/affection/models.py:9-46,engine.py:40-63,164-248](../../plugins/affection/models.py) | `engine.record_interaction` / `engine.build_affection_block` | v3 不新建关系存储；扩 `AffectionProfile.familiarity_score`（短期亲密度，TTL 60 min）；映射到 register 档位 |
| `MoodProfile`（4 维 + label） | [plugins/schedule/types.py:47-63,plugins/schedule/mood.py](../../plugins/schedule/mood.py) | `MoodEngine.get(schedule, count)` | v3 把 mood.valence / energy 写进 RuntimeStateBus `state.mood.current`，给 register / scorer 读 |
| `ScheduleGenerator + TalkSchedule` | [plugins/schedule/generator.py:108-116,plugins/schedule/calendar.py](../../plugins/schedule/generator.py) | 02:00 CST LLM 生成 8-12 TimeSlots；`TalkSchedule.now_slot()` | v3 不改生成器；register 档位读 `slot.energy / slot.label` 调匀 |
| `ContextProvider / PromptProviderBus / PromptBudgetManager / BlockTraceStore` | [services/block_trace/](../../services/block_trace/) | `slang_provider.py` 是模板（89 行）；3-bucket 预算 static=1500/stable=2000/dynamic=4000 | v3 新增 `CatchphraseProvider` / `RegisterProvider` 两个 ContextProvider，priority/position 走预算管线；不新建 prompt 装配代码 |
| `EpisodeStore` | [services/episodic/store.py:21-32,425-452](../../services/episodic/store.py) | 5-state 机；`list_for_recall(group_id, limit=3)` | v3 register-aware 召回：按 `bus.register.label` 过滤 episode tags |
| `CardStore` | [services/cards/](../../services/cards/) | typed cards | v3 不动；只在 V8 critic 评分轴用 cards 命中率 |
| `SlangStore / StyleStore` | [services/slang/store.py,services/style/store.py](../../services/slang/store.py) | learning pipeline 已跑通；persona_fit / mood_fit / risk_tags / output_policy 全部就绪 | v3 激活 mood_fit（按 bus.mood.valence 加权）；扩 domain="catchphrase" 到 learning_normalizer |
| `MemoryConsolidator` | [services/memory/consolidator.py](../../services/memory/consolidator.py) | LLM 5-bucket dry-run → consolidator_candidates.db | v3 不动；产出物给 ContextService.search 自然消费 |
| `LearningNormalizer` | [services/learning_normalizer/normalize.py:55-119](../../services/learning_normalizer/normalize.py) | NFKC + n-gram + rapidfuzz；store 支持 domain 扩展 | v3 扩 domain="catchphrase"；为 catchphrase_pool 提供去重 / 复用率检测 |
| `StickerStore` | [services/media/sticker_store.py:45-235](../../services/media/sticker_store.py) | 目录 + JSON index，SHA-256 去重；`format_prompt_view` 全表 dump 进 prompt | v3 扩 per-group sticker register（最近用过的 sticker hash 30 min TTL；写 RuntimeStateBus per_session） |
| `Persona v2 cutover` | [services/persona/runtime_selector.py,services/llm/prompt_builder.py:71-85](../../services/llm/prompt_builder.py#L71-L85) | B3 灰度 993065015 / 984198159 已切流 | v3 不动 v2 yaml schema；register / catchphrase / sticker register 都是 dynamic block，与 static yaml 正交 |
| 强制颜文字 sticker round | [services/llm/client.py:2236-2257](../../services/llm/client.py#L2236-L2257) | post-LLM 二次 LLM round 添加 sticker | v3 V11 critic-rewrite 复用此模式：候选不达标 → 二次 round 重写 |

### 1.3 10 大缺口的接入图（用户点名 6 + 全量审计补 4）

> 用户点名缺口 6 项（心情 / 每日节奏 / 学习管线表达方式 / 用户好感度 / 上下文与记忆 / 表情包），加上全量审计补充 4 项（thinker / 日期上下文 / chat plugin / reply_workflow 语义前置 gate）。

| 缺口 | 现有载体 | v3 接入点 | 子任务 |
|---|---|---|---|
| **心情** | `MoodEngine` / `MoodProfile` | `MoodEngine` 扩 per-(group, session) cache（U2）；`bus.state.mood.current` 持续可读；StyleProvider/CatchphraseProvider 召回时按 mood.valence 加权（V6） | U2 / V6 |
| **每日节奏** | `ScheduleGenerator` + `TalkSchedule` + `slot.energy` | register 档位读 slot；Humanizer 升级读 slot 调 delay（U3）；DreamAgent 扫 register TTL 状态（U7） | U3 / U7 |
| **学习管线表达方式** | `SlangStore` + `StyleStore` + `learning_normalizer` | 激活 `mood_fit / persona_fit` 死列（U4）；扩 `domain="catchphrase"` 写入 learning_normalizer（U5）；CatchphraseProvider 走 ContextProvider 注入（V3） | U4 / U5 / V3 |
| **用户好感度** | `AffectionProfile` + `engine.build_affection_block` | **不写数值**（2026-05-25 校准 — Part 2/3 §3.4 + Part 4 §3.2 共同推翻 familiarity_score 数值方案，MaiBot 21 个月 5 次回退是反例）：V2 写入 `bus.state.willingness.stage`（5-stage label：disclose/engage/commit/withdraw/cold），V8 critic 用该 stage 调 register_fit 阈值；具体落点延后到 Part 2/3 P3.4 立项；当前为占位 stub | V2 / V8（详见 §13.1） |
| **上下文与记忆** | `EpisodeStore` + `CardStore` + `ContextService.search` + `MemoryConsolidator` | EpisodeStore.list_for_recall register-aware 过滤（V4）；critic 评分用 cards 命中率轴（V8） | V4 / V8 |
| **表情包** | `StickerStore` + 强制颜文字 round | per-group sticker register 写 bus（U8）；critic 用 sticker 复用率轴（V8）；强制颜文字 round 仅在 register=playful 触发（V11 复用其模式） | U8 / V8 / V11 |
| **thinker（预回复思考）** | [services/llm/thinker.py:320-403](../../services/llm/thinker.py#L320-L403) max_tokens=256 输出 JSON {action/retrieve_mode/rewritten_query/thought/sticker/tone}；on_thinker_decision hook 0 订阅 | thinker 决策写 RuntimeStateBus（V13 替代 hook）；thinker 看到 slot/date（V14 `_build_thinker_time_text`）；thinker block 走 PromptBlock 管线（V15 替代 client.py:2107-2124 旁路） | V13 / V14 / V15 |
| **日期上下文** | mood.py:280-281 build_mood_block，ZoneInfo("Asia/Shanghai") hardcoded；client.py:881 `_build_debug_block` datetime.now(CST) | 抽 `services/runtime_clock.py` 单一 source；mood/thinker/debug 均读同一时钟；周末/工作日/节假日特征写 `bus.state.clock.current` per_turn | V14 配套 |
| **chat plugin（bootstrap god-file）** | [plugins/chat/plugin.py:584-1031](../../plugins/chat/plugin.py#L584-L1031) on_startup 一函数装配所有；P0.5 双 close 已识别 | V17 在 on_startup 末尾装配 humanization ModuleContract owner + register classifier worker；不拆 god-file（Part 1.5 / Part 2 处理）；本期仅 +30 行装配 | V17 |
| **reply_workflow 语义前置 gate** | [kernel/router.py:935-947](../../kernel/router.py#L935-L947) evaluate_semantic_gate 0.78 hardcoded threshold；2.2s timeout；不读 affection；与 thinker 同 turn 双 haiku call | semantic_force_threshold 改为读 `bus.state.affection.<uid>.familiarity` + `bus.state.mood.energy` 动态调（V16）；与 thinker 合并 token 不在本期（避免改 prompt 拼接） | V16 |

---

## 2. v3 顶层架构（编排，不并建）

```
┌──────────────────────────────────────────────────────────────────────┐
│                     已有：register 无关层（不动）                       │
│  Schedule(02:00 LLM) → TimeSlot.energy/label → TalkSchedule.now_slot │
│  MoodEngine._compute(slot, count) → MoodProfile (4-dim + label)       │
│  AffectionProfile (per-user, score 0-100, 5 tier)                    │
│  EpisodeStore / CardStore / SlangStore / StyleStore / StickerStore    │
└──────────────────────────────────────────────────────────────────────┘
              ↓ U2/U3/U4/U5/U6 各模块 read-only 写 bus
┌──────────────────────────────────────────────────────────────────────┐
│            v3 引入：RuntimeStateBus 唯一中枢（生产首次启用）             │
│  state.mood.current        per_session  ← MoodEngine.get(group_id)   │
│  state.affection.<uid>     per_user     ← AffectionEngine            │
│  state.register.label      per_session  ← RegisterClassifier (V1)    │
│  state.register.recent_used per_session  ← TTL set (catchphrase 复读) │
│  state.sticker.recent_used per_session  ← TTL set (sticker 复读)     │
│  state.slot.current        per_turn     ← TalkSchedule.now_slot      │
│  state.topic.recent        per_session  ← topic_tracker (V1)         │
│  humanization.last_metrics per_turn     ← V8 scorer trace            │
└──────────────────────────────────────────────────────────────────────┘
              ↓ ContextProvider 协议 (priority/position 写预算)
┌──────────────────────────────────────────────────────────────────────┐
│       v3 新增：3 个 Provider 走 PromptProviderBus（不改 prompt 装配）   │
│  CatchphraseProvider (V3, dynamic, priority=15)                      │
│    - 读 bus.register / bus.mood / bus.topic                          │
│    - 从 SlangStore（domain="catchphrase"）召回，去重 + TTL 抑制         │
│  RegisterProvider (V2, stable, priority=25)                          │
│    - 读 bus.register / bus.affection.tier / slot.energy              │
│    - 渲染当 turn register 行为指令（不写描述、写召回示例 + 统计目标）   │
│  StickerRegisterProvider (U8, dynamic, priority=18)                  │
│    - 读 bus.sticker.recent_used                                      │
│    - 把 30 min 内已用 sticker hash 标"近期已用，避免再发"               │
│  + 已有 StyleProvider / EpisodeProvider / SlangProvider mood-fit 加权  │
└──────────────────────────────────────────────────────────────────────┘
              ↓ PromptBudgetManager 3-bucket 预算自然容纳
┌──────────────────────────────────────────────────────────────────────┐
│        v3 新增：critic-rewrite-loop（复用强制颜文字 round 模式）         │
│  V8 StylometricScorer 本地 5 轴评分（content / register / mood / surface_penalty / sticker_reuse) │
│  V11 critic-rewrite-loop                                             │
│    - 第一轮 LLM 主输出（默认路径，不重采样）                            │
│    - scorer 打分；< threshold 时触发"二次 round"（复用 client.py:2236-2257 模式）│
│    - feature flag `humanization.rewrite_threshold`，默认 -1 = off     │
└──────────────────────────────────────────────────────────────────────┘
              ↓ Humanizer.delay 升级 register-aware
┌──────────────────────────────────────────────────────────────────────┐
│         v3 升级：Humanizer 读 (slot, mood, register, len) 算 delay      │
│  U3 register-aware delay 替换 char_delay 线性算法                      │
└──────────────────────────────────────────────────────────────────────┘
              ↓ BlockTraceStore 全程记账
┌──────────────────────────────────────────────────────────────────────┐
│       v3 复用：所有 PromptBlockCandidate 自动记录到 prompt_block_traces │
│  + V9 humanization_metrics 表（轻量扩列，单表）                         │
└──────────────────────────────────────────────────────────────────────┘
```

**v3 与 v2 的根本差异**：

- v2 三个新模块（persona_assets / register_state / style_resampler）都新建独立存储
- v3 全部状态走 RuntimeStateBus；全部召回走 ContextProvider；全部记账走 BlockTraceStore；只有「集线器（U6 humanization ModuleContract）/ 协议实现（V1 V2 V3 U8）/ 评分器（V8）/ 重采样（V11）」是真正的新代码

---

## 3. 跨子系统统一化清单 U1~U9（用户授权的重构 / 收口）

> 用户原话：「允许在该方案中重构会统一化其它模块」。U 系列是 v3 之前 / 之中要做掉的统一化清债，每条独立可 revert。

| 编号 | 统一化项 | 现状证据 | v3 动作 | 风险 |
|---|---|---|---|---|
| **U1** | segmentation 双实现合并 | `services/llm/segmentation.py` 574 行新模块 0 调用方；生产仍 [services/llm/client.py:359-538 `_reply_segments`](../../services/llm/client.py#L359) | 把 `_reply_segments` 替换为 segmentation.py 的导入；保留行为兼容；`tests/test_reply_segmentation.py` 全绿 | 中：影响所有出群消息；先跑回归 |
| **U2** | MoodEngine per-(group, session) 缓存 | [plugins/schedule/mood.py:125](../../plugins/schedule/mood.py#L125) 全局单 tuple；recent_interaction_count 形参所有调用点恒为 0 | `_cache: dict[tuple[int \| None, str], tuple[MoodProfile, float]]`；`get(group_id, session_id, now)` 新签名；老签名兼容 deprecation warn；接 GroupTimeline.recent_count(60s) 喂 recent_interactions | 中：mood block 文本会变；先跑 shadow 比对 |
| **U3** | Humanizer register-aware | [services/humanizer.py:36-43](../../services/humanizer.py#L36-L43) char_delay 线性 | 新签名 `delay(text, *, group_id=None, register=None, slot=None, mood=None)`；register=quiet+slot.energy<0.3+mood.energy<0.4 → 1.5x；register=playful → 0.7x；老签名走默认 1.0x | 低：纯延迟；先跑感官回归 |
| **U4** | StyleStore.mood_fit / persona_fit 激活 | [services/style/store.py:1826-1843 `_expression_relevance`](../../services/style/store.py#L1826-L1843) 只比文本，两数值死列 | `_expression_relevance` 加 `+ 0.3 * mood_alignment(expression.mood_fit, bus.mood.valence) + 0.2 * persona_alignment(expression.persona_fit, persona_id)`；权重灰度可调 | 低：召回排序变；style 已有 fallback |
| **U5** | learning_normalizer domain="catchphrase" | [services/learning_normalizer/store.py](../../services/learning_normalizer/store.py) 已支持 domain 扩展 | 注册新 domain；为 catchphrase_pool 提供"是否复用 / 复用率统计"；admin SPA 已有的 learning 视图自动多一个 domain tab | 极低：只加表行 |
| **U6** | humanization ModuleContract（RuntimeStateBus 首个生产 owner） | [services/system_module/catalog.py:70-293](../../services/system_module/catalog.py) 27 槽位声明完整；0 模块挂载 | 新增 `services/humanization/` 目录；声明 ModuleContract owner 拥有 `state.register.*` / `state.sticker.recent_used` / `humanization.last_metrics`；wiring 走 PluginContext 装配 | 中：RuntimeStateBus 首次跑生产路径；先单元锁 owner enforcement |
| **U7** | DreamAgent 接入 register / sticker TTL 清理 | [plugins/dream/plugin.py:274-281](../../plugins/dream/plugin.py) 现仅清 sticker LRU + memo | 加一段：清 RuntimeStateBus 内 30 min 以上未访问的 per_session 槽位（不影响 per_user / persistent） | 低：DreamAgent 默认 disabled |
| **U8** | StickerStore per-group register | [services/media/sticker_store.py:215-235 `format_prompt_view`](../../services/media/sticker_store.py) 全表 dump 无去重 | 新增 `StickerRegisterProvider`（dynamic, priority=18）：读 `bus.state.sticker.recent_used`，对全表中 30 min 已用的 sticker 标记"近期已用"提示词 | 极低：纯提示词，sticker store 不动 |
| **U9** | 前端 Admin SPA 两个面板（Part 2 范围）| 现 admin 无 register / mood 实时观测面板 | **不在 Part 1 范围内**；列出仅作 backlog，与 Part 2 节奏 / 输入感知一同推进 | — |
| **U10** | RuntimeClock 单源化（日期/时区/工作日特征） | mood.py:280-281 与 client.py:881 各自实例化 ZoneInfo("Asia/Shanghai")；thinker 看不到 slot/date | 新增 `services/runtime_clock.py` ≤80 行；导出 `now_cst()` / `slot_features()` / `is_weekend()` / `is_holiday(stub)`；mood / thinker / debug / scheduler 改读统一 API；写 `bus.state.clock.current` per_turn | 低：纯只读时间，不影响调度 |
| **U11** | thinker 输出走 RuntimeStateBus（替代 0 订阅 hook） | thinker.py 发 `on_thinker_decision` hook，PluginBus 0 subscriber；下游 client.py:2107-2124 旁路读 thinker 返回 dict | thinker 决策（action / tone / sticker / retrieve_mode）写 `bus.state.thinker.last_decision` per_turn；hook 删除（P0.8）；下游既有旁路保留为 fallback（V15 才迁移） | 中：thinker 是 hot path；先单元锁 owner enforcement |
| **U12** | reply_workflow.semantic gate 阈值动态化 | router.py:935-947 threshold=0.78 hardcoded；不读 affection / mood | gate 调用前读 `bus.state.affection.<uid>.familiarity` + `bus.state.mood.energy`；familiarity > 0.6 → threshold -0.1（更易触发回复）；mood.energy < 0.3 → threshold +0.05（更不容易抢话）；上下界 [0.6, 0.85] | 中：直接影响该不该回，先 shadow 比对 hardcoded vs 动态 |
| **U13** | thinker 与 reply_workflow 语义 gate 共享 token 预算（仅观测） | 同 turn 两次 haiku call（router.py:935 + thinker.py:320）：用户每发一条消息群里就触发 2 个 haiku call | 本期**不合并**；只在 BlockTraceStore 记录"同 turn 双 haiku"标记，便于 Part 2 评估合并价值；合并是 Part 2 V20+ 范围 | 极低：只加 trace 行 |

U1~U8 在 V0~V12 之中或之前完成，每条独立 commit、独立可 revert；U10~U13 在 V13~V17 主线中或之前完成；U9 显式推迟到 Part 2。

---

## 4. v3 设计逻辑（按用户点名 6 缺口逐一回应）

### 4.1 心情（MoodEngine）

- **现状**：MoodProfile 4 维已落，全局 cache 单 tuple，`build_mood_block` 已注入 prompt
- **v3 动作（U2）**：cache 改 `dict[(group_id, session_id), (profile, ts)]`；`recent_interaction_count` 真接 GroupTimeline 60s 内消息数
- **v3 写出**：`bus.state.mood.current = {valence, energy, openness, tension, label, slot_id}` per_session，TTL=30 min（与 cache 同步过期）
- **v3 读入**：StyleProvider mood_fit 加权（U4）；CatchphraseProvider 召回过滤（V3）；StylometricScorer mood 轴（V8）
- **不引入新存储**

### 4.2 每日节奏（Schedule + TalkSchedule）

- **现状**：02:00 CST LLM 生成 TimeSlots；TalkSchedule.now_slot 给出 `slot.energy / slot.label`
- **v3 动作**：不改生成器；`slot` 写 `bus.state.slot.current` per_turn
- **v3 读入**：Humanizer 升级（U3）按 slot.energy 调 delay 倍率；RegisterClassifier（V1）把 slot 当一维特征；DreamAgent（U7）扫 per_session TTL
- **关键**：之前 slot.energy 只影响 mood，没有影响 humanizer 与 register；v3 让节奏真正穿透到出口延迟与语气档位

### 4.3 学习管线表达方式（Slang + Style + learning_normalizer）

- **现状**：SlangStore / StyleStore / learning_normalizer 已跑通；persona_fit / mood_fit / risk_tags / output_policy 全部就绪；admin SPA learning 视图已有
- **v3 动作（U4 + U5 + V3）**：
  - U4：`_expression_relevance` 激活 mood_fit / persona_fit 加权
  - U5：learning_normalizer 加 domain="catchphrase"
  - V3：CatchphraseProvider 走 ContextProvider 协议；从 SlangStore（domain="catchphrase"）召回；按 mood / register 过滤；走 PromptProviderBus 进 dynamic bucket
- **不新建 colloquialism.yaml**：catchphrase 池就是 SlangStore 一张已有的表，admin 已有界面增删改查

### 4.4 用户好感度（AffectionProfile）

- **现状**：[plugins/affection/models.py:23-46](../../plugins/affection/models.py) 5 tier + mood_bonus_valence；`build_affection_block` 已注入；`record_interaction` 在 on_post_reply 写盘
- **v3 动作（V2 + V8）**：
  - 扩 `AffectionProfile.familiarity_score`：短期亲密度（30/60 min 内消息条数 + 互动正反馈），写 `bus.state.affection.<uid>.familiarity` per_user，TTL=60 min
  - V2 RegisterProvider 决档位时读 `affection.tier + familiarity`：tier=close + familiarity > 0.6 → casual_close；tier=stranger → polite_distant
  - V8 critic 评分 `register_fit` 阈值按 affection 调
- **不新建关系存储**

### 4.5 上下文与记忆（Episode + Card + ContextService）

- **现状**：EpisodeStore 5-state + per_group MAX 50 + `list_for_recall(group_id, limit=3)`；CardStore typed cards；ContextService.search RRF 聚合
- **v3 动作（V4 + V8）**：
  - V4 EpisodeProvider register-aware：list_for_recall 后按 `bus.register.label` 过滤 episode tags（quiet 模式过滤掉 high-energy episodes）
  - V8 critic 评分 `card_alignment` 轴：候选语义 vs CardStore 召回卡片的 cosine（避免说出与 facts 冲突的内容）
- **不新建检索路径**

### 4.6 表情包（StickerStore + 强制颜文字 round）

- **现状**：StickerStore 目录 + JSON index 全表 dump；client.py:2236-2257 强制颜文字 round（playful 时触发）
- **v3 动作（U8 + V8）**：
  - U8 StickerRegisterProvider：30 min 内已用 sticker hash 写 bus per_session；prompt 注入"近期已用，建议换"提示词
  - V8 critic `sticker_reuse` 轴：候选若包含已用 sticker，扣分
  - 强制颜文字 round 触发条件改为 `register==playful AND affection.tier >= 3 AND mood.energy > 0.5`，避免在陌生群 / 低能量时段强行颜文字
- **不新建 sticker 存储**

### 4.7 thinker（预回复思考）

- **现状**：[services/llm/thinker.py:320-403](../../services/llm/thinker.py#L320-L403) 输出 JSON {action / retrieve_mode / rewritten_query / thought / sticker / tone}；`on_thinker_decision` PluginBus hook 0 订阅；client.py:2107-2124 旁路读返回值拼 thinker block
- **v3 动作（U11 + V13 + V15）**：
  - U11：thinker 决策写 `bus.state.thinker.last_decision` per_turn；删除 0 订阅 hook
  - V13：tone / retrieve_mode 与 RegisterClassifier 协商——thinker 给"主观倾向"，classifier 给"客观分类"，两者写 bus 同一槽 owner=thinker（先写）+ owner=classifier（覆盖最终值）；RegisterProvider 读最终值
  - V15：thinker block 改走 PromptProviderBus（`ThinkerProvider`，dynamic, priority=12）；本期 V15 默认 off（feature flag），生产仍走旁路；shadow 验证后 Part 1 后期切流
- **不新建 thinker 模块**

### 4.8 日期上下文（RuntimeClock 单源）

- **现状**：mood.py 写「【当前时间】...」每 turn；client.py:_build_debug_block 各自 datetime.now(CST)；thinker prompt 看不到 slot/date；周末 / 节假日特征到处 hardcoded
- **v3 动作（U10 + V14）**：
  - U10：抽 `services/runtime_clock.py` 单源；导出 `now_cst()` / `slot_features(now)` / `is_weekend(now)` / `is_holiday(stub_calendar)`
  - V14：mood / thinker / debug 改读统一 API；thinker prompt 头部加 `_build_thinker_time_text(slot, weekend, holiday)` 让 thinker 知道是工作日 23:00 还是周末 23:00（前者更安静、后者更活跃）；写 `bus.state.clock.current` per_turn
- **不引入新时间存储**

### 4.9 chat plugin（bootstrap god-file）

- **现状**：[plugins/chat/plugin.py:584-1031](../../plugins/chat/plugin.py#L584-L1031) on_startup 单函数装配所有；P0.5 已识别 `card_store.close()` 双调用
- **v3 动作（V17）**：
  - 不拆 god-file（拆分 blast radius 大，列入 Part 1.5 / Part 2）
  - V17：在 on_startup 末尾追加 30 行装配 humanization ModuleContract owner + RegisterClassifier worker；仍走单一 PluginContext；命名空间隔离避免与 chat 主流程交叉
- **god-file 拆分**留 Part 1.5 单独立项

### 4.10 reply_workflow 语义前置 gate

- **现状**：[kernel/router.py:935-947](../../kernel/router.py#L935-L947) `evaluate_semantic_gate(haiku, threshold=0.78, timeout=2.2s)`；threshold hardcoded；与 thinker 同 turn 两个 haiku call（U13 已识别）
- **v3 动作（U12 + V16）**：
  - U12：threshold 改读 `bus.state.affection.<uid>.familiarity + bus.state.mood.energy`；动态范围 [0.6, 0.85]
  - V16：feature flag `humanization.semantic_gate_dynamic` 控制；默认 off，shadow 比对 hardcoded vs 动态后切流
- **token 预算合并**（U13）只加 trace 不动行为；合并是 Part 2 V20+ 范围

---

## 5. 子任务 V0 ~ V12（编号区别于 v2 P1.x）

> 命名约定：U 系列 = 统一化重构，V 系列 = humanization 主线交付。每条独立 commit。

### 5.1 任务总览

| 编号 | 任务 | 类型 | 依赖 | 关键产物 | 单测 |
|---|---|---|---|---|---|
| **U1** | segmentation 双实现合并 | 重构 | — | `_reply_segments` 替换为 segmentation.py 导入 | `tests/test_reply_segmentation.py` 全绿 |
| **U2** | MoodEngine per-(group, session) cache + recent_count 接通 | 重构 | — | mood.py:115-225 改造；老签名 deprecation warn | `tests/test_mood_engine.py` +6 |
| **U3** | Humanizer register-aware 升级 | 重构 | U2 | humanizer.py 76 行新签名；老签名兼容 | `tests/test_humanizer_register.py` +5 |
| **U4** | StyleStore mood_fit / persona_fit 激活 | 重构 | U2 | `_expression_relevance` 加权重；style_provider 召回排序变 | `tests/test_style_relevance.py` +4 |
| **U5** | learning_normalizer domain="catchphrase" | 扩展 | — | normalize.py / store.py 加 domain；admin SPA 自动多 tab | `tests/test_learning_normalizer_catchphrase.py` +3 |
| **U6** | humanization ModuleContract（RuntimeStateBus 首生产 owner） | 新增 | — | `services/humanization/{__init__.py,contract.py,state.py}` ≤180 行；PluginContext 装配 | `tests/test_humanization_contract.py` +5（owner enforcement / TTL / cancel-path） |
| **U7** | DreamAgent 接 register / sticker TTL 清扫 | 扩展 | U6 | dream/plugin.py +30 行 | `tests/test_dream_humanization_cleanup.py` +3 |
| **U8** | StickerRegisterProvider | 新增 | U6 | `services/block_trace/sticker_register_provider.py` ≤90 行（仿 slang_provider.py） | `tests/test_sticker_register_provider.py` +4 |
| **V0** | `[humanization]` config 段 + flag schema | 新增 | — | kernel/config.py +30 行；6 字段全默认 off | `tests/test_humanization_config.py` +6 |
| **V1** | RegisterClassifier（haiku one-shot, 5 轮窗口） | 新增 | U6 + V0 | `services/humanization/classifier.py` ≤140 行；写 `bus.state.register.label` | `tests/test_register_classifier.py` +6（happy / haiku 失败兜默认 / 多群隔离 / cancel-path / TTL / classifier_confidence） |
| **V2** | RegisterProvider（ContextProvider, stable, prio=25） | 新增 | U6 + V1 | `services/block_trace/register_provider.py` ≤95 行；读 register / affection / slot；渲染统计目标 + 召回 1 条示例 | `tests/test_register_provider.py` +5 |
| **V3** | CatchphraseProvider（ContextProvider, dynamic, prio=15） | 新增 | U5 + V1 | `services/block_trace/catchphrase_provider.py` ≤100 行；从 SlangStore(domain=catchphrase) 召回 + TTL 抑制 | `tests/test_catchphrase_provider.py` +5 |
| **V4** | EpisodeProvider register-aware 过滤 | 重构 | V1 | episode_provider.py 召回后按 register tag 过滤 | `tests/test_episode_provider_register.py` +3 |
| **V5** | AffectionEngine.familiarity_score 扩展 | 扩展 | U6 | affection/engine.py +25 行；写 bus per_user TTL=60min | `tests/test_affection_familiarity.py` +4 |
| **V6** | SlangProvider mood-fit 加权 | 重构 | U2 + U4 | slang_provider.py 召回过滤加 mood 维 | `tests/test_slang_provider_mood.py` +3 |
| **V7** | catchphrase_pool 种子（仅 30 条，从群历史 EpisodeStore 抽） | 数据 | U5 | `scripts/dev/seed_catchphrase_pool.py` 一次性脚本；写入 SlangStore | 跑一次入库；不进 commit |
| **V8** | StylometricScorer 5 轴（content / register / mood / surface / sticker_reuse） | 新增 | U6 + V1 | `services/humanization/scorer.py` ≤200 行；本地 regex+词典+rapidfuzz；零 LLM cost | `tests/test_humanization_scorer.py` +12（五轴各 2 + 阈值 + cancel-path） |
| **V9** | BlockTraceStore 扩 humanization_metrics 表 | 扩展 | U6 + V8 | block_trace/store.py +40 行；admin SPA backlog Part 2 看板 | `tests/test_humanization_metrics_persist.py` +3 |
| **V10** | Humanizer 真接 mood / register / slot 入参 | 扩展 | U3 + V1 | scheduler/_send_to_group 装配；走 PluginContext | `tests/test_humanizer_runtime.py` +3 |
| **V11** | critic-rewrite-loop（仅 score < threshold 时触发，复用 client.py:2236-2257 模式） | 新增 | V8 + V0 | `services/llm/client.py` 插入点 ~2204；二次 LLM round；feature flag `humanization.rewrite_threshold` 默认 -1 = off | `tests/test_llm_client_rewrite.py` +4 |
| **V12** | 灰度 + 指标 + 文档收口 | 收口 | U1~U13 + V0~V11 + V13~V17 | `scripts/dev/measure_humanization.sh` + 24h baseline + 本文 §10 状态表 + maintenance-log + migration §12 第 7 行 ⏳→✅ | — |
| **V13** | thinker.last_decision 写 RuntimeStateBus（U11 落地点） | 重构 | U6 + U11 | thinker.py:320-403 末尾 +15 行写 bus；删除 0 订阅 `on_thinker_decision`；PluginBus.fire 那一行 grep 0 caller 后删 | `tests/test_thinker_runtime_state.py` +5（happy / fail fallback / TTL / cancel-path / 多 group 隔离） |
| **V14** | RuntimeClock 单源 + thinker time_text | 新增 + 重构 | U10 | 新建 `services/runtime_clock.py` ≤80 行；mood.py / client.py:881 改 import；thinker.py 增 `_build_thinker_time_text(slot, weekend)` 注入 prompt；写 `bus.state.clock.current` per_turn | `tests/test_runtime_clock.py` +6（CST tz / weekend / holiday stub / slot_features / 多源一致性 / cancel-path） |
| **V15** | ThinkerProvider 走 PromptBlock 管线（旁路迁移） | 新增 | V13 + V0 | `services/block_trace/thinker_provider.py` ≤90 行（dynamic, priority=12）；feature flag `humanization.thinker_provider_enabled` 默认 off；旁路保留作 fallback | `tests/test_thinker_provider.py` +4 |
| **V16** | semantic_gate threshold 动态化 | 重构 | V5 + V0 | router.py:935-947 改读 `bus.state.affection.<uid>.familiarity + bus.state.mood.energy`；feature flag `humanization.semantic_gate_dynamic` 默认 off；shadow log 写比对 | `tests/test_semantic_gate_dynamic.py` +5（hardcoded fallback / familiarity 高 / mood 低 / 无 affection / cancel-path） |
| **V17** | chat plugin on_startup 装配 humanization | 扩展 | U6 + V1 + V13 | chat/plugin.py:on_startup 末尾 +30 行装配 ModuleContract owner + RegisterClassifier worker；命名空间隔离不动现有 chat 主流程 | `tests/test_chat_plugin_humanization_wire.py` +3 |

合计：**新增代码估算 ≤1200 行**（不含测试，含 V13~V17 + Part 0 净删除 ~200 行抵消），**新增测试 ≥110 条**，**修改文件 ≈18**，**新建文件 ≈12**。

### 5.2 提交节奏（8 commits + Part 0 前置）

每个 commit 落地后**等用户显式说 "commit" 才 `git commit`**——v3+ 跨多个生产模块，比 v2 更需谨慎。

| commit | 范围 | 风险 |
|---|---|---|
| **C0a** | Part 0 屎山清债：P0.1 + P0.2 + P0.3 + P0.5 + P0.6 + P0.8（纯删除，配置死链 + 0 caller dead code + 0 订阅 hook） | 低：不引入新行为 |
| **C0b** | Part 0 修复：P0.4（mood 双注入）+ P0.7（BackupScheduler 单实例） | 中：行为有变；先 shadow 后 24h 验证 |
| **C1** | U1（segmentation 合并）+ U2（MoodEngine cache）+ U3（Humanizer 升级）+ U4（StyleStore 激活）+ U5（normalizer domain）+ U10（RuntimeClock） | 6 项重构，单 commit 内 git diff 仍可一眼看清；每项独立可 revert |
| **C2** | U6 humanization ModuleContract + V0 config 段 + V1 RegisterClassifier + V5 familiarity + U11 thinker → bus + V13 thinker.last_decision + V14 thinker time_text | RuntimeStateBus 首次跑生产路径；这是 v3 最关键的 commit |
| **C3** | V2 RegisterProvider + V3 CatchphraseProvider + V4 EpisodeProvider register-aware + V6 SlangProvider mood-fit + U8 StickerRegisterProvider + V15 ThinkerProvider（默认 off） | 6 个 ContextProvider 改 / 增；进 PromptProviderBus + BudgetManager 自然容纳 |
| **C4** | V8 StylometricScorer + V9 humanization_metrics + V10 Humanizer runtime 装配 + V11 critic-rewrite-loop + U12 + V16 semantic_gate 动态化 + U13 双 haiku trace | scorer 是本地零 LLM；rewrite-loop 默认 off；可独立验证 |
| **C5** | V7 种子语料 + V17 chat plugin 装配 + 灰度配置（仅 993065015 / 984198159 中一群启用） | 数据 + config.json + 启动钩子 |
| **C6** | V12 文档收口 + measure_humanization.sh + maintenance-log + migration §12 ⏳→✅ + U7 DreamAgent 清扫 | 文档 / 脚本 |

---

## 6. 测试计划

### 6.1 新增单测分布

| 文件 | 条数 | 关键覆盖 |
|---|---|---|
| `tests/test_humanization_config.py` | 6 | TOML / JSON round-trip / 默认 off / 非法字段 graceful |
| `tests/test_humanization_contract.py` | 5 | RuntimeStateBus owner enforcement / per_turn 清扫 / cancel-path / 多 owner 写同槽 raise / TTL |
| `tests/test_register_classifier.py` | 6 | haiku happy / 失败兜默认 / 多 group 隔离 / cancel-path / classifier_confidence / TTL |
| `tests/test_register_provider.py` | 5 | 候选生成 / register=quiet 路径 / register=playful 路径 / affection.tier 联动 / position=stable + priority=25 进 budget |
| `tests/test_catchphrase_provider.py` | 5 | 召回顺序 / TTL 复读抑制 / mood 加权 / 非命中空候选 / cancel-path |
| `tests/test_episode_provider_register.py` | 3 | quiet 过滤 high-energy / playful 全召回 / register 缺失 fallback |
| `tests/test_affection_familiarity.py` | 4 | TTL=60min / on_post_reply 累积 / 多 user 隔离 / mood_bonus 同步 |
| `tests/test_slang_provider_mood.py` | 3 | 高 valence + low valence 排序差 / mood_fit=0.5 中性 / 数据库无 mood_fit fallback |
| `tests/test_humanization_scorer.py` | 12 | 五轴各 2 条 + 阈值短路 + cancel-path |
| `tests/test_humanization_metrics_persist.py` | 3 | sqlite 写入 / Schema 兼容 / cancel-path 不污染 |
| `tests/test_humanizer_runtime.py` | 3 | register=quiet 1.5x / register=playful 0.7x / register=None 默认 1.0x |
| `tests/test_llm_client_rewrite.py` | 4 | 默认 off 路径不变 / threshold 触发二次 round / 二次 round 失败兜底原始 / cancel-path |
| `tests/test_humanizer_register.py` | 5 | U3 老签名兼容 / 新签名各档位 / slot.energy 影响 / mood.tension 影响 / cancel-path |
| `tests/test_mood_engine.py`（新增 6） | 6 | per-(group, session) 隔离 / 老签名 deprecation / recent_count 真接 GroupTimeline / cancel-path / cache 过期 / 多群并发 |
| `tests/test_style_relevance.py` | 4 | mood_fit 加权 / persona_fit 加权 / 数据库 NULL fallback / 权重灰度可调 |
| `tests/test_learning_normalizer_catchphrase.py` | 3 | domain 注册 / 复用率统计 / 跨 domain 隔离 |
| `tests/test_dream_humanization_cleanup.py` | 3 | per_session TTL 30min 清扫 / per_user 不动 / DreamAgent disabled 不报错 |
| `tests/test_sticker_register_provider.py` | 4 | 30min 已用标注 / TTL 过期重新可用 / 全表 dump 不变 / cancel-path |
| `tests/test_thinker_runtime_state.py`（V13） | 5 | thinker happy / fail fallback / TTL / cancel-path / 多 group 隔离 |
| `tests/test_runtime_clock.py`（V14 + U10） | 6 | CST tz / weekend / holiday stub / slot_features / 多源一致性 / cancel-path |
| `tests/test_thinker_provider.py`（V15） | 4 | flag off 路径不变 / flag on 走 PromptBlock / 旁路 fallback / cancel-path |
| `tests/test_semantic_gate_dynamic.py`（V16 + U12） | 5 | hardcoded fallback / familiarity 高 / mood 低 / 无 affection / cancel-path |
| `tests/test_chat_plugin_humanization_wire.py`（V17） | 3 | on_startup 装配成功 / 命名空间隔离 / 重启幂等 |
| Part 0 单测 | 8 | P0.1~P0.8 各 1 条 grep / 配置 round-trip / shadow 比对 / 24h backup 单跑验证 |

合计：**≥110 条新测试**。

### 6.2 D2 cancel-path 显式

| 模块 | cancel 风险 | 测试要求 |
|---|---|---|
| `RegisterClassifier` | haiku 调用被 cancel | `pytest.raises(asyncio.CancelledError)`；断言 `bus.state.register.label` 不变 / classifier_confidence 不写脏 |
| `StylometricScorer` | rapidfuzz 长查 | `wait_for(timeout=0)` → 断言 `humanization.last_metrics` 不污染 |
| `CatchphraseProvider` | SlangStore 查询 cancel | `bus.state.register.recent_used` 不写脏 |
| `V11 critic-rewrite-loop` | 二次 round cancel | 主响应不退化、不影响主回复回写 |

### 6.3 D1 同模式扫描

每 commit 后：

```bash
grep -rn 'register_state\|RegisterClassifier\|CatchphraseProvider\|StickerRegisterProvider\|StylometricScorer\|humanization\.' \
  --include='*.py' .
```

预期命中：`services/humanization/`、`services/block_trace/{register,catchphrase,sticker_register}_provider.py`、`services/llm/{prompt_builder,client}.py`、`kernel/{config,types,router}.py`、`services/system_module/`、`tests/`。其它（`bot.py` / `admin/` / `plugins/` 除 humanization 装配点外）零命中。

### 6.4 灰度 24h 真实指标（measure_humanization.sh）

24h 样本 ≥ 200 条 group reply。分项：

| 指标 | baseline 目标（基于研究） | 采集方式 |
|---|---|---|
| em-dash 率（per 1K chars） | < 4 | regex |
| ☆ ♪ ✦ 装饰符出现 | 0 | regex |
| 句末 ～ | < 0.5 / 100 句 | regex |
| 句末有句号率 | 20%~30% | 标点统计 |
| 平均句长 | 12~20 字 | 字符数 |
| 句长方差 | ≥ 50 | std |
| catchphrase 命中率 | ≥ 30% | learning_normalizer domain="catchphrase" 复用率 |
| 同质化分（成对 Rouge-L） | < 0.18 | 邻 50 条对比 |
| 重复 catchphrase 率（30min） | < 15% | bus.state.register.recent_used 命中 |
| register fit（admin vs 群） | 句长差 ≥ 5 字 | by-group 分组统计 |
| sticker 复读率（30min） | < 10% | bus.state.sticker.recent_used 命中 |
| mood-text alignment | 高 valence 时短句 emoji 多 | scorer 五轴 mood 分项 |
| affection-register match | tier=stranger → polite_distant 主导 | by-user 分组统计 |
| 用户主观验收 | 「不再用力过猛」+「群 vs admin 有差异」 | 用户判断 |

未达标项独立追踪 `docs/tracking/humanization-baseline-tuning-execution.md`。

### 6.5 D5 全量 pytest

```bash
pkill -9 -f pytest
uv run pytest -q
```

预期：1566（B3 后基线）+ ≥ 110 = ≥ 1676 passed / 8 skipped。

---

## 7. 灰度方案（5 阶段）

| 阶段 | 时长 | enabled_groups | runtime_consume 之外的 flag | 监控重点 |
|---|---|---|---|---|
| 阶段 0 | 持续 | `[]`（全 off） | 全 off | merge 后无 ERROR / 测试全绿 / 全量 v1 路径不变 |
| 阶段 1 | 24h | `["993065015"]` | `humanization.context_providers=true`；`rewrite_threshold=-1`（off） | em-dash 率 / catchphrase 命中 / register fit / 用户主观 |
| 阶段 2 | 24h | `["993065015","984198159"]` | 同上 | 双群 register 差异 |
| 阶段 3 | 24h | 同上 | `rewrite_threshold=0.4`（开 critic-rewrite-loop，触发率应 <5%） | 二次 round 触发率 / 主回复延迟回归 |
| 阶段 4 | 持续 | 全 allowed_groups | 全 on | 同质化分 / 长期 rapport 演化 / 用户最终验收 |

每阶段 24h 用 §6.4 脚本采样 → 阀值未过 → 不进下阶段；连续 7 天达标 → Part 1 出口。

紧急回滚（30 秒）：

```bash
# config/config.json:
#   "humanization": {"context_providers": false, "rewrite_threshold": -1, "register_aware_humanizer": false}
docker compose restart bot
```

---

## 8. 回滚与失败模式（细分）

| 现象 | revert 范围 | 命令 |
|---|---|---|
| critic-rewrite-loop 延迟暴增 | flag off | json `humanization.rewrite_threshold=-1` + restart |
| RegisterClassifier 频繁误判 / haiku 限流 | classifier 失败兜默认 register；classifier_confidence < 0.3 时不写 bus | json `humanization.register_classifier_enabled=false` + restart |
| CatchphraseProvider 召回不准 / 过 budget | flag off | json `humanization.context_providers=false` + restart |
| MoodEngine per-key cache 内存膨胀 | DreamAgent 清扫 + cache 容量上限 | DreamAgent enable + 容量阈值 hot reload |
| 全 v3 失败 | revert C2~C4 commits | `git revert <c2>..<c4>` + restart；C1（U1~U5 重构）保留 |

**禁止依赖项**：

- V11 critic-rewrite-loop 不依赖 V1 RegisterClassifier（scorer 用 register=None 仍有 4/5 轴可打分）
- V3 CatchphraseProvider 不依赖 V2 RegisterProvider（catchphrase 召回退化为 mood-only 加权）
- V2 RegisterProvider 依赖 V1 但 V1 失败时 RegisterProvider degrade 到固定 register=neutral
- U6 humanization ModuleContract 是其它一切的前提；U6 失败 = 全 v3 阻断

---

## 9. 与 v2 的差异声明

### 9.1 不再并建 persona_assets / register_state / style_resampler 三个独立模块

v2 三模块都新建独立存储：

- v2 `services/persona_assets/` (loader + 5 yaml 文件) → v3 catchphrase 池就是 SlangStore domain="catchphrase"，不新建 yaml
- v2 `services/register_state/` (store.py + sqlite WAL) → v3 register 状态写 RuntimeStateBus，不新建 sqlite
- v2 `services/style_resampler/` (resampler 默认 N=2 draft) → v3 默认主回复仍单次生成，仅 score < threshold 时触发"二次 round"（成本节省 ≥50%）

### 9.2 不再单独新增 `services/style_resampler/`

draft + critic 完整 N=2 draft 是 v2 想象中的奢侈：每 turn LLM cost +1。v3 改成 critic-only：第一轮主输出 + 本地零 LLM scorer 打分 + 仅低分时触发二次 LLM round。这是基于 §1.2 强制颜文字 sticker round 的成熟 pattern（[client.py:2236-2257](../../services/llm/client.py#L2236-L2257)）——已在生产跑过的 post-LLM 二次 round 模式。

### 9.3 不再用 catchphrase_pool.yaml 作为静态文件

v2 静态 yaml + manifest hash 缓存的方案是 SillyTavern lorebook 的 mock。v3 直接写 SlangStore，admin SPA 已有 learning 视图（learning_normalizer 已支持 domain 扩展）→ 用户可在线增删改 catchphrase，零工程开销。

### 9.4 复用研究锚点（§0）

研究（Jain / Liu / Padmakumar / Pennebaker / Schwartz / 中文 IM 语料）保留——用户原话「目前的研究结果得到初步肯定」。v3 把研究的"内容驱动 / register 感知 / 文体重采样"三轴落到现有子系统，而不是另起炉灶。

---

## 10. 出口标准

全部满足才转入 Part 2：

- [x] P0.1 ~ P0.8 + U1 ~ U13（不含 U9 推迟 Part 2）+ V0 ~ V17（V12 收口）工程项已按派单文档收口；P0.3/P0.4 为证据不成立撤销项，P0.1 为订正保留项，详见 [执行文档 §6](./omubot-humanization-part1-execution.md#6-当前状态执行者每完成一条把--改--等验收验收后我改-)。
- [ ] `uv run pytest -q` ≥ 1676 passed / 8 skipped
- [ ] `uv run ruff check services/humanization services/block_trace/{register,catchphrase,sticker_register}_provider.py services/llm/prompt_builder.py services/llm/client.py services/humanizer.py kernel/config.py kernel/types.py plugins/affection plugins/schedule plugins/dream tests/test_humanization* tests/test_register* tests/test_catchphrase* tests/test_sticker_register* tests/test_affection_familiarity*` clean
- [ ] `uv run pyright services/humanization` 0 errors
- [ ] §6.4 灰度指标连续 7 天 ≥ 10/14 项达标；当前仅灰度-1 配置已落库，24h/7d 指标尚未形成。
- [ ] 用户主观验收：「不再用力过猛」+「admin 群 vs 普通群 register 差异可感」+「mood / 节奏在文本上有体感差」
- [x] `maintenance-log.md` v3 落地条目五段齐（2026-05-25 Humanization Part 1 Wave 7/8 收口）。
- [x] migration §12 第 7 行「Part 1 humanization」已新增并标 ✅ 工程收口；运行时灰度仍待 24h 指标。
- [ ] 紧急回滚演练成功（json 单字段切换 30 秒内回 v1 行为）
- [ ] D1 同模式扫描通过（§6.3 仅命中预期路径）
- [ ] D2 cancel-path 全部锁住（§6.2）
- [ ] D5 pytest 防孤儿流程已跑

---

## 11. 当前状态

| 编号 | 状态 | 落地证据 |
|---|---|---|
| 派单执行文档 | ✅ 已成为权威执行源 | [omubot-humanization-part1-execution.md](./omubot-humanization-part1-execution.md) 已逐步记录 Wave 0~8 的执行前拆分、风险、完成记录和验证命令 |
| P0 / U / V 工程项 | ✅ 工程收口 | 执行文档 §6 中 P0.0、P0.2、P0.5~P0.8、U1~U13、V0~V17 均有落地证据；P0.1/P0.3/P0.4 是证据订正/撤销项 |
| V7 catchphrase seed | ✅ 脚本完成，数据源不足 | `scripts/dev/seed_catchphrase_pool.py` 已落地；宿主/容器 live 执行均 `selected=0 written=0`，因为 live `EpisodeStore` 当前 `episodes_total=0`，未伪造种子 |
| 灰度-1 | 🟡 配置已落库，待上线观察 | `config/config.json` / TOML 已切 `runtime_groups=["993065015"]`、`context_providers=true`、`register_classifier=true`；rewrite/sticker/thinker/dynamic gate 仍 off；待 rebuild/restart 后跑 24h 出口矩阵 |
| 灰度-2 / 灰度-3 | ⏸ 阻塞 | 灰度-1 未满 24h 且出口指标未通过前，不扩 `984198159`，不启 `rewrite_threshold=0.4` |
| V12 measure | ✅ 度量入口完成 | `scripts/dev/measure_humanization.sh` 已落地；当前输出：`humanization_metrics` 表缺失、catchphrase=0、episode=0、U13 paired=0、rollout gate pending |
| 剩余验收 | ⏳ 待人工/时间窗口 | 全量 pytest、7 天指标、用户主观验收、紧急回滚演练仍需上线窗口执行 |

**当前口径**：本文是方案总览；实际执行状态以派单文档为准。Part 1 工程项已收口到灰度-1准备态，但尚未宣称灰度通过。用户原话「我最终做上线前最后验收」仍保留为最终放量门槛。

---

## 12.5 派单执行入口

> 2026-05-25 自审本文后，把可分发的并列执行清单落到独立文档（含 5 处证据订正、wave 依赖图、Provider priority 校准前置任务、24h 灰度指标矩阵）。
>
> 派单文档：[omubot-humanization-part1-execution.md](./omubot-humanization-part1-execution.md)
>
> **本文与派单文档冲突时，以派单文档 §1 订正为准**——其中包括：
>
> - P0.3 / P0.4 撤销（grep 实证证据不成立）
> - P0.6 升风险等级（GroupSendQueue 有 8 条测试覆盖，需先评估）
> - P0.7 文案订正（双实例化都在 bot.py，不是 admin + plugin）
> - P0.8 改语义（hook 保留作扩展点）
> - Provider priority 数值待 P0.0 校准（slang_provider=40 / style_provider=42-45 vs 本文规划的 12-25 数值与现有冲突）

---

## 13. 与 Part 2 / Part 3 / Part 4 / Part 5 / Part 6 的边界

> 2026-05-25 校准：Part 4 已落地为"长期记忆 / willingness / 学习管线"调研存档（[part4 doc](./omubot-humanization-part4-memory-relationship.md)）；Part 6 已落地为"源头生成调度"调研存档（[part6 doc](./omubot-humanization-part6-source-side-generation.md)）。原 v3 草稿误把"多角色支持 / fine-tune persona module"挂在 Part 4 — 这两条与 Part 4 实际范围不重合，已挪到 Part 7+（未立项 / 长尾）。

| Part | 范围 | 与 Part 1 v3 关系 |
|---|---|---|
| **Part 1（本文）** | 语感重构（编排既有基础设施 + critic-rewrite） | 基础设施统一化 |
| **Part 2** | 节奏 / 输入感知 / typing 延迟 / 该不该回 + admin SPA register / mood 观测面板 | 消费 RuntimeStateBus；U9 在此 |
| **Part 3** | 群语境感知（多人抢话仲裁 / @bot 真意识别 / 已读不回 / willingness 5-stage 决策） | 消费 `bus.topic.recent` + `bus.state.willingness.stage`（**不是** familiarity 数值，详见下文 C3 校准） |
| **Part 4** | 长期记忆 / willingness 落点 / 学习管线扩展点 / 本地 DB 协同（**已落地为调研存档**） | 与 Part 1 V12 user-card 互通；P4.10/4.12 依赖 Part 2/3 P3.4 willingness 5-stage |
| **Part 5** | 回复分段重构（"自然不打断" — 取消硬拆 + 概率合并 + 标点保留） | 与 Part 1 U1（segmentation 双实现合并）正交；详见 [part5 doc](./omubot-humanization-part5-segmentation.md) |
| **Part 6** | 源头生成调度（plan-then-utter / streaming-as-segment / reactive replan / pause-then-extend）（**已落地为调研存档**） | 与 Part 1 V/U 系列各点接入详见 [part6 doc §6.1](./omubot-humanization-part6-source-side-generation.md)；与 Part 5 natural_split 在方案 A/C 下语义互斥 |
| **Part 7+（未立项）** | 多角色支持 / fine-tune persona module / Character.AI Prompt Poet 模板系统 | 复用 SlangStore.domain="catchphrase" schema；不进当前主线 |

### 13.1 v3 草稿与后续 Part 决议的校准（C3）

本文 §4.3 缺口表"用户好感度"行原计划 V2 写入 `familiarity_score` 数值（短期 TTL 60 min）→ 该方案已被 Part 2/3 §3.4 + Part 4 §3.2 共同推翻（MaiBot 21 个月 5 次回退数值好感的代码事实是最强反例）。

**校准**：Part 1 V2 写入 bus 字段改为 `bus.state.willingness.stage`（5-stage 分类 label：disclose / engage / commit / withdraw / cold），**不写数值**；V8 critic 的 register_fit 阈值改读该 stage label。Part 3 消费同字段。

具体落点延后到 Part 2/3 P3.4 willingness 5-stage 立项时一并处理 — 在该立项前 V2 写入字段保留为占位 stub（仅写空字符串），不向下游真注入。

---

## 附录 A — 引用研究（22 篇 + 9 家产品架构）

### A.1 LLM 风格塌缩 / RLHF 残留

1. Jain et al. (2024) *From Text to Emoji: How PEFT-Driven Personality Manipulation Unleashes the Emoji Potential in LLMs*. [arxiv:2409.10245](https://arxiv.org/abs/2409.10245)
2. Liu (2026) *Surface Markers Amplify While Deep Syntax Dies: A Structural Depth Hypothesis of Self-Training Style Drift*. [arxiv:2605.20602](https://arxiv.org/abs/2605.20602)
3. Padmakumar & He (2024) *Does Writing with Language Models Reduce Content Diversity?* ICLR 2024. [arxiv:2309.05196](https://arxiv.org/abs/2309.05196)
4. Tang et al. (2025) *Role-Aware Reasoning (RAR) for Role-Playing Agents*. NeurIPS 2025.
5. Bai et al. (2022) *Training a Helpful and Harmless Assistant with RLHF*.

### A.2 计算文体学 / 性格语言学

6. Pennebaker & King (1999) *Linguistic Styles*. JPSP 77(6).
7. Mairesse et al. (2007) *Using Linguistic Cues for the Automatic Recognition of Personality*. JAIR 30. [link](https://www.jair.org/index.php/jair/article/view/10564)
8. Schwartz et al. (2013) *Personality, Gender, and Age in the Language of Social Media*. PLOS ONE.
9. Mehl, Gosling & Pennebaker (2006) *Personality in its Natural Habitat*. JPSP 90(5).
10. Yarkoni (2010) *Personality in 100,000 Words*. JRP 44(3). [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC2885844/)
11. Tausczik & Pennebaker (2010) *The Psychological Meaning of Words: LIWC*. JLSP 29(1).
12. Danescu-Niculescu-Mizil et al. (2012) *Echoes of Power*.

### A.3 角色扮演 / Role-play LLM

13. Wang et al. (2023) *RoleLLM*. [arxiv:2310.00746](https://arxiv.org/abs/2310.00746)
14. Tu et al. (2024) *CharacterEval*. [arxiv:2401.01275](https://arxiv.org/abs/2401.01275)
15. Li et al. (2023) *ChatHaruhi*. [arxiv:2308.09597](https://arxiv.org/abs/2308.09597)
16. Shao et al. (2023) *Character-LLM*. [arxiv:2310.10158](https://arxiv.org/abs/2310.10158)
17. Chen et al. (2024) *From Persona to Personalization: A Survey on Role-Playing Language Agents*. [arxiv:2404.18231](https://arxiv.org/abs/2404.18231)
18. Hebert et al. (2024) *PERSOMA*. [arxiv:2408.00960](https://arxiv.org/abs/2408.00960)
19. Ji et al. (2025) *Persona-Aware Contrastive Learning (PCL)*. [arxiv:2503.17662](https://arxiv.org/abs/2503.17662)
20. Zhou et al. (2018) *XiaoIce*. [arxiv:1812.08989](https://arxiv.org/abs/1812.08989)

### A.4 戏剧 / 小说创作论 / 文体学

21. McKee, R. (2016) *Dialogue: The Art of Verbal Action*. Twelve.
22. Egri, L. (1942/1960) *The Art of Dramatic Writing*. Touchstone.
23. Stein, S. (1995) *Stein on Writing*. St. Martin's. Ch.5+11.
24. Hemingway, E. (1932) *Death in the Afternoon*. iceberg theory.
25. Leech & Short (1981/2007) *Style in Fiction*. Longman.
26. Houghton, Upadhyay & Klin (2018) *Punctuation in text messages may convey abruptness. Period.* CHB 80.

### A.5 中文网络语言学 / IM 语料

27. Liu et al. (2008) *Statistical Analysis on Large Scale Chinese Short Message Corpus*. PACLIC 22. [ACL Anthology Y08-1041](https://aclanthology.org/Y08-1041.pdf)
28. 国家语言资源监测与研究中心 + 商务印书馆 (2024/2025) *年度十大网络用语*（80/78 亿字符语料）.
29. 王林林.《网络用语拼音缩写产生的语言学价值》. sinoss.net.
30. 蔡露.《网络时代新兴语气词的研究》. 郑州大学.
31. 李先银 (2016).《自然口语中的话语叠连研究》.《语言教学与研究》.
32. 汪奎 (2012).《网络会话中"呵呵"的功能研究》. 华东师大硕士论文.
33. 靖鸣 (2020).《颜文字：读图时代的表情符号与文化表征》.

### A.6 成熟产品 / 开源项目源码（深读，非 README）

35. Character.AI Prompt Poet — `github.com/character-ai/prompt-poet`
36. SillyTavern — `public/scripts/world-info.js`（commit `51ad27fb`）
37. RisuAI — `src/ts/process/lorebook.svelte.ts`（commit `b8b4de1d`）
38. Inworld AI — character template / IG_* runtime graphs
39. MaiBot — `src/chat/utils/utils.py:418-521`
40. ChatLuna — `packages/core/src/utils/{buffer_text.ts, string.ts}`
41. AstrBot — `astrbot/core/pipeline/result_decorate/stage.py`
42. Omubot 现状 — `services/humanizer.py` / `services/llm/segmentation.py` / `services/persona/runtime_selector.py` / `services/episodic/store.py` / `services/system_module/state_bus.py` / `services/block_trace/` / `plugins/{affection,schedule,dream}/`
