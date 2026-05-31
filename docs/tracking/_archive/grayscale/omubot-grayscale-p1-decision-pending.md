# 灰度 P1 问题——方案待定夺

> 状态：2026-05-27 从 [解决方案候选文档](omubot-grayscale-issues-2026-05-26-solutions.md) 摘出 P1 子集，供用户逐条拍板。
>
> P0 五件（F1/F3/F7/F10/F13）已进入派单执行；本文仅列 **P1 八件**。
>
> 定夺粒度：每条选 1 套方案 / 暂不做 / 自由备注混合方案。
>
> 约束：**不允许修改人设文件**（source.md / instruction.md）——换一个不知道规则的人写人设还是会触发问题，治标不治本。所有方案必须是纯运行时 / 代码层解决。

---

## 总览

| # | 标题 | 紧迫性 | 性质 | 推荐方案 |
|---|---|---|---|---|
| 4 | persona declaration drift | 中（破沉浸） | 纯运行时 4 层护栏 | 4A（Layer 1+2+3+5，anchor 从 freeze 自动派生） |
| 5 | OOV slang reflex | 中（黑话失语） | 投机执行 + 专用 API + 4 级降级 | 5A（PASTE + TianAPI cascade） |
| 6 | sticker 频率 / 位置 | 中（设计行为不达） | 激活 provider + segment-aware placement | 6A |
| 8 | schedule oversharing | 中（破沉浸） | 纯出口 detector + 累积计数 | 8A（detector + CAMP） |
| 11 | addressee binding 缺失 | 中（破真实感 + reply injection） | 架构层缺 nickname binding | 11A |
| 12 | 上游工具命令未屏蔽 | 中（prompt context 污染） | 架构层缺 input filter | 12A |
| 14 | @ 真 at 不出 CQ 段 | 中（破真实感） | 架构层缺 mention post-processor | 14A |
| 2 | 短消息 / 标点-only | 低（漏少量） | 架构层缺 layer | 2A |

---

## Issue 4 — persona declaration drift（破沉浸）

### 背景

bot 在长对话中会漂移出"我是凤笑梦""作为 WxS 成员"等 meta 自述，破坏沉浸感。根因：system prompt attention 随对话深度衰减 + 无运行时纠偏机制。

Anthropic API 不暴露 logit_bias / activation capping / grammar mask——根治路径只能落在流水线护栏 + 出口检查组合。

### 方案 4A — 4 层纯运行时护栏（推荐）

所有 anchor / baseline 从 persona v2 freeze artifacts 自动派生，不依赖 source.md 写法。

```
[Build]   Layer 1：compiler validator（扫 freeze output）  → bundle.ok=False / fallback
[Request] Layer 2：anchor reinjection at boundaries       → ContextEcho A-anchor（~80 token）
[Infer]   Layer 3：drift detector + bounded retry         → EchoMode EWMA + Nautilus embedding
[Exit]    Layer 5：runtime stripper / sentinel            → A 簇 belt-and-suspenders
```

- **Layer 1**（编译期）：compiler 扫 freeze output（`freeze/core.identity` / `freeze/voice_exemplars`），命中 declaration 模式写 ImportIssue(level=error)；bundle.ok=False 自动回落。**扫的是 freeze output 不是 source**——任何人重写 source 只要 freeze 通过 compiler，anchor 自动更新
- **Layer 2**（请求期）：检测 semantic boundary（topic shift / tool-result-return / @-mention 切换 / session boundary），命中时在 messages 末端追加 user-role A-anchor（从 freeze artifacts 自动提取 1 句 identity reminder + voice exemplar demo，~80 token）。学术验证：ContextEcho（arxiv 2605.24279）证明单次注入即恢复 drift，对 23 个 frontier model 有效；mcp-llm-constraints 证明每 5-7 轮 variable ratio reinforcement 最优
- **Layer 3**（推理后）：post-LLM drift detector，两路信号融合：
  - EchoMode EWMA（λ=0.3）：baseline persona signature 从 freeze 提取，每次输出计算 driftScore
  - Nautilus Compass embedding（可选增强）：BGE-m3 cosine similarity，ROC AUC 0.83
  - score > θ_repair(0.6) → REPAIR DIRECTIVE 重生成（hard cap 1 次）；score > θ_block(0.85) → drop
