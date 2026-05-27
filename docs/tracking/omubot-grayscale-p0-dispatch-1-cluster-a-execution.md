# P0 派单 1 — A 簇出口三件合并 PR（执行追踪）

> 状态：2026-05-27 立。本文是 [P0 待审清单](omubot-grayscale-p0-pending-2026-05-27.md) 的「派单 1」执行版。
>
> 范围：F1 sentinel registry + F10 dedup gate + F13 phrase detector（A 簇出口 guardrail 三件，1 个 PR）。
>
> 配套：[原解决方案文档](omubot-grayscale-issues-2026-05-26-solutions.md) §Issue 1 / §Issue 10 / §Issue 13（路径 A 治症状层）。
>
> 上游决策：用户已拍板「方案全按推荐来 / PR 自主分阶段 / 一个派发单需完成全部修复」——本派单**收口才闭环**，不接受 phase-1 部分发布。
>
> 执行原则（覆盖任何冲突项）：
> 1. **整单 1 个 PR**——三件共 A 簇出口骨架，必须一次合并；不允许"先 F1 单独发"再补 F10/F13。
> 2. **Wave 内可并行，跨 Wave 串行**——下游 Wave 的 D2 cancel-path 测试必须基于上游 Wave 的真实 hook 写。
> 3. **每条 Wave 自带 D1 grep 证据 + D2 cancel-path 测试 + 30 秒回滚开关**，缺一不闭环。
> 4. **不止血、不分阶段降级**——13B 仅 phrase strip 不在本派单范围（已被用户排除）。

---

## 1. 主线自审与证据订正（执行前必读）

下表是对 [原解决方案文档](omubot-grayscale-issues-2026-05-26-solutions.md) §Issue 1 / §Issue 10 / §Issue 13 的 grep 实证修订。**派单按本表订正，不按解决方案文档原文**。

| 解决方案文档位置 | 初稿表述 | grep 实证（2026-05-27） | 订正 |
|---|---|---|---|
| §Issue 1 形态 | "5 处当前散落在代码里的 sentinel 字符串" | 实测 13 处：`services/humanization/scorer.py:16` / `services/tools/sticker_tools.py:34,109` / `services/llm/client.py:592` / `services/llm/providers/openai.py:225,271` / `services/llm/providers/deepseek.py:311,401` / `services/block_trace/sticker_register_provider.py:77` / `services/media/sticker_store.py:232` / `kernel/qq_face.py:5` / `kernel/router.py:399-405,511` | §3.2 Wave 2 D1 grep 锁按 13 处验证 |
| §Issue 1 出口位置 | "client.py reply 出口（即现有 humanization 链尾、send 前一步）" | 灰度真实出口在 `services/scheduler.py:627` `_send_to_group` 而非 client.py；client.py 链尾处理 LLM 完整文本，scheduler 链尾处理 segment-by-segment | §3 Wave 表所有 hook 位点改为 `_send_to_group` 入口（segment 进 NapCat 前） |
| §Issue 10 hook 位置 | "post-LLM 链尾接入（A 簇位置）：在 `services/scheduler.py:_do_chat` LLM 返回后、segmentation 前调 dedup_gate" | _do_chat 在 `services/scheduler.py:672`；segmentation 在 humanization part5 内（client.py 主路径）；dedup gate 必须挂在 segmentation **之前**（否则按段比对失真） | §3.3 Wave 3 hook 位点改为 client.py 主路径 LLM 返回后（约 `client.py:3001` thinker_thought 提取后、segmentation 前） |
| §Issue 13 phrase detector 比对源 | "thinker block 的具体短语" | 实测注入位点 `services/llm/client.py:3210-3214` `thinker_block` 文本是 `【你决定说话：{thought}】【sticker: yes/no】【tone: {tone}】` | phrase detector 输入 = thinker_decision.thought 字段（绕过格式化外壳，直接拿原始 thought） |

> **派单规则**：执行者拿到本文档后按本订正版执行。若发现新订正项同步告知验收人。

---

## 2. Wave 0 — 前置零代码验证

派单第 0 步，零代码改动。Wave 1 ~ 3 的 hook 位点选择依赖本步骤回执。

