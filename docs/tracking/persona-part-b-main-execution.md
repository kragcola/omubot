# Persona Part B 主战场执行追踪（#3/#4/#8）

> 状态：Part B 主战场 #3/#4/#8 已完成
> 启动时间：2026-05-24
> 执行人：Codex
> 上游：[persona-source-importer.md §15.2/§15.3](./persona-source-importer.md)
> 参考格式：[persona-source-importer-remediation-execution.md](./persona-source-importer-remediation-execution.md)

---

## 0. 执行规则

1. 每个步骤开始前先写清楚：细分动作、风险、回滚方式、验收证据。
2. 每个步骤完成后立刻回填：实际改动、验证命令或人工核对证据、遗留风险。
3. 本文只处理 #3 `soul/instruction.md`、#4 bot QQ id 提示、#8 group profile `reply_style/custom_prompt`。
4. 本阶段仍是 importer/compiler dry-run：不替换 `config/soul/*`，不接入正式 `PromptBuilder` / `LLMClient` runtime。
5. 若发现字段需要运行时切流，先记录为后续 S12'，不得在本轮偷跑。

---

## 1. 步骤总览

| 步骤 | 名称 | 状态 | 负责人 | 完成证据 |
|---|---|---|---|---|
| E0 | 范围确认与执行文档立项 | ✅ 完成 | Codex | 本文落地 |
| E1 | #3 `instruction.md` 行为指令映射 | ✅ 完成 | Codex | `17 passed` + ruff |
| E2 | #4 bot QQ id / self id 提示映射 | ✅ 完成 | Codex | `19 passed` + ruff |
| E3 | #8 group profile reply style/custom prompt 映射 | ✅ 完成 | Codex | `21 passed` + ruff |
| E4 | 综合验证、迁移清单与维护日志收口 | ✅ 完成 | Codex | `32 passed` + ruff + diff check |

---

## 2. 执行日志

### E0 范围确认与执行文档立项

**开始前拆分**

1. 读取当前真实注入源：
   - #3：`services/llm/prompt_builder.py::load_instruction()` 读取 `config/soul/instruction.md` 并拼入 static block。
   - #4：`PromptBuilder.build_static(identity, bot_self_id)` 在 bot connect 后拼入 QQ self id 说明。
   - #8：`services/llm/client.py::_build_group_profile_block()` 将 `reply_style` hint 与 `custom_prompt` 拼入 plugin_stable。
2. 对照当前 importer：
   - `guard.yaml` 没有行为 instruction 原文落点。
   - `adapter.yaml` 没有 bot self id hint/schema。
   - `runtime.yaml.per_group_overrides` 是空字典，source 没有 group profile 映射。
3. 本阶段按“执行文档 → #3 设计/实现/验证 → #4 设计/实现/验证 → #8 设计/实现/验证 → 综合验证”推进。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 把 v1 runtime prompt 拼接直接切到 v2 | 高 | 只改 importer draft + compiler dry-run，不改 `PromptBuilder` |
| #4 bot QQ id 是启动时动态值，source 里写死会泄漏或过期 | 高 | 只允许 source 写 `self_id_hint` / `known_self_ids` 作为 draft hint；正式运行时仍由 adapter 注入 |
| #8 per-group prompt 能覆盖 persona core | 高 | 落到 `runtime.yaml.per_group_overrides`，compiler block 标为 runtime/group context，不写 `persona.yaml` |
| 工作树有并行版本发布改动 | 中 | 本文和后续提交只 stage persona Part B 文件，忽略 `CHANGELOG`/插件版本号/`pyproject.toml` |

**回滚方式**

- 删除本文与 E1-E4 对应代码/测试/日志；Part A tail 与之前归档 commits 不受影响。

**验收证据**

- 本文新增。
- 后续 E1-E4 分项测试和综合验证。

**完成后回填**

- 实际改动：
  - 新增独立执行文档 `docs/tracking/persona-part-b-main-execution.md`。
  - 锁定本阶段只处理 #3/#4/#8，不接正式 runtime。
  - 记录当前工作树存在并行版本发布改动，本阶段不接管。
- 验证证据：
  - 已读取 `prompt_builder.py`、`llm/client.py`、`kernel/config.py`、`runtime.yaml`、`adapter.yaml`、`guard.yaml` 与现有 persona tests。
- 遗留风险：
  - E1-E3 尚未实现。

### E1 #3 `instruction.md` 行为指令映射

**开始前拆分**

