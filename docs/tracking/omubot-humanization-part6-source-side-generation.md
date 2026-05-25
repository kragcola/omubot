# Omubot 拟人修复 Part 6 — 源头生成调度（LLM call schedule）调研

> 状态：2026-05-25 立项，**仅审计 + 调研阶段**，不进 Part 1 / Part 5 主线施工。
>
> 触发：用户对 Part 5 提出的根本反驳——「part5 方案仍聚焦在话语分割处理上，而没有从 llm 生成的源头提供研究」。
>
> Part 5 把"LLM 一次 1024-token 输出 → 客户端切碎"作为既定前提，只在切分策略上做改良；Part 6 拒绝这一前提，从生成调用本身的形态（call 数 / 触发节奏 / 可中断性 / 计划-执行分离）寻找拟人化突破口。
>
> 上下文授权（保留勿删）：「依据文档自主做上线前准备，不用问我。我最终做上线前最后验收」。灰度群 993065015 / 984198159。

---

## 0 研究锚点与边界

### 0.1 与 Part 1 / Part 2-3 / Part 4 / Part 5 的边界

| Part | 输入 | 输出 | 改变层 |
|---|---|---|---|
| Part 1 | 整段 LLM 文本 | 同长度文本 + register slot | 表面标记 / 风格评分 |
| Part 2-3 | 群消息流 | 是否回 / 何时回 | 仲裁 + 节奏（输入感知层） |
| Part 4 | 历史对话 + episode | 关系 / 记忆唤起 | 长期记忆调度 |
| Part 5 | 整段 LLM 文本 | N 个 segment + 段间延迟 | 发送层后处理 |
| **Part 6** | **prompt + 群上下文** | **N 次独立 LLM call + 调度** | **生成层 call 调度** |

**关键差异**：Part 5 的 N 段共享同一次 LLM call 的 token stream，N 段之间不存在"再观察 → 再决策"的人类认知循环；Part 6 研究的是把单次 1024-token call 替换为多次短 call，每次 call 之间允许观察对方反应、读群新消息、修正后续段内容。

### 0.2 取证原则（强约束，沿用 Part 4）

1. **不读 README / introduction / 综述博客**——所有结论必须有 (a) 仓库 file:line 引用，或 (b) arXiv ID + 章节号，或 (c) 官方 SDK / 协议规范文档段落
2. **surface ≠ implementation**——文档承诺 ≠ 实现具备
3. **架构边界**——Part 6 仅讨论"如何调用 LLM"，不动 Part 5 的"如何切分文本"，两者可以并存
4. **成本可观测性**——每个候选方案必须给出 token 成本系数 / cache hit-rate 估算 / latency P95 估算

### 0.3 8 条研究问题（研究轴）

| # | 研究轴 | 核心问题 |
|---|---|---|
| Q1 | Single-call vs Multi-call | 一次 1024-token + 后切，与 N 次 short-token call，行为是否等价？token 成本系数？persona 漂移率？ |
| Q2 | Plan-then-utter | 先 LLM short call 生成"段大纲"（≤ 50 token），再每段独立 LLM call 落实——CoVe / ToT / Plan-and-Solve 在 IM 场景的可移植性 |
| Q3 | Reactive mid-generation | 生成中检测对方新消息 → 截断 + 重 plan。SSE abort / vLLM cancel / SGLang abort 实际能力 |
| Q4 | Streaming-as-segment | 不动 call 数，但在 SSE token-stream 上 online 切，遇自然边界立即 flush。online SBD 在 token-stream 的可行性 |
| Q5 | Pause-then-extend | 发完第一段 → 等 N 秒 → 看对方是否回应 → 决定是否追发。turn-taking + IM rhythm 文献 |
| Q6 | 成本 / 缓存影响 | multi-call 对 Anthropic 4-breakpoint cache（5-min TTL）、token 成本、P95 latency 的破坏量 |
| Q7 | Persona stability | 每段独立 LLM call 时 persona / mood / register 在 N 段间的稳定性，多轮人格一致性文献 |
| Q8 | 可观测性 | multi-call 因果链如何写入 BlockTrace / usage 表，是否需要 segment_chain_id |

### 0.4 不在本文范围

- 不动 Part 5 的 natural_split 算法本身——Part 6 是"是否还需要 natural_split"的元问题
- 不动 Part 1 V11 critic-rewrite-loop——critic 是评分层，Part 6 是调用层
- 不动 Part 2/3 的 addressee / topic / @—Part 6 不决定"是否回复"，只决定"回复以何种调用形态生成"
- 不动 prompt cache 的现行 4-breakpoint 布局——Part 6 是观察 multi-call 对该布局的破坏量，不是重设计

---

## 1 代码取证

### 1.1 Omubot 现状 — 一次 chat() 实际是 3~8 次 LLM call（不是 single-call）

#### 1.1.1 入口与 SSE 形态

