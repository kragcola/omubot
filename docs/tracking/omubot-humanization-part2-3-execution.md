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

---

## 2. P0 前置体检（依赖 + 字段实证）

派单第 0 步，零代码改动。Part 2/3 全 wave 阻塞于以下 4 项依赖为 ✅：

| 步骤 | 命令 | 预期结果 |
|---|---|---|
| 1 | `grep -rn "natural_split_enabled\|reply_segment_plan\|inter_segment_delay" services/llm/segmentation.py services/llm/client.py services/send_queue.py` | 命中 Part 5 P5.2 / P5.3 已落地的 segmentation 函数 + client.py 两处 fan-out + send_queue.py inter_segment_delay_s 字段 |
| 2 | `grep -rn "REGISTER_LABEL_SLOT\|AFFECTION_FAMILIARITY_SLOT\|CLOCK_CURRENT_SLOT" services/humanization/contract.py services/block_trace/` | 命中 contract.py 7 个 slot + block_trace provider 5 处读取，证明 [Part 1 U6 + V1 + V5 + V14 ✅](./omubot-humanization-part1-execution.md#6) 已落 |
| 3 | `grep -n "humanization\|register_classifier\|context_providers\|runtime_groups" kernel/config.py config/config.json` | `HumanizationConfig` 7 字段 + `runtime_groups=["993065015","984198159"]`，证明 V0 / V12 已收口 |
| 4 | `uv run pytest --collect-only -q 2>&1 \| tail -1` | 当前基线 ≥ 1734 tests collected（Part 5 P5.3 出口）；Part 2/3 全量出口要求 ≥ 1734 + 137 = ≥ 1871 |
| 5 | `git status -uno` | working tree clean（避免 dirty 文件混入派单 commit） |
| 6 | `ls services/humanization/ services/persona/ plugins/sticker/ services/media/` | 4 个目标目录存在；mood_classifier.py / affection_classifier.py / decision_provider.py 当前**不存在**（这就是 P2.8 / P3.6 / P3.7 的施工目标） |
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
| natural_split + inter_segment_delay | Part 5 P5.1 / P5.2 / P5.3 | ✅（P5.4 灰度 🟡 未满 24h） |
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
| **P0** | 0 | ⏳ | 待执行：4 项依赖体检 + 1 项 collect-only 基线 |
| **P2.1** | 1 | ⏳ | 待执行：节奏度量基线脚本 |
| **P3.1** | 1 | ⏳ | 待执行：addressee detector 4 层 cascade |
| **P3.6** | 1 | ⏳ | 待执行：MOOD_CURRENT_SLOT + mood_classifier；本任务是 v2 优先级链关键节点 |
| **P3.4** | 1 | ⏳ | 待执行：willingness 5-stage（v1 部分） |
| **P2.4** | 1 | ⏳ | 待执行：typing 字符系数扩展（emoji 1s + thinking 10s） |
| **P3.2** | 2 | ⏳ | 阻塞于 P3.1 |
| **P3.7** | 2 | ⏳ | 阻塞于 P3.6；订正后落 `storage/affection_stage.db` 独立 sqlite |
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
7. **24h 灰度窗口约束**：Part 5 P5.4 灰度未满 24h 时（窗口至 2026-05-26 ~08:11 UTC），**只允许领 P0 体检**；24h 灰度通过后才允许进 Wave 1。

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
| **P5.4** 灰度 24h 体感比对 | **当前 🟡 灰度中**——Part 2/3 派单**等 P5.4 验收 ✅ 后再领 Wave 1** | 🟡 |

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

（待第一条任务领单后由执行者填）
