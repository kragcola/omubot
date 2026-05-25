# Omubot 拟人 Part 2/3 — 派单版并列执行追踪

> 状态：2026-05-25 立。本文是 [Part 2/3 调研 v2](./omubot-humanization-part2-3-research.md) 的执行版派单表。
>
> 用途：由别的执行者按 wave 顺序领单完成；我（Claude）做最终验收。
>
> 工作流：每条任务有「领单 → 自验 → 提交申请验收」三态。验收通过我会把 §6 状态表的 ⏳→✅。
>
> **执行原则**（以下规则覆盖任何调研文档的不一致表述）：
>
> 1. **每条独立 commit**——除非本文明确写"合 commit"。调研 §6.1 / §6.2 / §6.3 / §6.4 的子任务编号本身就是派单单位，不再细拆。
> 2. **同 wave 内任务可并行**——不同 wave 间严格串行。
> 3. **每条任务自带 D1 grep 证据 / D2 cancel-path 测试 / 30 秒 feature flag 回滚**，缺一不通过验收。
> 4. **遇调研证据与本文冲突，以本文为准**（§1 已记录调研 7 处证据订正）。
> 5. **24h 灰度窗口内只动 docs/，不动 .py / .json / .toml / 镜像 / 容器**——直到 [Part 5 P5.4 灰度](./omubot-humanization-part5-execution.md) 验收 ✅。

---

## 1. 调研自审与证据订正（执行前必读）

下表是我对 Part 2/3 调研 §6.1 / §6.3 / §6.4 / §5.6 进行 grep 实证后发现的与原文不符的项。**派单时按本表订正，不按调研原文写**。

