# Persona GroupOverride 完整迁移执行追踪

> 状态：完整 GroupOverride 迁移 dry-run 已完成
> 启动时间：2026-05-24
> 执行人：Codex
> 上游：[persona-part-b-main-execution.md](./persona-part-b-main-execution.md)、
> [persona-s12-parity-audit-execution.md](./persona-s12-parity-audit-execution.md)
> 参考格式：[persona-source-importer-remediation-execution.md](./persona-source-importer-remediation-execution.md)

---

## 0. 执行规则

1. 每步先写：细分动作、风险、回滚方式、验收证据；再回填实际改动 / 验证 / 遗留风险。
2. 本文只处理 `kernel.config.GroupOverride` 全部 15 个字段：blocked_users、allowed_tools、blocked_tools、at_only、talk_value、planner_smooth、debounce_seconds、batch_size、history_load_count、reply_style、custom_prompt、tools_enabled、sticker_mode、slang_enabled、presence_mode。
3. 仍是 importer/compiler dry-run：不读取生产 `BotConfig`，不写入正式 `runtime.yaml`，不接 `LLMClient` / `GroupChatScheduler` / `ToolRegistry` runtime；切流由 S12'+ 单独立项。
4. 字段语义对齐 v1：取 None 表示沿用全局；source 写出值即视为本群覆盖。
5. parity audit 暂只覆盖 `reply_style/custom_prompt`；本轮新增字段先以 importer/compiler dry-run 落点为目标，parity 后续切片再扩展。

---

## 1. 步骤总览

| 步骤 | 名称 | 状态 | 负责人 | 完成证据 |
|---|---|---|---|---|
| F0 | 范围确认 + 执行文档立项 | ✅ 完成 | Codex | 本文 |
| F1 | importer 扩展 `group_profiles` 全字段解析 | ✅ 完成 | Codex | `pytest` + `ruff` |
| F2 | compiler dry-run 输出全字段摘要 | ✅ 完成 | Codex | `pytest test_persona_compiler` 通过 |
| F3 | 文档闭环（migrations §10 + maintenance-log） | ✅ 完成 | Codex | doc diff |

---

## 2. 字段 → 落点 → 校验

| 字段 | 类型 / 取值 | runtime.yaml 落点 | importer 处理 |
|---|---|---|---|
| blocked_users | list[int] | `per_group_overrides.<gid>.blocked_users[]` | int normalize；非 list/数字 → warn，丢弃整字段 |
| allowed_tools | list[str] \| null | `per_group_overrides.<gid>.allowed_tools[]` | strip 空字符串；空列表保留 |
| blocked_tools | list[str] \| null | `per_group_overrides.<gid>.blocked_tools[]` | 同 allowed_tools |
| at_only | bool \| null | `per_group_overrides.<gid>.at_only` | 接受 bool；非 bool → warn，丢弃 |
| talk_value | float \| null | `per_group_overrides.<gid>.talk_value` | 转 float；非数字 → warn，丢弃；不再做范围限制（v1 自身没强约束） |
| planner_smooth | float \| null | `per_group_overrides.<gid>.planner_smooth` | 同 talk_value |
| debounce_seconds | float \| null | `per_group_overrides.<gid>.debounce_seconds` | 同 talk_value |
| batch_size | int \| null | `per_group_overrides.<gid>.batch_size` | 转 int；非整数 → warn，丢弃 |
| history_load_count | int \| null | `per_group_overrides.<gid>.history_load_count` | 同 batch_size |
| reply_style | `default/gentle/playful/concise/energetic/steady` | 同前 | 枚举校验；不合法 → warn，丢弃 |
| custom_prompt | str | 同前 | strip；空字符串 → 丢弃 |
| tools_enabled | bool \| null | 同 at_only | 同 at_only |
| sticker_mode | `inherit/off/rarely/normal/frequently` | 同 reply_style | 枚举校验 |
| slang_enabled | bool \| null | 同 at_only | 同 at_only |
| presence_mode | `active/silent_learn/off` | 同 reply_style | 枚举校验 |

---

## 3. 执行日志

### F0 范围确认 + 执行文档立项

**开始前拆分**

