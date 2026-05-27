# Persona v2 Importer 迁移清单

> 状态：2026-05-24 Part A importer 已完整收尾（含 #1/#7/#5 小尾巴）；S6/S10' admin SPA 首版闭环已完成；Part B dry-run 闭环与 #3/#4/#8 prompt source 映射已完成；S12' parity audit dry-run 已上线。
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
| S6/S10' | admin UI source 编辑、导入报告、draft 文件清单与 Pending Freeze 交互 | ✅ 首版已实现；issue 行号点击双栏滚动高亮已上线（A4），e2e smoke 仍待后续 |
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
| S10' 双栏点击 issue 自动滚动高亮 | Issues / Fields 行号点击触发左侧 source textarea focus + setSelectionRange + scrollTop（buffer 3 行）；source dirty 时 chip 灰显，提示重新 import | ✅ 已实现 |

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

## 9. S12' parity audit dry-run

> 上游：[persona-s12-parity-audit-execution.md](../tracking/persona-s12-parity-audit-execution.md)
>
> 本节仅记录 v1 → v2 比对工具的落地。`PromptBuilder` / `LLMClient` runtime 仍是 v1，未切流。

| axis | v1 出处 | v2 dry-run 出处 | 当前 parity status | 备注 |
|---|---|---|---|---|
| identity_personality | `Identity.personality` 写入 `PromptBuilder.build_static()` Block 1 头部 | `core.identity` (`position=static`) | aligned | parity 用 personality 前 5 条**有意义锚点**（跳过 markdown 标题与列表前缀）做 any-match；2026-05-27 后切多锚点 |
| bot_self_id | `PromptBuilder.build_static(identity, bot_self_id)` 拼接 `【你的QQ号是 …】` 段 | `runtime.adapter` (`position=static`) | aligned | 三锚点齐：`bot self id hint：{id}`、`runtime source：adapter_connect_event`、`昵称不可信`；source 必须配 `bot_self_id_hint` front matter，2026-05-27 已补到 fengxiaomeng-v2 source.md |
| behavior_instruction | `PromptBuilder.build_static()` 拼接 `self._instruction`（来自 `instruction.md`） | `core.guard` 的 `行为指令：…` 段 | aligned | source 没写时 v2 缺段，axis = `divergent`；2026-05-27 后 anchor 跳 markdown 标题 |
| admins | `PromptBuilder.build_static()` 输出 `【管理员】@QQ(nick)、…` | `runtime.adapter` 末尾追加 `【管理员】@QQ(label)` + 信任策略尾巴 | aligned | source front matter `admins:` 落到 `adapter.yaml.permissions.admins[]`；2026-05-27 已补到 fengxiaomeng-v2 source.md，prompt block 端 [compiler.py:228-231](../../services/persona/compiler.py#L228-L231) 早已渲染 |
| proactive_rules | `PromptBuilder.build_static()` 末尾追加 `identity.proactive` | `core.guard` 的 `插话方式：…` 段 | aligned | source `## 插话方式` 段 → `persona.yaml.identity.proactive_rules`；compiler [_guard_text](../../services/persona/compiler.py#L172) 已经把它接到 `core.guard`；2026-05-27 后 anchor 跳 markdown 标题 |
| group_profile | `LLMClient._build_group_profile_block()` 输出 plugin_stable | `runtime.group_profile` (`position=stable`) | aligned | reply_style hint + custom_prompt 都覆盖；hint 表由 `tests/test_persona_parity_audit.py::test_reply_style_hints_reference_matches_runtime` 锁住 |

| 旧状态 / 旧入口 | 新状态 / 新入口 | 状态 |
|---|---|---|
| 没有 v1 vs v2 比对工具 | `services/persona/parity_audit.py::compare_v1_vs_v2_dry_run()` + `tests/test_persona_parity_audit.py` | ✅ 已实现 |
| 切流前需 admin SPA parity 视图 | `/api/admin/persona/*` 暂未暴露 `ParityReport` | ⏳ 后续 |
| 切流前需 `proactive_rules` / `admins` 落 prompt block | source schema + compiler block 设计未启动 | ⏳ 后续 |
| 正式 runtime 切流 | 仍由 v1 `PromptBuilder` / `LLMClient` 使用现有配置路径；parity audit 仅用于离线比对 | ⏳ 后续 |

## 10. GroupOverride 完整迁移 dry-run

> 上游：[persona-group-override-full-execution.md](../tracking/persona-group-override-full-execution.md)
>
> 本节仅记录 importer / compiler dry-run 扩展；`kernel.config.GroupOverride` / `LLMClient` / `GroupChatScheduler` runtime 未切流。

| 字段 | v1 出处 | v2 dry-run 落点 | 状态 |
|---|---|---|---|
| blocked_users | `GroupOverride.blocked_users` (list[int]) | `runtime.yaml.per_group_overrides.<gid>.blocked_users[]` | ✅ 已实现；非 list 写 warn issue |
| allowed_tools | `GroupOverride.allowed_tools` | 同前 | ✅ 已实现；元素 strip |
| blocked_tools | `GroupOverride.blocked_tools` | 同前 | ✅ 已实现 |
| at_only | `GroupOverride.at_only` | 同前 | ✅ 已实现；非 bool 写 warn |
| talk_value | `GroupOverride.talk_value` | 同前 | ✅ 已实现；非数字写 warn |
| planner_smooth | 同上 | 同上 | ✅ 已实现 |
| debounce_seconds | 同上 | 同上 | ✅ 已实现 |
| batch_size | `GroupOverride.batch_size` | 同上 | ✅ 已实现；非 int 写 warn |
| history_load_count | 同上 | 同上 | ✅ 已实现 |
| reply_style | `GroupOverride.reply_style`（GroupReplyStyle 枚举） | 同上 | ✅ 已实现；非法值写 warn |
| custom_prompt | `GroupOverride.custom_prompt` | 同上 | ✅ 已实现 |
| tools_enabled | `GroupOverride.tools_enabled` | 同上 | ✅ 已实现 |
| sticker_mode | `GroupOverride.sticker_mode`（GroupStickerMode 枚举） | 同上 | ✅ 已实现 |
| slang_enabled | `GroupOverride.slang_enabled` | 同上 | ✅ 已实现 |
| presence_mode | `GroupOverride.presence_mode`（GroupPresenceMode 枚举） | 同上 | ✅ 已实现 |
| compiler dry-run group_profile block | 仅渲染 reply_style/custom_prompt | 按固定字段顺序渲染全部 15 字段 + `source` token；空值跳过 | ✅ 已实现 |
| parity audit 全字段比对 | 仅覆盖 reply_style/custom_prompt | 暂未扩展 | ⏳ 后续 |
| 正式 runtime 切流 | 仍由 v1 `BotConfig.group.overrides` / `LLMClient` / `GroupChatScheduler` 消费 | dry-run only | ⏳ 后续 |

## 11. Legacy `instruction.md` opt-in dry-run

> 上游：[persona-legacy-instruction-md-execution.md](../tracking/persona-legacy-instruction-md-execution.md)
>
> 本节仅记录 importer dry-run 扩展；v1 `PromptBuilder._instruction` / `LLMClient` / admin Soul SPA 全部不动。

| 旧状态 / 旧入口 | 新状态 / 新入口 | 状态 |
|---|---|---|
| importer 完全不读 `config/soul/instruction.md` | front matter `legacy_instruction_md: true` + `legacy_instruction_md_path` 显式 opt-in 后，writer 从相对 `source.md` 的路径读取文本，bullets 追加到 `guard.yaml.behavior_instructions.items[]` | ✅ 已实现；默认关闭，不影响现有 importer 行为 |
| 行为指令 extractor 仅 `behavior_instruction_md` | 新增 `legacy_instruction_md_opt_in`（confidence=0.6）；report 中 source-section bullets 在前、legacy bullets 在后 | ✅ 已实现 |
| opt-in 但缺路径 / 路径不存在时无信号 | 落 warn issue `legacy_instruction_md_path_missing` / `legacy_instruction_md_file_not_found`，不阻断 import、不读任何文件 | ✅ 已实现 |
| compiler dry-run `core.guard` 行为指令拼接 | 自动覆盖 legacy 追加项（`behavior_instructions.items[]` 已经是统一来源） | ✅ 已实现，无需 compiler 改动 |
| 正式 runtime 切流 | 仍由 v1 `PromptBuilder._instruction` 直接读 `config/soul/instruction.md`，不走 v2 importer | ⏳ 后续 |

## 12. Runtime Cutover B1 — 协议层 + 配置层 + runtime 入口骨架

> 上游：[persona-runtime-cutover-B1-execution.md](../tracking/persona-runtime-cutover-B1-execution.md)
>
> 本节仅记录 runtime 切流前的协议骨架；4 个 feature flag 默认全 off，`PromptBuilder` / `LLMClient` / `GroupChatScheduler` 在本期完全不动。

| 旧状态 / 旧入口 | 新状态 / 新入口 | 状态 |
|---|---|---|
| `BotConfig` 无 v2 切流 flag | `BotConfig.persona_v2`（`PersonaV2Config`）落地：`runtime_consume=false` / `shadow_compare=false` / `runtime_groups=[]` / `fallback_on_compile_error=true` / `persona_id="default"`；TOML 段名 `[persona_v2]` 与 Pydantic 字段名直接对齐 | ✅ 已实现；`tests/test_persona_runtime_config.py` 6 条锁默认值 + TOML round-trip |
| compiler 仅 `compile_persona_dry_run` 单入口（读 `.draft/`） | 抽 `_compile_internal(writer, persona_id, *, mode)` 共享主体，新增 `compile_persona_runtime` 读 `_pending_freeze/`；`mode` 字段 `dry_run`/`runtime` 区分日志锚点 | ✅ 已实现；`tests/test_persona_compiler.py` 锁 byte-equal 不变量 + yaml-error 不 raise |
| `_pending_freeze/<id>/` 仅 yaml + source.frozen.md | 同 commit 增写 `_persona_runtime.json`（`schema_version=1.0` + `persona_id` + `frozen_at` + `source_sha256`），与 runtime 协议对齐 | ✅ 已实现 |
| runtime 无 v2 入口 | 新增 `services/persona/runtime.load_pending_freeze()` + `PersonaRuntimeBundle`；MAJOR mismatch 是唯一硬熔断，source 漂移仅 warn；永不 raise | ✅ 已实现；`tests/test_persona_runtime_loader.py` 7 条锁 None / happy / meta 缺失 / MAJOR mismatch / 漂移 warn / yaml 错 / meta 损坏 |
| Shadow compare 双算 | `services/persona/shadow.py::ShadowCompareEngine` + `ShadowDiffReport` + `ShadowCounter`；`kernel/router.py::_on_connect` flag-gated hook（24 行，shadow_compare=off 时零代码路径变化）；JSONL log 落 `storage/persona_shadow_diff.log`；复用 `parity_audit.compare_v1_vs_v2_dry_run` 收 `divergent_axes` | ✅ 已实现；详见 [persona-runtime-cutover-B2-execution.md](../tracking/persona-runtime-cutover-B2-execution.md)；`tests/test_persona_shadow.py` 5 条锁 flag off / happy / bundle 缺失 / divergent / cancel-path |
| PromptBuilder / LLMClient 注入 v2 prompt blocks | `services/persona/runtime_selector.py::PersonaRuntimeSelector` 决定 v1↔v2 per turn；`PromptBuilder.set_runtime_selector` + `resolve_static_block(group_id)` 替换第一块；`LLMClient` 两处 fallback 同样走 resolve；`kernel/router.py::_on_connect` 装配 selector；单群灰度 `runtime_groups=["993065015"]`，私聊与未授权群继续 v1 | ✅ 已实现；详见 [persona-runtime-cutover-B3-execution.md](../tracking/persona-runtime-cutover-B3-execution.md)；`tests/test_persona_runtime_selector.py` 8 条 + `tests/test_prompt_builder_runtime.py` 7 条；运行时验证待手动验收 |
| Part 1 humanization runtime 编排 + 灰度-1 | `services/humanization/` RuntimeStateBus owner、Register/Catchphrase/Sticker/Thinker PromptBlock Provider、StylometricScorer、critic rewrite-loop、semantic gate dynamic threshold、U13 double-haiku trace、V7 catchphrase seed 脚本、V12 measure 脚本已按派单文档收口；`config/config.json` 的 `humanization.runtime_groups=["993065015"]` 单群灰度-1 已落库，rewrite/sticker/thinker/dynamic gate 仍 off | ✅ 工程收口；详见 [omubot-humanization-part1-execution.md](../tracking/omubot-humanization-part1-execution.md)；灰度-1 仍待 rebuild/restart 后 24h 指标和用户最终验收 |
| 正式 runtime 切流 | 4 flag 全 off；caller=0；`grep -rn 'load_pending_freeze\|PersonaRuntimeBundle' --include='*.py'` 在 `PromptBuilder`/`LLMClient`/`bot.py`/`kernel/` 零命中（D1 同模式扫描通过） | ⏳ 后续（B2~B6） |