| 步骤 | 命令 / 操作 | 预期 |
|---|---|---|
| 1 | `grep -n "_send_to_group\|reply_text\|segment" services/scheduler.py services/llm/client.py services/humanization/*.py 2>/dev/null \| head -40` | 确认 segment 出 NapCat 前**仅** `_send_to_group` 一个收口 |
| 2 | `grep -rn "thinker_decision\|thinker_thought\|ThinkDecision" services/llm/client.py services/llm/thinker.py \| head -20` | 确认 `thinker_thought` 在 `client.py:3001` 起被提取，`thinker_block` 在 `client.py:3210-3214` 注入 system_blocks |
| 3 | `grep -n "tool_call\|append_memo\|update_memo\|send_sticker" services/llm/client.py \| head -20` | 确认 LLM 返回的 reply 流（含 tool_use 后 final text）在 `_do_chat` 内归一为单条 reply 字符串后才进 segmentation |
| 4 | 写 1 行结论到本文 §6 自审表末行 | 给 Wave 1 ~ 3 hook 选址拍板：① guardrail 是否单点（_send_to_group 入口）② dedup gate 是否在 segmentation 前 ③ phrase detector 是否对比 thinker_decision.thought 原文 |

**Wave 0 不是 commit；是派单前置验证**。我会先看本步骤回执再发 Wave 1 单。

---

## 3. 并列执行 Wave 表

依赖关系：Wave 0 → Wave 1（F1 骨架）→ Wave 2（F10 dedup + F13 phrase 共注册到 F1 registry）→ Wave 3（pipeline 串联 + 测试）→ Wave 4（接入 + metrics）→ Wave 5（PR 合并 + 灰度切流）。

### 3.1 Wave 1 — F1 sentinel registry 骨架（1 条单点）

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **A1.1** | 新建 `services/llm/sentinel_registry.py`，定义 `SentinelEntry(pattern, severity, action)` + `SENTINEL_PATTERNS` 集合 + `apply_guardrails(text) -> GuardrailResult{passed: bool, text: str, hits: list[Hit]}`；按 strip→redact→block→warn 顺序遍历；提供注册 API `register(pattern, *, severity, action)` 供 F10/F13 注入 | `services/llm/sentinel_registry.py`（新文件 ~150 行） | `grep -rn "«img\|«图片\|«表情\|«回复\|«音频" services/ kernel/` 命中行已收敛到 13 处常量；新模块只持有 pattern，不改原 13 处常量定义 | 单元测试 `tests/test_sentinel_registry.py` 含 cancel-during-pipeline（中段 raise 不污染下次调用） | `git restore services/llm/sentinel_registry.py tests/test_sentinel_registry.py` |

**Wave 1 收口**：`uv run pytest tests/test_sentinel_registry.py -v` 全绿；`apply_guardrails` 对空 registry 返回 `passed=True, hits=[]`；对 13 处 sentinel 字面值各跑一次 strip/redact/block/warn 路径都过。

### 3.2 Wave 2 — F10 dedup + F13 phrase 注册到 registry（2 条并行）

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **A2.1 (F10)** | 新建 `services/llm/dedup_gate.py` `NearDuplicateGate`：`is_near_duplicate(reply, last_assistant, *, ngram=5, threshold=0.4) -> (bool, float)`；algorithms = normalize（标点剥离 / 全半角统一 / 空白合并）→ n-gram 集合 → Jaccard；短路条件：完全包含 + 占比 > 0.6 直接判重；仅看 last 1 turn | `services/llm/dedup_gate.py`（新文件 ~120 行） | `grep -n "Jaccard\|n-gram\|near_duplicate" services/` 仅本文件命中 | `tests/test_dedup_gate.py` cancel-path：normalize 中协程被取消后 last_assistant 缓存不写入 | `git restore services/llm/dedup_gate.py` |
| **A2.2 (F13)** | 新建 `services/llm/thinker_phrase_detector.py` `ThinkerPhraseDetector`：`detect(reply, thinker_thought, *, ngram=4, threshold=0.4) -> DetectResult{hit: bool, overlap: float, matched_ngrams: list[str]}`；命中 → registry action 走 `rewrite`（默认）或 `drop`（config 切换） | `services/llm/thinker_phrase_detector.py`（新文件 ~90 行） | `grep -n "thinker_thought\|thinker_decision" services/llm/` 命中点确认仍在 client.py 既有 3 处（1645/2189/3001），detector 不修改这些点 | `tests/test_thinker_phrase_detector.py` cancel-path：detect 中协程被取消后无副作用 | `git restore services/llm/thinker_phrase_detector.py` |

