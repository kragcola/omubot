# Omubot 拟人修复 Part 2 + Part 3 调研报告

> 状态：2026-05-25 完成 §0~§9 调研，**仅审计 + 设计阶段**，不进 Part 1 / Part 5 主线施工。
>
> 触发：Part 1 V11/V12/U1~U14 收口后空窗，用户授权"在此之前你不要闲着，考察 part2 和 3 相关成熟项目与论文，做调研报告。要求一样，不许看 readme 和简述，所有依据来自代码深度解析和系统拆分"。
>
> 取证原则（强约束）：
> 1. **不读 README / introduction / 中文综述**——所有结论必须有 (a) MaiBot 仓库 file:line 引用，或 (b) arXiv ID + 章节号；
> 2. **surface ≠ implementation**——MaiBot 文档大量提及但代码未消费的字段（dead code）必须显式标注；
> 3. **架构边界**——Part 2 = 「节奏 / typing / 该不该回」输出层；Part 3 = 「群语境 / @ / addressee / topic / relationship」识别层；两者分轴但共享 MaiBot 取证基线。
>
> 上下文授权（保留勿删）：「依据文档自主做上线前准备，不用问我。我最终做上线前最后验收」。灰度群 993065015 / 984198159。
>
> 与 Part 1 关系：[omubot-humanization-part1-language-feel.md §0](./omubot-humanization-part1-language-feel.md#0-研究锚点-v2-§0-沉淀结论保留) 锚点继承「surface ≠ 活跃」原则；本文沿用同一研究分级，但落点不同——Part 1 改 surface markers / register / scorer，Part 2/3 改「节奏 / 群感知」。
>
> 与 Part 5 关系：[omubot-humanization-part5-segmentation.md](./omubot-humanization-part5-segmentation.md) 的 natural_split 处理「段拆分」，Part 2/3 处理「段间节奏 + 是否发段」——两者输入相似输出正交。

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

**5-stage IM withdrawal**（telepressure / Gen Z 文献群）

- 阶段 1: response latency creep（回复变慢）
- 阶段 2: emoji substitution（用 emoji 代替文字）
- 阶段 3: init-only（只主动发，不回复）
- 阶段 4: selective response（挑选性回复）
- 阶段 5: emotional withdrawal（情感撤离）
- 启发：**「关系恶化」是 5 个递进阶段**，binary 太粗

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

### 6.3 总体预算

- **新增代码 ≤ 655 行**（Part 2 + Part 3）
- **新增测试 ≥ 50 条**
- **依赖关系**：Part 1 U1/U3/V1/V11/V12 + Part 5 P5.1 必须先落地
- **优先级**：P2.2 > P3.1 > P2.3 > P3.2 > P2.4 > P3.3 > 其他

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

紧急回滚（30 秒）：

```bash
# config/config.json:
#   "humanization": {
#     "binary_planner_enabled": false,
#     "addressee_detector_enabled": false,
#     "topic_drift_enabled": false,
#     "read_mark_enabled": false,
#     "willingness_enabled": false
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

### 9.3 Omubot 仓库（接入点、file:line）

- [plugins/chat/plugin.py](../../plugins/chat/plugin.py) — group_listener
- [services/llm/client.py:359-538](../../services/llm/client.py#L359-L538) — _reply_segments
- [services/humanizer.py](../../services/humanizer.py) — typing 模拟
- [services/group/timeline.py](../../services/group/timeline.py) — group history
- [services/persona/runtime.py](../../services/persona/runtime.py) — persona_v2 runtime
- [kernel/router.py:572,597](../../kernel/router.py#L572) — B2/B3 hook（不动）

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

---

## 11 与既有 Part 的边界（最终确认）

- **Part 1**：surface markers / register / scorer / critic——「**说什么**」
- **Part 2**：节奏 / typing / 是否回——「**什么时候说，要不要说**」
- **Part 3**：群语境 / addressee / topic / 已读不回——「**对谁说，回应谁**」
- **Part 4**（未立项）：好感 / persona evolution / 长期记忆——「**记住什么**」
- **Part 5**：natural_split / segment delay——「**怎么拆**」

Part 2/3 与 Part 1 V11 共用 LLM critic 框架，与 Part 5 P5.2 共用 inter_segment_delay 函数；与 Part 4 完全隔离（不动 person_info 数值通道，只用 stage 分类）。

---

> 本调研报告**只设计、不施工**。后续若用户验收通过，按 §6 子任务清单与 Part 1 / Part 5 主线节奏排队上线。