1. 直读 `kernel/config.py::GroupOverride` 与 `ResolvedGroupConfig`，确认 15 个字段及类型。
2. 直读 `services/persona/builder.py::_frontmatter_group_profiles()` / `_extract_group_profiles()`，确认现状只接 `reply_style/custom_prompt`。
3. 直读 `services/persona/compiler.py::_group_profile_text()`，确认 dry-run group_profile block 只渲染两字段。
4. 直读 `tests/test_persona_importer.py::test_persona_importer_maps_group_profiles_to_runtime_overrides` 与 `tests/test_persona_compiler.py::test_compile_persona_dry_run_includes_group_profile_block`，确认现有断言形态。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 误把 GroupOverride 全量字段当做正式 runtime 切流 | 高 | 文档全程标 importer/compiler dry-run；不改 `kernel.config` / `LLMClient` / `GroupChatScheduler` |
| 字段 None vs 缺省混淆 | 中 | importer 写值即非空；缺省字段不出现在 draft（保留 v1 “None=沿用全局”语义） |
| 枚举值漂移 | 中 | 校验时复用 `kernel.config` 枚举常量字面集合 |
| YAML 数字（debounce_seconds=5）转 float 后改变类型 | 低 | 数值字段统一转 float / int，落 draft；compiler 摘要再做 str |

**回滚方式**

- 删除本文 + builder/compiler 改动 + 新增测试 + migrations §10 + maintenance log 当日条目即可；reply_style/custom_prompt 既有最小迁移不动，回到 §8 状态。

**验收证据**

- 本文落地。

**完成后回填**

- 实际改动：本文 §0–§2 落地。
- 验证证据：通过 Read 已对照源代码确认 §2 字段类型表。
- 遗留风险：F1–F3 尚未执行。

### F1 importer 扩展 `group_profiles` 全字段解析

**开始前拆分**

1. 在 `services/persona/builder.py` 新增 `_GROUP_PROFILE_FIELDS`、`_GROUP_REPLY_STYLE_VALUES`、`_GROUP_STICKER_MODE_VALUES`、`_GROUP_PRESENCE_MODE_VALUES`（与 `kernel.config` 对齐）。
2. 重写 `_frontmatter_group_profiles()`：按字段分类 (str/bool/float/int/enum/list[int]/list[str])，每字段独立 `_coerce_*()`；非法值写 `warn` issue，issue 字段定位到 `per_group_overrides.<gid>.<field>`，并丢弃该字段。
3. `_extract_group_profiles()` 写 report：每个字段独立 ReportField；保留 `source=source_front_matter`。
4. 不引入 admin SPA / API 变化；不改 `_extract_part_b_overrides`。
5. 调整既有最小测试断言，覆盖 reply_style/custom_prompt 仍然兼容。
6. 新增测试：
   - 全字段 happy path
   - 枚举非法值 → warn issue 且字段不进 draft
   - bool/float/int 非法值 → warn
   - list[int] / list[str] 类型保护（含元素 strip）
   - 多组共存
   - report fields 行号正确

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| issue 多到主导 importer report | 中 | 仅在显式提供字段时校验；缺省不报错 |
| 误把 v1 行为复刻到 draft（如 access 侧效应） | 高 | 严格按 GroupOverride 字段表，不投影 access/whitelist |
| YAML 数字精度漂移 | 低 | float/int 转换后写 draft |

**回滚方式**

- 还原 builder.py、tests 修改，文档对应小节；不影响 #8 已迁移最小集。

**验收证据**

- `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_importer.py -q`
- `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check services/persona tests/test_persona_importer.py`

**完成后回填**

- 实际改动：
  - `services/persona/builder.py` 新增枚举常量 `_GROUP_REPLY_STYLE_VALUES` / `_GROUP_STICKER_MODE_VALUES` / `_GROUP_PRESENCE_MODE_VALUES` 与字段处理表 `_GROUP_PROFILE_FIELD_KINDS`，并把 `_frontmatter_group_profiles()` 重写为分类型 coerce + 非法值 warn issue，校验失败的字段被丢弃且不入 draft。
  - `_extract_group_profiles()` 现在为 15 字段中实际命中的每个字段补 ReportField，extractor 仍是 `front_matter_group_profiles`。
  - `tests/test_persona_importer.py` 新增 `test_persona_importer_maps_group_profiles_full_field_set` 覆盖全字段 happy path、枚举/非数值/非 list 非法值落 warn、bool/数值类型守卫与多组共存。
