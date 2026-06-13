# Prompt Cache 深度调研报告

日期：2026-05-18
撰写：claude-opus-4-7-thinking（自动调研）
复审：gpt（2026-05-18）
范围：DeepSeek prompt cache 命中率诊断 + 业界顶级 LLM-application 缓存工程对照（Claude Code、Anthropic、DeepSeek、SGLang、Aider）

**触发问题**：DeepSeek 后台显示 Omubot 整体 prompt cache 命中率 38.6%（2026-05-17，1,031,168 命中 / 1,640,814 未命中）。本地 `usage.db` 同日 `proactive` call 命中率 51.0%——两者口径差异是因为 `usage.db` 只覆盖了 `proactive`/`chat`/`compact`，遗漏 `thinker` 与 `slang*` 调用。本报告不只是回答"为什么 38.6%"，而是把 Omubot 的缓存设计放到业界对照里全面体检，给出有依据的改造路线。

**采样说明**：报告中的代码引用、binary 反编译片段、SQL 数据采样于 2026-05-18 本地环境；Claude Code binary 版本 2.1.143（2026-05-15 build）；上游开源项目（SGLang、Aider、Cline）拉取自 2026-05-18 main 分支。

---

## 0. 调研档案位置

一手证据已归档到 [`_assets/prompt-cache-research-2026-05-18/`](./_assets/prompt-cache-research-2026-05-18/)：

| 文件 | 内容 | 大小 |
|---|---|---|
| `_chunk_00_line333352_hits38.js` | Claude Code 的 `Ti9` 函数（每请求 6 路独立哈希 + diff classifier） | 18 KB |
| `_chunk_01_line339567_hits14.js` | `wU3`（breakpoint 放置 + `tengu_api_cache_breakpoints` 埋点） | 34 KB |
| `_chunk_02_line339554_hits12.js` | `Xj6` 工具 schema memoization；`zd8` 系统 prompt 分段 | 2 KB |
| `_chunk_03_line339566_hits9.js` | `MSK`（TTL 原地升级）、`hi` cache_control 构造、`ikH` 1h TTL allowlist | 13 KB |
| `_chunk_04_line333343_hits5.js` | `Q85`/`CGH` 持久化；`Oi9` 图像哈希消毒；`gz8` MCP 名字归一化 | 2 KB |

JS chunks 为 Claude Code 2.1.143 native binary（`/Users/kragcola/.vscode/extensions/anthropic.claude-code-2.1.143-darwin-arm64/resources/native-binary/claude`，198 MB Mach-O 64-bit ARM64）的 `strings` 输出按 cache 关键词密度筛选后的 minified JS 片段。**仅作研究参考，不可二次分发**——Claude Code 是 Anthropic 闭源产品。

附录 A 是调研中间态（最初的 4 方向暂存），保留作为认知演进过程的记录。报告正文（§ 8）已经精确替代了它。

---

## 1. 重要更正：DeepSeek vs Anthropic 缓存机制完全不同

调研前我曾以为"DeepSeek 要求前缀 ≥1024 tokens 才进缓存"——**这是 Anthropic Sonnet 的最低长度，不是 DeepSeek 的**。

| 维度 | Anthropic | DeepSeek |
|---|---|---|
| 客户端配置 | **必须**带 `cache_control` | **不需要任何客户端配置**，自动按 token 前缀匹配 |
| 最小命中长度 | Sonnet 1024 / Opus+Haiku 4096 tokens | **64 tokens**（Page size = 64） |
| TTL | 5 min（默认）/ 1h（2x 价格） | 几小时到几天，**不可控** |
| 写入定价 | 1.25× base | 1× base（无写入费） |
| 读取定价 | 0.1× base | 0.1× base |
| Breakpoint | 最多 4 个，由客户端放 | 无 breakpoint 概念，全自动 |
| 存储 | 显存（5min）/ Bedrock SSD | **磁盘**（基于 MLA 压缩） |
| 命中条件 | 命中标记 + 64+ token 前缀完全相同 | 64+ token 前缀完全相同（页对齐） |

**对 Omubot 的关键含义**：

1. 我之前以为黑话审核 system_prompt（200–350 tokens）"短到没法缓存"——**错**。DeepSeek 64-token 起就缓存，黑话调用本来 *能* 缓存。
2. Omubot 在所有 system_blocks 上设的 `cache_control: ephemeral` 对 DeepSeek **完全无效**——DeepSeek 自动管理，不看这个字段。当前不构成 bug 但是 dead code，需要在改造时一并清理（避免误导未来维护者）。
3. 命中失败的真正机理是 **前缀不稳定**：DeepSeek 是 token 级前缀匹配，前 N 个 token 任何一处变化就直接 N 之后全 miss。
4. Anthropic 的最佳实践（cache_control 放在哪、放几个）对 Omubot 当前不适用——但如果以后要加 Anthropic 路径（Claude opus 等），框架要为此预留位置。

---

## 2. 现状诊断：38.6% 命中率的真实分布

`usage.db` 与 DeepSeek 后台对账（2026-05-17）：

| 来源 | 输入未命中 | 输入命中 | 输入总和 | 输出 | 命中率 |
|---|---:|---:|---:|---:|---:|
| DeepSeek 后台 | 1,640,814 | 1,031,168 | 2,671,982 | 835,844 | **38.6%** |
| 本地 `usage.db` (proactive) | 256,938 | 267,648 | 524,586 | 4,036 | 51.0% |
| **缺口** | **1,383,876** | **763,520** | **2,147,396** | **831,808** | — |

`usage.db` 只覆盖主回复链路；遗漏的 80% 流量主要是：

1. **thinker 决策**（最大）：每条群消息触发，prompt 短，system_text 含 mood/affection（每轮变前缀）→ 命中率近 0%。日志中 `silent_learn` 高频出现，估计调用数 5–10× 主回复。`call_type="thinker"` 在 5/03 之后就再无记录，因为 client.py:1767-1777 只在 `action="wait"` 路径写入。
2. **黑话调用**：extractor / review_utils / drift_reviewer / semantic_reviewer 四条入口全部直接调 `llm_client._call(...)`，**完全绕过 `_record_usage`**。
3. **可能还有**：reply_segmentation 的 reply_gate、vision、未来增加的层（style 反馈闭环、knowledge 检索）。

按数量级估算：5/17 `usage.db` 输入未命中 256k → DeepSeek 1.64M，**未记录的部分约是已记录的 5–6 倍**。

---

## 3. Claude Code 的 11 个缓存工程技术（从 binary 反编译）

按价值降序，全部有反编译证据。

### 技术 1：六路独立哈希（最重要）

`Ti9(H)` 函数对每次请求计算 6+ 个独立 hash（[`_chunk_00_line333352`](./_assets/prompt-cache-research-2026-05-18/_chunk_00_line333352_hits38.js)）：

```
systemHash       = SGH(systemBlocks - cache_control - billing-prefix blocks)
toolsHash        = SGH(toolSchemas - cache_control)
cacheControlHash = SGH(systemBlocks.map(b => b.cache_control))
perBlockHashes   = systemBlocks.map(SGH)
perBlockLengths  = per-block token count
perToolHashes    = a85(toolSchemas, toolNames)  // keyed by tool name
messageHashes    = o85(messagesForAPI)           // per message
```

`SGH = Bun.hash(SH(x))`（[`_chunk_04`](./_assets/prompt-cache-research-2026-05-18/_chunk_04_line333343_hits5.js)）。

**为什么有用**：当 cache 命中率掉了，能精确告诉你"是 system 变了 / 还是 tool schemas 变了 / 还是哪条 message 变了"。Omubot 当前只看到聚合数据，根本不知道是哪一段变了。

### 技术 2：持久化 cache state 跨进程重启

`Q85()` 在启动时从 `cache-break-state-${k_()}.json` 重新加载上次的 hash 状态；`CGH()` 在每次请求后 write-through（[`_chunk_04`](./_assets/prompt-cache-research-2026-05-18/_chunk_04_line333343_hits5.js)）。

```js
function Ki9() {
  return Hi9.join(PI(), `cache-break-state-${k_()}.json`)
}
function Q85() {
  if (Qz8 || !qi9()) return;
  Qz8 = !0;
  // load hash state from disk into Pm
}
function CGH() {
  if (!qi9()) return;
  // serialize Pm to disk
}
```

意味着 **claude CLI 重启不会丢失"我之前的 prompt 长什么样"的记忆**。重启后第一次发出的 prompt 如果与重启前 byte-for-byte 相同，DeepSeek/Anthropic 是会命中的（DeepSeek TTL 几小时到几天）。Claude Code 在客户端跟踪了这件事。

**对 Omubot**：bot 容器重启后丢失"上次 prompt"的记忆，第一轮请求看起来像 100% miss——但**前缀其实还在 DeepSeek 的磁盘缓存里**。这只是诊断盲区，不是真损失。

### 技术 3：Hash-payload 消毒（核心防爆）

四个具体消毒函数（[`_chunk_04`](./_assets/prompt-cache-research-2026-05-18/_chunk_04_line333343_hits5.js)）：

```js
sn9(blocks)  // 哈希前剥掉 cache_control（TTL 变化不应触发 systemHash 变化）
tn9(block)   // 跳过 "x-anthropic-billing-header:" 开头的 system 块
gz8(name)    // 把非白名单 MCP 工具名归一为字面 "mcp"
Oi9(content) // base64 数据 > 256 chars 时，哈希时只用长度而非内容
```

**对 Omubot**：当前的 system blocks 包含 `mood_text + affection_text + state_board + style_block + ...`，这些**每轮变化**。正确做法不是哈希时剥掉它们（破坏一致性），而是把它们**物理上**移到非缓存段（比如 system 末尾或 user message）。

### 技术 4：Diff classifier 输出诊断字段

`$i9` 在 break 发生时上报（[`_chunk_00`](./_assets/prompt-cache-research-2026-05-18/_chunk_00_line333352_hits38.js)）：

