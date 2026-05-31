# Issue 17 Part 0 — Dialogue Climate 前置能力调研与立项

> 状态：2026-05-27 调研完成，立项
>
> 来源：用户指示——确保 Dialogue Climate 系统的信号源完整，bot 需正常处理与发出戳一戳、群消息表情回复、表情包含义、语音处理
>
> 约束：与 P0/P1 同——**不允许修改人设文件**。所有方案纯运行时 / 代码层。

---

## 一、能力现状总览

| # | 能力 | 接收（Inbound） | 发送（Outbound） | 语义理解 | Climate 信号价值 |
|---|---|---|---|---|---|
| 1 | 戳一戳 | ✅ 完整 | ✅ 完整 | ✅ 触发回复 | 高（irritation/attention 信号） |
| 2 | 群消息表情回复 | ✅ 完整 | ✅ 完整 | ⚠️ 仅 emoji_code 数字 | 高（用户对 bot 回复的即时反馈） |
| 3 | 表情包含义 | ✅ 完整 | ✅ 完整 | ✅ vision 自动标注 | 中（情绪信号辅助） |
| 4 | 语音消息 | ❌ 完全缺失 | ❌ 完全缺失 | ⚠️ NapCat V4.18.2 已有原生 `ptt2text` | 中（语音情绪信号） |

### 结论

**3/4 能力已完整实现**，且实现质量高（有速率限制、配置开关、humanization profile 分级）。

唯一真正缺失的是**语音消息处理**。但语音对 Dialogue Climate 的信号价值是**中等**而非关键——文字消息已经是主要信号源。

真正需要补强的不是"能不能收发"，而是**语义理解深度**——让已有能力产出更丰富的 Climate 信号。

---

## 二、各能力详细分析

### 2.1 戳一戳 — 已完整，无需改动

**现有实现**：

| 层 | 文件 | 功能 |
|---|---|---|
| 事件解析 | `services/humanization/qq_interactions.py:60-70` | `PokeNotifyEvent` → `QQInteractionSignal(kind="poke")` |
| 速率限制 | 同文件 :99-119 | 同一用户 60s 内 5 次 → 静默 60s |
| 调度触发 | 同文件 :141-180 | `scheduler.notify()` with `TriggerContext(mode="qq_interaction")` |
| 发送能力 | `services/tools/interaction_tools.py` | `poke_user` tool，调用 `send_poke` API |
| 配置 | humanization profile | `poke_inbound_response_enabled` / `poke_outbound_enabled` |

**Climate 信号映射**：
- 被戳 → `IrritationSensor` 的 attention signal（类似 @ 但更轻量）
- 高频被戳 → tension 上升（已有速率限制可复用为信号强度）
- bot 主动戳 → 需要 Climate 的 `openness > 0.6` 才允许（Phase 4 Policy 约束）

**Part 0 工作量**：0 行。已完整。

---

### 2.2 群消息表情回复 — 已完整，语义理解可增强

**现有实现**：

| 层 | 文件 | 功能 |
|---|---|---|
| 事件解析 | `services/humanization/qq_interactions.py:72-91` | 解析 NapCat reaction notice → `QQInteractionSignal(kind="message_reaction", emoji_code=...)` |
| 调度触发 | 同文件 :141-180 | 触发回复（仅 `is_tome=True` 时） |
| 发送能力 | `services/tools/interaction_tools.py` | `react_to_message` tool，调用 `set_msg_emoji_like` API |
| 配置 | humanization profile | `reaction_inbound_response_enabled` / `reaction_outbound_enabled` |

**当前缺口——emoji_code 语义映射**：

收到 reaction 时只有 `emoji_code`（数字 ID，如 "76" = 👍），没有语义解读。对 Climate 系统来说：

- 👍 (76) / ❤️ (66) / 😂 (21) → 正面反馈 → valence +
- 👎 / 😡 / 💩 → 负面反馈 → valence - / tension +
- 😢 / 😰 → 同情/担忧 → 中性

需要一个 `emoji_code → sentiment` 映射表，让 reaction 事件产出有方向的 Climate 信号。

**Part 0 工作量**：~30 行（emoji sentiment 映射表 + signal 分类）

---

### 2.3 表情包含义 — 已完整，系统成熟

**现有实现**：

| 层 | 文件 | 功能 |
|---|---|---|
| QQ 内置表情 | `kernel/qq_face.py` | 180+ face ID → 中文名映射（`«微笑»`、`«大哭»`） |
| 表情包识别 | `services/media/sticker_capture.py:41-55` | `is_sticker_like_segment()` 检测 mface/动画表情 |
| 语义标注 | 同文件 :12-15 | vision LLM 自动生成 `usage_hint`（"适合轻松闲聊时使用"） |
| 表情库 | `plugins/sticker/plugin.py` | 完整的学习/存储/发送/管理系统 |
| prompt 注入 | 同文件 `on_pre_prompt` | 注入表情使用规则和库视图 |

