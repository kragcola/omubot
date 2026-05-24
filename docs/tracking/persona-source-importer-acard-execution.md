# Persona Source Importer A 档 dry-run 扩展执行追踪

> 状态：2026-05-24 立项；A1 ↔ A5 顺序串联，全部归口 dry-run。`PromptBuilder` / `LLMClient` / `GroupChatScheduler` / `kernel.config.GroupOverride` / admin Soul SPA 编辑入口本轮**全部不动**。
>
> 上游：[Persona Source Importer 主战场总览](persona-source-importer.md)、[Persona Source Importer Remediation 执行追踪](persona-source-importer-remediation-execution.md)。
>
> 旁支（已落地）：
> - [persona-s12-parity-audit-execution.md](persona-s12-parity-audit-execution.md)
> - [persona-group-override-full-execution.md](persona-group-override-full-execution.md)
> - [persona-legacy-instruction-md-execution.md](persona-legacy-instruction-md-execution.md)

---

## 0. 范围与护栏

A 档全部为 **dry-run / 离线比对 / admin SPA only**：

- 不写正式 runtime 配置，不读 `storage/*.db*`，不替换 `BotConfig` / `PromptBuilder` / `LLMClient` 任何路径
- `config/persona/*/.draft/`、`source.frozen.md`、`_pending_freeze/` 仍受 .gitignore 物理护栏（D7）
- 任何 importer / compiler dry-run 字段必须显式声明：来源、缺失时行为、是否进入 prompt block
- `parity_audit` 仅供离线读，不写 `_import_report.json`，不进入 `LLMRequest`
- D6：admin/static 是 bind mount——前端只跑 `npm run build`，不需要 docker rebuild
- D5：跑全量 pytest 前 `pkill -9 -f pytest`
- 用户未显式指示前不做 commit / push / build / deploy

---

## 1. A 档子任务编号与依赖

| 编号 | 任务 | 依赖 | 关键产物 |
|---|---|---|---|
| A1 | parity audit 扩展到 GroupOverride 全部 15 字段 | — | `services/persona/parity_audit.py` GroupOverrideSnapshot 扩到 15 字段；新增 `v2_extended` 状态 |
| A2 | admins / proactive_rules source schema + compiler block | — | source §X / front matter 扩展；adapter.yaml.permissions / persona.yaml.identity.proactive；compiler `core.identity` / `core.guard` 注入 |
| A3 | `/api/admin/persona/parity/{id}` + SPA 视图 | A1 + A2 | `admin/routes/api/persona_importer.py`、`PersonaImporterView.vue` 新增 Parity 折叠面板 |
| A4 | S10' importer issue 双栏点击行号自动滚动高亮 | — | `PersonaImporterView.vue` 双栏联动 + textarea/code mirror 跳转高亮 |
| A5 | 回填主执行文档 §2 总览表 + E-L 段 | A1-A4 全部落地 | `persona-source-importer-remediation-execution.md` §2 增补 |

A1 / A2 / A4 之间无强依赖，可并行；A3 必须在 A1+A2 落定后再写视图，避免视图字段漂移。

---

## 2. A1 — Parity audit 扩展到 GroupOverride 全部 15 字段

### 2.1 现状

- `services/persona/parity_audit.py::GroupOverrideSnapshot` 仅承载 `reply_style` / `custom_prompt`（v1 `_build_group_profile_block` 实际只渲染这两项的 hint+块）
- `_evaluate_group_profile()` 只锚 `reply_style={...}` / `custom_prompt={...}` 两个 substring
- v2 compiler `_GROUP_PROFILE_FIELD_ORDER` 已渲染全部 15 字段（`presence_mode/at_only/talk_value/planner_smooth/debounce_seconds/batch_size/history_load_count/reply_style/custom_prompt/tools_enabled/allowed_tools/blocked_tools/sticker_mode/slang_enabled/blocked_users` + `source` token）

### 2.2 设计决策