1. 设计字段落点：
   - `guard.yaml.behavior_instructions.source="source_section"`
   - `guard.yaml.behavior_instructions.items[]` 保存 source §8.4 / “行为指令” / “回复规则”中的 bullet。
   - 每条 item 使用 `{text, origin_anchor, review_status}`，避免变成不可追溯长文本。
2. 修改默认模板：
   - `config/persona/_defaults/v2/guard.yaml` 增加空 `behavior_instructions` 块。
3. 修改 builder：
   - 新增 `_extract_behavior_instructions()`。
   - 在 `build_persona_draft()` Part B overrides 前调用，保证 source 规则覆盖默认空块。
   - 只从明确的行为指令章节抽取，不把整份 instruction.md 原文搬进 persona core。
4. 修改 compiler dry-run：
   - `_guard_text()` 输出行为指令摘要，确认 dry-run prompt block 能看到 #3。
5. 补测试：
   - source 附加 `# 8.4 行为指令` 后，`guard.yaml.behavior_instructions.items[0]` 存在且有 anchor。
   - compiler dry-run 的 `core.guard` block 含行为指令文本。
6. 运行 E1 targeted pytest/ruff。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| instruction.md 通常很长，直接搬运会造成僵硬/复读 | 中 | 仅从 source 明确章节抽 bullet；保留结构化 item，不写整块原文 |
| 行为指令被放到 persona core 覆盖身份 | 高 | 放入 `guard.yaml.behavior_instructions`，compiler dry-run 归 `core.guard` |
| source 最小模板没有 §8.4，导致必填错误 | 低 | 选填；缺失时保留默认空数组和 info/default |

**回滚方式**

- 删除 guard 默认块、builder 抽取、compiler 输出与新增测试；不影响 Part A tail。

**验收证据**

- `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_importer.py tests/test_persona_compiler.py -q`
- `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check services/persona tests/test_persona_importer.py tests/test_persona_compiler.py`

**完成后回填**

- 实际改动：
  - `config/persona/_defaults/v2/guard.yaml` 新增 `behavior_instructions` 默认空块。
  - `services/persona/builder.py` 新增 `_extract_behavior_instructions()`，从 source “行为指令/回复规则/instruction” 章节抽取 bullet 到 `guard.yaml.behavior_instructions.items[]`。
  - 每条行为指令包含 `text/origin_anchor/review_status=candidate`，并写入 `_import_report.json.fields`。
  - `services/persona/compiler.py` 的 dry-run `core.guard` block 输出行为指令摘要。
  - `tests/test_persona_importer.py` 与 `tests/test_persona_compiler.py` 增加 #3 回归。
- 验证证据：
  - `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_importer.py tests/test_persona_compiler.py -q` 通过，`17 passed`。
  - `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check services/persona tests/test_persona_importer.py tests/test_persona_compiler.py` 通过。
- 遗留风险：
  - 当前只抽取 source 明确章节；尚不直接读取 legacy `config/soul/instruction.md`，避免隐式迁移生产指令。

### E2 #4 bot QQ id / self id 提示映射

**开始前拆分**

1. 设计 source 输入：
   - front matter `bot_self_id_hint: "10000"`：单个已知 self id hint。
   - front matter `known_bot_self_ids: ["10000", "20000"]`：多环境/多 bot hint。
2. 设计字段落点：
   - `adapter.yaml.bot_identity.self_id_hint` 保存单个 hint。
   - `adapter.yaml.bot_identity.known_self_ids[]` 保存候选 self ids。
   - `adapter.yaml.bot_identity.runtime_source="adapter_connect_event"` 明确正式运行时仍由 adapter connect 填充。
   - `adapter.yaml.bot_identity.prompt_policy` 保存当前 v1 提示语义摘要：assistant role 才是 bot、user role 中昵称不可信、QQ 号可信。
3. 修改默认模板：
   - `config/persona/_defaults/v2/adapter.yaml` 增加 `bot_identity` 默认块。
4. 修改 builder：
   - `_extract_bot_identity()` 规范化 front matter id 到 str。
   - 缺失时保留默认空 hint，不报错。
   - 写 report fields：`bot_identity.self_id_hint` 和 `bot_identity.known_self_ids[i]`。
5. 修改 compiler dry-run：
   - 增加 `runtime.adapter` prompt block 或现有 block 输出 bot identity hint，不能混入 `core.identity`。
6. 补测试：
   - importer front matter 写入 adapter bot_identity。
   - compiler dry-run 输出 `runtime.adapter` block，且含 `runtime_source=adapter_connect_event` 或 self id hint。