**Climate 信号映射**：
- 用户发表情包 → `MessageSensor` 的 sticker_density 信号（MoodClassifier 已实现）
- 表情包的 `usage_hint` 可以辅助情绪判断（"适合生气时使用" → tension signal）
- bot 发表情包的频率受 Climate 的 `openness` 和 `valence` 调制

**Part 0 工作量**：0 行。已完整。usage_hint 到 Climate signal 的映射属于 Phase 3 Sensor 工作。

---

### 2.4 语音消息 — omubot 未处理，但 NapCat 已有原生 STT

**关键发现**：NapCat V4.18.2（2026-05-13 合并，PR #1837）新增了 `ptt2text` 接口，直接调用 QQ 服务端的语音转文字能力。

**NapCat 协议支持**：

| 能力 | API | 说明 |
|---|---|---|
| 接收语音 | `record` segment in message | silk 格式 |
| **原生语音转文字** | **`ptt2text(message_id)`** | **QQ 服务端 STT，零成本，中文质量极高** |
| 获取音频文件 | `get_record(file, out_format)` | 支持 mp3/wav/ogg/flac 等 |
| 发送语音 | `send_group_msg` with `record` segment | 自动转 silk |
| AI 语音 TTS | `send_group_ai_record(character, group_id, text)` | QQ 内置 AI 声线 |

**最小化实现方案（推荐）**：

```
[用户发语音] → record segment
    → bot.call_api("ptt2text", message_id=msg_id)
    → 返回 {"text": "转写文字"}
    → 注入 timeline 为 "«语音»{text}"
    → LLM 正常处理
```

**优势**：
- **零额外成本**——QQ 服务端免费提供
- **无需外部 STT 服务**——不需要 Whisper API key、不需要本地模型
- **无需下载音频文件**——不调用 `get_record`，不做格式转换
- **中文识别质量极高**——QQ 自己的语音转文字，针对中文优化
- **实现极简**——约 10-15 行代码

**修改位点**：`kernel/router.py` 的 `_render_message()` 函数

```python
# kernel/router.py _render_message() 内新增分支
elif seg.type == "record":
    try:
        result = await bot.call_api("ptt2text", message_id=event.message_id)
        text = result.get("text", "").strip()
        parts.append(f"«语音»{text}" if text else "«语音消息»")
    except Exception:
        parts.append("«语音消息»")
```

**前置条件**：NapCat 版本 ≥ V4.18.2。需确认当前部署版本。

**降级策略**：`ptt2text` 调用失败时（版本不支持 / 网络问题 / QQ 服务端限流），fallback 为占位符 `«语音消息»`。

**配置**：

```json
{
  "voice": {
    "stt_enabled": true,
    "fallback_placeholder": "«语音消息»"
  }
}
```

---

## 三、Part 0 立项范围

### 3.1 必做项（Climate 前置）

| # | 任务 | 行数 | 依赖 | 理由 |
|---|---|---|---|---|
| P0-1 | Emoji reaction sentiment 映射表 | ~30 | 无 | reaction 事件需要有方向的 Climate 信号 |
| P0-2 | 语音消息 `ptt2text` 接入 | ~15 | NapCat ≥ V4.18.2 | 消除"语音是黑洞"，零成本 STT |

### 3.2 推荐项（显著增强 Climate 信号质量）

| # | 任务 | 行数 | 依赖 | 理由 |
|---|---|---|---|---|
| P0-3 | Reaction 作为 Climate feedback 信号 | ~40 | P0-1 | 用户对 bot 消息的 reaction 直接反馈到 valence |
| P0-4 | Poke 频率 → IrritationSensor 桥接 | ~20 | Phase 0 接线 | 戳一戳速率数据直接喂 Climate |

### 3.3 可选项（锦上添花）

| # | 任务 | 行数 | 依赖 | 理由 |
|---|---|---|---|---|
| P0-5 | QQ AI 语音发送能力 | ~60 | P0-2 | bot 可以发语音回复 |

---

## 四、Emoji Reaction Sentiment 映射设计

### 4.1 QQ Emoji ID → 情感极性

基于 QQ 表情 ID 体系（与 `kernel/qq_face.py` 同源）：

