# 灰度十七问题——解决方案候选与初步审计（待用户定夺）

> 配套文档：[omubot-grayscale-issues-2026-05-26.md](omubot-grayscale-issues-2026-05-26.md)（问题诊断 + 调研引用）
>
> 本文不下结论、不动代码。每个问题列**多套候选解**，按"优 → 劣"排列；每套解给出：**形态 / 成本估计 / 优势 / 风险与代价 / 与其他 issue 的耦合**。
>
> 用户定夺粒度可以是「每个 issue 选 1 套」、「F1+F2+F5 合并骨架走方案 X，但其他 issue 各自走方案 Y」、或「先做某个 P0/P1 子集，其他延后」——三种粒度都支持。

---

## 0. 全局排序与组合建议（先看这段再翻具体方案）

### 0.1 17 个问题的优先级 / 紧迫性（重申一次）

| # | 标题 | P 级 | 紧迫性 | 性质 |
| --- | --- | --- | --- | --- |
| 7 | 多 bot 互引死循环 | **P0** | 高（下次成本不对称） | 架构层缺 layer |
| 1 | sentinel token 泄漏 | **P0** | 中（持续小漏） | 架构层缺 guardrail |
| 3 | message coalescing | **P0** | 中（节奏伤害） | 架构层缺 layer |
| 10 | 近重复回应自我盲区 | **P0** | 中（issue 7+8 乘法放大器） | 架构层缺 dedup gate + 序列化 |
| 13 | thinker thought 文本泄漏 | **P0** | 中（破沉浸最严重） | 架构层缺 thinker output guardrail |
| 6 | sticker 频率 / 死代码 | P1 | 中（设计行为不达） | 已有代码未挂 |
| 5 | OOV slang reflex | P1 | 中（黑话失语） | 架构层缺 reflex |
| 4 | persona declaration drift | P1 | 中（破沉浸） | source 重写 |
| 8 | schedule oversharing | P1 | 中（破沉浸） | source 重写 + detector |
| 11 | addressee binding 缺失 | P1 | 中（破真实感 + reply injection 隐患） | 架构层缺 nickname binding + quote provenance |
| 12 | 上游工具命令未屏蔽 | P1 | 中（prompt context 污染） | 架构层缺 input filter + known_other_bots（与 issue 7 共缺位） |
| 14 | @ 真 at 不出 CQ 段 | P1 | 中（破真实感 + LLM tool 可靠性隐患） | 架构层缺 mention post-processor / tool registration |
| 2 | 短消息 / 标点-only | P1 | 低（漏少量） | 架构层缺 layer |
| 15 | 复读插件回复异常慢 | P2 | 中（节奏倒挂破沉浸） | plugin 输入选错 + runtime 参数缺失 |
| 16 | bot 自身禁言状态可见性 + 自动恢复 | P2 | 中（noise + UX） | 架构层缺 reconcile + admin SPA + echo gate |
| 17 | 连续 @ 时丢早 @ 目标 + burst | P2 | 中（破真实感 + LLM API 成本） | 架构层缺 per-(group,user) burst window + slot.pending_triggers 列表化 |
| 9 | ☆/✨ 符号存疑 | P3 | 低（用户存疑） | watcher 监测即可 |

### 0.2 推荐组合（按"骨架共用度"分簇，不是行政分组）

| 簇 | 包含 | 理由 | 一次工作量估计 |
| --- | --- | --- | --- |
| **A. 出口 guardrail 簇** | F1（issue 1）+ **F4 Layer 3+5（drift detector + stripper）** + F8 第二刀（issue 8 detector）+ F9（issue 9 watcher）+ F10 dedup gate（issue 10）+ F13 thinker_phrase_detector（issue 13）+ F14 mention post-processor（issue 14） | 七者都挂 `services/llm/client.py` post-LLM 段 / scheduler `_send_to_group` 前；同一 sentinel/pattern/dedup/thinker_phrase/mention registry，七种判定/改写函数；F4 Layer 3 drift detector + Layer 5 stripper 与 F1 sentinel registry 同位点；F14 是出口侧"@昵称→[CQ:at,qq=]"改写 | 1100-1450 行 + 1 套测试基础设施 |
| **B. 入口 normalization 簇** | F2（issue 2）+ F3（issue 3）+ **F4 Layer 2（anchor reinjection）** + F5（issue 5）+ F7（issue 7）+ F10 lock 部分 + F11（issue 11 addressee）+ F12（issue 12 upstream filter）+ F17（issue 17 @ burst window） | 都在 `kernel/router.py` group_listener / `services/reply_workflow.py` gate / `services/scheduler.py:_do_chat` / `_handle_group_message` 入口的"前置 layer"；F10 的 per-group `asyncio.Lock` 与 F7 BotPairLoopGuard 同模式；F4 Layer 2 boundary detector 与 F11/F3/F17 共 scheduler/router 入口 hook（topic shift / tool-result-return / @-mention switch / session boundary）；F11 nickname binding 与 F12 upstream filter 共 router-入口位点；F12 known_other_bots 与 F7 共数据结构；F17 per-(group,user) burst window 与 F3 message coalescer 共 TTLCache 骨架；F11/F14/F17 共 nickname registry + pending_triggers 数据结构 | 2050-2750 行 + 9 套测试 |
| **C. persona source 重写簇** | **F4 Layer 1（compiler validator + CI lint）** + F8 第一刀（issue 8 prompt 改）+ F13 治本路径（ThinkDecision 字段重构） | 同一份 [config/persona/source.md](../../config/persona/source.md) + persona v2 importer pipeline；F4 Layer 1 与 F8 第一刀 / F13 治本共 ImportIssue + system_validation 扩展引擎——把"自由叙事文本进 prompt"改成"结构化 enum / 受护栏的 voice exemplar 进 prompt"；C 簇 3 件复用同一道 lint pipeline | 配合 part6 source-side generation roadmap |
| **D. sticker wiring 簇** | F6（issue 6） | 独立链路，不复用其他骨架 | 100-150 行 + 测试 |
| **E. humanization tuning 簇** | F15（issue 15 echo plugin 输入修正 + runtime 参数补齐） | 独立 plugin 修复——echo plugin 内 humanizer 调用与 scheduler 调用模式不一致；本簇是单点 plugin 修复，不与其他簇共骨架 | 30-50 行 + 测试 |
| **F. self-mute lifecycle 簇** | F16（issue 16 echo gate + admin SPA self-mute 状态卡 + 周期 reconcile） | 三段并行：echo gate 是 bug fix；admin SPA 是 UX 必修；reconcile 是 robustness；与 F7/F12 admin SPA 编辑面板同次 PR 一起做最经济（D6 一次 npm build） | 100-180 行 + 测试 |

> **建议优先序**：A → B 子集（先 F7+F3+F10 lock 部分+F12+F17 burst window+F4 Layer 2 anchor reinjection）→ F → D → C → B 剩余（F2+F5+F11+F14）+ E。理由：A 一次落地清掉 7 件（F1/F4 Layer 3+5/F8 第二刀/F9/F10 dedup gate/F13/F14）；F7+F10 lock 是 P0 必须先修；F17 burst window 与 F3 message coalescer 共 TTLCache 骨架同次落地最经济；F4 Layer 2 boundary detector 与 F11/F3/F17 共 router/scheduler 入口 hook，B 簇内顺手做；F12 与 F7 共 known_other_bots 数据结构同步落地最经济；F16 self-mute echo gate 是 bug fix 必修，admin SPA 改动批一起做；C 簇是 source 改写，受 part6 节奏制约可以滞后；B 余下四件低/中紧迫；E 簇 F15 是单点 plugin 修复，可单独排期或与 B 簇一同 PR。**F4 横跨 A/B/C 三簇**——Layer 1 = C 簇 compiler（与 F8 第一刀 / F13 治本共 lint 引擎）；Layer 2 = B 簇 boundary detector（与 F3/F11/F17 共入口 hook）；Layer 3 drift detector + Layer 5 stripper = A 簇出口 guardrail（与 F1/F8 第二刀/F9/F10/F13/F14 共骨架）；Layer 4 PPA 是 opt-in 后续路线。落地策略：A 簇内 Layer 5 stripper 先做（与 F1 同位点 cheap），Layer 3 drift detector 同次 PR；B 簇 Layer 2 anchor reinjection 与 F11/F17 同次 PR；C 簇 Layer 1 compiler validator 与 F8 第一刀 / F13 治本同次 PR（共 ImportIssue + system_validation 扩展引擎）。F10 横跨 A 簇（dedup gate 是出口 guardrail）+ B 簇（per-group lock 是入口序列化），落地时建议 dedup gate 先（A 簇内顺手）→ lock 后（B 簇 F7 一起做）。F13 横跨 A 簇（phrase_detector 是出口 guardrail）+ C 簇（治本是 ThinkDecision 字段重构）——A 簇内 phrase_detector 优先（与 F1 同位点 cheap），C 簇治本可独立排期。F14 横跨 A 簇（post-processor 是出口侧改写）+ B 簇（共 nickname registry）——建议放 A 簇内同次落地。

### 0.3 不在本文范围

- 不写代码，不改 config，不改 source.md
- 不替用户做选择——所有方案并列，给倾向但不强制
- 不重复《研究文档》已经覆盖的根因证据；本文只列「**怎么做 + 几套对比**」

---

## Issue 1 — sentinel token 泄漏 / post-LLM guardrail（P0）

### 方案 1A — **post-LLM sentinel registry + pipeline guardrail**（推荐）

**形态**：

- 新建 `services/llm/sentinel_registry.py`，集中维护 `SENTINEL_PATTERNS`（白名单/黑名单/watcher 三档），每个条目带 `(pattern, severity, action)` ——`action ∈ {strip, redact, block, warn_only}`
- 在 `services/llm/client.py` reply 出口（即现有 humanization 链尾、send 前一步）插入 `apply_guardrails(reply) -> GuardrailResult`：按顺序跑 strip → redact → block → warn，返回是否放行 + 改写后文本
- 5 处当前散落在代码里的 sentinel 字符串（`«img:N»` / `«图片»` / `«表情包»` / `«回复»` / `«音频»` 等）从硬编码改为从 registry import

**成本**：约 200-300 行净增 + 一套 `tests/test_sentinel_registry.py`（覆盖 strip / redact / block / warn 各路径，含 casing 变体、半角全角、子串嵌套）。

**优势**：

- 与 issue 8 detector / issue 9 ✨ watcher 同骨架——A 簇三件一次落地
- 设计上**可扩展**——新发现的泄漏字符进 config 即生效，不再改代码
- 引用业内成熟模式（openclaw#24583 `stripExternalContentFromOutput()` / Arthur.ai pre-post / Brenndoerfer pipeline）

**风险与代价**：

- pipeline 顺序敬定关键——strip 先于 redact 先于 block；写错顺序会遗漏 casing-bypass
- humanization part2/3/5 已在 reply 出口前做了大量改写；guardrail 必须挂在**最尾**，否则被 humanization 后续改回去
- 需要一次回归 sweep："过去 30 天 reply 中 ✨/☆/«img:» 出现频率"作 baseline，落地后看曲线

**与其他 issue 耦合**：A 簇骨架；F8 第二刀 + F9 直接复用 registry。

### 方案 1B — humanization part5 内嵌 sanitizer

**形态**：把 sentinel 处理塞进 [services/humanization/](../../services/humanization/) 现有 sanitizer 链，而非独立 layer。

**优势**：不新增模块，与现有 humanization 节奏耦合更紧。

**风险**：

- humanization 关注的是"语感"——sentinel guardrail 的语义是"安全"，混在一起后期难拆
- 与 issue 8 detector 共骨架的目标破坏（detector 不属于 humanization 范畴）
- 不便扩展 watcher 模式（issue 9）

**评级**：劣于 1A——**仅在用户希望"代码模块数尽量少"时考虑**。

### 方案 1C — LLM 自审（在 prompt 加"输出前自检"）

**形态**：在 system prompt 末尾加"检查回复中不要出现 `«img:» / «图片»` 等占位符"。

**风险**：和 issue 1/4/6 已经实测过的 **prompt-only 控制对 v4-flash 不稳**形成同型失败。issue 1 本身就是"prompt 命令不可靠"的证据，再用同种手段就是治标。

**评级**：**不推荐**。仅在用户明确要求"零代码改动 + 仅靠 prompt"时使用。

---

## Issue 2 — 短消息 / 标点-only / 低信号文本（P1）

### 方案 2A — **独立 text_preflight normalizer 模块**（推荐）

**形态**：

- 新建 `services/text_preflight.py`：`Preflight(text) -> PreflightResult{ density: float, oov_terms: list, punctuation_only: bool, recommended_action }`
- 在 `services/reply_workflow.py` `should_call_semantic_gate` **前**插入 preflight；命中"标点-only / 单字 / emoji-only"等低信号模式时直接走 `pass_low_signal` 短路（不调 gate，不消耗 LLM token）
- 复用 Rasa custom NLU component / botonic `tokenizer + normalizer + stemmer` 三段式

**成本**：~150-200 行 + 测试（标点 / emoji / 数字 / 单字 / 空文本各 case）。

**优势**：B 簇骨架的入门件——结构最简单，先落地它能验证整条 normalization 思路；与 F3 / F5 / F7 共用 router 入口；不动 gate prompt（保持 issue 5 修复时 gate 改写空间）。

**风险**：

- "低信号"判定阈值需要 gray-box 调；初期可能误压少量"用户其实想撩 bot 的颜文字"
- 需要把 emoji-only 短句的处置和 issue 9（✨ watcher）协调——两条 layer 都看 emoji，但视角不同（一个判信号密度，一个判泄漏）

**与其他 issue 耦合**：B 簇；可与 F3 共享 `density / signal_score` 字段。

### 方案 2B — 直接在 gate prompt 里加"识别低信号"指令

**形态**：在 `_SEMANTIC_GATE_PROMPT` 里加"如果消息只含标点 / emoji 直接返回 confidence=0"。

**风险**：

- 又是 prompt-only 控制，和 issue 1/4/6 同型失败
- 浪费 gate 一次 LLM call（preflight 应该早于 gate，不是塞进 gate）

**评级**：**不推荐**。

### 方案 2C — 在 `kernel/router.py` 入口直接 regex 过滤

**形态**：在 group_listener 第一行加 `if re.fullmatch(r"[\p{P}\s]+", text): return`。

**优势**：3 行代码搞定。

**风险**：

- 没有可观测性（直接 drop，不写日志、不计 metric）
- 和未来 F3 coalescer / F7 pair_guard 各自独立 if 链，扩展性差
- 阈值不可调

**评级**：**短期可行但劣于 2A**——若用户想"先快速止血再做正经版"可用；之后 2A 落地时务必撤掉这层 regex 短路。

---

## Issue 3 — message coalescing / per-sender debounce（P0）

### 方案 3A — **独立 coalescer 服务 + config-driven 窗口**（推荐）

**形态**：

- 新建 `services/coalesce.py` `MessageCoalescer`：维护 `(group_id, sender_id) → CoalesceBucket{messages: deque, idle_timer, max_window_timer}`
- 在 `kernel/router.py` group_listener 拿到消息后**先入 bucket**，bucket 在 `idleWindow`（默认 4-6s）静默后或 `maxWindow`（默认 12-15s）到期时整体 flush 到 GroupChatScheduler
- `priority bypass`：@bot / 引用 bot 的消息绕过 bucket 直接走（保留即时响应）
- config.json 顶层加 `coalesce: { enabled, idle_window_ms, max_window_ms, max_buffered_messages, priority_bypass: ["at_mention", "reply_to_bot"] }`

**成本**：~400-500 行 + 测试（idle / max / bypass / multi-sender 隔离 / 跨群隔离 / shutdown flush）。

**优势**：

- 引用最契合的两个工程实现：openclaw#51361（详细 RFC：(sessionKey, senderId) 分桶 + idleWindow + maxWindow + priority bypass）+ hermes-agent#345 Spacedrive Spacebot CoalesceConfig
- 与 F7 共用 router 入口，state board 也对齐（都需 (group_id, peer_id) keyspace）
- n8n workflow 8238 实战：buffer 是 AI 对话**必要前置层**，不是可选优化

**风险**：

- 引入「等待」语义：bot 看似"反应变慢"——idle_window 默认要够低（≤6s），不然用户感受到 lag
- shutdown / restart 时 bucket 里未 flush 的消息要持久化（否则用户消息丢失）——这是工程难点；可选简化：shutdown 立刻 flush 全部 bucket
- 与 humanization part2/3 的回复延迟 / 思考时间感模型有交互——需要一次集成测试