7. 运行 E2 targeted pytest/ruff。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| source 写死 bot QQ 号导致未来 runtime 过期 | 高 | 字段命名为 hint；默认 `runtime_source=adapter_connect_event`，正式运行时仍以连接事件为准 |
| bot id prompt 被塞入 persona core 导致身份事实污染 | 中 | compiler 单独输出 `runtime.adapter` block |
| YAML 数字吞前导零 | 中 | 规范化时转 str；文档建议 id 加引号；测试覆盖字符串 |

**回滚方式**

- 删除 adapter 默认 `bot_identity`、builder 抽取、compiler runtime.adapter block 与测试。

**验收证据**

- `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_importer.py tests/test_persona_compiler.py -q`
- `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check services/persona tests/test_persona_importer.py tests/test_persona_compiler.py`

**完成后回填**

- 实际改动：
  - `config/persona/_defaults/v2/adapter.yaml` 新增 `bot_identity` 默认块。
  - `services/persona/builder.py` 新增 `_extract_bot_identity()`，支持 front matter `bot_self_id_hint` 与 `known_bot_self_ids`。
  - `adapter.yaml.bot_identity.runtime_source=adapter_connect_event` 明确正式 self id 仍由 adapter connect 事件提供。
  - `services/persona/compiler.py` 新增 dry-run `runtime.adapter` block，输出 self id hint、known ids 与 prompt policy。
  - `tests/test_persona_importer.py` 与 `tests/test_persona_compiler.py` 增加 #4 回归。
- 验证证据：
  - `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_importer.py tests/test_persona_compiler.py -q` 通过，`19 passed`。
  - `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check services/persona tests/test_persona_importer.py tests/test_persona_compiler.py` 通过。
- 遗留风险：
  - `bot_self_id_hint` 只是 draft hint，不替代运行时真实 self id；正式切流时仍需以 adapter connect event 覆盖。

### E3 #8 group profile reply style/custom prompt 映射

**开始前拆分**

1. 设计 source 输入：
   - front matter `group_profiles`，mapping keyed by group id。
   - 示例：
     ```yaml
     group_profiles:
       "12345":
         reply_style: playful
         custom_prompt: 多接梗，少说教。
     ```
2. 设计字段落点：
   - `runtime.yaml.per_group_overrides.<gid>.reply_style`
   - `runtime.yaml.per_group_overrides.<gid>.custom_prompt`
   - 每组补 `source=source_front_matter`，强调是 draft 覆写，不是生产 `GroupOverride` 投影。
3. 修改默认模板：
   - `config/persona/_defaults/v2/runtime.yaml` 已有 `per_group_overrides: {}`，不需要新增文件。
4. 修改 builder：
   - `_frontmatter_group_profiles()` 校验 group id 非空，payload 必须为 dict。
   - 只收 `reply_style/custom_prompt` 两个字段；不偷跑 at_only/tools/sticker/slang 等 GroupOverride。
   - 写 report fields：每个 gid 的 reply_style/custom_prompt。
5. 修改 compiler dry-run：
   - 新增 `runtime.group_profile` block，输出每组 reply_style/custom_prompt 摘要。
   - block position 标为 `stable`，对应现有 `plugin_stable` 注入点。
6. 补测试：
   - importer front matter `group_profiles` 写入 runtime per_group_overrides。
   - compiler dry-run 输出 `runtime.group_profile` block 且包含 custom_prompt。
7. 运行 E3 targeted pytest/ruff。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| per-group custom_prompt 覆盖 core persona | 高 | 落到 `runtime.yaml.per_group_overrides`，compiler block 标 `stable`，不写 persona/guard core |
| 误把完整 GroupOverride 全量搬入本轮 | 中 | 本轮只收 #8 明确的 `reply_style/custom_prompt` |
| group id YAML 裸数字前导零丢失 | 中 | 规范化为 str；建议 source 使用引号 |

**回滚方式**

- 删除 group profile front matter parser、runtime 写入、compiler block 与测试。

**验收证据**

- `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_importer.py tests/test_persona_compiler.py -q`
- `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check services/persona tests/test_persona_importer.py tests/test_persona_compiler.py`

**完成后回填**

