# Persona S12' Parity Audit 执行追踪

> 状态：S12' parity audit 已完成
> 启动时间：2026-05-24
> 执行人：Codex
> 上游：[persona-part-b-main-execution.md](./persona-part-b-main-execution.md) §E4
> 参考格式：[persona-source-importer-remediation-execution.md](./persona-source-importer-remediation-execution.md)

---

## 0. 执行规则

1. 每个步骤先写：细分动作、风险、回滚方式、验收证据。
2. 每个步骤完成后回填实际改动、验证证据、遗留风险。
3. 本文只做 **比对**，不做任何 v1 → v2 切流；`PromptBuilder` / `LLMClient` 运行时仍然原样。
4. parity 输出落到 `services/persona/parity_audit.py` + 回归测试，不写新的运行路径，不污染线上 prompt。
5. 工作树存在并行版本发布改动；本阶段只 stage parity 相关文件，提交时显式列文件，绕开 D7 风险。

---

## 1. 步骤总览

| 步骤 | 名称 | 状态 | 负责人 | 完成证据 |
|---|---|---|---|---|
| P0 | 范围确认与执行文档立项 | ✅ 完成 | Codex | 本文 |
| P1 | 比对维度梳理（v1 → v2 axis） | ✅ 完成 | Codex | §3 表 |
| P2 | parity_audit 模块 + 单元测试 | ✅ 完成 | Codex | `38 passed` + ruff |
| P3 | 文档闭环（migrations §9 + maintenance-log） | ✅ 完成 | Codex | doc diff |

---

## 2. 执行日志

### P0 范围确认与执行文档立项

**开始前拆分**

1. v1 注入源已知：
   - `services/llm/prompt_builder.py::PromptBuilder.build_static()` 拼接 personality / bot self id / instruction / admins / proactive，输出 Block 1。
   - `services/llm/client.py::_build_group_profile_block()` 把 `GroupOverride.reply_style/custom_prompt` 拼成 plugin_stable 一行 text block。
2. v2 dry-run 已知：`services/persona/compiler.py::compile_persona_dry_run()` 输出 7 个 `CompilePromptBlock`：
   - `core.identity` / `core.voice` / `core.knowledge` / `core.examples` / `core.guard` (`position=static`)
   - `runtime.adapter` (`position=static`)
   - `runtime.group_profile` (`position=stable`)
3. parity audit 不读真实 runtime 配置，只接受调用方传入的 `Identity` / `bot_self_id` / `instruction` / `admins` / `proactive` / `GroupOverride`，对比维度由 §3 列表枚举。
4. 实现位置：`services/persona/parity_audit.py`，对外暴露 `compare_v1_vs_v2_dry_run(...) -> ParityReport`，回归测试落 `tests/test_persona_parity_audit.py`。
5. parity result 不进入 `_import_report.json`，避免和 importer 的 issues 流耦合。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| parity 模块意外 import 真实 runtime 路径，污染 v2 dry-run 边界 | 高 | 模块只 import `services.identity.Identity`、`services.persona.compiler.CompileResult`、`kernel.config.GroupOverride` 与 `services.llm.client._GROUP_REPLY_STYLE_HINTS`；不构造 `LLMClient` |
| parity output 与 importer report 字段冲突 | 中 | 单独 `ParityReport` dataclass，不写入 `_import_report.json` |
| 工作树并行版本发布改动被误带入 commit | 中 | 提交时显式列 parity 相关文件 |
| v1 prompt 文案后续微调，导致 parity 脱锚 | 中 | parity 维度只比对语义锚点（`你的QQ号是 {id}` 关键短语、`【管理员】`、`【群聊回复偏好】` 等），不做整段字符串相等 |

**回滚方式**

- 删除本文 + `services/persona/parity_audit.py` + `tests/test_persona_parity_audit.py` + `docs/migrations/persona-v2-importer.md` §9 + maintenance-log 当日条目即可；不影响 Part A / Part B 既有产物。