| 调研位置 | 调研原文 | grep 实证 | 派单订正 |
|---|---|---|---|
| §6.1 P2.3 | "client.py:_SEGMENT_DELAY 替换为函数 ≤ 25 行；与 Part 5 共享" | [services/llm/segmentation.py:inter_segment_delay()](../../services/llm/segmentation.py) 已由 [Part 5 P5.2 ✅](./omubot-humanization-part5-execution.md#6) 实现并落 [services/llm/client.py:2531-2535,2737-2741](../../services/llm/client.py#L2531-L2535) 两处 fan-out + [services/send_queue.py:32,322-323](../../services/send_queue.py#L32) | **P2.3 撤销**——已由 Part 5 P5.2 全量实现；位置标记 `❌ 证据不成立` |
| §6.4 P3.6 | "新增 services/humanization/mood_classifier.py ≤ 180 行" | [services/humanization/contract.py:9-15](../../services/humanization/contract.py#L9-L15) 现有 7 个 slot；mood / affection_stage 都没注册；mood 信号源散布 register classifier / sticker plugin / timeline | **P3.6 派单细化**：①[services/humanization/contract.py](../../services/humanization/contract.py) 加 `MOOD_CURRENT_SLOT="humanization.mood.current"` + slot 注册 ≤ 8 行；②新建 `services/humanization/mood_classifier.py` ≤ 180 行 |
| §6.4 P3.7 | "持久化到 admin map 扩展字段" | grep `admin_map` 在 `services/persona/` / `admin/` / `kernel/` 0 命中；persona admin 走 [services/persona/compiler.py:235](../../services/persona/compiler.py#L235) `_admins_line` 渲染 source.md frontmatter（一次性导入），无运行时滚动写入通道 | **P3.7 派单订正**：affection_stage 不进 persona admin map（语义错位）；新建独立 sqlite `storage/affection_stage.db` 与 episodic / learning_normalizer 同级；落点在 `services/persona/affection_classifier.py` ≤ 150 行（含 store） |
| §5.6.1 / §6.3 P2.8 | "thinker.sticker bool 移除自决" | [services/llm/thinker.py](../../services/llm/thinker.py) 当前 `ParsedDecision` 含 sticker 字段；[kernel/config.py:1060](../../kernel/config.py#L1060) `thinker_provider` feature flag 默认 off | **P2.8 派单订正**：不删 thinker.sticker 字段；sticker_decision_provider.decide() 入口将 thinker.sticker 视作 hint（参与 candidate pool 评分），但 should_send 由 sticker_decision_provider 单点出 |
| §6.3 P2.9 | "services/llm/client.py:2485-2506 改 ≤ 35 行" | [services/llm/client.py:2481-2506](../../services/llm/client.py#L2481-L2506) `_text_has_kaomoji` 检测 + 强制轮 emit "请现在发送一个表情包"；同模式 [client.py:2486](../../services/llm/client.py#L2486) `_sticker_sent` re-entry 锁 | **P2.9 派单订正**：不动 `_text_has_kaomoji` 函数体；仅在 `if (_text_has_kaomoji(reply) and not _sticker_sent ...)` 条件前加 `register=="playful" and mood in {"playful", "high"}` gate；feature flag `humanization.kaomoji_enforce_strict=false` 退回 v1 |
| §6.3 P2.11 | "services/url_meta/og_title.py" | services/ 下没有 url_meta 目录 | **P2.11 派单订正**：新建 `services/url_meta/__init__.py` + `services/url_meta/og_title.py` ≤ 130 行；`services/url_meta/blacklist.py` ≤ 30 行（admin / banking / private domain 名单） |
| §6.3 P2.10 | "替换 DEFAULT_STICKER_USAGE_HINT" | grep 命中 [services/media/sticker_capture.py:7](../../services/media/sticker_capture.py#L7) `DEFAULT_STICKER_USAGE_HINT="群友常用表情..."`；[plugins/sticker/plugin.py:19,192,330](../../plugins/sticker/plugin.py#L19) + [plugins/history_loader/plugin.py:21,217](../../plugins/history_loader/plugin.py#L21) 4 处引用 | **P2.10 派单订正**：保留 `DEFAULT_STICKER_USAGE_HINT` 作为冷启动 fallback；新增 `services/media/sticker_capture.py:emit_emotion_tag()` ≤ 90 行（vision LLM 单次调用 → 写 sticker_store.usage_hint）；新建离线脚本 `scripts/dev/sticker_recaption.py` 重跑全库 |

> **派单规则**：执行者拿到本文档后，**§1 这 7 项订正一律落本文版本**，不要按调研原文写。

**P0 体检结论（2026-05-25 / Codex）**：核心依赖达 ✅（segmentation / client fan-out / humanization slots / pytest 基线均已实证）；但有两处派单基线需按实况订正：`humanization.runtime_groups=["993065015"]`，双群锚点目前在 `persona_v2.runtime_groups=["993065015","984198159"]`；`services/sticker/` 目录当前不存在，P2.8 需新建该目录与 `decision_provider.py`。2026-05-25 用户授权忽略 24h 窗口限制，P0 与 Part 5 P5.4 已代验收 ✅，可进入 Wave 1 领单。

---

## 2. P0 前置体检（依赖 + 字段实证）

派单第 0 步，零代码改动。Part 2/3 全 wave 阻塞于以下 4 项依赖为 ✅：

| 步骤 | 命令 | 预期结果 |
|---|---|---|
| 1 | `grep -rn "natural_split_enabled\|reply_segment_plan\|inter_segment_delay" services/llm/segmentation.py services/llm/client.py services/send_queue.py` | 命中 Part 5 P5.2 / P5.3 已落地的 segmentation 函数 + client.py 两处 fan-out + send_queue.py inter_segment_delay_s 字段 |
| 2 | `grep -rn "REGISTER_LABEL_SLOT\|AFFECTION_FAMILIARITY_SLOT\|CLOCK_CURRENT_SLOT" services/humanization/contract.py services/block_trace/` | 命中 contract.py 7 个 slot + block_trace provider 5 处读取，证明 [Part 1 U6 + V1 + V5 + V14 ✅](./omubot-humanization-part1-execution.md#6) 已落 |
| 3 | `grep -n "humanization\|register_classifier\|context_providers\|runtime_groups" kernel/config.py config/config.json` | `HumanizationConfig` 7 字段存在；实况为 `humanization.runtime_groups=["993065015"]`，双群锚点在 `persona_v2.runtime_groups=["993065015","984198159"]` |
| 4 | `uv run pytest --collect-only -q 2>&1 \| tail -1` | 当前基线 ≥ 1734 tests collected（Part 5 P5.3 出口）；Part 2/3 全量出口要求 ≥ 1734 + 137 = ≥ 1871 |
| 5 | `git status -uno` | working tree clean（避免 dirty 文件混入派单 commit） |
| 6 | `ls services/humanization/ services/persona/ plugins/sticker/ services/media/` | 4 个现有目标目录存在；mood_classifier.py / affection_classifier.py 当前**不存在**；`services/sticker/` 目录当前也不存在，P2.8 需新建该目录与 decision_provider.py |
| 7 | 写 1 行结论到本文 §1 第 8 行（替换"待验证"） | 给 P2.x / P3.x 派单确定基线 |

**P0 不是 commit；是派单前置体检**。我会先看本步骤回执再发后续单。**任意 1 项依赖未达 ✅，Part 2/3 wave 整体阻塞**。

**P0 前置依赖检查表**：

| 依赖 | 来源 Part | 状态 |
|---|---|---|
| segmentation 双实现合并 | Part 1 U1 | ✅ |
| RuntimeStateBus + humanization contract | Part 1 U6 | ✅ |
| RegisterClassifier | Part 1 V1 | ✅ |
| AffectionFamiliarity slot | Part 1 V5 | ✅ |
| StylometricScorer | Part 1 V8 | ✅（v2 不动） |
| critic-rewrite-loop | Part 1 V11 | ✅ |
| Humanizer register/mood/slot | Part 1 U3 / V10 | ✅ |
| HumanizationConfig 灰度旗标 | Part 1 V0 / V12 | ✅ |
| natural_split + inter_segment_delay | Part 5 P5.1 / P5.2 / P5.3 / P5.4 | ✅（P5.4 用户授权代验收通过） |
| persona_v2 灰度 | Persona B3 | ✅ |

---

## 3. 并列执行 Wave 表（按依赖图编排）

**依赖关系核心规则**：

- **Wave 0**：P0 前置体检（前置，零代码）
- **Wave 1**：信号源 + 度量基线（5 条互不依赖，可并行）
- **Wave 2**：检测器 + 分类器（4 条，部分依赖 Wave 1）
- **Wave 3**：决策器 + 收敛层（5 条，依赖 Wave 1+2）
- **Wave 4**：Sticker 收尾 + URL/Modality（4 条，依赖 P2.8）
- **Wave 5**：联动层 + Long-tail（3 条，依赖大部分）
- **Wave 6**：v1 + v2 合并灰度（24h 体感比对）
- **Wave 7**：文档收口

### 3.1 Wave 1 — 信号源 + 度量基线（5 条并列）

| 编号 | 一句话 | 改动文件（≤ N 行） | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **P2.1** | 节奏度量基线脚本：采样 200 条 group reply 的回复延迟 / 段间间隔 / 段数分布 | `scripts/dev/measure_rhythm.sh`（new ≤ 60 行）+ 只读 `storage/block_trace.db` / `storage/group_messages.db` | grep `measure_rhythm\|rhythm_baseline` 仅命中新脚本与 README 引用 | 纯只读 SQLite，无 cancel-path | `rm scripts/dev/measure_rhythm.sh` |
| **P3.1** | addressee detector 4 层 cascade（adapter / regex / quote / @）；输出 `(target_id, confidence)` | `services/group/__init__.py` + `services/group/addressee.py`（new ≤ 150 行）；不修改现有 `services/scheduler.py` `at_only` 判定 | grep `class AddresseeDetector\|addressee_detector` 仅命中新模块 + tests | 不写 RuntimeStateBus；测试用 `pytest.raises(asyncio.CancelledError)` 锁 4 层 cascade 中途 cancel 不污染 detector 状态 | feature flag `humanization.addressee_detector_enabled=false` |
| **P3.6** | mood RuntimeStateBus slot：① `services/humanization/contract.py` 加 `MOOD_CURRENT_SLOT="humanization.mood.current"` + slot 注册（per_session, ttl=300s）≤ 8 行；② 新建 `services/humanization/mood_classifier.py` ≤ 180 行（FiSMiness 5 态 FSM；信号源 = 用户回复延迟 / 短回复占比 / sticker 调用密度 / 语气词命中率） | `services/humanization/contract.py`（+8）+ `services/humanization/__init__.py`（+1 export）+ `services/humanization/mood_classifier.py`（new ≤ 180 行） | grep `MOOD_CURRENT_SLOT\|MoodClassifier` 仅命中 contract.py + mood_classifier.py + tests | 测试 cancel during FSM transition；锁定 mood slot 不被脏写 | feature flag `humanization.mood_slot_enabled=false` |
| **P3.4** | willingness 5-stage 分类（v1 部分）：基于近期回复延迟 + register 一致性输出 stranger/acquaint/familiar/close/withdraw；不存数值 | `services/persona/willingness.py`（new ≤ 80 行） | grep `class Willingness\|willingness_stage` 仅命中 new 模块 + tests | 纯计算，无写入；测试 5 档边界 | 删除 `services/persona/willingness.py` + `tests/test_willingness.py` |
| **P2.4** | typing 字符系数扩展：emoji 1s 起步价 + thinking 10s 兜底 | `services/humanizer.py`（≤ +30 行；扩 `delay()` 内字符权重表） | grep `EMOJI_BASE_DELAY\|THINKING_FALLBACK\|emoji_base_s` 仅命中 humanizer.py + tests | 已有 register/mood 入参兼容 | git restore services/humanizer.py |

**Wave 1 commit 顺序**：5 条独立，**5 个 commit**；建议顺序 P2.1 → P3.1 → P3.6 → P3.4 → P2.4（前 3 条互不依赖，后 2 条独立）。

### 3.2 Wave 2 — 检测器 + 分类器（4 条，部分依赖）

| 编号 | 一句话 | 关键文件（≤ N 行） | 依赖 | 测试 |
|---|---|---|---|---|
| **P3.2** | topic drift detector（in-line）：读 last 3 messages，输出 `(topic, drift_score)`；用 embedding cosine（复用 `services/similarity.py`）替代 difflib | `services/group/topic_drift.py`（new ≤ 120 行） | P3.1 | tests/test_topic_drift.py +6 |
| **P3.7** | affection 5-档分类器：① 新建 `storage/affection_stage.db`（与 episodic / learning_normalizer 同级）；② 新建 `services/persona/affection_classifier.py` ≤ 150 行（含 store + 5-档分类逻辑 + 24h 滚动）；③ 不入 persona admin map（参 §1 订正 #3）；④ `services/humanization/contract.py` 加 `AFFECTION_STAGE_SLOT="humanization.affection.stage"` per_user, ttl=86400s | `services/persona/affection_classifier.py`（new ≤ 150 行）+ `services/humanization/contract.py`（+5 行 slot） | P3.6（共享 RuntimeStateBus 写路径） + persona_v2 ✅ | tests/test_affection_classifier.py +10（含冷启动 stranger / 退回 acquaint / 5 档边界） |
| **P2.2** | LLM planner binary（reasoning-first）：reasoning + decision 两段输出；read register / context；与 V11 critic 共用 LLMRequest 框架 | `services/reply_planner/binary_planner.py`（new ≤ 180 行）+ `services/reply_planner/__init__.py` | Part 1 V11 ✅ | tests/test_binary_planner.py +12 |
| **P2.6** | consecutive_no_reply 阶梯阈值：3/5 升级 threshold | binary_planner 内置 counter ≤ 20 行 | P2.2 | tests/test_no_reply_threshold.py +3 |

**Wave 2 commit 顺序**：4 条独立 commit；P3.2 → P3.7 → P2.2 → P2.6（P3.2 / P3.7 / P2.2 互不依赖可并行；P2.6 依赖 P2.2 必后置）。

### 3.3 Wave 3 — 决策器 + 收敛层（5 条，依赖 Wave 1+2）

| 编号 | 一句话 | 关键文件（≤ N 行） | 依赖 | 测试 |
|---|---|---|---|---|
| **P2.8** | sticker_decision_provider 单决策点（v2 v2 优先级链头）：4 触发源统一进入；输出 `StickerDecision(should_send, candidate_pool, rerank_strategy, cooldown_ms)` | `services/sticker/__init__.py` + `services/sticker/decision_provider.py`（new ≤ 220 行） | P3.6 + P3.7 + Part 1 V12 ✅ | tests/test_sticker_decision_provider.py +14（含 4 触发源互斥 / mood gate / 冷启动 / cancel-path） |
| **P3.8** | mood → typing + inter_segment_delay 渗透：① `services/humanizer.py` 加 5 档 mood 系数表 ≤ 20 行（cold ×1.3 / playful ×0.8）；② `services/llm/segmentation.py:inter_segment_delay` 加 mood 系数 ≤ 20 行（cold ×1.5 / playful ×0.7） | services/humanizer.py + services/llm/segmentation.py（合计 ≤ 40 行） | P3.6 + Part 5 P5.2 ✅ | tests/test_humanizer_mood.py +6 |
| **P3.9** | mood / affection → binary_planner / addressee gate：mood=cold + addressee≠self → no_reply；affection=stranger → register 偏 neutral | binary_planner.py + addressee.py（合计 ≤ 50 行） | P3.6 + P3.7 + P2.2 + P3.1 | tests/test_planner_addressee_mood.py +8 |
| **P2.5** | force_reply 兜底收紧（is_at + addressee=self 双条件） | `plugins/chat/plugin.py` group_listener ≤ 5 行改 | P3.1 | tests/test_force_reply.py +3 |
| **P3.3** | read_mark prompt 注入：在 PromptBuilder 第 2 块（group context）加 read_mark marker | `services/llm/prompt_builder.py`（≤ +15 行） | P3.2 | tests/test_prompt_read_mark.py +2 |

**Wave 3 commit 顺序**：5 条独立 commit；P2.8 头单（v2 v2 优先级链头）；P3.8 / P3.9 并列；P2.5 / P3.3 末单。

### 3.4 Wave 4 — Sticker 收尾 + URL/Modality（4 条，依赖 P2.8）

| 编号 | 一句话 | 关键文件（≤ N 行） | 依赖 | 测试 |
|---|---|---|---|---|
| **P2.9** | kaomoji_enforce 拆解：仅 register=playful + mood ∈ {playful, high} 时启用 | services/llm/client.py:2481-2506 改 ≤ 35 行 | P2.8 | tests/test_kaomoji_enforce.py +6（含两个 gate 条件 / feature flag 退回） |
| **P2.10** | sticker library emotion tag 重标注：保留 DEFAULT_STICKER_USAGE_HINT 作 fallback；新增 `emit_emotion_tag()` 写 sticker_store.usage_hint | `services/media/sticker_capture.py`（≤ +50 行） + `scripts/dev/sticker_recaption.py`（new ≤ 90 行） | P2.8 | tests/test_sticker_capture_emotion.py +5 |
| **P2.14** | sticker_id 调用密度反馈 → mood slot：sticker_decision_provider 写回 RuntimeStateBus density signal；mood_classifier 消费 | sticker_decision_provider.py（≤ +25 行）+ mood_classifier.py 消费点 | P2.8 + P3.6 | tests/test_sticker_density_feedback.py +3（含自反馈环上限 0.3） |
| **P2.11** | og:title 注入：① 新建 `services/url_meta/__init__.py` + `og_title.py` ≤ 130 行；② `blacklist.py` ≤ 30 行；③ 24h LRU；④ 500ms timeout；⑤ PromptBuilder group context 块加注入点 ≤ 10 行 | `services/url_meta/og_title.py`（new ≤ 130 行）+ `services/url_meta/blacklist.py`（new ≤ 30 行）+ `services/llm/prompt_builder.py`（≤ +10 行） | P2.8 后即可 | tests/test_og_title.py +8（含黑白名单 / timeout / fetch 失败静默） |

**Wave 4 commit 顺序**：4 条独立 commit；P2.9 → P2.10 → P2.14 → P2.11。

### 3.5 Wave 5 — 联动层 + Long-tail（3 条）

| 编号 | 一句话 | 关键文件（≤ N 行） | 依赖 | 测试 |
|---|---|---|---|---|
| **P3.10** | mood × addressee × topic 联动表落地：§4.5 联动表 9 行 trigger combinations 的 lookup 实现 | `services/humanization/coupling.py`（new ≤ 80 行） | P3.6 + P3.7 + P3.8 + P3.9 | tests/test_mood_coupling.py +6（含 9 联动条目 + 仲裁优先级 affection > mood > register） |
| **P2.12** | sticker FairMatch rerank（U-Sticker long-tail）：调用频次直方图 → 过度集中 id 降权 0.5 | `services/sticker/fairmatch.py`（new ≤ 60 行） | P2.8 + P2.10 | tests/test_fairmatch.py +4 |
| **P2.13** | bilibili / youtube 视频元信息 adapter（专用 adapter；可选启用） | `services/url_meta/video_adapter.py`（new ≤ 110 行） | P2.11 | tests/test_video_adapter.py +5 |

**Wave 5 commit 顺序**：3 条独立 commit；P3.10 → P2.12 → P2.13。

### 3.6 Wave 6 — v1 + v2 合并灰度（24h 体感比对）

| 编号 | 一句话 | 改动 | 出口指标（24h）| 依赖 |
|---|---|---|---|---|
| **P2.7+P3.5+v2-灰度** | 单群 `993065015` 启 [humanization] 全部新旗标（保留 `984198159` 作对照群仅开 v1 部分）；跑 24h；`scripts/dev/measure_humanization.sh` + `scripts/dev/measure_rhythm.sh` 联合采样 200 条 group reply | `config/config.json`（启 11 个 v2 旗标）+ `scripts/dev/measure_humanization.sh` 扩展（≈ +40 行） | 见 §4 出口表 | Wave 5 ✅ + 用户确认进入灰度 |

> 灰度群锚定 [Part 1 §0a 灰度授权](./omubot-humanization-part1-language-feel.md)：993065015 + 984198159；本期单群 A/B（对照群仅开 v1）。

### 3.7 Wave 7 — 文档收口

| 编号 | 一句话 | 改动 |
|---|---|---|
| **P2/3-DOC** | maintenance-log 当日条目 + 调研 §10 当前状态全 ✅ + 本文 §6 状态表全 ✅ + Part 1 / Part 5 主线状态表追加"Part 2/3 已落地"行 + `docs/migrations/persona-v2-importer.md §12` 加 Part 2/3 行 | 文档 |

---

## 4. 灰度 24h 出口指标矩阵

执行者灰度结束跑一次合并采样，把下表填进结果。我看到 ≥ 12/16 项达标才放到 P2/3-DOC 文档收口。

### 4.1 v1 部分（节奏 / addressee / topic）

| 指标 | v1 baseline | 目标 | 灰度群（993065015） | 对照群（984198159） |
|---|---|---|---|---|
| 回复延迟分布标准差 | ≈ 0.8s | ≥ 1.5s | 等待 24h 样本 | 不变即通过 |
| typing 时长 vs 字数 Pearson r | < 0.3 | ≥ 0.7 | 等待 24h 样本 | 不变即通过 |
| binary_planner balanced accuracy | — | ≥ 60% | 等待 24h 样本 | N/A |
| addressee 准确率 | ≈ 70%（at_only） | ≥ 90% | 等待 24h 样本 | 不变即通过 |
| topic_drift 检出率 | — | ≥ 80% | 等待 24h 样本 | N/A |
| read_mark 注入"补回老消息"占比 | — | ≥ 15% | 等待 24h 样本 | N/A |

### 4.2 v2 部分（sticker / modality / video-url / mood / affection）

| 指标 | v1 baseline | 目标 | 灰度群（993065015） | 对照群（984198159） |
|---|---|---|---|---|
| sticker 单回复出现率 | ≈ 60%+ | ≤ 25% | 等待 24h 样本 | 不变即通过 |
| sticker_id 分布熵 | ≈ 0.8 bits（2 ids 占 73%） | ≥ 3.0 bits | 等待 24h 样本 | 不变即通过 |
| eWe-bench 错误（emotion mismatch + overuse） | ≈ 50% 异常 case | ≤ 5% | 等待 24h 样本 | N/A |
| kaomoji_enforce 触发占比 | ≈ 50% 异常 case | ≤ 10% | 等待 24h 样本 | 不变即通过 |
| og:title 注入命中率（白名单内 URL） | 0% | ≥ 60% | 等待 24h 样本 | 0%（不变即通过） |
| MOOD_CURRENT_SLOT 写入命中率 | 0% | ≥ 95% | 等待 24h 样本 | 0%（不变即通过） |
| mood=cold/tired 时 sticker_probability | — | ≤ 0.1 | 等待 24h 样本 | N/A |
| mood=playful vs tired 回复延迟 ratio | — | ≤ 0.7 | 等待 24h 样本 | N/A |
| affection stranger+acquaint 占比 | — | ≤ 60% | 等待 24h 样本 | N/A |
| mood / affection slot 不在 prompt 字符串 | — | grep `identity.md \| 提示词` 不命中 mood / affection | grep 校验 | 同左 |

### 4.3 用户主观验收

| 项 | 期望反馈 |
|---|---|
| v1 节奏 | 「节奏不再机器」「短消息更快」「长消息有等待感」 |
| v1 addressee | 「群里 @ 别人时不抢话」「话题切换时反应自然」 |
| v2 sticker | 「不再乱发 sticker」「sticker 跟话题对得上」 |
| v2 mood/affection | 「累的时候 bot 自己也短了」「熟悉的人发的话 bot 更主动」 |

> 出口判定规则：≥ 12/16 项 + 用户主观验收 = 进入 P2/3-DOC；< 12/16 项 = 留 24h 再观察一轮，仍不达标则按 §8 风险矩阵回滚部分 / 全部旗标。

---

## 5. 验收清单（每条任务交付时勾）

执行者每条 commit 后填 PR / 提交说明附上：

```
- [ ] 改动行数与计划匹配（声明：实际 +X / -Y）
- [ ] D1 grep 命中仅在预期路径
- [ ] D2 cancel-path 测试落实（pytest.raises(CancelledError) 锁脏写）
- [ ] uv run pytest -q 全绿（含本任务新测试）；当前累计 ≥ 1734 + 已交付的新测试数
- [ ] uv run ruff check 改动范围 clean
- [ ] uv run pyright 改动范围 0 errors
- [ ] feature flag 30 秒回滚演练成功（命令：sed -i '' 's/"<flag_name>": true/"<flag_name>": false/' config/config.json && docker compose restart bot）
- [ ] 同 wave 其它任务无冲突（git rebase / merge clean）
- [ ] 所改文件不包含 mood / affection 文字进 identity.md / prompt 字符串（grep `identity.md` 自检）
```

---

## 6. 当前状态（执行者每完成一条把 ⏳ 改 🟡 等验收，验收后我改 ✅）

| 编号 | wave | 状态 | 落地证据 / 备注 |
|---|---|---|---|
| **P0** | 0 | ✅ | 用户授权代验收通过；核心依赖达标，`1742 tests collected`；`humanization.runtime_groups` 单群 / `services/sticker/` 未建目录为基线订正，不阻塞 Wave 1 |
| **P2.1** | 1 | ✅ | 自主验收通过：`scripts/dev/measure_rhythm.sh`（59 行，只读 SQLite）；dry-run 输出 200 reply 节奏基线 |
| **P3.1** | 1 | ✅ | 自主验收通过：`services/group/addressee.py`（126 行）+ 7 条测试；4 层 cascade 与 cancel-path 验证通过 |
| **P3.6** | 1 | ✅ | 自主验收通过：`MOOD_CURRENT_SLOT` + `MoodClassifier`（122 行）+ 6 条 mood 测试；cancel-path 不脏写 |
| **P3.4** | 1 | ✅ | 自主验收通过：`services/persona/willingness.py`（53 行）+ 6 条边界测试；纯计算无写入 |
| **P2.4** | 1 | ✅ | 自主验收通过：Humanizer emoji 起步价 + thinking fallback；`services/humanizer.py` +29/-1，12 条相关测试通过 |
| **P3.2** | 2 | ✅ | 自主验收通过：`services/group/topic_drift.py`（119 行）+ 6 条测试；last 3 user messages drift 检测与 provider fallback 验证通过 |
| **P3.7** | 2 | ✅ | 自主验收通过：`AFFECTION_STAGE_SLOT` + `services/persona/affection_classifier.py`（146 行）+ 10 条测试；独立 sqlite store 与 24h rolling 验证通过 |
| **P2.2** | 2 | ⏳ | 待执行：LLM planner binary（reasoning-first） |
| **P2.6** | 2 | ⏳ | 阻塞于 P2.2 |
| ~~P2.3~~ | — | ❌ | 证据不成立：已由 [Part 5 P5.2 ✅](./omubot-humanization-part5-execution.md#6) 实现，不重复 |
| **P2.8** | 3 | ⏳ | 阻塞于 P3.6 + P3.7；v2 优先级链头 |
| **P3.8** | 3 | ⏳ | 阻塞于 P3.6 + Part 5 P5.2 ✅ |
| **P3.9** | 3 | ⏳ | 阻塞于 P3.6 + P3.7 + P2.2 + P3.1 |
| **P2.5** | 3 | ⏳ | 阻塞于 P3.1 |
| **P3.3** | 3 | ⏳ | 阻塞于 P3.2 |
| **P2.9** | 4 | ⏳ | 阻塞于 P2.8 |
| **P2.10** | 4 | ⏳ | 阻塞于 P2.8；保留 DEFAULT_STICKER_USAGE_HINT fallback |
| **P2.14** | 4 | ⏳ | 阻塞于 P2.8 + P3.6；自反馈环上限 0.3 |
| **P2.11** | 4 | ⏳ | 阻塞于 P2.8 |
| **P3.10** | 5 | ⏳ | 阻塞于 P3.6 + P3.7 + P3.8 + P3.9 |
| **P2.12** | 5 | ⏳ | 阻塞于 P2.8 + P2.10 |
| **P2.13** | 5 | ⏳ | 阻塞于 P2.11 |
| **P2.7+P3.5+v2-灰度** | 6 | ⏳ | 阻塞于 Wave 5 全 ✅ + 用户授权进灰度 |
| **P2/3-DOC** | 7 | ⏳ | 阻塞于 Wave 6 出口指标 ≥ 12/16 项 + 用户主观验收 |

> 任务总数 = v1 部分 11 条（剔除 P2.3） + v2 部分 12 条 + P0 + 灰度 + 文档收口 = 25 条。预算合计：v1 ≤ 290 行 + 27 测试，v2 ≤ 1170 行 + 87 测试，**合计 ≤ 1460 行 + ≥ 114 新测试**。

---

## 7. 执行者交接说明

1. **领单顺序**：先做 P0，回执贴依赖体检结论；再领 Wave 1 任意一条。
2. **多人并行**：Wave 1 内 5 条可同时下发，Wave 2 内 4 条同时（P2.6 必后于 P2.2），不同 wave 严格串行。
3. **commit 规范**：每条任务一个 commit，末尾不署 Co-Authored-By 行（本仓约定见 [docs/agent-discipline.md](../agent-discipline.md)）。
4. **验收提交**：把 §6 状态从 ⏳ 改 🟡 + PR 链接发我，我跑 §5 验收清单后改 ✅。
5. **冲突冲突**：本文 §1 与调研冲突时**以本文为准**（§1 7 项订正）；其它部分以 [Part 2/3 调研 v2](./omubot-humanization-part2-3-research.md) 为准。
6. **遇到证据不成立**：跟我同步，由我决定撤销 / 重订正。
7. **24h 灰度窗口约束（已由用户授权覆盖）**：原约束为 Part 5 P5.4 灰度未满 24h 时只允许领 P0；2026-05-25 用户明确要求忽略 24h 窗口限制并代验收 P0 / P5.4，因此 Wave 1 可领单。

---

## 8. 与 Part 1 / Part 4 / Part 5 的关系

### 8.1 Part 1 是前置基线（已 ✅）

| Part 1 子任务 | Part 2/3 接入点 | 现状 |
|---|---|---|
| **U1** segmentation 双实现合并 | P3.8 inter_segment_delay 改造目标在合并后的单一 segmentation 模块 | ✅ |
| **U3** Humanizer register-aware | P2.4 typing 系数扩展 / P3.8 mood 渗透 Humanizer | ✅ |
| **U6** humanization ModuleContract | P3.6 MOOD_CURRENT_SLOT / P3.7 AFFECTION_STAGE_SLOT 都加在 contract.py | ✅ |
| **V0** HumanizationConfig | Part 2/3 全量 feature flag 走 V0 同一旗标系统 | ✅ |
| **V1** RegisterClassifier | P3.6 mood_classifier 读 register slot；P3.9 mood/affection→planner 读 register | ✅ |
| **V5** AffectionFamiliarity | P3.7 AFFECTION_STAGE_SLOT 是 V5 binary 的 5-档升级版；保留 V5 slot 作 feature flag off fallback | ✅ |
| **V8** StylometricScorer | **不动**——mood / affection 不接 V8 输入；只接下游决策器 | ✅ |
| **V11** critic-rewrite-loop | P2.2 binary_planner 复用 V11 critic 框架；P3.10 联动 critic prefer rule 加 mood gate | ✅ |
| **V12** admin SPA 灰度 | Part 2/3 全 11 个 v2 旗标走 V12 同一 SPA | ✅ |

### 8.2 Part 5 是协同基线（部分 ✅）

| Part 5 子任务 | Part 2/3 接入点 | 现状 |
|---|---|---|
| **P5.1** natural_split | P3.8 mood 渗透 inter_segment_delay 时复用 P5.1 segments 长度 | ✅ |
| **P5.2** inter_segment_delay | **P2.3 已 ❌（被 P5.2 实现覆盖）**；P3.8 在 P5.2 公式上加 mood 系数表 | ✅ |
| **P5.3** client.py 切流 wiring | Part 2/3 全程在 P5.3 已切的 fan-out 通道工作；不另起改造 | ✅ |
| **P5.4** 灰度 24h 体感比对 | 用户授权忽略 24h 窗口限制并代验收通过；Part 2/3 可进入 Wave 1 | ✅ |

### 8.3 Part 4 完全隔离（未立项）

| Part 4 范畴 | Part 2/3 隔离方式 |
|---|---|
| 长期 persona evolution | mood / affection 是**短中时态**（分钟~天级），**不写入** persona_v2 freeze artifacts |
| 长程 mood 衰减 | Part 2/3 mood FSM 仅状态级（5-7 态），不做数值衰减 |
| "对 X 好感 +1" 数值通道 | **不做**（Replika 反例警示） |
| 88-dim emotion 向量 | **不做**（5-7 态足够） |

### 8.4 风险矩阵 + 30 秒回滚

继承自 [调研 §8 风险与回滚](./omubot-humanization-part2-3-research.md#8-风险与回滚)，按 wave 分组。

| 风险 | 触发条件 | 30s 回滚 |
|---|---|---|
| binary_planner 误判沉默 | LLM 二分类失败 → 全部 no_reply | `humanization.binary_planner_enabled=false` + restart |
| addressee detector 误识别 | 多 @ degree centrality 错误 | `humanization.addressee_detector_enabled=false` |
| topic_drift embedding 调用慢 | 每条消息 +200ms LLM call | feature flag + 改用 difflib fallback |
| read_mark 让 LLM 重复回老消息 | prompt 误导 | `humanization.read_mark_enabled=false` |
| willingness 5-stage 误标 | 分类器训练数据不足 | feature flag + 退回 register 二分类 |
| typing 时长引发段间过慢 | emoji 1s × N 段 | clamp 上限 max=2.0s |
| LLM planner 调用增加 token 消耗 | +1 LLM call per group msg | metrics 监控 + 改 schedule 抽样 50% |
| **sticker_decision_provider 误关 sticker** | mood 信号噪声大 → cold 状态过度触发 | `humanization.sticker_decision_provider_enabled=false` 退回 v1 4 路触发 |
| **kaomoji_enforce 拆解后 v1 case 回归** | 部分用户喜欢 kaomoji，新策略不发 | `humanization.kaomoji_enforce_strict=false` 退回 v1 强制轮 |
| **og:title fetch 拖慢回复** | 网络抖动 timeout 串行 | timeout 500ms + 异步 + 缓存；`humanization.og_title_enabled=false` |
| **MOOD_CURRENT_SLOT 自反馈环** | sticker 调用密度反馈 mood，反过来又改 sticker 概率 | 反馈衰减系数 0.3 + 上限阈值；`humanization.mood_slot_enabled=false` |
| **AFFECTION 5-档误标 stranger** | 冷启动 / 老用户记录缺失 | `humanization.affection_enabled=false` 退回 acquaint 默认档 |
| **mood / affection 渗透引发 V8 偏移** | mood=cold 时回复变短 → V8 5 轴漂移 | mood/affection 不接 V8 输入（设计已隔离） |

紧急回滚（30 秒）：

```bash
# config/config.json:
#   "humanization": {
#     "binary_planner_enabled": false,
#     "addressee_detector_enabled": false,
#     "topic_drift_enabled": false,
#     "read_mark_enabled": false,
#     "willingness_enabled": false,
#     "sticker_decision_provider_enabled": false,
#     "kaomoji_enforce_strict": false,
#     "og_title_enabled": false,
#     "mood_slot_enabled": false,
#     "affection_enabled": false
#   }
docker compose restart bot
```

---

## 9. 执行者 GPT 逐步追踪

> 本节由执行者按 P0 → Wave 1 → ... → P2/3-DOC 顺序追加；每条任务"领单拆分（执行前）" + "完成记录（执行者 GPT）" 双段，照搬 [Part 1 执行追踪 §9](./omubot-humanization-part1-execution.md#9-执行者-gpt-逐步追踪) 与 [Part 5 执行追踪 §9](./omubot-humanization-part5-execution.md#9-执行者-gpt-逐步追踪) 的格式。

### P0 领单拆分（执行前）— Codex / 2026-05-25 19:59 CST

- **任务边界**：只执行 §2 P0 前置体检与本文回填；不改 `.py` / `.json` / `.toml` / 镜像 / 容器，不进入 Wave 1。
- **自主评估**：P0 是零代码依赖实证，当前 Part 5 P5.4 仍标记 🟡，因此唯一可执行派单就是 P0；若任一依赖不达标，Part 2/3 Wave 1+ 全部继续阻塞。
- **执行拆分**：
  1. grep segmentation / inter_segment_delay 证据，确认 Part 5 P5.2/P5.3 已落点。
  2. grep humanization contract / block_trace slot 证据，确认 Part 1 U6/V1/V5/V14 基线。
  3. grep config humanization / runtime_groups 证据，确认 V0/V12 灰度旗标基线。
  4. `source ./scripts/dev/env.sh && uv run pytest --collect-only -q` 采集测试基线。
  5. `git status -uno` 与目标目录 `ls`，确认施工目录存在且目标新模块当前不存在。
- **风险与回滚**：无运行时代码改动，无需 feature flag 回滚；仅更新追踪文档，如证据不达标则只记录阻塞原因。

### P0 完成记录（执行者 Codex）— 2026-05-25 20:04 CST

自验结果：P0 进入 🟡 等验收。核心前置依赖达标，测试 collect-only 基线为 `1742 tests collected in 0.73s`（≥ 1734）。本轮只改追踪文档，无运行时副作用。2026-05-25 用户后续授权代验收 P0 与 Part 5 P5.4 后，P0 状态改 ✅，Wave 1 入场阻塞解除。

命令回执：

- `rg -n "natural_split_enabled|reply_segment_plan|inter_segment_delay" services/llm/segmentation.py services/llm/client.py services/send_queue.py` → 命中 `segmentation.py:442 inter_segment_delay()`、`:909 reply_segment_plan()`、`client.py:2508/2714` 两处 plan 入口、`client.py:2533/2739` 两处动态 delay fan-out、`send_queue.py:32/322-323` 队列固定 delay 字段，证明 Part 5 P5.2/P5.3 已落。
- `rg -n "REGISTER_LABEL_SLOT|AFFECTION_FAMILIARITY_SLOT|CLOCK_CURRENT_SLOT" services/humanization/contract.py services/block_trace` → 命中 `contract.py:9/12/14/34/37/39` 与 `register_provider.py` / `catchphrase_provider.py` / `episode_provider.py` 多处读取，证明 Part 1 U6/V1/V5/V14 基线已落。
- `grep -n "humanization\|register_classifier\|context_providers\|runtime_groups" kernel/config.py config/config.json` → `HumanizationConfig` 与 `context_providers` / `register_classifier` / `runtime_groups` 字段存在；实况订正：`config/config.json` 的 `humanization.runtime_groups` 当前为 `["993065015"]`，双群 `["993065015","984198159"]` 出现在 `persona_v2.runtime_groups`。
- `source ./scripts/dev/env.sh && uv run pytest --collect-only -q 2>&1 | tail -1` → `1742 tests collected in 0.73s`。
- `git status -uno` → 执行前 clean；写入 P0 追踪后仅 `docs/tracking/omubot-humanization-part2-3-execution.md` dirty。
- `ls services/humanization/ services/persona/ plugins/sticker/ services/media/` → 4 个目标目录存在；`services/humanization/mood_classifier.py`、`services/persona/affection_classifier.py` 不存在，符合 P3.6/P3.7 施工目标；额外实况订正：`services/sticker/` 目录当前不存在，P2.8 需新建目录。

代验收与下一步：P0 已由用户授权代验收 ✅；下一步执行者需承认上述两处基线订正后再领 P2.1/P3.1/P3.6/P3.4/P2.4。

### P2.1 领单拆分（执行前）— Codex / 2026-05-25 21:20 CST

- **任务边界**：新增 `scripts/dev/measure_rhythm.sh`，只读 `storage/messages.db` 的 `group_messages` 与可选 `storage/block_trace.db`；不改运行代码、不改配置、不写数据库。
- **自主评估**：P2.1 是 Wave 1 的最低风险度量基线，适合作为 P0/P5.4 代验收后的第一单。当前仓库实际没有 `storage/group_messages.db`，但 `storage/messages.db` 内已有 `group_messages` 表，因此脚本按实况使用 `GROUP_MESSAGES_DB` 覆盖入口，默认落到 `storage/messages.db`。
- **执行拆分**：
  1. 读取 `group_messages` schema 与 role 分布，确认可从 `role='assistant'` 或 bot 侧消息计算 reply 段数与相邻段间隔。
  2. 新建只读 shell 脚本，输出 `rhythm_baseline`、group 过滤、样本数、回复延迟、段间间隔、段数分布。
  3. 用 `bash -n`、脚本 dry-run、D1 grep 验证；P2.1 无 cancel-path，记录为 read-only 不适用。
- **风险与回滚**：脚本只读 SQLite；回滚为删除 `scripts/dev/measure_rhythm.sh` 与本节 / §6 状态回填。

### P2.1 完成记录（执行者 Codex）— 2026-05-25 21:30 CST

自验结果：P2.1 完成，进入 🟡 等验收。新增脚本 59 行，满足 `new ≤ 60 行`；脚本只读 SQLite，不写 `storage/`，无运行时副作用。

改动内容：

- 新增 `scripts/dev/measure_rhythm.sh`：默认读取 `storage/messages.db:group_messages`，兼容 `GROUP_MESSAGES_DB` / `MESSAGES_DB` / `GROUP_ID` / `LIMIT` / `SEGMENT_GAP_S` / `REPLY_DELAY_MAX_S` 环境变量。
- 输出 `rhythm_baseline`、样本 reply 数、回复延迟 avg/p50/p95、段间间隔 count/avg/p95、段数分布；`BLOCK_TRACE_DB` 仅作存在性回显，保持只读。
- 根据本仓实况订正派单路径：当前没有 `storage/group_messages.db`，真实表在 `storage/messages.db` 的 `group_messages`。

验证：

- `wc -l scripts/dev/measure_rhythm.sh` → `59`。
- `bash -n scripts/dev/measure_rhythm.sh` → passed。
- `scripts/dev/measure_rhythm.sh` → `sample_replies: 200`，`reply_delay_s_avg: 9.758`，`inter_segment_gap_count: 135`，`segment_count_distribution: 1=128, 2=39, 3=11, 4=17, 5=2, 6=3`。
- `GROUP_ID=993065015 scripts/dev/measure_rhythm.sh` → `sample_replies: 200`，`reply_delay_s_avg: 11.195`，`inter_segment_gap_count: 230`，`segment_count_distribution: 1=110, 2=42, 3=11, 4=18, 5=7, 6=6, 10=6`。
- D1 grep：`rg -n "measure_rhythm|rhythm_baseline" scripts docs README.md` → 新脚本 + Part 2/3 tracking/research 中的任务定义命中；无运行时代码命中。

D2 / 回滚：P2.1 是纯只读采样脚本，无 cancel-path 写状态；回滚为删除 `scripts/dev/measure_rhythm.sh` 并撤销 §6 / §9 的 P2.1 回填。

### P3.1 领单拆分（执行前）— Codex / 2026-05-25 21:35 CST

- **任务边界**：新建 `services/group/__init__.py` 与 `services/group/addressee.py`，提供独立 addressee detector；不修改 `services/scheduler.py` / chat plugin / router，不接生产判定。
- **自主评估**：当前仓库无 `services/group/` 包，P3.1 可作为 Wave 1 并行任务独立落地。为降低 blast radius，本步只提供 stateless detector 与单测，feature flag 真正消费留给后续 P2.5/P3.9。
- **执行拆分**：
  1. 新增 `AddresseeResult(target_id, confidence, source)` 与 `AddresseeDetector.detect()`。
  2. 按 adapter / regex / quote / @ 四层 cascade 识别：显式 adapter 字段优先，其次 bot name 呼唤，再读 quote sender，最后读 CQ/文本 @。
  3. 新增 `tests/test_addressee_detector.py` 覆盖 4 层、无命中、cancel-path 后仍可复用 detector。
  4. 跑定向 pytest、ruff/pyright、D1 grep。
- **风险与回滚**：未接生产路径，运行时无行为变化；回滚为删除 `services/group/` 与 `tests/test_addressee_detector.py`，并撤销 §6 / §9 P3.1 回填。

### P3.1 完成记录（执行者 Codex）— 2026-05-25 21:43 CST

自验结果：P3.1 完成，进入 🟡 等验收。新增 `services/group/addressee.py` 126 行，低于 `new ≤ 150 行`；未修改 `services/scheduler.py` / chat plugin / router，运行时行为不变。

改动内容：

- 新增 `services/group/__init__.py` 与 `services/group/addressee.py`。
- `AddresseeDetector.detect()` 按 adapter → regex → quote → @ 四层 cascade 返回 `AddresseeResult(target_id, confidence, source)`。
- adapter 层读取 `target_id` / `addressee_id` / `mentioned_user_id` / `to_user_id` 与 `additional_config`；regex 层识别 bot name 呼唤；quote 层读取 `reply_sender_id` / `quote_user_id` 等；@ 层识别 CQ at / Discord-style mention / 数字 @。
- 新增 `tests/test_addressee_detector.py` 7 条，覆盖四层优先级、无命中、cancel-path 后 detector 可复用。

验证：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_addressee_detector.py -q` → `7 passed`。
- `source ./scripts/dev/env.sh && uv run ruff check services/group/addressee.py services/group/__init__.py tests/test_addressee_detector.py` → `All checks passed!`。
- `source ./scripts/dev/env.sh && uv run pyright services/group/addressee.py tests/test_addressee_detector.py` → `0 errors, 0 warnings, 0 informations`。
- `source ./scripts/dev/env.sh && uv run python -m py_compile services/group/addressee.py services/group/__init__.py tests/test_addressee_detector.py` → passed。
- D1 grep：`rg -n "class AddresseeDetector|addressee_detector|AddresseeDetector|AddresseeResult" services tests docs/tracking/omubot-humanization-part2-3-execution.md` → 命中新模块、新测试与本追踪记录；无生产接线路径命中。

D2 / 回滚：cancel-path 测试通过，`asyncio.CancelledError` 不被吞且 detector 后续可复用；回滚为删除 `services/group/`、`tests/test_addressee_detector.py` 并撤销 §6 / §9 的 P3.1 回填。

### P3.6 领单拆分（执行前）— Codex / 2026-05-25 21:45 CST

- **任务边界**：在 humanization contract 注册 `MOOD_CURRENT_SLOT`，导出 mood classifier API，并新增 `services/humanization/mood_classifier.py`；本步不接生产调度、不写配置。
- **自主评估**：P3.6 是后续 P3.7/P2.8/P3.8 的关键前置。当前 RuntimeStateBus 已支持 owner/ttl/decay，P3.6 可通过 `decay_at=now+300s` 实现派单要求的 300 秒短态 slot。
- **执行拆分**：
  1. `contract.py` 添加 `MOOD_CURRENT_SLOT="humanization.mood.current"` 与 per_session slot 注册。
  2. `__init__.py` 导出 `MOOD_CURRENT_SLOT`、`MoodClassifier`、`MoodDecision`、`MoodLabel`、`MoodSignals`。
  3. 新建 `MoodClassifier`，基于用户回复间隔、短回复占比、sticker 密度、语气词命中率做 5 态 FSM：cold/tired/neutral/playful/high。
  4. 新增测试覆盖 5 态边界、slot 写入 decay、cancel-path 不脏写。
- **风险与回滚**：未接生产 worker，运行时无行为变化；回滚为撤销 contract/__init__ 导出、新文件与测试。

### P3.6 完成记录（执行者 Codex）— 2026-05-25 21:54 CST

自验结果：P3.6 完成，进入 🟡 等验收。`services/humanization/mood_classifier.py` 122 行，低于 `new ≤ 180 行`；contract.py 只增加 slot 常量与注册，未改变既有 slot 语义。

改动内容：

- `services/humanization/contract.py` 新增 `MOOD_CURRENT_SLOT="humanization.mood.current"`，schema `omubot.state.humanization_mood_current.v1`，ttl=`per_session`。
- `services/humanization/__init__.py` 导出 mood slot 与 classifier 类型。
- `services/humanization/mood_classifier.py` 新增 `MoodSignals` / `MoodDecision` / `MoodClassifier`；`classify_and_write()` 写 RuntimeStateBus 时设置 `decay_at=now+300s`。
- `tests/test_mood_classifier.py` 新增 6 条，覆盖 cold / tired / playful / high / slot 写入 / cancel-path；`tests/test_humanization_contract.py` 补 MOOD slot owner 断言。

验证：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_mood_classifier.py tests/test_humanization_contract.py -q` → `12 passed`。
- `source ./scripts/dev/env.sh && uv run ruff check services/humanization/contract.py services/humanization/__init__.py services/humanization/mood_classifier.py tests/test_mood_classifier.py tests/test_humanization_contract.py` → `All checks passed!`。
- `source ./scripts/dev/env.sh && uv run pyright services/humanization/mood_classifier.py tests/test_mood_classifier.py` → `0 errors, 0 warnings, 0 informations`。
- D1 grep：`rg -n "MOOD_CURRENT_SLOT|MoodClassifier" services/humanization tests docs/tracking/omubot-humanization-part2-3-execution.md` → 命中 contract.py / mood_classifier.py / __init__.py / tests / 本追踪记录；无其它生产接线路径命中。

D2 / 回滚：cancel-path 测试通过，`asyncio.CancelledError` 发生在 classify 阶段时 `MOOD_CURRENT_SLOT` 不写入；回滚为撤销 contract/__init__ 改动、删除 `mood_classifier.py` 与 `tests/test_mood_classifier.py`，并撤销 §6 / §9 的 P3.6 回填。

### P3.4 领单拆分（执行前）— Codex / 2026-05-25 21:56 CST

- **任务边界**：新建 `services/persona/willingness.py`，提供纯计算 willingness 5-stage；不写 RuntimeStateBus / DB / persona admin map。
- **自主评估**：P3.4 与 P3.6/P3.1 同属 Wave 1，可并行落地。该模块后续供 binary planner / addressee gate 读取，当前只提供稳定分类 API 和边界测试。
- **执行拆分**：
  1. 定义 `WillingnessStage` 与 `Willingness` 结果对象。
  2. 基于近期回复延迟、register 一致性、最近互动数、沉默计数输出 stranger/acquaint/familiar/close/withdraw。
  3. 新增 `tests/test_willingness.py` 覆盖 5 档边界与纯计算无副作用。
  4. 跑定向 pytest、ruff、pyright、D1 grep。
- **风险与回滚**：纯计算模块，无 cancel-path 写入；回滚为删除 `services/persona/willingness.py` 与 `tests/test_willingness.py`，并撤销 §6 / §9 P3.4 回填。

### P3.4 完成记录（执行者 Codex）— 2026-05-25 22:00 CST

自验结果：P3.4 完成，进入 🟡 等验收。`services/persona/willingness.py` 53 行，低于 `new ≤ 80 行`；模块纯计算，不写 DB / RuntimeStateBus / persona admin map。

改动内容：

- 新增 `services/persona/willingness.py`，提供 `Willingness` dataclass 与 `willingness_stage()`。
- 基于 `recent_reply_delay_s`、`register_consistency`、`interaction_count`、`consecutive_no_reply` 输出 stranger/acquaint/familiar/close/withdraw。
- 新增 `tests/test_willingness.py` 6 条，覆盖 5 档边界和 `to_state_value()` 的 `willingness_stage` 输出。

验证：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_willingness.py -q` → `6 passed`。
- `source ./scripts/dev/env.sh && uv run ruff check services/persona/willingness.py tests/test_willingness.py` → `All checks passed!`。
- `source ./scripts/dev/env.sh && uv run pyright services/persona/willingness.py tests/test_willingness.py` → `0 errors, 0 warnings, 0 informations`。
- D1 grep：`rg -n "class Willingness|willingness_stage" services/persona tests docs/tracking/omubot-humanization-part2-3-execution.md` → 命中新模块、新测试与本追踪记录；无其它生产接线路径命中。

D2 / 回滚：纯计算，无 cancel-path 写入面；回滚为删除 `services/persona/willingness.py`、`tests/test_willingness.py` 并撤销 §6 / §9 的 P3.4 回填。

### P2.4 领单拆分（执行前）— Codex / 2026-05-25 22:02 CST

- **任务边界**：只扩展 `services/humanizer.py` 的 typing delay 计算与测试；不改 scheduler / send_queue / config。
- **自主评估**：P2.4 是 Wave 1 最后一条，触碰真实发送前 delay，但可通过默认参数保持旧行为。emoji 起步价采用 `max(extra, emoji_base_s)`，避免多 emoji 线性叠加；thinking 10s 兜底用可选 `thinking_elapsed_s`，旧调用不传则不触发。
- **执行拆分**：
  1. 新增 `EMOJI_BASE_DELAY` / `THINKING_FALLBACK` 常量与 emoji 检测。
  2. `Humanizer.__init__` 增加 `emoji_base_s` 默认 1.0；`delay()` 增加 `thinking_elapsed_s=None`。
  3. 长度 extra 改为 helper 计算：普通文本保持旧公式；emoji 文本至少 1s；thinking ≥10s 时 total cap 到 1s。
  4. 新增 `tests/test_humanizer_typing.py` 覆盖普通兼容、emoji 起步价、thinking fallback、disabled 不 sleep。
- **风险与回滚**：默认调用不传 `thinking_elapsed_s`，旧文本无 emoji 时行为不变；回滚为 `git restore services/humanizer.py` 并删除新测试。

### P2.4 完成记录（执行者 Codex）— 2026-05-25 22:08 CST

自验结果：P2.4 完成，进入 🟡 等验收。`services/humanizer.py` 实际 `+29/-1`，符合 `≤ +30 行`；旧调用不传 `thinking_elapsed_s` 时保持兼容。

改动内容：

- `services/humanizer.py` 新增 `EMOJI_BASE_DELAY=1.0`、`THINKING_FALLBACK=10.0`、emoji 检测与 `_typing_extra()`。
- `Humanizer.__init__()` 增加 `emoji_base_s`，默认 `1.0`；emoji/CQ face/CQ mface 文本的 typing extra 至少 1 秒。
- `Humanizer.delay()` 增加可选 `thinking_elapsed_s`；当 thinking 已耗时 ≥10 秒时，本次 typing delay cap 到 1 秒。
- 新增 `tests/test_humanizer_typing.py` 4 条，覆盖普通兼容、emoji 起步价、thinking fallback、disabled 不 sleep。

验证：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_humanizer_typing.py tests/test_humanizer_register.py tests/test_humanizer_runtime.py -q` → `12 passed`。
- `source ./scripts/dev/env.sh && uv run ruff check services/humanizer.py tests/test_humanizer_typing.py tests/test_humanizer_register.py tests/test_humanizer_runtime.py` → `All checks passed!`。
- `source ./scripts/dev/env.sh && uv run pyright services/humanizer.py tests/test_humanizer_typing.py` → `0 errors, 0 warnings, 0 informations`。
- `source ./scripts/dev/env.sh && uv run python -m py_compile services/humanizer.py tests/test_humanizer_typing.py` → passed。
- D1 grep：`rg -n "EMOJI_BASE_DELAY|THINKING_FALLBACK|emoji_base_s" services/humanizer.py tests docs/tracking/omubot-humanization-part2-3-execution.md` → 命中 humanizer.py、新测试与本追踪记录；无其它生产路径命中。

D2 / 回滚：已有 register/mood/slot 入参兼容测试通过；本任务无新增写状态 cancel-path。回滚为 `git restore services/humanizer.py`、删除 `tests/test_humanizer_typing.py` 并撤销 §6 / §9 的 P2.4 回填。

### Wave 1 自主验收记录（Codex）— 2026-05-25 22:14 CST

用户授权：`自主执行，自动完成，如果不出现风险项自动commit`。

自主验收结论：P2.1 / P3.1 / P3.6 / P3.4 / P2.4 均通过，§6 状态由 🟡 改 ✅。未发现需要阻断的风险项：

- P2.1 只读 SQLite 脚本，无运行时副作用。
- P3.1 / P3.6 / P3.4 均未接生产路径或不写持久化状态；cancel-path / 纯计算边界已覆盖。
- P2.4 是唯一运行时行为变更，但普通无 emoji 文本保持旧公式；新增 `thinking_elapsed_s` 为可选参数，旧调用不触发 fallback。

验收命令：

- `source ./scripts/dev/env.sh && uv run pytest tests/test_addressee_detector.py tests/test_mood_classifier.py tests/test_humanization_contract.py tests/test_willingness.py tests/test_humanizer_typing.py tests/test_humanizer_register.py tests/test_humanizer_runtime.py -q` → `37 passed`。
- `source ./scripts/dev/env.sh && uv run ruff check ...`（Wave 1 改动范围）→ `All checks passed!`。
- `source ./scripts/dev/env.sh && uv run pyright ...`（Wave 1 改动范围）→ `0 errors, 0 warnings, 0 informations`。
- `source ./scripts/dev/env.sh && uv run pytest --collect-only -q` → `1765 tests collected`。
- `git diff --check` → passed。

提交策略：按用户授权，本批 Wave 1 通过后自动提交；Wave 2 在该提交之后继续领单。

### P3.2 领单拆分（执行前）— Codex / 2026-05-25 22:34 CST

- **任务边界**：新增 `services/group/topic_drift.py` 与 `tests/test_topic_drift.py`；只提供 in-line detector，不接 PromptBuilder / planner / 生产调度，不写 RuntimeStateBus / DB。
- **自主评估**：P3.2 依赖 P3.1，当前 P3.1 已自主验收并随 Wave 1 提交。仓库现有 `services/similarity.py` 仅提供 ngram 默认 provider，`embedding` provider 是未安装占位；为避免引入外部依赖，本步通过 provider 注入复用 similarity 接口，默认用无依赖 provider，后续可替换真实 embedding/cosine provider。
- **执行拆分**：
  1. 定义 `TopicDriftResult(topic, drift_score, is_new_topic, participants)`。
  2. `TopicDriftDetector.detect()` 读取最近 3 条用户消息，把前两条作为 previous topic，最后一条作为 current topic。
  3. 用 `SimilarityProvider.similarity()` 计算 topic 连贯度，`drift_score = 1 - similarity`，不使用 difflib。
  4. 新增 6 条测试覆盖冷启动、低漂移、高漂移、参与者去重、CQ/URL 清洗、provider 异常 fallback。
  5. 跑定向 pytest、ruff、pyright、D1 grep。
- **风险与回滚**：未接生产路径，运行时无行为变化；回滚为删除 `services/group/topic_drift.py` 与 `tests/test_topic_drift.py`，并撤销 §6 / §9 P3.2 回填。

### P3.2 完成记录（执行者 Codex）— 2026-05-25 22:44 CST

自验结果：P3.2 完成并自主验收 ✅。`services/group/topic_drift.py` 119 行，低于 `new ≤ 120 行`；本步未接 PromptBuilder / planner / 生产调度，不改变线上回复行为。

改动内容：

- 新增 `services/group/topic_drift.py`，提供 `TopicDriftResult` 与 `TopicDriftDetector.detect()`。
- detector 只读取最近 3 条 user message：前两条合成 previous topic，最后一条作为 current topic；输出 `topic`、`drift_score`、`is_new_topic`、`participants`。
- 复用 `services/similarity.py` 的 `SimilarityProvider` / `NgramSimilarityProvider` / `normalize_text_key`；不引入 difflib；注入 embedding provider 失败时 fallback 到 ngram provider，避免未安装后端影响调用。
- 新增 `tests/test_topic_drift.py` 6 条，覆盖冷启动、低漂移、高漂移、只读 last 3 user messages、CQ/URL 清洗、provider 异常 fallback。

验证：

- `wc -l services/group/topic_drift.py tests/test_topic_drift.py` → `119 services/group/topic_drift.py`，`111 tests/test_topic_drift.py`。
- `source ./scripts/dev/env.sh && uv run pytest tests/test_topic_drift.py -q` → `6 passed`。
- `source ./scripts/dev/env.sh && uv run ruff check services/group/topic_drift.py tests/test_topic_drift.py` → `All checks passed!`。
- `source ./scripts/dev/env.sh && uv run pyright services/group/topic_drift.py tests/test_topic_drift.py` → `0 errors, 0 warnings, 0 informations`。
- `source ./scripts/dev/env.sh && uv run python -m py_compile services/group/topic_drift.py tests/test_topic_drift.py` → passed。
- D1 grep：`rg -n "class TopicDriftDetector|TopicDriftResult|topic_drift" services/group/topic_drift.py tests/test_topic_drift.py docs/tracking/omubot-humanization-part2-3-execution.md` → 命中新模块、新测试与本追踪记录；无生产接线路径命中。

D2 / 回滚：本任务无写状态 cancel-path；回滚为删除 `services/group/topic_drift.py`、`tests/test_topic_drift.py` 并撤销 §6 / §9 的 P3.2 回填。

### P3.7 领单拆分（执行前）— Codex / 2026-05-25 22:53 CST

- **任务边界**：新增 `services/persona/affection_classifier.py`，在 `services/humanization/contract.py` 注册 `AFFECTION_STAGE_SLOT` 并从 `services/humanization/__init__.py` 导出；不写 persona admin map，不改现有 `plugins/affection` 分数/昵称行为，不接生产调用。
- **自主评估**：P3.7 依赖 P3.6 的 RuntimeStateBus 写路径，当前 P3.6 已 ✅。仓库已有 `AFFECTION_FAMILIARITY_SLOT` 和 JSON affection store，但派单明确订正为独立 sqlite `storage/affection_stage.db`；因此本步只新增 stage store，与旧好感 JSON store 并存。
- **执行拆分**：
  1. `contract.py` 新增 `AFFECTION_STAGE_SLOT="humanization.affection.stage"`，slot ttl=`per_user`，写入时用 `decay_at=now+86400s` 表达 24h 滚动。
  2. 新建 `AffectionSignals` / `AffectionDecision` / `AffectionStageStore` / `AffectionClassifier`，含 5 档 stranger/acquaint/familiar/close/withdraw。
  3. `AffectionStageStore` 默认路径为 `storage/affection_stage.db`，schema 按 `(user_id, group_id)` upsert，读取只认 24h 内记录。
  4. 新增测试覆盖 cold start stranger、无近期记录 fallback acquaint、5 档边界、store 24h 滚动、RuntimeStateBus 写入与 cancel-path 不脏写。
  5. 跑定向 pytest、ruff、pyright、D1 grep。
- **风险与回滚**：当前未接生产路径；回滚为撤销 contract/__init__ 改动、删除 `services/persona/affection_classifier.py` 与 `tests/test_affection_classifier.py`，并撤销 §6 / §9 P3.7 回填。

### P3.7 完成记录（执行者 Codex）— 2026-05-25 23:08 CST

自验结果：P3.7 完成并自主验收 ✅。`services/persona/affection_classifier.py` 146 行，低于 `new ≤ 150 行`；未写 persona admin map，未改现有 `plugins/affection` 分数 / 昵称 / prompt block 行为。

改动内容：

- `services/humanization/contract.py` 新增 `AFFECTION_STAGE_SLOT="humanization.affection.stage"`，schema `omubot.state.humanization_affection_stage.v1`，ttl=`per_user`。
- `services/humanization/__init__.py` 导出 `AFFECTION_STAGE_SLOT`。
- 新增 `services/persona/affection_classifier.py`，包含 `AffectionSignals` / `AffectionDecision` / `AffectionStageStore` / `AffectionClassifier`。
- `AffectionStageStore` 默认 `storage/affection_stage.db`，按 `(user_id, group_id)` upsert，`load_recent()` 只返回 24h 内记录。
- `AffectionClassifier` 输出 stranger/acquaint/familiar/close/withdraw；`classify_and_write()` 写 RuntimeStateBus 时设置 `decay_at=now+86400s`。
- 新增 `tests/test_affection_classifier.py` 10 条，覆盖 cold start stranger、无近期记录 fallback acquaint、5 档边界、store 24h rolling、bus 写入与 cancel-path。

验证：

- `wc -l services/persona/affection_classifier.py` → `146`。
- `source ./scripts/dev/env.sh && uv run pytest tests/test_affection_classifier.py tests/test_humanization_contract.py -q` → `16 passed`。
- `source ./scripts/dev/env.sh && uv run ruff check services/persona/affection_classifier.py services/humanization/contract.py services/humanization/__init__.py tests/test_affection_classifier.py tests/test_humanization_contract.py` → `All checks passed!`。
- `source ./scripts/dev/env.sh && uv run pyright services/persona/affection_classifier.py services/humanization/contract.py services/humanization/__init__.py tests/test_affection_classifier.py tests/test_humanization_contract.py` → `0 errors, 0 warnings, 0 informations`。
- `source ./scripts/dev/env.sh && uv run python -m py_compile services/persona/affection_classifier.py services/humanization/contract.py services/humanization/__init__.py tests/test_affection_classifier.py` → passed。
- D1 grep：`rg -n "AFFECTION_STAGE_SLOT|AffectionClassifier|AffectionStageStore|affection_stage" services/persona/affection_classifier.py services/humanization tests/test_affection_classifier.py docs/tracking/omubot-humanization-part2-3-execution.md` → 命中新模块、contract/export、新测试与本追踪记录；未命中 persona admin map。

D2 / 回滚：cancel-path 测试通过，`asyncio.CancelledError` 发生在 classify 阶段时不写 RuntimeStateBus / sqlite；回滚为撤销 contract/__init__ 改动，删除 `services/persona/affection_classifier.py`、`tests/test_affection_classifier.py` 并撤销 §6 / §9 的 P3.7 回填。