**核心约束**：v1 prompt 只输出 2 字段，v2 draft / compiler 已承载 15 字段。直接对比"v1 prompt vs v2 prompt"会让 13 字段永远 `v1_only`。需要新增第 6 个 status：`v2_extended` 表示"v2 已 dry-run 输出，v1 未在 prompt block 暴露但已通过 BotConfig.group.overrides 消费"——等价于 D3 同迁移路径上的"v1 已读取但不写 prompt"分类。

**实现拆分**：

1. `GroupOverrideSnapshot` 增 13 字段，全部默认 `None`，类型用 `Any | None` 减少 import 阴影；保留两个老字段位置不变保证调用方兼容
2. `ParityStatus` Literal 增 `"v2_extended"`
3. `ParityAxis` Literal 增 `"group_profile.fields"`，老 `"group_profile"` 保持原语义（prompt-block 锚点：reply_style/custom_prompt）
4. `_evaluate_group_profile()` 不动（行为兼容）；新增 `_evaluate_group_profile_fields()` 聚合 finding：
   - snapshot 13 字段全 None → 跳过，不进入 findings（保持 happy path test 6 axes 不变）
   - 有非 None 字段 → 用与 compiler 相同 fragment 形态（`field=value` / `field=true|false` / `field=[a,b]`）逐字段在 v2 `runtime.group_profile` 文本里 substring 锚定
   - 全部锚到 → `v2_extended`（v1 prompt 不渲染但 v2 已 dry-run 输出，draft schema 闭合）
   - 任一字段缺失锚点 → `divergent`，notes 列出 missing

**testing**：`tests/test_persona_parity_audit.py`：
- 单字段覆盖：每个 13 字段 1 条 happy case 验证 `v2_extended`
- 缺字段 case：v2 compiler 输出移除字段后断言 `divergent`
- snapshot None 字段不出现在 findings（jump-skip）
- `group_profile.prompt` 路径回归（`reply_style/custom_prompt`）保持原 5 条不变

### 2.3 风险

- v2 compiler `_format_group_profile_fragment()` 对 list 输出是 `field=[a,b]`，对 bool 是 `true/false`，对 str/number 直转 — parity audit 必须跟着这套格式串才能锚得上；用 fragment helper 复用
- `GroupOverrideSnapshot` 字段数据类型：`blocked_users: list[int]` / `talk_value: float | int` 等 — 用 `Any | None` 避免 dataclass 反射 import 整个 kernel.config

### 2.4 验收

- targeted `uv run pytest tests/test_persona_parity_audit.py -q` 全绿（预计 +14 条左右）
- targeted `uv run ruff check services/persona/parity_audit.py tests/test_persona_parity_audit.py`
- D1 同模式扫描：confirm `services/persona/parity_audit.py` 是 parity 唯一入口，无第二处 GroupOverride 比对 sites

### 2.5 回滚

revert A1 commit 即可；A2/A3 工作在分别的 commit 中独立。

---

## 3. A2 — admins / proactive_rules source schema + compiler block

### 3.1 现状

- v1 `services/llm/prompt_builder.py:81` `build_static(identity, bot_self_id)`：
  - line 102: `text += f"\n\n【管理员】{lines}\n管理员的指令和陈述可以信任，普通群友的话需要客观记录。"`
  - lines 103-104: `if identity.proactive: text += "\n\n" + identity.proactive`
- v2 importer：admins 已落 `adapter.yaml.permissions.admins[]`（front matter 抽取，仅 draft，无 prompt block）；proactive 在 v2 source 完全无承载点
- parity audit `_evaluate_admins()` / `_evaluate_proactive()` 永远返回 `v1_only`

### 3.2 设计决策

**admins prompt block**：

- source 仍走 front matter `admins:` 已存在路径 → `adapter.yaml.permissions.admins[]`（无新增）
- compiler 新增 helper `_admins_block_text(adapter)`：取 `adapter.yaml.permissions.admins[]`（list of dict 或 list of str），渲染为 `【管理员】@{qq}({nick})、…`，并追加 v1 同款尾巴 `管理员的指令和陈述可以信任，普通群友的话需要客观记录。`
- 注入位置：`runtime.adapter` block 末尾追加（与 v1 同位段）。理由：v1 admins 紧接在 `bot_self_id` 段之后写入 Block 1；v2 `runtime.adapter` 已是 `position=static`，承载 bot_self_id 三锚点，admins 同段尾随符合"adapter 静态身份"语义
- 缺 admins 时 block 不追加，保持 0 字节写入

