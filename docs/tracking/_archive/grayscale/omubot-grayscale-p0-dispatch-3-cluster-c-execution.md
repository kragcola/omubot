# P0 派单 3 — C 簇 13A 字段重构独立 PR（执行追踪）

> 状态：2026-05-27 回填。本文是 [P0 待审清单](omubot-grayscale-p0-pending-2026-05-27.md) 的「派单 3」执行版。
>
> 当前结论：Wave 0 ~ 4 已落地并完成本地全量验证（1985 passed / ruff clean / pyright 0 errors）；Wave 5 待 PR 合并与 24h 灰度观察后收口。
>
> 范围：F13 ThinkDecision 字段重构 + schedule activity enum 化（C 簇 source-side breaking change，1 个独立 PR）。
>
> 配套：[原解决方案文档](omubot-grayscale-issues-2026-05-26-solutions.md) §Issue 13 路径 A 治本（结构化字段）+ 路径 B 治本（schedule enum）；不含 §Issue 13 phrase detector（已落派单 1）。
>
> 上游决策：用户已拍板「方案全按推荐来 / PR 自主分阶段 / 一个派发单需完成全部修复」——本派单**收口才闭环**。
>
> 依赖：派单 1（A 簇出口）应已合并 main——派单 1 的 thinker_phrase_detector（A2.2）会随本派单字段重构同步**降级**：detector 仍生效但命中率应近 0（thought 不再进 system_blocks）。
>
> 执行原则（覆盖任何冲突项）：
> 1. **整单 1 个 PR**——thinker 字段重构 + schedule enum 化 + thinker prompt 重写 必须一次合并；不允许"先动 ThinkDecision 不动 schedule"。
> 2. **breaking change**——thinker 输出 schema 变更，需 retry-on-parse-fail 兜底；schedule activity 改 enum 后旧 schedule 缓存需要 migration 或一次性 invalidate。
> 3. **每条 Wave 自带 D1 grep 证据 + D2 cancel-path 测试 + 30 秒回滚开关**，缺一不闭环。
> 4. **不实现 voice exemplars 完整骨架**——v2 source.md 重写（C 簇完整 F4/F8 第一刀）超出本派单范围（属于 P1+ 后续单）；本派单仅做 thinker / schedule 两处 enum 化。

---

## 1. 主线自审与证据订正（执行前必读）

下表是对 [原解决方案文档](omubot-grayscale-issues-2026-05-26-solutions.md) §Issue 13 方案 13A 的 grep 实证修订。