- **Layer 5**（出口）：`strip_persona_declaration` 规则（regex 匹配"我是凤笑梦""作为 WxS 成员"等 meta 自述），与 A 簇 sentinel_registry 共骨架

**成本**：~800-950 行（Layer 1+2+3+5）

**引证**：EchoMode FSM（github.com/Seanhong0818/Echo-Mode）/ Nautilus Compass（arxiv 2605.09863，MIT）/ ContextEcho A-anchor（arxiv 2605.24279）/ mcp-llm-constraints（github.com/11PJ11/mcp-llm-constraints）/ agent-guard-rails（github.com/MukundaKatta/agent-guard-rails）

**风险**：Layer 3 EWMA 阈值需灰度调参 7-14 天；Layer 2 user-role 注入会让 prompt cache 命中率下降；Layer 3 retry 最坏 2× LLM call

### 方案 4B — 仅 Layer 5 出口 stripper（stop-gap）

仅做 A 簇出口侧 strip，~100-150 行。治症状不治本——不满足"换一个人写也不会犯错"的结构诉求。可作为 4A 落地前临时止血。

---

## Issue 5 — OOV slang reflex（黑话失语）

### 背景

用户发"op""下位替代""推了"等二次元/游戏黑话时，gate 判 confidence=0.10 直接 skip，bot 失语。slang.db 有部分条目但 gate 不查。面对未知词语的首次应对是群聊 bot 极为重要的指标——不能因为超时风险就放弃首次应对能力。

### 方案 5A — PASTE 投机执行 + 专用黑话 API + 4 级降级 cascade（推荐）

核心改进：将原 5A 的 ③ WebSearchTool（3-15s 延迟、命中率低）替换为专用黑话 API + 投机并行 + 4 级降级，确保 bot 永不失语。

**完整 cascade 架构：**

```text
[并行启动]
├── gate LLM call（输出 specification_confidence + unknown_terms）
└── [投机] 对 unknown_terms 发起 TianAPI 查询（500ms timeout）

[gate 返回后]
├── confidence ≥ 0.8 → 正常回复（丢弃投机结果）
├── confidence < 0.5 且 unknown_terms 非空：
│   ├── 投机结果已就绪 → 注入 system block，re-judge（1 次）
│   ├── 投机超时 → 降级：将 unknown_terms + 群聊上下文注入 re-judge prompt
│   │   让 LLM 从上下文自行推断含义（Minnow in-context learning）
│   └── re-judge 仍 < 0.3 → 降级：生成"主动询问"回复
│       （"op 是什么意思？我没跟上"——符合人设的自然反应）
└── confidence 0.5-0.8 → 正常回复（不触发 lookup）
```

**三个子问题的解法：**

| 原 5A 设计 | 问题 | 修订 |
|---|---|---|
| ③ WebSearchTool（通用搜索） | 3-15s 延迟，中文黑话命中率低 | 替换为专用黑话 API（TianAPI，<200ms） |
| 串行阻塞（gate → lookup → re-judge） | 查询期间 bot 完全静默 | PASTE 投机执行：gate 判定与 API 查询并行 |
| 无降级路径 | 超时/miss 即失语 | 4 级降级：SlangStore → API → LLM 上下文推断 → 主动询问 |
| hard cap 2 次 web search | 最坏 2×timeout | per-call 500ms timeout + circuit breaker |

**专用 API 选型：**

- **TianAPI**（tianapi.com/apiview/33）：REST `GET /hotword/index?word={term}`，<200ms SLA，网络热词/流行语/梗释义，免费档 100 次/日
- **羽山数据**（ENT004）：备选 API，与 TianAPI 互为 fallback
- 架构参考：ZeroBot-Plugin/jikipedia（Go，完整 QQ bot 黑话查询集成）

