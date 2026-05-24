# Persona v2 Importer 迁移清单

> 状态：2026-05-24 Part A importer 已完整收尾（含 #1/#7/#5 小尾巴）；S6/S10' admin SPA 首版闭环已完成；Part B dry-run 闭环与 #3/#4/#8 prompt source 映射已完成。
>
> 上游：[Persona Source Importer](../tracking/persona-source-importer.md)、
> [整改执行追踪](../tracking/persona-source-importer-remediation-execution.md)。

## 1. 已完成的文档/配置迁移

| 旧位置 / 旧说法 | 新位置 / 新说法 | 状态 |
|---|---|---|
| `persona-source-importer.md §16.1~§16.12` 内嵌 Runtime/SystemModule 架构 | [system-module-architecture.md](../tracking/system-module-architecture.md) 独立承载；importer §16 只保留引用 | ✅ 完成 |
| `persona-spec-format.md` 仅描述 v2.0 minimal core 12 文件 | [persona-spec-format.md](../persona-spec-format.md) 追加 v2.1 state/thinker/system/modules 扩展 | ✅ 完成 |
| 默认模板只存在于方案正文 YAML 块 | `config/persona/_defaults/v2/{guard,eval,trace}.yaml` + README | ✅ 完成 |
| 首版 draft 输出在 12/15 文件之间摇摆 | 统一为 15 个 `.draft/*.yaml` partial skeleton + `.draft/modules/_README.md` + `_import_report.json` | ✅ 完成 |
| compiler 前 Freeze 写正式 runtime 路径的模糊表述 | compiler 前只允许 Pending Freeze 到 `_pending_freeze/`；Schema Freeze 需 compiler dry-run 后再写正式路径 | ✅ 完成 |
| `/api/persona/*` | `/api/admin/persona/*` | ✅ 已实现 |
| 硬编码 importer 模型 id | `persona_import` task profile，由配置选择模型 | ✅ 已实现 |
| draft YAML 内嵌字段级 metadata | draft YAML 保持纯 schema；字段溯源写入 `_import_report.json` | ✅ 已实现 |

## 2. 代码实现迁移

| 切片 | 目标 | 状态 |
|---|---|---|
| S1 | `services/persona/` importer package scaffold | ✅ 已实现 |
| S2 | source parser + section normalizer | ✅ 已实现 |
| S3 | `persona_import` LLMTask/profile 接入 | ✅ 已实现 |
| S4 | draft writer + `_import_report.json` | ✅ 已实现 |
| S5 | `/api/admin/persona/*` admin API | ✅ 已实现 |
| Part A tail #1 | `identity.md` 主体静态身份块迁移到 `persona.yaml.identity.personality` | ✅ 已实现；draft 字段，不接正式 runtime |
| Part A tail #7 | `memory.yaml.paragraph` / `entity_index` schema + §6 seed episodes | ✅ 已实现；不读取真实 memory DB |
| Part A tail #5 | front matter `admins` → `adapter.yaml.permissions.admins[]` | ✅ 已实现；不读取生产 `BotConfig.admins` |
| S6/S10' | admin UI source 编辑、导入报告、draft 文件清单与 Pending Freeze 交互 | ✅ 首版已实现；issue 行号跳转高亮/e2e smoke 待后续 |
| Part B S1' | SystemModule contract catalog + RuntimeStateBus dry-run 骨架 | ✅ 已实现；未接入正式 chat/prompt runtime |
| Part B S2'/S3' | source §11.2/§12 扩展 + 9 默认模板 | ✅ dry-run 已实现；§13 patch 待后续 |
| Part B S5' | SystemModule validator 接入 importer report | ✅ 已实现 |
| Part B S11' | persona compiler dry-run | ✅ 已实现；不写正式 runtime |
| Part B #3/#4/#8 | `instruction.md` / bot QQ id / group profile reply style 与 custom prompt | ✅ dry-run 已实现；未接正式 runtime |
| Part B S6'~S9'/S12' | 完整模块业务实现、admin 模块状态卡、灰度切流 | ⏳ 后续 |

## 3. 护栏

1. 真实运行时配置仍受 `.gitignore` 保护；只放行 `config/persona/_defaults/v2/**`。
2. `config/persona/*/.draft/`、`source.frozen.md`、`_pending_freeze/` 不应提交。
3. 任何新增 importer 字段必须同时声明：来源、缺失时行为、是否属于 Part A 首版。
4. 后续迁移若触及 API、菜单、admin UI 或 runtime 写盘，继续在本文追加旧→新对照。

## 4. S6/S10' admin SPA 首版迁移

| 旧状态 / 旧入口 | 新状态 / 新入口 | 状态 |
|---|---|---|
| 只能通过 CLI 或 JSON API 写 `source.md` 前置文件 | `/admin/persona-importer` 在线加载、编辑、保存 `config/persona/<id>-v2/source.md` | ✅ 已实现 |
| `/api/admin/persona/*` 仅支持 import/draft/freeze | 新增 `GET/PUT /api/admin/persona/source/{id}`，只读写 `source.md` | ✅ 已实现 |
| Pending Freeze 只能通过 API/CLI 触发 | admin SPA 通过二次确认触发 Pending Freeze；source 保存后必须重新 import 才能 freeze | ✅ 已实现 |
| `_import_report.json` 只能读 JSON | admin SPA 展示 Issues / Fields / Files 三个视图 | ✅ 已实现 |
| S10' 双栏点击 issue 自动滚动高亮 | 当前只显示 `source_span` 文本，不做自动滚动定位 | ⏳ 后续 |

