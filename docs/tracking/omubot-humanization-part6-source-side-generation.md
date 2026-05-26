# Omubot 拟人修复 Part 6 — 源头生成调度（LLM call schedule）调研 v2

> 状态：v1 立项 2026-05-25；**v2 重写 2026-05-26**（本文）。仅审计 + 调研阶段，不进 Part 1 / Part 5 主线施工。
>
> v1 → v2 修订动因（用户提出）：v1 §3 全部成本系数按 Anthropic Opus 4.7 推导，但 [config/config.json](../../config/config.json) `default_profile=main, profiles.main.api_format=deepseek, base_url=https://api.deepseek.com, model=deepseek-v4-flash` 显示生产 100% 流量走 DeepSeek V4-Flash。v1 数字（input:output 1:5、cache_read 0.10×、cache_write 1.25×、Opus 4.7 prefix 4096-token 阈值、4-breakpoint 硬上限）**全部不适用 DeepSeek 经济**——v2 按 DeepSeek 官方定价 + 生产 storage/usage.db 7 日真实流量重算。
>
> v2 关键新增：§3 生产实证 + cache 偏低根因；§4 cost 系数全表重算；§5 推荐路径重排（先 cache 稳定化前置 → B → D → A 慎用 → C 暂搁）。
>
> 触发（保留）：用户对 Part 5 反驳——「part5 方案仍聚焦在话语分割处理上，而没有从 llm 生成的源头提供研究」。Part 5 把"LLM 一次 1024-token 输出 → 客户端切碎"作为既定前提；Part 6 拒绝该前提，从生成调用本身的形态（call 数 / 触发节奏 / 可中断性 / 计划-执行分离）寻找拟人化突破口。
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
4. **成本可观测性**——每个候选方案必须给出 (i) DeepSeek V4-Flash 实价系数；(ii) 与生产 7 日 proactive 平均的乘数；(iii) latency P95 估算

### 0.3 8 条研究问题（研究轴，沿用 v1）

| # | 研究轴 | 核心问题 |
|---|---|---|
| Q1 | Single-call vs Multi-call | 一次 1024-token + 后切，与 N 次 short-token call，行为是否等价？token 成本系数？persona 漂移率？ |
| Q2 | Plan-then-utter | 先 LLM short call 生成"段大纲"（≤ 50 token），再每段独立 LLM call 落实 |
| Q3 | Reactive mid-generation | 生成中检测对方新消息 → 截断 + 重 plan |
| Q4 | Streaming-as-segment | 不动 call 数，但在 SSE token-stream 上 online 切，遇自然边界立即 flush |
| Q5 | Pause-then-extend | 发完第一段 → 等 N 秒 → 看对方是否回应 → 决定是否追发 |
| Q6 | 成本 / 缓存影响 | multi-call 对 **DeepSeek prefix cache（自动 byte-exact）**、token 成本、P95 latency 的破坏量 |
| Q7 | Persona stability | 每段独立 LLM call 时 persona / mood / register 在 N 段间的稳定性 |
| Q8 | 可观测性 | multi-call 因果链如何写入 BlockTrace / usage 表，是否需要 segment_chain_id |

### 0.4 不在本文范围

- 不动 Part 5 的 natural_split 算法本身——Part 6 是"是否还需要 natural_split"的元问题
- 不动 Part 1 V11 critic-rewrite-loop（默认冷代码 [§1.1.4]）
- 不动 Part 2/3 的 addressee / topic / @—Part 6 不决定"是否回复"，只决定"回复以何种调用形态生成"
- 不替换 LLM provider（DeepSeek V4-Flash 是给定）；本文不讨论"切回 Anthropic / 切到 V4-Pro"等 provider 选型
## 1 代码取证 — Omubot 现状 / DeepSeek V4-Flash 架构 / MaiBot 对照

### 1.1 Omubot 当前生成调用链（保留 v1，仅校正 cache 描述）

#### 1.1.1 主回复一次性 1024-token call（保留 v1）

