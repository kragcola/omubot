# 表情包配图 · 心情二轴改造方案

> 状态：**已实施（2026-06-10）** · 代码+测试落地，全量 pytest 2588 passed；待下次部署 rebuild 随车
> 关联：[[project_mood_two_axis]]、[[project_sticker_mood_two_axis]]、社会工程调研（见本仓 maintenance-log 2026-06-10 条目上游讨论）
> 决策人诉求：方案 A（thinker/语境当总闸）+ 方案 B（频率按心情）+ 保留 web 可配频率；纠正「难过/累 ≠ 不发，累应趋近话少」
> 落地差异：§4.4「_STICKER_FREQUENCY_PROMPTS / 每条必发」措辞在现仓已不存在，跳过；其余 §1~§6 全部实施，并按 §6.7 D1 扫描修了 `_should_force_kaomoji_sticker_round` 同源死代码。

---

## 0. 一句话目标

把「配不配图」从**一个被心情硬拦的开关**，改成**两根正交轴的调制**：
- **valence（开心↔难过）→ 决定发哪一类图**（难过照发，偏共情/低落类），不降频率
- **energy（满电↔枯竭）→ 决定发图概率乘子**（累→话少→配图按比例减，但不归零）
- web 可配 `sticker_mode` 当**基线频率**，心情在基线上**动态调制**，thinker/语境效价一致性当**最终闸**

---

## 1. 必须先纠正的三个现状 bug（调研已证）

| # | 现状 | 事实 | 证据 |
|---|---|---|---|
| B1 | `decision_provider._COLD_MOODS={"cold","tired"}`、`_PLAYFUL_MOODS={"playful","high"}` | **英文标签集合，与生产传入的中文 label 永不相等** → 整段 mood 逻辑是死代码 | `mood_engine.evaluate()` 返回 `MoodProfile.label ∈ {困倦,低落,兴奋,疲惫,烦躁,放松,专注,期待,匆忙}`；`_humanization_state_label` 取 label 后 `.lower()` 仍是中文 |
| B2 | `_blocked_by_mood` 把 cold/tired 硬拦到 0 | 即使词表对上，「难过/累一刀切禁发」也违反二轴：难过照发（换效价）、累是降量非归零 | 抑郁用户表情不减只转负向效价（😔😢💔 vs 😂😊）；「低落」人设本身写「不拒绝温暖对话，需要温暖」 |
| B3 | `StickerDecisionContext` 未传 `affection_stage`（用默认 "acquaint"） | 关系亲密度对配图的调节（负向语境亲密豁免）从未生效 | client.py:1589 构造 context 时无 affection_stage 实参 |

附带事实（非 bug 但影响落地）：
- **F1**：`StickerPlacementConfig.enabled` 默认 `False`，config.toml 未开 → 兜底配图路 `_send_post_reply_sticker_if_needed` 生产里**整条未启用**。本方案改的是这条路；上线前需在 config 打开 `[sticker_placement] enabled=true`。
- **F2**：`mood_classifier.py`（cold/tired/playful/high）**全仓零实例化**，不是配图的心情源。配图的真实心情源是 `MoodProfile`（schedule，bot 自身）。本方案据此**不碰 mood_classifier**，直接吃 MoodProfile 的 energy/valence 数值。

---

## 2. 契约变更：mood 从「字符串标签」改为「数值二轴」

### 2.1 现状契约（要废弃的）
```python
# client.py:1594 — 把 MoodProfile 压成一个中文字符串塞进 context
mood_label = self._humanization_state_label(mood_profile, keys=("label","mood","name"))
StickerDecisionContext(mood_label=mood_label, ...)
```
问题：丢掉了 energy/valence 数值，只剩一个对不上词表的字符串。

### 2.2 新契约
`StickerDecisionContext` 增加两个数值字段（保留 `mood_label` 仅作 observability/日志，不再参与判定）：
```python
@dataclass(frozen=True, slots=True)
class StickerDecisionContext:
    register_label: str = "neutral"
    mood_label: str = "neutral"          # 保留，仅日志
    mood_energy: float = 0.6             # NEW [0,1]，默认取 _DEFAULT_MOOD.energy
    mood_valence: float = 0.0            # NEW [-1,1]，0=中性
    affection_stage: str = "acquaint"    # B3：client 改为真实传入
    cooldown_active: bool = False
    cooldown_ms: int = _DEFAULT_COOLDOWN_MS
    base_frequency: str = "normal"       # NEW：web 可配基线（rarely/normal/frequently）
    frequent_candidates: ...
    kaomoji_candidates: ...
    thinker_candidates: ...
    tool_call_candidates: ...
```

