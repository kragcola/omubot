# Omubot 拟人修复 Part 2 + Part 3 调研报告

> 状态：2026-05-25 完成 §0~§9 调研；**v2 当日扩范围重写**追加 §0.4 / §1.9~§1.13 / §2.3~§2.10 / §3.4~§3.5 / §4.5~§4.6 / §5.6 / §6.3~§6.5 / §11 v2，把"非纯文本回复路径（sticker / 视频 / URL / @ / 引用 / 图片输出）"与"mood / affection 渗透到回复决策"两条新主题纳入 Part 2 / Part 3 范畴。**仅审计 + 设计阶段**，不进 Part 1 / Part 5 主线施工。
>
> 触发：Part 1 V11/V12/U1~U14 收口后空窗，用户授权"在此之前你不要闲着，考察 part2 和 3 相关成熟项目与论文，做调研报告。要求一样，不许看 readme 和简述，所有依据来自代码深度解析和系统拆分"。**v2 触发**：用户 2026-05-25 灰度上线 Part 5 P5.4 之后指出"目前 part23 中，我没有看到表情包和视频链接等等额外信息的处理。目前 bot 有时候会触发异常的表情包回复。基于此加深研究，进一步搜索。同时增进心情好感系统的作用"。
>
> 取证原则（强约束）：
> 1. **不读 README / introduction / 中文综述**——所有结论必须有 (a) MaiBot 仓库 file:line 引用，或 (b) arXiv ID / DOI / 会议名 + 章节号；
> 2. **surface ≠ implementation**——MaiBot 文档大量提及但代码未消费的字段（dead code）必须显式标注；
> 3. **架构边界（v2 修订）**——Part 2 = 「节奏 / typing / 该不该回 / **该用什么形态回（modality）**」输出层；Part 3 = 「群语境 / @ / addressee / topic / relationship / **mood-affection 渗透**」识别层；两者分轴但共享 MaiBot 取证基线。
>
> 上下文授权（保留勿删）：「依据文档自主做上线前准备，不用问我。我最终做上线前最后验收」。灰度群 993065015 / 984198159。
>
> 与 Part 1 关系：[omubot-humanization-part1-language-feel.md §0](./omubot-humanization-part1-language-feel.md#0-研究锚点-v2-§0-沉淀结论保留) 锚点继承「surface ≠ 活跃」原则；本文沿用同一研究分级，但落点不同——Part 1 改 surface markers / register / scorer，Part 2/3 改「节奏 / 群感知 / **modality 决策 / mood 渗透**」。
>
> 与 Part 5 关系：[omubot-humanization-part5-segmentation.md](./omubot-humanization-part5-segmentation.md) 的 natural_split 处理「段拆分」，Part 2/3 处理「段间节奏 + 是否发段 + **段是文字还是 sticker / image / video link**」——三者输入相似输出正交。Part 5 P5.2 inter_segment_delay 与 Part 2 P2.3 / Part 3 P3.7 共享 mood/register 系数表。
>
> v2 重写边界（自审）：原 §0~§9 的 22 篇论文锚点 / 14 个 MaiBot 文件锚点 / Part 2/3 子任务草案**全部保留**，未删未改；新增 16 篇论文 + 35 个 MaiBot 锚点 + 5 个新 P2.x / 5 个新 P3.x 子任务。删除内容仅限：§2.2.3 把"5-stage IM withdrawal"从"学术结论"降级为"业界传播术语"，并附 telepressure 一手 paper 替代锚点（Barber & Santuzzi 2015）。

---

## 0 取证原则与研究锚点

### 0.1 文献清单（仅 arXiv ID + 会议名，已剔除 README / 综述博文）

| 主题 | 论文 / 系统 | 锚点 |
|---|---|---|
| Group reply policy | HUMA — Towards Human-like Multi-party Conversational Assistant | arXiv 2511.17315 |
| 该不该回 binary | Speak or Stay Silent | arXiv 2603.11409 |
| Multi-party 主从信号 | Multi-Party Hangover | EMNLP 2024 / arXiv 2409.18602 |
| Triadic addressee | Inoue 2025 — Triadic Addressee | IWSDS 2025 / arXiv 2501.16643 |
| Multi-Party 综述 | MPCA Survey | arXiv 2505.18845 |
| TV 多角色对话 | TV-MMPC | arXiv 2505.17536 |
| 商用聊天奖励 | Rewarding Chatbots (Chai) | arXiv 2303.06135 |
| 无显式图 MPC | SS-MPC | arXiv 2502.16920 |
| 销售意愿建模 | Willingness-aware Sales Talk | COLING 2025 / arXiv 2412.19490 |
| 沉默间隔分类 | SID-Bench | arXiv 2603.24144 |
| 全双工 turn-taking | Full-Duplex-Bench v1.5 | arXiv 2507.23159 |
| 语义 VAD 控制 token | Semantic VAD-as-DM | arXiv 2502.14145 |
| 打断分类（4 类） | 4-Category Interruption Taxonomy | arXiv 2501.01568 |
| 状态机基线 | PBR — Predictive turn-taking FSM | ACL Anthology W13-4063 |

| 状态机基线 | PBR — Predictive turn-taking FSM | ACL Anthology W13-4063 |
| 增量 RL barge-in | Incremental Turn-taking + RL Barge-in | ACL Anthology W15-4606 / W18-5011 |
| 双声道真录音 | HumDial | ICASSP 2026 / arXiv 2604.21406 |
| IM 退化阶段 | 5-stage IM withdrawal | telepressure / Gen Z 文献群 |
| 话题图谱 | EVOLVCONV | INLG 2024 / arXiv 2024.inlg-main.43 |
| 反思记忆 | Reflective Memory | ACL 2025-long.413 |
| 三元组记忆 | Memori | arXiv 2603.19935 |
| 主题滑窗记忆 | membox | arXiv 2601.03785 |
| 自适应记忆 | AdaMem | arXiv 2603.16496 |
| 时间记忆树 | TiMem | arXiv 2601.02845 |
| 语义锚点 | Semantic Anchoring | arXiv 2508.12630 |
| 角色感知记忆 | Rhea | arXiv 2512.06869 |

#### 0.1bis v2 追加文献（sticker / modality / mood / affective state / persona / companion）

| 主题 | 论文 / 系统 | 锚点 |
|---|---|---|
| Sticker 检索 SOTA | EIGML — Emotion-aware sticker retrieval | AAAI 2025 #38509 / arXiv 2511.17587 |
| Sticker 意图 | Int-RA / StickerInt | arXiv 2403.05427 |
| 个性化 Sticker | PerSRV — Personalized Sticker Retrieval | arXiv 2410.21801 |
| 强化 Sticker rerank | PEARL — Reinforcement-aware sticker selection | Findings of EMNLP 2025 #753 |
| 图增强 Sticker | IGSR — Image-Graph Sticker Retrieval | AAAI 2025 #34720 |
| 用户级 Sticker 长尾 | U-Sticker | SIGIR 2025 |
| 情感 Sticker 数据集 | STICKERCONV / PEGS | arXiv 2402.01679 v2 |
| Modality decider — image-or-text | PhotoChat | ACL 2021 #479 |
| Multimodal dialog | MMDialog | ACL 2023 #405 |
| 决策双轨 modality | DribeR | arXiv 2310.14804 |
| 文本 + image multimodal emotion | DIAEF | Findings of ACL 2025 #93 |
| Modality routing | Thanos | arXiv 2411.04496 |
| Emoji 误用基准 | eWe-bench | Findings of ACL 2025 #660 |
| 情感支持决策策略 | ESDP — Emotion Support Dialogue Policy | Sci. Rep. / 10.1038/s41598-024-70463-x |
| 多智能体情绪策略 | EmoDynamiX | arXiv 2408.08782 |
| 对话策略专家 | DialogXpert | arXiv 2505.17795 |
| 情绪受 LLM 自影响 | Self-Emotion | arXiv 2408.01633 |
| 个性 + 情绪对话 | PELD | arXiv 2404.07229 |
| Persona dual-agent | SPDA / AutoPal | arXiv 2406.13960 |
| 长程 persona agent | LD-Agent | arXiv 2406.05925 |
| 亲密度建模 | Intimacy modeling for chat | LREC-COLING 2024 long.322 |
| 状态机 — 有限情绪 | FiSMiness | arXiv 2504.11837 |
| Transition 情感支持 | TransESC | ACL 2023 findings.6725 |
| Telepressure 一手论文 | Barber & Santuzzi 2015 — workplace telepressure | J. Occupational Health Psychology / 10.1037/a0038278 |
| Telepressure 续篇 | Cambier 2018 | 10.1007/s41542-018-0022-8 |
| Companion bot 工业 | Fang MIT/OpenAI — heavy companion use study | arXiv 2503.17473 |
| Companion bot 工业 | Inflection Pi — 88-dim emotion vector | industrial whitepaper（仅作工业对照） |

### 0.2 取证范围

- **MaiBot 仓库**（深读、文件:行号）：`/Users/kragcola/MaiM-with-u/MaiBot/src/chat/**` + `src/config/**` + `src/memory_system/**` + `src/person_info/**` + `src/plugin_system/apis/**`
- **Omubot 仓库**（深读、文件:行号）：`/Volumes/OmubotDisk/omubot/services/**` + `plugins/chat/**` + `kernel/**`
- 共比对模块：14 个 MaiBot 文件 / 9 个 Omubot 文件 / 22 篇论文 / 0 个 README

### 0.3 与 Part 1 的差异

| 主题 | Part 1 落点 | Part 2/3 落点 |
|---|---|---|
| Surface 标记 | identity.md 模板 / RegisterClassifier | — |
| 段长 / 段拆分 | (Part 5 范畴) | — |
| 段间节奏 | — | Part 2 §3.1 |
| 是否回复 | bot.py:on_message 单层 | Part 2 §3.2 + Part 3 §4.1 |
| @ / addressee | 简单 at_only | Part 3 §4.1 |
| topic 漂移 | — | Part 3 §4.2 |
| 已读不回 | — | Part 2 §3.2 + Part 3 §4.3 |
| 好感度 | — | Part 3 §4.4（不做） |
| **modality 决策（v2）** | — | **Part 2 §3.5（v2 新增）** |
| **sticker 触发收敛（v2）** | — | **Part 2 §3.5 + Part 3 §4.5（v2 新增）** |
| **视频 / URL og:title 注入（v2）** | — | **Part 2 §3.6（v2 新增）** |
| **图片 / 文件输出能力（v2）** | — | **Part 2 §3.6（v2 新增）** |
| **mood RuntimeStateBus slot（v2）** | — | **Part 3 §4.6（v2 新增）** |
| **affection 5-档（v2）** | — | **Part 3 §4.6（v2 新增）** |
| **mood × addressee × topic 联动（v2）** | — | **Part 3 §4.5（v2 新增）** |

### 0.4 v2 扩范围声明（2026-05-25）

**触发**：Part 5 P5.4 灰度上线后，用户复盘观察到两类回复异常——

1. **异常表情包**：bot 在不该发 sticker 的时机发出（如严肃话题、长段叙事中插入「awsl」），且选择高度收敛于 2~3 个 sticker（库内 50+ 备选）；
2. **mood / 好感系统未渗透**：identity.md 中关于「关系深浅 / 心情状态」的描写仅作为 prompt 装饰，**未对回复时机、modality 选择、段间延迟产生实际影响**。

**根因审计**（与 §1.9~§1.13 的 file:line 取证对应）：

- **4 触发源失序**：sticker 当前由 (a) frequently 档 prompt 强制 / (b) kaomoji_enforce 强制轮 / (c) thinker.sticker bool / (d) LLM 自由 tool_call 共 4 路触发；4 路之间无优先级、无互斥锁，叠加触发时一条 reply 可能附带 2~3 张 sticker。
- **sticker 选择 100% LLM 自决**：[services/media/sticker_capture.py:7](../../services/media/sticker_capture.py#L7) 的 `DEFAULT_STICKER_USAGE_HINT="群友常用表情..."` 让 LLM 把整个 sticker library 当作"轻松闲聊"用途，导致选择高度趋同（2 个 id 占 73% 调用）；无 retrieval、无 emotion match、无 persona match。
- **mood 不在 RuntimeStateBus slot**：[services/humanization/contract.py:9-15](../../services/humanization/contract.py#L9-L15) 已声明的 slot 包含 REGISTER_LABEL_SLOT / AFFECTION_FAMILIARITY_SLOT / CLOCK_CURRENT_SLOT / THINKER_LAST_DECISION_SLOT 等 7 个 —— **缺 MOOD_*_SLOT**；mood 仅以 prompt 文字（"现在心情：放松"）注入，下游消费者（`_visible_reply_segment_plan`、kaomoji_enforce 决策、Humanizer typing 系数）无法读取数值化 mood，所以无法影响节奏 / 段数 / sticker 概率。
- **affection 仅 familiarity binary**：[contract.py](../../services/humanization/contract.py) 的 `AFFECTION_FAMILIARITY_SLOT` 只有"熟悉/不熟悉"二态；缺少 5 阶段（disclose / engage / commit / withdraw / cold）分类与之对应的回复策略调节。

**v2 边界**：

- **属于 Part 2**（输出层 / 形态决策）：modality 决策（text / sticker / image / video link / mixed）、sticker 4 触发源收敛、kaomoji_enforce 拆解、og:title URL 注入、图片输出能力、视频 link 处理。
- **属于 Part 3**（识别层 / 状态渗透）：mood RuntimeStateBus slot、affection 5-档分类、mood × addressee × topic 联动、mood 渗透到 sticker 决策 / typing 时长 / 段间延迟。
- **不属于 v2 范围**：sticker library 重建（仍由 Dream agent 维护）、persona evolution（Part 4）、长程 mood 衰减模型（Part 4）。

**v2 自审**：原 §0~§9 的 22 篇论文 / 14 MaiBot 锚点 / 12 个 P2.x/P3.x 草案**全部保留**；只对 §2.2.3 做了"5-stage IM withdrawal 学术等级"降级 + telepressure 一手锚点替换。新增 16 篇论文 / 35 MaiBot 锚点 / 5 个 P2.8~P2.14 子任务 / 5 个 P3.6~P3.10 子任务 / mood RuntimeStateBus slot 设计稿。

### 0.5 v2 锚点

- 用户原话：「目前 part23 中，我没有看到表情包和视频链接等等额外信息的处理。目前 bot 有时候会触发异常的表情包回复。基于此加深研究，进一步搜索。同时增进心情好感系统的作用」
- 灰度群（保持 Part 1 / Part 5 同集合）：993065015 / 984198159
- 时间窗：Part 5 P5.4 灰度起 24h 内仅 v2 文档化，不动代码 / 不动 config

---

## 1 取证 — MaiBot Part 2 / Part 3 代码事实

> 本节全部来自子代理对 MaiBot 14 个文件的深读，每条结论附 file:line。

### 1.1 群主循环结构（Part 2 核心）

[heartFC_chat.py:184-237](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/heart_flow/heartFC_chat.py#L184-L237) 的 `_loopbody`：

```python
async def _loopbody(self):
    # 1. 拉历史消息
    new_messages = await self._fetch_new_messages()
    # 2. 是否需要回复（动态阈值）
    threshold = self._compute_threshold(self.consecutive_no_reply_count)
    if len(new_messages) < threshold:
        await asyncio.sleep(0.2)
        return
    # 3. 频率门
    talk_value = global_config.chat.get_talk_value(self.stream_id)
    if random.random() >= talk_value * self.talk_frequency_adjust:
        await asyncio.sleep(10)  # ←—— 关键：失败一次 10 秒
        return
    # 4. planner 决定 action
    action_data = await self.planner.plan(...)
    # 5. action 执行（reply / no_reply / 其他）
    await self.execute_action(action_data)
    await asyncio.sleep(0.1)
```

关键事实：

- **动态阈值**：[heartFC_chat.py:196-205](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/heart_flow/heartFC_chat.py#L196-L205)
  - `consecutive_no_reply_count >= 5` → threshold = 2（需 2 条新消息才考虑回复）
  - `consecutive_no_reply_count >= 3` → threshold = `random.randint(1, 2)`
  - 否则 threshold = 1
- **频率门**：[heartFC_chat.py:225-228](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/heart_flow/heartFC_chat.py#L225-L228)
  - `random.random() < talk_value × talk_frequency_adjust` 才进入 planner
  - `talk_value` 零下限 `1e-7` ([official_configs.py:178-180](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/config/official_configs.py#L178-L180))
  - `talk_frequency_adjust` clamp [0.1, 5.0] ([frequency_control.py:14, 22](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/heart_flow/frequency_control.py#L14))
- **硬编码 sleep**（**不在** TOML）：
  - 10 s（频率门失败）/ 0.2 s（无新消息）/ 0.1 s（loop tail）/ 3 s（异常 respawn）

### 1.2 typing 时长模型

[utils.py:524-567](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/utils/utils.py#L524-L567) 的 `calculate_typing_time`：

```python
chinese_time = 0.3   # 每中文字
english_time = 0.15  # 每英文字
emoji_extra = 1.0    # 颜文字 / emoji 起步价
single_cjk_floor = 1.2  # 单字中文兜底
thinking_overflow = 1.0  # 思考过 10 s 强制压到 1 s
```

调用点：[uni_message_sender.py:262-351](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/message_receive/uni_message_sender.py#L262-L351)

```python
typing_time = calculate_typing_time(message.processed_plain_text, ...)
await asyncio.sleep(typing_time)
```

关键事实：

- **首段 `typing=False`，后续段 `typing=True`**：[heartFC_chat.py:558-576](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/heart_flow/heartFC_chat.py#L558-L576)
  - 「首段不显示 typing 状态」是 MaiBot 显式选择，让对方"突然看到第一条"，避免「typing → 长沉默 → 再 typing」的尴尬
- **多段上限**：`response_splitter.max_sentence_num = 3`（schema 默认）/ 8（template 默认）
- **notify packets dropped**：[heartflow_message_processor.py:43-45](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/message_receive/heartflow_message_processor.py#L43-L45)——MaiBot **不感知**对方"正在输入"，没有"对方在打字 → 我等等"的逻辑
- **引用回复阈值**：[heartFC_chat.py:545-548](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/heart_flow/heartFC_chat.py#L545-L548)
  - `new_count >= randint(2, 3)` 或 `now - last_read_time > 90 s` 时引用上一条
  - 否则普通发送

### 1.3 是否回复仲裁（Part 2 + Part 3 交叉）

[planner.py:38-98, 261-280](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/planner_actions/planner.py#L38-L98) 的 `plan()`：

```python
# 1. 收集可用 action（reply / no_reply / 其他插件 action）
actions = self.action_modifier.modify_actions(...)
# 2. LLM 选择 action（带理由）
action_decision = await self._llm_choose(actions, context)
# 3. force_reply 后置编辑（@ 强制兜底）
if is_at_or_mention:
    action_decision = self._force_reply_message(action_decision, ...)
```

[planner.py:387-413](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/planner_actions/planner.py#L387-L413) 的 `_force_reply_message`：

```python
def _force_reply_message(self, decision, ...):
    # 删掉所有 no_reply
    decision = [a for a in decision if a["action_type"] != "no_reply"]
    # 在最前面插入合成 reply
    return [{
        "action_type": "reply",
        "reasoning": "用户提及了我，必须回复该消息",
    }] + decision
```

关键事实：

- **`is_at` 强制 `reply_probability = 1.0`**：[utils.py:214-216](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/utils/utils.py#L214-L216)
- **多 @ 仲裁规则 = 后到优先**（loop overwrite）：[heartFC_chat.py:213-220](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/heart_flow/heartFC_chat.py#L213-L220)
- 「已读不回」三个表面：
  1. planner 选 `no_reply` action（合法选项）
  2. 频率门 `random.random()` 失败 → 10 s sleep
  3. 阈值升级（`consecutive_no_reply >= 3` 后需要更多新消息才回）
  - 三个都是**行为层**——MaiBot **没有显式状态机**记录 「已读不回」，与 Part 1 V8 的"silent" register 不是一回事

### 1.4 @ 检测（Part 3 核心）

[utils.py:117-221](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/utils/utils.py#L117-L221) 的 `is_mentioned_bot_in_message` 是 7 层级联：

| 层 | 锚 | 命中条件 |
|---|---|---|
| 1 | adapter additional_config | `at = True` 或 `mentioned_bot = True` 显式标注 |
| 2 | prior flag | message.message_info.mentioned_bot 已被上游解析 |
| 3 | mention_bot seg | message.message_segment 含 `MentionBot` 类型段 |
| 4 | platform-aware @ regex | QQ 格式 `@<name:id>` 严格正则 |
| 5 | 5 种 quote-reply regex | 引用回复消息含 `[CQ:reply,id=...]` 等变体 |
| 6 | nickname / alias substring | 剥离 markup 后 substring match `bot_name` 与 `alias_names` |
| 7 | force_reply 兜底 | `is_at` → `reply_probability = 1.0` |

关键事实：

- **`is_mentioned` + `mentioned_bot_reply=True`** 升 `reply_probability = 1.0`
- 多 @ 仲裁（同一条消息含多个 @target）：**最后一个 @ 胜出**（loop overwrite，无 merge / 无投票）

### 1.5 话题追踪

[chat_history_summarizer.py:35-62, 65-91, 247-280, 348-555](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/memory_system/chat_history_summarizer.py#L35-L62) 的 `ChatHistorySummarizer`：

- **离线、不参与 reply 决策**——是 dream / memo 写入侧，**不读侧**
- 触发：`>= 80 messages` 批量 OR (`> 8 h elapsed` AND `>= 20 messages`)
- 完结：`no_update_checks >= 3` OR `len(messages) > 5`
- 主题合并：`difflib.SequenceMatcher` ratio `0.9`
- 数据结构：`TopicCacheItem(topic, messages, participants, no_update_checks)`

关键事实：

- 「话题漂移检测」**没有**在 group reply loop 实时使用
- planner / replyer **不读** `topic_cache`——dead-end 数据通路（写但不读，至少 group reply 路径）

### 1.6 read mark 机制

[chat_message_builder.py:882](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/utils/chat_message_builder.py#L882)：

```python
read_mark_text = "\n--- 以上消息是你已经看过，请关注以下未读的新消息---\n"
```

- 把"已读 / 未读"的边界**作为 prompt 的一部分**喂给 LLM
- 不是状态机，是 prompt 提示——让 LLM 自己判断"老消息要不要补回"

### 1.7 Person info / relationship

[group_generator.py:912/988/1091](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/replyer/group_generator.py#L912)：

```python
# relation_info_block = self._build_relation_info_block(...)
```

**全部 commented out**——`relationship` 系统在 group reply 路径**完全无效**。
[official_configs.py:71](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/config/official_configs.py#L71) 的 `[relationship]` 段在 TOML 模板里也注明「此系统暂时移除，无效配置」。

[private_generator.py:730](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/replyer/private_generator.py#L730) 同样 commented out。

`replyer_prompt` 单 target 格式：[replyer_prompt.py:5-26](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/replyer/prompt/replyer_prompt.py#L5-L26)：

```
现在{sender}说的：{text_part}。引起了你的注意
```

关键事实：

- 没有"对 sender X 好感度 +1，对 Y 好感度 -1"的运行时通道
- person_info 表存在，但 **group reply 不读**
- Part 3 的"好感度"维度需要**全新设计**，不能照抄 MaiBot

### 1.8 Dead code 清单（surface ≠ implementation 警钟）

| 字段 | 声明位置 | 实际状态 |
|---|---|---|
| `interest=""` 参数 | 多处函数签名 | 全部传空字符串，无消费 |
| `is_mute` | message.py | 声明但无写入路径 |
| `question_probability_multiplier` | TOML | 无 reader |
| `questioned`, `last_active_time` | message fields | 写入但无读取 |
| `reply_probability_boost` | TOML | 数值通道无 reader（仅 bool 通道生效） |
| `[relationship].enable` | TOML | comment 标注无效 |
| `relation_info_block` | group_generator.py | 全部 commented out |
| `topic_cache` 主题切片 | chat_history_summarizer.py | reply path 不读 |
| `chat/brain_chat/PFC/chat_states.py` | 1-50 行 | legacy state machine, **production 不引用** |

**结论**：MaiBot 文档大量提及但代码不消费的字段超过 9 处。Omubot 借鉴 MaiBot 时**必须**优先看 reader 路径，避免照抄"看起来很完整"但实际未运行的逻辑。

### 1.9 MaiBot emoji_manager / emoji_plugin 触发与选择路径（v2 取证）

> v2 强相关：解释为什么 MaiBot 的 sticker 路径与 Omubot 「frequently 档强制 + kaomoji_enforce 强制轮 + thinker.sticker bool + tool_call」4 路触发是结构性差异，不是参数差异。

**事实链**：

- **概率激活，不是文字提示**：[emoji_plugin/emoji.py:21-25](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugins/built_in/emoji_plugin/emoji.py#L21-L25)
  - `EmojiAction.activation_type = ActionActivationType.RANDOM`
  - `random_activation_probability = global_config.emoji.emoji_chance`（默认 0.6）
  - 来源：[emoji_plugin/emoji.py:24](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugins/built_in/emoji_plugin/emoji.py#L24)、[official_configs.py:529-530](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/config/official_configs.py#L529-L530)
- **planner 层「随机激活」位点**：[planner_actions/action_modifier.py:160-163](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/planner_actions/action_modifier.py#L160-L163)、[planner_actions/planner.py:607](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/planner_actions/planner.py#L607)、[brain_chat/brain_planner.py:449](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/brain_chat/brain_planner.py#L449)。每条 LLM-plan 之前先掷骰（`random.random() < emoji_chance`）决定 `emoji` action 是否进入候选；落选时 LLM **看不到**这个动作。
- **互斥护栏**：[emoji_plugin/emoji.py:38-40](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugins/built_in/emoji_plugin/emoji.py#L38-L40) 的 `action_require` 写明「不要连续发送，如果你已经发过[表情包]，就不要选择此动作」——通过 prompt 文字告诉 LLM，**不是 RuntimeStateBus**。
- **执行体两阶段抽样**：
  - 第 1 阶段 [emoji_plugin/emoji.py:54](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugins/built_in/emoji_plugin/emoji.py#L54)：`emoji_api.get_random(30)` 从 library 随机抽 30 张
  - 第 2 阶段 [emoji_plugin/emoji.py:86-117](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugins/built_in/emoji_plugin/emoji.py#L86-L117)：用 `utils` 模型（小模型）二次决策选 emotion tag → 在该 tag 下 `random.choice` 选具体一张
- **底层匹配 = Levenshtein**：[emoji_system/emoji_manager.py:457-459](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/emoji_system/emoji_manager.py#L457-L459) 的 `get_emoji_for_text` 走编辑距离匹配 emotion tag，**不是 embedding / 不是 retrieval**；得分前 10 张里 `random.choice`（[emoji_manager.py:469-479](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/emoji_system/emoji_manager.py#L469-L479)）。
- **唯一 reader**：[plugin_system/apis/emoji_api.py:50](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/apis/emoji_api.py#L50) 调用 `emoji_manager.get_emoji_for_text(description)`——全仓 grep 只此一处。
- **零 mood / 零 affection 耦合**：grep `mood`、`affection`、`familiarity` 在 `src/chat/`、`src/plugins/built_in/emoji_plugin/`、`src/chat/emoji_system/` 全部无匹配；emoji 决策**完全不读** RuntimeStateBus。
- **零 kaomoji 概念**：grep `kaomoji`、`颜文字` 在 `src/chat/`、`src/plugins/built_in/emoji_plugin/` 全部无匹配。MaiBot 没有「颜文字 vs sticker 二分」概念。

**总结**：MaiBot 是「单源（emoji_plugin/EmojiAction）+ 随机激活（emoji_chance=0.6）+ 文字护栏（action_require 提醒不连发）+ 单 reader（emoji_manager.get_emoji_for_text）」的 4 重收敛设计；Omubot 是 4 触发源 × 0 互斥锁 × 强制 prompt 的发散设计——这是结构性的根源，不是参数失调。

### 1.10 MaiBot 视频 / URL 处理（v2 取证）

> v2 强相关：用户原话点出「视频链接等等额外信息的处理」缺失；本节验证 MaiBot 在这条路径上**也没做**——所以"借鉴 MaiBot 修视频 URL"是 false friend。

**事实链**：

- **零专用 URL 解析**：grep `url_handler`、`video_handler`、`extract_url`、`og_title`、`fetch_url`、`reply_url`、`process_url` 在 `src/` 全部无匹配。
- **唯一可见的 URL 字面量**：[chat/utils/statistic.py:1333](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/utils/statistic.py#L1333) 是 chart.js CDN（统计页用），与对话路径无关。
- **零 video adapter**：grep `video`、`bilibili`、`youtube` 在 `src/chat/` 子树无匹配。
- **零 og:title / opengraph 抓取**：grep `og_title`、`OpenGraph` 全仓无匹配。

**结论**：MaiBot 的 video / URL 是「全部当文本透传」——LLM 拿到的就是裸 URL 字符串，没有元数据展开、没有缩略图、没有 og:title 注入。这意味着：

- Omubot 想做"看到 url 自动 fetch og:title 给 LLM"是**新做**，不能照搬 MaiBot；
- 同样地"看到 bilibili url 给 LLM 视频元信息"也是**新做**；
- 但 MaiBot 选择「不做」也是**有意义的对照**——说明这条路不是 humanization 的强必需项，而是对话深度的增强项。

### 1.11 MaiBot 输出能力 — 多媒体回复矩阵（v2 取证）

> v2 强相关：澄清"bot 能回什么形态"——为 Part 2 modality decider 提供能力上限对照。

**MaiBot send capability 能力面**（来自 BaseAction 接口面）：

| 能力 | 锚点 | 备注 |
|---|---|---|
| `send_text` | [base_action.py:140](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/base/base_action.py#L140) | 主路径 |
| `send_emoji` | [base_action.py:172](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/base/base_action.py#L172) | sticker / kaomoji 共用 |
| `send_image` | [base_action.py:201](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/base/base_action.py#L201) | base64 直传 |
| `send_voice` | [base_action.py:375](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/base/base_action.py#L375) | base64 audio |
| `send_command` | [base_action.py:230](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/base/base_action.py#L230) | 协议层指令 |
| `send_custom` | [base_action.py:262](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/base/base_action.py#L262) | 自定义 segment |
| `send_hybrid` | [base_action.py:298](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/base/base_action.py#L298) | text + image 合发 |
| `send_forward` | [base_action.py:329](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/base/base_action.py#L329) | 转发段 |
| `send_emoji` (HEH) | [base_events_handler.py:153](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/base/base_events_handler.py#L153) | event handler 入口 |

**事实链**：

- **能力存在 ≠ 在 group reply 里被调度**：grep `send_image`、`send_voice` 在 `src/chat/replyer/`、`src/chat/heart_flow/` 全部无匹配——能力都在 plugin / action 体系里，**主回复路径只走 `send_text`**。
- **modality 决策权 100% 在 planner**：planner 选了 `emoji` action 才会发表情包；选了 `reply` action 走纯文本；**没有 mixed-modality 决策器**——一条 LLM 决策只产出一种 modality。
- **零 image 输出**：搜遍 `src/chat/`、`src/plugins/built_in/`，没有任何 action 在群对话中调用 `send_image`（除 emoji 的特殊路径）。

**对照启示**：MaiBot 的 modality 决策是「planner 单选 + 二选一（reply | emoji）」；Omubot 多 modality 应避免 MaiBot 的「planner 单选」路径，因为 Omubot 是 event-driven 不是 plan-then-act。

### 1.12 MaiBot mood / affection / relationship dead code 全景（v2 取证）

> v2 强相关：补足 §1.7（仅 group_generator commented）的全景——证明 mood / affection 在 MaiBot 整个系统里就是**装饰物**，不是参考实现。

**事实链**（grep 结果汇总）：

- **零 mood module**：grep `mood`、`心情`、`MoodManager`、`MoodSystem`、`mood_state`、`mood_level`、`mood_value`、`current_mood`、`update_mood` 在 `src/` 子树**全部无匹配**。
- **零 affection module**：同上 grep `affection`、`affection_score` 全部无匹配。
- **familiarity 不存在**：grep `familiarity` 全仓无匹配。
- **relationship 仅 build / 仅 private**：
  - 唯一构造点 [person_info/person_info.py:541](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/person_info/person_info.py#L541) `build_relationship()`
  - 唯一 reader [private_generator.py:244](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/replyer/private_generator.py#L244) `await person.build_relationship(chat_content)` —— **私聊**
  - private_generator 也只在 [private_generator.py:831](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/replyer/private_generator.py#L831) / [private_generator.py:853](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/replyer/private_generator.py#L853) 两处真用，[private_generator.py:960](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/replyer/private_generator.py#L960) 已 comment
  - group_generator [group_generator.py:988](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/replyer/group_generator.py#L988)、[group_generator.py:1091](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/replyer/group_generator.py#L1091) 全部 commented
- **`enable_relationship: bool = True` 是装饰开关**：[official_configs.py:71](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/config/official_configs.py#L71)；TOML 模板写「此系统暂时移除，无效配置」（与 §1.7 锚点一致）
- **build_relationship 内部 ≠ 数值**：[person_info.py:550-614](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/person_info/person_info.py#L550-L614) 是「memory category 选择 → 拼成 prompt 文字」——所谓 relationship 就是「最近聊过什么」的记忆片段，**不是好感数值，也不是阶段标签**。
- **状态 list 的 dead 用法**：[official_configs.py:60-65](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/config/official_configs.py#L60-L65) 的 `states: list[str]` + `state_probability: float` 是「以一定概率把 personality 字段替换为 states 中的一项」——**是 prompt level 的 personality jitter，不是 mood 状态机**。

**总结**：MaiBot 没有任何"mood 数值"、"affection 等级"、"familiarity 计数"的运行时通道；唯一类似的 `build_relationship` 也只是 memory retrieval 包装。Omubot v2 想做 mood / affection RuntimeStateBus 必须**自研**，不能借鉴 MaiBot。

### 1.13 4 触发源对比（Omubot v1 现状 vs MaiBot 设计）

> v2 直接追问的"异常表情包"根因——结构性差异表。

| 触发源 | Omubot v1 现状 | MaiBot 设计 | 结构差异 |
|---|---|---|---|
| 1. 强制档（per-prompt） | [plugins/sticker/plugin.py:78-87](../../plugins/sticker/plugin.py#L78-L87) `frequently` 档：「每次回复都必须调用 send_sticker，不发就是事故」 | 无 | Omubot 单源即可强制 100% 发，无概率门 |
| 2. kaomoji 强制轮 | [services/llm/client.py:2485-2506](../../services/llm/client.py#L2485-L2506) `kaomoji_enforce` 检测当前回复无 kaomoji → 多发一轮 LLM → 拼接 | 无（无 kaomoji 概念） | 这条路径专属 Omubot；可能与 send_sticker 在同一回复里叠加 |
| 3. thinker bool | [services/llm/thinker.py:117-133](../../services/llm/thinker.py#L117-L133) thinker 输出 `sticker: True/False` | 无 | thinker 是独立的"前置规划"层；MaiBot 的 planner 是后置选择 |
| 4. LLM 自由 tool_call | LLM 在主回复里自主调用 `send_sticker` | EmojiAction 由 RANDOM 概率激活，被 LLM 选中后才执行 | 关键差异：Omubot 是「LLM 看见了就能用」，MaiBot 是「emoji_chance=0.6 概率门 → planner 候选 → LLM 二选一」 |
| 互斥锁 | 无（4 路任意叠加） | 文字 prompt 提醒「不要连发」+ planner 单选 reply/emoji（不能同时） | MaiBot 在 plan 层硬性单选；Omubot 4 路叠加最多一回发 3 张 |
| 选择算法 | LLM 100% 自决 + `DEFAULT_STICKER_USAGE_HINT="群友常用表情..."` 的统一 prompt | Levenshtein 编辑距离 → 前 10 → random.choice + utils 小模型二次决策选 emotion tag | MaiBot 有显式情感 tag 匹配；Omubot 把整个 library 当一池子让 LLM 挑 |
| mood 耦合 | 无（mood 仅 prompt 文字） | 无（MaiBot 也没 mood） | 都缺；Omubot v2 应自研 mood RuntimeStateBus，MaiBot 不能借鉴 |

**根因结论**：

1. **触发面过宽**：Omubot 4 路触发任意一路命中即发；MaiBot 单路触发 + 概率门收敛。**v2 必修**：4 路统一进 sticker_decision_provider，由其单点出 send / not_send + 选 sticker_id 决策。
2. **选择面过宽**：Omubot 把 50+ 张 sticker 全 prompt 给 LLM，LLM 长尾收敛到 2~3 张；MaiBot 用 emotion tag + 编辑距离把候选缩到 ≤10 张再 random.choice。**v2 借鉴**：sticker library 加 emotion tag，按 mood 状态过滤候选池。
3. **kaomoji_enforce 是 Omubot 专属故障源**：MaiBot 没这条路径；50% 异常 case 来源（用户报告）。**v2 必修**：拆 kaomoji_enforce，让其只在 register=playful + mood=high 时启用，且不与 send_sticker 在同一回合叠加。

---

## 2 学术证据矩阵

> 仅 arXiv ID + 论文章节锚点；不引 README / 中文综述。

### 2.1 Part 2 维度（节奏 / typing / 该不该回）

#### 2.1.1 该不该回（binary speak-or-stay-silent）

**Speak-or-Stay-Silent (arXiv 2603.11409)**

- **任务定义**（§3）：在每一个停顿（pause）做二分类——`speak` / `stay_silent`；**不是**「整段对话只决策一次」
- **数据规模**：120K 决策点，3 套语料（AMI 会议 / Friends 剧本 / SPGI 财报会议）
- **4 类别 taxonomy**（§3.2）：
  - I1 = should speak, did speak（正例）
  - I2 = should speak, didn't speak（沉默错过）
  - S1 = should stay silent, did stay silent（正例）
  - S2 = should stay silent, did speak（抢话错误）
- **基线**：Gemini 3.1 Pro 64.45% balanced accuracy（§5.1）
- **训练**（§4.2）：LoRA rank=32, α=64, dropout=0.05；AdamW lr=10⁻⁴, cosine, batch 32, 3 epochs；4-way balanced sampler
- **关键 ablation**：
  - Reasoning-with-Decision（先输出 reasoning 再输出 label）+7.2pp（§5.3）
  - Label-conditioned distillation 从 Gemini 2.5 Flash teacher 蒸馏 +23pp（§5.4）

**HUMA (arXiv 2511.17315)**

- **Router**（§4.1）：20 个细分策略 union 后投票；包含「timeliness」`T_s = min(1, k/N)` 时效因子，k = 当前消息距离上次发言的间隔，N 是窗口
- **Action Agent**（§4.2）：禁止并发 send_message；同一时间最多一条 in-flight message
- **Reflection**（§4.3）：每轮 1-sentence reflection 注入 system prompt
- **评测**：97-person human eval，AI 检测率 55.4%（HUMA） vs 46.7%（human baseline）≈ 不可区分

**SID-Bench (arXiv 2603.24144)**

- 沉默间隔 K = 3 连续 Interrupt 平滑（§3.2）——避免「短促沉默被误判为应该插话」
- 评测维度：interruption / completion / silent

#### 2.1.2 typing 延迟与全双工

**Full-Duplex-Bench v1.5 (arXiv 2507.23159)**

- 4 种 overlap 场景：
  1. user keeps talking while bot is speaking
  2. user interrupts mid-sentence
  3. bot interrupts user mid-sentence
  4. simultaneous start
- 指标：t_stop（bot 多久停止）/ t_resp（bot 多久开始回复）
- GPT-4o：Respond=0.78（合理回复率），t_stop=0.23s（被打断后 230ms 内停）

**Semantic VAD-as-DM (arXiv 2502.14145)**

- 0.5B 参数 LLM 输出 4 个控制 token：
  - `<start_speaking>` / `<start_listening>` / `<continue_speaking>` / `<continue_listening>`
- 没有「打字 → 发送」的固定时序，是连续语义流
- 关键启发：**控制 token 是分类标签**，不是状态机；可以把"该不该回"建模成 4-class，避开 binary 的硬切

**4-Category Interruption Taxonomy (arXiv 2501.01568)**

- §3：cooperative agreement / assistance / clarification / disruptive
- §4 决策规则：
  - `< 2 s remaining` → 忽略打断，继续
  - `< 5 s` 且 aggressive → 维持回复但内部 summarize（缩短）

**HumDial (arXiv 2604.21406, ICASSP 2026)**

- 双声道真录音；标注 turn boundary、overlap、laughter
- 用于 typing 延迟模型的「真实分布」基线

**Inoue 2025 (arXiv 2501.16643, IWSDS)**

- TEIDAN 三人对话语料
- GPT-4o 80.9% addressee（chance 80.6%）→ **几乎不学习**
- next-speaker prediction 46%（low chance 50%）

#### 2.1.3 频率 / typing 时长建模

**PBR FSM (ACL W13-4063)** — Speaking Pred / Silent Pred / Completion 三态机器
**ACL W15-4606 / W18-5011** — incremental + RL barge-in
**Rewarding Chatbots (arXiv 2303.06135, Chai)** — pseudo-label reward model + sample-rejection；MCL +70%, retention +30%

### 2.2 Part 3 维度（群语境 / addressee / topic / relationship）

#### 2.2.1 Addressee detection

**Multi-Party Hangover (EMNLP 2024 / arXiv 2409.18602)**

- §4.2：Response Selection ∝ 文本内容；Addressee Recognition ∝ 结构特征（degree centrality + average outgoing weight）
- 关键发现：**addressee 主要靠图结构（谁过去回过谁），不是文本**

**TV-MMPC (arXiv 2505.17536)**

- 4378 speakers + 5599 addressees + 3412 side-participants
- 3 个 binary 维度：addressed / ratified / attending
- 与 Goffman 1981 footing 框架一致——参与者不是 binary（说话/不说话），是三元

**SS-MPC (arXiv 2502.16920)**

- 不构建显式图；只用 prompt 中的对话历史
- BLEU-1 15.60% +3.91pp over baseline
- 对 Omubot 启发：**「群图结构」可以是 prompt 表征**，不需要单独图存储

#### 2.2.2 话题追踪

**EVOLVCONV (arXiv 2024.inlg-main.43)**

- §3：图谱 topic tracker + recommender + generator
- K-hop topic retrieval；user-pref Yes/No/Unknown 三分类

**Reflective Memory (ACL 2025-long.413)**

- §4：Prospective Reflection（事前预测）+ Retrospective Reflection（事后回顾）

**Memori (arXiv 2603.19935)**

- 三元组 (Subject, Predicate, Object) 链接到 summary
- 检索时对三元组做语义匹配

**membox (arXiv 2601.03785)**

- §3：Topic Loom 滑动窗 LLM 分类器 + Trace Weaver
- 68% F1 on LoCoMo

**AdaMem (arXiv 2603.16496)**

- §4：working / episodic / persona / graph 四层
- 问题驱动检索；target-participant resolution

**TiMem (arXiv 2601.02845)**

- Temporal Memory Tree
- 75.30% LoCoMo / 76.88% LongMemEval-S；recall length -52.20%

**Semantic Anchoring (arXiv 2508.12630)**

- §3：dependency parsing + coreference + discourse 混合
- +18% over RAG baseline

**Rhea (arXiv 2512.06869)**

- §4：Role-aware Instructional + Episodic
- MT-Eval +1.04pt / Long-MT-Bench+ +16% relative

#### 2.2.3 关系 / 好感度

**MPCA Survey (arXiv 2505.18845)**

- §5：未来方向 = ToM 集成，但**当前 80% 系统不做** relationship modeling
- §3：action-modeling 是 80% 共识；relationship 是 long tail

**Willingness-aware Sales Talk (COLING 2025 / arXiv 2412.19490)**

- §3：3 种 willingness 类型（disclose / engage / commit）
- 用对方 willingness 调节 bot 的 follow-up 强度
- 启发：**好感度可以建模为 willingness，不是 score**——是分类不是数值

**5-stage IM withdrawal**（**业界传播术语**，非学术结论 — v2 修订）

- 阶段 1: response latency creep（回复变慢）
- 阶段 2: emoji substitution（用 emoji 代替文字）
- 阶段 3: init-only（只主动发，不回复）
- 阶段 4: selective response（挑选性回复）
- 阶段 5: emotional withdrawal（情感撤离）

**v2 学术降级声明**：原版本将该 5 阶段挂为「学术结论」缺乏一手 paper 锚点；它本质是社媒 / 自媒体对组织行为学领域 telepressure 概念的二次叙事化重述。**真正可引文献**是 telepressure 的一手论文：

- **Barber & Santuzzi 2015** — *J. Occupational Health Psychology* 20(2), 172-189，DOI: 10.1037/a0038278。提出 **workplace telepressure** 概念（"对消息回应的强迫感 + 主动发起回应的紧迫感"）；§3 measurement scale 给出 6 题量表，§4 给出 latency 与压力相关的相关系数。
- **Cambier 2018** — *Occupational Health Science* 2(2)，DOI: 10.1007/s41542-018-0022-8。把 telepressure 扩展到下班后场景 + 与 burnout / withdrawal 的纵向相关。

**学术启示（替代原 5-stage 表面叙述）**：

1. **「回复延迟变慢」是关系冷却的可观察前兆**——这条结论由 Barber & Santuzzi 2015 §4.2 的 latency-stress 相关支撑，可作为 affection 5-档分类器的**输入信号**之一；
2. **「用 emoji 替代文字」不是独立阶段**——文献无支持；它可能是 register=playful 的 surface marker（与 Part 1 V8 的 register 标记冲突），也可能是 telepressure 应对策略，**Omubot 不应将其建模为关系阶段**；
3. **「情感撤离」是结果，不是阶段**——Cambier 2018 §3 区分 "behavioral withdrawal" 与 "emotional withdrawal" 是 burnout 的两种症状，不是序列化阶段；
4. **结论**：Omubot 的 affection 5-档不可照搬 5-stage IM withdrawal 表面叙述；§4.6 的 5-档应基于 Willingness-aware Sales Talk 的 disclose / engage / commit 三型 + telepressure 一手 latency 信号设计，而**不是**从社媒文献群里抄。

---

### 2.3 Sticker Reply Selection (SRS) 学术矩阵（v2 新增）

> 原 §2 不覆盖「sticker 形态决策」与「sticker 个性化检索」；v2 补 7 篇 SRS 学术对照。

| 编号 | 论文 / 系统 | 锚点 | 与 Omubot v2 的关系 |
|---|---|---|---|
| **A1** | EIGML — Emotion-aware sticker retrieval | AAAI 2025 #38509 / arXiv 2511.17587 §3 dual-encoder + emotion-aware contrastive loss | 借鉴：sticker library 加 emotion tag + dual-encoder retrieval 替代 LLM 自决 |
| **A2** | Int-RA / StickerInt — intent-aware retrieval | arXiv 2403.05427 §4 intent encoder + retrieval rerank | 借鉴：intent-aware rerank 在 emotion 候选基础上做精修 |
| **A3** | PerSRV — Personalized Sticker Retrieval | arXiv 2410.21801 §3 user-history embedding | 部分借鉴：persona_v2 freeze 已有 admin map，可扩 user-history embedding；但 P3.10 范畴 |
| **A4** | PEARL — RL sticker rerank | Findings of EMNLP 2025 #753 §4 reward = engagement signal | 不借鉴：Omubot 无 engagement RL 数据闭环；记入 long-tail |
| **A5** | IGSR — Image-Graph Sticker Retrieval | AAAI 2025 #34720 §3 visual graph propagation | 不借鉴：Omubot sticker library 无图片图谱标注；维护成本远超收益 |
| **A6** | U-Sticker — user-level long-tail | SIGIR 2025 §4 long-tail re-ranking | 借鉴：long-tail 信号（用户 sticker_id 调用频次）作为 fairness rerank 的输入 |
| **A7** | STICKERCONV / PEGS — emotion sticker dataset | arXiv 2402.01679 v2 §3 dataset + §4 PEGS framework | 借鉴：用其 emotion taxonomy（25 种细粒度情感）替换 DEFAULT_STICKER_USAGE_HINT 的"轻松闲聊"统一描述 |

**SRS 一致性结论**：A1 / A2 / A7 共识 — sticker 选择不应依赖 LLM 自决（散布度高 + 长尾收敛差），应走 retrieval（candidate pool ≤ 10）→ rerank（intent / emotion / persona）两阶段；与 §1.13 第 2 点根因结论一致。

### 2.4 Modality Decider 学术矩阵（v2 新增）

| 编号 | 论文 / 系统 | 锚点 | 与 Omubot v2 的关系 |
|---|---|---|---|
| **B1** | PhotoChat — image-or-text decider | ACL 2021 #479 §4 image intent classifier | 借鉴：modality 决策是独立分类器，不是 LLM 自决 |
| **B2** | MMDialog — multimodal turn dataset | ACL 2023 #405 §3 1.08M turns 标注 modality | 借鉴：modality 选择是 turn-level decision，不是 generation-level |
| **B3** | DribeR — dual-rail modality | arXiv 2310.14804 §3 双 transformer 头 | 部分借鉴：双轨设计可作为 P2.8 sticker_decision_provider 的接口 inspiration |
| **B4** | DIAEF — text + image multimodal emotion | Findings of ACL 2025 #93 §4 cross-modal emotion fusion | 借鉴：sticker 决策可读 mood slot 与文本 emotion 二者融合 |
| **B5** | Thanos — modality routing | arXiv 2411.04496 §3 router head | 不借鉴：Omubot 无图像 generation；router 简化为「文本 / 文本+sticker / 文本+image link / pure sticker」4 类即可 |

**Modality 一致性结论**：B1~B5 共识 — modality 决策应是**前置分类器**而非 generation-time 自决；Omubot 应借鉴 PhotoChat 的"image intent classifier"思路设 sticker_decision_provider，让其在 LLM 主调用**之前**给出"该不该发 sticker / 该用什么 modality"决策。

### 2.5 Emoji 误用基准（v2 新增）

| 编号 | 论文 / 系统 | 锚点 | 与 Omubot v2 的关系 |
|---|---|---|---|
| **C1** | eWe-bench — emoji misuse evaluation | Findings of ACL 2025 #660 §3 错误类型 taxonomy（情绪不匹配 / 过度使用 / 文化错位） | 借鉴：eWe taxonomy 可作为 v2 灰度采样判定"异常 sticker"的标注框架 |

**结论**：eWe-bench 把 emoji 误用分为 (1) emotion mismatch (2) overuse (3) cultural misuse 3 类。Omubot v2 用户报告的"异常 sticker"主要落在 (1) + (2) 两类，与 §1.13 根因结论一致。

### 2.6 Long-tail / Cold-start 学术对照（v2 新增）

| 编号 | 论文 / 系统 | 锚点 | 与 Omubot v2 的关系 |
|---|---|---|---|
| **D1** | U-Sticker long-tail rerank | SIGIR 2025 §4 | sticker_id 调用频次直方图 fairness rerank：把过度集中的 2~3 张降权 |
| **D2** | PerSRV cold-start | arXiv 2410.21801 §5 | 新群 / 新对象时退回 emotion-only retrieval；persona_v2 admin map 还未学到该 user 偏好时使用 |

### 2.7 Emotion-policy 非 prompt 通道（v2 新增）

| 编号 | 论文 / 系统 | 锚点 | 与 Omubot v2 的关系 |
|---|---|---|---|
| **A1** | ESDP — Emotion Support Dialogue Policy | Sci. Rep. 2024 / 10.1038/s41598-024-70463-x §3 policy network | 借鉴：mood 状态 → 回复策略的映射应是 lookup table 不是 prompt 字符串 |
| **A2** | EmoDynamiX — multi-agent emotion policy | arXiv 2408.08782 §4 emotion-aware dispatch | 部分借鉴：当前 Omubot 单 agent 不需要 multi-agent；但其 emotion → action 映射表可借 |
| **A3** | DialogXpert — RL dialog strategy | arXiv 2505.17795 §3 strategy RL | 不借鉴：缺 reward 数据闭环 |
| **A4** | Self-Emotion — LLM 自影响情绪 | arXiv 2408.01633 §4 内部情绪反馈 | 借鉴：mood slot 应来自外部信号（用户语气、回复延迟），**不是**让 LLM 自决；避免 self-reinforcement |
| **A5** | PELD — Personality + Emotion Dialogue | arXiv 2404.07229 §3 PELD dataset | 借鉴：emotion 与 persona 应解耦——mood 是短时态变量，persona 是长时不变量 |
| **A6** | EmPO — Emotion Preference Optimization | （扩展引用） | 长 tail，记入 §6.5 候选 |
| **A7** | ReflectDiffu — diffusion emotion | （扩展引用） | 长 tail，记入 §6.5 候选 |

**Emotion-policy 一致性结论**：A1 + A2 + A4 共识 — mood 应通过**结构化通道**（slot / lookup table）影响下游决策，而**不是**通过 prompt 字符串。这与 Omubot v2 mood RuntimeStateBus slot 设计方向一致（§4.6）。A4 还提供了重要警示：mood 信号应外源，避免让 LLM 自决 mood 形成 self-reinforcement。

### 2.8 Persona / Familiarity 学术对照（v2 新增）

| 编号 | 论文 / 系统 | 锚点 | 与 Omubot v2 的关系 |
|---|---|---|---|
| **B1** | SPDA / AutoPal — dual-agent persona | arXiv 2406.13960 §3 persona-aware reward | 借鉴：persona_v2 已有结构化 admin map；不需重做 |
| **B2** | LD-Agent — long-distance persona agent | arXiv 2406.05925 §4 long-context persona consistency | 借鉴：affection 5-档可读 persona_v2 的 freeze 时间戳作为长程 anchor |
| **B3** | Commonsense Persona | （文献群） | 长 tail |
| **B4** | DEEPER — persona retrieval | （文献群） | 长 tail |
| **B5** | Intimacy LREC-COLING 2024 | LREC-COLING 2024 long.322 §4 5 级亲密度量表 | 借鉴：5 级亲密度量表正好对应 v2 affection 5-档的设计标尺 |

**Persona 一致性结论**：B1 + B5 共识 — persona / familiarity 应是结构化分类（5 级 / persona profile），不是数值 score；与 §1.7 MaiBot dead code 警示与 §4.6 affection 5-档设计方向一致。

### 2.9 Companion bot 工业实践（v2 新增）

| 编号 | 系统 | 锚点 | 与 Omubot v2 的关系 |
|---|---|---|---|
| **C1** | Fang MIT/OpenAI — heavy companion use study | arXiv 2503.17473 §4 4-week field study | 警示：长期高频 sticker 使用与孤独感正相关；Omubot 应**降低** sticker 频次而非提升 |
| **C2** | Inflection Pi — 88-dim emotion vector | industrial whitepaper | 不借鉴：88 维过度复杂；Omubot 只需 3-5 维 mood（happy / calm / tired / playful + intensity） |
| **C3** | Anthropic Soul Document | industrial design pattern | 已对照：Omubot identity.md 即对应 Soul Document 设计模式；v2 应保持 identity.md 不动 |
| **C4** | Replika industrial | （public docs） | 警示：affection 数值会被刷分逆模型；Omubot **不做数值好感度** |
| **C5** | Character.AI industrial | （public docs） | 警示：persona drift 在长上下文常态化；persona_v2 freeze + 短 mood 状态正交是更好设计 |

### 2.10 Affective State Machine（v2 新增）

| 编号 | 论文 / 系统 | 锚点 | 与 Omubot v2 的关系 |
|---|---|---|---|
| **D1** | FiSMiness — Finite State Machine for emotion | arXiv 2504.11837 §3 5-state FSM + transition matrix | 借鉴：mood 状态机用 5 态 FSM 而非 88-dim 向量 |
| **D2** | TransESC — Transition for ESC | ACL 2023 findings.6725 §4 transition reward | 借鉴：mood 状态转移应基于"输入信号 + 转移规则"，不是 LLM 自决 |
| **D3** | AFlow — affective flow | （文献群） | 长 tail |

**State Machine 一致性结论**：D1 + D2 共识 — mood 是有限态机（≤ 7 状态），转移由外部信号触发；与 §4.6 设计方向一致：mood RuntimeStateBus slot 应是 5-7 态 enum，不是连续向量。

---

## 3 借鉴维度与不可借鉴维度（Part 2）

### 3.1 节奏控制层

| 维度 | MaiBot 现状 | 学术对照 | Omubot 现状 | 借鉴判断 |
|---|---|---|---|---|
| **是否回复 binary** | planner LLM 选 reply / no_reply + force_reply 兜底 | Speak-or-Silent: 64.45% bal-acc + Reasoning+7.2pp | bot.py:on_message 单层（基于 at_only / debounce / batch） | **借鉴**：引入 reasoning-first 二分类（Part 1 V11 的 critic 已有「先 reason 再 act」雏形，可复用） |
| **频率门 talk_value** | `random()<talk_value×adjust` 一刀切 | HUMA timeliness `T_s=min(1,k/N)` 时效因子 | 无 | **借鉴**：引入「时效因子」让最近活跃群更倾向回复，长期沉默群倾向不回 |
| **动态阈值** | consecutive_no_reply 3/5 阶梯 | SID-Bench K=3 连续平滑 | 无 | **借鉴但简化**：3 阶段就够，不上 K=3 平滑（数据量不足） |
| **idle sleep 10/0.2/0.1/3 s** | 硬编码 | Full-Duplex-Bench v1.5 t_resp 0.78s 基线 | services/llm 无主循环 | **不借鉴**：Omubot 是 event-driven NoneBot2 + debounce，**不需要主循环** |
| **典型 typing 时长** | 0.3s 中文 / 0.15s 英文 / emoji 1s | Full-Duplex t_resp / HumDial 真实分布 | services/humanizer.py 已有 typing 模拟（Part 1 U3） | **部分借鉴**：单字 0.3s 系数已经在 Part 1 U3；新增点是 emoji 1s 起步价 + thinking 10s 兜底 |
| **首段 typing=False** | 显式选择 | — | 全部 typing on | **借鉴**：让首段「突然出现」更接近真人；Part 5 P5.2 inter_segment_delay 接入 |
| **多段 max=3 / 8** | response_splitter | HUMA 禁止并发 send_message | reply_segmentation.max_send_segments=0 | **借鉴**：硬上限 8 段（与 MaiBot 模板一致），超出合并 |
| **notify packet drop** | 显式不感知"对方在打字" | — | 同 | **不借鉴**：QQ 不暴露 typing 信号，无法引入 |

### 3.2 是否回复仲裁层

| 维度 | MaiBot 现状 | 学术对照 | Omubot 现状 | 借鉴判断 |
|---|---|---|---|---|
| **planner LLM 决策** | LLM 选择 reply/no_reply（带 reasoning） | HUMA Action Agent + Speak-or-Silent reasoning-first | **无独立 planner**——基于 schedule 直接 reply | **借鉴**：引入轻量 planner（Part 1 V11 critic loop 可扩展） |
| **force_reply 兜底** | is_at → 删 no_reply → 插 reply | HUMA Router 20 strategies vote | at_only=true 时全 reply | **借鉴但收紧**：只在 `is_at + addressee=self` 时兜底，不依赖 substring |
| **「已读不回」三表面** | planner / 频率门 / 阈值升级 | 5-stage IM withdrawal | 无 | **借鉴**：Part 3 §4.3 引入 register-aware「冷淡档」 |
| **多 @ 仲裁后到优先** | loop overwrite | Multi-Party Hangover degree centrality | 无 | **借鉴但改算法**：先看 degree centrality（最近群内对话频次），相同时再 last-wins |

### 3.4 Sticker 决策（v2 新增）

> 对应 §1.13 4 触发源对比 + §2.3 SRS 学术矩阵 + §2.5 emoji misuse 基准。

| 维度 | Omubot v1 现状 | 学术 / MaiBot 对照 | 借鉴判断 |
|---|---|---|---|
| **触发源** | 4 路无互斥（frequently 强制 + kaomoji_enforce + thinker.sticker + LLM tool_call） | MaiBot 单源 + emoji_chance=0.6 概率门 + planner 单选 reply/emoji | **必修**：4 路统一进 sticker_decision_provider 单决策点；引入互斥锁（同一回复最多 1 sticker） |
| **kaomoji_enforce 强制轮** | 无条件检测无 kaomoji 即多发 1 轮 LLM | MaiBot 无此概念 | **必修**：拆解为「register=playful + mood ∈ {high, playful}」时才启用，否则关闭 |
| **frequently 档强制 prompt** | "每次回复都必须调用 send_sticker，不发就是事故" | MaiBot 用 `action_require` 文字提醒"不要连发" | **必修**：把 frequently 档拆为 (a) prompt 软提示（"建议但不强制"）+ (b) sticker_decision_provider 概率提升至 0.7（仍可被 mood 关掉） |
| **选择算法** | LLM 100% 自决 + 整库 dump | EIGML §3 dual-encoder retrieval / Int-RA §4 intent rerank / MaiBot Levenshtein top-10 + random.choice | **必修**：candidate pool ≤ 10（emotion tag 过滤）→ rerank（intent + persona）→ random.choice |
| **DEFAULT_STICKER_USAGE_HINT** | "群友常用表情，适合轻松闲聊或回应同类情绪" 统一描述 | STICKERCONV §3 25 类细粒度 emotion taxonomy | **必修**：替换为按 sticker 单独标注的 emotion tag（与 §6.4 P2.14 sticker semantic re-caption 对接） |
| **长尾收敛 (2 ids = 73%)** | 无 fairness rerank | U-Sticker SIGIR 2025 §4 long-tail rerank | **借鉴**：sticker_id 调用频次直方图 fairness rerank（过度集中的 id 降权 0.5） |
| **冷启动** | 无（新群直接走 LLM 自决） | PerSRV §5 emotion-only fallback | **借鉴**：persona_v2 admin map 未学到该 user 偏好时退回 emotion-only retrieval |

### 3.5 视频 / URL 处理（v2 新增）

> 对应 §1.10 MaiBot 也没做 + 用户原话点出"视频链接等等额外信息的处理"。

| 维度 | Omubot v1 现状 | 学术 / MaiBot 对照 | 借鉴判断 |
|---|---|---|---|
| **URL 透传** | 文本透传，LLM 拿到裸 URL | MaiBot 同 | **借鉴 MaiBot 的"先不做"**：v2 阶段先做 og:title 注入（最小代价、最大收益），不做完整页面抓取 |
| **og:title 注入** | 无 | 无（学术界 dialog 系统普遍未集成） | **新做**：services/url_meta/og_title.py 新模块；轻量 GET + opengraph 解析；接入 PromptBuilder group context 块（§5.6） |
| **bilibili / youtube 视频元信息** | 无 | 无 | **不做（Part 4 范畴）**：需要专用 adapter 与配额管理；P2.11 候选保留 |
| **抓取超时 / 失败兜底** | N/A | N/A | **新做**：500ms timeout，失败静默不注入（不影响 LLM 主调用） |
| **隐私 / 敏感 URL 过滤** | 无 | 无 | **必修**：黑名单（admin / banking / private domain）；只在白名单或公共内容站点走 fetch |

### 3.6 输出能力（modality decider，v2 新增）

> 对应 §1.11 MaiBot 多媒体能力存在但 group reply 不调度 + §2.4 modality decider 矩阵。

| 维度 | Omubot v1 现状 | 学术 / MaiBot 对照 | 借鉴判断 |
|---|---|---|---|
| **可输出 modality** | text / sticker；无 image / voice 输出 | MaiBot 接口面有 send_image/voice/forward；group reply 仅 send_text 调度 | **现状保持**：v2 不引入 image / voice 输出；P3.10 候选保留 |
| **modality decision 时机** | LLM 主调用内 tool_call 自决 | PhotoChat ACL 2021 §4 前置分类器 + MMDialog §3 turn-level decision | **必修**：sticker_decision_provider 在 LLM 主调用**前**给出"该不该发 sticker"决策；LLM tool_call 仅作为执行通道 |
| **mixed-modality 输出** | text + sticker 在同一回复并发 | MaiBot planner 单选 reply/emoji（互斥） | **借鉴**：text + sticker 同回复保留（P5 段拆分前提下），但限制为「最后一段是 sticker，且整回复最多 1 sticker」 |
| **modality routing** | 无 | Thanos arXiv 2411.04496 §3 router head | **简化借鉴**：4 类（text-only / text+sticker / sticker-only / text+url-card）作为 sticker_decision_provider 输出空间，不做完整 router |

### 3.7 mood 渗透（v2 新增）

> 对应 §1.12 MaiBot mood 全空缺 + §2.7 emotion-policy 矩阵 + §2.10 affective state machine。

| 渗透点 | Omubot v1 现状 | 学术对照 | 借鉴判断 |
|---|---|---|---|
| **mood 数据来源** | 无 RuntimeStateBus slot；仅 prompt 文字 | Self-Emotion arXiv 2408.01633 §4 警示自决会 self-reinforce | **必修**：mood 信号来自外部（用户语气词分类 + 回复延迟 + sticker 调用密度），**不**让 LLM 自决 |
| **mood slot 形式** | 无 | FiSMiness arXiv 2504.11837 §3 5-state FSM | **必修**：MOOD_CURRENT_SLOT 定义为 5-7 态 enum（calm / playful / tired / cold / focus + low/high intensity）；通过 RuntimeStateBus 注入 |
| **mood → sticker 概率** | 无（4 路触发源不读 mood） | EIGML §3 emotion-aware retrieval | **必修**：sticker_decision_provider 读 MOOD_CURRENT_SLOT，cold/tired 时 send_probability ≤ 0.1；playful 时 ≤ 0.7 |
| **mood → typing 时长** | Humanizer 单字系数（与 register 关联，与 mood 无关） | TransESC ACL 2023 §4 transition reward | **借鉴**：mood=tired 时 typing 系数 ×1.3；mood=playful 时 ×0.8；与 §6.4 P3.8 接 |
| **mood → 段间延迟** | Part 5 P5.2 已就位 inter_segment_delay 公式 | — | **借鉴**：在 P5.2 公式里加 mood 系数表（cold ×1.5 / playful ×0.7） |
| **mood → addressee** | 无 | EmoDynamiX arXiv 2408.08782 §4 multi-agent dispatch | **借鉴**：mood=cold 时 addressee detector confidence 阈值收紧（0.95 → 严格只回 @ + 引用） |
| **mood → topic drift 反应** | 无 | Reflective Memory ACL 2025 §4 prospective | **借鉴**：mood=playful 时主动接话；mood=tired 时不接新话题（与 P3.7 接） |
| **affection 5-档** | 无（仅 familiarity binary） | Intimacy LREC-COLING 2024 long.322 §4 5 级量表 + Willingness-aware §3 三型 | **必修**：affection 5-档（stranger / acquaint / familiar / close / withdraw）；输入信号 = 历史互动密度 + 回复延迟 + register 一致性；输出 = sticker_probability + register prefer + topic depth |

### 3.3 不借鉴维度

- **MaiBot 主循环 `_loopbody`**——Omubot 是 NoneBot2 event-driven，不需要 polling
- **`talk_value × talk_frequency_adjust` 的零下限 `1e-7`**——这是 MaiBot 调试残留（避免 0×0）；Omubot 直接 `enabled=False` 关掉群即可
- **Dead code（interest, is_mute, relationship, topic_cache 反读）**——Part 1 §1.8 已列；Omubot 不能照抄
- **PFC 状态机**（chat_states.py 1-50）——MaiBot 自己也没用，legacy

---

## 4 借鉴维度与不可借鉴维度（Part 3）

### 4.1 @ / addressee 检测

| 维度 | MaiBot 现状 | 学术对照 | Omubot 现状 | 借鉴判断 |
|---|---|---|---|---|
| **@ 7 层级联** | adapter / prior / seg / regex / quote / nickname / force | Multi-Party Hangover 结构 + 文本双信号 | bot.py 单层 at_only 判定 | **借鉴**：剥离前 4 层（确定性强）+ 第 5 层 quote-reply；不上 nickname substring（噪声大） |
| **`@<name:id>` regex** | platform-aware QQ format | TV-MMPC structural | 同 | **借鉴**：QQ CQ:at format 已可解析，强化 regex |
| **5 种 quote-reply 变体** | regex 列表 | TV-MMPC ratified vs attending | 无 | **借鉴**：CQ:reply 已有现成 parser |
| **nickname substring** | 第 6 层 fallback | SS-MPC 无显式图 | 无 | **不借鉴**：QQ 群中昵称冲突高（"小明"、"小美"），substring 误判风险大 |
| **多 @ last-wins** | loop overwrite | Multi-Party Hangover degree centrality | 无 | **借鉴但改**：先 degree centrality，相同再 last-wins |
| **is_at 强制 reply=1.0** | 硬切 | HUMA Router 20 strategies | at_only=true | **借鉴但加 confidence 门**：只在 is_at confidence>0.9 才硬切 |

### 4.2 话题追踪

| 维度 | MaiBot 现状 | 学术对照 | Omubot 现状 | 借鉴判断 |
|---|---|---|---|---|
| **离线 ChatHistorySummarizer** | reply path 不读 | EVOLVCONV K-hop / membox Topic Loom | 无 | **借鉴但移到 in-line**：reply path 实时读 last 3 messages 的 topic embedding，不参与决策只参与 prompt 注入 |
| **80 messages 批量触发** | 离线 batch | TiMem Temporal Memory Tree | 无 | **不借鉴**：Omubot 群消息密度低，80 阈值太高 |
| **8h elapsed cold trigger** | 离线 batch | EVOLVCONV K-hop | 无 | **借鉴但参数化**：可调，建议 4h |
| **difflib.SequenceMatcher 0.9** | 主题去重 | Semantic Anchoring +18% over RAG | 无 | **不借鉴**：太粗；用 embedding cosine 替代 |
| **`TopicCacheItem(topic, messages, participants)`** | 数据结构 | Memori 三元组 | 无 | **借鉴**：但去掉 messages 字段（占空间），只留 topic + participants |

### 4.3 已读不回

| 维度 | MaiBot 现状 | 学术对照 | Omubot 现状 | 借鉴判断 |
|---|---|---|---|---|
| **planner no_reply action** | LLM 自决 | HUMA Action Agent | 无 | **借鉴**：Part 2 §3.2 planner 已经覆盖 |
| **频率门 sleep(10s)** | 硬编码 | 5-stage IM withdrawal stage 1 | 无 | **不借鉴 sleep 形式**：Omubot 是 event-driven，没必要 sleep；改成「跳过本条」即可 |
| **阈值升级 3/5** | consecutive_no_reply | Speak-or-Silent S1/S2 | 无 | **借鉴**：consecutive_no_reply 计数 + 升级 threshold |
| **read_mark prompt 提示** | 喂给 LLM 的边界 marker | Reflective Memory Prospective | 无 | **借鉴**：在 group context block 加 read_mark marker（一行） |

### 4.4 关系 / 好感度

| 维度 | MaiBot 现状 | 学术对照 | Omubot 现状 | 借鉴判断 |
|---|---|---|---|---|
| **person_info 表** | 不读 | Rhea Role-aware | services/persona 有 admin map | **借鉴**：person_info 已存 admin / nickname；扩展 willingness 字段 |
| **relation_info_block 拼接** | commented out | Willingness-aware Sales Talk | 无 | **不借鉴**：MaiBot 自己都关掉了，证明这条路有问题 |
| **好感度数值** | 无 | 5-stage IM withdrawal 阶段化 | 无 | **不借鉴 score**，**借鉴 stage**：5 阶段分类 disclose / engage / commit / withdraw / cold |
| **「对 X 好感 +1」运行时通道** | 无 | Rhea Episodic | 无 | **不做**：复杂度远超本期；Part 4 范畴 |

### 4.5 mood × addressee × topic 联动（v2 新增）

> 把分散在 §3.7 / §4.1 / §4.2 / §4.3 的渗透点合成一张端到端联动表。

| 触发组合 | 行为 | 学术 / 工程依据 | 实现锚 |
|---|---|---|---|
| mood=cold + addressee≠self | **不回**（即使 register 是 active） | EmoDynamiX §4 dispatch；Self-Emotion §4 警示 | binary_planner 读 mood slot |
| mood=cold + addressee=self（@/引用） | **回，但短**（max_segments=1，typing 系数 ×1.3） | telepressure latency creep（Barber 2015 §4.2） | inter_segment_delay 公式 + Humanizer |
| mood=playful + topic_drift>0.6 | **主动接话题**（critic 偏向 elaborate） | Reflective Memory ACL 2025 §4 prospective | V11 critic prefer rule 加 mood gate |
| mood=tired + topic 是新话题 | **不接新话题**（仅延续旧话题） | TransESC §4 transition reward | topic_drift detector 输出附加 mood mask |
| mood=playful + register=playful | **sticker_probability=0.7**（在 sticker_decision_provider 内） | EIGML §3 emotion-aware | sticker_decision_provider §3.4 |
| mood=cold/tired | **sticker_probability=0.1**（关闭 frequently 强制） | eWe-bench §3 overuse 类 | sticker_decision_provider §3.4 |
| affection=stranger | **register 偏 neutral**（不用 nickname、不接私话题） | Intimacy LREC-COLING §4 stranger 级 | affection_classifier §4.6 |
| affection=close + addressee=self | **register 可主动 playful**；sticker_probability ×1.2 | Willingness-aware §3 disclose 型 | affection_classifier §4.6 |
| affection=withdraw | **回复延迟分布上移 +30%**（不主动）；sticker_probability ≤ 0.05 | telepressure stage 1 latency creep | inter_segment_delay + sticker_decision_provider |

**联动原则**：

1. **mood 是短时态**（小时级），**affection 是长时态**（周级）；不让两者互相直接覆写。
2. **mood / affection 都不动 V8 stylometric scorer**（5 轴本质是文体特征，与情绪正交）。
3. **所有渗透点都通过 RuntimeStateBus slot 单向流入决策**——禁止下游直接读 identity.md / config 计算 mood，防止 prompt-fact 漂移。
4. **冲突仲裁**：affection > mood > register（长时态优先级最高，短时态次之，文体最低）。

### 4.6 affection 5-档 + RuntimeStateBus slot 设计（v2 新增）

> 对应 §1.12 MaiBot dead code 警示 + §2.8 persona 矩阵 + §2.10 affective state machine + §2.2.3 telepressure 一手锚点。

#### 4.6.1 mood RuntimeStateBus slot

新增 slot：`MOOD_CURRENT_SLOT`

```python
# services/humanization/contract.py 追加
MOOD_CURRENT_SLOT = "humanization.mood.current"  # FiSMiness 5-7 态 enum
```

值域（5 态 + intensity 二级）：

| 状态 | intensity ∈ {low, mid, high} | 触发信号 |
|---|---|---|
| `calm` | mid 默认 | 默认 / 长时无强信号 |
| `playful` | low/mid/high | 用户连续语气词 + sticker 高密度 + register=playful |
| `tired` | low/mid/high | 用户回复延迟 +200%（rolling 1h）+ register=concise |
| `cold` | low/mid/high | 用户连续短回复（≤ 5 char）+ 无 sticker + 沉默间隔 +300% |
| `focus` | low/mid/high | 长段技术话题 + topic_drift 持续低 |

**信号计算**（v2 设计稿，不实现）：

- **不允许 LLM 自决 mood**——避免 Self-Emotion arXiv 2408.01633 §4 的 self-reinforcement
- **输入信号**：
  - 用户最近 5 条回复的平均长度
  - 用户最近 5 条回复的延迟分布
  - 用户最近 10 条 sticker 调用密度
  - 用户最近 10 条语气词命中率（"哈哈" / "555" / "草" / "好的"）
- **状态转移**：FiSMiness §3 5-state FSM；每 5 分钟或每条新用户消息触发一次评估

#### 4.6.2 affection 5-档 slot

新增 slot：`AFFECTION_STAGE_SLOT`

```python
# services/humanization/contract.py 追加
AFFECTION_STAGE_SLOT = "humanization.affection.stage"  # 5-档 enum
```

值域（基于 Intimacy LREC-COLING 2024 5 级量表 + Willingness-aware 三型 + telepressure 一手锚点）：

| 档位 | 定义 | 信号（输入） | 行为（输出） |
|---|---|---|---|
| `stranger` | 互动 ≤ 5 次 / 月，平均回复延迟 > 3min | 累计互动数 + 延迟 | register 偏 neutral；不主动接话；不用 nickname |
| `acquaint` | 5 < 月互动 ≤ 30，延迟 1~3min | 累计互动数 + 延迟 + register 一致性 | register 可 playful；可用 nickname |
| `familiar` | 30 < 月互动 ≤ 100，延迟 < 1min | 累计互动数 + 延迟 + sticker 调用密度 | register 偏 playful；可主动接话；sticker_probability ×1.0 |
| `close` | 月互动 > 100，延迟 < 30s，topic 深度 > 阈 | 累计 + 延迟 + topic 深度 | register 自由；sticker_probability ×1.2 |
| `withdraw` | 30d 互动密度下降 > 50% + 延迟上升 > 100% | latency creep（Barber 2015 §4.2 一手锚点） | 不主动；回复变短；sticker_probability ≤ 0.05 |

**信号计算**：

- **持久化**：affection_stage 写入 `services/persona/` 的 admin map 扩展字段（与 persona_v2 freeze artifacts 共表）
- **更新频率**：每 24h 滚动一次（mood 是分钟级，affection 是天级）
- **冷启动**：未学到记录 → `stranger` 档（fallback safe）
- **回退路径**：feature flag `humanization.affection_enabled=false` → 退回 `acquaint` 默认档

#### 4.6.3 与已有 slot 的关系

| 已有 slot | 与 mood / affection 的关系 |
|---|---|
| `REGISTER_LABEL_SLOT` | mood 与 register 正交；register 是文体（长 vs 短），mood 是情绪（hot vs cold） |
| `AFFECTION_FAMILIARITY_SLOT`（v1） | **被 AFFECTION_STAGE_SLOT 替代**；v1 binary 升级为 5-档 enum；保留 v1 slot 作为 fallback（feature flag off 时） |
| `THINKER_LAST_DECISION_SLOT` | thinker 不再决策 sticker bool；改由 sticker_decision_provider 单点出 |
| `CLOCK_CURRENT_SLOT` | 与 mood 正交；clock 是物理时间（凌晨偏低能量），mood 是心理状态 |

#### 4.6.4 出口路径（与 §3.4 / §3.7 接）

mood / affection slot 注入路径：

```text
RuntimeStateBus
   │
   ├─→ sticker_decision_provider（§3.4）  → send/not + 选 id
   ├─→ inter_segment_delay 公式（§3.7 + Part 5 P5.2）→ 段间 sleep 系数
   ├─→ Humanizer typing 系数（§3.7）  → 单字延迟 ×0.7~×1.3
   ├─→ binary_planner（§3.2）  → 加 mood / affection gate
   └─→ addressee_detector（§4.1）  → confidence 阈值动态化
```

**禁止路径**：mood / affection **不写入** identity.md prompt 文字（避免 prompt-fact 漂移），仅通过 RuntimeStateBus 注入下游决策器。

---

## 5 与 Omubot Part 1 / Part 5 主线接入点

### 5.1 与 Part 1 已落地子任务的接口

| Part 1 子任务 | Part 2/3 复用 / 扩展点 |
|---|---|
| **U1 segmentation 双实现合并** | Part 2 P2.3 段间延迟在合并后的单一 segmentation 模块上挂钩；Part 5 同源依赖 |
| **U3 Humanizer register-aware** | Part 2 P2.4 typing 字符系数（emoji 1s 起步价）扩展 Humanizer，不另起模块 |
| **V1 RegisterClassifier** | Part 2 P2.2 是否回复决策读 register 标签（quiet 档减回复率）；Part 3 P3.1 addressee confidence 与 register 联动 |
| **V8 StylometricScorer** | Part 3 P3.2 topic drift detector 不动 V8 5 轴；topic 输出走独立 channel |
| **V11 critic-rewrite-loop** | Part 2 P2.2 LLM planner 复用 critic 框架（先 reason 再 act）——不新起 LLM 调用 |
| **V12 admin SPA 灰度** | Part 2/3 全量 feature flag 走 V12 同一旗标系统（[humanization] 段） |

### 5.2 与 Part 5（segmentation）的接口

| Part 5 任务 | Part 2/3 接口 |
|---|---|
| **P5.1 natural_split 算法** | Part 2 段间延迟函数读 P5.1 的 segments[i].length 计算下一段 sleep |
| **P5.2 inter_segment_delay** | Part 2 P2.4 typing 时长合并到 inter_segment_delay 公式 |
| **P5.3 client.py 切换** | Part 2 P2.3 同 PR 落地，避免双重重构 |

### 5.3 与 Persona v2 的接口

- Part 3 P3.4 person_info 扩展读 **persona_v2 freeze artifacts 的 admin map**——不另起 person 表
- Part 3 P3.3 read_mark prompt 在 PromptBuilder 第 2 块（group context）注入，与 v2 selector 第 1 块（identity）正交

### 5.4 与现有 services/ 的接口

| 现有模块 | Part 2/3 改动 |
|---|---|
| [plugins/chat/plugin.py](../../plugins/chat/plugin.py) `group_listener` | Part 2 P2.2 替换 `at_only / debounce` 单层判定为 planner 二分类 |
| [services/llm/client.py](../../services/llm/client.py) `_reply_segments` | Part 2 P2.3 段间延迟函数替换 `_SEGMENT_DELAY=0.8` |
| [services/humanizer.py](../../services/humanizer.py) | Part 2 P2.4 emoji 起步价 + thinking 兜底 |
| [services/group/timeline.py](../../services/group/timeline.py) | Part 3 P3.1 addressee detector 读 timeline；P3.2 topic detector 同 |
| [services/persona/](../../services/persona/) | Part 3 P3.4 admin map 扩展 willingness stage |
| [kernel/router.py](../../kernel/router.py) | 不动；Part 2/3 的 hook 全部走 ctx 注入 |

### 5.5 不接入的现有模块

- [services/scheduler/](../../services/scheduler/)——Part 2/3 不做 scheduler；Dream agent 与节奏控制正交
- [services/episodic/](../../services/episodic/)——Part 3 不动 episodic store；topic detector 是新通路
- [services/block_trace/](../../services/block_trace/)——观测层不参与决策

### 5.6 v2 接入点扩展

> 对应 §3.4 / §3.5 / §3.6 / §3.7 / §4.5 / §4.6 的具体落点。

#### 5.6.1 sticker_decision_provider 接入

**新模块**：`services/sticker/decision_provider.py`

**接入路径**：

| 现有触发源 | v2 收敛后 |
|---|---|
| [plugins/sticker/plugin.py:78-87](../../plugins/sticker/plugin.py#L78-L87) `frequently` 强制 prompt | 改为 prompt 软提示 + sticker_decision_provider.send_probability=0.7（受 mood 调制） |
| [services/llm/client.py:2485-2506](../../services/llm/client.py#L2485-L2506) `kaomoji_enforce` 强制轮 | 拆解：仅 register=playful + mood ∈ {playful, happy} 时启用 |
| [services/llm/thinker.py:117-133](../../services/llm/thinker.py#L117-L133) thinker.sticker bool | 移除自决；thinker 仅产出 register / energy 上下文，不决 sticker |
| LLM 自由 tool_call `send_sticker` | 由 sticker_decision_provider 守门：未授权 → tool 拒绝执行 |

**接口签名（设计稿）**：

```python
class StickerDecision(NamedTuple):
    should_send: bool
    candidate_pool: list[str]   # ≤ 10 sticker_id
    rerank_strategy: str         # "emotion" | "intent" | "persona"
    cooldown_ms: int             # 当前回合后冷却

async def decide(
    bus: RuntimeStateBus,
    register: str,
    mood: MoodState,
    affection: AffectionStage,
    last_sticker_used: list[str],
) -> StickerDecision: ...
```

#### 5.6.2 mood RuntimeStateBus slot 接入

**新 slot**：`MOOD_CURRENT_SLOT` + `AFFECTION_STAGE_SLOT`（参 §4.6）

**写入路径**：

| 信号源 | 写入触发 | 写入频率 |
|---|---|---|
| `services/group/timeline.py` 用户回复延迟 rolling | 每条新用户消息 | event-driven |
| `services/llm/thinker.py` register 一致性观察 | thinker 产出 register 时 | 每回合 |
| sticker / kaomoji 调用密度 | sticker_decision_provider 自反馈 | 每回合 |
| `services/persona/runtime.py` admin map 互动累积 | 每 24h 滚动 | scheduler |

**读取路径**（已在 §4.5 展开）：sticker_decision_provider / inter_segment_delay / Humanizer / binary_planner / addressee_detector

#### 5.6.3 og:title 注入接入

**新模块**：`services/url_meta/og_title.py`

**接入路径**：

| 触发点 | 行为 |
|---|---|
| `services/group/timeline.py` 入消息含 URL（黑名单过滤后）| 异步 fetch（500ms timeout） |
| `services/llm/prompt_builder.py` group context 块 | 把 og:title 作为 1-line 注入"用户消息：[URL] (标题：xxx)" |

**约束**：

- 黑名单：admin / banking / private domain（不抓取）
- 白名单优先：bilibili / youtube / 微博 / GitHub / arXiv / 主流新闻站
- 抓取失败静默不注入（不阻塞 LLM 主调用）
- 缓存：URL → og:title 24h LRU

#### 5.6.4 video adapter（占位）

**P2.11 候选保留**：bilibili / youtube 视频元信息抓取，本期不做；记入 §6.4 long-tail。

#### 5.6.5 不接入的现有模块（v2 追加）

- `services/dream/`——Dream agent 仅维护 sticker library / memory，不参与 v2 决策路径
- `services/scheduler/`——同 v1，不接 mood / affection
- `kernel/router.py:572,597`——B2/B3 hook 不动；mood / affection slot 通过 RuntimeStateBus 注入，不走 router 改造

---

## 6 候选子任务清单（P2.x / P3.x）

> 本节给出**草案**；具体编号、依赖、预算待 Part 1 主线收口后单独立项。

### 6.1 Part 2 候选子任务

| 编号 | 任务 | 依赖 | 关键产物 | 预估行数 | 关键测试 |
|---|---|---|---|---|---|
| **P2.1** | 节奏度量基线（采样脚本） | Part 1 V12 灰度可读 | `scripts/dev/measure_rhythm.sh` 采样 200 条 group reply 的回复延迟 / 段间间隔 / 段数分布 | 60 | 无（数据收集） |
| **P2.2** | LLM planner 二分类（reasoning-first） | Part 1 V11 完成 | `services/reply_planner/binary_planner.py` ≤ 180 行；reasoning + decision 两段输出；read register / context | 180 | tests/test_binary_planner.py +12 |
| **P2.3** | inter_segment_delay 函数（与 Part 5 P5.2 共享） | Part 5 P5.1 完成 | client.py:_SEGMENT_DELAY 替换为函数 ≤ 25 行；与 Part 5 共享 | 25 | tests/test_inter_segment_delay.py +5 |
| **P2.4** | typing 字符系数扩展（emoji 1s 起步 + thinking 10s 兜底） | Part 1 U3 完成 | Humanizer 扩展 ≤ 30 行 | 30 | tests/test_humanizer_typing.py +4 |
| **P2.5** | force_reply 兜底收紧（is_at + addressee=self） | P3.1 落地 | plugin.py group_listener 5 行改 | 5 | tests/test_force_reply.py +3 |
| **P2.6** | consecutive_no_reply 阶梯阈值 | P2.2 落地 | binary_planner 内置 counter ≤ 20 行 | 20 | tests/test_no_reply_threshold.py +3 |
| **P2.7** | 灰度 + 24h 节奏比对（v1 hardcoded vs P2.x） | P2.1~P2.6 全落地 | maintenance-log 当日条目；用户验收 | — | — |

合计：**新增代码 ≤ 290 行**，**新增测试 ≥ 27 条**。

### 6.2 Part 3 候选子任务

| 编号 | 任务 | 依赖 | 关键产物 | 预估行数 | 关键测试 |
|---|---|---|---|---|---|
| **P3.1** | addressee detector（4 层 cascade） | Part 1 V12 | `services/group/addressee.py` ≤ 150 行；adapter / regex / quote / @ 四层；输出 `(target_id, confidence)` | 150 | tests/test_addressee.py +10（含 multi-@ degree centrality） |
| **P3.2** | topic drift detector（in-line） | P3.1 + V8 不动 | `services/group/topic_drift.py` ≤ 120 行；读 last 3 messages，输出 `(topic, drift_score)`；用 embedding cosine 替代 difflib | 120 | tests/test_topic_drift.py +6 |
| **P3.3** | read_mark prompt 注入 | P3.2 | PromptBuilder group context 块加 read_mark marker ≤ 15 行 | 15 | tests/test_prompt_read_mark.py +2 |
| **P3.4** | person willingness 5-stage 分类（不存数值） | persona_v2 admin map | `services/persona/willingness.py` ≤ 80 行；5-stage 分类器（基于近期回复延迟 + register） | 80 | tests/test_willingness.py +5 |
| **P3.5** | 灰度 + 24h addressee precision 比对 | P3.1~P3.4 | maintenance-log；用户验收 | — | — |

合计：**新增代码 ≤ 365 行**，**新增测试 ≥ 23 条**。

### 6.3 Part 2 v2 追加子任务（P2.8 ~ P2.14）

> 对应 §3.4 / §3.5 / §3.6 与 §1.13 4 触发源对比。

| 编号 | 任务 | 依赖 | 关键产物 | 预估行数 | 关键测试 |
|---|---|---|---|---|---|
| **P2.8** | sticker_decision_provider 单决策点 | §4.6 mood slot 落地 + Part 1 V12 | `services/sticker/decision_provider.py` ≤ 220 行；4 触发源统一进入；输出 `StickerDecision` | 220 | tests/test_sticker_decision_provider.py +14 |
| **P2.9** | kaomoji_enforce 拆解（仅 register=playful + mood ∈ {playful, happy}） | P2.8 | services/llm/client.py:2485-2506 改 ≤ 35 行 | 35 | tests/test_kaomoji_enforce.py +6 |
| **P2.10** | sticker library emotion tag 重标注（替换 DEFAULT_STICKER_USAGE_HINT） | P2.8 | `services/media/sticker_capture.py` 替换 + 离线脚本 `scripts/dev/sticker_recaption.py` ≤ 90 行 | 90 | tests/test_sticker_capture_emotion.py +5 |
| **P2.11** | og:title 注入 | P2.8 后即可 | `services/url_meta/og_title.py` ≤ 130 行；24h LRU；500ms timeout | 130 | tests/test_og_title.py +8 |
| **P2.12** | sticker FairMatch rerank（U-Sticker long-tail） | P2.8 + P2.10 | `services/sticker/fairmatch.py` ≤ 60 行；调用频次直方图 | 60 | tests/test_fairmatch.py +4 |
| **P2.13** | bilibili / youtube 视频元信息 adapter | P2.11 后；P3.10 之前 | `services/url_meta/video_adapter.py` ≤ 110 行；专用 adapter；可选启用 | 110 | tests/test_video_adapter.py +5 |
| **P2.14** | sticker_id 调用密度反馈 → mood slot | §4.6 mood slot 落地 | sticker_decision_provider 写回 RuntimeStateBus ≤ 25 行 | 25 | tests/test_sticker_density_feedback.py +3 |

合计 v2 追加：**新增代码 ≤ 670 行**，**新增测试 ≥ 45 条**。

### 6.4 Part 3 v2 追加子任务（P3.6 ~ P3.10）

> 对应 §3.7 / §4.5 / §4.6。

| 编号 | 任务 | 依赖 | 关键产物 | 预估行数 | 关键测试 |
|---|---|---|---|---|---|
| **P3.6** | mood RuntimeStateBus slot 实现 | Part 1 V12 | `services/humanization/contract.py` 加 MOOD_CURRENT_SLOT；mood 信号收集器 `services/humanization/mood_classifier.py` ≤ 180 行 | 180 | tests/test_mood_classifier.py +12 |
| **P3.7** | affection 5-档分类器 | persona_v2 admin map + P3.6 | `services/persona/affection_classifier.py` ≤ 150 行；24h 滚动；persist 到 admin map 扩展字段 | 150 | tests/test_affection_classifier.py +10 |
| **P3.8** | mood → typing 系数 + inter_segment_delay 渗透 | P3.6 + Part 5 P5.2 | services/humanizer.py + Part 5 P5.2 公式各加 ≤ 20 行 mood 系数表 | 40 | tests/test_humanizer_mood.py +6 |
| **P3.9** | mood / affection → binary_planner / addressee gate | P3.6 + P3.7 + P2.2 + P3.1 | binary_planner + addressee 加 mood/affection gate ≤ 50 行 | 50 | tests/test_planner_addressee_mood.py +8 |
| **P3.10** | mood × addressee × topic 联动表落地 | P3.6 + P3.7 + P3.8 + P3.9 | services/humanization/coupling.py ≤ 80 行；§4.5 表的 lookup 落实 | 80 | tests/test_mood_coupling.py +6 |

合计 v2 追加：**新增代码 ≤ 500 行**，**新增测试 ≥ 42 条**。

### 6.5 v2 总体预算（替换原 6.3）

- **v1（保留）**：≤ 655 行 / ≥ 50 条 测试（P2.1~P2.7 + P3.1~P3.5）
- **v2（追加）**：≤ 1170 行 / ≥ 87 条 测试（P2.8~P2.14 + P3.6~P3.10）
- **合计**：≤ 1825 行 / ≥ 137 条 测试
- **依赖图**（v2 关键路径）：
  - Part 1 V12 灰度旗标 → P3.6 mood slot → P2.8 sticker_decision_provider → P2.9 kaomoji 拆解
  - persona_v2 admin map → P3.7 affection 5-档
  - Part 5 P5.2 inter_segment_delay → P3.8 mood 系数渗透
- **v2 优先级**：P2.8 > P3.6 > P2.9 > P2.10 > P3.7 > P2.11 > P3.8 > P3.9 > P3.10 > P2.12 > P2.13 > P2.14
- **不进 v2 范围**：sticker library 重建（Dream agent 维护）/ persona evolution（Part 4）/ 长程 mood 衰减（Part 4）/ image / voice 输出（保留接口面）

### 6.6 long-tail / 不立项

- **PEARL RL sticker rerank** — 缺 engagement reward 数据闭环
- **IGSR 图谱标注** — sticker library 维护成本远超收益
- **Inflection Pi 88-dim emotion** — 过度复杂；Omubot 5-7 态足够
- **long-context persona drift 修复** — Part 4 范畴
- **基于关系数值的"对 X 好感 +1"** — Replika 反例警示，不做

---

## 7 出口标准（草案）

### 7.1 Part 2 出口标准

- [ ] 24h 灰度 200 条 group reply 采样：
  - 回复延迟分布的标准差 ≥ 1.5s（v1 baseline ≈ 0.8s）
  - 平均 typing 时长与字数线性相关（Pearson r ≥ 0.7）
  - 「该不该回」二分类 vs 人工标注 balanced accuracy ≥ 60%
- [ ] 用户主观验收：「节奏不再机器」「短消息更快」「长消息有等待感」
- [ ] `uv run pytest -q` ≥ Part 1 出口基线 + 27 = ≥ 1703 passed
- [ ] feature flag 30 秒回滚演练成功
- [ ] D1 同模式扫描：`grep -rn 'binary_planner\|inter_segment_delay\|typing_time' --include='*.py'` 仅命中预期点位

### 7.2 Part 3 出口标准

- [ ] 24h 灰度 200 条 group reply 采样：
  - addressee 准确率 ≥ 90%（vs at_only baseline ≈ 70%）
  - topic drift 检出率 ≥ 80%
  - read_mark 注入 reply 中"补回老消息"的比例 ≥ 15%
- [ ] 用户主观验收：「群里 @ 别人时不抢话」「话题切换时反应自然」
- [ ] `uv run pytest -q` ≥ Part 2 出口基线 + 23 = ≥ 1726 passed

### 7.3 v2 出口标准（追加）

#### 7.3.1 Part 2 v2（sticker / modality / video-url）

- [ ] 24h 灰度 200 条 group reply 采样：
  - sticker 单回复出现率 ≤ 25%（v1 baseline ≈ 60%+）
  - sticker_id 分布熵 ≥ 3.0 bits（v1 baseline 2 ids 占 73%，约 0.8 bits）
  - eWe-bench 错误类型 (1) emotion mismatch + (2) overuse 合计 ≤ 5%
  - kaomoji_enforce 触发占比 ≤ 10%（v1 ≈ 50% 异常 case）
  - og:title 注入命中率 ≥ 60%（白名单内 URL）
- [ ] 用户主观验收：「不再乱发 sticker」「sticker 跟话题对得上」
- [ ] D1 同模式扫描：`grep -rn 'send_sticker\|kaomoji_enforce\|sticker_decision' --include='*.py'` 仅命中预期点位

#### 7.3.2 Part 3 v2（mood / affection / 渗透）

- [ ] 24h 灰度 200 条 group reply 采样：
  - MOOD_CURRENT_SLOT 写入命中率 ≥ 95%
  - mood=cold/tired 时 sticker_probability 实际值 ≤ 0.1（与 §3.7 表一致）
  - mood=playful 时回复延迟 vs mood=tired 的回复延迟 ratio ≤ 0.7
  - affection 5-档 stranger / acquaint 占比合计 ≤ 60%（避免分类器过度乐观）
  - mood / affection slot 不在 prompt 字符串中（grep identity.md 不命中 mood / affection）
- [ ] 用户主观验收：「累的时候 bot 自己也短了」「熟悉的人发的话 bot 更主动」
- [ ] `uv run pytest -q` ≥ Part 2 v2 出口基线 + 42 = ≥ 1813 passed

---

## 8 风险与回滚

| 风险 | 触发条件 | 回滚 | 影响范围 |
|---|---|---|---|
| binary_planner 误判沉默 | LLM 二分类失败 → 全部 no_reply | feature flag `humanization.binary_planner_enabled=false` + restart | 群回复全停 |
| addressee detector 误识别 | 多 @ degree centrality 错误 | feature flag `humanization.addressee_detector_enabled=false` | 退回 at_only |
| topic_drift embedding 调用慢 | 每条消息 +200ms LLM call | feature flag + 改用 difflib fallback | 段间延迟拉长 |
| read_mark 让 LLM 重复回老消息 | prompt 误导 | feature flag `humanization.read_mark_enabled=false` | 退回不注入 |
| willingness 5-stage 误标 | 分类器训练数据不足 | feature flag + 退回 register 二分类 | 节奏波动 |
| typing 时长引发段间过慢 | emoji 1s × N 段 | clamp 上限 max=2.0s | 长 reply 慢 |
| LLM planner 调用增加 token 消耗 | +1 LLM call per group msg | metrics 监控 + 必要时改 schedule 抽样 50% | 成本上升 |
| Part 2/3 与 V11 critic 二次 round 累积延迟 | critic + planner 串行 | 改为并行调用 | 总延迟 |
| **sticker_decision_provider 误关 sticker（v2）** | mood 信号噪声大 → cold 状态过度触发 | feature flag `humanization.sticker_decision_provider_enabled=false` | 退回 v1 4 路触发 |
| **kaomoji_enforce 拆解后 v1 case 回归（v2）** | 部分用户喜欢 kaomoji，新策略不发 | feature flag `humanization.kaomoji_enforce_strict=false` | 退回 v1 强制轮 |
| **og:title fetch 拖慢回复（v2）** | 网络抖动 timeout 串行 | timeout 500ms + 异步 + 缓存；feature flag `humanization.og_title_enabled=false` | 退回不注入 |
| **MOOD_CURRENT_SLOT 自反馈环（v2）** | sticker 调用密度反馈 mood，反过来又改 sticker 概率 | 反馈衰减系数 0.3 + 上限阈值；feature flag 关 mood 反馈通道 | 退回静态 mood |
| **AFFECTION 5-档误标 stranger（v2）** | 冷启动 / 老用户 admin map 缺失 | feature flag `humanization.affection_enabled=false` → 退回 acquaint 默认档 | 节奏波动 |
| **mood / affection 渗透引发 V8 stylometric 偏移（v2）** | mood=cold 时回复变短 → V8 5 轴漂移 | mood/affection 不接 V8 输入；只接 sticker / typing / planner / addressee | V8 评分异常 |

紧急回滚（30 秒）：

```bash
# config/config.json:
#   "humanization": {
#     "binary_planner_enabled": false,
#     "addressee_detector_enabled": false,
#     "topic_drift_enabled": false,
#     "read_mark_enabled": false,
#     "willingness_enabled": false,
#     "sticker_decision_provider_enabled": false,
#     "kaomoji_enforce_strict": false,
#     "og_title_enabled": false,
#     "mood_slot_enabled": false,
#     "affection_enabled": false
#   }
docker compose restart bot
```

---

## 9 引用

### 9.1 论文（仅 arXiv ID + 关键章节）

- HUMA — Multi-party Conversational Assistant — arXiv 2511.17315 §4.1 Router / §4.2 Action Agent / §4.3 Reflection / §6 Eval
- Speak or Stay Silent — arXiv 2603.11409 §3 Task / §4.2 Training / §5 Eval
- Multi-Party Hangover — EMNLP 2024 / arXiv 2409.18602 §4.2 Selection vs Recognition
- Inoue 2025 Triadic Addressee — IWSDS 2025 / arXiv 2501.16643 §4 GPT-4o eval
- MPCA Survey — arXiv 2505.18845 §3 Action-modeling / §5 ToM future
- TV-MMPC — arXiv 2505.17536 §3 Annotation / §4 Roles
- Rewarding Chatbots — arXiv 2303.06135 §3 Reward / §4 Sample Rejection
- SS-MPC — arXiv 2502.16920 §3 Prompt / §4 BLEU
- Willingness-aware Sales Talk — COLING 2025 / arXiv 2412.19490 §3 Three Willingness Types
- SID-Bench — arXiv 2603.24144 §3.2 K=3 Smoothing
- Full-Duplex-Bench v1.5 — arXiv 2507.23159 §3 4 Scenarios
- Semantic VAD-as-DM — arXiv 2502.14145 §3 4 Control Tokens
- 4-Category Interruption Taxonomy — arXiv 2501.01568 §3 / §4 Decision Rules
- PBR FSM — ACL Anthology W13-4063 §2 3-state
- Incremental + RL Barge-in — ACL Anthology W15-4606 / W18-5011
- HumDial — ICASSP 2026 / arXiv 2604.21406 §3 Dual-channel
- EVOLVCONV — INLG 2024 / arXiv 2024.inlg-main.43 §3 K-hop
- Reflective Memory — ACL 2025-long.413 §4 Prospective + Retrospective
- Memori — arXiv 2603.19935 §3 Triples
- membox — arXiv 2601.03785 §3 Topic Loom
- AdaMem — arXiv 2603.16496 §4 Four Layers
- TiMem — arXiv 2601.02845 §3 Temporal Memory Tree
- Semantic Anchoring — arXiv 2508.12630 §3 Hybrid
- Rhea — arXiv 2512.06869 §4 Role-aware

#### 9.1bis v2 追加论文（仅 arXiv ID / DOI + 章节）

- EIGML — AAAI 2025 #38509 / arXiv 2511.17587 §3 dual-encoder + emotion-aware contrastive
- Int-RA / StickerInt — arXiv 2403.05427 §4 intent encoder + retrieval rerank
- PerSRV — arXiv 2410.21801 §3 user-history embedding / §5 cold-start
- PEARL — Findings of EMNLP 2025 #753 §4 RL reward = engagement
- IGSR — AAAI 2025 #34720 §3 visual graph propagation
- U-Sticker — SIGIR 2025 §4 long-tail re-ranking
- STICKERCONV / PEGS — arXiv 2402.01679 v2 §3 dataset / §4 PEGS framework
- PhotoChat — ACL 2021 #479 §4 image intent classifier
- MMDialog — ACL 2023 #405 §3 multimodal turn dataset
- DribeR — arXiv 2310.14804 §3 dual-rail modality
- DIAEF — Findings of ACL 2025 #93 §4 cross-modal emotion fusion
- Thanos — arXiv 2411.04496 §3 router head
- eWe-bench — Findings of ACL 2025 #660 §3 emoji misuse taxonomy
- ESDP — Sci. Rep. 2024 / 10.1038/s41598-024-70463-x §3 policy network
- EmoDynamiX — arXiv 2408.08782 §4 emotion-aware dispatch
- DialogXpert — arXiv 2505.17795 §3 strategy RL
- Self-Emotion — arXiv 2408.01633 §4 LLM 自影响情绪
- PELD — arXiv 2404.07229 §3 PELD dataset
- SPDA / AutoPal — arXiv 2406.13960 §3 persona-aware reward
- LD-Agent — arXiv 2406.05925 §4 long-context persona consistency
- Intimacy LREC-COLING 2024 — long.322 §4 5 级亲密度量表
- FiSMiness — arXiv 2504.11837 §3 5-state FSM + transition matrix
- TransESC — ACL 2023 findings.6725 §4 transition reward
- Barber & Santuzzi 2015 — *J. Occupational Health Psychology* 20(2), 172-189 / 10.1037/a0038278 §3 measurement scale / §4 latency-stress 相关
- Cambier 2018 — *Occupational Health Science* 2(2) / 10.1007/s41542-018-0022-8 §3 behavioral vs emotional withdrawal
- Fang MIT/OpenAI — arXiv 2503.17473 §4 4-week field study

### 9.2 MaiBot 仓库（深读、file:line）

- [heartFC_chat.py:184-237 主循环](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/heart_flow/heartFC_chat.py#L184-L237)
- [heartFC_chat.py:196-205 动态阈值](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/heart_flow/heartFC_chat.py#L196-L205)
- [heartFC_chat.py:213-220 多 @ last-wins](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/heart_flow/heartFC_chat.py#L213-L220)
- [heartFC_chat.py:225-228 频率门](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/heart_flow/heartFC_chat.py#L225-L228)
- [heartFC_chat.py:545-548 引用阈值](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/heart_flow/heartFC_chat.py#L545-L548)
- [heartFC_chat.py:558-576 typing False/True](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/heart_flow/heartFC_chat.py#L558-L576)
- [heartflow_message_processor.py:43-45 notify drop](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/heart_flow/heartflow_message_processor.py#L43-L45)
- [frequency_control.py:14-22 clamp](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/heart_flow/frequency_control.py#L14-L22)
- [planner.py:38-98 plan()](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/planner_actions/planner.py#L38-L98)
- [planner.py:387-413 force_reply](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/planner_actions/planner.py#L387-L413)
- [utils.py:117-221 is_mentioned 7 层](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/utils/utils.py#L117-L221)
- [utils.py:524-567 calculate_typing_time](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/utils/utils.py#L524-L567)
- [chat_message_builder.py:882 read_mark](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/utils/chat_message_builder.py#L882)
- [uni_message_sender.py:262-351 wire send](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/message_receive/uni_message_sender.py#L262-L351)
- [chat_history_summarizer.py:35-555 topic detector](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/memory_system/chat_history_summarizer.py#L35-L555)
- [group_generator.py:912/988/1091 relation_info commented](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/replyer/group_generator.py#L912)
- [replyer_prompt.py:5-26 single-target prompt](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/replyer/prompt/replyer_prompt.py#L5-L26)
- [official_configs.py:71 relationship.enable invalid](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/config/official_configs.py#L71)
- [official_configs.py:178-180 talk_value floor](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/config/official_configs.py#L178-L180)

#### 9.2bis MaiBot 仓库 v2 追加锚点（emoji / video-url / capability / dead code）

- [emoji_plugin/emoji.py:21-25 RANDOM 激活 + emoji_chance](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugins/built_in/emoji_plugin/emoji.py#L21-L25)
- [emoji_plugin/emoji.py:38-40 action_require 不连发护栏](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugins/built_in/emoji_plugin/emoji.py#L38-L40)
- [emoji_plugin/emoji.py:54 emoji_api.get_random(30)](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugins/built_in/emoji_plugin/emoji.py#L54)
- [emoji_plugin/emoji.py:86-117 utils 模型二次决策](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugins/built_in/emoji_plugin/emoji.py#L86-L117)
- [emoji_system/emoji_manager.py:423 get_emoji_for_text 唯一 reader](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/emoji_system/emoji_manager.py#L423)
- [emoji_system/emoji_manager.py:457-459 Levenshtein 匹配](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/emoji_system/emoji_manager.py#L457-L459)
- [emoji_system/emoji_manager.py:469-479 top-10 random.choice](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/emoji_system/emoji_manager.py#L469-L479)
- [planner_actions/action_modifier.py:160-163 RANDOM 激活位点](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/planner_actions/action_modifier.py#L160-L163)
- [planner_actions/planner.py:607 RANDOM 激活分支](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/planner_actions/planner.py#L607)
- [brain_chat/brain_planner.py:449 RANDOM 激活分支（brain）](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/brain_chat/brain_planner.py#L449)
- [official_configs.py:529-530 emoji_chance=0.6](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/config/official_configs.py#L529-L530)
- [plugin_system/apis/emoji_api.py:50 唯一 caller](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/apis/emoji_api.py#L50)
- [base_action.py:140 send_text](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/base/base_action.py#L140)
- [base_action.py:172 send_emoji](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/base/base_action.py#L172)
- [base_action.py:201 send_image](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/base/base_action.py#L201)
- [base_action.py:230 send_command](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/base/base_action.py#L230)
- [base_action.py:262 send_custom](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/base/base_action.py#L262)
- [base_action.py:298 send_hybrid](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/base/base_action.py#L298)
- [base_action.py:329 send_forward](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/base/base_action.py#L329)
- [base_action.py:375 send_voice](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/base/base_action.py#L375)
- [base_events_handler.py:153 send_emoji event handler](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/plugin_system/base/base_events_handler.py#L153)
- [chat/utils/statistic.py:1333 唯一 URL 字面量（chart.js CDN）](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/utils/statistic.py#L1333)
- [person_info/person_info.py:541 build_relationship 仅构造](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/person_info/person_info.py#L541)
- [person_info/person_info.py:550-614 relationship 内部 = memory retrieval](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/person_info/person_info.py#L550-L614)
- [private_generator.py:244 build_relationship 唯一 reader（私聊）](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/replyer/private_generator.py#L244)
- [private_generator.py:831 relation_info 用例](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/replyer/private_generator.py#L831)
- [private_generator.py:853 relation_info 用例](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/replyer/private_generator.py#L853)
- [private_generator.py:960 relation_info commented](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/replyer/private_generator.py#L960)
- [official_configs.py:60-65 states + state_probability personality jitter](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/config/official_configs.py#L60-L65)

### 9.3 Omubot 仓库（接入点、file:line）

- [plugins/chat/plugin.py](../../plugins/chat/plugin.py) — group_listener
- [services/llm/client.py:359-538](../../services/llm/client.py#L359-L538) — _reply_segments
- [services/humanizer.py](../../services/humanizer.py) — typing 模拟
- [services/group/timeline.py](../../services/group/timeline.py) — group history
- [services/persona/runtime.py](../../services/persona/runtime.py) — persona_v2 runtime
- [kernel/router.py:572,597](../../kernel/router.py#L572) — B2/B3 hook（不动）

#### 9.3bis Omubot 仓库 v2 接入点（file:line）

- [plugins/sticker/plugin.py:78-87](../../plugins/sticker/plugin.py#L78-L87) — frequently 档强制 prompt（v2 P2.8 改造）
- [services/llm/client.py:2485-2506](../../services/llm/client.py#L2485-L2506) — kaomoji_enforce 强制轮（v2 P2.9 拆解）
- [services/llm/thinker.py:117-133](../../services/llm/thinker.py#L117-L133) — thinker.sticker bool（v2 P2.8 移除自决）
- [services/media/sticker_capture.py:7](../../services/media/sticker_capture.py#L7) — DEFAULT_STICKER_USAGE_HINT（v2 P2.10 替换）
- [services/humanization/contract.py:9-15](../../services/humanization/contract.py#L9-L15) — RuntimeStateBus slots（v2 P3.6 加 MOOD_CURRENT_SLOT、P3.7 加 AFFECTION_STAGE_SLOT）
- [services/llm/client.py:1641](../../services/llm/client.py#L1641) — _visible_reply_segment_plan（v2 P3.8 mood 系数渗透读取点）
- [services/llm/client.py:1681-1687](../../services/llm/client.py#L1681-L1687) — _current_humanization_mood（v2 P3.6 重构为 slot reader）

---

## 10 当前状态

| 阶段 | 状态 | 落地证据 |
|---|---|---|
| 立项 | ✅ 完成（本文 §0） | 用户原话锚点；研究锚点 22 篇论文清单 |
| 取证 | ✅ 完成（§1） | MaiBot 14 文件深读 + dead code 9 处清单 |
| 学术对照 | ✅ 完成（§2） | 22 篇论文 / arXiv ID + 章节锚点 |
| 借鉴判断 | ✅ 完成（§3 + §4） | Part 2 8 维度 + Part 3 4 类 17 维度 |
| 接入点设计 | ✅ 完成（§5） | 与 Part 1 U1/U3/V1/V11/V12 + Part 5 P5.1/P5.2 接口表 |
| 子任务草案 | ✅ 完成（§6） | P2.1~P2.7 + P3.1~P3.5；预算 ≤ 655 行 + ≥ 50 测试 |
| 出口标准 | ✅ 完成（§7） | 灰度采样指标 + 主观验收 |
| 风险回滚 | ✅ 完成（§8） | 8 风险 + 30s 回滚演练 |
| **正式立项 P2.x / P3.x** | ⏳ 阻塞于 Part 1 主线 + Part 5 P5.1 收口 | 调研报告先沉淀，等用户决策何时启动 |
| **v2 扩范围（§0.4）** | ✅ 完成（2026-05-25 当日） | 用户原话锚点；触发于 Part 5 P5.4 灰度后异常 sticker 观察 |
| **v2 取证（§1.9~§1.13）** | ✅ 完成 | MaiBot 35 锚点 + 4 触发源对比表 + dead code 全景 |
| **v2 学术对照（§2.3~§2.10）** | ✅ 完成 | 16 篇 v2 论文 / arXiv ID + 章节锚点 |
| **v2 借鉴判断（§3.4~§3.7 + §4.5~§4.6）** | ✅ 完成 | sticker / video-url / modality / mood 渗透 5 张借鉴表 + RuntimeStateBus slot 设计稿 |
| **v2 接入点（§5.6）** | ✅ 完成 | sticker_decision_provider / mood slot / og:title 4 类接入点 |
| **v2 子任务（§6.3~§6.5）** | ✅ 完成 | P2.8~P2.14 + P3.6~P3.10；v2 预算 ≤ 1170 行 + ≥ 87 测试 |
| **v2 出口标准（§7.3）** | ✅ 完成 | sticker / mood / affection 灰度采样指标 |
| **v2 风险回滚（§8）** | ✅ 完成 | 6 v2 风险 + 30s 回滚演练（含 mood 自反馈环、affection 误标）|
| **v2 正式立项 P2.8~P2.14 / P3.6~P3.10** | ⏳ 阻塞于 Part 1 主线 + Part 5 P5.4 灰度 24h 收尾 | 调研报告先沉淀；不在 24h 灰度窗口内动代码 / 动 config |

---

## 11 与既有 Part 的边界（最终确认）

- **Part 1**：surface markers / register / scorer / critic——「**说什么**」
- **Part 2**：节奏 / typing / 是否回 / **modality 决策（v2）**——「**什么时候说，要不要说，用什么形态说**」
- **Part 3**：群语境 / addressee / topic / 已读不回 / **mood-affection 渗透（v2）**——「**对谁说，回应谁，带什么情绪状态说**」
- **Part 4**（未立项）：好感 / persona evolution / 长期记忆——「**记住什么**」
- **Part 5**：natural_split / segment delay——「**怎么拆**」

Part 2/3 与 Part 1 V11 共用 LLM critic 框架，与 Part 5 P5.2 共用 inter_segment_delay 函数；与 Part 4 完全隔离（不动 person_info 数值通道，只用 stage 分类）。

### 11.1 v2 边界澄清

- **v2 Part 2 边界扩**：modality 决策（text / sticker / image / video link / mixed）、sticker 4 触发源收敛、kaomoji_enforce 拆解、og:title URL 注入、视频 link adapter（P2.13 候选）。
- **v2 Part 3 边界扩**：mood RuntimeStateBus slot、affection 5-档分类、mood × addressee × topic 联动、mood 渗透 sticker/typing/段间延迟/planner/addressee。
- **v2 与 Part 4 仍隔离**：mood / affection 是**短中时态**（分钟~天级），不写入 persona_v2 freeze artifacts；Part 4 的 persona evolution 是**周月级**，独立通道。
- **v2 与 Part 1 V8 仍正交**：mood / affection 不接 V8 stylometric 输入；只接下游决策器（sticker / typing / planner / addressee / inter_segment_delay）。
- **v2 不接的现有模块**：identity.md prompt 字符串（避免 prompt-fact 漂移）、Dream agent（仅维护库）、kernel/router.py B2/B3 hook（slot 走 RuntimeStateBus，不走 router）。

### 11.2 v2 不做声明

- **不做 sticker library 重建**——Dream agent 维护，v2 仅在使用侧改造
- **不做 image / voice 输出**——保留接口面，但 group reply 不调度
- **不做 RL reward 反馈闭环（PEARL / DialogXpert）**——缺 engagement 数据
- **不做 88-dim emotion 向量（Pi）**——5-7 态足够
- **不做"对 X 好感 +1"数值通道**——Replika 反例警示
- **不做 LLM 自决 mood**——Self-Emotion §4 警示 self-reinforcement
- **不在 prompt 字符串里塞 mood / affection 文字**——避免 prompt-fact 漂移；只通过 RuntimeStateBus slot 注入下游

---

> 本调研报告**只设计、不施工**。后续若用户验收通过，按 §6 子任务清单与 Part 1 / Part 5 主线节奏排队上线。