- 验证证据：
  - `pytest tests/test_persona_importer.py -q` 通过；`ruff check services/persona tests/test_persona_importer.py` 通过。
- 遗留风险：F2 compiler 摘要未改，draft 已含全字段但 dry-run group_profile block 仍只渲染 reply_style/custom_prompt。

### F2 compiler dry-run 输出全字段摘要

**开始前拆分**

1. `services/persona/compiler.py::_group_profile_text()` 改为按固定顺序渲染 15 字段 + `source` token；空值跳过。
2. block `position` 仍为 `stable`；不引入新的 module_id。
3. 测试：扩展 `tests/test_persona_compiler.py::test_compile_persona_dry_run_includes_group_profile_block`，新增覆盖全字段断言；保留旧断言。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| compiler 文本里出现空 `key=` | 低 | 空值跳过 |
| 行序不稳定导致测试 flaky | 低 | 使用固定字段顺序 |

**回滚方式**

- 还原 compiler 段 + 测试。

**验收证据**

- `pytest tests/test_persona_compiler.py -q`
- `ruff check services/persona tests/test_persona_compiler.py`

**完成后回填**

- 实际改动：
  - `services/persona/compiler.py` 把 `_group_profile_text()` 改为按固定顺序渲染 15 个字段 + 内部 `source` token，None / 空字符串 / 空 list 自动跳过；block 仍 `runtime.group_profile / position=stable`。
  - 列表渲染统一 `field=[a,b]`，bool/数值统一 `field=value`，与 admin SPA 后续 parity 视图对齐。
  - `tests/test_persona_compiler.py` 新增 `test_compile_persona_dry_run_renders_full_group_override_fields` 覆盖整组字段；既有最小集断言保留兼容。
- 验证证据：
  - `pytest tests/test_persona_compiler.py -q` 通过（6 passed）；`ruff check services/persona tests/test_persona_compiler.py` 通过。
- 遗留风险：parity audit 仍未把新字段纳入比较，留给 S12'+ 单独切片。

### F3 文档闭环

**开始前拆分**

1. `docs/migrations/persona-v2-importer.md` 追加 §10 “GroupOverride 完整迁移 dry-run”：旧入口 / 新落点 / status，明确 parity audit 还没扩展。
2. `maintenance-log.md` 顶部追加 2026-05-24 GroupOverride 完整迁移条目（五段齐）。
3. 自审：`git diff --check` / `git status --short` / `git diff --name-status`。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 文档误写正式切流 | 高 | 全程 dry-run / 只 importer+compiler 字样 |
| 工作树并行改动被误带 | 中 | 提交时显式列文件 |

**回滚方式**

- 撤销 §10 + maintenance log 当日条目；本文删除。

**验收证据**

- `pytest tests/test_persona_importer.py tests/test_persona_compiler.py tests/test_persona_parity_audit.py tests/test_persona_importer_api.py tests/test_system_module.py -q`
- `ruff check services/persona tests/test_persona_importer.py tests/test_persona_compiler.py tests/test_persona_parity_audit.py tests/test_persona_importer_api.py`
- `git diff --check`

**完成后回填**

- 实际改动：
  - `docs/migrations/persona-v2-importer.md` 追加 §10 “GroupOverride 完整迁移 dry-run” 表，列 15 字段映射 + parity follow-up + 切流仍未做。
  - `maintenance-log.md` 顶部追加 2026-05-24 “Persona GroupOverride 完整迁移 dry-run” 条目。
  - 本文状态切完成。
- 验证证据：
  - `pytest tests/test_persona_importer.py tests/test_persona_compiler.py tests/test_persona_parity_audit.py tests/test_persona_importer_api.py tests/test_system_module.py -q` 通过。
  - `ruff check ...` 通过。
  - `git diff --check` 无空白错误。
- 遗留风险：
  - parity audit 与 admin SPA 需要在后续切片把新增 13 个字段纳入比对/视图。
  - 切流仍未做；运行时仍走 v1 `BotConfig` / `LLMClient` / `GroupChatScheduler`。