**投机执行理论基础（PASTE，arxiv 2603.18897，Microsoft Research）：**

- 在 LLM gate 判定期间投机性预执行 tool call；命中则零延迟复用，miss 则丢弃（只读查询无副作用）
- 48.5% 延迟降低，1.8x 吞吐提升
- 黑话 API 查询是只读、幂等、无副作用——完美适合投机执行

**LLM in-context fallback（Minnow，EMNLP 2025，arxiv 2502.14791）：**

- 当 API miss/timeout 时，将 unknown term + 群聊上下文（前后 3-5 条）注入 re-judge prompt
- LLM 从上下文推断含义，不需要额外 LLM call（复用 re-judge）
- Chouxiang Language Benchmark（arxiv 2604.15841）证明：给出候选含义让 LLM 做 meaning selection 比 zero-shot 推断更可靠

**成本**：~300-400 行（含 TianAPI client + speculative executor + 4 级降级 + 测试）

**关键优势**：
1. 首次应对有保障——即使 API 全挂，LLM 上下文推断 + 主动询问确保 bot 永不失语
2. 延迟可控——投机执行 + 500ms timeout，最坏情况也只多等 500ms
3. 命中率高——专用黑话 API 比通用搜索精准得多
4. 符合人设——"不懂就问"是真人的自然反应，比沉默更真实

**风险**：gate prompt 改写后所有现有 gate 行为需回归；TianAPI 免费档 100 次/日可能不够（需评估日均 OOV 触发量）

### 方案 5B — slang.db 手动加条目

0 代码改动，被动补充。不解决"gate 也不懂"的根因。不推荐独立做。

---

## Issue 6 — sticker 频率 / 位置策略（设计行为不达）

### 背景

StickerDecisionProvider 203 行已就位但未接线。frequency="frequently" 在 prompt 里但 v4-flash 命中率 < 20%。此外，当前表情包被强制前置在所有文本段之前发出，违反自然语序。

### 方案 6A — 激活 provider + frequency 阈值 + segment-aware placement（推荐）

**频率激活：**

- 在 `client.py:2625` kaomoji_enforce 同位置调用 `sticker_provider.decide()`
- frequency 升级为 `send_probability` 阈值映射：`rarely/normal/frequently → 0.85/0.55/0.30`
- 新建 `(group_id, user_id) → last_sent_at` 持久化支撑 deterministic cooldown

**位置策略（学术共识 + 协议验证）：**

| 维度 | 当前行为 | 学术/工程共识 | 建议改法 |
|---|---|---|---|
| 位置 | 强制前置（sticker 总在第一段） | sticker 跟在触发它的句段之后（SR 场景）或独立成段（RR 场景） | segment split 后按 sentiment/intent 决定插入点 |
| 触发时机 | 在 send_queue 组装前一次性决策 | 逐段情感分析 / intent 分类后决定 | 在 segment split 循环内逐段判定 |
| 协议可行性 | — | OneBot v11 原生支持 `[text, face, text]` 混排 | 无协议阻碍 |
| 多条消息退化 | — | Chainlit/Discord 方案：拆多条按序发 | 如单消息混排渲染异常，退化为多条顺序发送 |

**实现路径：**

1. **segment splitter**：复用现有 `_split_segments()` 或新建，将 LLM reply 拆为句段列表
2. **per-segment sticker decision**：对每个句段调用 `sticker_provider.decide()`，返回 `(should_send, position: "after" | "standalone")`
3. **interleave assembly**：按原始句段顺序 + sticker 插入点组装最终 `Message` 对象
4. **send_queue ordering**：如果走多条消息路径，保证 send_queue 按组装顺序 FIFO 发出

**引证**：UbiComp 2025（arxiv 2512.22032，句段分割 + 情感插入）/ Int-RA（arxiv 2403.05427，SR/RR 两种场景）/ IGSR（AAAI 2025，intent-guided 位置）/ EIGML（arxiv 2511.17587，emotion+intention 联合建模）/ OneBot v11 MessageSegment 混排确认

**成本**：~180-270 行（频率激活 100-150 + 位置策略 80-120）