client.py 构造处改为直接读 MoodProfile 数值：
```python
mood_profile = self._current_humanization_mood(group_id=group_id, session_id=session_id)
mood_energy  = float(getattr(mood_profile, "energy", 0.6) or 0.6)
mood_valence = float(getattr(mood_profile, "valence", 0.0) or 0.0)
affection_stage = self._current_affection_stage(group_id=group_id, user_id=user_id)  # B3
base_frequency = self._resolve_sticker_base_frequency(group_id)  # web 可配，见 §4
```

---

## 3. decision_provider 改造：从硬拦到乘子

### 3.1 删除 `_blocked_by_mood`，改为 `_mood_probability_multiplier`
```python
def _mood_energy_multiplier(energy: float) -> float:
    """energy 低→话少→配图按比例减，但不归零（累≠不发）。
    energy 1.0→×1.0；0.5→×0.75；0.0→×0.5。线性，地板 0.5。"""
    return 0.5 + 0.5 * max(0.0, min(1.0, energy))
```
- 累（energy 低）→ 概率乘 0.5~1.0，**永不为 0**。
- 不再用 `valence` 拦截（valence 只管选类，见 §3.3）。

### 3.2 `_send_probability` 改造
```python
def _send_probability(context, source, has_pool) -> float:
    if not has_pool:
        return 0.0
    base = {"tool_call":0.85, "kaomoji":0.65, "frequent":0.7, "thinker":0.45, "none":0.0}[source]
    # web 基线频率：在 source 基础上整体抬/压
    base *= _BASE_FREQUENCY_MULT[context.base_frequency]   # rarely 0.5 / normal 1.0 / frequently 1.4
    # energy 轴：累→降量（乘子，非归零）
    base *= _mood_energy_multiplier(context.mood_energy)
    # affection：亲密放大、陌生收敛（B3 生效）
    if context.affection_stage == "close":    base = min(0.95, base + 0.1)
    elif context.affection_stage == "stranger": base = max(0.0, base - 0.15)
    elif context.affection_stage == "withdraw": base = min(base, 0.05)  # 唯一接近「禁」的态，但来自关系非心情
    # 颜文字非 playful 语域时压制（保留原逻辑，但 playful 改判数值化，见 §3.4）
    if source == "kaomoji" and context.register_label != "playful" and context.mood_energy < 0.7:
        base = min(base, 0.2)
    return max(0.0, min(1.0, base))
```
注意：`withdraw`（主动疏远）是唯一接近「禁发」的状态，但它来自 **affection（关系）** 而非 mood——符合调研「负向语境疏远对象配图不得体」。难过（valence 低）本身**不**触发任何禁/压。

### 3.3 valence → 选类（接已上线的语义检索）
valence 不进 `_send_probability`，而是改 client.py 的 `_select_post_reply_sticker`（2026-06-10 已落地的语义检索入口）的 **query 偏置**：
```python
# _select_post_reply_sticker 内，构造 BM25 query 时按 valence 加情绪意图词
if mood_valence <= -0.3:
    query = f"{query} 安慰 共情 陪伴 难过"     # 难过→偏共情/低落类
elif mood_valence >= 0.5:
    query = f"{query} 开心 兴奋 欢呼"          # 开心→偏正向
# 中性 valence 不加偏置，纯按语境检索
```
- 难过照发，但发的是共情类（依据：抑郁用户转负向效价；「低落」人设「不拒绝温暖」）。
- 文-图效价一致（依据：congruence 研究）。

### 3.4 playful 判定数值化（顺手修 B1 同源问题）
原 `_PLAYFUL_MOODS={"playful","high"}` 同样对不上中文。改为数值：
```python
def _is_playful(context) -> bool:
    return context.mood_energy >= 0.7 and context.mood_valence >= 0.4
```
对应 `_classify` 里 energy>0.6 & valence>0.6 的「兴奋/期待」区。

### 3.5 rerank_strategy 复活（可选，低优先）
原 `rerank_strategy`（intent/emotion/persona）是死字段。§3.3 已用 valence 偏置 query 实现了「emotion/intent」效果，rerank_strategy 可暂留不动或后续删除，不在本方案范围。

---

## 4. web 可配基线频率（保留诉求）

### 4.1 数据源：复用既有 `sticker_mode`
`GroupStickerMode = inherit|off|rarely|normal|frequently` 已是 web 可配（admin SPA per-group override → `GroupOverride.sticker_mode`）。本方案让它**真正进入概率计算**而非仅注入 prompt 文本。

### 4.2 解析（client.py 新增 helper）
```python
def _resolve_sticker_base_frequency(self, group_id) -> str:
    """off→调用方直接 return False；inherit→全局 frequency；其余原样。"""
    resolved = self._group_config.resolve(int(group_id)) if group_id else None
    mode = str(getattr(resolved, "sticker_mode", "inherit") or "inherit")
    if mode == "off":   return "off"        # 调用方据此 return False（与 _build_tool_defs 的 off 黑名单一致）
    if mode == "inherit": return self._global_sticker_frequency   # 注入时传入，默认 frequently
    return mode  # rarely/normal/frequently
```

