# Persona v2 Importer 迁移清单

> 状态：2026-05-24 Part A S1-S5 后端/CLI 首版已完成；admin SPA 与 Part B 尚未启动。
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
| S6/S10' | admin UI 高亮与 Pending Freeze 交互 | ⏳ 后续，不属于 Part A 首版 |
| Part B | RuntimeStateBus / SystemModule / compiler | ⏳ 后续架构提案 |

## 3. 护栏

1. 真实运行时配置仍受 `.gitignore` 保护；只放行 `config/persona/_defaults/v2/**`。
2. `config/persona/*/.draft/`、`source.frozen.md`、`_pending_freeze/` 不应提交。
3. 任何新增 importer 字段必须同时声明：来源、缺失时行为、是否属于 Part A 首版。
4. 后续迁移若触及 API、菜单、admin UI 或 runtime 写盘，继续在本文追加旧→新对照。