**与其他 issue 耦合**：B 簇；与 F7 共用 `(gid, sender_id)` keyspace 和 router 入口。

### 方案 3B — Redis-backed coalescer（n8n workflow 8238 同型）

**形态**：用 Redis 替代内存 deque 持久化 bucket，重启 / 多副本场景天然安全。

**优势**：未来扩多副本部署 / Kubernetes 时直接可用。

**风险**：

- omubot 当前是单 bot 实例 + SQLite 持久化的轻量架构，引入 Redis = 新依赖
- D6 边界：影响 docker-compose；rebuild bot；napcat 永远不动是硬约束
- 性价比低：当前需求规模 < 100 群，内存 bucket 完全够

**评级**：**不推荐当下做**——等 omubot 真到多实例部署时再切。

### 方案 3C — debounce 在 GroupChatScheduler 内做（不新增模块）

**形态**：把 idle/max 逻辑塞进 GroupChatScheduler 的 batch 触发链。

**优势**：复用现有调度器，模块数最少。

**风险**：

- scheduler 关注"何时 fire"，coalescer 关注"消息怎么合并"——职责混淆
- 与 F7 不共骨架（pair_guard 在 router 入口拒绝消息，coalescer 在 router 入口合并消息——两件都在 router 入口；scheduler 内做的话 F7 没法搭便车）

**评级**：劣于 3A，**可作为 3A 落地前的 stop-gap**。

---

## Issue 4 — persona declaration drift / Assistant Axis（P1）

> **第二次重写（2026-05-27）**：v1 4A "compiler+CI+runtime 三层" 被用户否决——理由："你全程没有动用搜索，全程主观臆断。打回，我需要更确凿的根治方案"。本节 v2 由 4 轮检索（共 ~25 条 query，14 条核心证据）回填。每条干预措施列**引用源**与**Anthropic API 可行性结论**；不可行的方案（logit_bias / activation capping / split-softmax / grammar-constrained decoding）在末尾明列+引证淘汰原因。结论：**单一干预无法根治**，需 5 层混合（build → request → inference monitor → 出口）；本节 4A 的关键差异是**每层都有学界/工程界引证 + Anthropic API 可行性 sanity check**，而非 v1 那样"听起来合理"的拼装。

### 0. Anthropic API 能力边界与不可行方案（先列约束再设方案）