### 4.3 基线乘子
```python
_BASE_FREQUENCY_MULT = {"rarely":0.5, "normal":1.0, "frequently":1.4, "off":0.0}
```
语义：web 设「这个群整体多话痨」(基线)，心情二轴在基线上动态调制（累压、亲密抬、难过转类）。三者相乘，互不覆盖。

### 4.4 与 prompt 文本规则的关系（方案 A 收口）
现 `_STICKER_FREQUENCY_PROMPTS["frequently"]` 写「每条必发，不发就是事故」——与 thinker 的 `sticker:no` 硬冲突（前几轮调研发现）。配合本方案：
- **frequently 文本降级**为「鼓励配图」措辞，删掉「强制每条/不发是事故」，把「最终发不发」让渡给 thinker/概率闸（方案 A）。
- thinker 的 `sticker` 决策仍是回复前的方向提示，但不再是唯一总闸——兜底路按本方案的概率+语义独立决策（thinker:false 时概率降权而非归零，见 §5 待议）。

---

## 5. 待议决策点（写代码前需拍板）

| # | 问题 | 选项 | 建议 |
|---|---|---|---|
| D1 | thinker `sticker:false` 时兜底路是否仍可发 | (a) 仍按概率发，thinker 只降权 / (b) thinker:false 直接 return False（保持现状闸） | **(a)**：否则又回到「thinker 对中性问候判 false → 永不配图」的原始病根。thinker 作降权因子（如 ×0.6），非一票否决 |
| D2 | 是否用 schedule 的 bot 自身 mood，还是引入群氛围 | bot 自身 MoodProfile（已接线）/ 接通 mood_classifier 群氛围 | **bot 自身**：已接线、语义清晰（bot 此刻心情），mood_classifier 未通电且语义是「群冷不冷」，不该混入 |
| D3 | base_frequency=frequently 的乘子 1.4 是否过高 | 1.2 / 1.4 / 1.5 | 1.4 起步，上线后看 send_count 调 |
| D4 | energy 乘子地板 0.5 是否够「话少」 | 地板 0.3 / 0.5 | 0.5 起步（累也是人，甩个「累瘫」表情很自然）；过低就回到变相禁发 |

---

## 6. 实施步骤（拍板后）

1. **types**：`StickerDecisionContext` +mood_energy/+mood_valence/+base_frequency；`affection_stage` 改由 client 真实传入。
2. **decision_provider**：删 `_blocked_by_mood`/`_COLD_MOODS`/`_PLAYFUL_MOODS`；加 `_mood_energy_multiplier`/`_is_playful`/`_BASE_FREQUENCY_MULT`；改 `_send_probability`。
3. **client.py**：构造 context 处读 MoodProfile 数值 + affection + base_frequency；`_select_post_reply_sticker` 加 valence query 偏置；加 `_resolve_sticker_base_frequency`/`_current_affection_stage` helper。
4. **prompt 文本**：`_STICKER_FREQUENCY_PROMPTS["frequently"]` 降级措辞（方案 A）。
5. **config**：上线前打开 `[sticker_placement] enabled=true`（F1）。
6. **测试**（回归清单）：
   - energy 低 → 概率降但 >0（不归零）；energy 高 → 概率升
   - valence 低 → query 含共情词、仍发图（不拦截）
   - valence 低 + 语义检索选中共情类而非开心类
   - affection close/stranger/withdraw → 概率单调
   - base_frequency rarely/normal/frequently → 概率单调；off → return False
   - thinker:false（D1=a）→ 降权但可发
   - 中英 label 不再参与判定（删除 _COLD_MOODS 后旧测试若依赖需改）
7. **同模式扫描（D1 纪律）**：grep 全仓 `_COLD_MOODS`/`_PLAYFUL_MOODS`/`mood_label in`，确认 kaomoji-enforce 路（client.py `_should_force_kaomoji_sticker_round` 用 `_PLAYFUL_KAOMOJI_MOODS`）是否同源 bug，一并修。
8. **maintenance-log + project-info**：记录契约变更与 sticker_placement 启用。

---

## 7. 风险与回滚

- **风险**：sticker_placement 从未在生产启用（F1），打开后是**全新行为上线**，非微调——需灰度观察 send_count 与用户反馈，可能需调 §5 的乘子。
- **回滚**：config 关 `[sticker_placement] enabled=false` 即整条兜底路熄火，回到「仅 thinker:yes + LLM 主动调 send_sticker」的现状。代码层 decision_provider 改动可单文件 revert。
- **不影响**：路径①（LLM 主动 send_sticker by-intent，走 BM25）和路径②（kaomoji-enforce）逻辑独立，本方案只重写路径③的概率+选图。