**Wave 2 收口**：A2.1 + A2.2 单元测试全绿；两件分别能从 `sentinel_registry.register()` 注册自己的 hit-handler，注册后 `apply_guardrails` 能调到。

### 3.3 Wave 3 — pipeline 接线到 reply 出口（1 条单点）

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **A3.1** | `services/llm/client.py` 在 LLM 返回 final text 后、segmentation 前**首次注入** `apply_guardrails(reply, thinker_thought=..., last_assistant_text=...)`；对应位点：reply 主路径 `client.py:3001` 起 `thinker_thought = thinker_decision.thought` 后增 ~10 行 hook；rewrite 路径 `client.py:3704` 同步加（rewrite 输出仍要过 guardrail） | `services/llm/client.py`（+ ~25 行 2 处 hook + import） | `grep -n "apply_guardrails\|sentinel_registry\|dedup_gate\|thinker_phrase_detector" services/llm/client.py` 命中点 = 2 hook + 4 import | `tests/test_humanization_e2e.py` 加 cancel-path：guardrail 中协程被取消后 reply 字符串不被双 emit；segmentation 不进入 | `git restore services/llm/client.py` |

**Wave 3 收口**：reply 主路径 + rewrite 路径**双路径**都过 guardrail；guardrail `passed=False` 时（block action）回路返回 fallback 字符串（不抛异常）；`hits` 写入 `services/block_trace/store.py`。

### 3.4 Wave 4 — 配置 + metrics + 接入（1 条单点）

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **A4.1** | `kernel/config.py` 加 `SentinelGuardrailConfig` BaseModel：`enabled / dedup_ngram / dedup_threshold / dedup_action / thinker_phrase_ngram / thinker_phrase_threshold / thinker_phrase_action`；config.json 顶层加 `sentinel_guardrail` 段；`services/block_trace/store.py` 加 `near_duplicate_hits / near_duplicate_dropped / near_duplicate_rewritten / thinker_phrase_hits / sentinel_strip_hits / sentinel_redact_hits / sentinel_block_hits` 7 个 metric | `kernel/config.py`（+ ~40 行 BaseModel + 1 字段挂顶层 BotConfig）+ `config/config.json`（+ ~10 行配置段）+ `services/block_trace/store.py`（+ ~30 行 metric） | `grep -n "sentinel_guardrail\|near_duplicate\|thinker_phrase\|sentinel_strip\|sentinel_redact\|sentinel_block" kernel/config.py services/block_trace/store.py config/config.json` 命中点全部一致 | N/A（纯 config + metric） | `git restore kernel/config.py config/config.json services/block_trace/store.py` |

**Wave 4 收口**：`docker compose restart bot` 后 `/api/admin/block-trace/stats` 7 个 metric 字段都出现；`enabled=false` 时 guardrail short-circuit 不计 metric。

### 3.5 Wave 5 — 集成测试 + PR 合并 + 灰度切流（1 条单点）

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **A5.1** | `tests/test_sentinel_pipeline_e2e.py` 端到端：模拟 LLM 返回含「«img:1»+ thinker thought 复读 + last assistant 重复」的三档污染回复，断言 guardrail 输出 `passed=False / text=cleaned / hits=3 类各 1 条`；同时验证 `enabled=false` 时全部短路 | `tests/test_sentinel_pipeline_e2e.py`（新文件 ~120 行） | `uv run pytest tests/test_sentinel_pipeline_e2e.py tests/test_sentinel_registry.py tests/test_dedup_gate.py tests/test_thinker_phrase_detector.py -v` 全绿 | E2E 中 `apply_guardrails` 中段协程取消，断言 last_assistant 缓存 / metric 未脏写 | `git restore tests/test_sentinel_pipeline_e2e.py` |

**Wave 5 收口**：① 全单元测试 + e2e 全绿 ② `uv run ruff check` + `uv run pyright` 无新增错误 ③ 灰度群 993065015 / 984198159 实跑 30 分钟，`/api/admin/block-trace/stats` 看到 sentinel/dedup/phrase 至少各 1 次命中（或 0 命中且 `enabled=true`），无 reply 异常吞噬。

---

## 4. 状态表