## 5. Part B S1' dry-run 骨架迁移

| 旧状态 / 旧入口 | 新状态 / 新入口 | 状态 |
|---|---|---|
| Part B 只存在于 `system-module-architecture.md` 架构方案 | 新增 `services/system_module/`，承载 catalog、contract、validator、RuntimeStateBus 内存骨架 | ✅ 已实现 |
| SystemModule catalog 只在文档表格中 | `MODULE_CATALOG` 固化为 27 个首版模块 + 7 个 reserved 模块；6 个 required module 显式标记 | ✅ 已实现 |
| DAG / state owner 只在方案中描述 | `validate_module_graph()` 支持 required/reserved、重复 owner、缺 owner、缺依赖和环检测 | ✅ 已实现 |
| RuntimeStateBus 只在伪代码中 | `RuntimeStateBus` 支持 owner 写入约束、Scope TTL key、per-turn 清理、trace snapshot | ✅ dry-run 已实现 |
| 正式运行时消费 v2 persona | 仍未接入；现有 PluginBus/PromptBuilder/chat runtime 保持原状 | ⏳ 后续 S6'~S12' |

## 6. Part B dry-run 闭环迁移

| 旧状态 / 旧入口 | 新状态 / 新入口 | 状态 |
|---|---|---|
| 默认模板只有 guard/eval/trace 3 份 | `_defaults/v2` 扩展到 runtime/state/thinker/adapter/capabilities/system 共 9 份 | ✅ 已实现 |
| source.md 不影响 SystemModule 开关 | source §12 checkbox 可写入 `system.yaml.modules.<id>.enabled` | ✅ 已实现 |
| thinker tone 集合只在方案中 | source §11.2 `tone_palette` 同步到 `voice.yaml.tone_palette` 和 `thinker.yaml.policy.tone_set` | ✅ 已实现 |
| SystemModule 校验只存在于独立 S1' 测试 | importer 写 `_import_report.json.fields[].key_path="_system_module_validation"`，issues 进入 report | ✅ 已实现 |
| compiler 只在方案中 | `services.persona.compiler.compile_persona_dry_run()` 和 CLI `--compile-dry-run` 输出 prompt block 草案 | ✅ 已实现 |
| 正式切流 | 仍未做；dry-run 结果不进入 `PromptBuilder` / `LLMClient` | ⏳ 后续 |

## 7. Part A #1/#7/#5 完整收尾迁移

| 旧注入源 / 缺口 | 新 draft 落点 | 状态 |
|---|---|---|
| #1 `soul/identity.md` 主体静态 personality block 只有拆分字段，缺原文迁移落点 | `persona.yaml.identity.personality`，由 source §1/§1.1/§1.2/§1.3 组合，report extractor=`identity_static_md` | ✅ 已实现 |
| #7 全局记忆索引只在 runtime DB，importer 只有 `seed_episodes` 空壳 | `memory.yaml.paragraph`、`memory.yaml.entity_index`、`retrieval_policy` + source §6 `seed_episodes[]` candidate/source anchor | ✅ 已实现 |
| #5 admins 名单未被 source 收录 | front matter `admins` → `adapter.yaml.permissions.admins[]`，source 标记为 `source_front_matter` | ✅ 已实现 |
| #3 `instruction.md`、#4 bot QQ id、#8 group profile | 不在 Part A 尾巴中偷跑 | ✅ 已由 Part B 单独执行文档收口 |

## 8. Part B #3/#4/#8 prompt source 映射迁移

| 旧注入源 / 缺口 | 新 draft / dry-run 落点 | 状态 |
|---|---|---|
| #3 `config/soul/instruction.md` 行为指令只在 v1 static prompt 中拼接，v2 draft 没有结构化承载 | source “行为指令/回复规则/instruction” 章节 bullet → `guard.yaml.behavior_instructions.items[]`，compiler dry-run 输出 `core.guard` 行为指令摘要 | ✅ 已实现；不直接读取 legacy `instruction.md` |
| #4 bot QQ self id 由 `PromptBuilder.build_static(..., bot_self_id)` 运行时提示，v2 source 无 hint/schema | front matter `bot_self_id_hint` / `known_bot_self_ids` → `adapter.yaml.bot_identity`，`runtime_source=adapter_connect_event`，compiler dry-run 输出 `runtime.adapter` | ✅ 已实现；运行时真实 self id 仍由 adapter connect event 提供 |
| #8 `GroupOverride.reply_style/custom_prompt` 由 `LLMClient._build_group_profile_block()` 运行时拼入 plugin_stable，v2 source 无群级 override 映射 | front matter `group_profiles.<gid>.reply_style/custom_prompt` → `runtime.yaml.per_group_overrides.<gid>`，`source=source_front_matter`，compiler dry-run 输出 `runtime.group_profile` / `position=stable` | ✅ 已实现；只覆盖 reply_style/custom_prompt |
| 正式 runtime 切流 | 仍由 v1 `PromptBuilder` / `LLMClient` 使用现有配置路径；v2 compiler dry-run 仅用于比对和后续灰度 | ⏳ 后续 S12' |