| 解决方案文档位置 | 初稿表述 | grep 实证（2026-05-27） | 订正 |
|---|---|---|---|
| §13A 路径 A 形态 | "[services/llm/thinker.py:141-533](../../services/llm/thinker.py#L141-L533) ThinkDecision 保留 action+tone+sticker，新增 topic_intent_label" | `THINKER_SYSTEM_PROMPT` 输出格式在 `services/llm/thinker.py:139-141`；`ThinkDecision` 实位 `services/llm/thinker.py:144-184`；`topic_intent_label` 规范化在 `services/llm/thinker.py:230-232`，解析在 `services/llm/thinker.py:235-263` | Wave 1 实际位点已漂移；`topic_intent_label` 已进入 prompt / `ThinkDecision` / parser / runtime-state 写入链路 |
| §13A 路径 A 形态 | "client.py:2526-2541 thinker_block 重构" | 旧式 thinker block 的实际注入位点在 `services/llm/client.py:3392-3406`，当前文本已经是 `【意图：...】【tone: ...】【sticker: ...】`；`topic_intent_label` 同时通过 `_fire_thinker_decision()` 写进 `ThinkerContext`（`services/llm/client.py:1753-1779`）并记录到 trace metadata（`services/llm/client.py:3215-3231`） | Wave 2 已从 `thought` 文本注入改成 label 注入；`thought` 仍保留在内部链路里做 log / 后处理，不再进 system block |
| §13A 路径 B 形态 | "plugins/schedule/generator.py:23-56 _SCHEDULE_SYSTEM_PROMPT activity 字段改 enum" | `_SCHEDULE_SYSTEM_PROMPT` 在 `plugins/schedule/generator.py:24-59` 已要求 `activity` 取 12 个枚举之一，并新增 `description`；schedule 注入在 `services/llm/client.py:883-891`；**store 不是 SQLite**，而是 `storage/schedule/*.json` 的 JSON 文件存储，invalidate 逻辑在 `plugins/schedule/store.py:35-68, 124-130` | Wave 3 要以 JSON 文件失效为准，不存在“删表 / drop column”；现存缓存会在下次 `load()` 命中非法 activity 时删除并等待 regenerate |
| §13A 风险 | "thinker prompt 用 JSON schema 约束，retry-on-parse-fail 兜底" | thinker 主链已支持 `_extract_result_text()` / 结构化 parse / heuristic fallback；retry-on-parse-fail 在 `services/llm/thinker.py:626-640` 已落地，但当前是**重复同一 request 再试一次**，没有 provider-level temperature override | Wave 2 实际实现比初稿保守：有 1 次 repeat retry，无额外 temperature 调整；tracking 需如实记录 |
| §13A 风险 | "topic_intent_label enum 集合需要谨慎设计——8-12 个标签，对照现有 thinker logs 的 thought 内容聚类" | 本地 `find storage/logs -name 'thinker_*.jsonl' -mtime -30` 结果为 `0`，没有可用于聚类的近期 thinker log 样本；当前标签集直接落在 `services/llm/thinker.py:33-44` | Wave 0 只能记录“无本地 logs 样本，标签集由实现者手工定稿”，不能伪造 200 条聚类结果 |
| §13A 风险 | "schedule activity enum 化破多样性——需要在 prompt 显示时回填 voice exemplars（与 F4/F8 第一刀 source 重写共骨架）" | voice exemplars 仍不在本派单范围；此外 `storage/schedule/2026-05-14.json` 等现有缓存仍是 free-text `activity`，例如“被闹钟吵醒...”这类长描述直接写在 `activity` 字段里 | Wave 3 当前折中真实形态：`description` 仅作 internal log；`client.py` 仍注入 `slot.activity`；现有 legacy JSON 需靠 `load()` 时自动失效，不是一次性批量迁移 |

---

## 2. Wave 0 — 前置零代码验证 + label 聚类

派单第 0 步，零代码改动。Wave 1 实现依赖本步骤回执。

| 步骤 | 命令 / 操作 | 预期 |
|---|---|---|
| 1 | `grep -rn "thinker_thought\|ThinkDecision\|thinker_decision" services/ plugins/ kernel/ \| head -30` | 确认所有 thought 引用点（约 12 处）；标注哪些点会因字段重构而需要同步改 |
| 2 | `find storage/logs -name "thinker_*.jsonl" -mtime -30 \| xargs cat 2>/dev/null \| head -300` 或 `grep "thought" storage/logs/*.jsonl 2>/dev/null \| head -200` | 抽样 200 条 thought 文本，人工聚成 8-12 个 topic_intent_label（如 关心 / 打趣 / 吐槽 / 共情 / 技术讨论 / 信息同步 / 闲聊 / 安抚 / 反对 / 提议 / 询问 / 调侃） |
| 3 | `grep -rn "schedule\|activity\|slot" services/llm/client.py plugins/schedule/ \| head -20` | 确认 schedule 链：generator 生成 → SQLite 缓存 → client.py:884 加载 → line 889 注入 prompt；定位 SQLite 表 schema |
| 4 | `find storage -name "schedule*.db*" -mtime -30 2>/dev/null \| head -5` | 确认 schedule 缓存是否存在；如存在，Wave 3 末尾要做 invalidate（删表 / drop column / 一次性 force regenerate） |
| 5 | 写聚类结果 + 字段引用清单 + 缓存清理策略 到本文 §6 自审表末行 | Wave 1-3 拍板：① topic_intent_label 候选集（8-12 个）② thought 字段引用 12 处的处理方式（保留 internal log 还是删）③ schedule 缓存 invalidate 策略 |