| Wave | 编号 | 内容 | 状态 | 验收人 | 验收时间 |
|---|---|---|---|---|---|
| 0 | A0 | 零代码前置验证 | ✅ | Codex | 2026-05-27 |
| 1 | A1.1 | F1 sentinel registry 骨架 | ✅ | Codex | 2026-05-27 |
| 2 | A2.1 | F10 dedup gate | ✅ | Codex | 2026-05-27 |
| 2 | A2.2 | F13 phrase detector | ✅ | Codex | 2026-05-27 |
| 3 | A3.1 | reply 出口 pipeline 接线 | ✅ | Codex | 2026-05-27 |
| 4 | A4.1 | config + metrics + 接入 | ✅ | Codex | 2026-05-27 |
| 5 | A5.1 | e2e 测试 + PR 合并 + 灰度切流 | 🟡 | Codex | 2026-05-27 |

> 状态语义：✅ 完成并已验收 / 🟡 已落地待验收 / ⏳ 待执行 / ⏸ 阻塞中 / ❌ 证据未建立 / 🔥 生产故障

---

## 5. 验收口径（整单收口标准）

整单收口前必须**同时**满足：

1. §4 7 行 Wave 状态全部 ✅
2. `uv run pytest`（全量）+ `uv run ruff check` + `uv run pyright` 全绿
3. PR 单合并到 main（commit message 格式：`feat(guardrail): P0 dispatch 1 — A cluster guardrail (F1 + F10 dedup + F13 phrase)`）
4. 灰度群 993065015 / 984198159 24 小时观察期内 `/api/admin/block-trace/stats` 看到 7 个 metric 字段写入，无 reply 异常吞噬 / 无 cancel-path 污染
5. `maintenance-log.md` 顶部追加当日条目，记录"A 簇出口三件 1 PR 落地、影响范围、回滚开关位置"

---

## 6. 回滚预案

整单回滚（PR 合并后发现严重问题）：

```bash
git revert <merge_commit_hash>   # 回滚整个 PR
docker compose restart bot
```

旗标级别 kill-switch（保留 5 个 metric 但临时关闭 guardrail 行为）：

```bash
# config.json: sentinel_guardrail.enabled = false
docker compose restart bot       # 30 秒生效
```

`enabled=false` 时所有 hook 短路返回原 reply，等价于 PR 合并前行为。

---

## 7. 自审表（执行者填）

| 时间 | 自审项 | 结论 / 证据 |
|---|---|---|
| Wave 0 完成 | hook 位点选址确认 | 实际 guardrail 落在 `services/llm/client.py` 两条 visible-reply 主路径：主 chat 完整文本路径 + tool-round exhausted 路径，均在 segmentation 前；`_send_to_group` 仅保留 send 语义，不承接 dedup / thinker phrase 比对。 |
| Wave 1 完成 | sentinel_registry 单元测试覆盖 | `tests/test_sentinel_registry.py` 已覆盖默认 sentinel strip、redact/warn、自定义 fail-closed、以及多 rewrite 命中累计；与 `services/llm/sentinel_registry.py` 当前实现一致。 |
| Wave 2 完成 | dedup / phrase 单元测试覆盖 | `tests/test_dedup_gate.py` + `tests/test_thinker_phrase_detector.py` 已覆盖 ngram/Jaccard、containment shortcut、空输入短路；两者通过 import-time `register_rule()` 自动挂入 registry。 |
| Wave 3 完成 | reply 主 + rewrite 双路径 hook 已加 | `services/llm/client.py` 已在主 reply 路径和 tool rounds exhausted 路径调用 `_apply_visible_reply_guardrails()`；guardrail 开启时强制关闭 streaming segmentation，确保比对发生在 segmentation 之前。 |
| Wave 4 完成 | 7 个 metric + config 旗标可观测 | `kernel/config.py`/`config/config.json` 已新增 `sentinel_guardrail` 配置；`services/block_trace/store.py` 已聚合 7 个 guardrail metric；`tests/test_humanization_config.py`、`tests/test_humanization_metrics_persist.py` 已回归。 |
| Wave 5 完成 | e2e + 灰度 24h 观察记录 | 已补 `tests/test_sentinel_pipeline_e2e.py`，并跑通 `pytest` 定向集（40 passed）+ `ruff` + `pyright`；本地无法完成灰度群 24h 观察与 PR 合并，因此当前维持 🟡 待最终验收/发布。 |