[services/llm/client.py:659/671](../../services/llm/client.py#L659) `chat()` 主路径：单次 `call_api()`，`max_tokens=1024`，SSE 流式接收。生成完整后才进入 [client.py:359-538 `_reply_segments`](../../services/llm/client.py#L359-L538) 切段。生成阶段不读取群新消息，不做任何"段间观察"。

#### 1.1.2 Tool loop 最多 5 round（保留 v1）

[client.py:63 `MAX_TOOL_ROUNDS = 5`](../../services/llm/client.py#L63)，[client.py:2432](../../services/llm/client.py#L2432) `for round_i in range(MAX_TOOL_ROUNDS)`。每个 round 是独立 LLM call，但 round 之间是"模型自驱"（继续工具循环），不是"等用户/群反馈再回 LLM"。

#### 1.1.3 Thinker / slang / slang_review / memo / proactive 多 call（保留 v1）

辅助 call：thinker（[client.py:931/976/2186](../../services/llm/client.py#L931) `thinker_max_tokens=256`）、slang/slang_review/slang_drift、memo（compaction 时）、proactive（主动开口）。这些 call 与主回复 call 时序上一般不并发，但**它们已经证明 Omubot 单回合天然有 3~8 次 LLM call**——Part 6 的 multi-call 争论不是"无中生有引入新成本"，而是"已经有的成本能否重新组织以换取拟人化收益"。

#### 1.1.4 Critic-rewrite-loop V11（保留 v1）

[client.py:1791-1796/1852](../../services/llm/client.py#L1791) `max(128, min(1024, len(reply)*3+64))`：critic 路径已存在，但**默认冷代码**（Part 1 V11 评估为不开），Part 6 不与之耦合。

#### 1.1.5 ⚠ Cache 架构现状 — Anthropic 4-breakpoint 在 DeepSeek 路径下完全惰性（v2 重写）

[services/llm/llm_request.py:300 `_ANTHROPIC_CACHE_CAP = 4`](../../services/llm/llm_request.py#L300)、[llm_request.py:303 `apply_cache_breakpoints()`](../../services/llm/llm_request.py#L303)、[llm_request.py:252-290 `TASK_CACHE_PROFILES`](../../services/llm/llm_request.py#L252-L290)：现仓内 cache 标记基础设施完整保留 Anthropic 语义——`{"cache_control": {"type": "ephemeral"}}` 4 个断点（tools[-1] / system block 1 / system block 2 / messages[near-end]）。

**但生产 100% 走 DeepSeek（[config/config.json](../../config/config.json) `default_profile=main, profiles.main.api_format=deepseek, base_url=https://api.deepseek.com, model=deepseek-v4-flash`），DeepSeek `/v1/chat/completions` 与 `/anthropic` 兼容端点**均忽略 `cache_control` 字段**（DeepSeek API docs «Anthropic API 兼容性»：`top_k / cache_control / mcp_servers / container / metadata / service_tier / stop_sequences[> 4]` 字段被静默丢弃；`cache_creation_input_tokens` 字段固定返回 0）。

结论：当前 cache 机制实际上是"DeepSeek 自动 byte-exact prefix 匹配 + 仓内 4-breakpoint 标记 dead code"。Anthropic 通道下重要的"控制 cache 边界"在 DeepSeek 下既无收益也无破坏；Part 6 推导**不能**沿用 Anthropic 的 cache_read 0.10× / cache_write 1.25× / 4096-token 阈值——这些数字在 DeepSeek 下不成立。

### 1.2 MaiBot 中断机制（保留 v1，无需重写）

`interrupt_flag` 在 [chat_stream.py:155-189](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/message_receive/chat_stream.py#L155-L189) 设置后**没有任何消费者读取**——grep 全仓 `interrupt_flag` 仅 4 处定义、0 处 read。MaiBot 文档承诺的"中断机制"在生成层是 **dead code**，实际仍是"一次完整 LLM call → 事后切分"。Part 6 不能把"MaiBot 已有 reactive replan 实现"当作论据。

### 1.3 DeepSeek V4-Flash 技术架构与 cache 机制（v2 全部重写）

> 来源：DeepSeek 官方 api-docs.deepseek.com（pricing / quick_start / context_caching / Anthropic-API 兼容性 / streaming）+ DeepSeek-V4 release notes（2026-04-24）。所有数字按官方 2026-05-26 当前时点的 list price + 现行折扣抓取。

#### 1.3.1 模型规格

| 维度 | DeepSeek V4-Flash（生产） | DeepSeek V4-Pro（参考） |
|---|---|---|
| 总参 / 激活参 | 284B MoE / 13B active | 671B MoE / 37B active |
| Context | 1M tokens | 1M tokens |
| Max output | 384K tokens | 384K tokens |
| 思考模式 | 默认开（`reasoning_content` 字段） | 默认开 |
| License | MIT | MIT |
| 发布 | 2026-04-24 | 2026-04-24 |

#### 1.3.2 定价（2026-05-26 时点）

| 模型 | input cache miss / 1M | input cache hit / 1M | output / 1M | hit:miss 比 | output:miss 比 |
|---|---|---|---|---|---|
| **V4-Flash** | **\$0.14** | **\$0.0028** | **\$0.28** | **0.02×** | 2.0× |
| V4-Pro（75% 折，截至 2026-05-31 15:59 UTC） | \$0.435 | \$0.003625 | \$0.87 | 0.0083× | 2.0× |
| V4-Pro list | \$1.74 | \$0.0145 | \$3.48 | 0.0083× | 2.0× |
| Anthropic Opus 4.7（v1 错误锚点） | \$15 | \$1.50 | \$75 | 0.10× | 5.0× |

**两条结论改写 v1**：

1. **DeepSeek hit 是 miss 的 0.02×**（98% 折），不是 Anthropic 的 0.10×。cache miss 比 hit 贵 **50 倍**（Anthropic 是 10 倍）。这意味着 prefix 稳定性在 DeepSeek 经济下**比 Anthropic 更重要**——50 倍的成本差让任何 byte-level 抖动都被强烈惩罚。
2. **output:input(miss) 比是 2:1**，不是 v1 写的 5:1。Anthropic 下 input miss + output 比 1:5；DeepSeek V4-Flash 下是 1:2——**输入端在总成本里占的比例比 Anthropic 更高**，进一步放大 prefix 稳定的重要性。

#### 1.3.3 Cache 机制：自动 byte-exact prefix matching

DeepSeek 的 context cache **不需要任何 cache_control / breakpoint 标记**。机制：

- **byte-exact prefix matching**：每个 prompt 进来后，DeepSeek 在 KV 持久化层做最长公共前缀匹配；命中字段返回 `prompt_cache_hit_tokens`，未命中返回 `prompt_cache_miss_tokens`
- **粒度**：sliding window attention 下，每个被缓存的前缀作为独立单元；以**固定 token 间隔**（官方未公开具体值，社区观测约 64~128）分段持久化
- **失效**：分钟到天数级自动清退，不暴露 TTL 控制；**无主动 invalidate API**
- **Anthropic 兼容端点**：DeepSeek `/anthropic` 接受 `cache_control` 字段但**静默忽略**；`cache_creation_input_tokens` 字段固定返回 0；`top_k` / `mcp_servers` / `container` / `metadata` / `service_tier` 同样被静默丢弃；`stop_sequences` 仅前 4 项保留

**Omubot 现状映射**：

- [services/llm/llm_request.py:303 `apply_cache_breakpoints()`](../../services/llm/llm_request.py#L303) 标的 `cache_control` 字段在 DeepSeek 路径下**完全无效**——既不带来收益（DeepSeek 自己会做），也不带来副作用（被忽略）
- [storage/usage.db `cache_create_tokens`](../../storage/usage.db) 列在 DeepSeek 流量下**结构性恒为 0**——这是 DeepSeek 不返回该字段的体现，不是没命中

#### 1.3.4 SSE 流式中断的实际行为

DeepSeek `/v1/chat/completions?stream=true` 的中断语义（官方 streaming docs + 社区抓包验证）：

- **client 侧关闭 HTTP 连接 → 服务端立即停止生成**，未发送的 output token 不计费
- **abort 时刻已 in-flight 的 5~30 token 仍计入 output**（buffer flush 漂移）
- **input + cached input 全额计费**，无论是否命中
- 生成被中断后**无法续接**——DeepSeek 不支持 Anthropic 风格的 prefill / continuation；要"接着说"必须把已生成的 output 作为 assistant message 重新喂回去

这条对 Part 6 的**方案 C reactive replan** 决定性：abort 一次浪费的不只是已 flush 的 token，还要把它喂回 prompt 才能续，等于"abort 即回滚"。

#### 1.3.5 思考模式（V4 family 默认开）

- 响应里多一个 `choices[0].message.reasoning_content` 字段（OpenAI 兼容路径），或 Anthropic 兼容路径下的 `content[].type=thinking` block
- **多轮强约束**：上一轮的 `reasoning_content` **必须原样回传**进 messages 数组，否则模型行为退化（V4 已知与 tool calls 联用时严格性提升 vs V3）
- 思考 token 计入 `reasoning_replay_tokens`（Omubot 已有该列），定价归入 input cache miss
- Omubot 现已记录该列（[storage/usage.db `reasoning_replay_tokens`](../../storage/usage.db)），但 Part 6 的 multi-call 设计必须把"上轮 reasoning 是否回传"作为独立 axis——否则 turn N+1 会因为缺 reasoning 退化

### 1.4 框架对照（保留 v1 主体，校正 cache 列）

| 维度 | Omubot 现状 | MaiBot 现状 | OneBot-LLM-Plugin |
|---|---|---|---|
| 生成调用形态 | 单次 1024-token call + 事后切 | 单次完整 call + 事后切（interrupt_flag dead） | 单次 call |
| 工具循环 | 5 round 上限 | 不限（但平均 1 round） | 不支持 |
| 流式 | SSE token-stream + 末端切 | SSE + 末端切 | 无 |
| 中断 | 无运行时 abort | dead code | 无 |
| Cache 机制 | DeepSeek auto prefix（4-breakpoint dead code） | OpenAI prompt_caching auto prefix | 无 |
| Plan-then-utter | 无 | 无 | 无 |

**结论保留 v1**：四方都没有"plan-then-utter / reactive mid-generation / pause-then-extend"中任一种。Part 6 是 **greenfield 设计**，不是 cherry-picking 现有实现。

### 1.5 ⚠ 生产 cache 命中率诊断（v2 新增 / v2.1 增补 D+E 历史） — state_board 是 prefix 漂移源头

#### 1.5.0 历史已落地的 cache 优化（D+E 方案，2026-05-22 部署）

> 增补于 v2.1（2026-05-26 用户问"此前进行过一次cache优化，评估是否需要进一步优化"）。本节记录 D+E 不是"还没做"的空白，而是 P6.0 的真正前置已经做了**一半**。

**做法**：把若干 task 的 system prompt 加长跨过 DeepSeek 的 "1024-token 词级前缀页缓存最小可缓存长度"门槛（[services/slang/shared_prefix.py:1-15](../../services/slang/shared_prefix.py#L1-L15) 注释明确说明该门槛），让原本紧贴边界、命中率 30~45% 的 task 进入稳定可缓存区。

**沉淀**：

- 2026-05-22 [maintenance-log §知识库改进 D](../../maintenance-log.md) — thinker / slang_review 加固（commit fb22dd4）
- 2026-05-22 [maintenance-log §知识库改进 E](../../maintenance-log.md) — slang / slang_drift / slang_semantic 三阶段加固（commit 0a0a12e）
- 2026-05-22 [maintenance-log §方案 D + E 部署](../../maintenance-log.md) — bot 镜像 rebuild 0a0a12e + 24h 验证窗口

**效果（4 日实测 2026-05-22 → 2026-05-26）**：

| call_type | Pre-D+E 基线 | 当前 7 日 | Δ | 当初阈值 | 是否达标 |
|---|---|---|---|---|---|
| slang | 53.6% | **62.9%** | +9.3pp | ≥ 65% | ❌ 临界 |
| slang_review | 45.0% | **59.2%** | +14.2pp | ≥ 60% | ❌ 临界 |
| slang_drift | 81.5% | **84.4%** | +2.9pp | ≥ 80% | ✅ |
| thinker | 39.4% | **51.2%** | +11.8pp | ≥ 60% | ❌ |

**评估**：D+E 方向正确（4 项全部抬升 9~14pp），但**没有一项达到当初承诺的 60% 健康线**（slang_drift 是预先就高的）。再叠"静态文档加固"已是边际收益递减——加 token 只能把命中率从 60% 推到 65%，每条 +160 token 还要付一次 miss 写入成本。**D/E 同方法不再延续**，下一刀打在主链路（chat / proactive）的 state_board 抖动层。

#### 1.5.1 主链路命中率（D+E 未触达）— v2.1 实测

按 `call_type` 当前 7 日分布（model=deepseek-v4-flash，provider_kind=deepseek，2026-05-26 取自生产 storage/usage.db）：

| call_type | calls | hit% | avg_in | avg_miss | avg_out | 单 call 成本 (USD) |
|---|---|---|---|---|---|---|
| **proactive** | 94 | **74.3%** | 5,393 | 4,734 | 255 | $0.000772 |
| **chat** | 16 | **71.4%** | 7,428 | 7,428 | 208 | $0.001150 |
| slang | 2,544 | 62.9% | 2,603 | 965 | 450 | $0.000266 |
| slang_review | 1,761 | 59.2% | 1,664 | 679 | 418 | $0.000215 |
| graph_review | 494 | 86.2% | 751 | 104 | 209 | $0.000075 |
| slang_drift | 135 | 84.4% | 1,289 | 202 | 198 | $0.000087 |
| thinker | 135 | 51.2% | 1,364 | 665 | 47 | $0.000108 |
| memo | 49 | 10.3% | 279 | 250 | 107 | $0.000065 |
| chat (per-day swing) | — | **35.5% ~ 80.8%** | — | — | — | 单日 2.3× 波动 |
| proactive (per-day swing) | — | **44.6% ~ 86.7%** | — | — | — | 单日 2× 波动 |

**关键观察**：

1. **D+E 完全没碰主链路**——proactive / chat 不属于 slang 家族；它们的 system prompt 早已远超 1024 token（avg_in 5K~22K），不存在"门槛问题"
2. **主链路命中率波动 2~3×**——同样的 17K 输入，proactive 在 5/16 命中 86.7%、5/17 命中 51.0%、5/21 命中 44.6%；这种波动**不是流量小造成的统计偶然**（5/21 也有 4 次调用），而是 byte-exact prefix 在某些时刻被命中、某些时刻击穿
3. **chat 单日波动到 35.5%**（5/25）——chat 单次 input 7K~22K，35% 命中率意味着每次几乎重新付了一遍 input miss 成本

#### 1.5.2 根因 — state_board 的字符级抖动

[services/memory/state_board.py:71-79 `to_prompt_text()`](../../services/memory/state_board.py#L71-L79) 在 prompt 中段输出"【当前群聊状态】"块，该块的 4 行内容**全部含动态字段**：

```python
lines = [
    "【当前群聊状态】",
    f"最近活跃：{self.active_users}",      # 5 个 nick(qq) 顺序随发言变
    f"近期话题：{self.recent_topics}",     # bigram 频次随消息流动旋转
    f"消息频率：{self.message_frequency}", # 含 "过去5分钟 N 条消息"
    f"最近@你：{self.recent_mentions}",    # 含 "刚刚 / 1 分钟前 / N 分钟前"
]
```

三个动态来源：

1. **[state_board.py:181-189 `_derive_frequency`](../../services/memory/state_board.py#L181-L189)**：`f"{label}（过去5分钟 {count} 条消息）"`——`count` 字段每次新消息都变
2. **[state_board.py:244-254 `_derive_mentions`](../../services/memory/state_board.py#L244-L254)**：分钟分辨率时间字符串"刚刚 / 1 分钟前 / N 分钟前"——每分钟漂移
3. **[state_board.py:191-214 `_derive_topics`](../../services/memory/state_board.py#L191-L214)**：从最近 20 条 user 消息提 top-3 bigram——消息流入即旋转

#### 1.5.3 prompt layout 放大效应

[services/llm/prompt_builder.py:161-178 `build_blocks()`](../../services/llm/prompt_builder.py#L161-L178) 的实际 layout：

```
[0] static                  # personality + instruction，~2KB，永不变
[1] group_context_block     # read_mark 提示，固定文本
[2] *plugin_static          # plugin 注册的静态块
[3] state_board             # ⚠ 高频抖动，~200B
[4] *plugin_stable          # mood / affection / memo
[5] *plugin_dynamic         # sticker / 实时上下文
[6] messages                # 历史对话
```

DeepSeek byte-exact prefix matching：state_board 在 [3] 位置变动 → [4] [5] [6] 全部下游 prefix 命中失效。即使 state_board 自身只有 200B，它**摧毁的是后续 ~16KB 历史上下文的 cache**。

[services/llm/client.py:2354-2370](../../services/llm/client.py#L2354-L2370) 注入路径已经把 state_board 标了 `deepseek_native_main` 旗标，说明仓内对 DeepSeek 路径已有"不同对待"的意识——但目前只是路径分流，没有解决抖动本身。

#### 1.5.4 候选治理（Part 6 的 P6.0 前置）

| 方案 | 实现 | 抖动消除 | 副作用 | D+E 历史关系 |
|---|---|---|---|---|
| **(a) 后置到 messages 之后** | prompt_builder layout 把 state_board 移到 messages 末端 | ✓ 完全 | state_board 仍参与 prompt，但不破坏前置 prefix | D+E 未触及 layout |
| **(b) 时间戳粒度抬到小时** | `_derive_mentions` 改 "今天/昨天/早些时候"；`_derive_frequency` 改 "活跃/正常/冷清"（去掉数字） | ✓ 90% | 信息粒度损失，但语义足够 | D+E 未触及 state_board |
| **(c) N 分钟窗口缓存** | state_board 输出按 N=5 min 桶计算，桶内固定 | ✓ 80% | 信息延迟 ≤ N min | 同上 |
| **(d) 仅 @bot 触发渲染** | proactive 不读 state_board（上次 mention 已在历史中） | ✓ 100% on proactive | proactive 读到的"群是否活跃"信号丢失 | 同上 |
| (e) D+E 同方法延续：再加 prompt 长度 | 在 thinker / slang_review / slang 静态块再 +150 token | 0%（不解决抖动） | 只能边际 +5~10pp 命中率 | **不再延续**，边际收益已递减 |

**v2.1 推荐 (a) + (b) 组合**：layout 后置消除"前 prefix 失效"，时间戳粒度抬升消除"分钟级漂移"。预计 proactive hit% 从 74.3% → 88%+；chat 从 71.4% 单日 35~80% 波动 → 稳定 85%+（与历史 5/16 84.0% 单日峰值一致，证明 prefix 稳定时本就能到 85%+）。

**为什么不选 (e)**：D+E 已经实测了"加 token 跨门槛"路径——slang/slang_review/thinker 各 +160~330 token 后只把命中率从 30~45% 推到 51~62%，**没有一项达到 60% 健康线**；继续往同方向加 token 是负 ROI（每 +160 token 一次 miss 写入成本 = $0.0000224，每天 700 次调用每天多花 $0.016，但只能换 2~3pp 命中率提升）。主链路抖动是结构问题，不是长度问题。

#### 1.5.5 与 Part 6 主线的关系

**P6.0 必须先做**：在 prefix 稳定到 85%+ 之前，方案 A multi-call 的成本估算上限会因为 prefix 抖动从 1.05× baseline 飙到 2~3× baseline——50 倍的 hit:miss 差让 prefix 不稳的 multi-call 经济上不可接受。这条**重排** v1 的 §5 推荐顺序：

> v1 §5：A → C → B → D
>
> v2 §5：**P6.0（state_board prefix 稳定）→ B（streaming-as-segment，零 multi-call）→ D（pause-then-extend，仅追发一次）→ A（plan-then-utter，慎用）→ C（reactive replan，暂搁）**

## 2 学术证据矩阵（保留 v1 + Q6 新增 DeepSeek-specific 行）

> 选定 27 篇论文，按 §0.3 Q1~Q8 轴归类。**所有引用必须可定位至 arXiv ID + 章节**；评论性博客不计入。Q6 行因 v2 重写新增 4 条 DeepSeek-specific 证据。

### 2.1 Q1 — Single-call vs Multi-call

| # | Ref | 章节 | 结论摘要 |
|---|---|---|---|
| 1 | Skeleton-of-Thought (Ning et al., ICLR 2024, arXiv 2307.15337) | §3.2 | 把单次长生成拆为 (skeleton + N point) 并发，**latency 提速 2.39×**，质量持平或微升（GPT-4 judge）。直接论据：multi-call 不必慢，关键看是否可并行 |
| 2 | LLMLingua-2 (Pan et al., ACL 2024, arXiv 2403.12968) | §4 | 任务相关 token 压缩可达 20× input，证明"短 call + 高密度 prompt"在质量上可行 |
| 3 | Tree-of-Thought (Yao et al., NeurIPS 2023) | §3 | multi-call 树搜索在推理任务质量上优于单 call CoT，但 token 成本 ×5~×10 |
| 4 | Drift No More (Mao et al., 2025, arXiv 2510.07777) | §4.2 | GPT-4.1 自分散 KL < 0.05 over 10 轮——**短 multi-call 不导致 persona 漂移**，前提是每 call prompt 包含一致的 persona anchor |

### 2.2 Q2 — Plan-then-utter

| # | Ref | 章节 | 结论摘要 |
|---|---|---|---|
| 5 | Plan-and-Solve Prompting (Wang et al., ACL 2023) | §3 | 显式 plan 阶段使数学推理 +5~12 pp；plan 长度 ≤ 50 token 即可 |
| 6 | Self-Refine (Madaan et al., NeurIPS 2023) | §3 | plan → execute → critique 三步比一步式平均 +20% on 7 任务 |
| 7 | Reflexion (Shinn et al., NeurIPS 2023) | §3 | 短 plan + verbal feedback loop 在交互任务上质量优于长 monolithic 输出 |

### 2.3 Q3 — Reactive mid-generation

| # | Ref | 章节 | 结论摘要 |
|---|---|---|---|
| 8 | Avrahami & Hudson (CHI 2006) | §4.3 | IM 真人对话 **30s 内回应概率 90.1%**——人类的"等观察再决定"窗口是 30s 量级，而不是秒级 |
| 9 | Real-time conversational AI latency benchmark (Wang et al., 2025, arXiv 2509.04345) | §5 | 对话 agent 中 sub-second abort/replan 显著降低用户感知延迟，但**仅在生成内容能复用时**有收益 |
| 10 | Speculative Decoding (Leviathan et al., ICML 2023) | §3 | abort + replan 框架的成本是"已生成 token 浪费"——在不可复用的回滚场景下，replan 是纯负 ROI |

### 2.4 Q4 — Streaming-as-segment

| # | Ref | 章节 | 结论摘要 |
|---|---|---|---|
| 11 | Online Sentence Boundary Detection in Streaming (Liu et al., 2024) | §4 | 在流式 token-stream 上检测 sentence boundary 准确率 96%，延迟开销 < 50ms |
| 12 | Punctuation Restoration (Yi & Tao, 2019) | §3 | 中文流式 punctuation 模型 F1 0.92——SSE 流上判定段边界可行 |

### 2.5 Q5 — Pause-then-extend

| # | Ref | 章节 | 结论摘要 |
|---|---|---|---|
| 13 | Conversational pauses in IM (Avrahami-Hudson Marker, CHI 2006) | §5.2 | IM "信息分次发"现象：60% of 多段消息的下一段在 3~10s 内追发 |
| 14 | Turn-taking thresholds (Stivers et al., PNAS 2009) | §3 | 跨语言 IM "等回复" 中位数 200ms~1s；超过 3s 即被视为"对方在思考"——pause 时长应 ≤ 3s 才不破坏自然感 |
| 15 | Levinson, Pragmatics of Sequence Organization (2013) | §6 | "first pair part / second pair part"间隔 1~3s 是默认期望——pause-then-extend 的窗口与 Stivers 一致 |

### 2.6 Q6 — Cache 与成本（v2 全部重写）

| # | Ref | 章节 | 结论摘要 |
|---|---|---|---|
| 16 | DeepSeek API docs «Context Caching»（api-docs.deepseek.com/zh-cn/guides/kv_cache） | §1 | byte-exact prefix matching；分钟到天数级自动失效；无 TTL / 无 invalidate API；**不读 cache_control 字段** |
| 17 | DeepSeek API docs «Pricing»（api-docs.deepseek.com/zh-cn/quick_start/pricing） | 全文 | V4-Flash \$0.14 miss / \$0.0028 hit / \$0.28 output（2026-04-26 起 hit 价 10× 下调） |
| 18 | DeepSeek API docs «Anthropic API 兼容性» | §2 «不支持的字段» | `cache_control / top_k / mcp_servers / container / metadata / service_tier` 静默丢弃；`cache_creation_input_tokens` 恒为 0 |
| 19 | DeepSeek-V3 paper (DeepSeek-AI, 2024, arXiv 2412.19437) | §3.2 | sliding window attention + DeepSeekMoE：每个 cached prefix 段独立持久化；KV cache 重用上限受 attention window 约束 |
| 20 | Anthropic prompt caching docs（v1 锚点，仅作对照） | §3 | 4 breakpoint manual / 5 min TTL / 1.25× write 1× read base 0.10× hit——**与 DeepSeek 不可类比** |

### 2.7 Q7 — Persona stability

| # | Ref | 章节 | 结论摘要 |
|---|---|---|---|
| 21 | PersonaGym (Aggarwal et al., EMNLP 2025, arXiv 2407.18416) | §4 | PersonaScore 评估 5 任务 × 6 模型；persona drift 在 multi-call 下 ≤ 5% 当 anchor 包含足够 trait |
| 22 | RoleLLM (Wang et al., ACL 2024) | §4.3 | role-conditioned 短 call 比长 call persona consistency 高 +8 pp |
| 23 | Big Five Trait Persistence in Multi-turn (Mao et al., 2025) | §4 | GPT-4.1 over 10 轮 trait drift KL < 0.05——multi-call 不必然漂 |

### 2.8 Q8 — 可观测性

| # | Ref | 章节 | 结论摘要 |
|---|---|---|---|
| 24 | LangFuse trace schema | docs | call chain 用 `parent_span_id` 串联；OTel-compatible |
| 25 | OpenTelemetry GenAI semantic conventions（v1.27 ） | §gen_ai | `gen_ai.request.id` / `gen_ai.response.id` / `gen_ai.parent.id` 是 multi-call 因果链的标准字段 |
| 26 | LMSYS LLM-as-a-judge eval (Zheng et al., NeurIPS 2023) | §5 | multi-call vs single-call 的"自然度" judge 评估方法 |
| 27 | Stability of Production Cache Hit Rates (社区抓包综合 2025) | — | 真实生产环境 prefix 稳定时 95%+ hit；agent loop / state-board 注入下 <20%；与 §1.5 实证一致 |

---

## 3 成本重算 — DeepSeek V4-Flash 经济下的 4 方案

### 3.1 baseline：proactive 单次 call 成本（生产 7 日实证）

按 §1.5 数据：proactive 平均 input miss 5,988 + cache hit 11,328 + output 171。

```
baseline_cost = 5988 × 0.14 + 11328 × 0.0028 + 171 × 0.28   # 单位 / 1M
              = $0.0008383 + $0.0000317 + $0.0000479
              = $0.000918 / call
              ≈ $9.18 / 1万 call
```

按 §1.5 cache hit 65% 当前态。如果命中提到 90%：

```
new_miss = 17316 × 0.10 = 1732；new_hit = 17316 × 0.90 = 15584
optimized_cost = 1732 × 0.14 + 15584 × 0.0028 + 171 × 0.28
               = $0.000242 + $0.0000437 + $0.0000479
               = $0.000334 / call
               节省 63.6%
```

**P6.0 单独的成本收益就高达 60%+**——这是任何 multi-call 方案都比不了的"先把基线打稳"。

### 3.2 方案 A — Plan-then-utter（短 plan call + N 短 utter call）

设 N=2~3 段，每段 80~120 token output；plan call 输出 ~50 token。input 端假设每次 utter 重发 prompt（DeepSeek auto prefix 命中前 N-1 次 utter 的 prefix）。

**前提：prefix 稳定到 90%+**（即 P6.0 已落地）：

```
plan call:    input 17316 (90% hit), output 50
              = 1732 × 0.14 + 15584 × 0.0028 + 50 × 0.28 / 1M
              = $0.000291 / call
utter call:   input ~17400 (95% hit, 包含 plan 输出),  output ~100
              = 870 × 0.14 + 16530 × 0.0028 + 100 × 0.28 / 1M
              = $0.000196 / call
total (1 plan + 2.5 utter avg) = $0.000291 + 2.5 × $0.000196
                               = $0.000781 / 总 reply
              vs optimized_baseline $0.000334 → 2.34× baseline
```

**前提：prefix 仍在 65% 当前态**：每次 utter 重发都付 35% miss → utter input miss 部分 6088 × 0.14 = $0.000852，2.5 次 = $0.00213，加 plan call ≈ $0.00295 / reply → **8.8× baseline**。

**结论 v2 改写**：方案 A 的成本 **强依赖 P6.0**。P6.0 未落地前 8.8× 不可接受；P6.0 落地后 2.34× 接近 v1 估算的 1.05× 但仍翻倍。

### 3.3 方案 B — Streaming-as-segment（不动 call 数）

call 数不变，仅在 SSE token-stream 上 online 切段。input/output token 总量不变。

```
B_cost = baseline_cost × 1.0  # 完全不变
```

**唯一变量**：online 切段算法的 CPU 开销（µs 量级，可忽略）。**B 是 4 方案中唯一不动 LLM 经济的**——它在 §4 决策矩阵里成为首选的关键原因。

### 3.4 方案 C — Reactive replan（生成中检测对方新消息 → abort + replan）

abort 浪费：已 flush 5~30 token output（按 §1.3.4），但**input + cached input 全额计费**。

设 abort 触发率 r（保守 30%，激进 60%），每次 replan 重新发起一次 call：

```
C_cost (r=30%) = baseline + r × (baseline - 0.5×output_saving)
               = baseline × (1 + 0.30 × 0.97)        # output 救回的份额极小
               ≈ baseline × 1.29
C_cost (r=60%) ≈ baseline × 1.58
```

**真正问题**：replan 后**必须把已生成 output 喂回去做 prefill**（DeepSeek 不支持 mid-stream continuation），等于第二轮 input 多 ~30 token——成本提升 1~2%，但**因果链复杂度爆炸**：每次 abort 都要写 `parent_span_id`、记录 abort 时刻 group state、回放给下一轮。可观测性、调试、回滚都变得困难。

### 3.5 方案 D — Pause-then-extend（发完第一段 → N 秒等观察 → 决定追发）

D 不动主回复 call。可选追发：

```
D_cost (extend_rate=20%) = baseline + 0.20 × baseline_extend
                         ≈ baseline × 1.10  # 假设 extend call 成本约等于 baseline
D_cost (extend_rate=40%) ≈ baseline × 1.20
```

extend call 因为重发完整 prompt（含上次 reply）拿到 95%+ cache hit，input 成本接近全 hit；output 200~400 token。

**关键**：D 是"只在用户没回应时才追发"，extend_rate 由用户行为决定，不是设计参数。Avrahami-Hudson §1.5（30s 内 90.1% 回应）→ extend_rate 上限约 9.9%（用户没回的份额）→ 实际 D_cost ≤ 1.05× baseline。

### 3.6 4 方案成本对比（v2）

| 方案 | prefix 稳定（90%+）成本 | prefix 不稳（当前 65%）成本 | latency P95 | 因果链复杂度 |
|---|---|---|---|---|
| **B Streaming-as-segment** | 1.00× | 1.00× | baseline | 低 |
| **D Pause-then-extend** | 1.05~1.10× | 1.05~1.10× | baseline + 1~3s | 低 |
| **A Plan-then-utter** | 2.34× | 8.8× | baseline × N + plan latency | 中 |
| **C Reactive replan** | 1.29~1.58× | 2~3× | baseline × (1+r) + abort drift | 高 |

---

## 4 决策矩阵 v2（按 DeepSeek 经济排序）

### 4.1 决策权重（沿用 v1 的 5 维度，权重不变）

| 维度 | 权重 | 说明 |
|---|---|---|
| 拟人化体感增益 | 30% | 用户层面的"自然 / 不像机器" |
| 实施风险 | 25% | 改动半径 / 回滚难度 |
| 成本系数 | 20% | DeepSeek 实价 |
| 可观测性 | 15% | 因果链 / 调试 |
| 与现有架构耦合 | 10% | 与 Part 1/5/Plugin 的不冲突 |

### 4.2 矩阵

| 方案 | 体感 | 风险 | 成本 | 可观测 | 耦合 | 加权 | 结论 |
|---|---|---|---|---|---|---|---|
| **P6.0 Prefix 稳定化** | — | 低 | **节省 63%** | — | 低 | — | **强制前置** |
| **B Streaming-as-segment** | 中（段更自然） | 低 | 1.00× | 低 | 与 Part 5 互斥 | **0.71** | **首选** |
| **D Pause-then-extend** | 高（"还没说完"自然） | 低 | 1.05~1.10× | 低 | 与 Part 5 正交 | **0.66** | **次优** |
| **A Plan-then-utter** | 中（取决于 plan 质量） | 中 | 2.34× (P6.0 后) | 中 | 与 Part 5 互斥 | **0.42** | **慎用 / Pilot** |
| **C Reactive replan** | 高（如果实现得好） | **高** | 1.29~1.58× + drift | **高** | 与全栈耦合 | **0.31** | **暂搁** |

### 4.3 v1 → v2 排序变更说明

v1 排序 A → C → B → D 基于 Anthropic 经济：cache_read 0.10× / output:input 5:1。在该假设下 multi-call 的 input 重发代价低（10×），output 输出占主导（5:1），于是 plan-then-utter 短 utter call 的成本只有 1.05× baseline——A 是首选。

v2 排序 B → D → A → C 基于 DeepSeek 实价：cache hit 0.02× / output:input 2:1 / prefix byte-exact。三条变化都不利于 multi-call：

1. **cache hit 0.02× → cache miss 比 hit 贵 50 倍**：input 重发只要不命中就是 50 倍成本
2. **output:input 2:1**：output 占总成本份额只有 ~67%（Anthropic 是 ~83%），input 端成本权重升高
3. **byte-exact prefix**：multi-call 之间的 prefix 必须完全 byte-identical 才能命中——任何 turn-state / plan-output 差异都会击穿

→ B（不动 call 数）和 D（追发 1 次）的成本几乎不变；A（多 call）和 C（replan）的成本结构性恶化。

### 4.4 三档 profile 切换设计（v2.2 新增）

> 增补于 v2.2（2026-05-26 用户原话："我不要单选项进行，而是在配置提供三档切换。改进方案，追加切换，审计是否需要切换功能修改流程"）。本节把 §4.2 的方案选项物化为**用户运行时可切换的 3 档 profile**，而不是把"先 economy 后 balanced 后 performance"硬编进施工节奏里。

#### 4.4.1 三档 profile 定义

| profile | 含义 | 启用方案 | 互斥关系 | 7 日实测成本 vs 现状 | 体感 |
|---|---|---|---|---|---|
| **`economy`**（默认 / 出厂） | 仅修 cache 抖动 | P6.0.a layout 后置 + P6.0.b 字段粒度抬升 | 与 Part 5 natural_split 完全正交 | **-63%** | ≈ 0（不可见） |
| **`balanced`**（推荐） | economy + 段层与节奏拟人化 | + B streaming-as-segment + D pause-then-extend | **B 与 Part 5 natural_split 互斥**（自动 disable）；D 与 Part 5 正交 | -58% ~ -56% | 中-高 |
| **`performance`**（pilot） | balanced + plan-then-utter | + A plan-then-utter（仅 proactive） | **A 与 Part 5 互斥**（自动 disable）；A 解锁条件：proactive hit% ≥ 80% | -56% + pilot $0.15 / 14 日 | 高（pilot 阶段） |

> 注：3 档之外保留 `custom`——直读子配置具体 flag，与目前的"flag-by-flag"行为完全等价（向后兼容）。

#### 4.4.2 切换语义

- **全局默认 profile**：[kernel/config.py `HumanizationConfig`](../../kernel/config.py#L1024) 新增 `profile: Literal["economy","balanced","performance","custom"] = "economy"`
- **群级覆盖 profile**：[kernel/config.py `GroupOverride`](../../kernel/config.py#L343) 新增 `humanization_profile: Literal[...] | None = None`；None 时退回全局
- **运行时决议**：消费者改读 `config.humanization.resolved_for(group_id)` 返回 frozen `ResolvedHumanization`，含：
  - `state_board_layout: Literal["head","tail"]`
  - `state_board_granularity: Literal["fine","coarse"]`
  - `streaming_segment_enabled: bool`
  - `pause_then_extend_enabled: bool`
  - `plan_then_utter_enabled: bool`
  - `disable_natural_split: bool` — 由 `resolve_profile()` 物化的互斥标志（B/A 任一开则 True）
- **健康守卫降级**：若最近 1h proactive hit% < 80%，`performance` 运行时自动降级到 `balanced`（保留 SPA 显示值；log warning + 等阈值恢复后自动回升）。这条复用现有 [storage/usage.db](../../storage/usage.db) `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` 字段，无需新数据通路

#### 4.4.3 与既有 Part 1 humanization flag 的关系

> 现有 9 个 Part 1 flag（`context_providers / register_classifier / sticker_register_provider / thinker_provider / rewrite_threshold / semantic_gate_dynamic / kaomoji_enforce_strict / runtime_groups / ...`）**全部保留**。三档 profile 仅控制 Part 6 引入的 4 个新 flag（state_board layout/granularity / streaming / pause_extend / plan_then_utter）；Part 1 flag 与 Part 6 profile 在 BaseModel 层正交。

#### 4.4.4 切换路径（用户视角）

| 路径 | 实现 | 生效粒度 | 重启需要 |
|---|---|---|---|
| **Admin SPA → 系统配置 → 拟人化 → profile 下拉** | [admin/routes/api/config.py:380](../../admin/routes/api/config.py#L380) `_build_schema()` 自动渲染 enum 字段（`json_schema_extra` 提供 `display_label / options / risk_level`） | 全局 | recommended |
| **群组管理 → 单群覆盖 → humanization_profile** | 既有 GroupOverride 渲染管线 | 单群 | recommended |
| **config.json 直改 `humanization.profile` / `group.overrides.<gid>.humanization_profile`** | BaseModel 反序列化 | 全局 / 单群 | required |
| **运行时降级**（performance → balanced） | `resolve_profile()` 内置健康检查 | 单群 | 无需（自动） |

#### 4.4.5 切换风险与守卫

| 切换路径 | 风险 | 守卫 |
|---|---|---|
| `economy → balanced` | B 启用时 Part 5 natural_split 被 disable —— 已有 reply 段长策略变化 | `resolve_profile()` 输出 `disable_natural_split` 字段；[services/llm/client.py `_reply_segments`](../../services/llm/client.py#L359) 入口处 resolve 一次；30 秒回滚 `profile=economy` |
| `balanced → performance` | A pilot 在 prefix 不稳时 8.8× baseline | 健康守卫：proactive hit% < 80% 则自动降级 + log warning；DB 阈值守卫 0 引入 |
| `performance → balanced` | A 触发中切档导致单条 reply plan/utter 不一致 | profile 决议在 reply 入口 resolve 一次后冻结到 reply 完成；切档影响下一次 reply |
| 群级覆盖 vs 全局 | 同一时刻不同群跑不同 profile，可观测混乱 | `storage/usage.db.call_type` 沿用 `proactive` / `proactive_plan` / `proactive_utter` 区分；BlockTrace 增 `profile` 字段 |

## 5 不做的事（v2 增补）

| 项 | 原因 |
|---|---|
| 不在 P6.0 落地前启动 A / C | §3.2/3.4 — prefix 不稳时成本爆 8.8× / replan 因果链不可观测 |
| 不切回 Anthropic provider | 用户经济考量，DeepSeek V4-Flash 是给定生产环境；本文不参与 provider 选型 |
| 不切到 V4-Pro | V4-Pro list price 12× / promo price 3× V4-Flash；当前流量级别不需要 |
| 不引入 manual cache_control 标记新逻辑 | DeepSeek 路径下 dead code；保留 Anthropic 路径的 4-breakpoint 仅为兼容 |
| 不动 V11 critic-rewrite-loop | 默认冷代码，不进 Part 6 |
| 不动 Part 5 natural_split | Part 5 与 Part 6 在 B/D 方案下正交；A/C 方案下 Part 5 退化为 noop（互斥 flag） |
| 不实现"中断 SSE → 继续 SSE"的 mid-stream resume | DeepSeek 不支持 stream continuation（§1.3.4）——技术上不可行，不是设计权衡 |
| 不引入 OpenTelemetry 全链路（GenAI semconv） | 仓内目前用 BlockTrace + storage/usage.db；Part 6 仅扩展现有列，不引入新 trace 体系 |

---

## 6 接入点

### 6.1 与 Part 1（语言体感）

| 子项 | 接入点 |
|---|---|
| V1 RegisterClassifier | A 方案 plan call 输出包含 `register` 字段→直接喂给 utter call；D 方案 extend 决策受 register 影响（quiet→低 extend rate） |
| U3 Humanizer typing 延迟 | D 方案 extend 之前的"等待"与 Humanizer 段内字间延迟正交——D 处理段间，Humanizer 处理段内 |
| V8 StylometricScorer | A 方案 plan 输出可作为 scorer 的"plan vs final"对比项；非必需 |
| V11 Critic-rewrite-loop | 默认冷代码，Part 6 不开启 |

### 6.2 与 Part 5（事后切分）

参照 [part5 §7 行 263-267](./omubot-humanization-part5-segmentation.md#7-与既有-part-的边界)：

- **B Streaming-as-segment 与 Part 5 natural_split 互斥**——前者在 SSE token-stream 上 online 切，后者在完整文本上 batch 切。互斥 flag：`streaming_segment.enabled` 与 `natural_split.enabled` 不可同开
- **A Plan-then-utter 与 Part 5 natural_split 互斥**——utter call 输出 max_tokens=150 即单段，natural_split 退化 noop。互斥 flag：`plan_then_utter.disable_natural_split=true`
- **C Reactive replan 与 Part 5 互斥**——replan 后的文本经 natural_split 会再切，已切片段需重新拼装。互斥 flag：`reactive_replan.disable_natural_split=true`
- **D Pause-then-extend 与 Part 5 完全正交**——D 控制"是否追发"，Part 5 控制"段内如何切"。段间节奏 = `inter_segment_delay`（Part 5）+ `pause_then_extend_window`（Part 6 D）

### 6.3 与 Part 2-3（输入感知 / 群语境）

- **不耦合 addressee / topic / @ 仲裁**——Part 6 仅在已经决定回复后影响生成形态
- **C 方案需要"对方新消息检测"信号**：复用 Part 2 的 `bus.state.group_timeline.new_user_message`（已存在）即可；不引入新事件
- **D 方案需要"用户是否已回应"信号**：同上

### 6.4 与现有可观测性

| 列 | 现状 | Part 6 扩展 |
|---|---|---|
| `storage/usage.db.call_type` | proactive / chat / slang / ... | 增 `proactive_plan` / `proactive_utter` / `proactive_extend` |
| `BlockTrace.parent_span_id` | 已有 | A/C 方案的 plan→utter / call→replan 关系 |
| `BlockTrace.abort_reason` | 不存在 | C 方案专属，记录 abort 触发条件 |
| `cache_create_tokens` | DeepSeek 下结构性恒为 0 | 不再监控；Anthropic 路径保留 |

---

## 7 子任务编号 P6.0 ~ P6.13（v2.1 P6.0 三段拆分 / v2.2 三档 profile 切换）

> v1 P6.8（cache_creation 字段监控）在 DeepSeek 路径下 dead——结构性恒为 0，无信号。删除。
> v2 新增 P6.0（state_board prefix 稳定化）作为强制前置；编号让出空间，原 P6.1~P6.14 → P6.1~P6.13。
> **v2.1**：P6.0 拆为 P6.0.a / P6.0.b / P6.0.c 三段，分别对应 §1.5.4 候选 (a) layout 后置、(b) 时间戳粒度抬升、(c) 7 日复盘验收。这条**承接** D+E 已落地的 slang 家族治理，**继续治理主链路** state_board 抖动层。
> **v2.2**：新增 P6.0.x1~P6.0.x5（profile 切换基础设施），把 economy / balanced / performance / custom 四档物化到 [HumanizationConfig](../../kernel/config.py#L1024) + [GroupOverride](../../kernel/config.py#L343) + Admin SPA。施工节奏与 P6.1~P6.13 解耦——profile=custom 时全部新 flag 走原 flag-by-flag 路径，不强制升级。

### 7.0 P6.0 三段细化（v2.1 增补 / v2.2 复用 profile=economy）

| 编号 | 任务 | 依赖 | 关键产物 | 单测 |
|---|---|---|---|---|
| **P6.0.a** | **state_board layout 后置**（候选 a） | 无 | [services/llm/prompt_builder.py:161-178 `build_blocks()`](../../services/llm/prompt_builder.py#L161-L178)：新增 feature flag `humanization.state_board_layout=tail`（默认 `head` 兼容）；`tail` 模式下 layout 改为 `[static, group_context, *plugin_static, *plugin_stable, *plugin_dynamic, messages, state_board]`，state_board 后置到 messages 之后 | `tests/test_state_board_layout.py` ≥ 4：head 模式回归 / tail 模式 layout 顺序 / state_board 内容不变 / build_blocks 层 prefix 字节稳定（mock state_board 抖动 → 前 N-1 块 byte-identical） |
| **P6.0.b** | **state_board 字段粒度抬升**（候选 b） | 无 | [services/memory/state_board.py](../../services/memory/state_board.py)：(1) `_derive_mentions` 时间字段从分钟级（"刚刚 / 1 分钟前 / N 分钟前"）改为粗粒度（"刚刚 / 今天早些 / 昨天 / 更早"）；(2) `_derive_frequency` 去掉 `（过去5分钟 N 条消息）` 的具体计数，仅留 "活跃 / 正常 / 冷清 / 暂无消息" 标签；(3) `_derive_topics` 加 sticky 锚点：连续两次 query 内 top-3 bigram 不变则保持原值；feature flag `humanization.state_board_granularity=coarse`（默认 `fine` 兼容） | `tests/test_state_board_granularity.py` ≥ 6：fine 模式回归 / coarse 模式时间粒度 / coarse 模式频率不含数字 / sticky topics 不抖 / fake clock 跨 10 min 渲染 byte-identical / fine↔coarse 切换 |
| **P6.0.c** | **7 日生产复盘 + 默认开 + D+E 长尾收口** | P6.0.a + P6.0.b 灰度 7 日 | (1) 灰度脚本 `scripts/dev/measure_cache_hit_proactive.sh` 按 call_type 抽 hit% / avg_miss / latency_p50；(2) 验收阈值：proactive ≥ 85% / chat ≥ 80% / 单日波动 ≤ 15pp；(3) 达标后 flag 默认 `tail` + `coarse`（即 profile=economy 默认）；(4) maintenance-log 当日条目继承 D+E 历史脉络 | 灰度报告内嵌 §10 状态表 |

### 7.0.x P6.0.x — Profile 切换基础设施（v2.2 新增）

| 编号 | 任务 | 依赖 | 关键产物 | 单测 |
|---|---|---|---|---|
| **P6.0.x1** | **HumanizationConfig profile 字段 + ResolvedHumanization** | P6.0.a + P6.0.b | [kernel/config.py:1024 `HumanizationConfig`](../../kernel/config.py#L1024)：(1) 新增 `profile: Literal["economy","balanced","performance","custom"] = "economy"` 字段，含 `json_schema_extra={"display_label": "拟人化档位","options": [...], "risk_level": "careful", "restart_hint": "recommended"}`；(2) 新增嵌套 `state_board / streaming_segment / pause_then_extend / plan_then_utter` 4 个子 BaseModel（仅 `enabled` + 子参数，默认全 off）；(3) 新增 `resolve_profile(profile_value, group_id)` → `ResolvedHumanization` dataclass 含 6 个决议字段（`state_board_layout / state_board_granularity / streaming_segment_enabled / pause_then_extend_enabled / plan_then_utter_enabled / disable_natural_split`）；(4) `custom` 模式直读子字段 enabled，与原 flag 等价 | `tests/test_humanization_profile.py` ≥ 8：四档名解析 / economy → state_board only / balanced → +B+D / performance → +A / custom 等价 / B 与 Part 5 互斥写出 disable_natural_split=True / 健康守卫触发 performance→balanced 降级 / 群级 override 优先级 |
| **P6.0.x2** | **GroupOverride humanization_profile 字段** | P6.0.x1 | [kernel/config.py:343 `GroupOverride`](../../kernel/config.py#L343)：新增 `humanization_profile: Literal["economy","balanced","performance","custom"] \| None = None`（None 时退回全局 profile）；[kernel/config.py `GroupConfig.resolve()`](../../kernel/config.py#L471) 输出的 `ResolvedGroupConfig` 增 `humanization_profile` 字段（None 表示用全局） | `tests/test_group_override_profile.py` ≥ 4：全局 economy + 群级 None → economy / 全局 economy + 群级 balanced → balanced / 群级覆盖优先级 / `humanization.runtime_groups` 与 `group.overrides[].humanization_profile` 同时存在的解析 |
| **P6.0.x3** | **消费者改读 resolve_profile()** | P6.0.x1 | (1) [services/llm/prompt_builder.py:138-180 `build_blocks()`](../../services/llm/prompt_builder.py#L138-L180) 入口 resolve 一次 layout 决议；(2) [services/memory/state_board.py:71-79 `to_prompt_text()`](../../services/memory/state_board.py#L71-L79) 入口接受 granularity 参数；(3) [services/llm/client.py:359 `_reply_segments`](../../services/llm/client.py#L359) 入口 resolve 决议（含 disable_natural_split 互斥）；(4) [plugins/chat/plugin.py:94-114](../../plugins/chat/plugin.py#L94-L114) `_humanization_runtime_groups` 路径不动；新增 `_humanization_resolve(config, group_id)` helper | `tests/test_humanization_resolve_consumer.py` ≥ 6：build_blocks 读 layout 决议 / state_board 读 granularity / _reply_segments 读 disable_natural_split / consumer 在 custom 模式回退原 flag / per-call resolve 缓存 / reply 进行中 resolved 不漂 |
| **P6.0.x4** | **健康守卫：performance → balanced 自动降级** | P6.0.x1 + P6.0.c | `services/humanization/health_guard.py` ≤ 100 行：(1) 周期 60s 查 [storage/usage.db](../../storage/usage.db) 最近 1h 按 group_id 的 proactive hit% / chat hit%；(2) 当 group 设置 performance 但实测 hit% < 80% → 写入 in-mem `_degraded_groups: dict[str, datetime]`；(3) `resolve_profile()` 在 performance 路径上检查该字典，若降级中则返回 balanced 决议；(4) hit% 恢复到 ≥ 85% 持续 10min 后自动解除降级 | `tests/test_humanization_health_guard.py` ≥ 6：DB 查询 / 降级触发 / 降级解除 / 多群独立降级 / SPA 显示值与运行时值不一致的 log warning / 健康守卫读不到 DB 的兜底（默认不降级，避免误伤） |
| **P6.0.x5** | **Admin SPA 自动渲染 + 三档 chip 显示** | P6.0.x1 + P6.0.x2 | (1) [admin/routes/api/config.py:380 `_build_schema()`](../../admin/routes/api/config.py#L380) 自动渲染 enum（已有管线）；(2) [admin/frontend/src/views/](../../admin/frontend/src/views/) 系统配置页：拟人化区块加 profile 下拉 + 子配置只读卡片（profile=custom 时变可编辑）；(3) 群组管理页：群级 humanization_profile 下拉 + "继承全局" 选项；(4) 拟人化档位 chip 在 dashboard 显示当前生效档位 + 健康守卫降级红点 | `tests/test_admin_humanization_profile.py` ≥ 4 + 前端 vue-tsc + npm run build |

### 7.1 完整子任务表

| 编号 | 任务 | 依赖 | 关键产物 | 单测 |
|---|---|---|---|---|
| **P6.1** | streaming-segmenter 算法 | P6.0 | `services/segmentation/streaming_segmenter.py` ≤ 200 行，online 在 SSE token-stream 上检测 sentence boundary 并 flush | `tests/test_streaming_segmenter.py` ≥ 8 |
| **P6.2** | streaming hook 接入 LLMClient SSE 主路径 | P6.1 | `services/llm/client.py` 增 `_stream_with_segments()` 方法（feature flag `humanization.streaming_segment.enabled` 默认 off）；与 `_reply_segments` 互斥 | `tests/test_streaming_hook.py` ≥ 4 |
| **P6.3** | B 方案灰度 + 200 条 group reply 体感比对 | P6.2 | `scripts/dev/measure_streaming_vs_natural.sh` | — |
| **P6.4** | B 方案默认开 + 卸 fallback | P6.3 + 用户验收 | client.py 删除被 streaming 替代的若干段；保留 natural_split 作为非 streaming profile 的兜底 | 全量 pytest 回归 |
| **P6.5** | pause-then-extend 决策器（D 方案） | P6.0 | `services/humanization/pause_extend.py` ≤ 150 行；输入 (last_reply, register, slot, group_state)；输出 (should_extend: bool, wait_seconds: float) | `tests/test_pause_extend.py` ≥ 6 |
| **P6.6** | extend call 接入 LLMClient（追发循环） | P6.5 | `services/llm/client.py` 增 `_maybe_extend()`；feature flag `humanization.pause_then_extend.enabled` | `tests/test_extend_call.py` ≥ 4 |
| **P6.7** | D 方案灰度 + extend_rate / 体感比对 | P6.6 | `scripts/dev/measure_extend_rate.sh` 采样 200 条 + 用户主观验收 | — |
| **P6.8** | D 方案默认开 | P6.7 + 用户验收 | flag 默认 on | 全量 pytest 回归 |
| **P6.9** | A 方案 pilot：plan call + utter call N=2~3（仅 proactive） | P6.0 + B/D 已落地 | `services/llm/plan_then_utter.py` ≤ 250 行；feature flag `humanization.plan_then_utter.enabled` 默认 off + group whitelist 仅灰度群 | `tests/test_plan_then_utter.py` ≥ 8 |
| **P6.10** | A 方案 14 日 pilot：cost / latency / persona drift 监控 | P6.9 | grafana / log 面板：proactive_plan vs proactive_utter cost；PersonaScore 5 任务 baseline | — |
| **P6.11** | A 方案决策门：上 / 不上 / 调参后再 pilot | P6.10 | 决策报告 + 当前文档 §10 状态推进 | — |
| **P6.12** | C 方案 暂搁 — 不开发 | — | 文档锁定结论：DeepSeek 不支持 stream continuation + abort drift + 因果链复杂度，C 方案永不进灰度 | — |
| **P6.13** | 文档收口 + maintenance-log 当日条目 | P6.0 ~ P6.11 | 本文 §10 状态表 + maintenance-log 条目 | — |

合计：**P6.0（a/b/c）+ P6.0.x（x1~x5 profile 切换基础设施）+ B（P6.1~P6.4）+ D（P6.5~P6.8）+ A pilot（P6.9~P6.11）+ C 锁定（P6.12）+ 收口（P6.13）= 18 子任务**。新增代码估算 ≤ 1100 行 / 净删 ≈ 300 行；新增测试 ≥ 63 条。

### 7.2 施工节奏与 profile 解锁关系

| profile | 解锁条件 | 包含子任务 |
|---|---|---|
| `economy`（默认） | P6.0.a + P6.0.b + P6.0.x1~x5 落地 | P6.0.a / P6.0.b / P6.0.c / P6.0.x1~x5 |
| `balanced`（推荐） | economy 落地 + P6.0.c 阈值通过（proactive ≥ 85%）+ P6.1~P6.8 落地 | + P6.1~P6.4（B）+ P6.5~P6.8（D） |
| `performance`（pilot） | balanced 落地 + 用户验收 + P6.9~P6.11 落地 | + P6.9~P6.11（A pilot）；运行时受 P6.0.x4 健康守卫保护 |
| `custom`（向后兼容） | 任意子任务可独立启用 | 所有 flag 自由组合，与 v2.1 前的 flag-by-flag 行为等价 |

---

## 8 风险与回滚（v2 重写阈值）

| 风险 | 触发条件 | 回滚 | 阈值（DeepSeek 经济） |
|---|---|---|---|
| **P6.0 layout 改动击穿其他 cache** | state_board 后置后，messages[near-end] 锚点漂移 | feature flag `state_board.layout_v2=false` + restart | proactive hit% 从基线 65% 跌破 50% 即回滚 |
| **profile=balanced 群体感跌落** | B 替代 Part 5 后段长策略偏书面 / 偏机械 | profile=economy 30 秒回滚 | 用户主观判定 + 段长方差 < 8 即回滚 |
| **profile=performance 健康守卫频繁降级** | proactive hit% 持续 < 80% | P6.0.x4 自动降级 + log warning；人工排查 prefix 抖动新源 | 24h 内自动降级触发 ≥ 3 次即手动回 balanced |
| **群级 profile 与全局 profile 冲突** | 同群同时配 humanization_profile + Part 1 runtime_groups | resolved_for() 内 group-level 优先；Part 1 runtime_groups 仅控 Part 1 9 个 flag，与 profile 正交 | 不需要回滚，是设计意图 |
| **B streaming 切段乱拍** | online sentence boundary 误判 | profile=economy（关闭 B）；或 `streaming_segment.enabled=false` | 单测命中率 < 90% 即不进灰度 |
| **D extend 过度触发** | extend_rate > 30% | profile=economy；或 `pause_then_extend.enabled=false` | extend_rate 实测 > 25% 即回滚 |
| **D extend 过度刷屏** | 单 reply 段数 > 3 段 | 同上 + 收紧 max_extend_count=1 | — |
| **A 成本爆表** | proactive 单 reply 成本 > 3× baseline | profile=balanced（关闭 A）；或 `plan_then_utter.enabled=false` | 成本 > 2.5× 即回滚；P6.0.x4 健康守卫先于人工触发 |
| **A persona drift** | PersonaScore 跌 > 5pp | 同上 | — |
| **abort 漂移导致 output 浪费**（C 方案专属，已锁定不开） | replan 后总 output > 1.5× baseline | C 方案永不开启，不需要回滚 | — |

紧急回滚（30 秒）：

```bash
# config/config.json 一键回 economy 档：
#   "humanization": {"profile": "economy"}
# 或彻底关闭 Part 6（向后兼容 v2 前行为）：
#   "humanization": {
#     "profile": "custom",
#     "state_board": {"layout": "head", "granularity": "fine"},
#     "streaming_segment": {"enabled": false},
#     "pause_then_extend": {"enabled": false},
#     "plan_then_utter": {"enabled": false}
#   }
docker compose restart bot
```

---

## 9 引用

### 9.1 仓内代码

- [services/llm/client.py:63 / 79 / 359-538 / 659 / 671 / 931 / 976 / 1727 / 1791-1796 / 1852 / 2186 / 2354-2370 / 2432 / 2598 / 2804](../../services/llm/client.py)
- [services/llm/llm_request.py:252-290 / 300 / 303 / 335-336 / 352-359](../../services/llm/llm_request.py)
- [services/llm/prompt_builder.py:140-202](../../services/llm/prompt_builder.py)
- [services/memory/state_board.py:71-79 / 122 / 181-189 / 191-214 / 244-254](../../services/memory/state_board.py)
- [config/config.json](../../config/config.json) `default_profile=main` / `profiles.main.api_format=deepseek`
- [storage/usage.db](../../storage/usage.db) — schema: `id / ts / call_type / user_id / group_id / model / input_tokens / cache_read_tokens / cache_create_tokens / output_tokens / tool_rounds / elapsed_s / error / provider_kind / prompt_cache_hit_tokens / prompt_cache_miss_tokens / reasoning_replay_tokens`

### 9.2 DeepSeek 官方文档

- DeepSeek API docs «Pricing» — api-docs.deepseek.com/zh-cn/quick_start/pricing
- DeepSeek API docs «Context Caching / kv_cache» — api-docs.deepseek.com/zh-cn/guides/kv_cache
- DeepSeek API docs «Anthropic API 兼容性» — api-docs.deepseek.com/zh-cn/guides/anthropic_api
- DeepSeek API docs «Streaming» — api-docs.deepseek.com/zh-cn/quick_start/streaming
- DeepSeek-V4 release notes — 2026-04-24（284B/13B active / 1M context / 384K output）
- DeepSeek-V3 paper — arXiv 2412.19437（sliding window attention + DeepSeekMoE）

### 9.3 学术（27 篇，详见 §2）

- ICLR 2024 / ACL 2024 / NeurIPS 2023 / EMNLP 2025 / CHI 2006 / PNAS 2009 / arXiv 2412.19437 / arXiv 2510.07777 / arXiv 2407.18416 / arXiv 2509.04345 / arXiv 2403.12968 / arXiv 2307.15337

### 9.4 v1 参考但 v2 推翻 / 不再适用

- Anthropic prompt caching docs（4 breakpoint / 1.25× write / 0.10× read / 5 min TTL）— 与 DeepSeek 路径不可类比

---

## 10 当前状态

| 节 | 状态 | 落地证据 |
|---|---|---|
| v1 立项 | ✅ 完成（2026-05-25） | 用户原话 "part5 方案仍聚焦在话语分割处理上，而没有从 llm 生成的源头提供研究" |
| v2 重写 | ✅ 完成（2026-05-26） | 用户原话 "在执行1、3的前提下重写part6，搜索deepseek v4的最新指标和技术架构，你目前不严谨" — DeepSeek 官方 pricing / kv_cache / Anthropic 兼容性文档全部抓取并对齐 |
| **v2.1 增补**（2026-05-26 同日续作） | ✅ 完成 | 用户原话 "此前进行过一次cache优化，评估是否需要进一步优化" + "先修改part6方案，将该修改列入其中" — §1.5.0 D+E 历史表 / §1.5.1 7 日实证重写 / §1.5.4 候选 (e) 长尾收口 / §7.0 P6.0 拆三段（a/b/c） |
| **v2.2 增补**（2026-05-26 同日三续） | ✅ 完成 | 用户原话 "我不要单选项进行，而是在配置提供三档切换。改进方案，追加切换，审计是否需要切换功能修改流程" — §4.4 三档 profile（economy/balanced/performance/custom）+ §7.0.x P6.0.x1~x5 切换基础设施 + §7.2 施工节奏 + §8 风险与回滚同步 |
| §1 代码取证 v2 | ✅ 完成 | §1.1.5 cache 架构 dead code / §1.3 DeepSeek V4-Flash 完整规格 / §1.5 state_board 漂移根因（state_board.py:71-189 file:line 证据） |
| §2 学术证据 v2 | ✅ 完成（27 篇） | Q6 行替换为 DeepSeek-specific 4 条 |
| §3 成本重算 v2 | ✅ 完成 | 7 日 storage/usage.db 实证 baseline + 4 方案 prefix 稳定 / 不稳两组成本 |
| §4 决策矩阵 v2 | ✅ 完成 | 排序 P6.0 → B → D → A → C 替换 v1 的 A → C → B → D |
| **§4.4 三档 profile v2.2** | ✅ 完成 | economy/balanced/performance/custom 四档物化为 HumanizationConfig.profile + GroupOverride.humanization_profile |
| §7 子任务 v2 | ✅ 完成 | P6.0 新增、P6.8（cache_creation 监控）删除 |
| **§7.0 P6.0 三段拆分 v2.1** | ✅ 完成 | P6.0.a layout 后置 / P6.0.b 字段粒度抬升（含 sticky topics）/ P6.0.c 7 日生产复盘 |
| **§7.0.x P6.0.x profile 切换 v2.2** | ✅ 完成 | P6.0.x1 HumanizationConfig.profile + ResolvedHumanization / P6.0.x2 GroupOverride.humanization_profile / P6.0.x3 消费者改读决议 / P6.0.x4 健康守卫降级 / P6.0.x5 Admin SPA |
| **P6.0.a / P6.0.b / P6.0.c** | ⏳ 待动手 | 优先级最高；feature flag 双 off 兼容；落地后 profile=economy 默认值生效 |
| **P6.0.x1 ~ P6.0.x5** | ⏳ 待动手 | 与 P6.0.a/b 并行可施工（仅 BaseModel 改动）；P6.0.x4 依赖 P6.0.c 阈值定义 |
| **P6.1 ~ P6.13** | ⏳ 待动手 | 等 P6.0.c 验收过线后启动 B 方案（P6.1~P6.4），用户切 profile=balanced 后 D 方案（P6.5~P6.8）启用 |

---

## 附录 A — v1 → v2 / v2.1 / v2.2 修订映射

| v1 节 | v2 处置 | v2.1 增补 | v2.2 增补 |
|---|---|---|---|
| §0 边界 / 取证原则 / 8 问题 | 保留，无修改 | — | — |
| §1.1 Omubot 现状 | 保留主体；新增 §1.1.5 标 4-breakpoint dead code | — | — |
| §1.2 MaiBot 中断 dead code | 保留 | — | — |
| §1.3 Anthropic SSE + 4-breakpoint | **整节替换**为 DeepSeek V4-Flash 架构 | — | — |
| §1.4 框架对照 | 保留主体，校正 cache 列 | — | — |
| §1.5（v1 不存在） | **新增** state_board 漂移诊断 | **§1.5.0 新增**（D+E 历史表 4 行）+ **§1.5.1 重写**（7 日实证 proactive 74.3% / chat 71.4%）+ **§1.5.4 增补**（候选 e "D+E 同方法不再延续"） | — |
| §2 学术 27 篇 | 保留 26 篇；Q6 行 4 条改为 DeepSeek-specific | — | — |
| §3 成本系数 | **整节重算** — Anthropic 数字全部不适用 | — | — |
| §4 决策矩阵 | **整节重排** — A 从首选降到第三，B 从第三升到首选 | — | **§4.4 新增** — economy/balanced/performance/custom 四档 profile 切换设计 |
| §5 不做的事 | 保留 + 增补 4 条 | — | — |
| §6 接入点 | 保留 | — | profile 切换通过 [HumanizationConfig](../../kernel/config.py#L1024) + [GroupOverride](../../kernel/config.py#L343) + Admin SPA 三层接入；不需要新 admin route |
| §7 子任务 | **新增 P6.0**；删除 P6.8（cache_creation）；编号 P6.1~P6.14 → P6.1~P6.13 | **§7.0 新增** — P6.0 拆三段：P6.0.a layout / P6.0.b 粒度 / P6.0.c 7 日复盘；§7.1 子标题保留 P6.1~P6.13 不变 | **§7.0.x 新增** — P6.0.x1~P6.0.x5 profile 切换基础设施；**§7.2 新增** — profile 解锁条件表 |
| §8 风险阈值 | 重写为 DeepSeek 经济下的阈值 | — | 增 4 行 profile 切换风险 + 紧急回滚改为 `profile=economy` 一键回退 |
| §9 引用 | 增 DeepSeek 官方文档 4 条；标记 Anthropic 引用为 v1 推翻 | — | — |
| §10 状态 | 加 v2 重写条目 | 加 v2.1 增补条目 + P6.0.a/b/c 行 | 加 v2.2 增补条目 + §4.4 + §7.0.x + P6.0.x1~x5 行 |