**风险**：激活后"冷场段不发贴纸"可能让用户觉得"变冷淡了"，需灰度观测一周

### 方案 6B — frequency 默认改 normal

1 行改动。治症状不治根因。不推荐。

---

## Issue 8 — schedule oversharing（破沉浸）

### 背景

bot 在用户未问日程时主动报"今天排练""中午吃饭"等具体时段信息，破坏沉浸感。

### 方案 8A — 纯出口 detector + CAMP 累积计数（推荐）

不动任何人设文件。纯运行时两层防治：

**第一层：出口 detector**

- reply 出口加 `unsolicited_schedule_detector`：`user_msg` 不含时段询问（`几点|什么时候|日程|安排|忙不忙`）+ `bot_reply` 含时段词（`\d{1,2}[：:]\d{2}|上午|下午|晚上|排练|吃饭|休息`）→ dampen
- 复用 A 簇 sentinel_registry 骨架

**第二层：CAMP 累积计数**

- 同一 session 内主动报日程 ≥ N 次后提高 dampen 灵敏度
- 单次提"今天排练"可能自然，但每次都主动报日程就破沉浸
- 基于 Cumulative Privacy Exposure（CPE）score 跨 turn 累积

**引证**：AI Delegates（arxiv 2409.17642，disclosure strategy layer）/ IBM Contextual Privacy Toolkit（contextual appropriateness 判定）/ CAMP（arxiv 2604.16521，累积隐私保护）/ AegisGate（response pipeline sanitizer）/ agent-guard-rails（composable output rules）

**成本**：~80-120 行（detector + 累积计数，复用 F1 guardrail 骨架）

**风险**：regex 误判边界——用户主动问日程时 bot 应该正常回答；mitigation = 检测 user_msg 含时段询问词时 bypass

### 方案 8B — 仅 detector 不加累积计数

~50-80 行。无"偶尔可以、频繁不行"的灰度能力。

---

## Issue 11 — addressee binding 缺失（破真实感 + reply injection）

### 背景

群聊中 bot 不知道自己在回复谁，用泛指"你"或错误指向。`«回复 X(QQ): Y»` 字面化进 prompt 有 injection 隐患。

### 方案 11A — NameVariationRegistry + addressee_hint 注入 + quote provenance 三件套（推荐）

- `services/persona/name_registry.py`：启动期收集 bot alias + 群成员昵称 cache
- `AddresseeDetector.detect()` 返回 `AddresseeResult(target_uid, nickname, qq, confidence, provenance)`
- `services/llm/addressee_hint.py`：输出 `[当前你在回复：{nickname}（QQ: {qq}）]` 注入 system block 1 末尾
- timeline 渲染加结构化 `[QUOTED_METADATA ...]` marker

**成本**：300-400 行

**优势**：deterministic 解决（NapCat 已给 user_id + nickname）；治本 + 治输入双层；与 F3/F12/F14 共骨架

**风险**：fuzzy alias 需后续维护；quote provenance marker 需确认 humanization downstream 兼容

### 方案 11B — 仅 prompt 加 directive

1 行。v4-flash 命中率 < 20%，本仓第 7 次 prompt-only 失败模式。不推荐独立。

### 方案 11C — 仅 timeline rendering 加 sender→addressee 边

50-80 行。部分实现，优于 11B 但弱于 11A。适合渐进路径。

---

## Issue 12 — 上游工具命令未屏蔽（prompt context 污染）

### 背景

`#napcat info` / 一只魔精的查询回执等上游 bot 命令进入 timeline，污染 prompt context + 消耗 token。

### 方案 12A — upstream_command_filter 配置段 + UpstreamCommandFilter 服务（推荐）

- 新建 `services/upstream_filter.py`：`should_drop(event) -> (bool, reason)`
- router group_listener 入口插一道 filter
- config.json 加 `upstream_command_filter: { enabled: false, command_patterns, known_other_bots, drop_silently, log_drops }`
- group-level override 支持每群独立配置
- admin SPA 编辑面板