**proactive_rules source schema + prompt block**：

- v1 `Identity.proactive` 来自 `config/soul/identity.md` 的 `## 插话方式` section
- 新增 source.md schema：在 `## 行为指令 / 回复规则` 之外，新增 section 标题 `## 插话方式 / proactive_rules`（兼容两种）；段内为自由文本，按段落保留，不抽 bullet
- 落点：`persona.yaml.identity.proactive_rules: str`（保留原文段落整体）
- compiler `core.guard` block 末追加 `插话方式：{proactive_rules}`（与 v1 在 Block 1 末尾追加同样位置语义对齐）。理由：v1 proactive 拼在 instruction 之后，v2 `core.guard` 已承载 `行为指令：…`，proactive 是 guard 类规则的延伸
- extractor 标 `proactive_rules_md_section`，confidence=0.9
- 缺该 section 时 source extractor 跳过，compiler block 不追加

**parity audit**：

- `_evaluate_admins()`：从 `compile_result.prompt_blocks` 找 `runtime.adapter`，断言含 `【管理员】@{qq}` 与尾巴 `普通群友的话需要客观记录`，aligned；缺时 `divergent`
- `_evaluate_proactive()`：从 `core.guard` 找 `插话方式：{首行}`，aligned；缺时 `divergent`

### 3.3 风险

- v1 admins lines 字符串拼接会用 `、` 分隔，v2 compiler 输出格式必须严格对齐 v1 才能让 parity 用 substring 锚住。用首位 admin 的 `@{qq}({nick})` 作为锚点，避免严格全列表序锚
- `## 插话方式` 在 v1 是 H2，v2 source 也走 H2；parser 已能 by-section 切；不引入新 H1
- `_extract_proactive_rules` 在 builder 中新增；与已有 `_extract_behavior_instructions` 平行，复用 `find_section()` helper

### 3.4 验收

- targeted `uv run pytest tests/test_persona_importer.py tests/test_persona_compiler.py tests/test_persona_parity_audit.py -q`：admins / proactive 各 +3~4 条 happy/边界回归
- ruff / pyright（如有 type stub）通过
- D1 同模式扫描：proactive_rules 在 v1 仅一处 `prompt_builder.py:103-104` 注入；admins 在 v1 仅一处 `prompt_builder.py:102`；新增 v2 落点不重复挂钩 `LLMClient`

### 3.5 回滚

revert A2 commit；A1/A3 commit 不受影响。

---

## 4. A3 — `/api/admin/persona/parity/{id}` + SPA 视图

### 4.1 现状

- `admin/routes/api/persona_importer.py` 现有 5 个端点：`POST /import`、`GET/PUT /source/{id}`、`GET /draft/{id}`、`POST /freeze/{id}`
- `PersonaImporterView.vue` 现有 4 视图：Source 编辑、Issues、Fields、Files；无 parity 入口

### 4.2 设计决策

**API**：

- 新增 `GET /api/admin/persona/parity/{persona_id}`
  - 入参：`persona_id`（与 import 同样 namespace 校验）
  - 行为：调用 `compile_persona_dry_run(persona_id)` 拿 `CompileResult`；从 `services.identity.load_identity()` 拿真实 v1 identity；从 `BotConfig.admins` / `BotConfig.group.overrides` **只读快照**拿 admins / 第一个 group_override 作 snapshot；调 `compare_v1_vs_v2_dry_run(...)` 返回 `ParityReport.to_dict()`
  - 严格 read-only：不写 draft，不写 freeze，不入 LLMRequest
  - 错误：draft 不存在 → `{ok: false, error: "draft not found"}`；compile errors → 透传 errors[]