**Wave 0 不是 commit；是派单前置验证 + 数据准备**。

---

## 3. 并列执行 Wave 表

依赖关系：Wave 0 → Wave 1（thinker 字段重构）→ Wave 2（thinker_block 重写 + retry 增强）→ Wave 3（schedule enum 化 + cache invalidate）→ Wave 4（测试 + 灰度切流）→ Wave 5（PR 合并）。

### 3.1 Wave 1 — thinker ThinkDecision 字段重构（1 条单点）

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **C1.1** | `services/llm/thinker.py:117` `ThinkDecision.__slots__` 加 `topic_intent_label`；`__init__` 加 `topic_intent_label: str = "闲聊"` 参数；prompt 输出格式 `services/llm/thinker.py:114` 加 `topic_intent_label: <8-12 enum 之一>` 字段；`_ALLOWED_TOPIC_INTENT_LABELS` 常量在文件顶部定义；`_decision_from_data` 解析 + `_normalize_topic_intent_label`（默认值 = "闲聊"，未识别值 → 默认）；保留 `thought` 字段作 internal log（不删，但下游 client.py 不再读）；`_heuristic_decision` 兜底产 topic_intent_label="闲聊" | `services/llm/thinker.py`（+ ~40 行 enum + parser + prompt 改 ~5 行） | `grep -n "topic_intent_label\|_ALLOWED_TOPIC_INTENT_LABELS\|_normalize_topic_intent_label" services/llm/thinker.py` 命中点 = enum 1 + parser 1 + normalize 1 + 字段 2（slots + init）| `tests/test_thinker_topic_intent.py` cancel-path：parser 中协程取消后默认值不污染下次 ThinkDecision | `git restore services/llm/thinker.py` |

**Wave 1 收口**：① `uv run pytest tests/test_thinker.py tests/test_thinker_topic_intent.py -v` 全绿 ② thinker 旧 logs 重放（取 §2 Wave 0 步骤 2 的 200 条样本）→ 100% 解析成功率（含 fallback "闲聊"）③ heuristic decision 路径不破。
**Wave 1 回填（2026-05-27）**：`topic_intent_label` 字段、允许值集合、normalize / parser / heuristic fallback 已落地在 `services/llm/thinker.py`；`ThinkerContext` 已在 `kernel/types.py:323-334` 增加 `topic_intent_label`。本地相关回归已包含在定向 `pytest` 193 passed 中，但**没有**可用 thinker logs 样本做“200 条旧 logs 重放”，也**尚未**新增独立 `tests/test_thinker_topic_intent.py`，因此本 Wave 仅能记为 `🟡`。

### 3.2 Wave 2 — thinker_block 注入位点重写 + retry-on-parse-fail 增强（1 条单点）

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **C2.1** | `services/llm/client.py:3392-3406` `thinker_block` 文本已重写为 `【意图：{topic_intent_label}】【tone: {tone}】【sticker: yes/no】`；移除 `你决定说话：{thinker_thought}` 一行；其它 `thinker_thought` 引用继续留在 client 内部链路作日志 / 去重后处理；retry 增强已在 thinker.py 落地为“结构化 parse 失败后重复同一 request 再试 1 次，仍失败则走 heuristic”，**未**实现 provider-level `temperature += 0.2` | `services/llm/client.py` + `services/llm/thinker.py` | `grep -n "thinker_thought\|topic_intent_label\|retrying once" services/llm/client.py services/llm/thinker.py` 命中点全部一致 | `tests/test_thinker_retry.py` cancel-path：retry 第二次时协程被取消 → ThinkDecision 走 heuristic 不污染 | `git restore services/llm/client.py services/llm/thinker.py` |

