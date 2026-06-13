# Issue 17 Part 0 落地方案 — Climate 前置信号源（对齐当前代码重写）

> 状态：**执行方案（待编码）· 2026-06-06**
> 取代来源：[omubot-grayscale-issue17-part0-prerequisites.md](omubot-grayscale-issue17-part0-prerequisites.md)（2026-05-27，立项有效，但所有 `ClimateSignal`/`IrritationSensor`/`MessageSensor` 代码片段对着不存在的系统写）。
> 范围决策（用户 2026-06-06）：**做 P0-1 / P0-3 / P0-4，不含语音（P0-2）。** 信号改接现有 `MoodEngine`，不新建 Climate。

## 0. 核实结论（编码前的代码现实）

| 文档假设 | 当前现实（已 grep 核实） |
| --- | --- |
| `services/dialogue_climate/` Climate 系统 | **不存在**。`ClimateSignal`/`IrritationSensor`/`MessageSensor` 全仓 0 命中 |
| `ClimateSignal(dimension=...)` 接法 | 作废。真实信号汇是 [MoodEngine](../../plugins/schedule/mood.py)，valence/openness/tension 三维真实存在 |
| 信号注入要新建机制 | **不用**。`MoodEngine` 已有 `_recognition_nudges` + `_active_nudge`（30min 线性衰减 + cap 0.2），`evaluate()` 已应用 nudge（[mood.py:154-158](../../plugins/schedule/mood.py#L154)） |
| 信号源缺失 | 信号源**全在**：[qq_interactions.py](../../services/humanization/qq_interactions.py) 的 `QQInteractionSignal` 已带 `emoji_code`/`is_tome`/`kind`，poke 有速率限制，reaction 已解析到 `dispatch_qq_interaction_signal` |

**核心判断**：part0 真正缺的只有「emoji_code 数字 → 情感极性」语义层，和把极性/poke 接进**已存在**的 mood nudge。比文档设想的简单。

## 1. 改动清单（D3 四列：旧 → 新 / 文件 / 类型 / 回归）

| # | 旧 | 新 | 文件 | 类型 | 回归 |
| --- | --- | --- | --- | --- | --- |
| P0-1 | reaction 只有 emoji_code 数字，无语义 | `EMOJI_SENTIMENT` 表 + `classify_reaction_sentiment()` | `services/humanization/emoji_sentiment.py`（新，~45 行） | 新增模块 | 已知码→极性/强度、未知码→弱正面默认 单测 |
| E-ext | `_recognition_nudges` 三元组 `(ts, valence_d, openness_d)`，无 tension | 扩展为 `(ts, valence_d, openness_d, tension_d)`，`_active_nudge` 返回三维并 cap；`evaluate` 应用 tension | `plugins/schedule/mood.py`、`_active_nudge` | 改内部结构 | 向后兼容（旧 3 元组路径）+ tension 衰减/cap 单测 |
| reg-ext | `register_recognition_signal(relation,...)` 仅 self/friend | 新增 `register_interaction_signal(*, valence_d, openness_d, tension_d, group_id, session_id)` 通用入口；旧方法内部转调 | `plugins/schedule/mood.py:180` | 新增方法 | 累加/衰减/cap/三维 单测 |
| P0-3 | reaction 命中只投 scheduler trigger | dispatch 时若 `is_tome` 且有极性 → 经 `ctx.mood_engine.register_interaction_signal` 注入 valence±/tension+ | `services/humanization/qq_interactions.py:185` 附近 | 接线 | 正面→valence+、负面→valence-/tension+、mood_engine=None 安全 单测 |
| P0-4 | poke 速率仅用于静默判定 | poke 命中（含高频接近阈值）→ 注入 tension+ 小 nudge | `services/humanization/qq_interactions.py:169` 附近 | 接线 | poke→tension+、静默路径不注入 单测 |

## 2. 信号映射（接真实 MoodProfile 维度，非 Climate）

| 信号 | 维度 nudge | 强度 | 依据 |
| --- | --- | --- | --- |
| reaction 正面（👍❤️😂…） | valence + | `intensity * 0.10` | 用户对 bot 消息正反馈 |
| reaction 负面（翻白眼😡…） | valence − / tension + | `intensity * 0.12` / `intensity * 0.06` | 负反馈，轻微紧张 |
| reaction 未知码 | valence + | `0.2 * 0.05` | 主动 react 本身是参与信号 |
| poke 被戳 | tension + | `0.04` | attention 信号；高频已有速率限制兜底 |

所有 nudge 复用现有 `_nudge_cap=0.2` 上限 + 30min 线性衰减，**不会主导 base mood**。session_id 约定沿用 `group_{gid}`（[router.py:82](../../kernel/router.py#L82)）。

## 3. D1 同模式 / D2 cancel-path / D4 证据

- **D1**：emoji_sentiment 表照搬 [kernel/qq_face.py](../../kernel/qq_face.py) 的 ID→中文 dict 模式；nudge 注入照搬 issue17 E1 `register_recognition_signal` 已落地模式（同一 `_recognition_nudges` 基础设施）。
- **D2**：nudge 是纯内存同步操作，无 await/无 wait_for，不涉及 cancel-path；dispatch 路径已是同步 notify。无新增协程，D2 不适用（在日志注明）。
- **D4 证据**：① 同模式扫描 = 本节 D1；② 外部可观察 = 单测断言 `mood_engine.evaluate()` 后 profile.valence/tension 变化 + cap 生效；③ 回滚 = 见 §4。

## 4. 回滚 / 风险

- 三处接线均为**附加**，不改 reaction/poke 既有 trigger 投递行为。`ctx.mood_engine is None` 时全部 no-op（try/except 包裹）。
- nudge cap 0.2 + 30min 衰减保证「信号不主导」；负面 reaction 不会把 bot 直接推成烦躁。
- 回滚：`git restore` 三文件 + 删 `emoji_sentiment.py`；无 DB、无运行态资源、不碰 NapCat、不碰 sidecar。

## 5. P0-2（语音 ptt2text）单独挂起 — 不在本批

**硬阻塞**：`ptt2text` 需 NapCat ≥ V4.18.2，生产实跑 **v4.15.0**（`docker ps` 确认）。升级镜像 = recreate = 撞 CLAUDE.md **D6 红线**（设备指纹→反欺诈）。P0-2 留待单独决策（升级授权 + 指纹备份预案，或探测 v4.15 是否已带该 API）。

## 6. 落地顺序 & 验证

1. P0-1 `emoji_sentiment.py` + 单测（最小、无依赖、可独立测）。
2. mood.py 扩展 nudge 三维 + `register_interaction_signal` + 单测（向后兼容旧三元组）。
3. P0-3/P0-4 dispatch 接线 + 单测。
4. 全量 `uv run pytest`（先 `pkill -9 -f pytest`，D5）+ `ruff check` + `pyright`。
5. 维护日志 append 一条；ACTIVE.md / 本 tracker 状态更新（Codex 交接）。

零运行态变更（纯代码 + flag 行为不变），不需 docker rebuild 即可验证逻辑；上线随常规 bot rebuild。