**成本**：200-300 行

**优势**：默认 OFF 满足"默认关闭"诉求；与 F7 共 known_other_bots 数据结构同步落地最经济；可观测（drop log）

**风险**：command_patterns 有误伤风险（用户讨论性引用 `#napcat`）；mitigation = 仅匹配行首

### 方案 12B — router 入口 hardcode 正则

3-5 行。违背用户"可配置/默认关闭"诉求；无可观测性。不推荐。

---

## Issue 14 — @ 真 at 不出 CQ 段（mention wiring 缺失）

### 背景

LLM 输出 `@昵称` 字面量但未转为 `[CQ:at,qq=<id>]`，群里看不到真 at 效果。

### 方案 14A — mention post-processor + 共建 F11 nickname registry（推荐）

- 新建 `services/llm/mention_post_processor.py`：reply 进 send_queue 前最后一道 layer
- 扫描 `@昵称` 字面量，按 recent_speakers 的 member_card/nickname/qq 三段优先级命中，改写为 `[CQ:at,qq=<id>]`
- 与 F11 共建 nickname registry

**成本**：120-180 行

**优势**：不要求 LLM 改变行为；post-processor 兜底最稳；与 F11 共骨架

**风险**：同名歧义场景命中歧义时不改写保留字面量

### 方案 14B — LLM tool registration `at_user(qq: int)`

80-120 行。架构最干净但 LLM tool-use 可靠性弱于 post-processor。次选，作为未来升级路径。

### 方案 14C — Hybrid：prompt 提示 + post-processor fallback

14A + prompt 改写。双保险但测试矩阵爆炸。30 天观察后视情况升级。

---

## Issue 2 — 短消息 / 标点-only / 低信号文本（紧迫性低）

### 背景

纯标点、单 emoji、单字等低信号消息仍进 gate 消耗 LLM token，偶尔触发无意义回复。

### 方案 2A — 独立 text_preflight normalizer 模块（推荐）

- 新建 `services/text_preflight.py`：`Preflight(text) -> PreflightResult{ density, oov_terms, punctuation_only, recommended_action }`
- 在 `should_call_semantic_gate` 前插入 preflight；命中低信号模式直接短路
- 复用 Rasa NLU component 三段式

**成本**：~150-200 行

**优势**：B 簇骨架入门件；与 F3/F5/F7 共 router 入口

**风险**：低信号判定阈值需 gray-box 调；可能误压少量"用户想撩 bot 的颜文字"

### 方案 2B — gate prompt 里加"识别低信号"

浪费 gate 一次 LLM call。不推荐。

### 方案 2C — router 入口直接 regex 过滤

3 行代码。无可观测性、不可调。短期可行但劣于 2A。

---

## 决策模板

```text
Issue 4 / persona drift：    [ ] 4A 推荐（4 层纯运行时）  [ ] 4B 仅 Layer 5  [ ] 暂不做
Issue 5 / OOV slang：        [ ] 5A 推荐（PASTE + TianAPI）  [ ] 5B  [ ] 暂不做
Issue 6 / sticker policy：   [ ] 6A 推荐（激活 + 位置）  [ ] 6B  [ ] 暂不做
Issue 8 / schedule overshare：[ ] 8A 推荐（detector + CAMP）  [ ] 8B  [ ] 暂不做
Issue 11 / addressee binding：[ ] 11A 推荐  [ ] 11B  [ ] 11C  [ ] 暂不做
Issue 12 / upstream filter： [ ] 12A 推荐  [ ] 12B  [ ] 暂不做
Issue 14 / mention wiring：  [ ] 14A 推荐  [ ] 14B  [ ] 14C  [ ] 暂不做
Issue 2 / preflight：        [ ] 2A 推荐  [ ] 2C  [ ] 暂不做

执行批次偏好（可选）：
[ ] 独立 P1 派单，按簇分批
[ ] 先做紧迫性中的（F4/F6/F8/F11/F12/F14），F2/F5 滞后
[ ] 全部暂不做，观察 P0 落地效果再定
[ ] 其他：___
```