- 复用 `_valid_namespace(persona_id)` 做防穿越校验

**SPA**：

- `PersonaImporterView.vue` 新增"Parity 比对"折叠面板（与 Issues/Fields/Files 同级 NCollapse 项）
- 字段：每条 finding 一行 NCard：axis chip + status NTag（aligned=success/divergent=error/v1_only=warn/v2_only=info/v2_extended=info/not_applicable=default）+ v1 / v2 双栏 + notes 行
- 顶部 NTag："has_divergence" 红 / "all aligned" 绿
- 入口：现有"运行 Import"按钮旁新增"刷新比对"按钮，调 `/parity/{id}`；首次只在 draft 已存在时启用

### 4.3 风险

- `services.identity.load_identity()` 是异步 / 同步？需读源码确认；若需 ctx-based DI 则走 `request.app.state` 路径。fallback：让 API 直接读 `config/soul/identity.md` 的 v1 路径用 `Identity.from_file()`（如有）—— 在落地前需要小读
- `BotConfig.admins` 与 `BotConfig.group.overrides` 在 admin 进程中是否已加载？通常 admin 与 bot 共享 `BotConfig`；走 `request.app.state.config` 即可
- group_override snapshot 取哪一个 group？parity 是"全局比对工具"，按 design 取**第一个有 reply_style 或 custom_prompt 的 override**作 representative；若无则 snapshot 为 None，evaluator 走 `not_applicable`
- 视图无需 D6 docker rebuild（admin/static bind mount），但需 `npm run build`

### 4.4 验收

- 新增 `tests/test_admin_api_persona_parity.py`：mock writer/identity/BotConfig，断言 200 + 字段齐全；draft 不存在路径返回 ok=false；persona_id invalid 走 400 等价
- targeted `uv run pytest tests/test_admin_api_persona_parity.py tests/test_persona_parity_audit.py -q`
- 前端：手工浏览器验证（打 http://localhost:8081/admin/persona-importer，加载已有 fengxiaomeng-v2 draft，看到 Parity 面板加载）
- ruff / vue-tsc / npm run build 通过

### 4.5 回滚

revert A3 commit（API + SPA + 测试）；A1/A2 commit 不受影响。

---

## 5. A4 — S10' importer issue 双栏点击行号自动滚动高亮

### 5.1 现状

- `PersonaImporterView.vue` Issues 视图当前显示 `source_span.lines` 文本（如 `lines 12-15`）
- 不能跳到 source 编辑器对应行
- 上游 `persona-v2-importer.md §4` S10' 行：`当前只显示 source_span 文本，不做自动滚动定位 ⏳ 后续`

### 5.2 设计决策

- Issues 列表每条行号 chip 改为 NButton（quaternary tiny），点击后：
  - 在左栏 source textarea 里通过 `el.scrollTop = (lineNo - 1) * lineHeight - bufferRows*lineHeight` 滚动
  - 同时 select 该范围（用 `setSelectionRange()` 做反白）
  - lineHeight 取 `getComputedStyle(textarea).lineHeight` 解析；fallback 24px
- 若 `source_span.lines` 是 `[start, end]` 数组 → 选中整段；单数字 → 单行
- 高亮持续 2s 后自动 deselect（用 setTimeout + clearSelection）；用户继续编辑时立即取消（focus 事件）

### 5.3 风险

- naive-ui NInput 的 textarea 实例需要 `inputRef.value?.textareaElRef`（NInput 内部 ref 路径）；要看现状是否暴露；若不暴露，加 `:input-props="{ ref: 'innerTextarea' }"` 透传 ref
- chip 点击不应该触发 source dirty / save；只读交互
- 行号定位可能漂移（如 source 已改但 Issues 还来自旧 import）—— 已有"saved 后必须 re-import 才能 freeze"约束，UI 在 source dirty 时把跳转 chip 灰掉、tooltip 提示"重新 import 后再用"

### 5.4 验收

- 手工浏览器验证：故意写错的 source.md → import → Issues 列表 → 点击行号 chip → textarea 正确滚动到行
- vue-tsc / npm run build 通过
- `docs/migrations/persona-v2-importer.md §4` S10' 行从 ⏳ 改为 ✅