**验收证据**

- 本文新增。
- P1-P3 各步分项证据。

**完成后回填**

- 实际改动：本文落地，锁定 P1-P3 范围。
- 验证证据：已读取 `prompt_builder.py`、`llm/client.py::_build_group_profile_block`、`kernel/config.py::GroupOverride`、`services/persona/compiler.py`、`services/persona/builder.py` 与 Part B main execution 结果。
- 遗留风险：P1-P3 尚未执行。

### P1 比对维度梳理（v1 → v2 axis）

**开始前拆分**

1. 选定 6 个 axis（与 [persona-part-b-main-execution.md](./persona-part-b-main-execution.md) #1/#3/#4/#5/#8/proactive 保持一致）：

| axis | v1 出处 | v2 dry-run 出处 | 比对要点 |
|---|---|---|---|
| identity_personality | `Identity.personality` 进入 Block 1 头部 | `core.identity` 内 `静态身份块：…`、`名字`、`角色`、`自称`、`性格底色`、`价值观` | personality 文本 v2 是否覆盖 v1 头部信息 |
| bot_self_id | Block 1 的 `【你的QQ号是 {id}…】` 段（依赖 `prompt_policy`） | `runtime.adapter` 内 `bot self id hint：{id}` + `runtime source：adapter_connect_event` + 两条 prompt_policy 文案 | 是否能用 v2 hint+policy 还原 v1 关键信息 |
| behavior_instruction | Block 1 拼接 `self._instruction`（来自 `instruction.md`） | `core.guard` 的 `行为指令：…` 段 | v1 整段 vs v2 bullet 拼接的语义是否一致 |
| admins | Block 1 `【管理员】@QQ(nick)、…` | v2 暂无 prompt block；admins 落 `adapter.yaml.permissions.admins[]`（draft 字段） | 标记 `v1_only`，记录 follow-up |
| proactive_rules | Block 1 末尾追加 `identity.proactive` | v2 暂无 block；source 也尚未承载（依赖 `## 插话方式`） | 标记 `v1_only`，列入 S12' 后续 |
| group_profile | `LLMClient._build_group_profile_block(group_profile)` 输出 plugin_stable 文本 | `runtime.group_profile` (`position=stable`) | reply_style hint + custom_prompt 是否对齐 |

2. 对 `divergent` / `v1_only` / `v2_only` 给出统一分类：

   - `aligned` — 双方都有承载，且关键锚点能在 v2 文本中找到。
   - `divergent` — 双方都有承载，但 v2 文本关键锚点缺失或与 v1 相反。
   - `v1_only` — 仅 v1 输出，v2 还没有对应 block。
   - `v2_only` — 仅 v2 输出，v1 没有对应来源（本轮预期为空）。
   - `not_applicable` — 输入参数说明这一 axis 无内容（如 `bot_self_id == ""`），跳过比对。

3. parity API 草案：

```python
ParityAxis = Literal["identity_personality", "bot_self_id", "behavior_instruction", "admins", "proactive_rules", "group_profile"]
ParityStatus = Literal["aligned", "divergent", "v1_only", "v2_only", "not_applicable"]

@dataclass(frozen=True)
class ParityFinding:
    axis: ParityAxis
    status: ParityStatus
    v1_signal: str
    v2_signal: str
    notes: str = ""

@dataclass(frozen=True)
class ParityReport:
    persona_id: str
    findings: tuple[ParityFinding, ...]

    @property
    def has_divergence(self) -> bool: ...
    def to_dict(self) -> dict[str, Any]: ...
```

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 锚点选错导致 v1 文案后续小改就触发误报 | 中 | 锚点只取最稳定的语义片段，不依赖标点/全角细节 |
| `admins` / `proactive_rules` 长期标 `v1_only`，掩盖未做的迁移 | 中 | parity report 单独列出 v1_only axis；`docs/migrations/persona-v2-importer.md` §9 显式登记 |

**回滚方式**