- 实际改动：
  - `services/persona/builder.py` 新增 `_frontmatter_group_profiles()` 与 `_extract_group_profiles()`，支持 front matter `group_profiles` mapping。
  - 每个 group id 只投影 `reply_style/custom_prompt` 到 `runtime.yaml.per_group_overrides.<gid>`，并补 `source=source_front_matter`。
  - 非 mapping 的 `group_profiles` 或单组 payload 会写入 warn issue，不阻断 import；空 group id 跳过并告警。
  - `services/persona/compiler.py` 新增 dry-run `runtime.group_profile` block，`position=stable`，对齐现有 `LLMClient` group profile 注入点语义。
  - `tests/test_persona_importer.py` 与 `tests/test_persona_compiler.py` 增加 #8 回归。
- 验证证据：
  - `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_importer.py tests/test_persona_compiler.py -q` 通过，`21 passed`。
  - `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check services/persona tests/test_persona_importer.py tests/test_persona_compiler.py` 通过。
- 遗留风险：
  - 本轮没有把 production `GroupOverride` 的 at_only/tools/sticker/slang 等字段纳入 v2 source；后续如需迁移必须单独设计，避免 group prompt 越权覆盖 core persona。

### E4 综合验证、迁移清单与维护日志收口

**开始前拆分**

1. 文档一致性：
   - 更新 `docs/migrations/persona-v2-importer.md`，把 Part B #3/#4/#8 从“单独执行文档推进”改为 dry-run 已实现。
   - 保留“正式切流未做”的护栏，明确 `PromptBuilder` / `LLMClient` runtime 仍是 v1。
2. 维护日志：
   - 在 `maintenance-log.md` 顶部追加 Part B #3/#4/#8 条目，说明 importer/compiler dry-run 范围、影响、验证与回滚。
3. 综合验证：
   - 跑 persona importer/compiler/system/admin API 组合测试，覆盖 E1-E3 与既有 Part B dry-run。
   - 跑 ruff 覆盖 `services/persona`、SystemModule 测试与 persona 测试文件。
4. 自审：
   - `git diff --check` 检查空白错误。
   - `git diff --stat` 与 `git status --short` 检查只准备提交 Part B 文件。
   - staging 时显式列文件，避开并行版本发布/插件改动。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 文档误写成正式 runtime 已切流 | 高 | 所有文档只写 dry-run/importer/compiler，反复标注未接正式 runtime |
| 综合测试被工作树并行插件改动污染 | 中 | 测试命令限定 persona/system/admin API；提交只 stage Part B 文件 |
| 漏 stage 新执行文档 | 低 | `git status --short` 与 `git diff --cached --name-status` 双查 |

**回滚方式**

- revert Part B #3/#4/#8 commit 即可；Part A tail 与此前 archive commits 不受影响。

**验收证据**

- `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_importer.py tests/test_persona_compiler.py tests/test_system_module.py tests/test_persona_importer_api.py -q`
- `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check services/persona tests/test_persona_importer.py tests/test_persona_compiler.py tests/test_system_module.py tests/test_persona_importer_api.py`
- `git diff --check`
- `git diff --cached --name-status`

**完成后回填**

- 实际改动：
  - `docs/migrations/persona-v2-importer.md` 新增 Part B #3/#4/#8 迁移表，把 `instruction.md`、bot self id、group profile 三处旧注入源映射到 v2 draft / compiler dry-run 落点。
  - `maintenance-log.md` 追加 2026-05-24 Part B 主战场收口条目，记录影响、验证、回滚路径。
  - 本文状态改为已完成，E1-E4 证据闭环。
- 验证证据：
  - `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_importer.py tests/test_persona_compiler.py tests/test_system_module.py tests/test_persona_importer_api.py -q` 通过，`32 passed`。
  - `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check services/persona tests/test_persona_importer.py tests/test_persona_compiler.py tests/test_system_module.py tests/test_persona_importer_api.py` 通过。
  - `git diff --check -- config/persona/_defaults/v2/adapter.yaml config/persona/_defaults/v2/guard.yaml services/persona/builder.py services/persona/compiler.py tests/test_persona_importer.py tests/test_persona_compiler.py docs/tracking/persona-part-b-main-execution.md docs/migrations/persona-v2-importer.md maintenance-log.md` 无输出，未发现空白错误。
  - `git diff --name-status -- ...` 只显示 Part B persona/doc/test 相关文件；全局 `git status --short` 仍有并行插件/版本发布改动，本阶段不接管。
- 遗留风险：
  - 正式 runtime 切流仍未做，后续 S12' 需要把 compiler dry-run 与现有 `PromptBuilder` / `LLMClient` 输出做逐项对照后再灰度。
  - #8 本轮只覆盖 `reply_style/custom_prompt`，完整 `GroupOverride` 迁移仍需单独设计。