```
systemPromptChanged, toolSchemasChanged, modelChanged, cacheControlChanged,
globalCacheStrategyChanged, betasChanged, autoModeChanged, overageChanged,
cachedMCChanged, cacheDiagnosisChanged, effortChanged, extraBodyChanged,
messagesHistoryChanged, firstChangedMessageIndex,
prevMessageCount, addedToolCount, removedToolCount,
addedTools, removedTools, changedToolSchemas,
prevBlockCount, newBlockCount, changedBlockIndices, changedBlockLengthDeltas,
systemCharDelta, previousModel, newModel, prevGlobalCacheStrategy, newGlobalCacheStrategy
```

通过 `tengu_prompt_cache_break` 埋点上报。

**对 Omubot**：当前 admin/system 页只显示一个聚合的 `last_cache_hit_pct`。要做到 Claude Code 这个粒度，需要在客户端对前后两次请求做结构化 diff，记录"是哪一段变了"。

### 技术 5：False-positive 抑制

- `cacheDeletionsPending`：当 client 主动做 microcompact 时，预期 cache_read 会下降，**不要**当成 cache break
- `cache deletion applied, cache read: Y → N (expected drop)` 是专门为这个场景设计的日志
- `Lz_(querySource)`：单次清空 `prevCacheReadTokens`，跳过下一次 diffing
- 阈值常量：`l85 = 2000`、相似度 `>= Y * 0.95`、TTL `n85 = 300_000`(5min)、`dz8 = 3_600_000`(1h)

**含义**：Claude Code 区分"应该 break"和"自然 break（TTL 过期 / 主动清理）"。Omubot 当前没有这种区分。

### 技术 6：Constant sentinel 替换 LLM 生成的总结（**对 Omubot 极其关键**）

`Xi9`（keep-recent microcompact）把所有老 tool_result 的 content **统一换成固定字符串** `lz8 = "[Old tool result content cleared]"`（[`_chunk_00`](./_assets/prompt-cache-research-2026-05-18/_chunk_00_line333352_hits38.js)）：

```js
function Xi9(H, _, q) {
  let { keepSet: K, tokensSaved: O, candidates: T } = iz8(H, q.keepRecent);
  if (O < nz8) return null;
  let $ = new Set(T.map((Y) => Y.tool_use_id)), z = new Map;
  for (let Y of T) {
    let w = Y.content ? await q.persist?.(Y.content, Y.tool_use_id) : null;
    z.set(Y.tool_use_id, w ?? lz8)  // ← 统一替换为 sentinel "lz8"
  }
  // ...
}
```

`nz8 = 20000`（触发阈值）。同样的对话 N 次重新发起，老 tool_results 段是 byte-identical 的。

**对 Omubot**：当前 `services/llm/client.py` 的 compact 路径调 LLM 生成对话总结。LLM 生成的总结**每次都不一样**，意味着每次 compact 后整个对话历史的前缀全 broken。Claude Code **不靠 LLM 总结**老内容，而是直接用固定字符串"占位"。

### 技术 7：Per-source 独立 cache 桶

`Pm = new Map()`，按 `H_6(querySource, agentId)` 索引。`c85` 白名单（[`_chunk_00`](./_assets/prompt-cache-research-2026-05-18/_chunk_00_line333352_hits38.js)）：

```
c85 = ["repl_main_thread", "sdk", "agent:custom", "agent:default", "agent:builtin"]
```

主线程 / 子 agent / SDK 调用各自有独立的 cache state。子 agent 抖动不会污染主线程的诊断。容量 `d85 = 10`（LRU eviction）。

**对 Omubot**：当前所有任务（main / thinker / slang / compact）共用一个 `ProfileRateLimitState.last_cache_hit_pct`。这就是为什么 admin 页一会儿被 thinker 拉到 0%、一会儿被主回复抬到 80%——不同任务的瞬时值在覆盖同一个变量。

### 技术 8：Tool schema canonicalization + memoization

`Xj6` 用 `provider-prefix + name + sha256(inputJSONSchema)` 作为 key 缓存解析后的 schema（[`_chunk_02`](./_assets/prompt-cache-research-2026-05-18/_chunk_02_line339554_hits12.js)）。两次请求工具集相同时，发出去的 `tools[]` 是 byte-identical 的（关键前提）。

**对 Omubot**：`_build_tool_defs(group_profile)` 每次都重新构建 tool_defs。如果 group_profile 中工具白名单不变，结果**理应** byte-identical——但需要审计是否真的稳定（顺序、空格、JSON key 顺序）。

### 技术 9–11（Omubot 暂不适用，仅作参考）

- **Fork-point breakpoint pinning**（`wU3`）：conversation 分叉时保留长前缀缓存
- **TTL 原地升级**（`MSK`）：升级 5min → 1h 不重建内容
- **Per-block scope**（`hi({scope})`）：org-shared cache 命名空间

DeepSeek 没有 TTL / scope 概念，且 Omubot 没有显式分叉。这些技术留给未来如果加 Anthropic 路径再说。

---

## 4. Aider 的极简实现（同样有效）