### 5.5 回滚

revert A4 commit；其他工作不受影响。

---

## 6. A5 — 回填主执行文档 §2 总览表

### 6.1 范围

回填到 [persona-source-importer-remediation-execution.md](persona-source-importer-remediation-execution.md) §2 总览表，新增 4 个段落（H/I/J/K）+ 1 个汇总段（L）：

- H. S12' parity audit dry-run（旁支已落地，回填指针）
- I. GroupOverride 完整迁移 dry-run（旁支已落地，回填指针）
- J. Legacy `instruction.md` opt-in dry-run（旁支已落地，回填指针）
- K. A 档 dry-run 扩展（A1-A4 状态 + 验收证据指针）
- L. 切流前必做项（汇总）：admins/proactive prompt block 已 ✅、parity 全 axis aligned 已 ✅、SPA 视图与跳转已 ✅，剩余仅 B 档 runtime 切流（feature flag + 灰度）

主执行文档 §1 / §3 / §4 不动；只追加 §H~§L。

### 6.2 验收

- 文档 lint：表格分隔 / 链接相对路径正确（`./persona-s12-parity-audit-execution.md` 等）
- maintenance-log.md 当日条目同步追加 A 档闭环条目（五段齐：变更类型 / 内容 / 影响 / 验证 / 回滚）

---

## 7. 提交节奏

按 A1 → A2 → A3 → A4 → A5 顺序产出 5 个独立 commit。每个 commit 落地后等用户显式说"commit"才执行 `git commit`（CLAUDE.md 明确 `Only commit when user explicitly asks`）。

每个 commit message 模板：

```
feat(persona/parity): A{n} — {short title}

- {bullet 1: behavior}
- {bullet 2: tests}
- {bullet 3: docs / migrations / maintenance-log 链接}

dry-run only; PromptBuilder/LLMClient/GroupChatScheduler unchanged.
```

---

## 8. 当前状态

| 编号 | 状态 | 落地证据 |
|---|---|---|
| A1 | ✅ 已落地 (2026-05-24) | commit a0e54d1；`services/persona/parity_audit.py` GroupOverrideSnapshot 扩 15 字段 + `v2_extended` status + `group_profile.fields` axis；`tests/test_persona_parity_audit.py` 全绿 |
| A2 | ✅ 已落地 (2026-05-24) | commit a0e54d1；source `## 8.4 行为指令 / 插话方式` schema + adapter.yaml.permissions.admins / persona.yaml.identity.proactive_rules → compiler `core.identity` / `core.guard` 注入；`tests/test_persona_compiler.py` + `tests/test_persona_parity_audit.py` 覆盖 |
| A3 | ✅ 已落地 (2026-05-24) | commit 4711b4d；`admin/routes/api/persona_importer.py` GET /persona/parity/{id}；`admin/routes/api/__init__.py` 注入 identity_mgr/config/soul_dir/bot；`PersonaImporterView.vue` 新增 Parity 折叠面板；`tests/test_admin_api_persona_parity.py` 5 用例全绿；vue-tsc + npm build 通过 |
| A4 | ✅ 已落地 (2026-05-24) | `PersonaImporterView.vue` Issues/Fields 行号 chip → NButton quaternary tiny；click → focusSourceLines（textarea focus + setSelectionRange + scrollTop，buffer 3 行）；sourceDirty 时灰显并 tooltip "保存并重新导入后再跳转"；vue-tsc + npm run build 通过；`docs/migrations/persona-v2-importer.md` S10' ⏳→✅ |
| A5 | ✅ 已落地 (2026-05-24) | `docs/tracking/persona-source-importer-remediation-execution.md` §2 追加 H/I/J/K/L 行 + §9「dry-run 长尾扩展（旁支 + A 档 + 切流前清单）」段；本文 §8 状态表对齐；`maintenance-log.md` 追加「Persona A 档 dry-run 扩展 A4/A5 收口」条目 |
