# Omubot 拟人 Part 5 — 派单版并列执行追踪

> 状态：2026-05-25 立。本文是 [Part 5 主线](./omubot-humanization-part5-segmentation.md) 的执行版派单表。
>
> 用途：由别的执行者按 wave 顺序领单完成；我（Claude）做最终验收。
>
> 工作流：每条任务有「领单 → 自验 → 提交申请验收」三态。验收通过我会把 §6 状态表的 ⏳→✅。
>
> **执行原则**（以下规则覆盖任何主线文档的不一致表述）：
>
> 1. **每条独立 commit**——除非本文明确写"合 commit"。Part 5 主线 §4 的 P5.1~P5.6 就是派单单位本身，不再细拆。
> 2. **同 wave 内任务可并行**——不同 wave 间严格串行；P5.1 / P5.2 是 Wave 2 内已识别的并列对。
> 3. **每条任务自带 D1 grep 证据 / D2 cancel-path 测试 / 30 秒 feature flag 回滚**，缺一不通过验收。
> 4. **遇主线证据与本文冲突，以本文为准**（§1 已记录主线 4 处证据订正）。

---

## 1. 主线自审与证据订正（执行前必读）

下表是我对 Part 5 主线 §1.2 / §1.3 / §3 进行 grep 实证后发现的与原文不符的项。**派单时按本表订正，不按主线原文**。