- 同 P0 回滚清单。

**验收证据**

- §3 表与 P2 实现保持一致。

**完成后回填**

- 实际改动：本文 §1/§2/§3 落地。
- 验证证据：表中 v1 出处和 v2 出处通过 §0 的 Read 已逐项确认。
- 遗留风险：admins / proactive_rules 仍未在 v2 落 prompt block，由 §9 登记后续 follow-up。

### P2 parity_audit 模块 + 单元测试

**开始前拆分**

1. 在 `services/persona/parity_audit.py` 实现 `compare_v1_vs_v2_dry_run()`：
   - 入参：`identity`、`bot_self_id`、`instruction_text`、`admins`、`proactive`、`group_override`、`compile_result`。
   - 内部不构造 `LLMClient`、不发请求。
   - 对每个 axis 各自 `evaluate_*()`，返回 `ParityFinding`。
2. 锚点策略：
   - `identity_personality`：v1 signal 取 `identity.personality` 头一行；v2 signal 取 `core.identity` block，期待文本里包含 `静态身份块：`/personality 头一行（subset 锚点）。
   - `bot_self_id`：当 `bot_self_id == ""` 时跳过；否则要求 v2 `runtime.adapter` 含 `bot self id hint：{bot_self_id}` 与 `runtime source：adapter_connect_event` 与 `昵称不可信`。
   - `behavior_instruction`：当 `instruction_text == ""` 时跳过；否则取 v1 拼接行的关键短语（按行 split → 取第一行 strip），要求出现在 v2 `core.guard` `行为指令：…` 中。
   - `admins`：当 admins 非空时，axis 直接标 `v1_only`，notes 指向 follow-up。
   - `proactive_rules`：当 proactive 非空时，axis 标 `v1_only`，notes 指向 follow-up。
   - `group_profile`：当 `group_override is None` 或 `reply_style/custom_prompt` 都为空时，标 `not_applicable`；否则要求 `runtime.group_profile` 文本含 `reply_style={style}` 和 / 或 `custom_prompt={text}`，且 `_GROUP_REPLY_STYLE_HINTS[style]` 与 v2 文本互相印证（v1 plugin_stable hint 文案 vs v2 stable block 文案）。
3. 模块只做静态比对，无 IO；测试构造一份 `MINIMAL_SOURCE` + 必要 front matter，调用 importer + compile_persona_dry_run 一次，把结果直接喂进 parity，期望 `aligned` / `not_applicable` 各落各位。
4. 测试维度（最少 7 条）：
   - happy path：identity / bot_self_id / instruction / group_profile 全部 `aligned`，admins/proactive `v1_only`。
   - 空 bot_self_id：bot_self_id axis 标 `not_applicable`。
   - 空 instruction：behavior_instruction axis 标 `not_applicable`。
   - reply_style 缺失：group_profile axis 标 `not_applicable`。
   - v2 缺 instruction（手动剥离 source 的行为指令章节）：behavior_instruction `divergent`。
   - admins 非空：axis `v1_only` + notes 指向 follow-up。
   - `to_dict()` 序列化结构稳定。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 测试构造的 v2 draft 跟 builder 出参不一致 | 中 | 复用 `tests.test_persona_importer.MINIMAL_SOURCE` 与 `_write_defaults`，尽量贴近现有 importer 测试 |
| parity 模块被 import 进 runtime 链路 | 高 | 仅放在 `services/persona/`，不在 `__init__.py` 默认导出；测试通过完整路径 import |
| 文案差异（`；` vs `;`）误判 | 中 | 比对函数统一对比 substring，不比较整段相等 |

**回滚方式**

- 删除 `services/persona/parity_audit.py` 与 `tests/test_persona_parity_audit.py`。

**验收证据**

- `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_parity_audit.py tests/test_persona_compiler.py tests/test_persona_importer.py -q`
- `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check services/persona tests/test_persona_parity_audit.py`

**完成后回填**