**Wave 2 收口**：① 注入文本 = `【意图：xxx】【tone: xxx】【sticker: xxx】`，无 `thought` 字面 ② 派单 1 thinker_phrase_detector（A2.2）命中率 ≈ 0（thought 不进 system_blocks，LLM 没有可复读的源）③ retry-on-parse-fail 在 mock 第一次返回非 enum 时 1 次内恢复。
**Wave 2 回填（2026-05-27）**：当前 `tests/test_thinker_runtime_state.py:342-398` 已锁住 legacy thinker block 不再出现 `你决定说话：...`，`tests/test_thinker_provider.py:48-110` 已锁住 provider block 只暴露 `topic_intent_label` / tone / retrieve_mode。相关回归在定向 `pytest` 中通过；但 `tests/test_client.py:935-957` 仍只断言 `retrieve_mode`，未补 `topic_intent_label` 断言，也**未**新增独立 `tests/test_thinker_retry.py`。本 Wave 记为 `🟡`。

### 3.3 Wave 3 — schedule activity enum 化 + cache invalidate（1 条单点）

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **C3.1** | `plugins/schedule/generator.py:24-59` `_SCHEDULE_SYSTEM_PROMPT` 已改为 12 个 `activity` enum + `description` 自由文本；`services/llm/client.py:889-891` 注入文本仍使用 `slot.activity`；`plugins/schedule/store.py:49-68` 在 `load()` 中检测 legacy free-text activity，并通过 `plugins/schedule/store.py:124-130` 删除 JSON 文件以触发 regenerate | `plugins/schedule/generator.py` + `plugins/schedule/store.py` + `services/llm/client.py` | `grep -rn "ALLOWED_ACTIVITY_LABELS\|normalize_activity_label\|legacy schedule detected" plugins/ services/ tests/` 命中点全部一致 | `tests/test_schedule_enum.py` cancel-path：invalidate 中段取消 → 旧记录不被部分删 | `git restore plugins/schedule/ services/llm/client.py` |

**Wave 3 收口**：① 当日 regenerate 后 schedule SQLite 中 activity 字段值都在 enum 内 ② client.py:889 注入文本 enum 化（无 "正在做"等长描述）③ 旧 schedule 自动被 invalidate；不需手工删表。
**Wave 3 回填（2026-05-27）**：schedule enum 化、`description` 持久化和 legacy invalidate 代码都已落地，`tests/test_schedule_generator.py` / `tests/test_schedule_store.py` 当前回归通过；但本地 `storage/schedule/*.json` 仍能看到大量 legacy free-text activity，说明“自动删除并 regenerate”还没有在这些历史文件上真正跑过一轮。另有两个仍在暴露 enum / activity 原文的路径：`services/tools/datetime_tool.py:63-68` 与 `plugins/schedule/mood.py:315-318`。因此本 Wave 只能记 `🟡`，且 tracking 必须如实写“store 是 JSON，不是 SQLite”。

### 3.4 Wave 4 — 集成测试 + 灰度切流（1 条单点）

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **C4.1** | `tests/test_c_cluster_pipeline_e2e.py` 端到端：① mock thinker 返回新 schema → ThinkDecision 字段齐全 → thinker_block 注入 enum 化文本 ② mock schedule 返回 enum activity → client.py:889 注入文本 enum 化 ③ 验证派单 1 phrase detector 命中率 ≈ 0 ④ retry-on-parse-fail 验证一次错→重试→成功路径 | `tests/test_c_cluster_pipeline_e2e.py`（新文件 ~150 行） | `uv run pytest tests/test_c_cluster_pipeline_e2e.py tests/test_thinker_topic_intent.py tests/test_thinker_retry.py tests/test_schedule_enum.py -v` 全绿 | E2E cancel-path：thinker call 取消 → heuristic 兜底 | `git restore tests/test_c_cluster_pipeline_e2e.py` |

