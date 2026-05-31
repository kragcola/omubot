# Omubot 拟人修复 Part 5 — 回复分段「自然不打断」重构

> 状态：2026-05-25 立项，**仅审计 + 设计阶段**，不进 Part 1 / Part 2 主线。
>
> 触发：用户观摩 MaiBot 的群内回复时观察到「自然不打断」的体感——MaiBot 即便回复较长，也不会出现"机械硬拆 → 段长齐刷 → 节奏机器感"。Omubot 现状（client.py:_reply_segments）会把回复硬拆到 ≤ 20 字 / 段，导致一句话被切成 3-4 条机械短消息，体感反而比"一条整句"更不自然。
>
> 上游研究锚点：[omubot-humanization-part1-language-feel.md §0](./omubot-humanization-part1-language-feel.md#0-研究锚点-v2-§0-沉淀结论保留)；本文继承"surface ≠ 活跃"原则，但落点不同——Part 1 改 surface markers / register 档位，Part 5 改"分段策略本身"。
>
> 范围声明：Part 5 与 Part 1 U1（segmentation 双实现合并）**正交**——U1 只是把 [services/llm/segmentation.py](../../services/llm/segmentation.py) 的 574 行新模块替换 [services/llm/client.py:359-538 `_reply_segments`](../../services/llm/client.py#L359-L538)（行为等价合并），不动算法；Part 5 重写算法本身。U1 必须先做，Part 5 才有干净基线动手。
>
> 上下文授权（保留勿删）：「依据文档自主做上线前准备，不用问我。我最终做上线前最后验收」。灰度群已落地 993065015 / 984198159。

---

## 1. 现象与体感取证

### 1.1 用户观察

> 「在观摩 MaiBot 机器人回复时，其回复自然不打断，与当前 omubot 可能会出现的强拆句子不同。寻找原因。」

体感差异的具体维度（用户口述 + 旁观佐证）：

| 维度 | MaiBot 体感 | Omubot 现状 | 差异点 |
|---|---|---|---|
| **段长波动** | 一句一段 / 一段一句，长短不齐 | 大量 ≤ 20 字定长段，整齐排布 | Omubot 的硬上限 `_MAX_CHUNK = 20` 让长句被规则截断；MaiBot 让句子保持自然长度 |
| **句末标点** | 大量保留 / 删除是概率性的（结尾句号 90% 删除，逗号 5% 删除 / 20% 转空格） | 全部 strip（[client.py:518-520 `_TRAILING_CLAUSE`](../../services/llm/client.py#L518-L520)） | Omubot 把所有 `，；：、,;:` 一律剪掉，让段尾光秃秃 |
| **段间节奏** | typing 延迟基于"打字时间"模型（每中文字 0.3 s + 0.3 s 回车），有"思考过 10 s 强制压到 1 s"逻辑 | 固定 `_SEGMENT_DELAY = 0.8 s` 段间 sleep（[client.py:58](../../services/llm/client.py#L58)） | Omubot 段间一律 0.8 s；MaiBot 是文本长度自适应 + 上限熔断 |
| **段数控制** | 软上限 `max_sentence_num = 8`；超出时一次性返回原文（不报警语） | 老配置 `max_send_segments = 0` 表示无限；新模块有 `coalesced_overflow` 但生产 0 caller | Omubot 实际无上限 → 规则切割越多段越细 |
| **强拆点** | 引号内不切；冒号前后不切；英文 / 数字之间空格不切 | 引号内不切；CQ code 内不切；但中间标点后必切（_smart_chunk 第二优先级） | Omubot 在"句子超过 20 字"这一硬条件触发时，一定会从 `_CLAUSE_BREAK = 「，；：、,;:」` 找一个点切开 |
| **概率合并** | 按文本长度分 3 档：< 12 字 split=0.2 / < 32 字 split=0.6 / ≥ 32 字 split=0.7；剩下的概率合并相邻段 | 无概率合并；只有 `merge_short_tail`（< 6 字尾段并入前段）的硬规则 | MaiBot 是"先拆后概率合并"，Omubot 是"硬拆 + 短尾兜底" |

### 1.2 代码取证（Omubot）

[services/llm/client.py:378-538](../../services/llm/client.py#L378-L538)：

```python
_SENTENCE_ENDING = set("。！？～…」』）\"!?~)")
_SENTENCE_BREAK = set("。！？～…!?~")
_CLAUSE_BREAK = set("，；：、,;:")
_MIN_CHUNK = 6
_MAX_CHUNK = 20

def _smart_chunk(text, max_len=20):
    # Priority 1: 句末标点 _SENTENCE_BREAK
    # Priority 2: 子句标点 _CLAUSE_BREAK ←—— 这一条是硬拆元凶
    # Priority 3: 字符边界（不破英文词 / CQ）
    # Priority 4: 硬切 max_len
```

证据：

- **硬拆元凶 = 优先级 2 的 `_CLAUSE_BREAK`**：当一句话长度 > 20 字（约 13~16 中文字符）时，`_smart_chunk` 必然在前 20 字内找一个 `，；：、` 切开，不管这个逗号是否表达"独立子意思"。例：『今天我去了一趟超市，顺便买了点水果回来给你尝尝』被切成『今天我去了一趟超市，』+『顺便买了点水果回来给你尝尝』，前段尾的 `，` 又被 [client.py:518-520](../../services/llm/client.py#L518-L520) `chunks = [c.rstrip(_TRAILING_CLAUSE) for c in chunks]` 抹掉，结果是『今天我去了一趟超市』+『顺便买了点水果回来给你尝尝』，强行变成两条消息。
- **`_TRAILING_CLAUSE`** 是第二刀：所有段尾的 `，；：、,;:` 一律剪掉，让段尾光秃秃。中文 IM 真人聊天的段尾保留率约 50%（PACLIC 2008 子句末标点统计），Omubot = 0%。
- **`_SEGMENT_DELAY = 0.8`** 是第三刀：每段间 sleep 0.8 s 不论段内字数，让 3 个 12 字段（共 36 字）的发送总时间 = 3 × 0.8 = 2.4 s 段间延迟 + humanizer.delay 的 char_delay；MaiBot 一段 36 字的发送时间 ≈ 36 × 0.3 = 10.8 s 但只有一段，体感是"一条消息打完发出"。

### 1.3 代码取证（MaiBot）

[utils.py:236-402](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/utils/utils.py#L236-L402) 的 `split_into_sentences_w_remove_punctuation`：

```python
separators = {"，", ",", " ", "。", ";", "\n"}
# 1. 切成 (内容, 分隔符) 元组
# 2. 概率合并：
if len_text < 12:    split_strength = 0.2
elif len_text < 32:  split_strength = 0.6
else:                split_strength = 0.7
merge_probability = 1.0 - split_strength
# 80%/40%/30% 的概率把相邻两段合并回一段
```

[utils.py:524-567](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/utils/utils.py#L524-L567) 的 `calculate_typing_time`：

```python
chinese_time = 0.3   # 每中文字 0.3 s
english_time = 0.15  # 每英文字 0.15 s
# 单字中文 → 0.3 × 3 + 0.3 = 1.2 s
# 思考过 10 s → 强制压到 1 s
```

[uni_message_sender.py:320-326](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/message_receive/uni_message_sender.py#L320-L326) 的发送循环：

```python
typing_time = calculate_typing_time(input_string=message.processed_plain_text, ...)
await asyncio.sleep(typing_time)  # 段间延迟与字数耦合
```

关键差异（与 Omubot 对照）：

| 项 | MaiBot | Omubot |
|---|---|---|
| 切分锚点 | `{，, ,, " ", 。, ;, \n}` | `{。！？～…!?~}`（句末）+ `{，；：、,;:}`（子句，超 20 字时强拆） |
| 长度上限 | 软上限 `max_length = 512` 字（超出返回 default reply）；句数上限 `max_sentence_num = 8` | 硬上限 `_MAX_CHUNK = 20`（每段强拆） |
| 概率合并 | 短文本 80% 合并、中长文本 40~30% 合并 | 无 |
| 段尾标点 | 概率删除（句号 90% 删 / 逗号 5% 删 + 20% 转空格） | 全部剪光 |
| 段间延迟 | 字数 × 0.3 s + 思考时间熔断 | 固定 0.8 s |
| 引号内 | 不切 | 不切 |
| 冒号前后 | 不切 | 不切 |
| 数字 / 英文之间空格 | 不切 | 不切（_smart_chunk Priority 3 处理） |
| 引号外保护 | 颜文字 protect_kaomoji 占位符 | 颜文字（v1 humanization 已剥离）/ CQ code 占位 |

---

## 2. 三大根因（按"屎山"严重度排序）

### 2.1 根因 1 — `_MAX_CHUNK = 20` 硬上限是错的策略

`_MAX_CHUNK = 20` 写死的是"消息长度"，不是"句子边界"。中文 IM 真人单条消息长度分布（PACLIC 2008）：

- ≤ 20 字消息占 77.95%
- 20~40 字占 14.6%
- ≥ 40 字占 7.45%

也就是说，**真人 22% 的消息超过 20 字**，Omubot 把这 22% 一律硬切到 ≤ 20 字 → 这正是用户感知到的"机械感"。

修法：**取消硬切**，改为软上限 + 概率合并。

### 2.2 根因 2 — 段尾标点全剪光

`chunks = [c.rstrip(_TRAILING_CLAUSE) for c in chunks]`（[client.py:520](../../services/llm/client.py#L520)）一律 strip 段尾的 `，；：、,;:`。这条逻辑的初衷可能是「段尾光秃看起来像独立消息」，但反效果——

- 中文 IM 真人单条消息约 50% 保留段尾标点（句号 / 感叹号高保留，逗号 / 顿号低保留）
- Houghton 2018：段尾句号 = 突兀 / 敷衍；段尾**逗号**反而是"还没说完，但先停一下"的自然信号
- Omubot 全 strip → 每段都像"硬剪过"，段尾光秃统一 → 模式化机器感

修法：**概率保留**——句号 90% 删 / 感叹号问号保留 / 逗号 70% 保留 / 顿号 80% 保留 / 冒号 100% 保留（冒号后通常跟内容，剪了破坏语义）。

### 2.3 根因 3 — `_SEGMENT_DELAY = 0.8` 固定段间延迟

Humanizer 内的 char_delay 已经是字数自适应的，但**段间** sleep 0.8 s 与字数无关。结果：

- 单字短段（"嗯"、"哈"）→ 0.8 s 段间 + 0.x s humanizer ≈ 1.0 s 节奏
- 长段（25 字）→ 0.8 s 段间 + ~5 s humanizer ≈ 5.8 s 节奏

短段"段间过快"（让短促应答看起来仓促），长段"段间正常"——但因为 §2.1 硬切，长段几乎不存在。所以体感是「全是 1.0 s 节奏的均匀短段」。

修法：**段间延迟与上一段字数耦合**——参考 MaiBot calculate_typing_time，改成 `delay = max(0.5, min(3.0, prev_seg_chars × 0.15))`。

---

## 3. 设计：Part 5 重写「自然不打断」分段

### 3.1 命名

`services/segmentation/natural_split.py`（新文件）；保留 `services/llm/segmentation.py` 暂作 fallback。

### 3.2 算法（融合 MaiBot + Omubot 双方优点）

```python
def natural_split(text, *, soft_max_chars=80, max_sentence_num=8, register=None):
    """
    1. 保护：CQ code / kaomoji / URL / ASCII token（已有 _ASCII_TOKEN_RE / _URL_TOKEN_RE）
    2. 切分：在所有 separators {。！？～…!?~ ，,；; 。 \\n} 处切
       - 引号内不切（已有 inside_quote 标记）
       - 冒号前后不切
       - 英文 / 数字之间空格不切
       - 不再有 _MAX_CHUNK 硬切
    3. 概率合并（按文本长度分档，与 MaiBot 同）：
       - len < 12 → split_strength = 0.2（80% 合并）
       - len < 32 → split_strength = 0.6（40% 合并）
       - len < 80 → split_strength = 0.7（30% 合并）
       - len ≥ 80 → split_strength = 0.85（15% 合并）  # 长文本仍倾向多段
    4. 段尾标点概率保留：
       - 。/ 。 → 90% 删
       - ！/？/ ！ / ？ → 100% 保留
       - ，/ , → 70% 保留 / 20% 删 / 10% 转空格
       - 、 → 80% 保留
       - ；/ ; → 90% 保留
       - ：/ : → 100% 保留
    5. 段数硬上限 max_sentence_num = 8：超过则把尾部 N 段合成一条
    6. 段长软上限 soft_max_chars = 80：仅当段超过软上限时，递归调用 natural_split 拆这一段
       - 避免出现单段 200+ 字阻塞发送
       - 软上限是"分段策略层" / 与 MaiBot 的 max_length=512 字（消息总长上限）正交
    7. register awareness（对接 Part 1 V1 RegisterClassifier）：
       - register=quiet  → split_strength × 0.7（更多合并 → 段更长）
       - register=playful → split_strength × 1.2（更细粒度 → 段更短）
       - register=neutral → 不变
    """
```

### 3.3 段间延迟（client.py:_SEGMENT_DELAY 重写）

```python
def inter_segment_delay(prev_segment: str, *, register=None, slot_energy=1.0) -> float:
    """段间延迟与上一段字数耦合 + register / slot.energy 调系数。"""
    chinese_chars = sum("一" <= ch <= "鿿" for ch in prev_segment)
    english_chars = sum(ch.isascii() and ch.isalnum() for ch in prev_segment)
    base = chinese_chars * 0.15 + english_chars * 0.07
    if register == "quiet":
        base *= 1.5
    elif register == "playful":
        base *= 0.7
    base *= max(0.5, slot_energy)  # slot.energy 低时延迟拉长
    return max(0.5, min(3.0, base))
```

注意：与 Part 1 U3 Humanizer 升级 **不重复**——Humanizer 处理的是"段内 typing 时间"（逐字打字模拟），这里处理的是"段间停顿"。两者输入相似但语义正交。

### 3.4 与 Part 1 的接口

| Part 1 子任务 | Part 5 接入点 |
|---|---|
| **U1（segmentation 双实现合并）** | Part 5 必须等 U1 落地后才动手。U1 不动算法只合并模块；Part 5 在合并后的单一模块上重写 `natural_split`。 |
| **U3（Humanizer register-aware）** | Part 5 段间延迟读相同的 register / slot 参数，复用 Part 1 V1 RegisterClassifier 写入 `bus.state.register.label`；不再起新分类器。 |
| **V11（critic-rewrite-loop）** | Part 5 不参与 critic 评分；分段是发送层，critic 是生成层。两者顺序：LLM 生成 → V11 critic 评分（可能重写）→ Part 5 natural_split → 段间延迟。 |
| **V8（StylometricScorer）** | scorer 5 轴中"surface_penalty"轴可以加一个子项"段长方差 < threshold 扣分"——但这是 Part 1 V8 范围，Part 5 不动 V8。 |

### 3.5 不做的事

- **不引入错别字生成**（MaiBot 的 `chinese_typo` 模块）。错别字的伪人化收益远不及风险（admin 看不懂、客服群尴尬），列入 **Part 5+ 长尾** 备选，本期不做。
- **不引入"思考过 10 s 强制压到 1 s"熔断**（MaiBot calculate_typing_time）。Omubot 现在是 SSE 流式，"思考时间"的概念与 MaiBot 不同；列入 Part 2 typing 延迟范畴。
- **不动 humanizer 段内逐字打字延迟**（Part 1 U3 / V10 已经动过）。Part 5 只动段间延迟与分段算法。
- **不引入概率删 / 转空格分隔符**（MaiBot 的 5% / 20% 逗号变换）。这条改动信噪比不高，等 Part 5 主体稳定后再评估。
- **不引入 max_length = 512 字熔断 + default reply 兜底**（MaiBot 的"长文本拒绝回复"）。Omubot 现在 `max_tokens = 1024`，rarely 超过 400 字；列入 Part 2 长回复策略。

---

## 4. 子任务编号 P5.1 ~ P5.6

| 编号 | 任务 | 依赖 | 关键产物 | 单测 |
|---|---|---|---|---|
| **P5.1** | natural_split 算法 + 概率合并 + 段尾标点概率保留 | Part 1 U1 完成 | `services/segmentation/natural_split.py` ≤ 220 行 | `tests/test_natural_split.py` +12（短文本 80% 合并 / 中文本 40% 合并 / 长文本 30% 合并 / 段尾标点概率分布 / 引号内不切 / 冒号前后不切 / soft_max_chars 递归拆 / max_sentence_num 尾部合并 / register=quiet / register=playful / register=None / cancel-path） |
| **P5.2** | inter_segment_delay 函数 + register / slot 联动 | Part 1 U3 + V1 完成 | client.py:_SEGMENT_DELAY 替换为函数调用 ≤ 25 行 | `tests/test_inter_segment_delay.py` +5 |
| **P5.3** | client.py:_reply_segments 切到 natural_split | P5.1 + P5.2 | client.py 改 5 行；保留 _reply_segments 老实现作 fallback（feature flag `humanization.natural_split_enabled` 默认 off） | `tests/test_reply_segments_natural.py` +4 |
| **P5.4** | 灰度 + 24h 体感比对（natural vs hardcoded） | P5.3 | `scripts/dev/measure_segmentation.sh` 采样 200 条 group reply | — |
| **P5.5** | 默认开 + 卸 fallback | P5.4 + 用户验收 | client.py 删除 _smart_chunk / _split_naturally / _coalesce_segments 等 ≈ 200 行老代码 | 全量 pytest 回归 |
| **P5.6** | 文档收口 + maintenance-log + Part 5 §6 状态表 | P5.1 ~ P5.5 | 本文 §6 + maintenance-log 当日条目 | — |

合计：**新增代码估算 ≤ 250 行 / 净删 ≈ 200 行 = 净增 ≈ 50 行**；**新增测试 ≥ 21 条**。

---

## 5. 出口标准

- [ ] 24h 灰度 200 条 group reply 采样：
  - 段长方差 ≥ 30（v1 baseline ≤ 8）
  - 段尾标点保留率 30%~50%（v1 baseline = 0%）
  - 单段 ≥ 30 字消息占比 ≥ 8%（v1 baseline ≈ 0%）
  - 平均段数 / reply 从 v1 baseline 的 ≈ 3.5 降到 ≈ 2.0
- [ ] 用户主观验收：「不再机械硬拆」「段长有变化」「段尾不再光秃秃」
- [ ] `uv run pytest -q` ≥ Part 1 出口基线 + 21 = ≥ 1697 passed
- [ ] feature flag 30 秒回滚演练成功
- [ ] D1 同模式扫描通过：`grep -rn 'natural_split\|inter_segment_delay\|_MAX_CHUNK' --include='*.py'` 仅命中 `services/segmentation/`、`services/llm/client.py`、`tests/`

---

## 6. 当前状态

| 编号 | 状态 | 落地证据 |
|---|---|---|
| 立项 | ✅ 完成（本文） | 用户原话锚点；MaiBot 取证（utils.py:236 + uni_message_sender.py:320） |
| 取证 | ✅ 完成（§1 + §2） | Omubot client.py:378-538 / segmentation.py:574 行 0 caller / TRAILING_CLAUSE strip / _SEGMENT_DELAY=0.8；MaiBot split_into_sentences_w_remove_punctuation / calculate_typing_time / response_splitter config |
| 设计 | ✅ 完成（§3） | natural_split + inter_segment_delay 双函数；与 Part 1 U1/U3/V1/V11/V8 接口表 |
| P5.1 ~ P5.6 | P5.1~P5.4 ✅；P5.5 🟡 ⚠️；P5.6 ⏳ | P5.4 用户授权代验收（2026-05-25）忽略 24h 窗口；P5.5 代码已落地（默认翻 True + `_legacy_segment_path` 删除 + 1980 passed），但 2026-05-27 误判验收已撤回 — 与 [Part 6 bugfix Part 1](./omubot-humanization-part6-bugfix-part1.md) §1.2 故障强耦合，bugfix Phase 1A 改名 `disable_natural_split` → `streaming_already_emitted` 会重构 P5 fallback 路径，且 balanced 实地「单段死锁」证伪了 P5 默认 True 在 profile 干预下真正分段；阻塞于 bugfix Phase 1 全绿后再判定收口；P5.6 同步阻塞 |

---

## 7. 与既有 Part 的边界

- **与 Part 1 U1 的差异**：U1 = 行为等价合并 dual-impl（删 client.py:359-538 _reply_segments，改用 segmentation.py 的导入）；Part 5 = 算法重写。U1 必须先做，Part 5 才在干净基线上动手。
- **与 Part 1 V8 的差异**：V8 是 critic 评分轴（包含 surface_penalty / register_fit 等），Part 5 是发送层分段策略。V8 评分输入是「LLM 生成的整段文本」，Part 5 处理输入是「V8 评分通过后准备发送的整段文本」。
- **与 Part 2 的差异**：Part 2 typing 延迟讨论的是「输入感知层」（用户打字时 bot 是否插话），Part 5 讨论的是「输出发送层」（bot 内部段间延迟）。
- **与 Part 3 的差异**：Part 3 群语境讨论"该不该回 / 抢话"，Part 5 不影响"是否回复"。
- **与 Part 6 的差异（2026-05-25 补）**：Part 6 是「源头生成调度」（call 数 / 触发节奏 / 可中断性），Part 5 是「事后切分」。两者在不同方案下耦合关系不同：
  - **Part 6 方案 A（plan-then-utter）/ 方案 C（reactive replan）**：每 utter call 输出 max_tokens=150 已是单段 → Part 5 `natural_split` 退化为 noop（仅尾部标点清洁保留）。互斥旗标 `plan_then_utter.disable_natural_split = true` 默认开
  - **Part 6 方案 B（streaming-as-segment）**：在 SSE token-stream 上 online 切分 → 与 Part 5 `natural_split` **语义直接互斥**（事后切分 vs 流式同步切分）。互斥旗标 `streaming_segment.enabled` 与 `natural_split.enabled` 不可同开
  - **Part 6 方案 D（pause-then-extend）**：完全正交 — Part 5 仍负责段间延迟，Part 6 D 负责段间延迟之后是否追发；段间节奏 = `inter_segment_delay` + `pause_then_extend_window`
  - 详见 [part6 doc §6.2](./omubot-humanization-part6-source-side-generation.md#62-与-part-5)

---

## 8. 风险与回滚

| 风险 | 触发条件 | 回滚 |
|---|---|---|
| natural_split 误把"独立小句"合并成一长段 | 概率合并参数过于偏向合并 | feature flag `natural_split_enabled=false` + restart（30 秒） |
| 段尾标点概率保留破坏 IM 阅读体感 | 用户判断"看起来更书面" | 调整 `trailing_punct_keep_ratio` 4 个分类参数 |
| 段间延迟字数耦合让长段过慢 | 单段 60 字 → 9 s 段间延迟 | 上限改 max=2.0；下限改 min=0.3 |
| natural_split 与 Part 1 V11 critic-rewrite 二次 round 后的文本不兼容 | 重写文本带 markdown 残留 / 异常空白 | natural_split 内置 _clean_text 复用 P0/U1 路径 |

紧急回滚（30 秒）：

```bash
# config/config.json:
#   "humanization": {"natural_split_enabled": false}
docker compose restart bot
```

---

## 9. 引用

- MaiBot 源码（深读）：[utils.py:236 split_into_sentences_w_remove_punctuation](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/utils/utils.py#L236) / [utils.py:524 calculate_typing_time](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/utils/utils.py#L524) / [uni_message_sender.py:320](../../../../Users/kragcola/MaiM-with-u/MaiBot/src/chat/message_receive/uni_message_sender.py#L320) / [bot_config_template.toml:234 response_splitter](../../../../Users/kragcola/MaiM-with-u/MaiBot/template/bot_config_template.toml#L234)
- Omubot 源码：[client.py:359-538 _reply_segments + _smart_chunk + _split_naturally](../../services/llm/client.py#L359-L538) / [segmentation.py:574 行](../../services/llm/segmentation.py)
- 研究锚点：见 [Part 1 §0 + 附录 A](./omubot-humanization-part1-language-feel.md#附录-a-—-引用研究22-篇-9-家产品架构)；本文复用 PACLIC 2008（≤ 20 字消息 77.95%）+ Houghton 2018（段尾句号突兀）
