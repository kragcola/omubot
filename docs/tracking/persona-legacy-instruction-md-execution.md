# Persona v2 — Opt-in Legacy `instruction.md` Importer

> 状态：2026-05-24 启动；只走 importer dry-run，不接 chat runtime；不替换 v1 `PromptBuilder._instruction`。
>
> 上游：[persona-v2-importer.md](../migrations/persona-v2-importer.md) §3 / §8
> 同期：[persona-s12-parity-audit-execution.md](./persona-s12-parity-audit-execution.md)、[persona-group-override-full-execution.md](./persona-group-override-full-execution.md)

## 0. 范围 / 非范围

- **In scope**：让 importer 在 `source.md` front matter 显式 opt-in 时，把指定的 legacy md 文件（默认指向 `config/soul/instruction.md`）的 bullet 抽取为 `guard.yaml.behavior_instructions.items[]` 候选；落 report `extractor=legacy_instruction_md_opt_in`，源行号锚点用 legacy 文件的 basename。
- **Out of scope**：替换 v1 `PromptBuilder._instruction`；改 `LLMClient`；改 admin Soul SPA；为 legacy 文件做章节级语义切分；提供任何默认路径自动读取（**不 opt-in 时绝不读 legacy 文件**）。

## T0. 安全护栏（开干前必须先确认）

1. **Default-off**：front matter 不显式写 `legacy_instruction_md: true` 时，importer 行为完全不变。
2. **路径必须显式**：opt-in 但缺 `legacy_instruction_md_path` → 落 warn issue `legacy_instruction_md_path missing`，不读任何文件。
3. **路径解析锚点**：`legacy_instruction_md_path` 相对路径以 `source.md` 所在目录解析（非 cwd、非 repo root）。绝对路径 / 用户家目录展开按文件系统语义。
4. **不存在文件**：解析后路径不是常规文件 → 落 warn issue `legacy_instruction_md_file_not_found`，不阻断 import。
5. **零 bullet**：legacy 文件存在但没 bullet → info-level 报告（不 warn、不 error），保留原 source-section items。
6. **不替换 source-section**：legacy bullets **追加**在 source-section bullets 之后；report 区分 `extractor=behavior_instruction_md` vs `legacy_instruction_md_opt_in`。
7. **不读取 v1 状态**：builder 仍是纯函数；I/O 由 writer 负责，builder 通过 `legacy_instruction` 参数收文本。

## T1. importer 抽取实现

| 模块 | 改动 |
|---|---|
| `services/persona/builder.py` | `build_persona_draft()` 新增可选参数 `legacy_instruction: LegacyInstructionPayload \| None = None`。新增 `_extract_legacy_instruction_md()`，opt-in 时把 bullets 拼到 `guard.yaml.behavior_instructions.items[]`，每条带 `text/origin_anchor/review_status` schema，extractor 标 `legacy_instruction_md_opt_in`；缺路径 / 缺文件分别走 warn issue。 |
| `services/persona/writer.py` | `import_source()` 在调 builder 前读 front matter `legacy_instruction_md` / `legacy_instruction_md_path`，按 T0 路径规则解析并读文本；构造 `LegacyInstructionPayload` 透传给 builder。Builder/writer 都不持有 cwd 状态。 |

## T2. 回归测试

`tests/test_persona_importer.py` 新增：

- `test_persona_importer_legacy_instruction_md_opt_out` — 默认无 front matter flag，报告里没有 `legacy_instruction_md_opt_in` extractor，items 仅来自 source-section 或为空。
- `test_persona_importer_legacy_instruction_md_appends_items` — opt-in + 同目录 legacy md → bullets 追加到 items 末尾，extractor 区分。
- `test_persona_importer_legacy_instruction_md_missing_path` — opt-in 但缺 `legacy_instruction_md_path` → warn issue `legacy_instruction_md_path missing`，items 不变。
- `test_persona_importer_legacy_instruction_md_file_not_found` — opt-in + 路径不存在 → warn issue `legacy_instruction_md_file_not_found`，items 不变。

`tests/test_persona_compiler.py` 不动（compiler 已经把 `behavior_instructions.items[]` 全部串到 `core.guard`，extractor 不参与渲染）。

## T3. 文档与维护日志

- `docs/migrations/persona-v2-importer.md` 在 §8 Part B #3/#4/#8 表后追加 §11 “Legacy instruction.md opt-in dry-run”。
- `maintenance-log.md` 顶部追加 2026-05-24 “Persona legacy instruction.md opt-in 上线” 条目（五段齐：变更类型/内容/影响/验证/回滚）。

## 验证清单（D4 完成证据）

- targeted `pytest tests/test_persona_importer.py tests/test_persona_compiler.py tests/test_persona_parity_audit.py tests/test_persona_importer_api.py tests/test_system_module.py -q` 全绿。
- targeted `ruff check services/persona tests/test_persona_importer.py tests/test_persona_compiler.py tests/test_persona_parity_audit.py tests/test_persona_importer_api.py` 通过。
- `git diff --check` 不含尾空格 / `<<<<<<<`；`git status -uno` 无非预期 storage/db 改动。

## 回滚路径

revert 本次 commit 即可；删除 `_extract_legacy_instruction_md()`、writer 的 `_read_legacy_instruction_md()`、新增 4 条测试与 §11、当日维护日志条目。Phase 1/2（S12' parity audit / GroupOverride 完整迁移）已归档不受影响。