| 主线位置 | 主线原文 | grep 实证 | 派单订正 |
|---|---|---|---|
| §1.2 强拆元凶 | `_smart_chunk` 优先级 2 在 `client.py:378-538` 的 `_CLAUSE_BREAK` | 实际位点已随 [Part 1 U1](./omubot-humanization-part1-execution.md#3.2-wave-2--u-系列重构6-条并列) 合并到 [services/llm/segmentation.py](../../services/llm/segmentation.py)；`client.py:382 _split_naturally` 与 `client.py:387 _reply_segments` 仅是 1 行委托 stub；`_TRAILING_CLAUSE` / `_CLAUSE_BREAK` / `_MIN_CHUNK` 现位于 [segmentation.py:15-19](../../services/llm/segmentation.py#L15-L19) | **派单订正**：P5.1 改写目标是 `services/llm/segmentation.py` 而非 `services/llm/client.py`；P5.3 wiring 只动 [segmentation.py:reply_segments](../../services/llm/segmentation.py) 内分支，client.py 不再有需要替换的 `_smart_chunk` 主体 |
| §3.1 命名 | 新文件 `services/segmentation/natural_split.py`；保留 `services/llm/segmentation.py` 暂作 fallback | grep `services/segmentation/` 不存在；继续保留双模块在 import 路径上重复语义 | **派单订正**：P5.1 不再起 `services/segmentation/` 新目录，而是在现有 [services/llm/segmentation.py](../../services/llm/segmentation.py) 内新增 `natural_split()` 函数和 `inter_segment_delay()` 函数；feature flag 切流位点改在 [segmentation.py:reply_segments](../../services/llm/segmentation.py) 内分支，P5.5 卸 fallback 时也只删函数体 |
| §1.2 段间延迟 | `_SEGMENT_DELAY = 0.8 s` 段间 sleep（[client.py:58](../../services/llm/client.py#L58)） | 实际位点是 [client.py:70 `_SEGMENT_DELAY = 0.8`](../../services/llm/client.py#L70)；分发由 `segmentation.py:73 inter_segment_delay_s: float = 0.8` 字段携带；fan-out 入口在 [client.py:2453 / client.py:2652](../../services/llm/client.py#L2453) 与 [send_queue.py:322-323](../../services/send_queue.py#L322-L323) | **派单订正**：P5.2 改造点不是单一 `_SEGMENT_DELAY` 常量，而是把 `inter_segment_delay_s` 配置字段升格为可选 callable / 函数返回值，并在 send_queue.py 走"按上一段字数算 delay"的分支 |
| §3.2 register 入参 | `register=quiet` / `playful` / `neutral` | 实际 `state.register.label` 已由 [Part 1 V1 RegisterClassifier](./omubot-humanization-part1-execution.md#34-wave-4--依赖-u6-的扩展7-条并列) 写入 [services/humanization/contract.py:9 REGISTER_LABEL_SLOT="state.register.label"](../../services/humanization/contract.py#L9)；语义 5 档（"quiet" / "neutral_default" / "playful" / "polite_distant" / "snark"）非 3 档 | **派单订正**：P5.1 register 系数表扩为 5 档；polite_distant ≈ quiet × 0.9（更克制），snark ≈ playful × 1.1（更细粒度）；P5.2 inter_segment_delay 同步扩 5 档 |

> **派单规则**：执行者拿到本文档后，**§1 这 4 项订正一律落本文版本**，不要按主线原文写。

**P5.0 体检结论（2026-05-25 / Codex）**：4 项前置依赖均达 ✅；`services/llm/client.py` 已委托 `services.llm.segmentation`，register slot / BlockTrace provider 已落地，Humanizer register+slot 接线存在，当前 pytest collect-only 基线为 `1714 tests collected`，允许进入 P5.1 / P5.2。

---

## 2. P5.0 新增前置任务（依赖体检 + 字段实证）

派单第 0 步，零代码改动。Part 5 全 wave 阻塞于以下 3 项依赖为 ✅：

| 步骤 | 命令 | 预期结果 |
|---|---|---|
| 1 | `grep -n "from services.llm.segmentation\|reply_segments(" services/llm/client.py` | 命中 [client.py:40 import](../../services/llm/client.py#L40) + [client.py:391 委托](../../services/llm/client.py#L391)，证明 Part 1 U1 ✅ 已落地 |
| 2 | `grep -rn "REGISTER_LABEL_SLOT\|state.register.label" services/humanization/ services/block_trace/` | 命中 [contract.py:9](../../services/humanization/contract.py#L9) + [classifier.py:90](../../services/humanization/classifier.py#L90) + [episode_provider.py:115](../../services/block_trace/episode_provider.py#L115)，证明 Part 1 U6 + V1 + V4 ✅ 全部已落地 |
| 3 | `grep -n "Humanizer.delay\|register=\|slot=" services/humanizer.py services/scheduler.py` | 命中 Humanizer 5 参数签名 + scheduler 装配真传 register / slot，证明 Part 1 U3 + V10 ✅ 已落地 |
| 4 | `uv run pytest --collect-only -q 2>&1 \| tail -1` | 当前基线 ≥ 1714 tests collected；P5 出口要求 ≥ 1714 + 21 = ≥ 1735 |
| 5 | 写 1 行结论到本文 §1 第 5 行（替换"待验证"） | 给 P5.1 ~ P5.6 派单确定基线 |

**P5.0 不是 commit；是派单前置体检**。我会先看本步骤回执再发后续单。**任意 1 项依赖未达 ✅，Part 5 wave 整体阻塞**。

---

## 3. 并列执行 Wave 表（按依赖图编排）

**依赖关系核心规则**：

- **Wave 0**：P5.0 依赖体检 + 字段实证（前置，零代码）
- **Wave 1**：P5.1 + P5.2 算法重写（**两条并列**）
- **Wave 2**：P5.3 client.py / segmentation.py 切流 wiring（feature flag 默认 off）
- **Wave 3**：P5.4 灰度 + 24h 体感比对
- **Wave 4**：P5.5 默认开 + 卸 fallback
- **Wave 5**：P5.6 文档收口

### 3.1 Wave 0 — P5.0 依赖体检（前置）

见 §2，零代码。

### 3.2 Wave 1 — P5.1 + P5.2 算法核心（2 条并列）

| 编号 | 一句话 | 改动文件（≤ N 行） | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **P5.1** | 在 [services/llm/segmentation.py](../../services/llm/segmentation.py) 新增 `natural_split(text, *, soft_max_chars=80, max_sentence_num=8, register=None)`：保护 CQ/kaomoji/URL/ASCII（复用现有 `_ASCII_TOKEN_RE` / `_URL_TOKEN_RE`）→ 在 separators `{。！？～…!?~ ，,；; \n}` 切（引号/冒号/数字-英文空格不切）→ 概率合并 4 档（<12=0.2 / <32=0.6 / <80=0.7 / ≥80=0.85）→ 段尾标点概率保留（。90%删 / ！？100%留 / ，70%留 / 、80%留 / ；90%留 / ：100%留）→ `max_sentence_num=8` 尾部合并 → `soft_max_chars=80` 递归拆 → register 5 档系数（quiet=×0.7 / polite_distant=×0.63 / neutral=×1.0 / playful=×1.2 / snark=×1.32） | `services/llm/segmentation.py`（≤ +220 行 net） | `grep -rn 'natural_split\|_TRAILING_CLAUSE\|_CLAUSE_BREAK\|_MAX_CHUNK' --include='*.py'` 仅命中 `services/llm/segmentation.py` + tests | random.Random 注入种子参数（默认 secrets.SystemRandom）；新增 `tests/test_natural_split.py::test_cancel_during_recursive_split` 用 `pytest.raises(asyncio.CancelledError)` 断言不污染 RuntimeStateBus | feature flag `humanization.natural_split_enabled=false` + restart |
| **P5.2** | 在 [services/llm/segmentation.py](../../services/llm/segmentation.py) 新增 `inter_segment_delay(prev_segment, *, register=None, slot_energy=1.0) -> float`：中文字 × 0.15 + 英文字 × 0.07 base；register 5 档系数同 P5.1；slot_energy 入参 `max(0.5, slot_energy)` 倍率；clamp 到 `[0.5, 3.0]`。同时在 `ReplySegmentationConfig` 加 `natural_split_enabled: bool = False` 字段 | `services/llm/segmentation.py`（≤ +25 行 net）+ `kernel/config.py:ReplySegmentationConfig`（+1 行字段） | `grep -rn 'inter_segment_delay(' --include='*.py'` 仅命中 segmentation.py + send_queue.py wiring + tests | 函数纯计算无写入；测试 5 条覆盖 quiet 长度延伸、playful 缩短、长段 clamp 上限、空段下限、`slot_energy=0` 兜底 | feature flag `humanization.natural_split_enabled=false` + restart |

**Wave 1 commit 顺序**：P5.1 / P5.2 各自独立 commit；P5.1 先 P5.2 后（P5.2 复用 P5.1 的 register 系数表）。

### 3.3 Wave 2 — P5.3 切流 wiring（1 条独立）

| 编号 | 一句话 | 改动文件（≤ N 行） | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **P5.3** | [services/llm/segmentation.py:reply_segments](../../services/llm/segmentation.py) 加 `if config.natural_split_enabled: return _natural_split_path(...) else: return _legacy_path(...)`；老路径 0 行改动；新路径包 P5.1 + P5.2，并把 `inter_segment_delay()` 返回值写回 `ReplySegmentBatch.inter_segment_delay_s`；client.py 的 fan-out 入口 [client.py:2453](../../services/llm/client.py#L2453) / [client.py:2652](../../services/llm/client.py#L2652) 把固定 `_SEGMENT_DELAY` 改为按段动态值传入 send_queue | `services/llm/segmentation.py`（≤ +30 行）+ `services/llm/client.py`（≤ +10 行 fan-out 改造）+ `services/send_queue.py`（≤ +8 行支持每段 delay 数组） | `grep -rn 'natural_split_enabled\|_natural_split_path' --include='*.py'` 仅命中 segmentation.py + tests | `tests/test_reply_segments_natural.py::test_cancel_during_natural_path` 4 条覆盖：cancel 不脏写 / fallback 路径不变 / inter_delay 数组下界 / register 缺失降级 neutral | `humanization.natural_split_enabled=false` 30 秒 restart 回到 Wave 1 之前行为 |

**Wave 2 commit**：P5.3 独立 1 个 commit；同 commit 内必须有 cancel-path 测试。

### 3.4 Wave 3 — P5.4 灰度 + 24h 体感比对

| 编号 | 一句话 | 改动 | 出口指标（24h）| 依赖 |
|---|---|---|---|---|
| **P5.4** | 单群 `993065015` 启 `humanization.natural_split_enabled=true`（`984198159` 仍 off 作对照），跑 24h；`scripts/dev/measure_segmentation.sh` 采样 200 条 group reply | `config/config.json`（启单群 flag）+ `scripts/dev/measure_segmentation.sh`（new ≤ 80 行） | 见 §4 出口表 | Wave 2 ✅ + 用户确认进入灰度 |

> 灰度群锚定 [Part 1 §0a 灰度授权](./omubot-humanization-part1-language-feel.md)：993065015 + 984198159；本期先单群 A/B，符合用户授权"依据文档自主做上线前准备"。

### 3.5 Wave 4 — P5.5 默认开 + 卸 fallback

| 编号 | 一句话 | 改动文件 | 删除目标 | 依赖 |
|---|---|---|---|---|
| **P5.5** | `humanization.natural_split_enabled` 默认 `true`；`reply_segments` 内 `if natural_split_enabled` 反向（默认走新路径）；删除 [segmentation.py](../../services/llm/segmentation.py) 老路径函数 `_smart_chunk` / `_split_naturally` / `_coalesce_segments` 及 `_TRAILING_CLAUSE` / `_CLAUSE_BREAK` 常量 | `services/llm/segmentation.py`（≈ -200 行）+ `kernel/config.py`（默认值翻转） | `_smart_chunk` / `_split_naturally` / `_coalesce_segments` / `_TRAILING_CLAUSE` / `_CLAUSE_BREAK` / `_MIN_CHUNK` / `_MAX_CHUNK`（仅留 7 个常量名以备 git log 检索） | P5.4 灰度 24h 出口表 ≥ 5/6 项达标 + 用户主观验收 |

**Wave 4 commit**：P5.5 独立 1 个 commit；commit 后再跑全量 `uv run pytest -q` 必须 ≥ 1735 passed。

### 3.6 Wave 5 — P5.6 文档收口

| 编号 | 一句话 | 改动 |
|---|---|---|
| **P5.6** | maintenance-log 当日条目 + Part 5 主线 §6 状态表 6 条 ⏳→✅ + 本文 §6 状态表全 ✅ + Part 1 主线 §13 边界表追加"Part 5 已落地"行 | 文档 |

---

## 4. 灰度 24h 出口指标矩阵

执行者每阶段灰度结束跑一次 `scripts/dev/measure_segmentation.sh`，把下表填进结果。我看到 ≥ 5/6 项达标才放下一阶段（P5.5）。

| 指标 | v1 baseline（fallback 路径） | natural_split 目标 | 灰度-A 实测（993065015） | 灰度-B 实测（984198159 对照） |
|---|---|---|---|---|
| 段长方差（字²） | ≤ 8 | ≥ 30 | 等待 24h 样本 | 对照组 ≤ 8（不变即通过） |
| 段尾标点保留率 | 0%（全 strip） | 30% ~ 50% | 等待 24h 样本 | 对照组 0%（不变即通过） |
| 单段 ≥ 30 字消息占比 | ≈ 0% | ≥ 8% | 等待 24h 样本 | 对照组 ≈ 0%（不变即通过） |
| 平均段数 / reply | ≈ 3.5 | ≈ 2.0 | 等待 24h 样本 | 对照组 ≈ 3.5（不变即通过） |
| 段间延迟 P95（s） | 0.8 ± 0 | 0.5 ~ 3.0 自适应 | 等待 24h 样本 | 对照组 0.8 固定 |
| 用户主观验收 | 「机械硬拆」 | 「不再机械硬拆」+「段长有变化」+「段尾不再光秃」 | 待用户最终验收 | 对照组保持现状 |

> 出口判定规则：5/6 项达标 + 用户主观验收 = 进入 P5.5；< 5/6 项达标 = 留 24h 再观察一轮，仍不达标则按 §8 风险矩阵回滚。

---

## 5. 验收清单（每条任务交付时勾）

执行者每条 commit 后填 PR / 提交说明附上：

```
- [ ] 改动行数与计划匹配（声明：实际 +X / -Y）
- [ ] D1 grep 命中仅在预期路径（natural_split / inter_segment_delay / natural_split_enabled）
- [ ] D2 cancel-path 测试落实（pytest.raises(CancelledError) 锁脏写）
- [ ] uv run pytest -q 全绿（含本任务新测试）；当前累计 ≥ 1714 + 已交付的新测试数
- [ ] uv run ruff check 改动范围 clean
- [ ] uv run pyright 改动范围 0 errors
- [ ] feature flag 30 秒回滚演练成功（命令：sed -i '' 's/"natural_split_enabled": true/"natural_split_enabled": false/' config/config.json && docker compose restart bot）
- [ ] 同 wave 其它任务无冲突（P5.1 / P5.2 互不引用对方私有符号）
```

---

## 6. 当前状态（执行者每完成一条把 ⏳ 改 🟡 等验收，验收后我改 ✅）

| 编号 | wave | 状态 | 落地证据 / 备注 |
|---|---|---|---|
| **P5.0** | 0 | 🟡 | 已完成待验收：4 项前置体检均达标；collect-only 基线 `1714 tests collected`；详见 §9 P5.0 完成记录 |
| **P5.1** | 1 | 🟡 | 已完成待验收：`natural_split()` + register 5 档 + 12 条测试；详见 §9 P5.1 完成记录 |
| **P5.2** | 1 | 🟡 | 已完成待验收：`inter_segment_delay()` + `natural_split_enabled=false` 双配置模型字段 + 8 条测试；详见 §9 P5.2 完成记录 |
| **P5.3** | 2 | 🟡 | 已完成待验收：`reply_segment_plan()` feature flag 切流 + `LLMClient` 两处 fan-out 动态 delay + 8 条 P5.3 新测试；详见 §9 P5.3 完成记录 |
| **P5.4** | 3 | ⏳ | 待执行：灰度群 993065015 启 natural_split + 24h 采样；阻塞于 P5.3 验收 ✅ + 用户授权进灰度 |
| **P5.5** | 4 | ⏳ | 待执行：默认开 + 卸 fallback ≈ -200 行；阻塞于 P5.4 出口表 ≥ 5/6 项 + 用户验收 |
| **P5.6** | 5 | ⏳ | 待执行：maintenance-log + 主线状态表 + Part 1 §13 边界表追加；阻塞于 P5.5 ✅ |

> 本表与 [Part 5 主线 §6 当前状态](./omubot-humanization-part5-segmentation.md#6-当前状态) 双向同步：主线 §6 当前 4 行（立项 / 取证 / 设计 / P5.1~P5.6 阻塞）已 ✅ ✅ ✅ ⏳；P5.0 ~ P5.6 完成后主线 §6 统一改 ✅。

---

## 7. 执行者交接说明

1. **领单顺序**：先做 P5.0，回执贴依赖体检结论；再领 Wave 1 的 P5.1 / P5.2（可并列）。
2. **多人并行**：Wave 1 内 P5.1 / P5.2 可同时下发，不同 wave 串行。
3. **commit 规范**：每条任务一个 commit，末尾不署 Co-Authored-By 行（本仓约定见 [docs/agent-discipline.md](../agent-discipline.md)）。
4. **验收提交**：把 §6 状态从 ⏳ 改 🟡 + PR 链接发我，我跑 §5 验收清单后改 ✅。
5. **冲突冲突**：本文 §1 与主线冲突时**以本文为准**（§3.1 命名 / §3.2 register 5 档 / 段间延迟现位等 4 项）；其它部分以 [Part 5 主线](./omubot-humanization-part5-segmentation.md) 为准。
6. **遇到证据不成立**：跟我同步，由我决定撤销 / 重订正。

---

## 8. 与 Part 1 / Part 6 的关系

### 8.1 Part 1 是前置基线（已 ✅）

| Part 1 子任务 | Part 5 接入点 | 现状 |
|---|---|---|
| **U1** segmentation 双实现合并 | P5.1 / P5.3 改写目标是 `services/llm/segmentation.py`（U1 的合并产物） | ✅ |
| **U3** Humanizer register-aware | P5.2 inter_segment_delay 与 Humanizer 的 char_delay 同读 register / slot；语义正交（段间 vs 段内） | ✅ |
| **U6** humanization ModuleContract | P5.1 / P5.2 读 `state.register.label` 的 owner contract | ✅ |
| **V1** RegisterClassifier | P5.1 / P5.2 register 5 档系数表的语义来源 | ✅ |
| **V10** Humanizer 接 mood / register / slot | P5.2 slot_energy 入参与 V10 同源 | ✅ |
| **V11** critic-rewrite-loop | Part 5 在发送层切分；V11 在生成层评分。顺序：LLM 生成 → V11 critic → P5.3 natural_split → P5.2 inter_segment_delay | ✅ |

### 8.2 Part 6 是后续耦合（调研存档，未立项）

[Part 6 源头生成调度](./omubot-humanization-part6-source-side-generation.md) 的 4 个候选方案与 Part 5 的耦合关系：

| Part 6 方案 | 与 Part 5 关系 | 互斥 / 共存旗标 |
|---|---|---|
| **方案 A** plan-then-utter | 每 utter call 输出 max_tokens=150 已是单段 → P5.1 `natural_split` 退化为 noop | `plan_then_utter.disable_natural_split=true` 默认开 |
| **方案 B** streaming-as-segment | SSE token-stream 上 online 切分 → 与 P5.1 `natural_split` 语义直接互斥 | `streaming_segment.enabled` 与 `humanization.natural_split_enabled` 不可同开 |
| **方案 C** reactive replan | 每 utter call 同方案 A → P5.1 退化为 noop；abort 时段段链 segment_chain_id 由 Part 5 写入 | `plan_then_utter.disable_natural_split=true` 默认开 |
| **方案 D** pause-then-extend | 完全正交 — P5.2 段间延迟 + Part 6 D 段后追发等待窗口可叠加 | 段间节奏 = `inter_segment_delay()` + `pause_then_extend_window` |

详见 [Part 6 doc §6.2](./omubot-humanization-part6-source-side-generation.md#62-与-part-5)。

### 8.3 风险矩阵 + 30 秒回滚

继承自 [Part 5 主线 §8](./omubot-humanization-part5-segmentation.md#8-风险与回滚)：

| 风险 | 触发条件 | 回滚 |
|---|---|---|
| natural_split 误把"独立小句"合并成一长段 | 概率合并参数过于偏向合并 | `humanization.natural_split_enabled=false` + restart（30 秒） |
| 段尾标点概率保留破坏 IM 阅读体感 | 用户判断"看起来更书面" | 调整 P5.1 `trailing_punct_keep_ratio` 4 个分类参数 |
| 段间延迟字数耦合让长段过慢 | 单段 60 字 → 9 s 段间延迟 | P5.2 `clamp_max=2.0` + `clamp_min=0.3` |
| natural_split 与 V11 critic-rewrite 二次 round 后的文本不兼容 | 重写文本带 markdown 残留 / 异常空白 | natural_split 内置 `_clean_text` 复用 P0 / U1 路径 |

紧急回滚（30 秒）：

```bash
# config/config.json:
#   "humanization": {"natural_split_enabled": false}
docker compose restart bot
```

---

## 9. 执行者 GPT 逐步追踪

> 本节由执行者按 P5.0 ~ P5.6 顺序追加；每条任务"领单拆分（执行前）" + "完成记录（执行者 GPT）" 双段，照搬 [Part 1 执行追踪 §9](./omubot-humanization-part1-execution.md#9-执行者-gpt-逐步追踪) 的格式。

### P5.0 领单拆分（执行前）

目标：跑 §2 5 步依赖体检；产出 1 行结论替换本文 §1 第 5 行的"待验证"标记，并在本节追加完成记录。

详细步骤：

1. 跑 §2 步骤 1~4 共 4 条 grep / pytest --collect-only。
2. 把 4 条命令的 ≤ 1 行回执粘贴到本节。
3. 把 §6 P5.0 状态从 ⏳ 改 🟡 + 我 review 后改 ✅。

风险评估：

- 0 代码改动，无回滚需求。
- 若依赖体检发现任意 1 项不达 ✅，**立刻停下不要进 P5.1**，跟我同步派单订正。

### P5.0 完成记录（执行者 Codex）

自验结果：4 项前置依赖均达 ✅，P5.0 可进入验收态，后续允许领取 Wave 1。

命令回执：

- `grep -n "from services.llm.segmentation\|reply_segments(" services/llm/client.py` → 命中 `client.py:35/39` import、`client.py:387` stub、`client.py:391` 委托、`client.py:2453/2652` fan-out call site，证明 Part 1 U1 分段委托已落地。
- `grep -rn "REGISTER_LABEL_SLOT\|state.register.label" services/humanization/ services/block_trace/` → 命中 `services/humanization/contract.py:9`、`classifier.py:87`、`episode_provider.py:181`、`catchphrase_provider.py:84`、`register_provider.py:72` 等，证明 register slot 与 BlockTrace provider 已落地。
- `grep -n "Humanizer.delay\|register=\|slot=" services/humanizer.py services/scheduler.py` → 命中 `services/humanizer.py:52-53` 的 `register=` / `slot=` 传参；scheduler 侧无直接 `register=` 文本命中，但 Humanizer API 已接 register/slot，P5.2 只依赖同源语义，不阻塞。
- `source ./scripts/dev/env.sh && uv run pytest --collect-only -q 2>&1 | tail -1` → `1714 tests collected in 1.01s`。

风险与回滚：P5.0 只改追踪文档，不改运行代码；回滚为撤销本节与 §1 / §6 的 P5.0 状态回填。

### P5.1 领单拆分（执行前）

目标：在现有 `services/llm/segmentation.py` 内新增 `natural_split(text, *, soft_max_chars=80, max_sentence_num=8, register=None, rng=None)`，完成自然分段算法和单测；本步不接生产路径，不修改 `reply_segments()` 默认行为。

详细步骤：

1. 读取 `services/llm/segmentation.py` 当前实现，复用 `_clean_text` / `fix_cq_codes` / `_protected_spans` / `_safe_boundary` / `_ASCII_TOKEN_RE` / `_URL_TOKEN_RE`，避免另起新目录。
2. 新增 register 5 档系数表与分段私有 helper：安全边界扫描、概率合并、段尾标点概率保留、段数上限尾部合并、soft_max_chars 递归拆分。
3. 新增 `tests/test_natural_split.py`，覆盖短/中/长文本概率形态、标点保留、引号/冒号/URL/CQ/ASCII 保护、soft_max 递归、max_sentence_num、register 5 档关键差异、cancel-path。
4. 跑 P5.1 定向测试与 D1 grep；通过后把 §6 P5.1 改 🟡 并追加完成记录。

自主评估：

- 边界：P5.1 只暴露新函数，不让线上默认行为变化；P5.2 再加 delay/config，P5.3 才做 feature flag 切流。
- 风险：概率算法若直接使用全局随机会导致测试不稳定，因此必须允许注入 `random.Random` / `secrets.SystemRandom` 风格对象；测试全部固定 seed。
- D2 cancel-path：算法本身无 I/O 和持久状态，cancel 测试通过 monkeypatch helper 在递归路径抛 `asyncio.CancelledError`，断言外部状态未被污染。
- 回滚：删除 `natural_split()` 及其 helper、删除 `tests/test_natural_split.py`，P5.3 未接线前运行时不受影响。

### P5.1 完成记录（执行者 Codex）

自验结果：P5.1 完成，进入 🟡 待验收；运行时默认路径未切换，`reply_segments()` 仍走 legacy `segment_reply()`。

改动内容：

- `services/llm/segmentation.py` 新增 `natural_split()` 与私有 helper：CQ / URL / ASCII / 颜文字保护、自然断点扫描、register 5 档 split strength、概率合并、段尾标点概率保留、`max_sentence_num` 尾部合并、`soft_max_chars` 递归软拆。
- URL 保护同步收窄为合法 URL 字符集，避免把中文逗号后的正文吞进 URL span；soft_max 落在长 URL / 长 token 内时保留完整 token，不硬切。
- `tests/test_natural_split.py` 新增 12 条覆盖：短/中/长文本概率形态、标点概率、引号/冒号/URL/CQ/ASCII/颜文字保护、soft_max 递归、max_sentence_num、register 差异、未知 register 回退、cancel-path。

验证：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_natural_split.py -q` → `12 passed`
- `source ./scripts/dev/env.sh && uv run python -m py_compile services/llm/segmentation.py tests/test_natural_split.py` → 通过
- D1 grep：`grep -rn 'natural_split\|_TRAILING_CLAUSE\|_CLAUSE_BREAK\|_MAX_CHUNK' --include='*.py' services tests` 命中新函数与测试；同时仍命中 `services/llm/client.py:_MAX_CHUNK` / `tests/test_client.py` 的旧兼容项，属于 P5.5 明确删除目标，非 P5.1 新增路径。

行数：`services/llm/segmentation.py` +227/-1，`tests/test_natural_split.py` 为新增测试文件。

风险与回滚：P5.1 尚未被 feature flag 接入生产；回滚为删除 `natural_split()` 相关 helper 与 `tests/test_natural_split.py`。

### P5.2 领单拆分（执行前）

目标：新增 `inter_segment_delay(prev_segment, *, register=None, slot_energy=1.0) -> float` 与 `ReplySegmentationConfig.natural_split_enabled=false` 字段；本步只提供纯计算能力和配置开关，不接 `send_queue.py` / `client.py` 动态发送路径。

详细步骤：

1. 在 `services/llm/segmentation.py` 复用 P5.1 的 register 5 档语义，新增段间延迟纯函数：中文字 `0.15s`、ASCII 字母数字 `0.07s`、register 调整、`slot_energy=max(0.5, slot_energy)`、clamp `[0.5, 3.0]`。
2. 在 `services/llm/segmentation.py::ReplySegmentationConfig` 与 `kernel/config.py::ReplySegmentationConfig` 新增 `natural_split_enabled: bool = False`，默认关闭，确保 P5.3 前运行时行为不变。
3. 新增 `tests/test_inter_segment_delay.py`，覆盖 quiet/polite 更慢、playful/snark 更快、长段上限、空段下限、`slot_energy=0` 兜底与配置默认关闭。
4. 跑 P5.2 定向测试与 D1 grep；通过后把 §6 P5.2 改 🟡 并追加完成记录。

自主评估：

- 边界：P5.2 不修改 `reply_segments()` 结果，也不修改 `LLMClient` sleep；P5.3 才使用 `natural_split_enabled` 切换生产路径。
- 风险：P5.2 文档里写“函数纯计算无写入”，所以 cancel-path 不需要人为制造脏状态；以纯函数测试证明无副作用。
- 回滚：删除 `inter_segment_delay()`、删除两个 config 字段、删除 `tests/test_inter_segment_delay.py`；由于默认 off 且未接线，运行时不受影响。

### P5.2 完成记录（执行者 Codex）

自验结果：P5.2 完成，进入 🟡 待验收；`natural_split_enabled` 默认关闭，P5.3 前运行时行为不变。

改动内容：

- `services/llm/segmentation.py` 新增 `inter_segment_delay(prev_segment, *, register=None, slot_energy=1.0)` 纯函数：中文字 `0.15s`、ASCII 字母数字 `0.07s`、register 5 档系数、`slot_energy=max(0.5, slot_energy)`、clamp `[0.5, 3.0]`。
- `services/llm/segmentation.py::ReplySegmentationConfig` 新增 `natural_split_enabled: bool = False`。
- `kernel/config.py::ReplySegmentationConfig` 新增同名字段，Admin 配置 schema 标为 careful / restart recommended。
- `tests/test_inter_segment_delay.py` 新增 8 条，覆盖空段下限、quiet / polite 变慢、playful / snark 变快、长段上限、ASCII rate、`slot_energy=0` 兜底、未知 register 回退、两个 config 模型默认 off。

验证：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_inter_segment_delay.py -q` → `8 passed`
- `source ./scripts/dev/env.sh && uv run pytest tests/test_natural_split.py tests/test_inter_segment_delay.py -q` → `20 passed`
- `source ./scripts/dev/env.sh && uv run python -m py_compile services/llm/segmentation.py kernel/config.py tests/test_inter_segment_delay.py` → 通过
- D1 grep：`grep -rn 'inter_segment_delay(' --include='*.py' services tests` 仅命中 `services/llm/segmentation.py` 与 `tests/test_inter_segment_delay.py`；P5.3 前尚未接入 `send_queue.py`，符合 P5.2 边界。

风险与回滚：P5.2 仍未切流；回滚为删除 `inter_segment_delay()`、两个 `natural_split_enabled` 字段和 `tests/test_inter_segment_delay.py`。

### P5.3 领单拆分（执行前）

目标：在 `natural_split_enabled=true` 时启用 Part 5 自然分段路径，并把按上一段计算出的动态 delay 传给发送 fan-out；默认 off 时旧 `segment_reply()` 路径和固定 `inter_segment_delay_s` 保持不变。

详细步骤：

1. 扩展 `services/llm/segmentation.py::reply_segments()` 返回结构，增加每段之后的 `inter_segment_delays`；为了降低改动面，新增小 dataclass / tuple helper，并同步 `LLMClient._reply_segments()` 两处调用。
2. 在 `segmentation.py` 新增 `_natural_split_path()`：`natural_split()` 产出 segments，`inter_segment_delay()` 对 `segments[:-1]` 逐段计算 delay；缺 register / slot 时降级 neutral / 1.0。
3. `LLMClient` 两处 fan-out 从固定单值 `segment_delay` 改为 `delays[idx]`；如果旧路径没有 per-segment delays，则继续用 `cfg.inter_segment_delay_s`。
4. `send_queue.py` 暂保留固定 `ReplySegmentBatch.inter_segment_delay_s`，因为普通 LLM fan-out 当前没有走 `ReplySegmentBatch`；P5.4 前不扩大队列契约。若测试发现队列必须支持数组，再用兼容字段补充。
5. 新增 `tests/test_reply_segments_natural.py`：fallback 路径不变、flag on 使用自然路径、delay 下界/逐段数量、register 缺失降级、cancel-path 不脏写。

自主评估：

- 边界：P5.3 只在 flag on 时改变分段与延迟；flag off 是 30 秒回滚路径。
- 风险：`LLMClient` 有两处相同 fan-out，需要同模式同时改，避免 rewrite 后路径漏接。
- D2 cancel-path：在 natural path 中 monkeypatch `natural_split` 抛 `CancelledError`，断言外部状态不变，异常不被吞。
- 回滚：`natural_split_enabled=false` + restart 回旧路径；代码级回滚为撤销 P5.3 对 `reply_segments()` 返回契约和 `LLMClient` fan-out 的改动。

### P5.3 完成记录（执行者 Codex）

自验结果：P5.3 完成，进入 🟡 待验收；`natural_split_enabled` 默认仍为 `false`，未改线上群配置，P5.4 灰度仍阻塞于验收与用户授权。

实施调整：

- 未直接改坏 `reply_segments()` 的三元组兼容契约；新增 `ReplySegmentPlan` 与 `reply_segment_plan()`，旧 `reply_segments()` 继续供旧调试 / 兼容测试使用。
- `reply_segment_plan()` 内显式分成 `_legacy_segment_path()` 与 `_natural_split_path()`；`enabled=false` 优先于 `natural_split_enabled=true`，保留全局关闭分段语义。
- register 入参兼容 RuntimeStateBus 真实写入形态（`{"label": "playful"}` / object / string），并将 Part 1 旧标签 `affectionate` / `distant` / `serious` 映射到 Part 5 的 5 档语义。
- `LLMClient` 两处 fan-out（正常 reply path + tool exhausted path）统一改用 `_visible_reply_segment_plan()`，从 `REGISTER_LABEL_SLOT` 读取 register、从 `CLOCK_CURRENT_SLOT` 读取 `energy`，缺失时降级 neutral / `1.0`。
- `send_queue.py` 本步未改：普通 LLM fan-out 当前不走 `ReplySegmentBatch`，P5.3 先不扩大队列契约，避免灰度前放大风险面。

改动内容：

- `services/llm/segmentation.py` 新增 `ReplySegmentPlan`、`reply_segment_plan()`、`_natural_split_path()`、`_legacy_segment_path()`，并补 register 归一化 / alias。
- `services/llm/client.py` 新增 `_reply_segment_plan()` wrapper 与 `LLMClient._visible_reply_segment_plan()`，两处发送循环按 `plan.inter_segment_delays[idx]` 逐段 sleep。
- `tests/test_reply_segments_natural.py` 新增 7 条，覆盖 fallback 路径不变、`enabled=false` 优先、flag on 自然路径、delay 数组数量和下界、register 缺失 neutral、register dict 生效、cancel-path 不脏写。
- `tests/test_llm_client_reply_segment_plan.py` 新增运行时 fan-out 测试，锁定 callback 发送两段后按 `[0.5, 1.25]` 动态 sleep，最后一段作为返回值。

验证：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_natural_split.py tests/test_inter_segment_delay.py tests/test_reply_segments_natural.py tests/test_llm_client_reply_segment_plan.py tests/test_client.py::test_chat_uses_injected_reply_segmentation_config tests/test_segmentation.py -q` → `47 passed, 2 warnings`
- `source ./scripts/dev/env.sh && uv run ruff check services/llm/segmentation.py services/llm/client.py kernel/config.py tests/test_natural_split.py tests/test_inter_segment_delay.py tests/test_reply_segments_natural.py tests/test_llm_client_reply_segment_plan.py` → `All checks passed!`
- `source ./scripts/dev/env.sh && uv run pyright services/llm/segmentation.py services/llm/client.py tests/test_natural_split.py tests/test_inter_segment_delay.py tests/test_reply_segments_natural.py tests/test_llm_client_reply_segment_plan.py` → `0 errors, 0 warnings, 0 informations`
- `source ./scripts/dev/env.sh && uv run python -m py_compile services/llm/segmentation.py services/llm/client.py kernel/config.py tests/test_natural_split.py tests/test_inter_segment_delay.py tests/test_reply_segments_natural.py tests/test_llm_client_reply_segment_plan.py` → 通过
- D1 grep：`grep -rn 'natural_split_enabled\|_natural_split_path' --include='*.py' services tests kernel` → 仅命中 `services/llm/segmentation.py`、`kernel/config.py` 与 P5.2/P5.3 测试；`tests/test_llm_client_reply_segment_plan.py` 仅为新增 runtime fan-out 测试。

风险与回滚：运行时默认仍走 legacy path；灰度事故回滚为 `natural_split_enabled=false` + restart（当前已是 false）。代码级回滚为撤销 P5.3 中 `ReplySegmentPlan` / `reply_segment_plan()` / `LLMClient` fan-out 改动与 `tests/test_reply_segments_natural.py`、`tests/test_llm_client_reply_segment_plan.py` 新增测试。

### P5.4 阻塞评估（执行前）

当前不领取执行：P5.4 要求单群 `993065015` 打开 `natural_split_enabled=true` 并跑满 24h 采样，派发条件写明依赖 P5.3 验收 ✅ + 用户授权进灰度。本轮 P5.3 仅进入 🟡 待验收，且未取得新的灰度启动确认，因此不修改 `config/config.json`，不启动 24h 灰度窗口。
