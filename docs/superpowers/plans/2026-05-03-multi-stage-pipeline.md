# 多阶段流水线架构 — 应用方案

## 现状

单阶段 LLM 调用 + 工具循环（max 5 轮）。一个 prompt 需同时完成：决策是否说话 → 理解上下文 → 决定情绪 → 决定表情包 → 生成文字 → 搜索/记忆。

`services/llm/thinker.py` 已实现 Thinker-Talker 分离（受 MaiBot Planner→Replyer 启发）但从未接线——`thinker_enabled` 默认 false，`think()` 函数在全代码库无 import。

## 阶段一：接线现有 Thinker（低风险，立即可做）

**做法**：`LLMClient.chat()` 中，主 LLM 调用前先调 `think()`：

```
消息到达 → Thinker(256 tokens) → 决策 reply/wait/search + thought
         → 主 LLM 生成文本 + 工具调用
```

**具体改动**（`services/llm/client.py` `chat()` 方法）：
1. 主 LLM 调用前调 `think()`，根据 `ThinkDecision.action`：
   - `wait` → 直接 return None（等同 pass_turn，但不消耗主 LLM token）
   - `search` → 先执行搜索，结果增强 system prompt，再进主回复
   - `reply` → 正常进主回复
2. Thinker 的 `thought` 注入 system prompt：`【你决定说话：{thought}】`

**收益**：
- 决策与执行分离，主 prompt 可省略大量插话规则
- 减少不必要 pass_turn（目前 pass_turn 需主 LLM 完整调用一次才决定沉默）
- wait 决策直接 return，零主 LLM token 消耗

## 阶段二：表情包决策移入 Thinker（解决 sticker-without-text）

**做法**：扩展 `ThinkDecision`：

```python
class ThinkDecision:
    action: str       # reply / wait / search
    thought: str      # 核心想表达的意思
    sticker: bool     # 是否配表情包
    tone: str         # "元气" / "日常" / "安慰" / "认真"
```

主 LLM system prompt 注入 `【sticker: yes】【tone: 元气】`——文字和 send_sticker 可在同一轮生成，不需要工具回调后"续写"。

**收益**：
- 消除 "发了表情包没文字"
- 消除 kaomoji enforcement hack（`client.py:1132-1152`）
- Thinker 小模型（256 tokens）做 sticker 决策比主模型更可靠（prompt 短，注意力集中）

## 阶段三：后处理验证层（代码级兜底）

主 LLM 生成后经 `_clean_reply()` → 检查禁止模式 → 违规则丢弃文字。目前 `_clean_reply()` 已做括号/已发送清理。这一层是代码级防线，不依赖 prompt engineering 概率。

## instruction.md 重排序建议

当前结构：identity(142行) → instruction(431行) → admins → proactive

DeepSeek "Lost in the Middle" 问题：instruction 后半段（记忆系统、表情包、括号禁令）在注意力稀释区。

**调整 instruction.md 内部顺序**（只改 md，不改代码）：

| 位置 | 内容 | 原因 |
|------|------|------|
| 最前 | 底线规则速查（长度控制、禁止括号描述、sticker 后规则、禁用 Markdown） | 每次回复必查，需在注意力高区 |
| 中间 | 场景差分、稳固人格、保密规则 | 中频触发 |
| 最后 | 记忆系统、工具使用细节 | 有工具定义辅助，不纯靠 prompt |

**底线规则速查示例**（加在 instruction.md 最前）：

```
## 底线规则（每次回复前必查）
- 回复长度：能一行说完就一行
- 禁止：「（括号动作描述）」「已发送表情包」「以下是/作为AI」
- send_sticker 之后：直接说正事，不提及"已发送"
- 不用 Markdown 排版
```

核心约束在 prompt 开头和结尾各出现一次，形成注意力锚点。

## 实施顺序

| 阶段 | 改动量 | 收益 | 风险 |
|------|--------|------|------|
| 1. 接线 Thinker | ~50行，client.py + chat() | 减少无效 token 消耗，决策-执行分离 | 低（thinker 已有完整测试） |
| 2. sticker 决策移入 Thinker | ~30行，thinker.py + prompt | 解决 sticker-without-text | 中 |
| 3. instruction.md 重排序 | 只改 md | 缓解 DeepSeek 尾部规则忽略 | 低 |
| 4. 后处理增强 | ~20行，_clean_reply() | 代码级兜底 | 无 |