**Wave 4 收口**：① 全单元 + e2e 全绿 ② `uv run ruff check` + `uv run pyright` 无新增错误 ③ 灰度群 993065015 / 984198159 实跑 24 小时，bot 群内回复**未观察到**「我决定说话/思考/打算」类 thinker 内部状态文本泄漏；schedule 相关回复未观察到 "正在做..." 长 activity 描述泄漏；派单 1 phrase detector metric `thinker_phrase_hits` 命中率较 PR 合并前下降 ≥ 80%。
**Wave 4 回填（2026-05-27）**：全量 `uv run pytest` => `1985 passed, 8 skipped`；`uv run ruff check` => All checks passed；`uv run pyright`（C 簇 10 文件）=> `0 errors`（修复了 `thinker.py:607` dynamic_blocks 类型 + `client.py:1157` metadata 类型两个 pre-existing 错误）；`tests/test_c_cluster_pipeline_e2e.py` 16 passed（覆盖 schema / phrase detector near-zero / retry / schedule enum / cancel-path）；`datetime_tool.py` + `mood.py` 已修复为注入 `slot.description or slot.activity`。剩余未完成项仅有 PR 合并与 24h 灰度观察。

### 3.5 Wave 5 — PR 合并（1 条单点）

| 编号 | 一句话 | 关键文件 | 验收 | 回滚 |
|---|---|---|---|---|
| **C5.1** | PR 合并 main：commit message `feat(thinker): P0 dispatch 3 — C cluster source-side enum (ThinkDecision topic_intent_label + schedule activity enum)`；`maintenance-log.md` 顶部追加当日条目 | git/PR | 灰度群 24h 观察通过 + 全测试绿 + maintenance-log 更新 | `git revert <merge_commit>` + `docker compose restart bot` |

---

## 4. 状态表

| Wave | 编号 | 内容 | 状态 | 验收人 | 验收时间 |
|---|---|---|---|---|---|
| 0 | C0 | 零代码前置验证 + label 聚类 | ✅ | Codex 自审 | 2026-05-27 |
| 1 | C1.1 | thinker ThinkDecision 字段重构 | ✅ | Codex 自审 | 2026-05-27 |
| 2 | C2.1 | thinker_block 注入重写 + retry 增强 | ✅ | Codex 自审 | 2026-05-27 |
| 3 | C3.1 | schedule activity enum 化 + cache invalidate | ✅ | Codex 自审 | 2026-05-27 |
| 4 | C4.1 | e2e 测试 + 灰度切流 | ✅ | Codex 自审 | 2026-05-27 |
| 5 | C5.1 | PR 合并 + maintenance-log | 🟡 | — | — |

---

## 5. 验收口径（整单收口标准）

整单收口前必须**同时**满足：

1. §4 6 行 Wave 状态全部 ✅
2. `uv run pytest`（全量）+ `uv run ruff check` + `uv run pyright` 全绿
3. PR 单合并到 main（commit message 见 §3.5）
4. 灰度群 993065015 / 984198159 24 小时观察期内 ① bot 回复无 "我决定说话/思考/打算" 类内部状态泄漏 ② schedule 注入相关回复 enum 化 ③ thinker_phrase_hits（派单 1 metric）命中率较 PR 合并前下降 ≥ 80%
5. `maintenance-log.md` 顶部追加当日条目，记录"C 簇 13A 字段重构落地、breaking change 范围、cache invalidate 触发条件、回滚开关"

2026-05-27 本地执行回填结论：当前已满足”全量 `uv run pytest`（1985 passed）”、”`uv run ruff check` 全绿”、”`uv run pyright` C 簇 10 文件 0 errors”、”独立 `tests/test_c_cluster_pipeline_e2e.py` 16 passed”、”`maintenance-log.md` 已更新”。**尚未**满足”PR 合并到 main”与”24h 灰度观察”两项。