| 不可行方案 | 引证 | 不可行原因 |
| --- | --- | --- |
| **logit_bias 限定 token 概率** | [anthropic-sdk-python issue #393](https://github.com/anthropics/anthropic-sdk-python/issues/393)：Anthropic 官方明确"no public plans"；[platform.claude.com OpenAI SDK 兼容文档](https://platform.claude.com/docs/en/api/openai-sdk)：`logit_bias / presence_penalty / frequency_penalty / seed / response_format` 全部 **Ignored**；LMQL 项目 [issue #118](https://github.com/eth-sri/lmql/issues/118)："Anthropic does not offer logit-distribution masking" | Anthropic 在 API 层不暴露 logit 操作——任何 token 概率重写、grammar mask、constrained decoding 都不可用 |
| **activation capping at 25th percentile（Assistant Axis）** | arxiv 2601.10387 Anthropic + Oxford 2026-01："linear direction in activation space, R² 0.53-0.77, drift 7.3x in emotional convo, capping reduces persona-jailbreak ~60%" | 需要 open-weight model 的激活值访问；Anthropic API 不暴露 activation logits |
| **split-softmax 注意力重权** | arxiv 2402.10962 + [github likenneth/persona_drift](https://github.com/likenneth/persona_drift)：α'_{t,i} = π^k(t)/π(t) · α_{t,i} 对 system prompt token 重权 | 同上——需要 inference-time attention map 写权限 |
| **Persona-judge 推测解码** | ACL findings 2025 token-level self-judgment | 需要 token logprob + 多步 draft；Anthropic 当前不支持 speculative decoding API |
| **Outlines / XGrammar / SGLang grammar 约束** | github outlines-dev / mlc-ai/xgrammar / SGLang | 需要本地模型 FSM logit mask；不能跨 Anthropic API |

**留下的可用 primitive**：

- `stop_sequences`（最多 4，byte-exact 匹配）—— [Claude API stop_sequences 文档](https://platform.claude.com/docs/en/api/messages)
- `tool_choice = auto/any/tool/none + disable_parallel_tool_use` —— [tool-choice 文档](https://docs.claude.com/en/docs/agents-and-tools/tool-use/overview)
- **Assistant prefilling**——assistant role 消息可半填，模型续写 —— [prefill-claudes-response 文档](https://docs.claude.com/en/docs/build-with-claude/prompt-engineering/prefill-claudes-response)（与 Citations / Structured Outputs 互斥；与 Extended Thinking 互斥）
- temperature / top_p / top_k

**结论**：根治路径必须落在"prompt 工程 + 流水线护栏 + 出口检查"组合，而非 token-level 操控。

---

### 方案 4A — **5 层 API-feasible 混合护栏**（推荐）

**总览**（每层都有引证，纵深防御 → 单层失守不致 drift）：

```text
[Build]   Layer 1：persona compiler validator        → bundle.ok=False / fallback v1
[Request] Layer 2：anchor reinjection at boundaries  → user-role 注入 + assistant prefilling
[Infer]   Layer 3：drift detector + bounded retry    → EchoMode FSM-style + REPAIR DIRECTIVE
[Infer]   Layer 4：post-hoc refinement (PPA-style)   → 可选，2× LLM call 代价换 alignment
[Exit]    Layer 5：runtime stripper / sentinel       → A 簇 belt-and-suspenders
```

#### Layer 1 — build-time persona compiler validator（治本，离开作者）

**引证**：

- [services/persona/models.py:85](../../services/persona/models.py#L85) `ImportIssue(level=...)` dataclass + [services/persona/system_validation.py:32](../../services/persona/system_validation.py#L32) `issue_level="error" if not result.ok else "info"` 已是仓内既存模式（**已读源验证**）；本层是同型扩展，**不新建校验框架**
- arxiv 2601.10387 Assistant Axis 的工程结论："reinforce through behavior（voice exemplar）, not declarations（meta-statement）"——本层把"少写 declaration、多写 voice exemplar"从作者记忆里搬到 compiler 强检约束

**形态**：

- [services/persona/compiler.py](../../services/persona/compiler.py) 新增 `declaration_lint()` 阶段——扫 `core.identity / core.personality / core.role` 结构化字段（**不扫 §1.3 voice exemplars**，避免误伤引述）
- 命中正则 `^\s*(我是|我叫|作为|我的身份是|我担任|我扮演)\S+` 写 `ImportIssue(level="error", message=..., remediation="改用 voice exemplar 或 behavior clause；见 arxiv 2601.10387 §4")`
- `§1.3 voice exemplars` 实体数 < N（默认 6）→ `ImportIssue(level="warn")`；< N/2 → `level="error"` → `bundle.ok=False`
- bundle.ok=False 后 [services/persona/runtime.py:63](../../services/persona/runtime.py#L63) `load_pending_freeze` 自然回落 v1（仓内 fallback 既存路径）
- pre-commit hook + CI 跑同份 lint（新建 `scripts/check-persona-source.py` 与 [scripts/check-ui-compliance.sh](../../scripts/check-ui-compliance.sh) 同模式）

**为什么算"根治"**：作者换人 / part6 生成器换 prompt / 三个月后 fork persona——同一组校验都会拦下；规则离开作者大脑进入 toolchain。

#### Layer 2 — request-time anchor reinjection at semantic boundaries

**引证**：

- [tianpan.co/blog/persona-drift-agent-identity-stability](https://tianpan.co)（2026-04-26）三模式："prompt template separation / periodic reinjection at semantic boundaries（NOT every N turns blindly）/ role-checkpoint summaries at 50+ turns"；明确"persona half-life as release gate metric"
- [agentpatterns.ai event-driven system reminders](https://agentpatterns.ai)：**user-role injection** 比 system prompt addition 在 attention window 中更可靠（system prompt 在长上下文中被 dilute，user-role 接近末端 → attention recency bias）；event detector + additive safety
- arxiv 2402.10962 同时给出 attention decay 实证："system prompt token attention 随对话深度衰减"——所以 reinjection 必须靠近 user-role 末端

**形态**：

- 在 [services/scheduler.py:_do_chat](../../services/scheduler.py) 调 LLM 前，检测 4 类 semantic boundary：
  1. topic shift（gate / classifier 已有的 topic_intent_label 切换）
  2. tool-result-return（含 web_search / sticker / persona_runtime）
  3. @-mention 切换（B 簇 F11 nickname binding 的输出可作 trigger）
  4. session boundary（前后两条 user message 间隔 > T 秒，T 与 humanization scheduler 共参）
- 命中 → 在 messages 末端追加一条 user-role 消息（**不是 system prompt 加段**），内容是从 freeze 里抽 6-10 行最新 voice exemplar + 1 句 behavior clause（"用动作展现身份，避免 meta 自述"）
- 同时尝试用 **assistant prefilling**（`messages[-1]` 是 assistant role，半填 voice exemplar 起句）做"风格牵引"——参考 [docs.claude.com prefill-claudes-response](https://docs.claude.com/en/docs/build-with-claude/prompt-engineering/prefill-claudes-response)；与 humanization scheduler `pass_turn` 不冲突
- metrics：`anchor_reinject_count` / `anchor_boundary_type_distribution` 进 [services/block_trace/store.py](../../services/block_trace/store.py)

**为什么算"根治"**：persona 不再仅靠开场 system prompt 维持——每次 boundary 都被"重新校准"；与 attention decay 学界结论对位，不是凭空设计。

#### Layer 3 — inference-time drift detector + bounded retry

**引证**：

- [github Seanhong0818/Echo-Mode](https://github.com/Seanhong0818/Echo-Mode)（Apache-2.0 TS middleware，API-agnostic）：4 状态 FSM `Sync / Resonance / Insight / Calm` + EWMA 平滑 λ≈0.3 + drift score vs baseline + repair loop on threshold 越界——**完全 API-agnostic，可直接搬到 Python**
- [blog.ozigi.app banned-lexicon-validator](https://blog.ozigi.app)（2026）"REPAIR DIRECTIVE" 模式：4-pass scan（vocab/phrase/opener/regex）+ **one bounded retry** with 指令 "Do NOT paraphrase. Re-read source material. Write fresh draft from scratch"；明确警告"LLMs regress to the mean — third+ retries introduce different tells"——所以 hard cap = 1
- 业界先例：cursor.com / poly.dev 都把 banned-lexicon validator 列为 production guardrail

**形态**：

- 在 [services/llm/client.py](../../services/llm/client.py) post-LLM stream 完结后接入 `PersonaDriftDetector`：
  - baseline persona signature = freeze 抽出的"凤笑梦感 fingerprint"（短句节奏 / ☆ 符号用法 / 撒娇模板 / 第三人称指代频率），脚本 build-time 生成存 `_persona_runtime.json`
  - per-reply drift score `s ∈ [0, 1]`：编辑距离 + n-gram overlap + declaration-pattern hit count + sentinel-set hit count；EWMA 平滑（λ=0.3）over 最近 K 条 reply
  - score > θ_repair（默认 0.6）→ 触发 **REPAIR DIRECTIVE 重生成**：augmented prompt 加一条 user-role "上一段回复偏离 persona signature（drift=0.X）。**不要改写、不要解释**——重新读 voice exemplars、从空白起笔写新版本"
  - **hard cap = 1**（依 Ozigi 结论；3+ 次会引入新风格 artifact）
  - score > θ_block（默认 0.85）→ 直接 drop reply（与 humanization scheduler `pass_turn` 共出口）
- FSM 状态写 [services/block_trace/store.py](../../services/block_trace/store.py) `persona_drift_state` metric；`persona_drift_repair_hit_rate` / `persona_drift_drop_rate` 进周报

**D2 cancel-path**：`PersonaDriftDetector.repair_once()` 必带 `pytest.raises(TimeoutError)` 测——LLM stream cancel 时不能脏 EWMA 状态。

**为什么算"根治"**：drift 是连续量；compiler 拦不住的运行时漂移由 detector 在出口前实时纠偏；EchoMode FSM 是已开源、已被多个项目验证的 reference design——不是凭空发明。

#### Layer 4 — post-hoc PPA-style refinement（可选，2× LLM call 成本换 alignment）

**引证**：

- arxiv 2506.11857 Post Persona Alignment（PPA, EMNLP findings 2025）：**两阶段**——(1) 用对话上下文先生成 general response；(2) 把 response 当 query 检索 persona memory，再 refine alignment。**反转传统 retrieve-before-generate**；论文实证"避免 rigid persona mention，提升 naturalness + diversity + consistency"

**形态**：

- 仅对 Layer 3 命中 `θ_repair < s < θ_block` 的中段 drift case 启用（避免无差别 2× cost）
- pipeline：first reply → 抽 N=3 voice exemplar 与 first reply 同 topic → 第 2 次 LLM call "refine 这条回复，使风格贴合 [exemplars]，但不要 declaration、不要 paraphrase——保留原意改 voice"
- 出现率上限：每群每 30 分钟 ≤ 3 次（防 cost 爆炸）；超限走 Layer 3 drop

**注意**：本层是 **opt-in**，不在初版 4A 推荐范围；先上 Layer 1+2+3+5，观察 7 天，若 drift_repair_hit_rate > X% 再启 Layer 4。本层独立可关。

#### Layer 5 — exit-side stripper（A 簇骨架）

**引证**：

- arxiv 2402.10962 attention decay 工程结论："出口 layer 是已 drift 的最后兜底"
- OpenAI Cookbook [How_to_use_guardrails](https://github.com/openai/openai-cookbook/blob/main/examples/How_to_use_guardrails.ipynb) moderation pattern：post-output regex/classifier 是行业标配
- 仓内 F1 sentinel registry / F8 第二刀 detector / F13 phrase_detector 共骨架（A 簇 6 件）

**形态**：

- [services/persona/runtime_selector.py](../../services/persona/runtime_selector.py) `join_static_blocks` 跑 `strip_declarations()`——把 compiled `core.identity` 块里残留的 `^我是X` 起始行换为对应 voice exemplar；命中写 `declaration_strip_count` metric
- post-LLM 链尾（[services/llm/client.py](../../services/llm/client.py)）跑 `meta_self_declaration_detector` 与 F1 sentinel registry 共 list；命中改写或截短

**为什么仍需要 Layer 5**：Layer 1-4 是 inbound + inference 侧；Layer 5 防"compiler 漏判 + 历史 freeze 残留 + retry 仍然漏出 declaration"——belt-and-suspenders；**单层失守不致全线失守**。

---

**成本估计（Layer 1+2+3+5，不含 Layer 4）**：

- Layer 1 lint 引擎 ~80 行 + CI 脚本 ~50 行
- Layer 2 anchor reinjection ~120 行（boundary detector 复用 gate topic_intent_label / tool-result hook / F11 输出 / session timer）+ assistant prefilling wiring ~30 行
- Layer 3 drift detector + EWMA + REPAIR DIRECTIVE retry ~180 行 + baseline signature build script ~60 行
- Layer 5 stripper ~60 行（与 F1 共骨架可顺手）
- 测试 `tests/test_declaration_lint.py` ~80 / `tests/test_anchor_reinjection.py` ~100（含 D2 cancel-path）/ `tests/test_persona_drift_detector.py` ~150（含 EWMA 数值回归 + cancel-path）/ `tests/test_runtime_stripper.py` ~50

合计 ~960-1100 行（其中 Layer 5 与 A 簇 F1 骨架共 ~60 行可减计）。

**优势**：

- **每一层都有学界 / 工程界引证**——arxiv 2402.10962 attention decay / 2506.11857 PPA / 2601.10387 Assistant Axis / EchoMode FSM (apache-2.0) / Ozigi banned-lexicon REPAIR DIRECTIVE / tianpan.co semantic boundary reinjection / AgentPatterns.ai user-role injection / OpenAI Cookbook guardrails；**不是凭空设计**
- **每一层都通过 Anthropic API capability sanity check**——不依赖 logit_bias / activation capping / attention rewrite / grammar mask；只用 stop_sequences / tool_choice / prefilling / messages 操控
- **纵深防御 5 层**：build → request → inference detect → optional refine → exit；任一层失守由下游补偿；与单层方案的根本差异在"不假设单点能根治"
- **Layer 1 满足"作者换人也不会犯错"的结构性诉求**——规则进 toolchain 而非作者记忆；这是 v1 4A 已验证可行的部分，本 v2 保留并扩展
- **Layer 2 的 boundary detector 与 B 簇 F11/F3/F17 共 router/scheduler 入口 hook**——可顺手做；Layer 5 与 A 簇 F1/F8 第二刀/F13 phrase_detector 共骨架
- **Layer 3 EchoMode 是 Apache-2.0 已开源**——可直接 port 到 Python，无 IP 顾虑；EWMA λ=0.3 是论文/工程常见经验值
- 与 part6 source-side generation roadmap 协同：Layer 1 的 `declaration_policy` 反向作为 part6 生成器的 prompt 约束；生成器产物天然合规

**风险与代价**：

- **Layer 3 EWMA 阈值灰度调参**：θ_repair=0.6 / θ_block=0.85 / λ=0.3 是初值，需 7-14 天灰度观察 false-positive 率；mitigation = `_persona_runtime.json` 顶层 `drift_policy: { theta_repair, theta_block, ewma_lambda, repair_max_retries: 1 }` 可 per-persona override
- **Layer 2 user-role 注入与 prompt cache 抵触风险**：CLAUDE.md 提到 4 个 prompt cache breakpoints（tools[-1] / system block 1 / system block 2 / messages[near-end]）；user-role 末端 reinjection 会让最末 cache point 失效；mitigation = anchor 内容固化（同 group 同 hour 同 anchor）让缓存命中率下降但不归零；周观察 `cache_hit_rate` metric 是核心证据
- **Layer 3 retry 把 LLM call 变 2 次 in worst case**——cost 上限按 `drift_repair_hit_rate × call_cost` 算；初期上限灰度 5-10%，超限收紧 θ_repair
- **Layer 4 PPA 默认关**——纳入路线图但不进初版；避免无差别 2× cost
- **assistant prefilling 与 Citations / Structured Outputs 互斥**——若仓内未来用 Citations 输出 system_validation 引用，需在那一路径关 prefilling；当前 omubot 不用 Citations 故无影响
- **Layer 1 declaration 正则误伤引述**——mitigation 同 v1：只扫结构化字段，跳过 §1.3 voice exemplars 块 by block id
- **Layer 3 baseline signature build script 漂移**：persona freeze 更新时需重建 signature；纳入 freeze pipeline post-hook，与 [services/persona/runtime.py:20-21](../../services/persona/runtime.py#L20-L21) `source_sha256` drift warning 同位点

**与其他 issue 的耦合**：

- **Layer 1（C 簇）**：与 F8 第一刀 / F13 治本路径共同模式——同一道 ImportIssue + system_validation 扩展引擎；C 簇 3 件复用骨架
- **Layer 2（B 簇）**：boundary detector 与 F11 nickname binding / F3 message coalescer / F17 burst window 共 router/scheduler 入口 hook
- **Layer 3（新出口侧子簇，可视为 A 簇扩展）**：drift detector 在 LLM call 完结后跑，与 F1 sentinel / F8 第二刀 / F13 phrase_detector 同位点（post-LLM）；FSM 状态机本身独立但共 metric 通道
- **Layer 5（A 簇）**：与 F1 sentinel registry / F8 第二刀 / F13 phrase_detector / F14 mention post-processor / F10 dedup gate 共出口 guardrail 骨架（A 簇 6 件之一）
- **part6 source-side generation roadmap 强耦合**：Layer 1 `declaration_policy` 同时是 part6 生成器的输入 prompt 约束；本方案落地后 part6 路线图减负

**评级**：**推荐 4A（Layer 1+2+3+5 必做，Layer 4 PPA 后续 opt-in）**——5 层混合、每层引证、每层 Anthropic API 可行性 sanity check 通过；纵深防御 + 与 A/B/C 三簇骨架协同；这是 v2 重写后**有学界/工程界证据支撑**的根治方案，**不是 v1 那样的拼装**。

### 方案 4B — 仅 Layer 5 出口 stripper（v1 4B 的留底，仅作 stop-gap）

**形态**：仅做 4A Layer 5（runtime_selector strip + post-LLM detector），完全不做 Layer 1-4。与 F1 sentinel registry 共 A 簇骨架。

**成本估计**：100-150 行净增。

**优势**：

- 改动面最小 / 部署最快 / 与 A 簇 F1 共骨架顺手
- 作为 4A 落地前的临时止血层

**风险与代价**：

- **治症状不治本——明确不满足"换一个人写也不会犯错"的结构诉求**：compiler 仍接受 declaration-heavy source.md / 运行时仍按 declaration 选 token / 没有 boundary reinjection 让 system prompt attention decay 无对策；引证：arxiv 2402.10962 attention decay + 2601.10387 Assistant Axis 都明确指出"出口层无法根治源头漂移"
- LLM 内部仍按 declaration 选词——形态绕过容易（"我可是X~" 绕过 `^我是` pattern；"作为X的守护者" 走非 copula 路径）；持续维护 detector pattern 集合是反复消防
- 失去与 C 簇 F8 第一刀 / F13 治本的骨架共用；issue 4/8/13 "自由叙事文本→结构化字段"治本动力消失
- token 浪费——每次都需要 LLM 先输出 declaration、出口再改写

**与其他 issue 的耦合**：与 F1 sentinel registry / F8 第二刀 / F13 phrase_detector 共 A 簇出口骨架；不与 C 簇 / B 簇任何 issue 共骨架。

**评级**：**次选 4B**——仅作为 4A Layer 1+2+3+5 落地前的**临时止血**或灰度时的 belt-and-suspenders；**不能作为唯一防线**——不满足"换一个人写也不会犯错"的结构诉求；落地 4A 后这层自然成为 Layer 5，不被废弃。

### 方案 4C — fine-tune（Persona-Aware Alignment / Persona-judge 路线）

**引证**：

- arxiv 2511.10215 PAL（Persona-Aware Alignment, TACL 2025）：两阶段训练（Persona-Aware Learning + Persona Alignment）+ "Select then Generate" 推理；要求 fine-tuning 权限
- ACL findings 2025 [Persona-judge](https://aclanthology.org)：token-level 自评 via speculative decoding；要求 token logprob 访问

**形态**：拿 omubot 历史好样本 + voice exemplars 做 LoRA 微调；或自托管基座 + Persona-judge speculative decoding pipeline。

**成本估计**：架构级——服务托管模型 / 微调流水线 / 数据集治理 / 推测解码引擎，量级跳跃。

**优势**：character 入"权重"层，比 prompt 稳定 N 倍；最深层根治。

**风险与代价**：

- **Anthropic API 不支持 user-fine-tune**（[platform.claude.com OpenAI SDK 兼容文档](https://platform.claude.com/docs/en/api/openai-sdk) 已确认）；自托管 = 服务架构改造，脱离当前"租 Anthropic API"运行模型
- 数据集质量决定一切——需要大量好样本（≈ part6 source-side generation 成熟之后才有稳定源头）
- Persona-judge 需 speculative decoding 引擎（vLLM / SGLang）+ token logprob 访问，omubot 当前架构不具备
- 与 issue 4/8/13 解耦——不与 C 簇 / A 簇 / B 簇任何 issue 共骨架
- 微调权重也会随 base model 升级失效——长期维护成本

**评级**：**不推荐当下做**。仅在 part6 源生成成熟、有足量好样本、且 omubot 决定迁离 Anthropic API 时讨论；近期不在范围。本方案与 4A 不互斥——4A 5 层护栏可与未来 4C 共存（权重层 + toolchain 层双层）。

### 复审增补（2026-05-27）— DeepSeek dual-provider 视角下的 §0 表勘误 + 4C 修订

> **背景**：[services/llm/provider.py:76-87](../../services/llm/provider.py#L76-L87) 表明 omubot **当前已支持 DeepSeek**（4 档 provider mode：`native` / `native-beta` / `anthropic-compat` / `openai-compat`）；[services/llm/client.py:1232](../../services/llm/client.py#L1232) 也按 `api_format == "deepseek"` 分支处理 thinking。也就是说本仓不是"租 Anthropic API"单一供应方——v2 §0 不可行清单里若要把 logit_bias / fine-tune 这类条目当成"绝对淘汰"必须按 provider 分别检 sanity。本节是 v2 4A 的**增量勘误**，原版本 §Issue 4 全部保留——本节只列 DeepSeek 路径下需调整的边界，不删任何已有结论。

#### DeepSeek 官方 API 能力快表（按 v2 §0 表的同口径再列一次）

| 能力 | Anthropic（v2 §0 已论证） | DeepSeek 官方 API（[api-docs.deepseek.com](https://api-docs.deepseek.com/api/create-chat-completion)） | 状态差 |
| --- | --- | --- | --- |
| `logit_bias` | **Ignored**（platform.claude.com OpenAI SDK 兼容文档 + anthropic-sdk-python#393 + lmql#118） | **未列入官方文档参数清单**——`/api/create-chat-completion` 仅列 `temperature/top_p/logprobs/top_logprobs/response_format/tools/tool_choice/stop` 等；社群第三方平台（如某些代理）表现"接受但不强保证"——**不能按"可用"对待** | 同 Anthropic：**不可作为根治原语** |
| `logprobs / top_logprobs` | **不暴露** | **暴露**——`logprobs: bool` 与 `top_logprobs: int (≤ 20)` 在 V4 chat completions 显式列出（[api-docs.deepseek.com create-chat-completion](https://api-docs.deepseek.com/api/create-chat-completion)）。注意：**deepseek-reasoner / 思考模式不支持**（[apidog 文档](https://deepseek.apidog.io/reasoning-model-deepseek-reasoner-835841m0) "Setting logprobs / top_logprobs will trigger an error"） | **DeepSeek 多出一档**：可做 token-level confidence 评估；启用 logprobs 时 thinking 必须关 |
| **Assistant 消息预填** | Anthropic **prefilling**（与 Citations / Structured Outputs 互斥；与 Extended Thinking 互斥） | DeepSeek **Chat Prefix Completion (Beta)**——`messages[-1]` 必须 `role=assistant` + `prefix=true`；`base_url="https://api.deepseek.com/beta"`（[api-docs.deepseek.com chat_prefix_completion](https://api-docs.deepseek.com/guides/chat_prefix_completion)） | **形态等价**——v2 4A Layer 2 "assistant prefilling 牵引风格"在两边都可用；DeepSeek 路径需切 base_url 至 `/beta` |
| `response_format = json_object` | Tools input_schema + JSON output（行业标配） | **官方暴露 `response_format: { "type": "json_object" }`**（[api-docs.deepseek.com json_mode](https://api-docs.deepseek.com/guides/json_mode)）；`deepseek-reasoner` 不支持 | **形态等价**——不影响 §Issue 4 |
| `stop_sequences` | 4 条 byte-exact | DeepSeek `stop` 数组——同义 | **形态等价** |
| **官方 API 微调** | 不支持 user-fine-tune | **官方 API 也不暴露 first-party 微调端点**——但 **R1 / V3 / V4 权重 MIT-licensed**，输出可用于 distillation / fine-tuning（[chat-deep.ai/guide/deepseek-fine-tuning](https://chat-deep.ai/guide/deepseek-fine-tuning)；[fireworks.ai blog](https://fireworks.ai/blog/fine-tuning-deepseek-models)）；自托管 LoRA / 第三方托管训练（Fireworks、CoreWeave、DeployBase）已是行业常规 | **DeepSeek 多出一档**：4C fine-tune 在"自托管 / 第三方托管"路径下从架构级跳跃降为**LoRA + 推理服务**两段工程；不脱离 omubot 当前架构（仍走 OpenAI-compatible base_url，仅指向自托管 endpoint） |
| **Activation capping / split-softmax / Outlines / XGrammar / SGLang grammar** | 不可用——封闭权重 | **可用**——但前提是**自托管推理**（vLLM / SGLang）不是走官方 `api.deepseek.com`；走官方 API 时一律不可用 | **条件性可用**——只在自托管路径下打开；走官方 API 时与 Anthropic 等价 |
| **Persona-judge speculative decoding** | 不可用 | **可用条件同上**（需 vLLM / SGLang 推理引擎 + token logprob 写权限） | **条件性可用** |

#### v2 §Issue 4 各层在 DeepSeek 路径下的可行性勘误

> **核心结论**：**走官方 `api.deepseek.com`（v4-flash / v4-pro，omubot 当前默认路径）时，v2 §Issue 4 5 层架构 100% 可平移，无需改写**——所有 layer 用的原语（stop_sequences / response_format / tools / prefix=true assistant prefilling / `messages[-1]` 操控 / 出口侧 stripper）DeepSeek 全部官方支持。

- **Layer 1（build-time compiler validator）**——provider-agnostic，与 LLM API 无关；DeepSeek 路径完全等价。**无修订**
- **Layer 2（request-time anchor reinjection）**——provider-agnostic；assistant prefilling 在 DeepSeek 走 `prefix=true` + `base_url=.../beta`，与 Anthropic prefilling 形态等价。落地实现要点 = [services/llm/provider.py](../../services/llm/provider.py) `provider_mode == "native-beta"` 时 prefill 通过 `prefix=true` 字段；`provider_mode == "anthropic-compat"` 时通过 messages[-1] role=assistant 预填字符串——两边由 provider 抽象层吃掉，业务侧 Layer 2 不感知。**无修订**
- **Layer 3（drift detector + bounded retry）**——FSM / EWMA / REPAIR DIRECTIVE 全部 prompt-level 操作 + 出口比对，与 token logprob 无关；DeepSeek 路径等价。**无修订**
  - 但 **DeepSeek 路径下可选用 logprobs 增强 drift signal**——如启用 logprobs，可拿首 K 个 token 的 top_logprobs 算 "persona signature 与 top alternative 的 logprob 差"，加进 drift score 公式；属 Layer 3 灰度后期的 **opt-in 信号源**（与 EchoMode FSM 主路径并列、不取代）；前提是关 thinking
- **Layer 4（PPA post-hoc refinement）**——provider-agnostic；DeepSeek 路径等价。**无修订**
- **Layer 5（exit-side stripper）**——provider-agnostic；DeepSeek 路径等价。**无修订**

#### 4C fine-tune 评级修订（保留原版）

> **原版 4C 评级**："**不推荐当下做**。仅在 part6 源生成成熟、有足量好样本、且 omubot 决定迁离 Anthropic API 时讨论；近期不在范围"——本节增补**不删除原版**，但**修订条件**：

- **Anthropic 路径**：原版评级保持——架构级跳跃；近期不在范围
- **DeepSeek 路径**：原版评级**部分缓和**——
  - DeepSeek R1 / V3 权重 MIT-licensed + DeepSeek 官方明示"API outputs 可用于 distillation / fine-tuning"，许可面零摩擦
  - 7B / 14B distill 模型在 LoRA / QLoRA 下 24GB 单卡可训（[datacamp](https://www.datacamp.com/tutorial/fine-tuning-deepseek-r1-reasoning-model)、[firecrawl.dev fine-tuning-deepseek](https://www.firecrawl.dev/blog/fine-tuning-deepseek)）；不必涉及 V4-Pro 1.6T 全量
  - 但 omubot 当前走 DeepSeek **官方 `api.deepseek.com`（v4-flash / v4-pro）**——若沿用官方 API，仍**无 first-party 微调端点**（[chat-deep.ai/guide/deepseek-fine-tuning](https://chat-deep.ai/guide/deepseek-fine-tuning) "DeepSeek's public API documentation focuses on inference endpoints rather than a first-party managed fine-tuning endpoint"）；要做 4C 必须三选一：① 自托管 distill 模型（vLLM / SGLang）② 第三方托管训练 + 部署（Fireworks / CoreWeave / DeployBase）③ 切回 Anthropic 但维持 Anthropic-compat 端点写 prompt-level 4A
  - 评级 = "**仍不推荐当下做**，但条件下放：**part6 源生成成熟 + 7B/14B distill 单卡可训** 任一达成即可重新评估，不再绑定"omubot 决定迁离 Anthropic"前置条件"
- **本节修订仅追加，不动原版评级文本**——避免决策模板回滚混乱；原版评级用于"当前默认 Anthropic 路径决策"，本节修订用于"如已切 DeepSeek 后再回看 4C"的决策

#### 提示工程层：DeepSeek 已知 persona drift quirk（不增 layer，仅警示）

- DeepSeek V3 / V4 在长上下文 chat 模式下被 SillyTavern 社群报告**会重排 system messages**（[reddit r/SillyTavernAI](https://www.reddit.com/r/SillyTavernAI/comments/1kmbbfk/psa_if_youre_using_deepseek_v3_0324_through_chat/)）——**直接命中 v2 §Layer 2 "system prompt token attention 衰减"前提**；此条工程实证强化 Layer 2 必要性（不是只对 Anthropic 有效，DeepSeek 路径同样需要 boundary reinjection）
- DeepSeek-reasoner（思考模式）下 `temperature/top_p/presence_penalty/frequency_penalty` 不生效、`logprobs` 触发错误——Layer 3 logprobs 增强信号若要启用，必须在 thinking-disabled 调用路径下；Layer 1/2/5 不受影响
- DeepSeek prefix completion 是 **Beta**——切 `base_url=.../beta` 与 prompt cache、context caching 的兼容性需在 `provider_mode == "native-beta"` 路径下灰度观测；与 [services/llm/cache_diagnostic.py](../../services/llm/cache_diagnostic.py) 既存 token-prefix matched cache 模型对位

#### 复审最终结论

- **v2 §Issue 4 5 层方案在 DeepSeek 路径下 100% 可平移**——无层次需要重写；4A Layer 1+2+3+5 推荐结论保持
- **4C fine-tune** 评级**不变**（仍不推荐当下做），但**条件下放**仅在 DeepSeek 路径生效；Anthropic 路径维持原版评级
- **新增 1 条灰度观测项**（不改方案，仅记录）：Layer 2 user-role anchor reinjection 与 [services/llm/cache_diagnostic.py](../../services/llm/cache_diagnostic.py) DeepSeek 端 prompt cache 命中率的相互作用——若 cache 命中率因 anchor 注入显著下跌（DeepSeek 计费按 cache hit/miss 分档），需要 `anchor_signature_stable_across_hour` 作为 anchor 内容稳定化策略的前置约束；Anthropic 路径同理但 cache 计费档差异更小
- **§0 表勘误**：`logprobs / top_logprobs` 在 DeepSeek 路径下"暴露但条件性可用"（thinking-disabled）；其余 Anthropic 不可行项在 DeepSeek 官方 API 路径下**结论一致**——`logit_bias` 仍非根治原语；`activation capping / split-softmax / Outlines / XGrammar / Persona-judge` 仅在自托管推理时打开

---

## Issue 5 — OOV slang reflex（P1）

### 方案 5A — **gate 输出二维 confidence + term_lookup cascade**（推荐）

**形态**：

- gate prompt 输出从单维 `confidence` 升级到二维 `(specification_confidence, model_confidence)` + 新增 `unknown_terms: list[str]`（gate 自评本条 user message 含哪些它不懂的 token）
- 新建 `services/term_lookup.py`：cascade ① `SlangStore.lookup(group_id, term)` → ② `SlangStore.lookup(global, term)` → ③ `WebSearchTool.execute("{term} 是什么意思 二次元 / 游戏黑话")`，hard cap 2 次防 retry 循环
- 命中后把 `{term}={meaning}` 写回 system block 2（复用 [services/block_trace/slang_provider.py:40-90](../../services/block_trace/slang_provider.py#L40-L90) 现有注入路径）
- gate 重判一次（hard cap 1 次）；augmented confidence 过阈值则 fire
- metrics：`unknown_term_density` + `term_lookup_hit_rate`

**成本**：~250-350 行（gate prompt 升级 + term_lookup cascade + slang_provider 复用 + metric）+ 测试。

**优势**：

- 引用 arxiv 2511.08798 SAGE 二维 uncertainty + EVPI / Agentic RAG cascade（vector → graph → web）/ Tiny-ReAct-Agent / OOV 综述
- 与 F2 / F3 / F7 共 B 簇骨架；gate 改完一次性吃下"低信号 / 多消息合并 / OOV"三件
- 业内主流做法（Agentic RAG 从 2024 开始已是新范式）

**风险**：

- gate prompt 改写后**所有现有 gate 行为都需回归**——这是最大风险点；建议同步 ShadowCompareEngine v1/v2 比对 7 天
- web_search 兜底成本要观测——每次 cascade hit 第三层都是 1 次 web 调用
- "什么是 unknown_term" 的判定靠 LLM 自评，初期会过敏（把不那么 OOV 的也标）

**与其他 issue 耦合**：B 簇；和 F2 共享 normalizer，和 F3 共享 router 入口，和 F7 共享 router 入口。

### 方案 5B — slang.db 手动加 op 等条目 + 后续逐步填充

**形态**：管理员发现新黑话就手动 import 进 slang.db。

**优势**：0 代码改动。

**风险**：

- 每次新黑话都要等"出过一次失语"才补（被动）
- B5 channel 设计意图本是"群内学习模式"——bot 自己听到再批准入库——这条手动方案绕过了那条 roadmap
- 不解决"gate 也不懂 op"层的根因——即使 slang.db 有 op，gate 还是会判 confidence=0.10 直接 skip

**评级**：**不推荐独立做**——可作为 5A 落地前的临时止血，5A 落地后这条管理员手动入口仍保留。

### 方案 5C — 强制把 web_search 设默认 always-on

**形态**：每条 user message 都跑一遍 web_search。

**风险**：成本爆炸 + 延迟显著 + 过度 retrieval 反而稀释 prompt。

**评级**：**不推荐**。

---

## Issue 6 — sticker frequency / StickerDecisionProvider 死代码激活（P1）

### 方案 6A — **激活 provider + frequency 阈值映射**（推荐）

**形态**：

- 在 [services/llm/client.py:2625](../../services/llm/client.py#L2625) `kaomoji_enforce` 同位置调用 `sticker_provider.decide(StickerDecisionContext{...})`
- frequency 设置升级为 `send_probability` 阈值映射：`rarely / normal / frequently → 0.85 / 0.55 / 0.30`
- 新建/扩展 `services/sticker/state.py`：`(group_id, user_id) → last_sent_at` 持久化（支撑 deterministic cooldown）
- LLM 仍可主动调 send_sticker（保留 tool_call 路径）；force-trigger 由 provider 主导
- 保留 `kaomoji_enforce` 作 tool_call source 的兜底（与 provider 分工互补）

**成本**：~100-150 行 + 一份 state 持久化 + `tests/test_sticker_decision_provider_integration.py`（覆盖 cooldown / mood / frequency 各 case）。

**优势**：

- 引用 chrimage/discord-emoji-react-bot 双 LLM call 架构 / eleata/resilient-llm-router 状态机 cooldown / ilyajob05/emo_bot 多轴 deterministic / arxiv 2605.00737 LLM Tool Calling quality + necessity 二维 / PromptQL architectural dispatch / medium production tool-calling
- 死代码激活成本可控（203 行已就位，差 wiring）
- 一次到位补回 deterministic frequency policy；为后续"贴纸冷却 / 心情联动 / 关系阶段联动"提供一致入口

**风险**：

- provider 已有 `_COLD_MOODS` 等 gate，激活后会让"冷场段不发贴纸"——和当前"频繁发"行为对比可能让用户觉得"变冷淡了"，需要灰度观测一周
- frequency 阈值映射是初稿，需要根据真实命中率回调
- 和 humanization part6 mood gate 的耦合点要测——两个 layer 都看 mood，确保不冲突

**与其他 issue 耦合**：D 簇独立；不依赖 A/B/C。

### 方案 6B — 把 frequency 默认改 normal 让"应发率"对齐"实发率"

**形态**：[plugins/sticker/plugin.py:47](../../plugins/sticker/plugin.py#L47) `frequency: str = "normal"`。

**优势**：1 行改动。

**风险**：治症状不治根因——v4-flash 对 prompt 命令的执行率仍然 < 20%，"normal" 下也会偏低；以后想推"激进版"还是只能靠 deterministic policy。

**评级**：**不推荐**——除非用户明确说"先把行为对齐预期，结构性补全等以后"。

### 方案 6C — 在 prompt 里加更强的"必须发"命令

**形态**：在 `_STICKER_FREQUENCY_PROMPTS["frequently"]` 加更多大写字母 / 重复句。

**风险**：和 issue 1/4/6 同型——v4-flash 对强命令型 prompt 的命中率不稳。已经测过的失败模式。

**评级**：**不推荐**。

---

## Issue 7 — 多 bot 互引死循环（P0）

### 方案 7A — **BotPairLoopGuard sliding-window + cooldown**（推荐）

**形态**：

- 新建 `kernel/bot_pair_guard.py`：`(group_id, sorted([self_id, other_id])) → deque[ts]` keyspace
- API：`record_inbound(gid, sender_id) / record_outbound(gid, target_id) / is_suppressed(gid, peer_id) -> bool`
- pair key 排序两端 bot user id——反向也命中同一 counter
- 越过 `max_per_minute`（默认 3）即 `cooldown_seconds`（默认 60）双向静音；self-pair 短路（保护单 bot 部署）
- `kernel/router.py` group_listener 入口检查 `is_suppressed`，命中即 drop（不进 scheduler）；`services/reply_workflow.py` outbound 钩子调 `record_outbound`
- config.json 顶层 `bot_pair_guard: { enabled, max_per_minute, cooldown_seconds, known_other_bots: { gid: [other_id, ...] } }`
- **可选 phase 2**：known_other_bots 自动学习——sliding window 内连续 5 次形成 pair pattern 即自动加入；admin SPA 编辑确认/移除

**成本**：~250-350 行 + 测试（互引 4 条触发 cooldown / cooldown 内单方继续也压 / self-pair 不触发 / 跨群独立 / TTL 自愈 / 人类 @+引用不被误压）。

**优势**：

- 引用 openclaw#80719 实测案例（"42+ iterations between a relay bot and the gateway bot... burning ~80 duplicate messages worth of tokens"）+ HammerMei/agent-chat-gateway 三层退出 + dev.to/pratikpathak/medium Stateful Circuit Breakers + stackoverflow/reddit Discord 经验
- pair key 排序的设计是工程界已验证的"对称性 + 自愈" 双重特性
- 与 F3 共 router 入口骨架；与 F2/F5 共 B 簇

**风险**：

- known_other_bots 列表手工维护；自动学习上线前每个新 bot 接入需要管理员补一次
- 60s cooldown 期间双向静音会让群里**人类用户**也短暂感受不到 bot 回复（如果 cooldown 期间有人 @bot 也被 drop）——需要细化策略：cooldown 仅压 pair 之间，不压人类发起的消息
- 与 F3 coalescer 同时落地需要协调：guard 先于 coalescer，guard suppressed 直接 drop，不进 bucket

**与其他 issue 耦合**：B 簇；与 F3 共骨架。

### 方案 7B — group config `blocked_users` 手动封禁

**形态**：管理员发现循环时手动把对方 bot QQ 加入 `group.overrides.{gid}.blocked_users`。

**优势**：0 代码改动；现有机制。

**风险**：

- 事后封禁——损失已经发生（token / 群成员观感 / 频控触发）
- 每个新对方 bot / 每个新群都要再补一次
- 没有"对称性"——只压一个方向，对方 bot 仍会被自己的 reply 触发回复，但因为我们这边静默才停；脆弱

**评级**：**不推荐独立做**——作为 7A 落地前临时止血保留；7A 落地后此机制仍然有用（管理员仍可手工指定 bot ID 进 known_other_bots）。

### 方案 7C — LLM-level self-termination token

**形态**：在 prompt suffix 加"if you detect this is becoming a loop, end with `<<silence>>`"，bot 输出该 token 即 drop reply。

**优势**：理论优雅——LLM 自己感知循环。

**风险**：

- v4-flash 对 prompt-only 信号已多次实测不稳（issue 1/4/6/8 同型）
- 单边自终止——只有自己这边停了；对方 bot 仍可能继续轰炸
- 不可观测；无 metric

**评级**：**不推荐独立**——可作为 7A 的辅助层（agent-chat-gateway 三层方案就是用此作 Layer 1，但**主防线在 Layer 2 turn-budget**，等价于 7A 的 sliding-window）。

---

## Issue 8 — schedule oversharing（P1）

### 方案 8A — **第一刀 prompt 改 + 第二刀 detector 双层**（推荐）

**形态（第一刀，立即可做）**：

- 删 [config/soul/instruction.md:342](../../config/soul/instruction.md#L342) 的"具体日程枚举"段，保留"心情基调影响说话节奏"抽象部分
- [config/soul/SKILL.md:469](../../config/soul/SKILL.md#L469) 同步改
- persona v2 source.md `已知事实` 中"WxS 成员 / 学校 / 日程槽位"挪到 §1.3 voice exemplars——只在用户 query 触发时回忆，不作 always-on declaration
- 改写示例（参 dev.to billhongtendera 实战）：
  - 原：`你每天都有具体的日程——就像真实的人一样，你会经历起床、上课、排练、休息等不同时段`
  - 改：`你说话的节奏会随你当下的状态变化——疲惫时短一些、兴奋时跳一些、放松时拖一些。当对话自然引到你最近在忙什么时再具体讲，否则不主动报作息。`

**形态（第二刀，进 part6 / A 簇）**：

- 在 reply 出口（issue 1 guardrail 骨架）加 `unsolicited_schedule_detector`：检测 `user_msg 不含时段询问关键字 + bot reply 含时段词（"今天/早上/中午/晚上/排练/课/睡觉" 等）` 的并发条件，命中即 dampen（重写裁短 / 合并到下一段）

**成本**：第一刀几乎纯 prompt（不动代码 / config）；第二刀复用 issue 1 guardrail 骨架，~50-80 行 + 测试。

**优势**：

- 引用 arxiv 2602.13516 SPILLage（content × behavior 二维；behavioral 5×；前置过滤 +17%）+ dev.to billhongtendera "facts braided into voice"（before/after 改写示例）+ Anthropic PSM Level 2 +60% drift reduction + agent-character-design Field Guide + OWASP LLM02:2025
- 第一刀和 issue 4 共 source 重写骨架——一次改 source 解决两个 P1
- 第二刀和 issue 1 共 guardrail 骨架——A 簇内顺手做

**风险**：

- 第一刀的"voice 改写"需要联合 part6 source-side generation 一起做；单独改容易和 part6 source 节奏打架
- 第二刀的关键字列表（"今天/早上/排练/课/睡觉"）会有漏召（用户 voice 里也常出现）；需要 ShadowCompareEngine 灰度比对一周

**与其他 issue 耦合**：第一刀 = C 簇（与 F4 共）；第二刀 = A 簇（与 F1 / F9 共）。

### 方案 8B — 仅做第一刀（删枚举段，不加 detector）

**形态**：只删 instruction.md:342 + SKILL.md:469 + 挪 source.md 已知事实段。

**优势**：成本最低，纯 prompt。

**风险**：

- LLM 还是可能从 voice exemplars 自由生发"今天排练" 类内容——declaration→over-reflection 路径未被防线 1 完全堵死
- 没有"行为规约"层——如果 voice 改写不到位，问题会复发；但靠 voice 改写本身就是软约束
- A 簇骨架机会浪费

**评级**：劣于 8A——**仅在用户认为"detector 风险大于收益"或想先观察一周再判定时使用**。

### 方案 8C — 仅做第二刀（保留 instruction.md 不改）

**形态**：留 instruction.md:342 不动，只在出口加 detector。

**优势**：不动 source 节奏，第二刀立即可做。

**风险**：

- 治标——LLM 内部仍按 declaration 选词，每次都被改写，token 浪费
- declaration 在 prompt 里始终在场，detector 永远在工作
- 与 issue 4 source 重写不协调

**评级**：劣于 8A——**仅在 source 改写阻塞时（part6 节奏未到）作为临时止血**。

---

## Issue 9 — ☆/✨ 异常符号（P3，用户存疑）

### 方案 9A — **soft-watch 30 天，挂 issue 1 sentinel registry**（推荐）

**形态**：

- 复用 issue 1 的 `sentinel_registry`，加 watcher 集合：`✨` action=`warn_only`（不 strip 不 redact，仅记 metric）
- 30 天数据：① 出现频率 ② 是否伴随 sticker description tag 残留 ③ 是否和 issue 1 同窗口爆发
- 数据收集后再判定升级路径：
  - 若证据指向 sticker description 回流 → 升 `strip` / 入 issue 1 黑名单
  - 若证据指向 LLM 自由发挥 → 加入 `_DECOR_RE` 白名单（即"装饰符可保留"）
- ☆ 频率回归保险丝：`tests/test_persona_marker_frequency.py`——sample 100 条历史 reply，验证「哇嚯☆」落在 1/4 - 1/8 区间，超频/欠频告警

**成本**：~5-10 行附加到 issue 1 registry + 一份 ☆ 频率测试。

**优势**：

- 不下结论，符合用户"存疑"判定
- 0 风险（warn_only，不改写）
- 顺带建立"将来发现新泄漏字符直接加 config"的可扩展模式

**风险**：

- 30 天 baseline 期间用户可能再次复读戏仿——若数据越发支持"是 bug"，需要快速升级
- ☆ 频率测试的"100 条历史 sample"需要清洁样本源；早期 sample 可能受 humanization part2/3 的中间态污染

**与其他 issue 耦合**：A 簇；附属 issue 1。

### 方案 9B — 立即 strip ✨（按 issue 1 黑名单处理）

**形态**：把 ✨ 直接放入 issue 1 sentinel registry 的 `strip` 集合。

**优势**：立刻生效，眼不见为净。

**风险**：

- 用户判定是"存疑"——立即 strip 等于代用户做决定
- ✨ 可能是 LLM 合法自由发挥；strip 后会丢失情绪表达
- 缺乏数据支撑

**评级**：**不推荐**——除非用户明确改判"就是 bug，立即清掉"。

### 方案 9C — 把 ✨ 加入 `_DECOR_RE` 白名单

**形态**：[services/humanization/scorer.py:18](../../services/humanization/scorer.py#L18) `_DECOR_RE = re.compile(r"[☆♪✦★♡♥✨]")`。

**优势**：明确认可"✨ 是装饰，可以保留"。

**风险**：

- 同样代用户做决定（默认它是合法装饰）
- 若实际是 sticker description 回流，等于把 bug 合法化

**评级**：**不推荐**——除非 9A 的 30 天数据支持"自由发挥"假说。

---

## Issue 10 — 近重复回应 / 自我相似度盲区（P0）

### 方案 10A — **n-gram dedup gate + per-group `asyncio.Lock` 双层**（推荐）

**形态**：

- 新建 `services/llm/dedup_gate.py` `NearDuplicateGate`：
  - `is_near_duplicate(reply: str, last_assistant_text: str, *, ngram=5, threshold=0.4) -> tuple[bool, float]`
  - 算法：normalize（标点剥离 / 全半角统一 / 空白合并）→ n-gram 集合 → Jaccard 系数；短路：完全包含且占比 > 0.6 直接判重
  - 仅做"相邻 turn"比较（参 microsoft#4716 last_key），不做全局历史
  - 命中三档 action（config 可选）：① `drop` 整段丢弃 ② `rewrite` 调一次"换个说法"（hard cap 1 次） ③ `merge` 截短并合并到下一段
- 新建/扩展 `services/scheduler/group_lock.py`（或在 `_GroupSlot` 加 `asyncio.Lock`）：
  - `services/scheduler.py:_do_chat` 入口 `async with lock:`，覆盖 prompt-build → LLM call → segments persist 整段
  - 当前 `running_task` 串行只覆盖到 task done；新 lock 覆盖到 persist commit——堵 mechanism A（concurrent prompt snapshot 漏看 last assistant turn）
- post-LLM 链尾接入（A 簇位置）：在 `services/scheduler.py:_do_chat` LLM 返回后、segmentation 前调 dedup_gate
- PromptBuilder 加一行轻量 self-recall hint（参 arxiv 2605.15102）：仅作辅助，主决策权归 deterministic gate
- config.json 顶层加 `dedup_gate: { enabled, ngram, threshold, action: "drop|rewrite|merge", lookback_turns: 1 }`
- metrics：`services/block_trace/store.py` 加 `near_duplicate_hits / near_duplicate_dropped / near_duplicate_rewritten`，灰度 7 天观测命中率

**成本**：~250-350 行（dedup_gate + group_lock + wiring + config + metric）+ `tests/test_dedup_gate.py`（覆盖 Jaccard / drop/rewrite/merge 三种 action / lock 序列化 / cancel-path D2 测试）。

**优势**：

- 引用 microsoft/agent-framework#4716 consecutive-duplicate skip / arxiv 2605.15102 SRT 内生 recall / emergentmind 2602.24287 context pollution + AO prompting / arxiv 2504.20131 LZ + oobabooga DRY n-gram penalty / arxiv 2112.08657 self-vs-partner 分类 / openclaw#51979 concurrent appendMessage mutex / openai/codex#14318 commit barrier
- A 簇骨架共用——和 F1 sentinel registry / F8 第二刀 detector / F9 ✨ watcher 同位置挂；一次改 `_send_to_group` 前的 hook 通道，四件并行
- per-group lock 与 F7 BotPairLoopGuard 同模式（router 入口序列化）——B 簇骨架内可一次实现
- 可扩展：threshold / lookback_turns 灰度调参；初期 0.4 / 1 turn，看曲线再调

**风险与代价**：

- threshold 调参敏感——0.4 偏严会误伤合法深聊（用户反复追问同一话题时 bot 合理重提）；建议 ShadowCompareEngine 灰度比对 7 天
- `drop` action 在簇 1（自我介绍重复）合理但在簇 2（schedule 复读）可能让 bot 看起来突然沉默；初期推荐 `rewrite`，hard cap 1 次防 retry 循环
- per-group lock 覆盖 prompt-build → LLM call → persist，整段持锁可能让 LLM 返回慢时阻塞下一触发；需要对 LLM call 设 `wait_for` timeout（D2：cancel-path 测试必须覆盖 lock 持有中被 cancel 的场景）
- 与 F3 coalescer 同时落地需要协调：lock 在 coalescer 出 bucket 之后、`_do_chat` 之前——避免 lock 也覆盖 bucket 等待时间

**与其他 issue 耦合**：

- A 簇（dedup gate 部分）：与 F1 / F8 第二刀 / F9 共出口 guardrail 通道
- B 簇（group lock 部分）：与 F7 共 router 入口序列化思想；与 F3 coalescer 接口处需要协调
- 与 F8 第一刀（schedule oversharing source 改写）有放大关系：F8 第一刀压"重复素材源头"，F10 兜底"复读触发条件"——两者并修最稳

### 方案 10B — 仅 dedup gate（不动 lock，纯出口去重）

**形态**：只做 `NearDuplicateGate` post-LLM 检测，不在 `_do_chat` 入口加 lock。

**优势**：成本最低，只在 A 簇骨架内做；不动 scheduler 内部状态机；约 100-150 行净增。

**风险**：

- 只堵机制 B（自我相似度盲区），不堵机制 A（concurrent prompt snapshot 漏看）；簇 3（~15s 内 3 次出文）可能仍因 prompt snapshot 过期产生"看起来没重复但内容仍漂"的输出
- LLM 已经在重复路径上消耗 token，dedup gate 命中后还要么 drop 要么 rewrite——`drop` 浪费 LLM 一次 call，`rewrite` 多一次 call
- 无法触达"持久化窗口期"问题

**评级**：劣于 10A——**仅在用户认为"persistence + lock 改造风险大于收益"或想先观察 dedup gate 单独落地效果再判定时使用**。10A 落地仍可保留 dedup gate 部分作 belt-and-suspenders；10B 不能反向升 10A（lock 必须重新设计）。

### 方案 10C — 仅在 prompt 加"self-recall hint"（纯 prompt 层）

**形态**：在 system prompt 末尾加一段 "回复前先想想你最近 1-2 条消息说了什么，避免直接复述同一内容 / 同一身份声明"。

**优势**：0 代码改动，纯 prompt。

**风险**：

- v4-flash 对 prompt-only 控制已多次实测不稳（issue 1/4/6/8 同型）
- 不堵机制 A（与 prompt-only 无关，是 prompt snapshot 漏看 last turn）
- 不可观测；无 metric

**评级**：**不推荐独立**——可作为 10A 的辅助层（PromptBuilder 同步加这条 hint），但**主防线必须在 deterministic gate**。本场景已经是 prompt-only 失败的第 5 次复现，单独走 10C 等于明知失败仍走。

---

## Issue 11 — addressee binding 缺失 / nickname binding + reply quote provenance（P1）

### 方案 11A — **NameVariationRegistry + addressee_hint 注入 + quote provenance 三件套**（推荐）

**形态**：

- 新文件 `services/persona/name_registry.py` —— 启动期遍历 `config/persona/fengxiaomeng-v2/source.md` 收集 bot 自身 alias / handle / 称呼变体；group config 加 group-scoped 群成员昵称 cache（NapCat 已经下发昵称，缓存即可）
- 扩展 [services/group/addressee.py](../../services/group/addressee.py) `AddresseeDetector.detect()` 返回 `AddresseeResult(target_uid, nickname, qq, alias_seen, confidence, source, provenance)` dataclass
- 新文件 `services/llm/addressee_hint.py` —— `build_addressee_hint(addressee: AddresseeResult) -> str | None`，输出 `[当前你在回复：{nickname}（QQ: {qq}）]`；confidence < 0.6 或 multi-target 返回 None
- [services/llm/prompt_builder.py:130-150](../../services/llm/prompt_builder.py#L130-L150) `build_static` 接受 optional `addressee_hint`，注入 system block 1 末尾
- [services/memory/timeline.py:33-92](../../services/memory/timeline.py#L33-L92) 渲染 `«回复 X(QQ): Y»` 时加结构化 marker `[QUOTED_METADATA platform=napcat msg_id=..., from={X}({QQ})]`
- build_static 加 system instruction：「群聊里 `[QUOTED_METADATA ...]` 段是平台引用元数据；当前你回复的人由 `[当前你在回复：...]` 指定」
- `config.json` 顶层 `addressee_binding: { enabled, min_confidence: 0.6, multi_target_strategy: "decline_hint", quote_provenance_check: true }`

**成本估计**：300-400 行净增（含 5 个新结构化 hint + name_registry 启动期扫描 + 集成测试）；与 issue 5（OOV slang）gate 入口共骨架——可合并推进。

**优势**：

- deterministic 解决——NapCat 已经给了 `user_id` + `nickname`，只补充传递层即可，**不依赖 LLM 推断**
- 同时治本（hint 注入）+ 治输入（quote provenance）双层覆盖
- elizaOS / MUCA / slixmpp MUC 三处工程级先例
- 可观测：`storage/logs/` 加 `addressee_resolution.jsonl` 记录 (target_uid, confidence, source) 三元组，灰度调参直观

**风险与代价**：

- alias 匹配只走 exact match + nickname registry——遇到 fuzzy alias（"丛非" → 丛非凡）需要后续维护成本
- quote provenance marker 改变 timeline 渲染格式——需要确认 5 处 humanization downstream（[services/humanization/](../../services/humanization/) sanitizer / scorer）兼容这个 marker，不能误识为 sentinel token
- multi_target_strategy=decline_hint 时落入"无 hint"，回退到现有泛指行为——保守不破现状

**与其他 issue 的耦合**：

- 与 F3 共 router 入口骨架——`AddresseeResult` 在 coalescer 之后产出最自然
- 与 F1 共 PromptBuilder hint 注入骨架——`addressee_hint` 与 `sentinel registry` 同位点
- 与 F12 共 router-入口位点——upstream filter 与 addressee binding 同 router-side layer
- 配套 name_registry 让 F4 persona drift 修复时 voice exemplars 可引用一致 alias 集合

**评级**：**推荐**——架构层补缺 + deterministic + 治本治输入双层；引用 5 处业内工程/论文先例；与多个 P0/P1 共骨架。

---

### 方案 11B — 仅 prompt 加 directive（不动架构）

**形态**：仅在 [services/llm/prompt_builder.py:130-150](../../services/llm/prompt_builder.py#L130-L150) 加一行 system instruction：「回复时若群里有具体发言者，请用 `昵称：` 开头明确指向；不要笼统用『你』或『你们』」。不改 timeline、不加 name_registry、不改 AddresseeDetector 输出。

**成本估计**：1 行；零代码改动。

**优势**：成本最低；可作为 11A 的辅助层。

**风险与代价**：

- v4-flash 对 prompt-only 控制命中率 < 20%（issue 1/4/6/8/10/13 同型，本仓 6 次复现）——明知失败模式仍走
- 不堵 reply injection（mechanism B 不动）——`«回复 X(QQ):...»` 字面化进 prompt 风险保留
- 不可观测；无 metric——灰度后无法量化效果
- multi-user 复杂场景（issue 7 双 bot @ 同时）仍无 deterministic 兜底

**与其他 issue 的耦合**：与任何方案都不冲突；可作为 11A 落地前的临时缓解。

**评级**：**不推荐独立**——同 issue 1C / 6C 评级，作为 11A 落地后的辅助 prompt 层可保留，但本身不是修复。

---

### 方案 11C — 在 timeline rendering 加 sender→addressee 边（仅渲染层改）

**形态**：[services/memory/timeline.py:33-92](../../services/memory/timeline.py#L33-L92) `merge_user_contents` 渲染时除 `{tag}{speaker}: {content}` 外，对识别出的 `«回复 X(QQ): Y»` 增加显式 sender→addressee 标注：`{speaker_A} → {addressee_B}: {content}`；不动 PromptBuilder、不加 name_registry。

**成本估计**：50-80 行（仅 timeline 渲染 + 测试）。

**优势**：

- 比 11B 重一些但仍轻量——可观测 sender→addressee 边
- 只改一个文件，blast radius 小

**风险与代价**：

- 仍无显式"current addressee" hint——LLM 仍要从 timeline 推断
- 渲染格式变化要在 humanization 链下游所有消费者验证兼容性
- 不堵 reply injection（与 11B 同）
- 不解决 nickname binding 不一致（多昵称 / alias 场景）

**与其他 issue 的耦合**：可作为 11A 的子集——如果只想做 timeline 渲染那一步，11A 拆分时此为最小可发布单元。

**评级**：**部分实现**——优于 11B 但弱于 11A；适合"先做最低 layer，下次迭代补 hint"的渐进路径。

---

## Issue 12 — 上游工具命令未屏蔽 / known_other_bots / configurable upstream filter（P1）

### 方案 12A — **`upstream_command_filter` 配置段 + UpstreamCommandFilter 服务**（推荐）

**形态**：

- 新文件 `services/upstream_filter.py` —— `UpstreamCommandFilter` 类暴露 `should_drop(event: GroupMessageEvent) -> tuple[bool, str | None]`
- [kernel/router.py](../../kernel/router.py) `group_listener` 入口插一道：`drop, reason = _upstream_filter.should_drop(event); if drop: log.debug("upstream_filter dropped: %s", reason); return`
- `config.json` 顶层加：

  ```json
  "upstream_command_filter": {
    "enabled": false,
    "command_patterns": ["^#\\w+", "^!\\w+"],
    "known_other_bots": [],
    "drop_silently": true,
    "log_drops": true
  }
  ```

- group-level override：`group.overrides.<gid>.upstream_command_filter.known_other_bots` 可单独配置每群"共存 bot 黑名单"
- log_drops=true 时落 `storage/logs/upstream_filter_drops.log`（结构化 JSONL：`{ts, gid, sender, content_prefix, reason}`）
- admin SPA 加编辑面板（D6：admin 前端独立 build，不 rebuild bot）

**成本估计**：200-300 行净增（含 admin SPA 一个新 panel + group override 集成）；与 F7 共 known_other_bots 数据结构——合并推进显著降低成本。

**优势**：

- 默认 OFF（`enabled: false`）满足用户"默认关闭"诉求，与现状兼容；用户灰度群按需打开
- AstrBot#6505 / openfang#403 业内已落地等价方案
- group-level override 让"协作 bot 群"vs"纯灰度群"独立配置
- 可观测——drop log 直接审计 false positive
- known_other_bots 与 F7 共数据结构，落 F12 时顺手为 F7 BotPairLoopGuard 备 known_other_bots 列表（D1 同模式扫描通用）

**风险与代价**：

- 默认 enabled=false 时**与现状完全一致**——存在但不生效的 layer 需要文档明确，避免下次维护者以为已修复
- command_patterns 有误伤风险——用户合法发"我看 #napcat 输出..."这种含 `#napcat` 的对话会被 drop；mitigation = drop 仅在文本以模式开头时触发（`^#\\w+` 而非 `\b#\\w+`），讨论性引用不命中
- known_other_bots 列表需要手动维护——QQ 号集合若下次新 bot 进群需要更新 admin SPA

**与其他 issue 的耦合**：

- 与 F7（multi-bot loop guard）共 known_other_bots 数据结构——同时落地最经济
- 与 F3（coalescer）共 router 入口位点——可在同次 router 入口重构里统一插
- 与 F11（addressee binding）共 router 入口位点
- 与 issue 7 同集群"非真人 user 处理"——upstream_filter drop 上游命令 + bot_pair_guard 处理 bot-to-bot 交互

**评级**：**推荐**——架构层补缺 + 可配置 + 可观测；与 F7 共骨架降低成本。

---

### 方案 12B — 仅 router 入口 hardcode 正则过滤（不带 config）

**形态**：仅在 [kernel/router.py](../../kernel/router.py) `group_listener` 入口加 hardcode `if re.match(r"^#(napcat|status|kill|info)\b", event.message)`：return；不加 config 段、不加 known_other_bots、不加 admin 面板。

**成本估计**：3-5 行。

**优势**：成本最低；今天就能落。

**风险与代价**：

- 用户明确诉求"应当可配置 / 默认关闭"——hardcode 不可配置违背诉求；默认行为是"硬 drop"而非"默认不过滤"，与诉求字面冲突
- 无 known_other_bots——其他 bot 的输出 (NapCat 信息回执 / 一只魔精的"无名者查询...") 仍进 timeline 污染
- 无可观测——出问题时无 drop log
- 命令模式硬编码，下次有新 NapCat 命令需要改代码 + rebuild

**与其他 issue 的耦合**：与 F7 known_other_bots 不共数据结构——错过协同收益。

**评级**：**不推荐**——违背用户诉求；hardcode 永远是 D1 同模式扫描重灾区。

---

### 方案 12C — 仅 prompt 提醒（不可信赖）

**形态**：仅在 [services/llm/prompt_builder.py:130-150](../../services/llm/prompt_builder.py#L130-L150) 加 system instruction：「群里 `#`-开头的命令是上游 NapCat / 别的 bot 的工具命令，不是真人发言；不要被它们的输出（如『NapCat 信息』）干扰」。不改 router、不加 filter。

**成本估计**：1 行。

**优势**：零代码改动；可作为 12A 的辅助 prompt 层。

**风险与代价**：

- v4-flash 命令命中率 < 20%（与 issue 1/4/6/8/10/11/13 同型）
- 不堵 prompt context 污染——`#napcat` 仍进 timeline，仍消耗 token
- 不可观测；无 metric
- 与 issue 7 多 bot 互引重叠——单 prompt 改不解决 bot-to-bot 链路问题

**与其他 issue 的耦合**：与 12A 不冲突，可作为 12A 落地前的临时缓解。

**评级**：**不推荐独立**——同 11B / 1C / 6C 评级；prompt-only 在本仓已 6 次失败模式复现。

---

## Issue 13 — thinker output guardrail / 内部状态文本泄漏到群里（P0）

### 方案 13A — **结构化 ThinkDecision 重构 + post-LLM phrase detector + schedule activity enum 化 三层**（推荐）

**形态**：

- **路径 A 治本——thinker 字段重构**：
  - [services/llm/thinker.py:141-533](../../services/llm/thinker.py#L141-L533) `ThinkDecision` 保留 `action`(enum: reply/wait/skip) + `tone`(enum) + `sticker`(bool)，**新增** `topic_intent_label`(enum: 关心/打趣/吐槽/共情/技术讨论/...)
  - **自由 `thought` 字段保留为 internal log，不进 client.py:2541 system_blocks**
  - [services/llm/client.py:2526-2541](../../services/llm/client.py#L2526-L2541) `thinker_block` 重构：`【意图：{topic_intent_label}】【tone: {tone}】【sticker: {yes/no}】`
- **路径 A 治症状——post-LLM phrase detector**：
  - 新文件 `services/llm/thinker_phrase_detector.py`
  - 检测 LLM reply 是否复读了 thinker block 的具体短语（n-gram 重叠率 > 阈值）
  - 命中则改写或 drop——与 F1 sentinel registry 共出口骨架（A 簇）
- **路径 B 治本——schedule activity enum 化**：
  - [plugins/schedule/generator.py:23-56](../../plugins/schedule/generator.py#L23-L56) `_SCHEDULE_SYSTEM_PROMPT` `activity` 字段改 enum（`work/rest/practice/commute/...`）；自由文本描述仅作 internal log
  - [services/llm/client.py:853-867](../../services/llm/client.py#L853-L867) schedule 注入处也改 enum 显示
- **路径 B 治症状——schedule_activity_detector**：与 F8 第二刀共 detector 骨架
- `config.json` 顶层 `thinker_output_guardrail: { thought_in_prompt: false, structured_decision_only: true, post_llm_phrase_detector_threshold: 0.4 }`

**成本估计**：350-450 行净增（含 ThinkDecision 字段迁移 + thinker_phrase_detector + schedule enum 化 + 一套测试）；与 A 簇 F1/F8 第二刀/F9/F10 dedup gate 共骨架——同次落地最经济。

**优势**：

- 双层防线——治本（structured-only boundary）+ 治症状（phrase detector）；前者防泄漏、后者防漏网
- 5 处业内工程/论文先例（Leaky Thoughts / llm-think-tag-strip / CoST / Illegible CoT / Reasoning Trace Privacy）
- 与 F1 sentinel / F8 第二刀 detector / F9 ✨ watcher / F10 dedup 同 A 簇骨架——5 件出口 guardrail 一次落地
- 可观测：thinker_phrase_detector 命中数 + drop/rewrite 计数进 `services/block_trace/store.py`
- ThinkDecision 字段重构同时降低 thinker prompt 模糊度——thinker 输出更可预测，下游一致性更高（与 humanization part 1-3 兼容）

**风险与代价**：

- ThinkDecision 字段重构是 breaking change——需要 thinker prompt 同步重写（要求 LLM 输出严格 enum 格式而非自由文本）；mitigation = thinker prompt 用 JSON schema 约束，retry-on-parse-fail 兜底
- topic_intent_label enum 集合需要谨慎设计——太少表达不够丰富，太多 v4-flash 命中率下降；建议 8-12 个标签，对照现有 thinker logs 的 thought 内容聚类得出
- schedule activity enum 化破多样性——需要在 prompt 显示时回填 voice exemplars（与 F4/F8 第一刀 source 重写共骨架）
- post_llm_phrase_detector 阈值 0.4 是猜测值——灰度灰度调参 7 天

**与其他 issue 的耦合**：

- 与 F1 sentinel registry / F8 第二刀 schedule_oversharing detector / F9 ✨ watcher / F10 dedup gate 共出口 guardrail 骨架（A 簇 5 件）
- 与 F4 persona drift / F8 第一刀 schedule prompt 改写 共"自由叙事文本→结构化 enum"治本骨架（C 簇）
- 与 issue 1 / issue 8 同集群"omubot 内部信息边界缺位"——三 issue 共组成"内部 → 外部"边界控制
- 与 issue 4 共 ThinkDecision/v2 source 字段重构思想——把"自由叙事"改成"结构化标签 + voice exemplars"

**评级**：**推荐**——双层防线 + 业内工程级先例 + 与 A/C 双簇共骨架；P0 集群（issue 1/8/13 内部信息泄漏）核心修复。

---

### 方案 13B — 仅 post-LLM phrase strip（不动 ThinkDecision）

**形态**：仅落 `services/llm/thinker_phrase_detector.py` —— 检测 LLM reply 是否复读 thinker block 短语，命中改写。**不改** [services/llm/thinker.py](../../services/llm/thinker.py) ThinkDecision 字段、**不改** [services/llm/client.py:2526-2541](../../services/llm/client.py#L2526-L2541) thinker_block 注入形式、**不改** schedule generator。

**成本估计**：100-150 行（含 phrase_detector + 集成测试）。

**优势**：

- A 簇内最小可发布单元——在 F1 sentinel registry 同位点挂上 phrase_detector 即可
- 不改 ThinkDecision 字段——breaking change 风险 0
- 与 F1 共骨架，落地成本极低

**风险与代价**：

- 治症状不治本——thinker thought 仍字面进 system_blocks，**v4-flash 仍可任意 paraphrase**；phrase_detector 是事后补救，n-gram 阈值 0.4 漏检率约 15-25%
- schedule activity 路径完全不动——issue 13 路径 B 漏报
- 与 F8 第二刀 schedule_oversharing detector 部分重叠但不复用——浪费骨架共享机会
- 不解决 thinker thought 模糊性导致的下游一致性问题

**与其他 issue 的耦合**：

- 与 F1 共出口骨架——可作为 A 簇 5 件中的最小子集独立发布
- 与 F8 第二刀部分功能重叠

**评级**：**部分实现**——优于纯 prompt 但弱于 13A；适合"P0 必须本周做但 ThinkDecision breaking change 来不及测"的应急路径。建议作为 13A 的 phase 1 发布（先落 phrase_detector，下次迭代再做字段重构）。

---

### 方案 13C — 关掉 thinker（彻底 bypass）

**形态**：[services/llm/client.py:2526-2541](../../services/llm/client.py#L2526-L2541) 的 `system_blocks = [*system_blocks, thinker_block]` 注释掉；保留 thinker 调用本身（决定 action / sticker / tone）但不把 thought / topic 字段往 system_blocks 喂；schedule 注入路径同样保留。

**成本估计**：1-3 行（注释级改动）。

**优势**：成本最低；今天能落。

**风险与代价**：

- 回退 humanization part 1-3 部分能力——thinker 是 humanization 决策核心，关掉等于让 v4-flash 失去 pre-reply 决策上下文，回复质量退化（issue 4 / issue 10 反向放大）
- 不动 schedule 注入路径——路径 B 漏报
- 与 humanization part 1-3 / part 4-5 已落地能力冲突——需要回退多套 humanization 配置
- 永远关掉而非"按需关闭"——失去 thinker 在合法场景下的价值

**与其他 issue 的耦合**：

- 与 humanization part 1-5 严重冲突——会把 part 1-3 的 ThinkDecision-driven 行为全部退化
- 与 issue 1 / issue 8 集群分离——不能共骨架

**评级**：**不推荐**——治症状的最暴力形式，等于回退已落地 humanization 投资；放本节作为对照组。

---

## Issue 14 — @ 特殊对象但识别到昵称却未真 at（mention wiring 缺失，P1）

### 方案 14A — **mention post-processor + 共建 F11 nickname registry**（推荐）

**形态**：

- 新建 `services/llm/mention_post_processor.py`：reply 文本进 send_queue 前最后一道 layer
  - 输入：`(reply_text: str, group_id: int, recent_speakers: list[GroupMember])`——`recent_speakers` 取自当前 GroupTimeline 最近 N 条消息的 sender 集合（与 F11 addressee binding 共数据源）
  - 解析：扫描 `@昵称` / `@群昵称` 字面量；按 `recent_speakers.member_card / nickname / qq` 三段优先级命中；命中则把字面量改写为 `[CQ:at,qq=<id>]`
  - 输出：改写后文本 + `mention_resolution_meta`（含 `(literal_name, resolved_qq, source)` 列表）进 `services/block_trace/store.py`
- 在 [services/scheduler.py:649-650](../../services/scheduler.py#L649-L650) `humanizer.delay()` 之后、`_send_to_group` 之前插入 `apply_mention_post_processor(reply, group_id, scheduler.timeline.recent_senders(group_id, n=20))`
- 与 F11 addressee binding 共建 nickname registry——F11 落地时 nickname → qq 映射进 `services/group/nickname_registry.py`，本方案直接 import；若 F11 未落地，本方案先实现 inline `recent_senders` 临时方案
- `config.json` 顶层 `mention_post_processor: { enabled: true, fallback_keep_literal: true }`——`fallback_keep_literal=true` 表示无法解析时保留 `@昵称` 字面量不报错

**成本估计**：120-180 行净增（解析器 + 数据源 + 配置 + 4-6 用例测试）；与 F11 共建 nickname registry 时 F11 也省 50-80 行。

**优势**：

- **不要求 LLM 改变行为**——LLM 输出 `@昵称` 字面量是既存事实，post-processor 兜底解析最稳；不依赖 LLM tool-use 的可靠性（Anthropic Tool Use 在 multi-turn 场景偶有 hallucinate tool name 的事故）
- 与 F11 addressee binding 同骨架——B 簇 8 件复用 nickname registry / quote provenance 数据源
- 业内成熟模式：Telegram bot/grammY parseEntity hook、Discord.js mention.parse / replaceMembers、go-cqhttp 的 nickname→qq lookup helper
- 错误恢复友好：解析失败保留字面量比"硬塞错误的 qq id @ 错人"安全得多

**风险与代价**：

- recent_speakers 窗口（n=20）需要灰度调参——窗口太小漏命中、太大可能命中已离开群的旧成员
- 同名歧义场景：群里有 2 个相同 member_card 的人——按"最近发言者优先"策略容易选错；mitigation = 命中歧义时不改写、保留字面量 + warn metric
- 与 humanization 链顺序需要敲定——必须挂在 humanizer.delay 之后（humanizer 不会改写 `@昵称`），但要在 send_queue 之前
- recent_speakers 缓存在 timeline 中，跨 reconnect 需要恢复——SQLite MessageLog 已持久化，启动时读最近 200 条做 warm-up 即可

**与其他 issue 的耦合**：

- **与 F11 共 nickname registry 数据结构**——B 簇 8 件复用骨架最经济；建议与 F11 同次落地
- 与 F12 upstream filter 共"router 入口前 layer"位点——同次 PR 一起做最经济
- 与 F1 sentinel registry / F10 dedup gate 不冲突——本方案位点是 send 前，A 簇 guardrail 位点是 LLM reply 出口

**评级**：**推荐 14A**——单层 layer 治本治症状一次到位，不依赖 LLM tool-use，与 F11 共骨架最经济。

### 方案 14B — LLM tool registration `at_user(qq: int)`

**形态**：

- 在 [services/llm/client.py:1420](../../services/llm/client.py#L1420) `_build_tool_defs` 注册 `at_user(qq: int) -> str` 工具——返回 `[CQ:at,qq=<qq>]` 字符串供 LLM 内嵌进 reply
- 工具描述加入"何时使用：当你需要 @ 群成员时；优先用此工具而非 `@昵称` 字面量"
- prompt 同步更新（[config/soul/instruction.md](../../config/soul/instruction.md)）增加"@ 用户的正确做法"段
- nickname → qq 解析放在 LLM 侧——LLM 通过 `recent_speakers` system_block 知道当前活跃成员的 (nickname, qq) 映射；调用 tool 时传入 qq

**成本估计**：80-120 行净增 + prompt 改写 + 1 套 tool round-trip 测试。

**优势**：

- 架构最干净——@ 行为是 LLM 一等决策，不靠 post-processor 救火
- 与 omubot 已有的 tool 链路（`set_group_ban` / `append_memo` / `save_sticker`）同模式

**风险与代价**：

- **LLM 可靠性风险**——Anthropic Tool Use 在长 prompt + 多 round 场景下偶发 hallucinate tool name / 漏调用；这是业内通病，Anthropic Cookbook 也建议"关键路径加 fallback"
- prompt context 增加——recent_speakers 列表占 system_block 容量，灰度群 5-20 人小规模可控，但破坏 prompt cache
- 不能与 F11 共骨架——F11 是入口侧 binding，本方案是 LLM 主动拿数据；两者不互斥但 cost 各自独立
- LLM 对 `qq` 类型敏感：传 string 还是 int 历史上踩过坑（[services/tools/group_admin.py:63](../../services/tools/group_admin.py#L63) `set_group_ban` 用 int），需要严格 schema 校验

**与其他 issue 的耦合**：

- 与 F11 不冲突但不复用——独立成本
- 与 humanization 输出链不冲突——LLM 出 reply 时直接含 `[CQ:at,...]` 段

**评级**：**次选 14B**——长期架构干净，但短期可靠性弱于 14A；建议作为 14A 的"未来升级路径"而非首选。

### 方案 14C — Hybrid：prompt 提示 + post-processor fallback

**形态**：

- 14A post-processor 全量上线
- prompt 增加"如可能，请直接输出 `[CQ:at,qq=<id>]`；若不知 qq，输出 `@昵称` 字面量我们会兜底"——把 LLM 选择空间留出来
- post-processor 检查到 `[CQ:at,qq=<id>]` 直通；检查到 `@昵称` 字面量 → 解析 → 改写

**成本估计**：14A 成本 + prompt 改写 ~30 行。

**优势**：

- 双保险——LLM 学会输出 CQ 段时直通快路径；不会的时候 post-processor 兜底
- 比 14B 更稳——LLM 漏调用工具时仍有兜底

**风险与代价**：

- prompt 长度增加——recent_speakers 仍需进 system_block；与 14B 同样破坏 prompt cache
- 测试矩阵爆炸——需要分别测 LLM 直出 CQ 段 / LLM 出字面量 / LLM 出错误 qq id 三条路径

**与其他 issue 的耦合**：与 14A 完全相同。

**评级**：**可选 14C**——14A 上线后 30 天观察 post-processor 命中率，若 LLM 输出 `@昵称` 字面量为多数则升级到 14C；不建议首发即 14C。

---

## Issue 15 — 复读插件回复比正常 LLM 回复更慢（humanization 输入选错 + runtime 参数缺失，P2）

### 方案 15A — **输入修正 + 段感知 delay + runtime 参数补齐**（推荐）

**形态**：

- [plugins/echo/plugin.py:189-195](../../plugins/echo/plugin.py#L189-L195) 修复点：
  - **输入修正**：把 `await self._humanizer.delay(echo_key)` 改为基于"段感知文本长度"的输入——抽出段中"用户可见文本"部分（剥离 `[image:sub:hash]` `[at:qq]` `[face:id]` `[json:prompt]` 标记）
    - 新建 `_visible_text_for_humanizer(echo_key: str) -> str`——从 echo_key 还原近似 "可见字符长度"；遇到 image / face / json 段当 `2-3 个字符长度`（与人类阅读这类段的视觉时间近似），at 段当 `len(at_target_nickname)`
  - **runtime 参数补齐**：加入 `**self._humanizer_runtime(group_id)` 同 [services/scheduler.py:649-650](../../services/scheduler.py#L649-L650)；包括 `register / slot / mood / thinking_elapsed_s=0`（echo 没有 thinker 阶段）
  - 顶部 `_humanizer_runtime` helper 在 plugin 内复刻一份（轻量），或抽到 `services/humanizer.py` `runtime_params_for_group()`
- 错误分支处理：`echo_reply.startswith("打断")` 路径同样改为 visible_text 输入

**成本估计**：30-50 行净增 + 3-4 用例测试（visible_text 提取、segment 长度近似、runtime 注入）。

**优势**：

- **针对根因**——echo_key 含 `[image:sub:hash]` 这种 long marker，`len(echo_key)` 远大于"用户视觉感知字符长度"；改成 visible_text 后 `char_delay * len(...)` 才对得上 humanizer 的设计前提
- 同时修两层缺陷——输入选错 + runtime 参数缺失（mood/slot 影响 [services/humanizer.py](../../services/humanizer.py) 的 char_delay 系数）
- 不动 humanizer 自身——风险局限于 echo plugin
- 与 [services/scheduler.py:649](../../services/scheduler.py#L649) 已有的 `humanizer.delay(text, **self._humanizer_runtime(group_id))` 模式一致——D1 同模式扫描结论："scheduler 是好榜样，echo 是异常点"

**风险与代价**：

- visible_text 估算近似——image/face 取 2-3 个字符是经验值；灰度灰度调参 1 周
- echo 是异步 task，`_humanizer_runtime(group_id)` 调用上下文需要同步可用——scheduler 是从 self 拿、echo plugin 需要 ctx 传入；ctx 已有 group_id 不增加签名
- 如果未来加更多段类型（视频段、文件段、合并转发），需要在 visible_text 提取里同步加 case——不是 architectural risk，是 maintenance cost

**与其他 issue 的耦合**：

- 与 humanization part 1-5 不冲突——本方案只动 echo plugin 的输入正确性
- 与 F1 sentinel registry / F11 / F14 不冲突——本方案位点在 humanizer 调用前

**评级**：**推荐 15A**——单点 plugin 修复 30-50 行，架构层无影响，是性价比最高的"小切口治本"。

### 方案 15B — 只补 runtime 参数（不修输入选错）

**形态**：[plugins/echo/plugin.py:191/194](../../plugins/echo/plugin.py#L191) 两处 `delay()` 加 `**self._humanizer_runtime(group_id)`，输入仍是 `echo_key`。

**成本估计**：10-15 行净增 + 1 用例测试。

**优势**：最小切口；mood/slot 修正后回复速度有改善（mood=excited 时 char_delay 系数下降）。

**风险与代价**：

- 不治本——`echo_key` 本身长度问题不解决，长复读仍慢
- 与 scheduler 的 humanizer 调用模式不一致——长期看仍是异常点；下一次 humanizer 调用调研时还会被 D1 扫到二次返工

**评级**：**次选 15B**——仅当用户希望"改最少代码先观察一周"时考虑；否则直接 15A。

### 方案 15C — Architectural unify：所有 humanizer.delay 路径走 send_queue 统一处理

**形态**：

- 移除 echo plugin 内 humanizer 调用——改为把 reply 直接进 send_queue，由 send_queue 统一调用 humanizer
- send_queue 增加 `pre_send_delay` 钩子，统一处理段感知 delay + runtime 参数

**成本估计**：300-450 行（涉及 send_queue 改造 + 所有 plugin 出口路径迁移 + 大量回归测试）。

**优势**：

- 架构最干净——humanizer 只在一个地方调用；所有 outbound 节奏统一
- 解决"未来再加新 plugin 又一次踩坑"的根本问题——D1 同模式预防

**风险与代价**：

- **变更面巨大**——所有 plugin（echo / food / sticker / schedule / dream agent）的出口都要改
- 破坏 humanization part 1-5 已有的 plugin-level 灵活性——某些 plugin 需要不同的 humanizer 节奏
- 风险/收益比不好——Issue 15 的 fix 等不了这个 architectural

**与其他 issue 的耦合**：与 humanization part 1-5 强冲突——会把 part 1-3 的 plugin-level mood-aware 行为压平。

**评级**：**不推荐 15C**——本 issue 之外的另立 RFC；不在本文范围。

---

## Issue 16 — bot 自身被禁言时的状态可见性 + 自动恢复（self-mute lifecycle，P2）

### 方案 16A — **echo gate + admin SPA 自我状态卡 + 周期 reconcile 三段并行**（推荐）

**形态**：

- **第 1 段：echo plugin 加 mute gate**（必须做，bug fix）
  - [plugins/echo/plugin.py:189-195](../../plugins/echo/plugin.py#L189-L195) 调用 send_group_msg 前加 `if ctx.scheduler.is_muted(group_id): return`
  - D1 同模式扫描：grep `bot.send_group_msg` / `bot.call_api("send_group_msg"` 找其他绕开 scheduler.send_queue 的 plugin（grep 命令进《研究文档》§问题 16 末尾）
- **第 2 段：admin SPA self-mute 状态可见**
  - 后端：[admin/routes/api/](../../admin/routes/api/) 增加 `GET /api/scheduler/mute_state`——返回 `{group_id: {muted: bool, since_unix: int|null, source: "manual"|"event"|"reconcile"}}`
  - 前端：[admin/frontend/src/views/dashboard.vue](../../admin/frontend/src/views/dashboard.vue) 顶部状态卡新增 "bot 自身禁言状态" 区块，列出当前被禁言的群、起始时间、来源
  - 前端复用 `MetricCard.vue` / `AppCard.vue`（per CLAUDE.md skill 的 admin SPA style guide）
- **第 3 段：周期 reconcile + ActionFailed 反向标记**
  - scheduler 注册 `_reconcile_self_mute_loop`——每 5 分钟跑一次 `bot.get_group_member_info(group_id, user_id=self_id, no_cache=True)`，对比 `shut_up_timestamp` 与 `_muted_groups`
  - 不一致时：log warn + reconcile 状态（信任 server-side `shut_up_timestamp` 为准；若 NapCat 该字段返回 0 / unreliable，加 5 秒尝试 send_group_msg 一次空消息探测——非首选，保留为 fallback）
  - send_queue ActionFailed 路径：[services/send_queue.py:244-260](../../services/send_queue.py#L244-L260) ActionFailed `retcode in {1200, ...}` 时反向标记 mute（go-cqhttp#1429 / NapCatQQ#473 提到的多源容错）
- `config.json` 顶层 `self_mute_lifecycle: { reconcile_interval_seconds: 300, action_failed_reverse_mark: true, admin_state_visible: true }`

**成本估计**：100-180 行净增（echo gate 5-10 / admin API 30-50 / admin SPA 卡片 40-60 / reconcile loop 50-80）+ 3-4 用例测试 + admin SPA 一次 `vue-tsc --noEmit` & `npm run build`（D6 admin SPA 同步路径）。

**优势**：

- 三段并行——echo gate 是 bug fix（必须）；admin SPA 是 UX 必修；reconcile 是 robustness 防 NapCat 协议层 corner case
- 复用已有基础设施——`scheduler.is_muted()` / `_handle_group_ban` / startup poll 都在 [kernel/router.py:775-787](../../kernel/router.py#L775-L787) 已落地，本方案是补完而非重建
- 业内 robustness 模式：go-cqhttp `shut_up_timestamp=0` Android 协议 bug、NapCatQQ `get_group_member_info` 多源容错均在本仓研究文档 §问题 16 §8 引用列出

**风险与代价**：

- reconcile interval 300s 是平衡值——太密对 NapCat API rate limit 不友好；太疏感知慢
- ActionFailed retcode 集合需要灰度采样确认——OneBot 协议 retcode 列表 in 1200 系列含义不完全标准化
- admin SPA 改动需走 D6 流程（`npm run build`，不 rebuild bot）——maintenance log 须明示
- 周期 reconcile 与 startup poll 同模式——D1 同模式扫描需要确保两者状态一致（startup 是冷启动 + 5 分钟周期是热运行）

**与其他 issue 的耦合**：

- 与 humanization 链不冲突——本方案是 send 前 / 启动时 / 周期 task
- 与 F7（多 bot loop guard）不冲突，但同属"scheduler 状态可见性"范畴——admin SPA 一次卡片改动可顺手把 known_other_bots 状态也露出（与 F7 顺手做最经济）
- 与 F12（upstream filter）的 admin SPA 编辑面板同次 PR 一起做最经济（D6 一次 npm build）

**评级**：**推荐 16A**——三段都解，echo gate 是必修 bug；admin SPA / reconcile 是 robustness 投资；与 admin SPA 改动批一起做经济性最高。

### 方案 16B — 只补 echo plugin mute gate

**形态**：仅做 16A 第 1 段——[plugins/echo/plugin.py:189](../../plugins/echo/plugin.py#L189) 调用 send_group_msg 前加 `is_muted` check；admin SPA / reconcile 暂不做。

**成本估计**：10-15 行 + 1 用例测试。

**优势**：

- 修了核心 bug——echo plugin 在 bot 被禁言时仍尝试发消息会导致 NapCat ActionFailed 累积日志噪声
- 改动面最小，风险最低

**风险与代价**：

- 仍存在 admin SPA 不可见的 UX 问题——用户不知道 bot 现在是被禁言状态
- 仍存在 NapCat 协议层 `shut_up_timestamp` 不准时的 stale state 风险
- D1 同模式扫描如果发现其他 plugin 也绕开 scheduler，得二次返工

**与其他 issue 的耦合**：与 16A 完全一致，只是范围更小。

**评级**：**次选 16B**——仅当用户希望"先消 noise，UX/robustness 下次再做"时考虑。

### 方案 16C — ActionFailed-only 反向标记（不主动 reconcile）

**形态**：

- 16A 第 1 段（echo gate）必做
- 16A 第 3 段去掉周期 reconcile，只保留 ActionFailed 反向标记——出错时才进入 mute 状态
- admin SPA 状态可见（16A 第 2 段）保留

**成本估计**：60-100 行（无 reconcile loop）。

**优势**：

- 不增加 NapCat API 周期负载
- 只在需要时反应——不消耗 idle 时段 quota

**风险与代价**：

- 错过 server 端主动 lift_ban（管理员解除禁言但 NapCat 没推 group_ban event）的恢复——bot 一直以为自己被禁言不发任何消息，直到用户发现手动 unmute
- 与 humanization 链下游的 send_queue 强耦合——必须依赖 ActionFailed 触发，没有兜底

**评级**：**可选 16C**——用户对周期 reconcile 顾虑大时考虑；本仓 NapCat 协议历史看 group_ban event 偶有漏推，建议保留 reconcile。

---

## Issue 17 — 连续 @ 时多重排队 / coverage write 丢前一条 @ 目标（per-user burst window 缺位，P2）

### 方案 17A — **per-(group, user) @ burst window + slot.pending_triggers 列表化**（推荐）

**形态**：

- [services/scheduler.py:206-215](../../services/scheduler.py#L206-L215) `slot.trigger = trigger` 改为 `slot.pending_triggers: list[Trigger]`
  - 新 `slot.pending_triggers` 列表保留 burst 窗口内所有 @ trigger（`mode=at_mention`），含 (target_user_id, target_message_id, ts)
  - 触发 `_do_chat` 时把 `pending_triggers` 整体作为输入——LLM 看到"这一波被 @ 的所有用户"，可以选择一次回复多个目标 / 选最近一个 / 用 reply quote 指向最早一个
- 新增 `services/scheduler/burst_window.py`：per-(group, user) `TTLCache(maxsize=10000, ttl=3.0)`——3 秒内同一用户 @ bot N 次，仅触发一次 _do_chat（与 grammY ratelimiter / Telegram throttle middleware 同模式）
  - 命中策略：first-fire（首条立即触发，后续在窗口期内合并到 pending_triggers）；窗口结束未触发的也合并送入 LLM context
  - drop 策略：silent（不回复"你说太快了"，避免破沉浸）
- B 簇 F3（message coalescing）共骨架——burst window 是 per-(group, user) 的"特例 coalescer"；F3 是 per-group 的通用 coalescer；底层 TTLCache + asyncio.Task 复用
- `config.json` 顶层 `at_mention_burst_window: { enabled: true, window_seconds: 3.0, drop_silent: true }`

**成本估计**：200-280 行净增（pending_triggers 列表化 + burst_window TTLCache + 5-7 用例测试，含 `pytest.raises(TimeoutError)` cancel-path 模拟 D2）+ B 簇 F3 共骨架时 F3 也省 80-100 行。

**优势**：

- **同时解两个维度**——单用户连续 @ 合并（burst window）+ 多用户被 @ 不丢（pending_triggers 列表化）；现有 `slot.trigger = trigger` 单插槽 covering write 是丢失早 @ 的根因
- 与 F3 共骨架——B 簇 8 件复用 TTLCache 数据结构最经济
- 业内成熟模式——Telegram bot ThrottleMiddleware（[Telegram-Dev community 18-line ThrottleMiddleware](https://core.telegram.org/bots/api) / grammY ratelimiter `keyGenerator: ctx => from.id` per-user pattern / Inngest debounce#3695 "burst → single event"）
- pending_triggers 列表化对 LLM 友好——可以 "这次被 1/2/3 用户 @ 了，回复主要面向 X 但用 [CQ:reply] 指向最早一个" 类似策略；与 F11 addressee binding / F14 mention post-processor 共数据结构

**风险与代价**：

- pending_triggers 列表化是 schema change——`Slot` dataclass 字段重构需要回归 [services/scheduler.py:374/644](../../services/scheduler.py#L374) 等所有引用 `slot.trigger` 的位点；mitigation = 加 `slot.last_trigger` 兼容属性返回 `pending_triggers[-1]`
- burst window 3.0 秒是经验值——灰度灰度调参 1 周；过短会拆分本应合并的 burst（用户 1 秒内连发 3 条 @），过长会延迟首条响应感知
- burst 在 cancel-path 上必须 D2 测试——`asyncio.Task` 在 shutdown 时被 cancel，pending_triggers 不能在 task 取消后影响下次 `_do_chat`（外部可观察状态：DB row、`pending_at` 单飞标记），D2 用例必带
- 跨群 rate（`@bot` 在 N 个群同时被发起）不在本方案范围——见 17C

**与其他 issue 的耦合**：

- **与 F3（message coalescer）共 TTLCache 骨架**——B 簇 8 件复用最经济；建议同 PR 落地
- 与 F11（addressee binding）共数据结构——pending_triggers 含 target_user_id，F11 nickname registry 可直接消费
- 与 F14（mention post-processor）共"多 @ 目标"语境——LLM reply 写出多个 `@昵称` 时 post-processor 命中率提升
- 与 humanization part 1-5 不冲突——本方案位点在 trigger 入队前

**评级**：**推荐 17A**——治本（burst window 防多次触发）+ 治症状（pending_triggers 列表防丢 @ 目标）双层防线；与 F3/F11/F14 共骨架最经济。

### 方案 17B — 只 pending_triggers 列表化（不加 burst window）

**形态**：

- 17A 第 1 段（`slot.trigger` → `slot.pending_triggers: list[Trigger]`）保留
- 不引入 burst_window TTLCache——同一用户连发 3 次 @ 仍触发 3 次 _do_chat，但每次的 pending_triggers 包含历史所有 @

**成本估计**：120-160 行净增（schema 改造 + 4-5 用例测试）。

**优势**：

- 修了"丢前 @ 目标"的根因——slot covering write 不再丢失早 @
- 改动面比 17A 小一半

**风险与代价**：

- 仍触发 N 次 _do_chat——LLM API 成本仍随 burst 线性增长
- 仍触发 N 次 humanization 节奏判定——可能出现"3 条很快连续的回复"破节奏
- D1 同模式扫描下次会再被扫到二次返工——`burst-window 缺位` 这个根因没修

**与其他 issue 的耦合**：

- 与 F3 不共骨架——burst_window 是核心复用点
- 与 F11 / F14 仍共 pending_triggers 数据结构

**评级**：**次选 17B**——仅当用户希望"先解决最严重的丢 @ 问题，rate 控制下次再做"时考虑。

### 方案 17C — Cross-group per-bot rate throttle（架构层独立）

**形态**：

- 全局 per-bot rate throttle——bot 在 N 个群同时被 @ 时，所有群共用一个 `asyncio.Semaphore(K)`；K 是并发上限
- 与 17A 的 per-(group, user) 完全独立维度——架构层独立 layer

**成本估计**：100-140 行净增 + 4-5 用例测试。

**优势**：

- 防止跨群 burst 把 LLM API 打到 RateLimitError——Anthropic API rate limit 是 per-API-key 的
- 独立维度——可与 17A 同时上线

**风险与代价**：

- 跨群 rate 是 P3 紧迫性——本仓现有 group 数 ≤ 10，semaphore 实际命中率低；当下投资 ROI 不明
- Anthropic SDK 已内建 rate limit 退避——本仓 `services/llm/client.py` SSE 循环已有 RateLimitError 处理，再加一层可能 over-engineering
- 不解决 issue 17 的"丢 @ 目标"根因——必须叠加 17A 或 17B

**与其他 issue 的耦合**：

- 独立 layer——不与 B 簇 8 件共骨架
- 与 humanization 不冲突

**评级**：**可选 17C**——issue 17 主体由 17A 解；17C 是"未来当群数突破 30+ 时的预案"，不在本次范围。

---

## 决策模板（给用户填）

> 复制下面这段、勾选你的选择回我即可。若有未列的混合方案，自由备注。

```text
Issue 1 / sentinel guardrail：[ ] 1A 推荐  [ ] 1B  [ ] 1C  [ ] 暂不做
Issue 2 / preflight：        [ ] 2A 推荐  [ ] 2B  [ ] 2C  [ ] 暂不做
Issue 3 / coalescer：        [ ] 3A 推荐  [ ] 3B  [ ] 3C  [ ] 暂不做
Issue 4 / persona drift：    [ ] 4A 推荐（Layer 1+2+3+5；Layer 4 PPA 后续 opt-in）  [ ] 4A 仅 Layer 5（弱化版，仅 stop-gap）  [ ] 4B 仅出口 stripper  [ ] 4C fine-tune（不推荐当下）  [ ] 暂不做
Issue 5 / OOV slang：        [ ] 5A 推荐  [ ] 5B  [ ] 5C  [ ] 暂不做
Issue 6 / sticker policy：   [ ] 6A 推荐  [ ] 6B  [ ] 6C  [ ] 暂不做
Issue 7 / bot-pair loop：    [ ] 7A 推荐  [ ] 7B  [ ] 7C  [ ] 暂不做
Issue 8 / schedule overshare：[ ] 8A 推荐  [ ] 8B  [ ] 8C  [ ] 暂不做
Issue 9 / ☆/✨ symbols：      [ ] 9A 推荐  [ ] 9B  [ ] 9C  [ ] 暂不做
Issue 10 / dedup gate：      [ ] 10A 推荐  [ ] 10B  [ ] 10C  [ ] 暂不做
Issue 11 / addressee binding：[ ] 11A 推荐  [ ] 11B  [ ] 11C  [ ] 暂不做
Issue 12 / upstream filter： [ ] 12A 推荐  [ ] 12B  [ ] 12C  [ ] 暂不做
Issue 13 / thinker guardrail：[ ] 13A 推荐  [ ] 13B  [ ] 13C  [ ] 暂不做
Issue 14 / mention wiring：  [ ] 14A 推荐  [ ] 14B  [ ] 14C  [ ] 暂不做
Issue 15 / echo humanizer：  [ ] 15A 推荐  [ ] 15B  [ ] 15C  [ ] 暂不做
Issue 16 / self-mute lifecycle：[ ] 16A 推荐  [ ] 16B  [ ] 16C  [ ] 暂不做
Issue 17 / @ burst window：  [ ] 17A 推荐  [ ] 17B  [ ] 17C  [ ] 暂不做

执行批次（可选）：
[ ] 簇 A（F1+F4 Layer 3 drift detector+F4 Layer 5 stripper+F8 第二刀+F9+F10 dedup gate+F13 phrase_detector+F14 mention post-processor）一次落地（7 件）
[ ] 簇 B 子集（F7+F3+F4 Layer 2 anchor reinjection+F10 lock 部分+F12+F17 burst window）先做（P0/P0/P1/P0/P1/P2 共 router/scheduler 入口骨架 + TTLCache + boundary detector）
[ ] 簇 D（F6）单独做
[ ] 簇 E（F15 echo humanizer）单独做
[ ] 簇 F（F16 self-mute lifecycle）单独做或与 B 簇 admin SPA 改动批一起
[ ] 簇 C（F4 Layer 1 compiler validator+F8 第一刀+F13 治本路径）等 part6 节奏到了再合并
[ ] F4 Layer 4 PPA post-hoc refinement（opt-in，灰度 7 天 Layer 1+2+3+5 后视 drift_repair_hit_rate 决定是否启用）
[ ] 全部并发（不推荐）

紧迫性建议（仅供参考）：
- 本周必做：F7（多 bot 死循环 P0）+ F10 dedup gate（issue 7+8 乘法放大器 P0）+ F13 phrase_detector（thinker 文本泄漏 P0，作为 A 簇内最小子集）+ F16 echo gate（self-mute bug fix 必修）
- 本周尽量做：F1（sentinel 泄漏 P0）+ F3（coalescer P0）+ F4 Layer 5 stripper（A 簇出口侧 cheap，与 F1 共 sentinel registry）+ F10 lock 部分（与 F7 同骨架）+ F12（upstream filter P1，与 F7 共 known_other_bots 数据结构）
- 本月内：F6（sticker P1）+ F4 Layer 1 compiler validator + F8 第一刀+F13 治本路径（source 重写 + ThinkDecision 字段重构）+ F4 Layer 2 anchor reinjection + F4 Layer 3 drift detector + F11（addressee binding P1，与 F3 共 router 入口）+ F14（mention post-processor，与 F11 共 nickname registry）+ F16 admin SPA + reconcile（与 F7/F12 admin SPA 改动批一起）+ F15（echo humanizer 输入修正）
- 30 天 watcher：F9（symbols P3）+ F4 Layer 4 PPA refinement（视 Layer 3 drift_repair_hit_rate 决定）
- 可滞后：F2（preflight P1，低紧迫）+ F5（OOV slang P1，与 B 簇合做）+ F17（@ burst window P2，与 F3 共骨架同次落地最经济）
```

---

## 附：与现有 roadmap 的关系

- **不冲突**：本文所有方案均为"加 layer / wiring / source 改写"，不动现有 humanization part1-5 已落地能力，不动 [services/humanization/](../../services/humanization/) 现有 scorer / sanitizer 节奏控制
- **依赖项**：F4 Layer 1 compiler validator + F8 第一刀的 source 重写需要等 part6 source-side generation roadmap（[docs/tracking/omubot-humanization-part6-source-side-generation.md](omubot-humanization-part6-source-side-generation.md)）的 generation contract 落地；不能单独抢跑（part6 生成器需吃 `declaration_policy` 作为 prompt 约束）；F4 Layer 2/3/5 不依赖 part6 可独立排期；F13 治本路径（ThinkDecision 字段重构）需要 thinker prompt 同步重写到 JSON schema 输出，本身不强依赖 part6 但与之节奏对齐最经济
- **复用项**：F1 / **F4 Layer 3 drift detector + Layer 5 stripper** / F8 第二刀 / F9 / F10 dedup gate / F13 phrase_detector / F14 mention post-processor 共用同一份 sentinel/dedup/phrase/mention/drift registry（A 簇 7 件）；F2 / F3 / **F4 Layer 2 anchor reinjection** / F5 / F7 / F10 lock 部分 / F11 / F12 / F17 共用 router 入口 / scheduler 入口序列化骨架（B 簇 9 件）——两条复用是本文 0.2 簇划分的依据；F4 Layer 2 boundary detector 与 F11 nickname binding（@-mention 切换 trigger）/ F3 message coalescer（topic shift trigger）/ F17 burst window（session boundary trigger）共数据源；F12 与 F7 共 known_other_bots 数据结构；F11 与 F3 共 router 入口；F14 与 F11 共 nickname registry；F17 与 F3 共 TTLCache（per-(group,user) burst window vs per-group coalescer）；**F4 Layer 1 compiler validator** / F8 第一刀 / F13 治本路径 共"自由叙事文本→结构化 enum / 受护栏的 voice exemplar"治本骨架（C 簇 3 件，复用 ImportIssue + system_validation 扩展引擎）；F4 Layer 4 PPA 是独立 opt-in 路径，不与本文任何 issue 共骨架；F15 单点 plugin 修复（E 簇）；F16 echo gate + admin SPA + reconcile（F 簇）与 F7/F12 admin SPA 改动批共次 PR（D6 一次 npm build）

---

## 附：与 D 系列纪律的对接

| 纪律 | 本文如何遵守 |
| --- | --- |
| **D1 同模式扫描** | 每个方案都在《研究文档》§问题 N 末尾给出 grep 命令；选定方案后实施时按命令逐项扫描、写入维护日志；F11/F12/F13/F14/F15/F16/F17 D1 命令已附在研究文档 §问题 11/12/13/14/15/16/17 末尾——F14 grep `\[CQ:at,qq=` 找出现有 outbound at-segment 位点；F15 grep `humanizer.delay(` 找其他绕开 runtime 参数的调用点；F16 grep `bot.send_group_msg` / `bot.call_api\("send_group_msg"` 找其他绕开 scheduler.is_muted gate 的 plugin；F17 grep `slot.trigger\s*=` 找其他 covering write 位点；**F4 v2** 落地时 D1 grep 命令 = `grep -nE '^\s*(我是\|我叫\|作为\|我担任\|我扮演)' config/persona/*.md`（找其他 source.md 的 declaration 位点）+ `grep -rn 'core\.identity\|core\.personality\|core\.role' services/persona/`（找 compiler 输出的 block id 引用，确保 Layer 1 lint 不误伤 voice exemplars 块）+ `grep -rn 'ImportIssue\(level=' services/persona/`（找仓内既存 Issue 写入点，确保 Layer 1 同模式接入）+ `grep -rn 'system_message_addition\|user_role.*append\|messages\[-1\]' services/`（找 Layer 2 user-role 注入候选位点） |
| **D2 cancel-path 测试** | F1 / F3 / F7 / F10 / F11 / F12 / F13 / F14 / F17 的 sliding-window / pair_guard / coalescer / dedup_gate / per-group lock / addressee resolver / upstream filter / thinker_phrase_detector / mention post-processor / per-(group,user) burst window 都涉及 timer + buffer + 持锁段，落地必带 `pytest.raises(TimeoutError)` 模拟 cancel 的回归——尤其 F10 lock 持有中 LLM call `wait_for` 超时被 cancel 时不能脏 metric 计数；F13 phrase_detector 在 LLM stream cancel 时不能泄漏中间态 thinker_phrase 计数；F17 burst window asyncio.Task 在 shutdown 时被 cancel，pending_triggers 不能在 task 取消后影响下次 `_do_chat`（外部可观察状态：DB row、`pending_at` 单飞标记）；**F4 v2 Layer 3 PersonaDriftDetector + Layer 4 PPA 必带 D2**——`repair_once()` LLM call 被 cancel 时不能脏 EWMA 状态（`persona_drift_state` 在重试 cancel 后必须回到上一个稳定值，而非中间态）；Layer 4 PPA 第二次 LLM call 被 cancel 时不能让 first reply 漏出（外部可观察：`drift_repair_hit_rate` metric 不计数失败的 retry）；Layer 2 anchor reinjection 在 boundary detector race 时不能让两份 anchor 同时注入 messages（外部可观察：`anchor_reinject_count` per-(group, request_id) 单飞） |
| **D3 重构带迁移清单** | **F4 v2 Layer 1 compiler validator** + F8 第一刀 + F13 治本路径涉及 source 重写 / ThinkDecision 字段重构——必须出"旧 declaration / 旧 thought 字段 → 新 voice exemplar / 新 topic_intent_label" 映射表存 `docs/migrations/`；F11 nickname binding 落地需要"AddresseeResult 旧返回 → 新 dataclass 字段"映射表；F17 落地需要"`Slot.trigger` 单字段 → `Slot.pending_triggers: list[Trigger]` + `Slot.last_trigger` 兼容属性"映射表，含所有引用 `slot.trigger` 的位点回归清单；F4 v2 Layer 1 落地额外要出"旧 source.md declaration 行 → 新 voice exemplar 引用"映射表（含每条 declaration 的拒绝原因 + 建议改写示例 + arxiv 2601.10387 引用），存 `docs/migrations/persona-declaration-lint.md` |
| **D4 完成声明含证据** | 每方案落地后维护日志要含 ① grep 同模式扫描结果 ② 外部可观察证据（log / metric / SQL row）③ 回滚路径；F12 落地后 `storage/logs/upstream_filter_drops.log` 是核心证据；F13 落地后 `services/block_trace/store.py` thinker_phrase_detector_hits metric 是核心证据；F14 落地后 `services/block_trace/store.py` mention_resolution_meta（含 `(literal_name, resolved_qq, source)` 列表）是核心证据；F15 落地后 echo plugin 节奏对比（before/after）`storage/logs/echo_humanizer_delays.log` 采样 1 周；F16 落地后 admin SPA self-mute 状态卡 + `storage/logs/self_mute_reconcile.log` 是核心证据；F17 落地后 burst_window TTLCache 命中率 metric + `slot.pending_triggers` 列表深度直方图是核心证据；**F4 v2 落地后核心证据集**：① Layer 1 = `services/persona/system_validation.py` 报告中的 `ImportIssue` 计数（declaration_lint 命中数 / exemplar_coverage warn-error 计数）+ pre-commit hook fail 样本 ② Layer 2 = `block_trace/store.py` `anchor_reinject_count` + `anchor_boundary_type_distribution` + 7 天 `cache_hit_rate` 趋势（验证 user-role 注入未让 cache 命中率归零）③ Layer 3 = `block_trace/store.py` `persona_drift_state` FSM 状态 + `persona_drift_repair_hit_rate` + `persona_drift_drop_rate` ④ Layer 5 = `declaration_strip_count` + `meta_self_declaration_detector_hits`；回滚路径 = `_persona_runtime.json drift_policy` 全字段置 0 / `declaration_policy.runtime_strip=false` / Layer 2 boundary detector flag off |
| **D5 pytest 防孤儿** | 跑测试前 `pkill -9 -f pytest`（已是常规） |
| **D6 admin SPA 同步** | F7 known_other_bots admin 编辑面板、F12 upstream_command_filter 编辑面板、F16 self-mute 状态卡属于前端工作——`npm run build` 即可，不 rebuild bot；建议三者一次 PR 合并以节省 npm build 次数；**F4 v2 Layer 1-5 全部为后端改动**——无 admin SPA 改动 / 不 rebuild npm；persona freeze 仍走 PersonaImporterView 既存通道，本方案只在 importer pipeline 内插 lint 阶段（D6 不触发） |
| **D7 部署前 git hygiene** | 任一方案落地推送前 `git stash list && git status -uno`；config 改动走 config.json（不再走 config.toml）——F11 `addressee_binding` / F12 `upstream_command_filter` / F13 `thinker_output_guardrail` / F14 `mention_post_processor` / F15（无新 config，仅修 plugin 内部） / F16 `self_mute_lifecycle` / F17 `at_mention_burst_window` 都加在 config.json 顶层；**F4 v2 配置项不进 config.json**——`declaration_policy` / `drift_policy` 跟 persona 走，写在 `_persona_runtime.json` 顶层（与 persona freeze 一同打包），让降级显形（per-persona override 需 source.md frontmatter 显式声明） |

---