- 实际改动：
  - 新增 `services/persona/parity_audit.py`，提供 `ParityFinding/ParityReport` 与 `compare_v1_vs_v2_dry_run()`。
  - 不改 `services/persona/__init__.py` 默认导出，避免和现有 importer 入口耦合；调用方走完整路径 import。
  - 新增 `tests/test_persona_parity_audit.py`，覆盖 happy path / not_applicable / divergent / v1_only / `to_dict()` 7 条。
  - 复用 `tests.test_persona_importer.MINIMAL_SOURCE` 与 `_write_defaults`，统一 fixtures。
- 验证证据：
  - `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_parity_audit.py tests/test_persona_compiler.py tests/test_persona_importer.py -q` 通过，`38 passed`。
  - `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check services/persona tests/test_persona_parity_audit.py` 通过。
- 遗留风险：
  - parity report 仅供 audit 工具与文档引用，不写入 `_import_report.json`；切流前还需在 admin SPA 增加 parity 视图（列 §9 后续）。

### P3 文档闭环（migrations §9 + maintenance-log）

**开始前拆分**

1. 在 `docs/migrations/persona-v2-importer.md` 追加 §9 “S12' parity audit”，标明：
   - 新增 `services/persona/parity_audit.py` 与对应回归测试。
   - 6 个 axis 的当前 status（admin/proactive_rules 列为 `v1_only` follow-up）。
   - 不写入 runtime；切流前 admin SPA 需要 parity 视图。
2. `maintenance-log.md` 顶部追加 2026-05-24 条目，五段齐：变更类型 / 内容 / 影响 / 验证 / 回滚。
3. 自审：
   - `git diff --check` 校验空白错误。
   - `git diff --stat` 与 `git status --short` 确认只包含 parity 相关文件。
4. 不接 `git commit`；提交动作仍然要等用户显式指示。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| §9 误写成已切流 | 高 | 文案统一为 “parity audit / dry-run / 不写运行时” |
| 维护日志条目和 part B 主战场条目混淆 | 中 | 标题写明 “Persona S12' parity audit” |
| 误 stage 工作树并行改动 | 中 | 提交前显式列文件 |

**回滚方式**

- 撤销 §9 与 maintenance log 当日条目。

**验收证据**

- `source ./scripts/dev/env.sh && .venv/bin/python -m pytest tests/test_persona_parity_audit.py tests/test_persona_compiler.py tests/test_persona_importer.py tests/test_persona_importer_api.py tests/test_system_module.py -q`
- `source ./scripts/dev/env.sh && .venv/bin/python -m ruff check services/persona tests/test_persona_parity_audit.py tests/test_persona_compiler.py tests/test_persona_importer.py tests/test_persona_importer_api.py`
- `git diff --check`
- `git status --short` / `git diff --name-status`

**完成后回填**

- 实际改动：
  - `docs/migrations/persona-v2-importer.md` 追加 §9 “S12' parity audit”，列 6 axis status 与 follow-up。
  - `maintenance-log.md` 顶部追加 2026-05-24 “Persona S12' parity audit dry-run 上线” 条目。
  - 本文状态切到已完成，P1-P3 证据闭环。
- 验证证据：
  - `pytest tests/test_persona_parity_audit.py tests/test_persona_compiler.py tests/test_persona_importer.py tests/test_persona_importer_api.py tests/test_system_module.py -q` 通过。
  - `ruff check` 命中目标文件均通过。
  - `git diff --check` 无输出；`git status --short` 列表里 parity 相关文件单独可见，工作树并行改动没有被误带入 commit（本轮不提交，留给用户决定时机）。
- 遗留风险：
  - admin SPA 尚未提供 parity 视图；切流前需要把 `ParityReport` 暴露给 `/api/admin/persona/*`，并加入 issue 跳转（属于 S12' 后续）。
  - parity audit 当前不覆盖 `proactive_rules` / `admins` 的 v2 落点设计，需要 Part B 后续切片单独立项。