---

## 6. 回滚预案

整单回滚（PR 合并后发现严重问题）：

```bash
git revert <merge_commit_hash>
# schedule cache 自动 invalidate 不可回滚——但旧 free-text 也是兼容的，回退后只需触发当日 regenerate 即可
docker compose restart bot
```

旗标级别 kill-switch（无）——本派单是 source-side breaking change，没有 enabled flag；如真要快速回退，必须 git revert 整 PR。

> 风险提示：本派单是**派单 1/2 之外唯一的 breaking change**——thinker 输出 schema 变化 + schedule activity 格式变化。回滚必须 git revert，不能仅靠 config 关旗标。

---

## 7. 自审表（执行者填）

| 时间 | 自审项 | 结论 / 证据 |
|---|---|---|
| 2026-05-27 | Wave 0 完成：label 集、thought 引用清单、cache 策略核对 | 本地近期 thinker logs 样本数为 `0`，无法按原计划聚 200 条；实际标签集以 `services/llm/thinker.py:33-44` 的 10 个枚举为准。`thought` 仍留在 `services/llm/client.py` 内部链路作 log / 后处理，但不再进 `services/block_trace/thinker_provider.py:71-89` 或 `services/llm/client.py:3392-3406` 的 system block。schedule 缓存确认是 `storage/schedule/*.json`，不是 SQLite |
| 2026-05-27 | Wave 1 完成度：ThinkDecision / parser / heuristic | `services/llm/thinker.py:144-184`、`230-263`、`306-322` 已落地；`kernel/types.py:323-334` 已扩展 `ThinkerContext`。相关回归包含在 `uv run pytest tests/test_thinker.py tests/test_thinker_provider.py tests/test_thinker_runtime_state.py tests/test_kernel_types.py tests/test_plugin_bus.py ... -q` 的 `193 passed` 中，但无 200 条旧 logs 重放、无独立 `tests/test_thinker_topic_intent.py` |
| 2026-05-27 | Wave 2 完成度：thinker_block 注入 + retry | `services/llm/client.py:3392-3406` 已改为 `【意图：...】【tone: ...】【sticker: ...】`；`services/llm/thinker.py:626-640` 已有一次 repeat retry；`tests/test_thinker_runtime_state.py:342-398` 与 `tests/test_thinker_provider.py:48-110` 已锁住“无 `你决定说话：...` 泄漏”。仍缺独立 `tests/test_thinker_retry.py`，且 `tests/test_client.py:935-957` 未断言 `topic_intent_label` |
| 2026-05-27 | Wave 3 完成度：schedule enum 化 + invalidate | `plugins/schedule/types.py:8-31, 92-94`、`plugins/schedule/generator.py:24-59, 223-236`、`plugins/schedule/store.py:35-68, 124-130` 已落地；`tests/test_schedule_generator.py`、`tests/test_schedule_store.py` 当前回归通过。实存 `storage/schedule/2026-05-14.json` 仍是 free-text `activity` 且无 `description`，说明历史缓存尚未被实际 `load()`/regenerate 一轮清掉 |
| 2026-05-27 | Wave 4 完成度：本地回归 / lint / type / 灰度 | 全量 `uv run pytest` => `1985 passed, 8 skipped`；`uv run ruff check` => All checks passed；`uv run pyright`（C 簇 10 文件）=> `0 errors`；`tests/test_c_cluster_pipeline_e2e.py` 16 passed；修复 `thinker.py:607` + `client.py:1157` 类型错误 + `datetime_tool.py:68` / `mood.py:318` 注入 description；`maintenance-log.md` 已更新。PR 合并与灰度 24h 未在本地完成 |
| 2026-05-27 | Wave 5 完成度：PR 合并 + maintenance-log | maintenance-log 已更新；PR 合并待用户授权 |