[`/tmp/research/cache-hit/aider/aider/coders/chat_chunks.py:1-65`](https://github.com/Aider-AI/aider/blob/main/aider/coders/chat_chunks.py) 是教科书级的应用层缓存设计：

```python
@dataclass
class ChatChunks:
    system: List
    examples: List
    readonly_files: List
    repo: List
    done: List
    chat_files: List
    cur: List
    reminder: List

    def all_messages(self):
        return (self.system + self.examples + self.readonly_files + self.repo
                + self.done + self.chat_files + self.cur + self.reminder)

    def add_cache_control_headers(self):
        if self.examples: self.add_cache_control(self.examples)
        else:            self.add_cache_control(self.system)
        if self.repo:    self.add_cache_control(self.repo)
        else:            self.add_cache_control(self.readonly_files)
        self.add_cache_control(self.chat_files)
```

**关键设计原则**：

1. **结构化分段**：8 个 named 段，每段语义清晰、变化频率不同
2. **breakpoint 在分段边界**：不会突然往中间插标记
3. **拼装顺序固定**：`all_messages` 永远以同样顺序返回
4. **3 个 breakpoint**（位置极稳定）

Aider 没用 Claude Code 那么复杂的 hash chain，但因为**结构 stable + breakpoint 位置 stable**，效果一样好。**对 Omubot 这是更现实的目标形态**。

---

## 5. SGLang RadixAttention 的核心抽象

`sglang/python/sglang/srt/mem_cache/radix_cache.py:50-308`：

- **TreeNode** with `children = defaultdict(TreeNode)`，前缀树
- **page_size**（默认 64，跟 DeepSeek 数字一致）：所有匹配按 page-aligned 整块进行
- **eviction policies**：LRU / LFU / FIFO / MRU / FILO / priority / SLRU 全支持
- **lock_ref / inc_lock_ref**（`base_prefix_cache.py:240`）：在请求执行期间 pin 节点防淘汰

**对 Omubot**：这是**推理引擎侧**的实现，应用层不直接对应。但有一个迁移过来的概念：Omubot 现在 `compact` 时调 LLM 生成新摘要 → 整个老对话哈希被破坏 → 等于 RadixAttention 里的"父节点失效"。Claude Code 的 sentinel 替换是规避这个问题的应用层等价物（见技术 6）。

---

## 6. 共通的"应用层"缓存工程原则（5 系统达成共识）

按重要性排序：

1. **Stable prefix, volatile suffix**——前缀绝不动，变化都堆到后面
2. **结构化分段 + 边界稳定**（Aider 的 ChatChunks、Claude Code 的 system block 排序）
3. **任何 byte-level 变化都是死刑**——加一个时间戳到 system 头部就破坏整段缓存
4. **不在缓存段内调 LLM 生成内容**（Claude Code 用 sentinel 字符串替代 LLM 总结）
5. **Tool schema 必须 byte-stable**（顺序、空格、JSON 字段顺序都要一致）
6. **Per-axis 监控**（system / tools / messages 分开看哪个 break 了）

---

## 7. 反模式（5 系统都明确防御）

| 反模式 | Claude Code 怎么防 | Omubot 现状 |
|---|---|---|
| 时间戳 / 计数器进 system | `tn9` 跳过 billing 前缀 | mood_text 含"今天是 X 月 X 日" → 每天都 break |
| LLM 生成的摘要进缓存段 | `Xi9` 用 sentinel 替代 | `compact` 调 LLM 生成总结 → 每次破坏前缀 |
| 工具列表抖动 | `gz8` 归一化 MCP 名字 | tool_defs 在 group_profile 切换时变 → 全 break |
| 同图重新编码哈希漂移 | `Oi9` 哈希用 length 替代 base64 | Omubot 不发图 → 不适用 |
| 主动清理被误判为 break | `cacheDeletionsPending` 标记 | 没有这种区分 |
| 不同任务污染同一指标 | `Pm` 按 querySource 分桶 | 全部共享 `ProfileRateLimitState` |

---

## 8. 对 Omubot 的真实诊断（基于研究的最终结论）

回到最初的 38.6% 命中率问题，**真正的 root cause 是**：

### 致命问题 1：thinker.py 的 system_prompt 前缀不稳定

```python
# services/llm/thinker.py:269-275
system_text = THINKER_SYSTEM_PROMPT.format(name=identity_name)
if mood_text:
    system_text = mood_text + "\n\n" + system_text   # ← prepend
if affection_text:
    system_text = system_text + "\n\n" + affection_text  # ← append
```

`mood_text` 内含**实时心情值 + 当前时段 + 日程片段**，**每次都不同**。它被 prepend 到 system_text 前面 → DeepSeek 看到的前缀从第 1 个 token 起就 miss → 整个 thinker 调用 0% 命中。

→ **修复**：mood/affection **物理上**应该在 user message 段或者放在 system 末尾（不是开头）。Anthropic 的 system 是数组，可以 cache_control 标记中间某块；DeepSeek 是字符串拼接，**位置就是一切**。

### 致命问题 2：plugin 块的位置与变化频率不匹配

```python
# client.py:1791-1819
if group_profile_block is not None:
    plugin_stable.append(group_profile_block)
# plugin blocks: affection, schedule, knowledge, slang, style, memo, sticker, food
```

很多 plugin 的 `on_pre_prompt` **声明 position=stable / static**，但内容**含每个用户/每个群独有的状态**（affection score、当前时间、当前可用知识块）。位置在前面 = 前缀污染。

`prompt_builder.py:111-149` 的 `build_blocks()` 当前已经把 dynamic 全部放最后——结构上对的。问题是 **plugin 自己声明的 position 是否准确**，以及 **dynamic 段尺寸 / 静态段尺寸的比例**。

### 致命问题 3：compact 用 LLM 生成总结

```python
# client.py:2400+
new_summary, memo_writes = await self._compact_with_tools(...)
# new_summary 来自 LLM 生成，每次不同
```

每次 compact 后，新的"对话摘要"作为 stable prefix → 但这个 prefix 每次都不同 → compact 之后整段历史等同于全新前缀，命中率瞬间归零。

→ **Claude Code 做法**：用 sentinel 字符串占位，老内容**不调 LLM**。Omubot 当前这个设计就是反 cache 的。

### 致命问题 4：所有 task 共享一个 `last_cache_hit_pct`

`config/config.json:70-79` 把 main / thinker / compact / slang 全映射到 main profile → 一个变量被多个 task 覆盖。这不是 cache 命中率本身的问题，但是诊断口径混乱，让用户以为"主回复链路退化了"，实际是 thinker 和 slang 这种短调用拉低了整体。

### 次要问题：每条 user message 内容前缀不稳定

`bot_self_id` 注入、QQ 号格式化、群成员 nickname 注入等都在前缀位置——但这些是**全群共享**的，群内多次调用会复用，问题不大。

---

## 9. 重新规划的改善方向

按 ROI 降序：

### P0-A：thinker system_prompt 稳定化

**做法**：参考 Claude Code 的 system block 分段顺序。

```python
# 推荐目标形态（services/llm/thinker.py:269-275 改）
system_blocks = [
    {"type": "text", "text": THINKER_SYSTEM_PROMPT_STATIC},  # 100% 稳定
    {"type": "text", "text": MOOD_BLOCK_DYNAMIC},            # 每轮变（在末尾）
    {"type": "text", "text": AFFECTION_BLOCK_DYNAMIC},       # 每轮变（在末尾）
]
```

DeepSeek 是字符串拼接 → `system_blocks` 会被合并成一个 system 字符串。所以**关键是顺序**：先全静态、后动态。这样前缀（静态部分）能命中，动态部分变化只影响 system 末尾。

**预期效果**：thinker 命中率 0.65% → 50%+；整体 38.6% → ~55%+。

### P0-B：plugin 块的位置规则严格化

**做法**：审计每个 `add_block()` 的 position 参数，按 Aider 的 ChatChunks 模式重组：

| 当前 plugin | 当前 position | 应该是 |
|---|---|---|
| sticker rules | static | static ✓ |
| sticker library view | stable | stable ✓ |
| memo (global index) | stable | stable ✓ |
| memo (per-user cards) | dynamic | dynamic ✓ |
| affection | dynamic | **必须 dynamic + 放在末尾** |
| schedule | dynamic | **必须 dynamic + 放在末尾** |
| slang (per-group terms) | dynamic | dynamic ✓ |
| knowledge | dynamic | dynamic ✓ |
| style | dynamic | dynamic ✓ |
| food | stable | **审计是否真的 stable，否则 dynamic** |
| context | dynamic | dynamic ✓ |
| group_profile_block | stable | **审计：每群独有内容应 dynamic** |

### P0-C：compact 改用 sentinel 替代 LLM 总结

**做法**：参考 Claude Code `Xi9`：

```python
COMPACTED_SENTINEL = "「以上为已折叠的历史对话」"
# 不调 LLM；直接把老 messages 替换为 sentinel
```

如果业务确实需要"摘要"信息，可以**在 sentinel 后面**额外加一段 LLM 生成的 metadata，但**老内容本身**用 sentinel 替代。这样每次 compact 之后老内容前缀稳定。

**预期效果**：compact 之后立刻"复活"前缀缓存命中。Omubot 5/17 没有触发 compact，效果不立刻可见，但长期价值很高。

### P1-D：客户端结构化 cache diagnostic

**做法**：在 `services/llm/client.py` 加一个 lite 版的 `Ti9`：

```python
@dataclass
class CacheDiagnostic:
    system_hash: int
    tools_hash: int
    per_block_hashes: list[int]
    per_block_lengths: list[int]
    message_hashes: list[int]
    timestamp: float

# 每次调用 _call 后存到 sqlite
# admin 页加一个"上次 break 是哪一段"的视图
```

**收益**：以后再有命中率异常，5 分钟就能定位是 system / tools / 哪条 message 变了，不用再跑 DeepSeek 后台对账。

### P1-E：usage.db 把 thinker / slang 都记上

**做法**：在 `_call` 内部按 task 自动 `_record_usage(call_type=task)`，删掉所有 8 处分散的 `_record_usage` 显式调用。

**收益**：本地审计能跟 DeepSeek 后台对得上账。

### P1-F：per-task 独立 `last_cache_hit_pct`

**做法**：把 `ProfileRateLimitState` 的 `last_cache_hit_pct` 改成 per-task：

```python
last_cache_hit_pct_by_task: dict[str, float] = {}
last_cache_hit_pct_by_task["main"] = ...
last_cache_hit_pct_by_task["thinker"] = ...
last_cache_hit_pct_by_task["slang"] = ...
```

admin 页拆分显示。

### 取消的方向（调研后判断不值得做）

- **黑话审核加 cache_control**：DeepSeek 不看这个字段；user payload 完全不同；system_prompt 占总 input 比例小。**接受 0% 命中**比强行优化更划算。
- **独立 profile / API key 拆分**：DeepSeek 按 API key 维度算缓存桶没错，但拆 key 只是"账面好看"，不改实质成本。还多一个 key 要管。

---

## 10. 总结对比表

| 改善项 | 原暂存方向 | 调研后状态 | 优先级 |
|---|---|---|---|
| thinker system_prompt 稳定化 | A（拼接位置改） | **保留并强化**（移到末尾，依据 Claude Code system block 顺序） | P0 |
| plugin 块 position 审计 | — | **新增**（依据 Aider ChatChunks） | P0 |
| compact 改 sentinel | — | **新增**（依据 Claude Code Xi9） | P0 |
| 客户端 cache diagnostic | — | **新增**（依据 Claude Code Ti9） | P1 |
| usage.db 加 thinker/slang | C | **保留** | P1 |
| per-task last_cache_hit_pct | — | **新增** | P1 |
| 黑话审核加 cache_control | B | **取消**（DeepSeek 不看） | — |
| 独立 profile / API key | D | **取消**（无实质收益） | — |

P0 三项一起落地，预期 **整体命中率 38.6% → 60%+**，节省 ~50% 输入 token 成本（DeepSeek 命中价格 0.014/M vs 未命中 0.14/M，10× 差异）。

---

## 11. GPT 复审结果（2026-05-18）

审计人：gpt

### 11.1 总体结论

本报告的核心方向成立：**Omubot 的 prompt cache 优化重点确实应放在稳定前缀、动态后置、分任务监控、usage 对账**，而不是盲目给 DeepSeek 加 `cache_control`。但报告不能直接作为 P0 实施单使用，原因是它混合了三类材料：

1. 已由当前代码支持的事实；
2. 对上游产品的旧价格/旧规则采样；
3. 从闭源 Claude Code binary 反编译得到的工程推测。

建议把它定位为“调研材料 + 改造假设”，并在落代码前先做一轮本地 trace / usage 对账验证。

### 11.2 高风险问题

#### P0 — 反编译资产不应进入可分发仓库

报告把 Claude Code 2.1.143 native binary 的 minified JS 片段归档到 [`_assets/prompt-cache-research-2026-05-18/`](./_assets/prompt-cache-research-2026-05-18/)，总计约 70 KB。虽然报告第 25 行写了“不可二次分发”，但文件已经位于 repo 文档目录下，一旦提交/同步就有合规风险。

处置建议：

- 不把 `_assets/prompt-cache-research-2026-05-18/*.js` 纳入提交。
- 用自研复现、公开文档、公开开源项目链接替换闭源代码片段。
- 保留报告中的高层观察可以，但不要把闭源反编译片段当作可复用实现规格。

#### P1 — DeepSeek 规则与价格信息已部分过期

报告 §1 / §10 仍使用 2024-08-02 公告中的 `$0.014/M` cache hit 价格；DeepSeek 当前官方定价页显示 `deepseek-v4-flash` 的 cache hit 价已调整为 `$0.0028/M`，cache miss 为 `$0.14/M`，命中价约为 miss 的 1/50，而不是报告写的 1/10。

同时，DeepSeek 当前 Context Caching 指南补充了 cache prefix unit 的持久化规则：request boundary、common prefix detection、fixed token intervals。也就是说，“64+ token 前缀完全相同就一定从下一次命中”的表述过于简化；同一静态 system + 不同 user payload 的场景，可能需要先经过 common prefix detection 才在后续请求命中。

处置建议：

- 把 §1 的机制表述改成“DeepSeek 自动缓存已持久化的 prefix unit，64 token 是旧公告中的存储单位，不等同于任意 64 token 前缀下一次必命中”。
- 把 §10 的成本测算改用当前模型定价，并标注价格需以官方定价页为准。

官方核验：

- DeepSeek Context Caching：`https://api-docs.deepseek.com/guides/kv_cache`
- DeepSeek Models & Pricing：`https://api-docs.deepseek.com/quick_start/pricing`

#### P1 — P0-B 的“plugin 块污染 system 前缀”结论与当前代码不完全一致

报告 §8.2 说 plugin dynamic 块仍在 system 前部污染前缀，但当前 `services/llm/client.py` 已有 DeepSeek V4 native 分支：

- `deepseek_native_main = main_api_format == "deepseek" and is_deepseek_v4_model(main_model)`；
- DeepSeek native main 下，`state_board` 和 `plugin_dynamic` 被放进 `tail_blocks`；
- `tail_blocks` 最后通过 `_append_tail_metadata()` 追加到尾部 user message；
- `build_blocks(..., plugin_dynamic=None, include_state_board=False)` 避免它们留在 system prefix。

这说明报告对 P0-B 的方向仍对，但严重度需要下调：当前剩余风险主要是 `plugin_static` / `plugin_stable` / `group_profile_block` 是否误标、private chat 路径是否同样稳定、以及 thinker hint 等最后 system block 是否导致 provider 差异。

处置建议：

- 先跑 cache diagnostic，证明哪些 block 真实变化，再逐项改 position。
- 不要按报告旧描述直接大改所有 plugin position。

本地证据：

- `services/llm/client.py:1653-1654`
- `services/llm/client.py:1821-1840`
- `services/llm/client.py:507-535`
- `tests/test_prompt.py:98`

#### P1 — “黑话审核接受 0% 命中”判断过重

报告 §1 已承认 DeepSeek 对 200-350 tokens 的黑话 system prompt 也可能缓存，但 §9 又取消黑话审核缓存方向，理由是 user payload 完全不同。这两处存在张力。

按 DeepSeek 当前指南，静态 system prompt + 不同 user payload 并不天然等于 0%：系统可能在多次请求后识别并持久化共同前缀。黑话审核 prompt 是否值得优化，应该由真实 `prompt_cache_hit_tokens / prompt_cache_miss_tokens` 分任务数据决定。

同时，当前代码也不是“四条入口全部直接 `_call(...)`”：

- `services/slang/extractor.py` 优先使用 `_call_slang`；
- `services/slang/drift_reviewer.py` / `semantic_reviewer.py` 优先找 `_call_slang_*`，退化到 `_call_slang`；
- `services/slang/review_utils.py` 仍直接 `_call`；
- `services/style/extractor.py` 也直接 `_call`，但报告未纳入 style。

处置建议：

- P1-E 先把 `_call` 层 usage 统一记录，并带上 `task`。
- 拿到 slang/style/thinker 的分任务缓存数据后，再决定是否放弃黑话/style 小模型调用的缓存优化。

### 11.3 中风险问题

#### P2 — 不应把 `cache_control` 全局当 dead code 清理

对当前 DeepSeek native provider 来说，`cache_control` 确实不参与缓存决策；但 Omubot 代码已经支持 `anthropic` / `openai` / `deepseek` 三类 provider。Anthropic 官方当前仍要求通过 top-level 或 block-level `cache_control` 启用/控制 prompt caching；OpenAI 当前是自动 prompt caching，并通过 `usage.prompt_tokens_details.cached_tokens` 暴露命中量。

因此清理建议必须限定为：

- DeepSeek native 请求构造层可以剥离无效 `cache_control` 字段，避免误导；
- Omubot 内部的 provider-neutral `cache_control` 标记不能全局删除；
- `OpenAIProvider` 目前没有解析 `cached_tokens`，如果未来启用 OpenAI 模型，usage/cache 统计会低估命中。

官方核验：

- Anthropic Prompt Caching：`https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching`
- OpenAI Prompt Caching：`https://platform.openai.com/docs/guides/prompt-caching`

#### P2 — “compact 改 sentinel”不能直接作为 P0 行为改动

报告把 P0-C 写成“compact 改用 sentinel 替代 LLM 总结”。这对 cache 前缀稳定有利，但与 Omubot 当前 compact 职责冲突：`_compact_with_tools()` 不只是省 token，还会输出对话摘要，并用 `add_card` 写记忆卡片。直接 sentinel 化会削弱长期记忆连续性和用户上下文。

更稳的实现路线：

1. 保留 LLM compact 的语义总结与 `add_card` 写入；
2. 将生成摘要从可缓存前缀移到动态尾部，或给摘要加版本化/稳定化策略；
3. 对历史 tool_result / 大块附件再采用 sentinel 占位；
4. 用 `tests/test_client.py` 补“compact 后仍保留摘要语义 / memo writes / cache prefix 不被新摘要污染”的回归。

#### P2 — 命中率提升数字应降级为假设

报告写“thinker 命中率 0.65% → 50%+、整体 38.6% → 55%+ / 60%+”。当前本地 `usage.db` 在 2026-05-17 只记录了 `proactive`，而 thinker reply 路径、slang review、style extractor 等仍未完整入账；这些数字缺少可复现实测支撑。

处置建议：

- 在 P1-E 前不要把 55% / 60% 当验收指标；
- 先验收“usage coverage ≥ 95%”和“per-task hit/miss 可见”；
- 再用 3-7 天数据给出真实目标线。

### 11.4 可保留结论

以下结论经过复审后仍建议保留：

- DeepSeek native cache 不需要客户端 `cache_control` 才能生效。
- Stable prefix / volatile suffix 是 Omubot 当前最有价值的优化方向。
- Thinker 的 `mood_text` prepend 会破坏 DeepSeek 前缀稳定，仍应优先修。
- `ProfileRateLimitState.last_cache_hit_pct` 当前按 profile 共享，会导致 main / thinker / slang 指标互相覆盖，应改成 per-task 或 per-profile+task。
- 客户端结构化 cache diagnostic 是必要投入；当前 `_log_cache_debug()` 只有单次 hash，没有持久化 diff 与 admin 可视化。

### 11.5 修订后的执行顺序

1. **A0：文档/资产清理**  
   移除或 gitignore 反编译 JS 资产；把闭源证据改成不可分发的本地笔记摘要。

2. **A1：usage 统一入账**  
   在 `_call` 层按 task 记录 usage，补 thinker reply、slang、style、compact 等分任务口径；同时保留主回复聚合。

3. **A2：轻量 cache diagnostic**  
   持久化 system/tools/messages 的结构化 hash，按 task+profile+group 记录 diff 原因。

4. **A3：thinker 前缀稳定化**  
   把 mood/affection 从 system 开头移到静态 thinker system 后方或尾部 metadata；验收 thinker task hit/miss 改善。

5. **A4：plugin position 复核**  
   基于 diagnostic 只修真实抖动的 `plugin_static/stable`，避免重复改已经通过 DeepSeek tail metadata 规避的问题。

6. **A5：compact cache 友好化**  
   先做“摘要不污染可缓存前缀”，再评估 tool_result sentinel，不直接删除语义摘要能力。

---

## 12. 引用与延伸阅读

- **Prompt Cache 论文**：[arxiv.org/abs/2311.04934](https://arxiv.org/abs/2311.04934) — schema-defined prompt modules with positional accuracy；本地副本 `/tmp/research/arxiv_2311.04934_PromptCache.html`
- **SGLang 论文**：[arxiv.org/abs/2312.07104](https://arxiv.org/abs/2312.07104) — RadixAttention KV reuse；本地 PDF `/tmp/research/cache-hit/sglang_paper.pdf`
- **Anthropic Cookbook**：[github.com/anthropics/anthropic-cookbook](https://github.com/anthropics/anthropic-cookbook/blob/main/misc/prompt_caching.ipynb) — automatic vs explicit breakpoint
- **DeepSeek Context Caching 公告**：[api-docs.deepseek.com/news/news0802](https://api-docs.deepseek.com/news/news0802) — 64-token unit, MLA, disk-backed
- **DeepSeek Context Caching 当前指南**：[api-docs.deepseek.com/guides/kv_cache](https://api-docs.deepseek.com/guides/kv_cache) — prefix unit persistence, hit/miss usage fields
- **DeepSeek Models & Pricing 当前定价**：[api-docs.deepseek.com/quick_start/pricing](https://api-docs.deepseek.com/quick_start/pricing) — deepseek-v4-flash/pro cache hit/miss price
- **Anthropic Prompt Caching 当前文档**：[docs.anthropic.com/en/docs/build-with-claude/prompt-caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching) — automatic caching, explicit breakpoints, TTL, minimum tokens
- **OpenAI Prompt Caching 当前文档**：[platform.openai.com/docs/guides/prompt-caching](https://platform.openai.com/docs/guides/prompt-caching) — automatic caching, `cached_tokens`, retention policy
- **Aider chat_chunks.py**：[github.com/Aider-AI/aider/.../chat_chunks.py](https://github.com/Aider-AI/aider/blob/main/aider/coders/chat_chunks.py) — 教科书级应用层缓存设计
- **Cline anthropic provider**：[github.com/cline/cline/.../anthropic.ts](https://github.com/cline/cline/blob/main/src/core/api/providers/anthropic.ts)
- **本研究的 Claude Code 反编译产物**：[`_assets/prompt-cache-research-2026-05-18/`](./_assets/prompt-cache-research-2026-05-18/)

---

## 附录 A：调研中间态（最初的 4 方向暂存）

> 本附录是 2026-05-18 调研开始**前**的初步判断，已被正文 § 8 / § 9 替代。保留作为认知演进过程的记录——记录了"调研改变了哪些判断"。

### A.1 当前观测对账（2026-05-17，与正文 § 2 一致）

| 来源 | 输入未命中 | 输入命中 | 命中率 |
|---|---:|---:|---:|
| DeepSeek 后台 | 1,640,814 | 1,031,168 | **38.6%** |
| 本地 usage.db (proactive) | 256,938 | 267,648 | 51.0% |

### A.2 暂存的 4 个改善方向（以及调研后的命运）

#### 方向 A：thinker system_prompt 稳定化 → **保留并强化**（成为 P0-A）

调研前认为：把 mood/affection 移到 user 消息段。
调研后判断：保留方向，但具体做法升级为"放到 system 末尾而非 user 段"——这样既保前缀稳定，又不让 thinker 把 mood 当成"用户在说话"。

#### 方向 B：黑话审核加 cache_control → **取消**

调研前认为：四个 entry point 的 system_prompt 全部未设 cache_control，且 200–350 tokens 卡在阈值下，强行加长前缀让缓存生效。
调研后判断：**完全错的诊断**。DeepSeek 阈值是 64 tokens 不是 1024；DeepSeek 不看 cache_control 字段；黑话调用 user payload 每次完全不同，前缀本就无法稳定。**取消方向**——结构性 0% 命中是任务特征，不是 bug。

#### 方向 C：usage.db 加 thinker/slang → **保留**（成为 P1-E）

调研前认为：让本地审计能跟 DeepSeek 后台对得上账，避免下次又搞错口径。
调研后判断：保留，依据更扎实——Claude Code 的 `Pm` map 按 querySource 分桶就是这个思路的极致版。

#### 方向 D：task → 独立 profile → **取消**

调研前认为：拆 key 让"主回复"账面命中率不被 thinker 拖低。
调研后判断：纯账面工程，DeepSeek 按 API key 维度统计本来就是对的——拆 key 的实质收益只是"看着舒服"，多一个 key 要管。**取消方向**。

### A.3 调研给的额外发现（不在原 4 方向里）

- **plugin 块 position 审计**（Aider ChatChunks）→ 成为 P0-B
- **compact 改 sentinel 替代 LLM 总结**（Claude Code Xi9）→ 成为 P0-C，且这是原方向**完全没意识到**的反 cache 设计
- **客户端结构化 cache diagnostic**（Claude Code Ti9）→ 成为 P1-D
- **per-task `last_cache_hit_pct`** → 成为 P1-F

---

**调研归档完成时间**：2026-05-18
**预期下次更新**：P0-A/B/C 实施后追加"实测命中率变化"附录。

---

## § 12. LLM 调用统一抽象（Spine Refactor）：硬前置基石补丁

> **本节定位**：§ 9 的 P0-A/B/C 是点状修补，**不构成根治**。多层记忆框架（[multilayer-memory-learning-report-2026-05-17.md](./multilayer-memory-learning-report-2026-05-17.md)）Phase A.5 / Phase B / Phase D 都会引入新 LLM 调用点，每加一处就重新踩同样的坑。本节给出在多层记忆框架开工前**必须落地**的架构级修复。
>
> **硬约束**：本节 Spine Refactor 在 multilayer-memory Phase A0 开工**之前**完成。否则 Phase A.5 graph reviewer / Phase D reflection consolidator / Phase B BlockTraceBus 全部会在缓存链路上重新踩坑。
>
> **依据**：本节首版基于 2026-05-18 14:00 codebase；本次自审追加于 2026-05-18 21:00。
>
> **修订追踪（2026-05-18 自审第二轮）**：
> 自首版（2026-05-18 14:00）以来已落地的部分：
> - reply_gate 隐藏 bug 已修（[`services/llm/client.py:1134`](../../services/llm/client.py#L1134) `_call_reply_gate`）。
> - [`services/llm/llm_request.py`](../../services/llm/llm_request.py) 已实现 `LLMRequest` dataclass + `LLMTask` Literal + `LLMCapability` Literal + `system_blocks()` 强制顺序合成 + `to_provider_payload()`；含 `chat_private` / `bilibili_intent` / `element_detect` / `graph_review` / `graph_edge_classifier` / `reflection_consolidator` / `episode_summarizer` 等 plugin / Phase A.5+ 预留 task。
> - [`services/llm/cache_diagnostic.py`](../../services/llm/cache_diagnostic.py) 已实现 `CacheDiagnostic` + `compute_cache_diagnostic()` + `diff_cache_diagnostics()`（Claude Code `Ti9` 等价）。
> - [`tests/test_llm_request.py`](../../tests/test_llm_request.py) + [`tests/test_cache_diagnostic.py`](../../tests/test_cache_diagnostic.py) 共 **43 用例全通过**。
> - admin/system 已有 LLM 面板（[`admin/routes/api/providers.py`](../../admin/routes/api/providers.py) `/providers` GET + `/providers/selection` POST），task → profile 可在 UI 切换并热加载（[`services/llm/client.py:1170`](../../services/llm/client.py#L1170) `set_task_profiles`），`_LLM_TASKS = all_llm_tasks()` 已经从 `LLMTask` 同步。
> - [`kernel/config.py:73`](../../kernel/config.py#L73) `LLMProfileConfig.capabilities` 字段早已存在并由 admin 暴露。
>
> 仍未做的部分（剩余工作 — 见 § 12.5 重排步骤）：
> - `LLMClient._call` 仍是六位置参数签名（`system_blocks, messages, tools, max_tokens, thinking, task=...`），**未改成接受 `LLMRequest`**；spine 数据类有了但没人用。
> - 13 个调用点全部仍走旧路径：thinker / compact ×3 / slang ×4 / style / chat 私聊 ×2 / chat plugin 主入口 / bilibili / element_detector / memo / 主回复。
> - `requires_capabilities` 字段 dataclass 已声明，但 `_call` 内部**没有任何 fail-fast 校验逻辑**。
> - `_call` 内部**没有自动 `_record_usage(call_type=task)`**；slang / style / plugin 路径仍是 usage.db 盲区。
> - `_call` 内部**没有自动 `compute_cache_diagnostic` + 持久化**；admin 没有 cache diff 视图。
> - `ProfileRateLimitState.last_cache_hit_pct` 仍是 per-profile 单一字段（非 per-task）。
> - `LLMTask` Literal 与 `admin/frontend/.../helpers/types.ts ProviderTaskKey` 同步**没有 CI 自动守门**（首版注释说"由测试守门"，事实上未实现）。

### 12.1 LLM 调用点完整盘点（验证后）

实际调用点比我之前估算的 9 个多，且**有一处隐藏 bug**：

| # | 调用点 | 位置 | 状态 | 备注 |
| --- | --- | --- | --- | --- |
| 1 | proactive/chat 主回复 | `services/llm/client.py:1631-2212` `chat()` 方法 | ✓ 已存在 | **581 行的复杂方法**，含 group/private 双路径、thinker 决策、tool loop、reply finalize |
| 2 | thinker | `services/llm/thinker.py:269-275` → `client.py:1086 _call_thinker` | ✓ 已存在 | 字符串拼接 mood/affection |
| 3 | compact | `client.py:2212 _compact_with_tools` + `2346 _compact` + `2433 _compact_group` → `_call_compact:1102` | ✓ 已存在 | 调 LLM 生成总结（反 cache） |
| 4 | slang extractor | `services/slang/extractor.py:80-81` | ✓ 已存在 | `getattr(client, "_call_slang", client._call)` 动态分派 |
| 5 | slang review | `services/slang/review_utils.py:167` | ✓ 已存在 | 直接 `llm_client._call(...)`，绕过 task 标识 |
| 6 | slang drift | `services/slang/drift_reviewer.py:101-107` | ✓ 已存在 | 多级 fallback：`_call_slang_drift` → `_call_slang_review` → `_call_slang` → `_call` |
| 7 | slang semantic | `services/slang/semantic_reviewer.py:336-358` | ✓ 已存在 | 三阶段调用 `_call_stage`，同样多级 fallback |
| 8 | style extractor | `services/style/extractor.py:158-160` | ✓ 已存在 | 直接 `_llm_client._call(...)` |
| 9 | bilibili plugin | `plugins/bilibili/plugin.py:810` | ✓ 已存在 | 直接 `_llm_client._call(...)` |
| 10 | element_detector plugin | `plugins/element_detector/plugin.py:131` | ✓ 已存在 | 直接 `_llm_client._call(...)` |
| 11 | memo plugin | `plugins/memo/plugin.py:91` | ✓ 已存在 | `self._call(...)`（plugin 内部封装） |
| 12 | chat plugin（私聊 + dream 注入） | `plugins/chat/plugin.py:197, 277, 832` | ✓ 已存在 | 三处独立 `ctx.llm_client._call(...)`（其中 `:832` 把 `llm._call` 作为 `api_call` 注入给 dream agent，最终落入 chat 主回复或 compact） |
| 13 | reply_gate | `kernel/router.py:884` → `client.py:1134 _call_reply_gate` | ✓ 已修复（2026-05-18） | Step 0 已落地：薄 wrapper 已加，task=`"reply_gate"`，deepseek thinking 自动禁用列表已加该 task。详见 [maintenance-log.md L7](../../maintenance-log.md) |
| 14 | vision | 未实现 | 配置中预留（`config/config.json:78` `task_profiles.vision`） | 路径未存在；`LLMTask` Literal 已预留 |

**14 处调用点：13 处现有（含 reply_gate Step 0 已修）+ 1 处未实现（vision）**。

#### dream / schedule / food 三处的 `api_call` 注入归属

`plugins/dream/plugin.py:504`、`plugins/schedule/plugin.py:54-63`、`plugins/food/plugin.py:725` 不是新调用点：它们把 `ctx.llm_client._call`（或某个 wrapper）当作 `api_call` 注入给 plugin 内部的工作类（DreamAgent / ScheduleGenerator / FoodAgent）。最终调用还是落进上表 12 / 11 / 1 等格子。spine 迁移时这三处需要决定是各自声明 `dream_consolidate` / `schedule_generate` / `food_intent` 这类独立 task，还是直接复用现有 task；建议在 § 12.5 阶段 C 的 C.7（compact）/ C.8（chat_private + dream 注入）里临时复用，等多层记忆 Phase D 真正引入 reflection consolidator 时再独立成 `LLMTask`。

### 12.2 多层记忆框架将引入的新调用点

参考 [multilayer-memory-learning-report-2026-05-17.md](./multilayer-memory-learning-report-2026-05-17.md)：

| Phase | 新调用点 | 引入位置 | LLM 用途 |
|---|---|---|---|
| Phase A.5 | knowledge_graph fact reviewer | 待定 | 从 75 个 candidate 中筛选 active fact |
| Phase A.5 | graph edge classifier | 待定 | slang ↔ style ↔ episode 跨层关系判别 |
| Phase B | BlockTraceBus 不引入 LLM 调用 | — | 仅 trace 写入，不调 LLM |
| Phase D | reflection consolidator | 待定 | 把 episodes 聚合为 declarative facts |
| Phase D | episode summarizer | 待定 | 生成 episode 文本表示 |
| Phase F | declarative fact F.6 rollback | 待定 | 失效 fact 引用追溯 |

**至少 5 个新 LLM 调用点会在 Phase A.5–F 落地**。如果 spine 不先到位，每一处都会：

1. 自己拼字符串 system_prompt（→ 每次写代码的人都要重新决定 mood/state 怎么放）
2. 忘记调 `_record_usage`（→ usage.db 又一处盲区）
3. 没有 cache diagnostic（→ 命中率掉了不知道是哪一处）
4. 没有 task 标识（→ admin/system 页 last_cache_hit_pct 继续被瞬时值覆盖）

→ Spine refactor 是 Phase A.5 / Phase D 的**硬前置**。

### 12.3 Spine 设计：LLMRequest 数据类 + 强制三段结构

> **进度提示**：以下"目标形态"中，`LLMTask` Literal、`LLMCapability` Literal、`LLMRequest` dataclass、`system_blocks()` 强制顺序合成、`to_provider_payload()`、cache_diagnostic 模块**均已实现并通过 43 用例**（[`services/llm/llm_request.py`](../../services/llm/llm_request.py) / [`services/llm/cache_diagnostic.py`](../../services/llm/cache_diagnostic.py)）。本节保留为完整参考，凡是与现存代码有差异的，以现存代码为准；本次自审已对齐。

参考 Claude Code 的 `Pm` 按 querySource 分桶 + Aider 的 ChatChunks 八段固定结构，结合 Omubot 的 prompt_builder 已有的 static/stable/dynamic 三段语义：

```python
# services/llm/llm_request.py（已实现）
from dataclasses import dataclass, field
from typing import Any, Literal

LLMTask = Literal[
    # ── 核心链路（已存在调用点） ──
    "main", "thinker", "compact", "reply_gate", "vision",
    # ── slang 治理 ──
    "slang", "slang_review", "slang_drift", "slang_semantic",
    # ── 其他学习 / 抽取 ──
    "style", "memo",
    # ── plugin 调用点（spine 迁移时按调用方独立命名） ──
    "chat_private",          # plugins/chat/plugin.py:197/277/832
    "bilibili_intent",       # plugins/bilibili/plugin.py:810
    "element_detect",        # plugins/element_detector/plugin.py:131
    # ── Phase A.5+ 多层记忆框架预留 ──
    "graph_review", "graph_edge_classifier",
    "reflection_consolidator", "episode_summarizer",
]

LLMCapability = Literal["chat", "tools", "thinking", "vision", "json", "compact"]
```

> **Plugin task 命名约定（spine 迁移强制项）**：
> - 每个 plugin 直接 `_call(...)` 的入口必须在 `LLMTask` Literal 中独立命名（不允许复用 `"main"`）
> - `task_profiles` 默认全部映射到 `"main"` profile（行为保持一致），但任何插件可以通过 admin 面板（[`admin/routes/api/providers.py`](../../admin/routes/api/providers.py) `/providers/selection` POST）单独切换到 thinker / vision / 自定义 profile，**无需改代码**，无需重启 — 已支持热加载（`set_task_profiles`）
> - `admin/routes/api/providers.py:19` 的 `_LLM_TASKS = all_llm_tasks()` 已经从 `LLMTask` Literal 自动同步，无需手维
> - `admin/frontend/src/views/system/helpers/types.ts ProviderTaskKey` 当前**没有 CI 守门**与 `LLMTask` 同步；spine PR 必须加一个 build-time 守门测试或脚本（详见 § 12.5 Step D.7）

```python
@dataclass
class LLMRequest:
    """统一 LLM 调用契约。所有调用点必须构造此对象，禁止再字符串拼接 system_prompt。"""
    task: LLMTask
    user_id: str = ""
    group_id: str | None = None

    # System prompt 强制分三段：static → stable → dynamic
    # static: byte-stable across all calls of this task（identity prompt、output schema 说明）
    # stable: 偶尔变化（per-group profile、tool library view）
    # dynamic: 每轮变化（mood、affection、当前时间、当前对话状态）
    static_blocks: list[str | dict[str, Any]] = field(default_factory=list)
    stable_blocks: list[str | dict[str, Any]] = field(default_factory=list)
    dynamic_blocks: list[str | dict[str, Any]] = field(default_factory=list)

    user_messages: list[Any] = field(default_factory=list)
    tools: list[dict[str, Any]] | None = None
    max_tokens: int = 1024
    thinking: dict[str, Any] | None = None

    # provider-neutral 输出格式声明（供 capability 校验用，不影响 prompt 拼装）
    requires_capabilities: tuple[LLMCapability, ...] = ()  # e.g. ("tools",) / ("vision",) / ("thinking",)

    def system_blocks(self) -> list[dict[str, Any]]: ...   # 已实现：static → stable → dynamic
    def to_provider_payload(self) -> tuple[list[dict[str, Any]], list[Any], list[dict[str, Any]] | None]: ...  # 已实现
```

> **Capability 校验（spine 落地时一并加）**：
> `LLMRequest.requires_capabilities` 字段已落地，但 `_call` 内部**还没有真正读它**。落地时加 fail-fast：进入 `_call` 时若 `task` 解析到的 profile 缺声明的 capability，**抛 `ValueError(f"profile {name!r} missing capability {cap!r}")`**（不是 fail-silent）。这样 admin 把 vision task 误配到 chat-only profile 会立刻报错，避免再出现 reply_gate 那种"调用方法不存在/能力不匹配"被静默吞掉。

`LLMClient._call` 改成只接受 `LLMRequest`：

```python
# services/llm/client.py — 目标签名（当前仍是 6 位置参数，未改造）
async def _call(self, req: LLMRequest) -> dict[str, Any]:
    """所有 LLM 调用唯一入口。系统会自动：
    1. 按 static/stable/dynamic 顺序合成 system blocks（前缀稳定）— 已由 LLMRequest.system_blocks() 保证
    2. 按 task 路由到对应 profile — 复用现存 _profile_for_task()
    3. 校验 profile.capabilities ⊇ req.requires_capabilities，否则 ValueError（新增）
    4. 自动记录 usage.db（call_type=req.task）— 当前仅 main/proactive 路径有，需迁移
    5. 自动算 cache diagnostic — compute_cache_diagnostic 已实现，需在 _call 中调用并持久化
    6. 按 task 维度更新 ProfileRateLimitState.last_cache_hit_pct_by_task — 字段需新增
    """
    system, messages, tools = req.to_provider_payload()
    profile_name, base_url, api_key, model, api_format = self._profile_for_task(req.task)
    self._enforce_capabilities(profile_name, req.requires_capabilities)  # 新增

    result = await call_api(
        system=system, messages=messages, tools=tools,
        profile=profile_name, base_url=base_url, api_key=api_key,
        model=model, api_format=api_format,
        max_tokens=req.max_tokens, thinking=req.thinking,
    )

    self._record_usage(call_type=req.task, ...)            # 现有方法，spine 把它收进 _call 内一处
    self._record_cache_diagnostic(req.task, system, tools, messages, result)  # 新增
    return result
```

新调用方代码示例（迁移后）：

```python
# services/llm/thinker.py 改造后（核心：mood/affection 移到 dynamic_blocks 末尾，static_blocks 不再被 prepend 污染）
req = LLMRequest(
    task="thinker",
    user_id=user_id,
    group_id=group_id,
    static_blocks=[THINKER_SYSTEM_PROMPT.format(name=identity_name)],
    dynamic_blocks=[mood_text, affection_text],  # 物理上必然在 system 末尾，不再 prepend
    user_messages=[*recent_for_thinker, {"role":"user","content":"<thinker_query>"}],
    max_tokens=1024,
    thinking=thinking_config,
    requires_capabilities=("chat",) + (("thinking",) if thinking_config else ()),
)
result = await client._call(req)

# services/slang/extractor.py 改造后（删 getattr 多级 fallback）
req = LLMRequest(task="slang", static_blocks=[_SYSTEM_PROMPT],
                 user_messages=[{"role":"user","content":body}], max_tokens=900)
result = await llm_client._call(req)

# services/slang/semantic_reviewer.py 改造后（之前的 _call_slang_review → _call_slang → _call 三级 getattr 全删）
req = LLMRequest(task="slang_semantic", static_blocks=[_CONTEXT_SYSTEM_PROMPT],
                 user_messages=[{"role":"user","content":payload}], max_tokens=300)
result = await llm_client._call(req)
```

→ **任何新 LLM 调用都被强制声明 static/stable/dynamic 三段**。想把动态内容放前面？数据类型不允许（`static_blocks` 名字就告诉你这段必须不变）。忘了记 usage？`_call` 自动记。命中率掉了？per-axis hash 自动告诉你哪段变了。误配 capability？fail-fast 直接抛错。

### 12.4 ROI 与排期

#### 工作量重估（v3，自审第二轮）

> 阶段 A 骨架（LLMRequest + cache_diagnostic + admin 基础 + 43 测试用例）已落地，下表只列剩余工作量。详细 Step 列表见 § 12.5。

| 阶段 | 估算 | 风险 |
| --- | --- | --- |
| 阶段 A 骨架（dataclass / cache_diagnostic / admin 面板 / 43 测试） | ✅ 已完成 | — |
| 阶段 B `_call` 接入（`LLMRequest` 重载 + capability 校验 + 自动 usage / cache_diagnostic + per-task hit pct） | 1 天 | 中（旧 49 例不能退） |
| 阶段 C 13 处调用点迁移（含 thinker mood 移末尾 P0-A + compact sentinel P0-C） | 2 天 | 中（main chat 581 行最复杂） |
| 阶段 D 清理 + admin 扩展（per-task 表 + cache diff 视图 + capability 提示） + staging 灰度 | 1 天 | 低（admin 基础设施已存在） |
| **总计（剩余）** | **4 天** | — |

修订路径（v1 → v3）：

- v1（首版）：4–4.5 天，假设全部从零
- v2（同日修订）：4.5–5 天，加入 plugin task 命名 + capability 校验 + admin 同步 + D2 cancel-path
- v3（自审第二轮）：**4 天**，盘点真实进度后确认阶段 A 骨架已落地（约 1 天工作量已沉淀），相应减除

#### 与"只做 P0-A/B/C"的对比

| 维度 | 仅 P0-A/B/C | Spine + P0 |
| --- | --- | --- |
| 工作量（剩余） | 0.5–1 天 | 4 天（阶段 A 骨架已完成） |
| 解决当前 38.6% 命中率 | 是 | 是（且更快定位回归） |
| 保护未来 5+ 新调用点 | **否**（每加一处重新踩坑） | **是**（强制契约 + capability 校验） |
| reply_gate 类隐藏 bug | 仍可能复发 | ✅ 已解决 + 类型系统拦截未来同类问题 |
| usage.db 80% 流量盲区 | 不解决（旧方向 C 单独修） | 自动覆盖（含 plugin task） |
| 后续诊断速度 | 跑 DeepSeek 后台对账 | 5 分钟定位 axis |
| Plugin 自由路由（任 provider） | 仅 main/thinker/slang/vision 可配 | 全部 plugin task 可在 admin 面板热切换 |
| 多层记忆 Phase A.5 / D 兼容性 | **每个新调用点重做一次审计** | 直接套用 spine |

→ **建议必做 Spine**。理由：

1. **不可逆的延迟成本**：Phase A.5 / D 已经在路线图上，必然引入新调用。如果不先做 spine，每个新调用都要写"这次记得放末尾、记得加 cache_control、记得调 `_record_usage`"的 review 清单，且每次都会有人忘。
2. **诊断速度**：cache diagnostic 自动化对未来 6 个月每一次"为什么命中率变了"调研都生效。
3. **集中花一次成本**：13 处迁移 + 5 处未来新增 = 18 处。集中改 13 处比分 18 次踩坑+修复+复审低。
4. **类型系统永久消灭 reply_gate-style 隐藏 bug**。

### 12.5 落地步骤（修订版 v3，2026-05-18 自审第二轮）

> **本轮自审增量**：v2 假设 spine 全部从零开始，实际盘点后发现骨架已经落地（dataclass / cache_diagnostic 模块 / 43 用例 / admin 面板基础）。Step 改为"已完成 / 进行中 / 待开"三态分阶段，避免后续工程师把已做的工再做一遍。

#### 阶段 A：骨架（✅ 已完成）

| Step | 工件 | 状态 | 证据 |
|------|------|------|------|
| Step 0 | reply_gate 隐藏 bug 修复 | ✅ | [client.py:1134](../../services/llm/client.py#L1134)；maintenance-log L7 |
| Step A.1 | `LLMRequest` dataclass + `LLMTask` Literal（含 plugin / Phase A.5+ 预留） | ✅ | [llm_request.py:30-58](../../services/llm/llm_request.py#L30) |
| Step A.2 | `LLMCapability` Literal + `requires_capabilities` 字段 | ✅ | [llm_request.py:66, 155](../../services/llm/llm_request.py#L66) |
| Step A.3 | `system_blocks()` 强制 static→stable→dynamic 顺序 + 空段剥离 | ✅ | [llm_request.py:157-178](../../services/llm/llm_request.py#L157) |
| Step A.4 | `cache_diagnostic.py`：`compute_cache_diagnostic` + `diff_cache_diagnostics` | ✅ | [cache_diagnostic.py](../../services/llm/cache_diagnostic.py) |
| Step A.5 | 骨架测试 43 例（test_llm_request 30 + test_cache_diagnostic 13） | ✅ | `pytest tests/test_llm_request.py tests/test_cache_diagnostic.py` 全过 |
| Step A.6 | admin `_LLM_TASKS = all_llm_tasks()` 自动同步 | ✅ | [providers.py:19](../../admin/routes/api/providers.py#L19) |
| Step A.7 | admin `/providers` GET + `/providers/selection` POST + 热加载（`set_task_profiles`） | ✅ | [providers.py:65-156](../../admin/routes/api/providers.py#L65) / [client.py:1170](../../services/llm/client.py#L1170) |
| Step A.8 | admin 前端 SystemProviders.vue（task → profile 选择 UI） | ✅ | [SystemProviders.vue](../../admin/frontend/src/views/system/components/SystemProviders.vue) |
| Step A.9 | `LLMProfileConfig.capabilities` 字段 + admin `capability_options` 暴露 | ✅ | [config.py:73](../../kernel/config.py#L73) / [providers.py:273-279](../../admin/routes/api/providers.py#L273) |

#### 阶段 B：接入 `_call`（待开，估 1 天）

| Step | 工件 | 工作量 | 备注 |
|------|------|--------|------|
| Step B.1 | `_call` 增加 `LLMRequest` 重载（保留旧 6 位置参数签名作迁移期 shim） | 0.3 天 | 用 `if isinstance(arg, LLMRequest)` 分支；旧 `_call_thinker` / `_call_compact` / `_call_slang` / `_call_reply_gate` 暂不删 |
| Step B.2 | `_call` 内部 `_enforce_capabilities()`：`req.requires_capabilities ⊄ profile.capabilities` → fail-fast `ValueError` | 0.1 天 | 含 unit test，profile.capabilities 为空时按 `["chat"]` 兜底 |
| Step B.3 | `_call` 内部自动 `_record_usage(call_type=req.task, ...)` | 0.2 天 | 走 `LLMRequest` 路径时其他位置不再显式调，避免双写 |
| Step B.4 | `_call` 内部自动 `compute_cache_diagnostic` + 持久化 | 0.2 天 | sqlite ring buffer：`task / ts / system_hash / tools_hash / per_block_hashes_json / message_hashes_json`，保留最近 N=200 条 |
| Step B.5 | `ProfileRateLimitState.last_cache_hit_pct_by_task: dict[str, float]` 字段新增，每次 `_call` 后更新 | 0.1 天 | 旧 `last_cache_hit_pct` 保留作总和兜底 |
| Step B.6 | 测试：`tests/test_client.py` 49 例不退；新增 `LLMRequest` 路径 5–8 例 + capability mismatch fail-fast 1 例 + cancel-path D2 1 例 | 0.1 天 | D2：`pytest.raises(TimeoutError)` 模拟 `wait_for` 取消，断言 usage / cache_diagnostic 不写入半截 row |

**阶段 B 验收闸**：`pytest tests/test_client.py tests/test_llm_request.py tests/test_cache_diagnostic.py` 全过；旧 wrapper 仍正常；新 `LLMRequest` 路径冒烟通过。

#### 阶段 C：13 处调用点迁移（待开，估 2 天）

> 每处独立 commit / 小 PR，落完立即在 staging `usage.db` 中确认新 task 名出现，再做下一处。

| Step | 调用点 | task 名 | 工作量 | P0 内容 |
|------|--------|---------|--------|---------|
| Step C.1 | [style/extractor.py:158](../../services/style/extractor.py#L158) | `style` | 0.5h | — |
| Step C.2 | [bilibili/plugin.py:810](../../plugins/bilibili/plugin.py#L810) | `bilibili_intent` | 0.5h | — |
| Step C.3 | [element_detector/plugin.py:131](../../plugins/element_detector/plugin.py#L131) | `element_detect` | 0.5h | — |
| Step C.4 | [memo/plugin.py:91](../../plugins/memo/plugin.py#L91) | `memo` | 0.5h | — |
| Step C.5 | slang ×4：extractor / review_utils / drift / semantic | `slang` / `slang_review` / `slang_drift` / `slang_semantic` | 3h | 同步删 `getattr` 多级 fallback |
| Step C.6 | [thinker.py:269-275](../../services/llm/thinker.py#L269) | `thinker` | 1h | **P0-A：mood/affection 移到 `dynamic_blocks` 末尾**，static 不再被 prepend 污染 |
| Step C.7 | compact ×3：[client.py:2212/2346/2433](../../services/llm/client.py#L2212) | `compact` | 2h | **P0-C：sentinel 替代 LLM 总结**，但保留 add_card 与 memo writes 语义（依 §11.3 P2 修订意见，不直接删除摘要能力） |
| Step C.8 | chat 私聊 + dream 注入 ×3：[chat/plugin.py:197/277/832](../../plugins/chat/plugin.py#L197) | `chat_private` | 2h | dream 注入处（`:832`）评估是否独立 task；spine 阶段保持复用，标 TODO |
| Step C.9 | **main chat（最复杂）**：[client.py:1631-2212](../../services/llm/client.py#L1631) `chat()` 581 行 | `main` | 2–3h | 涉及 group/private 双路径、thinker 决策、tool loop、reply finalize；先抽出 `_build_main_request()` 工厂方法再迁移 |

**阶段 C 验收闸**：`grep -rn "ctx\.llm_client\._call\b\|llm_client\._call(\b\|self\._call_thinker\|self\._call_compact\|self\._call_slang\|self\._call_reply_gate\|getattr(.*_call_slang" services/ plugins/ kernel/` 无遗漏；`usage.db` 出现 ≥ 8 个新 task 名。

#### 阶段 D：清理 + admin 视图 + 灰度（待开，估 1 天）

| Step | 工件 | 工作量 | 备注 |
|------|------|--------|------|
| Step D.1 | 删除 `_call_thinker` / `_call_compact` / `_call_slang` / `_call_reply_gate` 四个 wrapper | 0.2 天 | 含 13 处迁移完成的双重确认 |
| Step D.2 | 删除 `_call` 旧 6 位置参数签名 shim，唯一签名变成 `_call(req: LLMRequest)`；删 `getattr(client, "_call_slang_*", ...)` 多级 fallback | 0.2 天 | — |
| Step D.3 | D3 迁移清单：`docs/migrations/spine-2026-05-XX.md`，四列：旧 wrapper / 旧调用点 / 新 task / 新代码位置 | 0.1 天 | 与最终 PR 一同提交 |
| Step D.4 | admin/system 页 per-task 命中率表格（拉 `last_cache_hit_pct_by_task`） | 0.2 天 | SystemProviders.vue 增 per-task 列表 |
| Step D.5 | admin/system 页 cache diagnostic 视图：「最近 break 是哪段变了」 | 0.2 天 | 拉 Step B.4 的 sqlite 表，前端按 system / tools / message[N] axis 与时间线展示 |
| Step D.6 | admin task_profiles 选择器旁加 capability 兼容性提示（profile 缺声明 capability 时高亮） | 0.1 天 | 已有 `capability_options` 暴露，前端做 client-side 校验 |
| Step D.7 | `LLMTask` Literal ↔ `ProviderTaskKey` (TS) 一致性守门测试 | 0.1 天 | `tests/test_admin_providers.py` 中读 ts 文件 grep 校验，防止改 Literal 忘改前端 |
| Step D.8 | staging 24h 灰度：对比 DeepSeek 后台命中率 vs 本地 usage.db per-task 分布 | 0.2 天 | 出灰度报告写入 maintenance-log |
| Step D.9 | （可选）观察 3–7 天后回写"实测命中率改善" | — | 不预设阈值，按真实分任务数据定基线 |

**阶段 D 验收闸**：grep 不出任何旧 wrapper / 旧 `_call` 6 位置调用；admin per-task 表 + cache diagnostic 视图工作；staging usage 与 DeepSeek 后台对账缺口 ≤ 5%。

#### 总工作量（v3 修订）

| 阶段 | 工作量 | 累计 | 风险 |
|------|--------|------|------|
| 阶段 A 骨架 | ✅ 已完成 | — | — |
| 阶段 B `_call` 接入 | 1 天 | 1 天 | 中（旧 49 例不能退） |
| 阶段 C 13 处迁移 | 2 天 | 3 天 | 中（main chat 581 行最复杂，含 P0-A/C） |
| 阶段 D 清理 + admin + 灰度 | 1 天 | 4 天 | 低（admin 基础设施已存在） |
| **总计（剩余）** | **4 天** | — | — |

比 v2 的 4.5–5 天**少 0.5 天**，差异来自阶段 A 骨架已沉淀进现有代码（约 1 天工作量已落地）。


### 12.6 验收标准

#### 已达（阶段 A）

- [x] reply_gate 隐藏 bug 已修（Step 0，2026-05-18）
- [x] `LLMRequest` dataclass + `LLMTask` Literal + `LLMCapability` Literal 落地（[llm_request.py](../../services/llm/llm_request.py)）
- [x] `system_blocks()` 强制 static→stable→dynamic 顺序，类型上不可乱序（30 个用例）
- [x] `cache_diagnostic.py` 提供 per-axis hash + diff classifier（13 个用例）
- [x] admin/system 页 task → profile 选择器存在并支持热加载（无需重启）
- [x] admin 暴露 `capability_options` + `LLMProfileConfig.capabilities` 字段

#### 未达（阶段 B–D）

- [ ] 所有 LLM 调用都构造 `LLMRequest`：`grep -rn "_call_thinker\|_call_compact\|_call_slang\|_call_reply_gate\|getattr(.*_call_slang" services/ plugins/ kernel/` 无遗漏
- [ ] `_record_usage` 不再在 `client.py` 多处显式调用（仅在 `_call` 内部一处）
- [ ] `usage.db` 的迁移完成后记录中至少出现 `slang` / `slang_review` / `slang_semantic` / `style` / `memo` / `bilibili_intent` / `chat_private` / `element_detect` 等 task 类型
- [ ] DeepSeek 后台 vs 本地 usage.db 对账缺口 ≤ 5%（当前 80%）
- [ ] admin/system 页能展示 per-task 命中率（拉 `last_cache_hit_pct_by_task`）
- [ ] `LLMRequest.requires_capabilities` ⊄ profile.capabilities 时 `_call` fail-fast `ValueError`，且 admin 面板提前高亮
- [ ] cache diagnostic 视图能在最近一次 break 发生 5 分钟内定位 axis（system / tools / message[N]）
- [ ] DeepSeek 后台命中率改善验收：**spine 全量上线 + Step C.6（thinker mood 移末尾）+ Step C.7（compact sentinel）完成后，跑 3–7 天**，对比 spine 上线前的 38.6% baseline。**不预设阈值**——按 §11.3 的"P1-E 前不要把 55%/60% 当验收指标"，目标线由真实分任务数据给出。
- [ ] D2 cancel-path 测试覆盖：`pytest.raises(TimeoutError)` 模拟 `_call` 被 wait_for 取消，断言 usage.db 不写入半截 row、cache_diagnostic 状态未污染下一次调用
- [ ] D3 迁移清单存档于 `docs/migrations/spine-2026-05-XX.md`：旧 wrapper / 旧 task 名 / 新 task 名 / 调用点四列表
- [ ] `LLMTask` Literal ↔ admin 前端 `ProviderTaskKey` (TS) 一致性测试（Step D.7）

### 12.7 与多层记忆框架的契合

Spine 落地后，多层记忆框架的 Phase A.5 / D 新调用点直接套用：

```python
# Phase A.5 graph reviewer
req = LLMRequest(
    task="graph_review",
    static_blocks=[GRAPH_REVIEW_SYSTEM_PROMPT],
    user_messages=[{"role":"user","content":candidate_payload}],
)

# Phase D reflection consolidator
req = LLMRequest(
    task="reflection_consolidator",
    static_blocks=[REFLECTION_SYSTEM_PROMPT],
    stable_blocks=[group_episode_summary],  # 偶尔变
    dynamic_blocks=[recent_episodes_text],
    user_messages=[{"role":"user","content":consolidation_query}],
)
```

→ Phase A.5 / D 实施时**不需要**重新做 cache 工程。spine 已经强制了正确形态。

### 12.8 不做 spine 的反例代价（量化）

假设 Phase A.5 / D 各引入 2 个新 LLM 调用，未来 6 个月内：

| 不做 spine | 做 spine |
| --- | --- |
| 4 个新调用点各自踩坑（4 × 0.5 天 = 2 天） | 4 个新调用点直接套契约（4 × 1h = 0.5 天） |
| 每次"命中率掉了"调研需 1 天对账 + 定位（按当前节奏季度 1 次 = 4 天/年） | 5 分钟 admin 页查 axis |
| usage.db 与 DeepSeek 后台账目持续不一致（运维负担） | 自动对得上账 |
| reply_gate-style 的隐藏 bug 在新调用点重复出现 | 类型系统拦截 |

**6 个月 ROI**：spine 4 天 vs 不做 spine 至少 6 天 + 持续运维负担 + 可能的隐藏 bug。

### 12.9 「统一 LLM 接口，plugin 自由路由到 deepseek/openai/anthropic」可达性回答

> **触发**：用户在 v3 自审前明确询问"我想要统一的 LLM 接口，对应 deepseek、openai 和 anthropic 以及未来更多可拓展 api，可在已有的 main / thinker / 视觉等可配置 AI 自由调用，也支持插件的自动调用。做完该方案后能做的吗"。

**回答**：能做，且工作量基本被 spine 的阶段 B–D 完整覆盖。

#### 已经具备（无需新建）

- **多 provider 抽象**：[`services/llm/provider.py:90`](../../services/llm/provider.py#L90) 定义了 `LLMProvider` ABC（`request_url` / `build_request` / `parse_sse_stream`），`create_provider(api_format, ...)` 工厂在 [provider.py:142-152](../../services/llm/provider.py#L142) 已支持 anthropic / openai / deepseek 三类；新增 provider 只需加一个文件 + 工厂分支
- **task → profile 路由**：[`config/config.json:70-79`](../../config/config.json#L70) 的 `task_profiles` + `profiles` 已支持每 task 独立配 `api_format / base_url / api_key / model`，运行期通过 [`client.py:1170 set_task_profiles`](../../services/llm/client.py#L1170) 热加载
- **admin UI 切换**：[SystemProviders.vue](../../admin/frontend/src/views/system/components/SystemProviders.vue) 已能在面板里切换任意 task 的 profile，无需改代码、无需重启
- **Capability 字段**：[`kernel/config.py:73`](../../kernel/config.py#L73) `LLMProfileConfig.capabilities`（chat / tools / thinking / vision / json / compact）已存在，admin 已暴露选择器

#### Spine 阶段 B–D 会补齐的（与"自由路由"直接相关）

| 用户要求 | 现状 | 补齐位置 |
|----------|------|----------|
| `main` / `thinker` / `vision` 走不同 AI | ✅ 已可（admin 直接切，task_profiles 热加载） | — |
| 插件可被 admin 单独路由到任意 provider | 部分：`bilibili` / `element_detector` / `chat_private` / `memo` 当前**全部硬编码走 `_call(...)` 即等价于 `main` task**，admin 切不动 | 阶段 C.2/C.3/C.4/C.8（13 处迁移） |
| 配错 capability 立刻报错（避免 reply_gate 那种静默 bug） | ❌ 字段在但 `_call` 没读 | 阶段 B.2（`_enforce_capabilities`） |
| 新增 provider（如未来加 Gemini / Bedrock）不需改业务代码 | ✅ 已可（加一个 provider 文件即可） | — |
| 插件作者能用统一契约写新调用，不用懂前缀稳定 / 缓存语义 | 部分：dataclass 在但没人用 | 阶段 B.1 / C.1–C.9 |

#### 不需要做（避免误投入）

- **不需要再写一层"插件自定义 LLM 接口"**：`LLMRequest` 就是这个接口，spine 落地后插件作者构造 `LLMRequest(task="my_plugin_task", ...)` 即可
- **不需要为每个 plugin 单独建 provider**：provider 是按 api_format 抽象的，按 task 路由由 `task_profiles` 完成
- **不需要额外的 admin 页面**：现有 SystemProviders 已能编辑全部 task → profile 映射，`_LLM_TASKS = all_llm_tasks()` 自动同步，新加 plugin task 只需在 `LLMTask` Literal 中加一行（admin 自动呈现）

#### 一句话

> 阶段 B–D 完成后，**插件作者只需在 `LLMTask` Literal 加一个 task 名 + 在调用方构造 `LLMRequest(task=..., ...)`**，运维即可在 admin 面板把它路由到任意已配置的 provider（含 anthropic / openai / deepseek / 未来 Gemini 等），配错 capability 会 fail-fast，cache 命中率自动按 task 监控。

---

**§ 12 写入时间**：2026-05-18 14:00 首版 → 2026-05-18 21:00 自审第二轮（v3）
**修订摘要**：

- v1 → v2（同日）：加入 plugin task 命名 + capability 校验 + admin 已有基础设施核验
- v2 → v3：盘点真实进度后发现阶段 A 骨架已落地，Step 改为四阶段（A 骨架 ✅ / B `_call` 接入 / C 13 处迁移 / D 清理 + admin），剩余工作量从 4.5–5 天下调为 4 天；新增 §12.9 回答用户对"统一 LLM 接口"可达性的提问；§12.6 验收清单按"已达 / 未达"重排。
**触发**：用户提出"目前 LLM 问题要在多记忆层开始前解决" + "想要统一 LLM 接口让 plugin 也能自由路由到 deepseek/openai/anthropic"。
**前置依赖**：reply_gate 隐藏 bug 修复 ✅ 已完成（2026-05-18 Step 0）。
**后续依赖**：multilayer-memory Phase A0 开工。
**预期下次更新**：阶段 B 落地后，回写"LLMRequest 契约首批接入实测"+"per-task 命中率初值"。