[services/llm/client.py:1963](../../services/llm/client.py#L1963) `LLMClient.chat()` 是群/私聊回复唯一入口。`call_api()` 在 [client.py:627](../../services/llm/client.py#L627) 做单次裸 SSE，`stream_api()` 不存在——SSE 走 `aiohttp.session.post(...).resp.content async-for` **累积完整 SSE 后**再交给 `provider.parse_sse_stream()`（[client.py:678](../../services/llm/client.py#L678) → [client.py:688](../../services/llm/client.py#L688)）。

**关键事实**：Omubot 当前是 **drain-all 模式**——所有 token 收齐后再一次性处理。**没有 mid-generation 钩子。** 这是 Part 6 §3 方案 B / 方案 C 必须新建的能力。

`_dispatch_call()`（[client.py:1109](../../services/llm/client.py#L1109)）是 `_call() → _dispatch_call() → call_api()` 中间层，套 cache breakpoints + provider 速率/profile 状态。

#### 1.1.2 一次 chat() 的真实 LLM call 次数

按主路径顺序（最坏情况：group force_reply + thinker on + humanization rewrite enabled + tool loop 满员）：

| # | 调用 | file:line | profile | max_tokens |
|---|---|---|---|---|
| 1 | **thinker (pre-reply)** | [client.py:2071](../../services/llm/client.py#L2071) `await think(...)` | thinker (haiku) | 256（[client.py:906](../../services/llm/client.py#L906)） |
| 2..6 | **main + tool loop**，最多 5 轮 | [client.py:2319](../../services/llm/client.py#L2319) `for round_i in range(MAX_TOOL_ROUNDS)`，[client.py:54](../../services/llm/client.py#L54) `MAX_TOOL_ROUNDS = 5` | main | 1024 |
| +1 | **humanization rewrite**（条件触发） | [client.py:1746](../../services/llm/client.py#L1746) `await self._call(rewrite_request)` | main | `max(128, min(1024, len(reply)*3+64))` |
| +1 | **register classifier**（独立路径，与 chat 不同栈） | [plugins/chat/plugin.py:196](../../plugins/chat/plugin.py#L196) → [services/humanization/classifier.py:73](../../services/humanization/classifier.py#L73) | thinker | 220 |
| +1..2 | **kaomoji enforce 强制再调一轮 main** | [client.py:2435](../../services/llm/client.py#L2435) `_sticker_sent = True; ... continue` | main | 1024 |

- **典型**：3 次（thinker + 1 main 不带 tool + register classifier）
- **最坏**：8+ 次（thinker + 5 main rounds with tools + 1 rewrite + 1 register classifier + kaomoji round）

#### 1.1.3 V1 RegisterClassifier — 确认是独立 LLM call

[services/humanization/classifier.py:65-76](../../services/humanization/classifier.py#L65) 走 `LLMRequest(task="thinker") → _call()`。fallback 在 line 62-63 `if self._llm_client is None: return RegisterDecision.default()`。生产路径**不是本地评分，是真发 thinker call**。

#### 1.1.4 V11 critic-rewrite-loop 默认关闭（关键事实）

[client.py:1689](../../services/llm/client.py#L1689) `_maybe_rewrite_humanization_reply` 在 line 1700 早退出：`if self._humanization_rewrite_threshold < 0: return ...`。default 在 [client.py:917](../../services/llm/client.py#L917) 是 `-1.0`，[kernel/config.py:1060](../../kernel/config.py#L1060) Field default 同。**生产环境 V11 是冷代码**——Part 6 设计不必假设 rewrite 路径已激活。

#### 1.1.5 4-breakpoint cache 注入位置（唯一注入点）

[services/llm/llm_request.py:303 `apply_cache_breakpoints`](../../services/llm/llm_request.py#L303)，在 `_dispatch_call` 调用一次（[client.py:1143](../../services/llm/client.py#L1143)）。Anthropic 硬上限 4，源码里 `_ANTHROPIC_CACHE_CAP = 4`（[llm_request.py:300](../../services/llm/llm_request.py#L300)）。

main 任务的 4 个 breakpoint：

| 序号 | 位置 | file:line |
|---|---|---|
| 1 | system static segment 末尾（identity / persona） | [llm_request.py:347-359](../../services/llm/llm_request.py#L347) 按 `_omu_segment` 标签选末尾 idx |
| 2 | system stable segment 末尾（plugin tail / state_board） | 同上 |
| 3 | provider tool tail | client.py:395 `_cached_text` 间接，reserved 由 [llm_request.py:328](../../services/llm/llm_request.py#L328) 计入 |
| 4 | messages 倒数第二条 user message | [client.py:1819](../../services/llm/client.py#L1819) (group) / [client.py:1851](../../services/llm/client.py#L1851) (private) |

system_breakpoints=3 + tool + message = 5 → 硬上限 4 → 落实预算时 system 减一个，最先牺牲 dynamic ([llm_request.py:352](../../services/llm/llm_request.py#L352))。

**Part 6 关键约束**：multi-call 设计**必须保持 system prefix byte-stable**，否则每次 call 的 cache_creation 都失效。Omubot 的 `apply_cache_breakpoints` 已强制 spine-唯一注入路径——这是 Part 6 复用现有架构的好消息。

#### 1.1.6 tool loop 与 pass_turn 退出

`for round_i in range(MAX_TOOL_ROUNDS)`（[client.py:2319](../../services/llm/client.py#L2319)）。pass_turn 退出：[client.py:2344](../../services/llm/client.py#L2344) `pass_turn = next((tu for tu in tool_uses if tu.name == "pass_turn"), None); if pass_turn: ... return None`。pass_turn tool 定义在 [client.py:580](../../services/llm/client.py#L580)。

退出三大门：① `pass_turn` tool 命中 → 不发回复；② 当前轮无 tool_use 且 text 非空 → 走分段发送 + return；③ 5 轮跑满（[client.py:2580](../../services/llm/client.py#L2580) warning）。

#### 1.1.7 token 量化（生产 max_tokens 上限）

| 路径 | max_tokens | file:line |
|---|---|---|
| call_api 默认（main） | 1024 | [client.py:634](../../services/llm/client.py#L634) |
| thinker | 256 | [client.py:906](../../services/llm/client.py#L906) |
| register classifier | 220 | [classifier.py:69](../../services/humanization/classifier.py#L69) |
| rewrite | `max(128, min(1024, len(reply)*3+64))` | [client.py:1742](../../services/llm/client.py#L1742) |
| compact | 1024 | [client.py:2760](../../services/llm/client.py#L2760), [2843](../../services/llm/client.py#L2843) |

**1024 是 main 一次输出上限——这就是 Part 6 文档所指"一次 1024 + 后切"的硬证据**。

### 1.2 MaiBot 现状 — plan-then-utter 双 call，但 mid-generation interrupt 是 dead code

#### 1.2.1 三段式架构（planner + replyer + heart_flow loop）

1. **Planner**（独立 LLM call #1）：[planner.py:101 class ActionPlanner](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/planner_actions/planner.py#L101)
   - line 107：`self.planner_llm = LLMRequest(model_set=...planner, request_type="planner")`
   - line 678：`llm_content, ... = await self.planner_llm.generate_response_async(prompt=prompt)` —— 输出 JSON 决定要执行哪些 action（reply / no_reply / 工具）
2. **Replyer**（独立 LLM call #2）：[group_generator.py:50 class DefaultReplyer](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/replyer/group_generator.py#L50)
   - line 56：`self.express_model = LLMRequest(model_set=...replyer, request_type=request_type)`
   - line 1150：`content, ... = await self.express_model.generate_response_async(prompt)`
3. **Executor / heart_flow loop**：[heartFC_chat.py:64 class HeartFChatting](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/heart_flow/heartFC_chat.py#L64)
   - line 372：`action_to_use_info = await self.action_planner.plan(...)` → planner call
   - line 682：`success, llm_response = await generator_api.generate_reply(...)` → replyer call

**MaiBot 本身就是 plan-then-utter 双 call 模型**——planner 决定要不要回 + 选谁回，replyer 生成文本。Omubot 的 thinker 也分到了 plan 角色，但 thinker 只做 wait/reply 二值，不像 MaiBot planner 决定 action set。

#### 1.2.2 mid-generation interrupt 是 dead code（关键事实）

`interrupt_flag: asyncio.Event | None` 形参在：
- [openai_client.py:283, 528](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/llm_models/model_client/openai_client.py#L283)
- [gemini_client.py:285, 524](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/llm_models/model_client/gemini_client.py#L285)

完整实现（流循环里检查并 `raise ReqAbortException`），但**全仓 grep `interrupt_flag=` / `asyncio.Event()` 在 chat 层零结果**。`utils_model.py:160 _execute_request` 调用链根本不带这个参数。

**结论**：MaiBot 声称的中断能力在 chat 层完全未接线，是 LLM client 层的 stub。

`chat_observer.py:308 self._task.cancel()` 是后台 ChatObserver 的关闭路径，跟 mid-generation abort 无关。

#### 1.2.3 发送循环 sleep 后不会回到 LLM 再生成

[uni_message_sender.py:326](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/message_receive/uni_message_sender.py#L326) `await asyncio.sleep(typing_time)` 之后 line 328 `sent_msg = await _send_message(...)`——发已生成的 segment，**没有再次调 LLM**。

段是怎么来的？[generator_api.py:173](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/utils/generator_api.py#L173) `processed_response = process_llm_response(content, enable_splitter, enable_chinese_typo)` → `process_llm_response` 在 [utils.py:446](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/utils/utils.py#L446)，是**纯本地切分函数**（line 483 走 `split_into_sentences_w_remove_punctuation`）。

**MaiBot 也是"一次 LLM 全文 → 本地分段 → typing sleep 节流发送"——跟 Omubot 行为同构。** Part 6 的"段间允许观察对方反应"在 MaiBot 也未实现。

#### 1.2.4 plan-then-utter 配置旗标

`global_config.chat.think_mode` 三值 `default / deep / dynamic`、`global_config.tool.enable_tool`、`global_config.response_splitter.enable`、`global_config.chat.llm_quote`。**没有**任何旗标控制"段间是否回到 LLM 再生成"——这条路径在 MaiBot 的 config 层不存在。

### 1.3 Anthropic / vLLM / SGLang streaming abort

#### 1.3.1 Anthropic SSE client-side abort 计费规则（Part 6 关键决策依据）

**确认可以 abort，input 全付，output 按已生成数计**。证据来源：

| 项 | 规则 | 来源 |
|---|---|---|
| input | 全付，cancel 不退款 | claudelab.net 实测 + Anthropic SDK 文档 |
| output | 按实际生成 token 计费，不按 max_tokens | 同上 |
| 漂移 | 5-30 token 在 abort 时已在 server 飞行中，仍计费 | 同上 |
| SDK 接口 | `async with client.messages.stream(...) as stream: ... await stream.close()` | anthropic-sdk-python helpers.md |
| aiohttp 等价 | 退出 `async with session.post(...) as resp:` 上下文 / `resp.close()` | aiohttp 标准行为 |
| usage 字段 | `message_delta` 事件累计 `usage.output_tokens`，最后一帧 abort 前的值即被计费数 | claudelab.net |

**Part 6 经济性结论**：reactive mid-generation abort **不是免费**——把"一次 1024 全付 output"换成"每段约 N tokens output × M 段"。如果 M × N > 1024，反而更贵；如果每次只生成 ~120 字一段（约 80 tokens），8 段满才等价于一次 1024 全收。

#### 1.3.2 Anthropic prompt cache multi-call 命中条件

| 项 | 数字 | 来源 |
|---|---|---|
| 默认 TTL | **5 分钟**（Anthropic 2026-04-23 postmortem 后从 1h 降回） | particula.tech |
| 1h TTL | 显式启用，write 价格翻倍 | agentpatterns.ai |
| Sonnet 4.6 prefix 门槛 | 1024 tokens | agentpatterns.ai + Anthropic docs |
| **Opus 4.7 prefix 门槛** | **4096 tokens** | 同上（Omubot 现役模型，**关键约束**） |
| Cache write 价格 | base × 1.25（5min）/ × 2.0（1h） | agentpatterns.ai |
| Cache read 价格 | base × **0.10**（90% 折扣） | 同上 |
| Break-even | 一次读就回本（5min） / 三次（1h） | particula.tech |
| 命中条件 | byte-stable prefix + 4 breakpoints + ≤20 block lookback | Anthropic docs |
| 失效 4 大坑 | image binary 变 / tool_choice 翻 / --resume reseed / system prompt 非确定构造 | Anthropic postmortem |

**Part 6 关键发现**：multi-call 不破坏 cache，反而能**充分利用 cache**——calls 2..N 在 5min 内全走 cache_read 0.10× 折扣。前提：① system prefix byte-stable（Omubot 已强制）；② Opus 4.7 prefix 必须 ≥ 4096 token（Omubot 当前 system 含 identity + instruction + memo index 通常已 ≥ 4096，需抽样验证）。

multi-TTL usage 字段（agentpatterns.ai）：response.usage 已分两栏 `cache_creation: { ephemeral_5m_input_tokens, ephemeral_1h_input_tokens }`。Omubot 现有 `_record_usage` 字段（[client.py:1461](../../services/llm/client.py#L1461)）**没区分这两个**——**监控盲区**，Part 6 落地前应补。

#### 1.3.3 aiohttp `cancel()` 实际生效路径

Omubot 的 `async with session.post(...) as resp: async for raw_line in resp.content`（[client.py:678](../../services/llm/client.py#L678)）—— 只要外层 task 被 cancel 或 break 出 for loop，HTTP 连接就关。**生效路径无需额外代码**。但当前 Omubot 没有 break/cancel 触发器——`call_api` 一定 drain 完整个 stream。Part 6 §3 方案 C 的工程缺口就是补这个触发器。

#### 1.3.4 vLLM / SGLang abort（自托管参考，不影响决策）

vLLM PR #7111（2024-08-03）修复了 streaming chat 请求 client disconnect 后继续运行的旧 bug，现在 `asyncio.aclose()` 明确 cancel + raise CancelledError。但 issue #24584 / #20362 显示 v1 引擎里 abort 行为仍不一致。**Omubot 用 Anthropic API，这条不影响决策**。

### 1.4 主流框架的调度形态

| 框架 | 调度形态 | mid-gen abort | 与 Part 6 关系 |
|---|---|---|---|
| LangChain RunnableSequence | 多 Runnable 链式编排 | py 端 `signal: AbortSignal` 间接绑定 | 编排层，可借鉴但不强求 |
| LlamaIndex ChatEngine | `CondenseQuestionChatEngine` / `CondensePlusContextChatEngine` 都是 plan-then-utter | 无显式 mid-gen | 与方案 A 同形 |
| DSPy | Modules 是声明式 LM 调用单元，每次 module 调用 = 一次 LLM call | 无 | Part 6 multi-call 形态的范式参考 |
| AutoGen ConversableAgent | 每个 turn = 一次 LLM call，`MAX_CONSECUTIVE_AUTO_REPLY=10`，`max_turns` 控制双 agent 轮数 | 无 | turn-based multi-call，与方案 D 同源思路 |
| HuggingGPT / Transformers Agent | plan 阶段 1 次 LLM call 出 task DAG，每个 task execute 独立 call | 无 | 与方案 A 同形 |
| **Pipecat** | full-duplex pipeline + `SileroVADAnalyzer` + `enable_interruptions=True` | **真 reactive abort**（VAD 检测到用户开口立即停 + 清 pending） | **唯一现成 reactive 实现**，但 audio-based 不直接适用 QQ 群 |

**关键发现**：plan-then-utter 是行业普遍模式（MaiBot / AutoGen / DSPy / LangChain），但**段间真正 abort 回到 LLM 重新决策的开源实现只有 Pipecat 一家**，且依赖 audio + VAD。Part 6 在 QQ 群 message-based 场景下的 reactive abort **没有现成参考实现**，需要自己定义触发器（"用户在 bot 说话期间发了新消息" → 取消当前 stream）。

---

## 2 学术证据矩阵 — 8 轴 27 篇

### 2.1 Q1 — Single-call vs Multi-call

#### [1] ReAct: Synergizing Reasoning and Acting in Language Models (arXiv 2210.03629, ICLR 2023)
- **章节**：§3 (HotpotQA/Fever), §4 (ALFWorld/WebShop), Table 3
- **关键论点**：把"reasoning trace"从"一次 long output"拆成"thought → act → obs → thought…"的多步交错，迫使每个 thought 都被环境 observation 校正一次
- **量化结论**：ALFWorld success rate 平均 65%（best 71%）vs Act-only 45%（best 45%）；WebShop 绝对 +10%；HotpotQA EM ReAct (29.4) 略低于 CoT (30.8) 但 hallucination 显著下降；Reflection finetuned 后 ReAct 78.4% 远超 baseline 70.9%
- **Omubot 适用性**：直接可借鉴
- **理由**：Omubot 群聊已有 tool loop（max 5 rounds + pass_turn），ReAct 验证多步交错对 hallucination 控制比纯生成更有效，prompt 模式可直接套到"每条消息 = 一次 thought→act"

#### [2] Reflexion: Language Agents with Verbal Reinforcement Learning (arXiv 2303.11366, NeurIPS 2023)
- **章节**：§3 Algorithm, §4.3 HumanEval ablation
- **关键论点**：失败后 LLM 自己 verbally 反思 → 写入 episodic memory → 下一 trial 利用反思
- **量化结论**：HumanEval pass@1 91% vs GPT-4 baseline 80%（绝对 +11%）；ALFWorld 130/134 任务用 ≤12 trials 解决；每 trial 增加约 2x token 成本但质量提升远超 token 增量
- **Omubot 适用性**：部分可借鉴
- **理由**：Reflexion 是 verifiable reward 场景，群聊无 ground-truth；但其 episodic memory 机制可对照 Omubot 的 `append_memo` 工具——对方"已读不回 / 主题切换"作为隐式 reward 触发反思

#### [3] Self-Refine: Iterative Refinement with Self-Feedback (arXiv 2303.17651, NeurIPS 2023)
- **章节**：§3 method, §4 7-task evaluation
- **关键论点**：单 LLM 同时扮 generator → feedback → refiner，无外部 reward
- **量化结论**：7 任务 GPT-3.5 / GPT-4 + Self-Refine 比 single-shot 平均 ~20% 绝对提升（5–40% 区间）；token 成本约 3x
- **Omubot 适用性**：仅作背景
- **理由**：3x 成本对实时 chat bot 致命；"先草稿再润色"对长篇知识科普类（角色为"老师"时）有用，不适合短闲聊

### 2.2 Q2 — Plan-then-utter

#### [4] Skeleton-of-Thought: Prompting LLMs for Efficient Parallel Generation (arXiv 2307.15337, ICLR 2024)
- **章节**：§2 method, §3.1 latency, Fig 2(a)/2(b), §3.2 quality
- **关键论点**：1 次 LLM call 生成"骨架要点列表" → 每点并行展开（API 并发或 batched decoding）→ 拼接，是 plan-then-utter 的工程化
- **量化结论**：12 LLM end-to-end latency 加速 up to **2.39×**；8/12 模型 >2×；GPT-4 / GPT-3.5 / Claude 上 ~2× 加速；FastChat 评测 SoT 与 baseline 持平（win 45.8%）；不适合数学/代码（强串行依赖）
- **Omubot 适用性**：直接可借鉴
- **理由**：与"边想边发"完美对齐——1 次 plan call 出"我要发的几条消息提纲"，每条独立第二次 call 展开后立即 flush；论文已验证 commonsense / roleplay / counterfactual（最像群聊场景）质量不掉。**Part 6 方案 A 的核心理论支撑**

#### [5] Plan-and-Solve Prompting (arXiv 2305.04091, ACL 2023)
- **章节**：§3 PS+ prompt design, §4 Table 2
- **关键论点**：Zero-shot CoT 经常 missing-step；显式"先 devise plan 再 carry out"稳住步骤
- **量化结论**：6 个数学数据集 PS+ 比 Zero-shot-CoT ≥ +5%（GSM8K +2.9%）；平均 70.4 → 76.7；与 8-shot manual CoT (77.6) 几乎持平；token 成本仅 +1 句 trigger
- **Omubot 适用性**：部分可借鉴
- **理由**：trigger sentence 改写极便宜；"plan token 几乎零成本就能稳化输出"现象对 Omubot 启发——plan 阶段加 30 token 提纲指令成本远低于 1024 后切

#### [6] Tree of Thoughts: Deliberate Problem Solving with LLMs (arXiv 2305.10601, NeurIPS 2023)
- **章节**：§3 ToT framework, §4.1 Game of 24, §4.2 Creative Writing
- **关键论点**：把"plan"展开成树/搜索，每节点独立评估
- **量化结论**：Game of 24 GPT-4 CoT 4% → ToT 74%（绝对 +70%）；token 成本 ~100× CoT；reproducibility 69%
- **Omubot 适用性**：仅作背景
- **理由**：成本爆炸 + 群聊不需 search；但"thought as text unit + parallel evaluator"概念对 Omubot"骨架要点 + 自评要不要发"有启发

#### [7] Chain-of-Verification Reduces Hallucination (arXiv 2309.11495, ACL 2024 Findings)
- **章节**：§3 4-step CoVe pipeline, §4.4 longform, §4.5 factored
- **关键论点**：(i) draft → (ii) plan verify questions → (iii) answer in isolation → (iv) revise
- **量化结论**：longform biography FACTSCORE 55.9 → 71.4（+28% absolute）；factored 比 joint +3 FACTSCORE；MultiSpanQA F1 0.39 → 0.48（+23%）；token 成本 ~4× baseline
- **Omubot 适用性**：部分可借鉴
- **理由**：factored variant 的"answer in isolation 防 hallucination 复制"对多消息场景有借鉴——每条独立 call 而不是看着前一句"将错就错"；但 4× token 不能用于普通闲聊

### 2.3 Q3 — Reactive mid-generation

#### [8] A Full-duplex Speech Dialogue Scheme Based On Large Language Model (NeurIPS 2024, Wang et al.)
- **章节**：§3 neural FSM, §4 Table 2 (interrupt rationality), Table 3 (latency)
- **关键论点**：LLM 在生成时同时 emit "control tokens" 决定 START_SPEAK / HALT / INTERRUPT_USER；本质是把"是否中止当前生成"做成 next-token prediction
- **量化结论**：Llama-3-8B-Instruct-fd 对用户打断响应正确率 **96.7%**，机器主动打断 precision 54.7%；latency 0.68s vs ASR-VAD 1.48s（**3× 减幅**）；38.8% interruption 发生在 user 仍说话时
- **Omubot 适用性**：直接可借鉴
- **理由**：核心思想"在生成过程中 LLM 自己决定是否 abort"可移植到文本群聊——SSE token-stream 检测到新消息进群 → 喂 control token → 决定续写或重 plan。Omubot 已有 SSE 流，工程缺口是"abort signal + 重 plan prompt 重写"。**Part 6 方案 C 的核心理论支撑**

#### [9] Combining Incremental Language Generation and Incremental Speech Synthesis (Baumann & Schlangen, SIGDIAL 2012)
- **章节**：method + evaluation
- **关键论点**：NLG 和 TTS 都做成 incremental modules，可在 utterance 半中接 acoustic understanding feedback → pause / repeat / rephrase 已发出部分
- **量化结论**：用户评分显著高于 (a) 完全忽略 interrupt 的 baseline 和 (b) 仅 pause 的 baseline；response time 更低（具体 ms 全文不可达）
- **Omubot 适用性**：直接可借鉴
- **理由**："mid-generation 收到对方信号 → 调整策略"的最早工程化样板，与"边想边发 + 看到对方回复就修正"目标完全同构

#### [10] Incremental Dialogue Management: Survey and Implications for HRI (arXiv 2501.00953v2, 2025)
- **章节**：§2 incremental processing, §4 DM requirements, §5 LLM age implications
- **关键论点**：当前 LLM "inherently monotonic"——无法在生成中修正解释；给出 incremental DM 需求清单（revisable hypotheses / partial commit / rollback）
- **量化结论**：survey；指出 incremental DM 文献仅 <10 篇，远少于 incremental ASR/NLG；IM 时代后绝大多数 LLM agent 仍 batch
- **Omubot 适用性**：直接可借鉴
- **理由**：survey 给出 incremental DM 的工程清单（rollback / revoke / commit），是 Omubot 设计 abort+replan 的需求 checklist

#### [11] Incremental Segmentation and Decoding Strategies for Simultaneous Translation (Yarmohammadi et al., IJCNLP 2013)
- **章节**：§3 silence vs phrase-based segmentation, §4 latency-accuracy tradeoff
- **关键论点**：online segmentation 决定何时把累积输入交给下游
- **量化结论**：silence-based segment 平均 4.28±3.28 词；phrase-based 6.56±4.73 词；BLEU 损失 ~1–2 点 vs 全句 batch
- **Omubot 适用性**：部分可借鉴
- **理由**：边想边发的 segment 决策与 simultaneous translation 同构——latency vs 完整性 tradeoff 框架可直接套用

### 2.4 Q4 — Streaming-as-segment

#### [12] PySBD: Pragmatic Sentence Boundary Disambiguation (Sadvilkar & Neumann, NLP-OSS 2020)
- **章节**：§5 Table 1, Table 2
- **关键论点**：rule-based SBD 在 noisy 文本（缩写 / 括号 / 列表）上比 ML 训练的 spaCy-dep / stanza 更稳
- **量化结论**：Golden Rules Set 准确率：PySBD 97.92% / blingfire 75.00% / syntok 68.75% / spaCy 52.08%；blingfire 最快 85ms 处理 100K 词
- **Omubot 适用性**：直接可借鉴
- **理由**：Omubot SSE 流上做 online SBD 可直接选 blingfire（85ms / 100K 词足够实时）或 PySBD（精度优先）；中文 SBD 需自定义，但量化基准框架可复用

#### [13] Adaptive Token Pacing for Cognitive-Friendly LLM Streaming (CHI EA 2026)
- **章节**：abstract + method（全文不可达）
- **关键论点**：默认 token-by-token streaming pacing 不规律，破坏阅读流畅；提 adaptive pacing 在自然边界处释放
- **量化结论**：声称提升 reading flow 但具体数字全文不可达
- **Omubot 适用性**：仅作背景
- **理由**：CHI EA 定量证据不足；只能作为"在 token-stream 上做语义边界 pacing"的 motivation 引用

### 2.5 Q5 — Pause-then-extend

#### [14] Responsiveness in Instant Messaging: Predictive Models (Avrahami & Hudson, CHI 2006)
- **章节**：Method (90K msg corpus from 16 users), Results
- **关键论点**：IM responsiveness 高度可预测——上下文（在线状态、历史、消息内容）决定回复是否在 N 秒内到达
- **量化结论**：90,001 条真实 IM 训练，30s/1m/2m/5m/10m 窗口预测准确率 **up to 90.1%**
- **Omubot 适用性**：直接可借鉴
- **理由**：直接给出"对方在 30s/1m/2m/5m 内回复的 base rate"——**Part 6 方案 D pause-then-extend 的 timer 阈值应卡在 30s（90% 用户回复在此前完成）**

#### [15] IM Waiting: Timing and Responsiveness in Semi-Synchronous Communication (Avrahami, Fussell & Hudson, CSCW 2008)
- **章节**：Results (work-fragmentation correlation, presentation effects)
- **关键论点**：IM "语义同步度"在使用者侧高度可塑——通知 presentation 比"对方在打字"指示器对响应速度的影响更大
- **量化结论**：work-fragmentation 与 faster response 显著正相关 (p<.05)；presentation 变量 effect size 大于 typing-indicator
- **Omubot 适用性**：直接可借鉴
- **理由**：印证"IM 不是同步通话"，pause-then-extend 节奏在用户侧被接受；机器单向"等-看-续发"不会被视为故障

#### [16] Interaction and Outeraction: Instant Messaging in Action (Nardi, Whittaker & Bradner, CSCW 2000)
- **章节**：Findings (outeraction concept), Implications
- **关键论点**：IM 核心不是信息传输（interaction），而是"connection management" / "negotiating availability" / "rhythmic conversation"（outeraction）
- **量化结论**：ethnographic N=20；定性发现 IM 对话频繁多段断续，user 期待"间歇连接"而非"完整轮次"
- **Omubot 适用性**：直接可借鉴
- **理由**：奠定 Omubot 多段输出合法性——单条 1024 后切违反 IM 文化；outeraction 概念发源，几乎所有后续 IM 节奏研究都引

#### [17] Turn-taking in Conversational Systems and Human-Robot Interaction: A Review (Skantze, Computer Speech & Language 67, 2021)
- **章节**：§3 multi-modal cues, §4 end-of-turn detection, §5 user interruption
- **关键论点**：turn-taking 是多模态线索整合（syntax / prosody / breathing / gaze），text-only 渠道下需用 syntactic completion + 语用信号代偿
- **量化结论**：human conversation gap 中位数 ~200ms，conversational system 通常 ~1500ms
- **Omubot 适用性**：部分可借鉴
- **理由**：text 群聊缺 prosody / breathing，但论文给出"完成度信号 + 静默时长"组合规则，可作为"发完一段后等多久"的设计依据

### 2.6 Q6 — 成本 / 缓存影响

#### [18] Anthropic Prompt Caching 官方机制（Anthropic API spec + Cadence/Spring AI/Portkey 文档汇总）
- **章节**：cache_control breakpoints, TTL, prefix-match
- **关键论点**：cache 是 byte-match prefix，breakpoint 切段；任何 upstream 改动 invalidate 下游所有段
- **量化结论**：默认 TTL **5 分钟**（每次命中刷新），可显式 1h（write 2× 价格）；命中读 = base × 10%（90% off）；write = base × 1.25；最多 4 breakpoints；search 回溯 ≤20 blocks；min cacheable Sonnet 4.6 1024 tokens / Opus 4.7 4096 tokens；mixed TTL 必须 1h-block 在 5min-block 之前
- **Omubot 适用性**：直接可借鉴
- **理由**：multi-call 设计直接撞 Omubot 现有 4 个 cache 断点（tools / system 1 / system 2 / messages near-end）——若每次 plan call 改 system，所有下游 cache 失效；plan-then-utter 必须把 plan call 也设计成 prefix-stable

#### [19] Efficient Memory Management for Large Language Model Serving with PagedAttention (Kwon et al., SOSP 2023, vLLM)
- **章节**：§3 PagedAttention, §6 evaluation
- **关键论点**：把 KV cache 像 OS 虚拟内存一样分页 → 减少碎片 + 跨请求共享 prefix
- **量化结论**：吞吐率 **2–4×** vs FasterTransformer/Orca @ 同 latency；prefix sharing 在 multi-call 同前缀场景几乎零额外内存
- **Omubot 适用性**：仅作背景
- **理由**：Omubot 用 Anthropic API（黑盒）无法直接用 vLLM；但论文证明"multi-call 共享 prefix"在系统层免费——这是 Anthropic prompt cache 商业产品的理论基础

#### [20] SGLang: Efficient Execution of Structured Language Model Programs (arXiv 2312.07104, NeurIPS 2024)
- **章节**：§3 RadixAttention, §4 compressed FSM, §5 API speculative execution, §6 evaluation
- **关键论点**：multi-call 程序的 KV cache 在 radix tree 里 LRU 共享 → 自动 prefix reuse；针对 API-only 模型有 API speculative execution
- **量化结论**：throughput up to **6.4×** vs SOTA 推理系统，6 类 multi-call agent 工作负载受益最大
- **Omubot 适用性**：部分可借鉴
- **理由**：Anthropic 端不可控，但 §5 API speculative execution 对"调远端 API 的 multi-call agent"思路直接适用——本地预测下一 call 的输入提前发起以隐藏 latency

#### [21] Network and Systems Performance Characterization of MCP-Enabled LLM Agents (arXiv 2511.07426, 2025)
- **章节**：§4 prompt overhead breakdown, §5 cost-token-time analysis
- **关键论点**：MCP-style multi-call agent 因每次 round-trip 重复序列化 system / tools / history → prompt token 膨胀
- **量化结论**：MCP 工作负载 prompt-to-completion 比 baseline chat **2× ~ 30×**；强制 serial tool call 显著拉长端到端 latency；建议 batch / parallel tool dispatch
- **Omubot 适用性**：直接可借鉴
- **理由**：直接量化"multi-call 隐性成本"——若 Omubot 把 1 次 1024 call 拆成 5 次 200 call，prompt overhead 可能放大 2–30×；论文的"parallel tool calls"建议对应 SoT 并发展开，是 multi-call 设计必须做的优化

### 2.7 Q7 — Persona stability

#### [22] PersonaGym: Evaluating Persona Agents and LLMs (arXiv 2407.18416, EMNLP 2025 Findings)
- **章节**：§3 PersonaScore (5 decision-theoretic tasks), §5 benchmark
- **关键论点**：PersonaScore 是 human-aligned automatic metric；测 10 LLM × 200 persona × 10,000 question
- **量化结论**：**GPT-4.1 PersonaScore 与 LLaMA-3-8B 完全相同**（model size 不决定 persona faithfulness）；Claude 3 Haiku 在 persona-conform 上"非常 resistant"
- **Omubot 适用性**：直接可借鉴
- **理由**：Omubot 切 multi-call 后 persona drift 必须有 metric 量化；PersonaScore 5 任务可直接套用做回归测试

#### [23] Drift No More? Context Equilibria in Multi-Turn LLM Interactions (arXiv 2510.07777, 2025)
- **章节**：§4 dynamical framework (KL divergence recurrence), §5 τ-bench
- **关键论点**：drift 不是无限发散，而是稳定在 "noise-limited equilibrium"；reminder intervention 把均衡点下移
- **量化结论**：GPT-4.1 self-divergence KL **<0.05** over T=10 turns（可作 anchor）；δt=0 drift 收敛到有限值，δt>ε（reminder injection）均衡点降低
- **Omubot 适用性**：直接可借鉴
- **理由**：直接回答"multi-call 是否会 persona 越漂越远"——结论是**不会无限漂**，定期 reminder 即可；Omubot 在每轮 utter call 重新注 persona anchor 就够，**不需要每个 call 全量 system prompt**。**Part 6 方案 A 成本可行性的关键支撑**

#### [24] The Assistant Axis: Situating and Stabilizing the Default Persona of LLMs (arXiv 2601.10387, Anthropic 2026)
- **章节**：Method (axis discovery in activation), Results (drift monitoring + steering)
- **关键论点**：可在 activation space 识别一条"Assistant 方向"——监测和 steering 该方向能稳住 default persona
- **量化结论**：定义 drift detection 指标（具体百分比全文不可达）；提 steering 可在 long context 下回拉 persona
- **Omubot 适用性**：仅作背景
- **理由**：activation steering 需白盒模型，Anthropic API 不暴露；但概念框架可指导 Omubot 用"persona-anchor sentence 的 embedding distance"做轻量监测

#### [25] Identifying and Mitigating Bottlenecks in Role-Playing Agents (arXiv 2601.04716, 2026)
- **章节**：3-axis disentangling (Familiarity / Structure / Disposition), 211-persona dataset
- **关键论点**：role-play 质量主要受 Disposition (moral/immoral) 影响，Familiarity 和 Structure 几乎不影响
- **量化结论**：Moral vs Immoral persona 性能差距巨大；Field-Aware Contrastive Decoding (FACD) 是 training-free 缓解
- **Omubot 适用性**：部分可借鉴
- **理由**：Omubot 角色配置写在 `config/soul/identity.md`，论文说明 Structure 不重要、Disposition 重要——可指导 identity.md 内容优先级（强化 disposition 而非格式细节）

### 2.8 Q8 — 可观测性 / Trace schema

#### [26] OpenTelemetry GenAI Semantic Conventions (opentelemetry.io spec v1.37+)
- **章节**：gen-ai-spans.md attribute table
- **关键论点**：标准化 GenAI span schema，supports parent-child for multi-call agent trace
- **量化结论**：必填 attr：`gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.request.stream`；推荐：`gen_ai.usage.input_tokens / output_tokens / cache_creation.input_tokens`；Datadog 已支持 v1.37
- **Omubot 适用性**：直接可借鉴
- **理由**：Omubot 现有 SQLite usage tracking 缺 trace 树概念；接入 OTel GenAI semconv 可让"一条逻辑回复 → N 个 LLM call"自然挂在同一 trace 下，parent_span_id 字段直接给 segment_chain 关联

#### [27] Langfuse Tracing Schema (langfuse-js packages/tracing/src/types.ts)
- **章节**：observation types + parent context types
- **关键论点**：observation 分 9 类（span / generation / event / embedding / agent / tool / chain / retriever / evaluator / guardrail），均带 `traceId + parentObservationId`
- **量化结论**：generation 类专用于 LLM call（带 model / usage / cost）；spec 不限 trace 内 generation 数量
- **Omubot 适用性**：部分可借鉴
- **理由**：generation type 与 OTel GenAI 对齐但更易自托管；Omubot 若需快速接入"一条回复多 call"trace，Langfuse SDK 比手写 OTel exporter 成本更低

### 2.9 文献缺口与限制

- **Q4 文献稀少**：streaming-as-segment 在 LLM 时代直接的 token-stream online SBD 论文几乎没有（多数仍是 batch-after-the-fact），仅找到 PySBD（pre-LLM 工程基准）和 Adaptive Token Pacing（CHI EA，证据不足）。下游若做该轴需自行实现并 benchmark
- **Q3 经典文献全文受限**：DeVault-Stone 2003 / Skantze-Hjalmarsson 2010 SIGDIAL / Schlangen-Skantze 2011 incremental DM 在 ACL Anthology 仅 abstract，已通过引用网络间接核实
- **Q6 prompt cache 数字来源**：Anthropic 官网原页未通过 fetch（domain 限制），引用的 5min/1h TTL / 90% 折扣 / 4096-token 阈值 / 4 breakpoints / 20-block lookback 等数字来自第三方文档（Cadence / Spring AI / Portkey / agentpatterns.ai / particula.tech）按官方 spec 转述，已交叉印证 ≥3 处一致
- **Q7 Anthropic Assistant Axis (arXiv 2601.10387)**：发布于 2026-01-15，全文获取仅经 abstract + GitHub safety-research/assistant-axis README，定量数字未取得；仅作背景使用

合计 **27 篇**，Q2 / Q3 / Q5 / Q6 主线每轴 ≥ 3 篇且均含量化结论；Q1 / Q4 / Q7 / Q8 各 ≥ 1 篇含量化结论。

---

## 3 候选架构方案 A / B / C / D

### 3.0 通用约束与口径

所有方案保持 [services/llm/llm_request.py:303 apply_cache_breakpoints](../../services/llm/llm_request.py#L303) 4-breakpoint 注入路径不变。成本口径：Opus 4.7 input:output ≈ 1:5，cache_read=0.10×，cache_write_5min=1.25×；baseline main 单次 input ≈ 6000 tokens / output ≈ 600 tokens / 总价 ≈ 9000（按 input=1 / output=5 折算）。N 默认取 4 段；每段 utter 默认 max_tokens=150。所有方案的 segment_chain trace 字段统一遵循 §6.4。

### 3.1 方案 A — Plan-then-utter

**调用形态**：1 次 thinker `plan` call（max_tokens=128，输出段大纲 JSON 数组）→ N 次 main `utter` call（每段 max_tokens=150，plan 对应行注入 messages tail）→ 每段 utter 完成立即 flush。结构与 MaiBot planner + replyer ([1.2.1]) 同形，但 plan 输出"段提纲数组"而非"action 选择"。

**Cache 兼容性**：system static / system stable / tool tail 全部 byte-stable → N 段命中 cache_read 0.10×；messages tail 每段 cache_creation。1 plan + 4 utter = 5 LLM call，仅 messages tail 5 次 cache_write。无需改 [llm_request.py:303](../../services/llm/llm_request.py#L303)。

**Token 成本系数**：plan input 6000×1.0=6000 + plan output 50×5=250 + utter input cached 6000×0.10×4=2400 + utter input delta（plan 行 ~200）200×1.0×4=800 + utter output 150×5×4=3000 → 合计 **12450**，vs baseline 9000 = **1.38×**。每段限 80 tokens：1.23×；限 50 tokens：1.16×。

**Latency P95**（Opus 4.7 TTFT P50≈600ms / P95≈1500ms / decode ≈50 tok/s）：baseline 1500+600×20=13500ms；A 全段完成 1500+50×20 + 4×(1500+150×20)=20500ms；**A 用户首段可见**：plan+utter#1=2500+4500=**7000ms**（**显著优于 baseline P95**）。

**复杂度**：~355 行（`services/llm/plan_then_utter.py` 驱动 120 + [kernel/router.py](../../kernel/router.py) 旗标分流 25 + [client.py](../../services/llm/client.py) plan 行注入 30 + `services/block_trace/segment_chain_provider.py` 60 + [kernel/config.py](../../kernel/config.py) 配置字段 20 + 测试 100）。

**Part 5 共存策略**：`plan_then_utter.enabled=true` 时 Part 5 `natural_split` 自动退化为 noop（仅尾部标点清洁）；互斥旗标 `plan_then_utter.disable_natural_split: bool = true` 默认开启；plan-then-utter 关闭时 Part 5 仍作兜底。

**论文支撑**：SoT [4] 2.39× 加速 + roleplay 质量持平；Drift No More [23] GPT-4.1 KL<0.05 over 10 turns → multi-call 不必每段重灌 system；Plan-and-Solve [5] +1 句 plan trigger 几乎零成本稳化顺序；MaiBot ([1.2.1]) planner+replyer 已工程化。

### 3.2 方案 B — Streaming-as-segment

**调用形态**：保持 1 次 main call（max_tokens=1024 不变）；改 [client.py:678](../../services/llm/client.py#L678) drain-all 为 incremental decode；在 SSE token-stream 上 online SBD（中文按句号/问号/感叹号/换行；英文走 PySBD），遇边界立即 flush 当前累积 buffer 到 IM；最终残量 flush 为末段。

**Cache 兼容性**：完全不变（仍 1 call），cache_read 命中率与 baseline 一致。

**Token 成本系数**：**1.0×**（无新增 call）。

**Latency P95**：TTFT 1500ms 不变；首边界出现于约 80 tokens × 20ms=1600ms 后 → **首段可见 ~3100ms**（vs baseline 13500ms 才见全段）。

**复杂度**：~150 行（incremental decode 50 + online SBD 60 + flush trigger 20 + 测试 20）。

**Part 5 共存策略**：与 Part 5 `natural_split` 语义重叠（事后切分 vs 流式同步切分）。建议互斥：`streaming_segment.enabled=true` 时关 `natural_split.enabled`。

**论文支撑**：PySBD [12] Golden Rules 97.92% / blingfire 85ms/100K 词；Adaptive Token Pacing [13] 提供"边界 flush 优于 token-by-token"motivation；Yarmohammadi [11] simultaneous translation 验证 online segmentation 在 latency-accuracy tradeoff 可控。

**局限**：仍是 single call，**无法在生成中观察对方反应**——群中插话时 bot 仍闷头跑完 1024 tokens；该缺陷须升级到方案 C。

### 3.3 方案 C — Reactive replan

**调用形态**：方案 A 之上加 mid-generation abort 触发器。每次 utter call 启动时挂 `abort_event = asyncio.Event()`；[bot.py group_listener](../../bot.py) 收到群新消息且符合 reactive 条件（同会话 / 非 bot 自发 / topic 连续）时 set 该 event；[client.py:678 SSE drain loop](../../services/llm/client.py#L678) 每帧检查 event → break + `resp.close()` → 把已生成 partial flush 出去 → 喂回 plan call 重写剩余 plan → 续 utter。

**Cache 兼容性**：与方案 A 同（plan / utter 全 byte-stable）。abort 不影响 cache（已写入 5min TTL 内的 cache 仍可被续 call 复用）。**关键监控**：[1.3.2] 提示 cache_creation 字段需区分 5min/1h；当前 `_record_usage` 缺该字段。

**Token 成本系数**：plan 6000+250 + utter#1 partial input cached 2400+200 + utter#1 partial output 80×5=400（比 baseline 多付的 abort 漂移成本：5-30 tokens [1.3.1]）+ replan input cached 2400+250 + 续 utter#2..N input cached + delta + output → 合计约 13000~14500，vs baseline 9000 = **1.45×~1.61×**（1 次 abort 场景）；多次 abort 线性叠加，最坏 4 段 4 次 abort = 2.1×。

**Latency P95**：首段可见与方案 A 同（~7000ms）；abort 后续 utter TTFT 全部命中 cache_read → 约 1500ms × 命中减半 ≈ 800ms；abort 决策延迟 ≤ SSE 帧间隔（Anthropic ~50ms）。

**复杂度**：~280 行（在方案 A 之上 +abort_event wiring 80 + replan trigger 50 + group_listener 钩子 40 + 漂移 token 计费 30 + 测试 80）。**总和方案 A+C ≈ 635 行**。

**Part 5 共存策略**：与方案 A 相同（互斥 `disable_natural_split`）。Part 5 兜底关闭。

**论文支撑**：Full-duplex LLM [8] 96.7% 中断响应正确率（control token 范式是同向工程化）；Incremental Dialogue Management [10] 给出 incremental DM 工程清单（revisable / partial commit / rollback）；Baumann-Schlangen [9] 早期 incremental NLG+TTS 样板；Anthropic abort 计费规则 [1.3.1] 量化漂移上限 5-30 tokens。

**风险点**：开源 IM 场景**无现成 reactive 实现**（[1.4]）——Pipecat 仅 audio。Omubot 是该模式在文本群聊的首例落地，触发器规则需谨慎设计（误触发会让 bot 半途而废）。

### 3.4 方案 D — Pause-then-extend

**调用形态**：发完第一段 → 在群上下文里 register pending follow-up timer（30s / 60s / 120s 三档）→ 监听该会话窗口对方是否回复：① 对方回复 → 清 timer，下一轮 chat() 自然处理；② timer 到期且无回复 → 触发"追发 call"（独立 LLM call，max_tokens=200，prompt 含"上次发了 X，对方未回，是否追发？输出 JSON {extend: bool, text: string}"）。

**Cache 兼容性**：完全保留——所有 call 走标准 chat() 路径，cache 命中率与 baseline 一致。

**Token 成本系数**：base case（对方在 30s 内回复，~90% 用户 [14]） = **1.0×**（无追发）；追发触发场景 = baseline + 1 次 6000+50×5+200×5=7250 = 1.81×；按 [14] 30s 窗口 90.1% 命中率折算期望 = 0.9×1.0 + 0.1×1.81 = **1.08×**。

**Latency P95**：首段与 baseline 一致；追发延迟 = pause 阈值（30s/60s/120s）+ TTFT 1500ms。**用户感知 latency 不退化**——pause 是设计的一部分而非延迟。

**复杂度**：~180 行（pending follow-up timer manager 60 + group_listener 取消钩子 30 + 追发 call 路径 40 + Part 4 episode 复用现有 chat() 不动 + 测试 50）。

**Part 5 共存策略**：完全正交——Part 5 仍负责"一次 LLM 输出后段间延迟"，Part 6 D 负责"段间延迟之后是否追发"。两者叠加时段间节奏 = Part 5 inter_segment_delay + Part 6 D pause_then_extend_window。

**论文支撑**：Avrahami-Hudson [14] 30s 窗口预测准确率 90.1% → 直接给 timer 阈值；Nardi-Whittaker-Bradner [16] outeraction 概念支持"间歇连接"合法性；Avrahami-Fussell-Hudson [15] semi-synchronous 论证 IM 等-看-续发不被视为故障；Skantze [17] turn-taking review 提供 text-only 渠道的等待时长设计依据。

**局限**：**不解决"边想边发"问题**——首段仍是单 call 1024 全收。本方案是 Part 1 V1-V14 表面拟人化的**节奏延伸**，不是源头生成调度的根本性改造。仅作为最低成本兜底选项。

---

## 4 决策矩阵

按 Part 6 §0.2 取证原则，所有数值均回引 §1 / §2 / §3 证据；不可计算项标 N/A，不留 TBD。

| 方案 | token 成本系数 | cache 命中破坏 | 用户首段可见 latency P95 | 代码净增（行） | 拟人化收益 | 回滚成本 | 工程风险 |
|---|---|---|---|---|---|---|---|
| **A Plan-then-utter** | 1.16~1.38× | 无（plan/utter prefix byte-stable） | ~7000ms（plan 2500 + utter#1 4500） | ~355 | 中：边想边发 + plan 控顺序 | 低（旗标 off 即回 baseline） | 中（plan JSON schema 设计） |
| **B Streaming-as-segment** | 1.0× | 无 | ~3100ms（TTFT 1500 + 首边界 1600） | ~150 | 低-中：仅切分时机提前，思考粒度仍 1024 | 极低（旗标 off） | 低（incremental decode + SBD） |
| **C Reactive replan** | 1.45~2.1× | 无（cache_creation 字段需补 [1.3.2]） | ~7000ms 首段 / abort 续段 ~800ms | ~635（A+C） | **高**：边想边发 + 可被对方插话打断重 plan | 中（abort_event wiring 拆解） | **高**（IM 场景无现成参考 [1.4]） |
| **D Pause-then-extend** | 1.08×（期望）/ 1.81×（追发触发） | 无 | 与 baseline 一致；追发后延 30~120s | ~180 | 低：仅追发节奏拟人，首段仍 1024 | 极低（旗标 off） | 低（timer + group_listener 钩子） |

### 4.1 维度判读

- **token 成本**：B << D < A < C；A 在 80-token utter 限额下 1.23× 已可接受
- **首段可见 latency**：B < A ≈ C < D（D pause 是设计的一部分非退化）；B 在 latency 维度独占优势
- **复杂度**：B < D < A < C；C 是 A 的 1.79 倍代码量
- **拟人化收益**：C > A > B ≈ D；C 的"被对方插话打断重 plan"是源头生成调度的最强形态
- **回滚成本**：所有方案均通过单一 config 旗标 disable（详见 §8）

### 4.2 推荐路径（仅候选，最终决策由用户）

按工程风险递增、拟人化收益递增分阶段推进：

1. **第一阶段（最低风险验证）**：方案 B 单独上线 → 验证 incremental decode + online SBD 在 Anthropic SSE 上稳定性 → 复用至方案 A/C
2. **第二阶段（核心方案）**：方案 A 与方案 B 互斥（旗标分流）→ A 成熟后下线 B
3. **第三阶段（最强形态）**：方案 C 在方案 A 基础上加 abort 路径 → 与 §1.4 Pipecat 案例形成开源对照
4. **D 不进推荐路径**：拟人化收益过低，仅作 Part 1 V/U 表面节奏的延伸；如要追发节奏，可单独作为 Part 5 子任务

---

## 5 不做的事

- **不立即立项 P 任务**——本文是 Part 4 模式（先研究存档，后由用户决策推进）
- **不动 prompt cache 4-breakpoint 布局**——观察 multi-call 对其的破坏量，不重设计
- **不改 services/llm/client.py 当前 single-call 路径**——研究阶段不动代码
- **不替代 Part 5**——Part 5 的 natural_split 仍可作为兜底切分；Part 6 只在调用形态上提供新选择

---

## 6 与既有 Part 的接入点

### 6.1 与 Part 1 V/U 系列

| 子项 | 接入点 | 推荐处理 |
|---|---|---|
| **V1 RegisterClassifier** | [services/humanization/classifier.py:73](../../services/humanization/classifier.py#L73) 是独立 thinker call（[1.1.3]） | 方案 A/C：plan call 已含语境，可让 plan 同时输出 register slot → 省 1 次 classifier call；方案 B/D：每段不重算，沿用 chat() 唯一一次 classifier 结果 |
| **V8 StylometricScorer** | [services/humanization/stylometric_scorer.py](../../services/humanization/stylometric_scorer.py) 在整段输出后打分 | 方案 A/C：每段独立打分，分数取最大值进 BlockTrace；方案 B：合并段后打分（与 baseline 同）；方案 D：首段+追发独立打分 |
| **V11 critic-rewrite-loop** | [client.py:1689 _maybe_rewrite_humanization_reply](../../services/llm/client.py#L1689) 默认 -1.0 关闭（[1.1.4]） | 生产冷代码，Part 6 不耦合；若未来开启 V11，方案 A/C 每段独立 critic（避免 1024-token rewrite 成本爆炸） |
| **U6 RuntimeStateBus** | [kernel/bus.py:47](../../kernel/bus.py#L47) `fire_on_thinker_decision` 钩子 | 方案 A/C：每次 utter call 复用同一 bus event（不重 fire thinker）；方案 D：追发 call 独立 fire 一次 |

### 6.2 与 Part 5

两条可能路径，由旗标 `plan_then_utter.disable_natural_split` 控制：

- **路径 1（嵌套）**：Part 6 多 call → 每 call 内仍走 Part 5 natural_split。**不推荐**——utter call max_tokens=150 时基本不需再切分，反而引入双重切分歧义
- **路径 2（卸载）**：Part 6 多 call → 每 call 输出已是单段，natural_split 退化为 noop（仅末尾标点清洁保留）。**推荐**——方案 A/C 默认开启 `disable_natural_split=true`，方案 B 与 Part 5 互斥（语义重叠），方案 D 完全正交

### 6.3 与 Part 2-3

- **Part 2-3 仍负责"是否进入下一次 chat()"**：方案 A/C 不改变 group 触发判定逻辑（[plugins/chat/plugin.py group_listener](../../plugins/chat/plugin.py)）
- **方案 C reactive 触发器与 Part 2-3 共享上下文**：判定"群新消息是否打断当前生成"应复用 Part 2-3 已有的 addressee / topic / @ 信号——避免在 Part 6 重写一套 group context
- **接入点**：[kernel/bus.py:274 fire_on_*](../../kernel/bus.py#L274) 已有 group event channel，方案 C abort_event 可订阅同一 channel

### 6.4 与 Part 4

- **Part 4 episode 检索成本**：方案 A/C 多 call 时 episode 检索**不应每段重触发**——episode 应在 plan call 时一次性检索好，注入 plan 输出，由 utter call 共享
- **接入点**：[services/episodic/store.py](../../services/episodic/store.py) 调用方在 chat() 主循环；方案 A 把 episode 检索前移到 plan call 之前
- **BlockTrace 因果链**：新增 `segment_chain_id`（UUID）+ `segment_index`（int）字段，所有同 chain 的 LLM call / segment / episode_query 共享 chain_id；写 [services/block_trace/__init__.py BlockTrace 列](../../services/block_trace/__init__.py)；OTel GenAI semconv [26] `gen_ai.parent_span_id` 自然映射

---

## 7 候选子任务清单（仅候选，不入项）

按 Part 4 模式仅列出"如果决策推进，预计的子任务"，不分配优先级 / 不进入 Part 1 灰度通道。

| ID | 子任务 | 关联方案 | 预计行数 | 阻塞依赖 |
|---|---|---|---|---|
| P6.1 | `services/llm/incremental_sse.py` —— [client.py:678](../../services/llm/client.py#L678) drain-all → incremental decode | B / C | ~50 | 无 |
| P6.2 | `services/humanization/online_sbd.py` —— 中文 + PySBD 双语 online SBD | B / C | ~60 | P6.1 |
| P6.3 | `services/llm/plan_call.py` —— plan call 驱动 + JSON schema validation | A / C | ~120 | 无 |
| P6.4 | `services/llm/utter_call.py` —— utter call 单段调用 + plan 行注入 | A / C | ~80 | P6.3 |
| P6.5 | `services/block_trace/segment_chain_provider.py` —— segment_chain_id / segment_index | A / B / C | ~60 | 无 |
| P6.6 | `services/llm/abort_signal.py` —— SSE abort_event 钩子 + Anthropic resp.close 路径 | C | ~80 | P6.1 |
| P6.7 | `services/llm/replan_trigger.py` —— group_listener → abort → replan plan call | C | ~50 | P6.3 P6.6 |
| P6.8 | [`services/llm/client.py` _record_usage](../../services/llm/client.py#L1461) 补 `cache_creation.ephemeral_5m_input_tokens` / `_1h_input_tokens` 字段 | A / B / C | ~30 | 无（独立可推） |
| P6.9 | `services/humanization/follow_up_scheduler.py` —— pending follow-up timer manager | D | ~60 | 无 |
| P6.10 | `kernel/router.py` 旗标分流 —— `plan_then_utter.enabled` / `streaming_segment.enabled` / `reactive_replan.enabled` / `pause_then_extend.enabled` 互斥校验 | A / B / C / D | ~25 | P6.3 P6.4 P6.9 |
| P6.11 | OTel GenAI semconv 接入 —— `gen_ai.parent_span_id` / `gen_ai.usage.cache_creation.input_tokens` | A / B / C / D | ~50 | P6.5 P6.8 |
| P6.12 | persona_drift 回归测试 —— 复用 PersonaScore [22] 5 任务 | A / C | ~100 | P6.3 P6.4 |
| P6.13 | tests/test_part6_*.py —— 单测 + cancel-path（D2）+ persona drift KL<0.05 | A / B / C / D | ~250 | 全部上述 |
| P6.14 | docs/migrations/part6-* —— 灰度上线清单（旧 chat 路径 → 新分流路径四列回归清单 D3） | A / B / C / D | docs | 全部 |

合计预估：方案 A 单上 ~610 行；方案 B 单上 ~290 行；方案 C 全上 ~870 行；方案 D 单上 ~270 行；测试与 docs 不计入主代码。

---

## 8 风险与回滚

### 8.1 风险矩阵

| 风险 | 触发条件 | 量化阈值 | 监控来源 | 缓解 |
|---|---|---|---|---|
| **token 成本爆炸** | 方案 C abort 频次 > 设计预期 | 单日 abort 比例 > 30% 即触发降级 | `services/llm/client.py:_record_usage` 新增 `abort_count` 字段（P6.8 配套） | 自动降级到方案 A（关 reactive） |
| **latency P95 退化** | 方案 A plan call 阻塞 → 首段 > 10s | P95 > 10000ms 持续 5 分钟 | OTel GenAI span（P6.11） | thinker call profile 切换 haiku；plan max_tokens 降至 64 |
| **persona drift** | 多 call 间 persona 漂移 | KL > 0.05（PersonaScore [22] / Drift No More [23] 阈值） | tests/test_part6_persona_drift.py 离线评测 | 每 utter call 注入 persona-anchor sentence（论文 [23] 验证 reminder injection 降均衡点） |
| **BlockTrace 因果链断裂** | segment_chain_id 缺失或错挂 | tests/test_block_trace_segment_chain.py 失败 | tests | P6.5 强制 chain_id 注入；CI 红线 |
| **与 Part 1 V11 / V8 耦合 broke** | V11 启用且方案 A 上线 | tests/test_humanization_part1_v11_v8.py 失败 | tests | V11 仍冷代码（[1.1.4]）；如未来启用，P6.13 必须覆盖联合场景 |
| **cache 命中失效** | 误改 system prefix → byte 不稳 | cache_read tokens / total_input < 0.7 持续 1h | usage 表 + P6.11 | apply_cache_breakpoints 单元测试覆盖 plan/utter 双路径 byte-stable 校验 |
| **reactive 误触发** | group_listener 把 bot 自己回声 / 同会话不相关消息当成中断 | 单日误触 > 5 次 | abort_event 日志 | 触发器规则白名单：① 非 bot 自发 ② 同 topic（复用 Part 2-3 信号） |
| **Anthropic SSE abort 漂移成本** | 实测漂移 > 文档 5-30 token | 单 abort 漂移中位数 > 50 token | abort 漂移日志 | 累计漂移 > 阈值则切回方案 A（关 reactive） |
| **mid-stream cancel SDK 行为偏离** | aiohttp resp.close 未生效 / 连接泄漏 | 连接数 > 历史均值 2× | docker stats + Anthropic 端 idle connection 监控 | P6.6 集成测试覆盖；fallback 强制 await drain |

### 8.2 回滚路径

所有方案均通过 `kernel/config.py` 单一旗标 disable，**不需 docker rebuild**（D6：仅 .py 改动 → restart bot）：

```bash
# 方案 A 回滚
docker compose exec bot sed -i 's/plan_then_utter_enabled = true/plan_then_utter_enabled = false/' /app/config/config.toml
docker compose restart bot
# 验证：grep "PlanThenUtter disabled" /app/storage/logs/bot.log

# 方案 B 回滚
docker compose exec bot sed -i 's/streaming_segment_enabled = true/streaming_segment_enabled = false/' /app/config/config.toml
docker compose restart bot

# 方案 C 回滚（先关 reactive 保留 plan-then-utter）
sed -i 's/reactive_replan_enabled = true/reactive_replan_enabled = false/' /app/config/config.toml
docker compose restart bot
# 完全回滚到 baseline 同方案 A

# 方案 D 回滚
sed -i 's/pause_then_extend_enabled = true/pause_then_extend_enabled = false/' /app/config/config.toml
docker compose restart bot
```

### 8.3 灰度路径（如决策推进）

复用 Part 1 灰度模板（阶段 0 全 off → 阶段 1 单群 993065015 → 阶段 2 双群 993065015+984198159 → 阶段 3 全 allowed_groups）。每阶段最少 24h 基线观察；阶段间过渡条件：① P95 latency 不退化 ② token 成本系数 ≤ §3 设计阈值 ③ persona_drift KL < 0.05 ④ abort 比例 < 30%（仅方案 C）。

---

## 9 引用

### 9.1 论文（27 篇，详见 §2）

按研究轴归类：

- **Q1 Single vs Multi-call**：[1] ReAct (2210.03629) / [2] Reflexion (2303.11366) / [3] Self-Refine (2303.17651)
- **Q2 Plan-then-utter**：[4] SoT (2307.15337) / [5] Plan-and-Solve (2305.04091) / [6] ToT (2305.10601) / [7] CoVe (2309.11495)
- **Q3 Reactive mid-generation**：[8] Full-duplex LLM (NeurIPS 2024) / [9] Baumann-Schlangen (SIGDIAL 2012) / [10] Incremental DM Survey (2501.00953v2) / [11] Yarmohammadi (IJCNLP 2013)
- **Q4 Streaming-as-segment**：[12] PySBD (NLP-OSS 2020) / [13] Adaptive Token Pacing (CHI EA 2026)
- **Q5 Pause-then-extend**：[14] Avrahami-Hudson (CHI 2006) / [15] Avrahami-Fussell-Hudson (CSCW 2008) / [16] Nardi-Whittaker-Bradner (CSCW 2000) / [17] Skantze (CSL 67, 2021)
- **Q6 成本/缓存影响**：[18] Anthropic Prompt Caching spec / [19] PagedAttention (SOSP 2023) / [20] SGLang (2312.07104) / [21] MCP-Enabled LLM Agents (2511.07426)
- **Q7 Persona stability**：[22] PersonaGym (2407.18416) / [23] Drift No More (2510.07777) / [24] Assistant Axis (2601.10387) / [25] Role-Playing Agents Bottlenecks (2601.04716)
- **Q8 可观测性**：[26] OpenTelemetry GenAI semconv (v1.37+) / [27] Langfuse Tracing Schema

### 9.2 仓库 file:line（详见 §1）

- **Omubot 入口**：[services/llm/client.py:1963 chat()](../../services/llm/client.py#L1963) / [client.py:627 call_api](../../services/llm/client.py#L627) / [client.py:678 SSE drain](../../services/llm/client.py#L678) / [client.py:1109 _dispatch_call](../../services/llm/client.py#L1109)
- **Omubot 调用计数**：thinker [client.py:2071](../../services/llm/client.py#L2071) / main loop [client.py:2319](../../services/llm/client.py#L2319) / rewrite [client.py:1746](../../services/llm/client.py#L1746) / register classifier [plugins/chat/plugin.py:196](../../plugins/chat/plugin.py#L196) → [classifier.py:73](../../services/humanization/classifier.py#L73)
- **Omubot cache 注入**：[llm_request.py:303 apply_cache_breakpoints](../../services/llm/llm_request.py#L303) / [llm_request.py:347-359](../../services/llm/llm_request.py#L347)
- **Omubot tool loop**：[client.py:54 MAX_TOOL_ROUNDS=5](../../services/llm/client.py#L54) / [client.py:580 pass_turn](../../services/llm/client.py#L580) / [client.py:2344 pass_turn 退出](../../services/llm/client.py#L2344) / [client.py:2580 5 轮跑满](../../services/llm/client.py#L2580)
- **Omubot V11 冷代码**：[client.py:1689](../../services/llm/client.py#L1689) / [client.py:1700](../../services/llm/client.py#L1700) / [client.py:917](../../services/llm/client.py#L917) / [kernel/config.py:1060](../../kernel/config.py#L1060)
- **Omubot RuntimeBus**：[kernel/bus.py:47](../../kernel/bus.py#L47) / [kernel/bus.py:274](../../kernel/bus.py#L274)
- **MaiBot**：[planner.py:101 ActionPlanner](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/planner_actions/planner.py#L101) / [group_generator.py:50 DefaultReplyer](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/replyer/group_generator.py#L50) / [heartFC_chat.py:64 HeartFChatting](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/heart_flow/heartFC_chat.py#L64)
- **MaiBot dead code interrupt_flag**：[openai_client.py:283/528](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/llm_models/model_client/openai_client.py#L283) / [gemini_client.py:285/524](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/llm_models/model_client/gemini_client.py#L285)
- **MaiBot 切分函数**：[generator_api.py:173 process_llm_response](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/utils/generator_api.py#L173) / [utils.py:446 split_into_sentences_w_remove_punctuation](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/utils/utils.py#L446) / [uni_message_sender.py:326 typing sleep](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/message_receive/uni_message_sender.py#L326)

### 9.3 官方 SDK / 协议规范

- **Anthropic SDK abort**：anthropic-sdk-python helpers.md `client.messages.stream(...).close()`；usage 字段 `cache_creation.ephemeral_5m_input_tokens / ephemeral_1h_input_tokens`
- **Anthropic prompt cache**：5min default TTL；4 breakpoints；20-block lookback；Sonnet 4.6 ≥1024 / Opus 4.7 ≥4096 prefix tokens；read 0.10× / write 1.25×（5min）/ 2.0×（1h）
- **aiohttp**：`async with session.post(...) as resp:` 退出上下文 → 自动 close；外层 task cancel → 触发 CancelledError 链式关闭
- **vLLM**：PR #7111 (2024-08-03) `asyncio.aclose()` 显式 cancel；issue #24584 v1 引擎 abort 行为不一致（Omubot 不受影响 — Anthropic API）
- **SGLang**：RadixAttention LRU 共享 / API speculative execution（[20] §5）
- **Pipecat**：`SileroVADAnalyzer` + `enable_interruptions=True` 是开源唯一 reactive 实现，audio-based
- **OpenTelemetry GenAI semconv v1.37+**：`gen_ai.operation.name` / `gen_ai.provider.name` / `gen_ai.request.model` / `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` / `gen_ai.usage.cache_creation.input_tokens` / `gen_ai.parent_span_id`

---

## 10 当前状态

| 阶段 | 状态 | 证据 |
|---|---|---|
| 立项 | ✅ 完成（本文 §0） | 用户原话："part5 方案仍聚焦在话语分割处理上，而没有从 llm 生成的源头提供研究" |
| §1 代码取证 | ✅ 完成 | Omubot 3-8 LLM call 实证 / MaiBot interrupt_flag dead code / Anthropic abort 计费规则 / 4-breakpoint cache spine |
| §2 学术证据 | ✅ 完成 | 27 篇覆盖 8 轴，Q2/Q3/Q5/Q6 主线 ≥3 篇含量化结论 |
| §3 候选方案 A/B/C/D | ✅ 完成 | 4 方案均含调用形态 + cache 兼容 + token 系数 + latency P95 + 复杂度 + Part 5 共存 |
| §4 决策矩阵 | ✅ 完成 | 4 方案 × 7 维度 全填，无 TBD |
| §6 接入点 | ✅ 完成 | V1/V8/V11/U6 + Part 5 双路径 + Part 2-3 + Part 4 |
| §7 候选子任务 | ✅ 完成 | P6.1~P6.14 列出，预估行数与依赖 |
| §8 风险与回滚 | ✅ 完成 | 9 类风险 + 量化阈值 + 监控来源 + 缓解；4 方案均给 sed 一键回滚命令 |
| §9 引用 | ✅ 完成 | 27 论文按 8 轴归类 + Omubot/MaiBot file:line + SDK/协议规范 |

**调研完成。**等待用户决策推进路径（推荐 §4.2：B → A → C 三阶段；D 不进推荐）。本文存档为 Part 4 模式 —— **不立项 P 任务，不动代码，等待用户最终验收前的决策**。