```python
# services/dialogue_climate/emoji_sentiment.py

from typing import Literal

Polarity = Literal["positive", "negative", "neutral", "ambiguous"]

# QQ emoji_id → (polarity, intensity 0.0-1.0, label)
EMOJI_SENTIMENT: dict[str, tuple[Polarity, float, str]] = {
    # 强正面
    "76":  ("positive", 0.9, "赞"),        # 👍
    "66":  ("positive", 0.8, "爱心"),      # ❤️
    "21":  ("positive", 0.7, "大笑"),      # 😂
    "63":  ("positive", 0.6, "玫瑰"),      # 🌹
    "124": ("positive", 0.8, "OK"),        # 👌
    "78":  ("positive", 0.5, "握手"),      # 🤝

    # 弱正面
    "4":   ("positive", 0.4, "得意"),
    "12":  ("positive", 0.3, "调皮"),
    "14":  ("positive", 0.2, "微笑"),

    # 强负面
    "86":  ("negative", 0.9, "翻白眼"),
    "111": ("negative", 0.8, "嫌弃"),
    "26":  ("negative", 0.7, "怒"),
    "174": ("negative", 0.6, "汗"),

    # 弱负面
    "3":   ("negative", 0.3, "流泪"),
    "9":   ("negative", 0.4, "大哭"),
    "96":  ("negative", 0.5, "冷汗"),

    # 中性/模糊
    "0":   ("neutral", 0.0, "惊讶"),
    "32":  ("ambiguous", 0.2, "疑问"),
    "277": ("ambiguous", 0.3, "汪汪"),
}

def classify_reaction_sentiment(emoji_code: str) -> tuple[Polarity, float]:
    """Return (polarity, intensity) for a QQ emoji reaction."""
    entry = EMOJI_SENTIMENT.get(emoji_code)
    if entry is not None:
        return entry[0], entry[1]
    # 未知 emoji 默认为弱正面（用户主动 react 本身是参与信号）
    return "positive", 0.2
```

### 4.2 Climate 信号转换

```python
def reaction_to_climate_signal(signal: QQInteractionSignal) -> ClimateSignal | None:
    if signal.kind != "message_reaction" or not signal.is_tome:
        return None
    polarity, intensity = classify_reaction_sentiment(signal.emoji_code)
    if polarity == "positive":
        return ClimateSignal(dimension="valence", delta=+intensity * 0.15)
    elif polarity == "negative":
        return ClimateSignal(dimension="valence", delta=-intensity * 0.2)
        # 负面 reaction 同时增加 tension
        # return ClimateSignal(dimension="tension", delta=+intensity * 0.1)
    return None
```

---

## 五、语音消息处理设计

**方案已确定**：使用 NapCat 原生 `ptt2text` 接口（QQ 服务端 STT）。

### 5.1 接收管线

**修改位点**：`kernel/router.py` 的 `_render_message()` 函数

当前 `_render_message()` 处理的 segment 类型：`text`, `at`, `face`, `image`, `forward`。需要增加 `record` 分支。

```python
# kernel/router.py _render_message() 内新增分支
elif seg.type == "record":
    try:
        result = await bot.call_api("ptt2text", message_id=event.message_id)
        text = result.get("text", "").strip()
        parts.append(f"«语音»{text}" if text else "«语音消息»")
    except Exception:
        parts.append("«语音消息»")
```

### 5.2 配置

```json
{
  "voice": {
    "stt_enabled": true,
    "fallback_placeholder": "«语音消息»"
  }
}
```

### 5.3 前置条件

- NapCat 版本 ≥ V4.18.2（2026-05-13 发布，PR #1837 合并）
- `ptt2text` API 签名：`POST /ptt2text { "message_id": number }` → `{ "text": string }`
- 降级策略：API 调用失败时 fallback 为占位符，不阻塞消息处理

---

## 六、与 Dialogue Climate 的信号接口

Part 0 完成后，Climate 系统获得的新信号源：

| 信号源 | 维度 | 触发条件 | 信号强度 |
|---|---|---|---|
| Reaction 正面 | valence + | 用户对 bot 消息点赞/爱心 | 0.03-0.14 |
| Reaction 负面 | valence - / tension + | 用户对 bot 消息翻白眼/怒 | 0.04-0.18 |
| 语音消息文本 | 经 MessageSensor | `ptt2text` 转写文本进入正常分析管线 | 同文字消息 |
| 语音消息存在 | openness + | 用户愿意发语音 = 亲密度信号 | 0.02 |
| Poke 频率（已有） | tension + | 高频戳一戳 | 由速率限制阈值决定 |

---

## 七、执行排期建议

```
Part 0 执行顺序（单批，与 Issue 17 第 1 层 cooldown 同 PR）：

  P0-1  Emoji sentiment 映射表              ~30 行
  P0-2  语音消息 ptt2text 接入              ~15 行
  P0-3  Reaction → Climate feedback 桥接    ~40 行
  P0-4  Poke → IrritationSensor 桥接        ~20 行
  ─────────────────────────────────────────────────
  合计                                      ~105 行

后续（Part 8 Phase 3 同批）：
  P0-5  QQ AI 语音发送能力                  ~60 行
```

---

## 八、决策模板

```text
Part 0 范围：
[ ] P0-1 + P0-2（~45 行，最小前置）
[ ] P0-1~P0-4（~105 行，完整信号源，推荐）
[ ] 全部 P0-1~P0-5（~165 行，含语音发送）
[ ] 暂不做，直接进 Issue 17 第 1 层

语音 STT 方案：
[x] NapCat 原生 ptt2text（零成本，~15 行，已确定）

执行批次：
[ ] 与 Issue 17 第 1 层同 PR
[ ] 独立 PR
[ ] 其他：___
```
